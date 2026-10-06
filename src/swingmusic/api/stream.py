"""
Contains all the track routes.
"""

import os
from pathlib import Path
import tempfile
import time
from typing import Literal

from pydantic import BaseModel, Field
from flask_openapi3 import APIBlueprint, Tag
from swingmusic.api.apischemas import TrackHashSchema
from swingmusic.config import UserConfig
from swingmusic.lib.transcoder import start_transcoding
from swingmusic.lib.transcode import (
    ProgressiveTranscoder,
    TranscodeCache,
    TranscodeStreamError,
    TRANSCODE_MIMES,
    cache_key,
    effective_bitrate,
    estimate_seek_seconds,
    normalize_format,
    parse_bitrate,
    should_transcode,
    transcode_file,
)
from flask import request, Response, send_from_directory
from swingmusic.lib.trackslib import get_silence_paddings
from swingmusic.settings import Paths

from swingmusic.store.tracks import TrackStore
from swingmusic.utils.files import guess_mime_type

bp_tag = Tag(name="File", description="Audio files")
api = APIBlueprint("track", __name__, url_prefix="/file", abp_tags=[bp_tag])


class TransCodeStore:
    map: dict[str, str] = {}

    @classmethod
    def add_file(cls, trackhash: str, filepath: str):
        cls.map[trackhash] = filepath

    @classmethod
    def remove_file(cls, trackhash: str):
        del cls.map[trackhash]

    @classmethod
    def find(cls, trackhash: str):
        return cls.map.get(trackhash)


_transcode_cache: TranscodeCache | None = None


def get_transcode_cache() -> TranscodeCache:
    """Singleton disk-backed LRU cache for transcoded audio."""
    global _transcode_cache
    if _transcode_cache is None:
        max_mb = getattr(UserConfig(), "transcodeCacheMaxMB", 1024)
        cache_dir = Paths().config_dir / "transcode_cache"
        _transcode_cache = TranscodeCache(cache_dir, max_mb=max_mb)
    return _transcode_cache


def get_user_transcode_prefs() -> tuple[str, str]:
    """
    Returns (quality, format) preferred by the current user, or ("original", "mp3")
    defaults when unavailable. Stored in the user's `extra` JSON field.
    """
    try:
        from swingmusic.utils.auth import get_current_userid
        from swingmusic.db.userdata import UserTable

        userid = get_current_userid()
        user = UserTable.get_by_id(userid)
        extra = (user.extra or {}) if user else {}
        quality = str(extra.get("transcode_quality", "original"))
        fmt = str(extra.get("transcode_format", "mp3"))
        return quality, fmt
    except Exception:
        return "original", "mp3"


class SendTrackFileQuery(BaseModel):
    filepath: str = Field(description="The filepath to play (if available)")
    quality: str = Field(
        "original",
        description="The quality of the audio file. Options: auto, original, 320, 192, 128",
    )
    bitrate: str | None = Field(
        None,
        description="Alias for quality: requested bitrate like 128k. Overrides quality if set.",
    )
    container: Literal["mp3", "aac", "opus", "ogg"] = Field(
        "mp3",
        description="The container format of the audio file. Options: mp3, aac, opus, ogg",
    )


@api.get("/<trackhash>/legacy")
def send_track_file_legacy(path: TrackHashSchema, query: SendTrackFileQuery):
    """
    Get a playable audio file without Range support

    Returns a playable audio file that corresponds to the given filepath. Falls back to track hash if filepath is not found.

    NOTE: Does not support range requests or transcoding.
    """
    requested_trackhash = path.trackhash.strip()
    filepath = query.filepath.strip()

    msg = {"msg": "File Not Found"}

    # prevent path traversal
    if "/../" in filepath:
        return {"msg": "Invalid filepath", "error": "Path traversal detected"}, 400

    requested_filepath = Path(filepath).resolve()

    # check if filepath is a child of any of the root dirs
    inside_root = False
    for root_dir in UserConfig().rootDirs:
        if root_dir == "$home":
            root_dir = Path.home()
        else:
            root_dir = Path(root_dir).resolve()

        if root_dir in requested_filepath.parents:
            inside_root = True
            break

    if not inside_root:
        return {
            "msg": "Invalid filepath",
            "error": "File not inside root directories",
        }, 400

    track = None
    tracks = TrackStore.get_tracks_by_filepaths([filepath])

    if len(tracks) > 0 and os.path.exists(tracks[0].filepath):
        for t in tracks:
            if os.path.exists(t.filepath) and t.trackhash == requested_trackhash:
                track = t
                break
    else:
        group = TrackStore.trackhashmap.get(requested_trackhash)

        # When finding by trackhash, sort by bitrate
        # and get the first track that exists
        if group is not None:
            tracks = sorted(group.tracks, key=lambda x: x.bitrate, reverse=True)

            for t in tracks:
                if os.path.exists(t.filepath):
                    track = t
                    break

    if track is not None:
        audio_type = guess_mime_type(track.filepath)
        return send_from_directory(
            Path(track.filepath).parent,
            Path(track.filepath).name,
            mimetype=audio_type,
            conditional=True,
            as_attachment=True,
        )

    return msg, 404


@api.get("/<trackhash>")
def send_track_file(path: TrackHashSchema, query: SendTrackFileQuery):
    """
    Get a playable audio file with Range headers support

    Returns a playable audio file that corresponds to the given filepath. Falls back to track hash if filepath is not found.

    Transcoding can be done by sending the quality and container query parameters.
    When no quality is requested, the user's saved transcode preference is used;
    when neither is set, the original file is served.

    Transcodes stream progressively (ffmpeg piped straight to the response)
    so playback starts in under a second; output is cached in the background
    for instant repeat plays. Falls back to blocking transcode-then-serve if
    progressive streaming fails.

    **NOTES:**
    - Transcoded streams report incorrect duration during playback (idk why! FFMPEG gurus we need your help here).
    - The quality parameter is the desired bitrate in kbps.
    - The mp3 container is the best container for upto 320kbps (and has better duration reporting).
    - Transcoded outputs are cached on disk (LRU, size-capped) so repeat plays don't re-transcode.
    - You can get the transcoded bitrate by checking the X-Transcoded-Bitrate header on the response.
    """
    trackhash = path.trackhash
    filepath = query.filepath

    # If filepath is provided, try to send that
    track = None
    tracks = TrackStore.get_tracks_by_filepaths([filepath])

    if len(tracks) > 0 and os.path.exists(filepath):
        track = tracks[0]
    else:
        res = TrackStore.trackhashmap.get(trackhash)

        # When finding by trackhash, sort by bitrate
        # and get the first track that exists
        if res is not None:
            tracks = sorted(res.tracks, key=lambda x: x.bitrate, reverse=True)

            for t in tracks:
                if os.path.exists(t.filepath):
                    track = t
                    break

    if track is None:
        return {"msg": "File Not Found"}, 404

    # Resolve requested quality: explicit param > user preference > original.
    # `bitrate` is an alias for `quality` when provided.
    req_quality = query.bitrate or query.quality
    if req_quality == "original":
        # Fall back to the user's saved preference, if any.
        pref_quality, pref_format = get_user_transcode_prefs()
        if pref_quality != "original":
            req_quality = pref_quality
            if query.container == "mp3":
                # Only apply the preferred format when the caller didn't
                # explicitly pick a container.
                query = query.model_copy(update={"container": pref_format})

    req_bitrate = parse_bitrate(req_quality)
    req_format = normalize_format(query.container) or "mp3"

    # Smart bypass: serve the original when no conversion is needed.
    if not should_transcode(track.bitrate, track.filepath, req_bitrate, req_format):
        return send_file_as_chunks(track.filepath)

    bitrate = effective_bitrate(track.bitrate, req_bitrate) or req_bitrate or 128
    # Cap non-lossless containers at 320k (matches upstream behavior).
    bitrate = min(bitrate, 320)

    cached = get_transcode_cache().get(cache_key(trackhash, bitrate, req_format))
    if cached is not None:
        resp = send_file_as_chunks(cached)
        resp.headers.add("X-Transcoded-Bitrate", f"{bitrate}k")
        return resp

    # Progressive first: pipe ffmpeg straight to the response for instant
    # playback (with background caching). Falls back to blocking
    # transcode-then-serve below if progressive streaming fails.
    try:
        return progressive_transcode_response(
            track.filepath,
            trackhash,
            bitrate,
            req_format,
            duration=getattr(track, "duration", None),
        )
    except TranscodeStreamError:
        pass

    # Blocking fallback: transcode fully, cache, serve with Range support.
    tmp = tempfile.NamedTemporaryFile(
        delete=False, suffix=f".{req_format}", dir=get_transcode_cache().cache_dir
    )
    tmp.close()
    ok = transcode_file(track.filepath, tmp.name, bitrate, req_format)
    if not ok:
        return {"msg": "Transcoding failed"}, 500

    cached_path = get_transcode_cache().put(
        cache_key(trackhash, bitrate, req_format), tmp.name, req_format
    )
    if cached_path is None:
        return {"msg": "Transcoding failed"}, 500

    resp = send_file_as_chunks(cached_path)
    resp.headers.add("X-Transcoded-Bitrate", f"{bitrate}k")
    return resp


def progressive_transcode_response(
    filepath: str,
    trackhash: str,
    bitrate: int,
    req_format: str,
    duration: "float | None" = None,
) -> Response:
    """
    Stream a transcoded file progressively: ffmpeg's stdout is piped
    directly to the HTTP response, so playback starts in under a second.

    Honors Range requests by restarting ffmpeg at the estimated seek
    position (-ss before -i). While streaming from position 0, output is
    also written to the transcode cache in the background, so the next
    identical request serves from disk.

    Raises TranscodeStreamError if ffmpeg can't produce output; callers
    should fall back to blocking transcode-then-serve (or the original).
    """
    range_header = request.headers.get("Range")
    seek = (
        estimate_seek_seconds(get_start_range(range_header), bitrate, duration)
        if range_header
        else 0.0
    )

    transcoder = ProgressiveTranscoder(
        filepath,
        bitrate,
        req_format,
        seek_seconds=seek,
        cache=get_transcode_cache(),
        cache_key=cache_key(trackhash, bitrate, req_format),
    )
    # Primes ffmpeg; raises TranscodeStreamError before we commit to a response.
    stream = transcoder.response_stream()

    mime = TRANSCODE_MIMES.get(req_format, "audio/mpeg")
    resp = Response(
        stream,
        status=206 if seek > 0 else 200,
        mimetype=mime,
        direct_passthrough=True,
    )
    resp.headers.add("X-Transcoded-Bitrate", f"{bitrate}k")
    resp.headers.add("Accept-Ranges", "bytes")
    resp.headers.add("Access-Control-Expose-Headers", "Content-Range")
    if seek > 0 and range_header:
        start_byte = get_start_range(range_header)
        if duration and duration > 0:
            est_total = int(duration * bitrate * 1000 / 8)
            resp.headers.add(
                "Content-Range", f"bytes {start_byte}-{est_total - 1}/{est_total}"
            )
        else:
            resp.headers.add("Content-Range", f"bytes {start_byte}-*/*")
    return resp


def transcode_and_stream(trackhash: str, filepath: str, bitrate: str, container: str):
    """
    Initiates transcoding and returns the first chunk of the transcoded file.

    The other chunks are streamed on subsequent requests and are rerouted to `send_file_as_chunks`.
    """
    temp_file = TransCodeStore.find(trackhash)
    if temp_file is not None:
        return send_file_as_chunks(temp_file)

    format_params = {
        "mp3": ["-c:a", "libmp3lame"],
        "aac": ["-c:a", "aac"],
        "webm": ["-c:a", "libopus"],
        "ogg": ["-c:a", "libvorbis"],
        "flac": ["-c:a", "flac"],
        "wav": ["-c:a", "pcm_s16le"],
    }

    # Create a temporary file
    format = f".{container}" if container in format_params.keys() else ".flac"
    container_args = (
        format_params[container]
        if container in format_params.keys()
        else format_params["flac"]
    )
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=format)
    temp_filename = temp_file.name
    temp_file.close()

    TransCodeStore.add_file(trackhash, temp_filename)
    start_transcoding(filepath, temp_filename, bitrate, container_args)

    chunk_size = 1024 * 512  # 0.5MB
    file_size = os.path.getsize(filepath)

    def generate():
        # Poll for the output file
        while (
            not os.path.exists(temp_filename)
            or os.path.getsize(temp_filename) < chunk_size
        ):
            print(f"Waiting for transcoding to complete... filename: {temp_filename}")
            time.sleep(0.1)  # Wait for 100ms before checking again

        with open(temp_filename, "rb") as file:
            file.seek(0)
            return file.read(chunk_size)

    audio_type = guess_mime_type(temp_filename)
    response = Response(
        generate(),
        206,
        mimetype=audio_type,
        content_type=audio_type,
        direct_passthrough=True,
    )
    response.headers.add("Content-Range", f"bytes {0}-{chunk_size}/{file_size}")
    response.headers.add("Accept-Ranges", "bytes")
    response.headers.add("X-Transcoded-Bitrate", bitrate)
    return response


def send_file_as_chunks(filepath: str) -> Response:
    """
    Returns a Response object that streams the file in chunks.
    """
    # NOTE: +1 makes sure the last byte is included in the range.
    # NOTE: -1 is used to convert the end index to a 0-based index.
    chunk_size = 1024 * 512  # 0.5MB

    # Get file size
    file_size = os.path.getsize(filepath)
    start = 0
    end = chunk_size

    # Read range header
    range_header = request.headers.get("Range")
    if range_header:
        start = get_start_range(range_header)

        # If start + chunk_size is greater than file_size,
        # set end to file_size - 1
        _end = start + chunk_size - 1

        if _end > file_size:
            end = file_size - 1
        else:
            end = _end

    def generate_chunks():
        with open(filepath, "rb") as file:
            file.seek(start)
            remaining_bytes = end - start + 1

            retry_count = 0
            max_retries = 10  # 5 * 100ms = 500ms total wait time

            while remaining_bytes > 0 or retry_count < max_retries:
                if retry_count == max_retries:
                    print("💚 sending final chunk! ...")

                    pos = file.tell()
                    chunk = file.read(os.path.getsize(filepath) - pos)

                    return chunk, pos, True

                if remaining_bytes < chunk_size:
                    time.sleep(0.25)
                    retry_count += 1
                    remaining_bytes = os.path.getsize(filepath) - file.tell()
                    continue

                chunk = file.read(min(chunk_size, remaining_bytes))
                if chunk:
                    remaining_bytes -= len(chunk)
                    return chunk, file.tell(), False
                else:
                    # If no data is read, wait for 100ms before retrying
                    time.sleep(0.25)
                    retry_count += 1

                    # update remaining bytes
                    remaining_bytes = os.path.getsize(filepath) - file.tell()
                    print(f"▶ Remaining bytes: {remaining_bytes}")

            return None, 0, True

    data, position, is_final = generate_chunks()

    audio_type = guess_mime_type(filepath)
    response = Response(
        response=data,
        status=206,  # Partial Content status code
        mimetype=audio_type,
        content_type=audio_type,
        direct_passthrough=True,
    )

    bytes_to_add = chunk_size if not is_final else 0
    response.headers.add(
        "Content-Range",
        f"bytes {start}-{position}/{os.path.getsize(filepath) + bytes_to_add}",
    )
    response.headers.add("Access-Control-Expose-Headers", "Content-Range")
    response.headers.add("Accept-Ranges", "bytes")
    return response


def get_start_range(range_header: str):
    try:
        range_start, range_end = range_header.strip().split("=")[1].split("-")
        return int(range_start)

    except ValueError:
        return 0


class GetAudioSilenceBody(BaseModel):
    ending_file: str = Field(description="The ending file's path")
    starting_file: str = Field(description="The beginning file's path")
    ending_trackhash: str = Field(description="The ending file's trackhash")
    starting_trackhash: str = Field(description="The beginning file's trackhash")


@api.post("/silence")
def get_audio_silence(body: GetAudioSilenceBody):
    """
    Get silence paddings

    Returns the duration of silence at the end of the current ending track and the duration of silence at the beginning of the next track.

    NOTE: Durations are in milliseconds.
    """
    ending_file = body.ending_file  # ending file's filepath
    starting_file = body.starting_file  # starting file's filepath

    if not TrackStore.is_valid_track_filepath(
        body.ending_trackhash, ending_file
    ) or not TrackStore.is_valid_track_filepath(
        body.starting_trackhash, starting_file
    ):
        return {"msg": "Invalid filepath"}, 400

    return get_silence_paddings(ending_file, starting_file)
