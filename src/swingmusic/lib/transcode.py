"""
On-the-fly audio transcoding for Reverb.

Clean-room implementation: converts audio streams to lower bitrates or
different formats for bandwidth-constrained playback and client
compatibility. Uses ffmpeg under the hood.

Key pieces:
- Quality presets and format definitions
- Smart bypass: serve the original file when no conversion is needed
- Disk-backed LRU cache keyed by (trackhash, bitrate, format)
"""

import hashlib
import os
import subprocess
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

# Bitrate presets in kbps. "original" means no transcoding.
TRANSCODE_PRESETS = ("original", "320", "192", "128")

# Container -> ffmpeg audio codec args. Keys are the file extensions we emit.
TRANSCODE_FORMATS: dict[str, list[str]] = {
    "mp3": ["-c:a", "libmp3lame"],
    "opus": ["-c:a", "libopus"],
    "aac": ["-c:a", "aac"],
    "ogg": ["-c:a", "libvorbis"],
}

# Extension -> canonical format name for bypass comparison.
EXT_TO_FORMAT = {
    ".mp3": "mp3",
    ".opus": "opus",
    ".ogg": "ogg",
    ".oga": "ogg",
    ".m4a": "aac",
    ".aac": "aac",
    ".flac": "flac",
    ".wav": "wav",
    ".wma": "wma",
}

# Format -> ffmpeg muxer name for piping to stdout (no file extension to infer from).
PIPE_FORMATS = {
    "mp3": "mp3",
    "opus": "opus",
    "ogg": "ogg",
    "aac": "adts",
}

# Format -> HTTP mimetype for streamed responses.
TRANSCODE_MIMES = {
    "mp3": "audio/mpeg",
    "opus": "audio/opus",
    "ogg": "audio/ogg",
    "aac": "audio/aac",
}

DEFAULT_CACHE_MAX_MB = 1024


class TranscodeStreamError(Exception):
    """Raised when a progressive transcode stream can't be started."""


def parse_bitrate(value: str | int | None) -> int | None:
    """
    Parse a bitrate preset. Returns kbps as int, or None for "original"/unset.
    Accepts "128", "128k", 128, "original", None.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    v = str(value).strip().lower().rstrip("k")
    if v in ("original", "", "0"):
        return None
    try:
        kbps = int(v)
        return kbps if kbps > 0 else None
    except ValueError:
        return None


def normalize_format(fmt: str | None) -> str | None:
    """Normalize a format name to our canonical set. Returns None if unknown."""
    if not fmt:
        return None
    f = str(fmt).strip().lower().lstrip(".")
    return f if f in TRANSCODE_FORMATS else None


def source_format(filepath: str) -> str | None:
    """Best-effort canonical format of a source file from its extension."""
    return EXT_TO_FORMAT.get(Path(filepath).suffix.lower())


def should_transcode(
    track_bitrate: int | None,
    filepath: str,
    req_bitrate: int | None,
    req_format: str | None,
) -> bool:
    """
    Decide whether a transcode is actually needed (smart bypass).

    No transcode when:
    - No bitrate requested (original quality), AND no format requested
      (or the requested format matches the source container)
    - Requested bitrate >= source bitrate AND format matches/unspecified
    """
    src_fmt = source_format(filepath)

    # Format mismatch always needs a transcode (if a format was requested).
    if req_format is not None and req_format != src_fmt:
        return True

    # No bitrate requested -> original quality; only format mattered (handled above).
    if req_bitrate is None:
        return False

    # Requested bitrate at/above source -> pointless to transcode.
    if track_bitrate and req_bitrate >= track_bitrate:
        return False

    return True


def effective_bitrate(
    track_bitrate: int | None, req_bitrate: int | None
) -> int | None:
    """The bitrate to actually encode at: never exceed the source bitrate."""
    if req_bitrate is None:
        return None
    if track_bitrate:
        return min(track_bitrate, req_bitrate)
    return req_bitrate


def transcode_file(
    input_path: str,
    output_path: str,
    bitrate_kbps: int,
    fmt: str,
    timeout: int = 300,
) -> bool:
    """
    Transcode input_path -> output_path with ffmpeg, blocking.

    Returns True on success, False on failure (partial output is removed).
    """
    codec_args = TRANSCODE_FORMATS.get(fmt)
    if codec_args is None:
        return False

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        input_path,
        "-map_metadata",
        "0",
        "-vn",
        "-b:a",
        f"{bitrate_kbps}k",
        *codec_args,
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if result.returncode != 0 or not os.path.exists(output_path):
            _safe_unlink(output_path)
            return False
        return os.path.getsize(output_path) > 0
    except (subprocess.SubprocessError, OSError):
        _safe_unlink(output_path)
        return False


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def cache_key(trackhash: str, bitrate_kbps: int, fmt: str) -> str:
    """Stable cache key for a (track, bitrate, format) triple."""
    raw = f"{trackhash}:{bitrate_kbps}:{fmt}".encode()
    return hashlib.sha256(raw).hexdigest()[:32]


class TranscodeCache:
    """
    Disk-backed LRU cache for transcoded audio.

    Files live under `cache_dir`; bookkeeping (key -> path, size, recency)
    is in memory and rebuilt lazily. Thread-safe.
    """

    def __init__(self, cache_dir: str | Path, max_mb: int = DEFAULT_CACHE_MAX_MB):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_mb * 1024 * 1024
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, dict] = OrderedDict()
        self._total_bytes = 0
        self._scan_existing()

    def _scan_existing(self) -> None:
        """Pick up files left from a previous run (oldest first)."""
        try:
            files = sorted(
                self.cache_dir.iterdir(),
                key=lambda p: p.stat().st_mtime,
            )
        except OSError:
            return
        for p in files:
            if not p.is_file():
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            self._entries[p.stem] = {"path": str(p), "size": size}
            self._total_bytes += size
        self._evict_locked()

    def get(self, key: str) -> str | None:
        """Return the cached file path for key, or None on miss."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            path = entry["path"]
            if not os.path.exists(path):
                # File vanished under us; drop the entry.
                self._total_bytes -= entry["size"]
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return path

    def put(self, key: str, src_path: str, fmt: str) -> str | None:
        """
        Move src_path into the cache under key. Returns the cached path,
        or None if src_path is unusable.
        """
        if not os.path.exists(src_path):
            return None
        dest = self.cache_dir / f"{key}.{fmt}"
        try:
            os.replace(src_path, dest)
            size = dest.stat().st_size
        except OSError:
            return None
        with self._lock:
            old = self._entries.pop(key, None)
            if old and old["path"] != str(dest):
                self._total_bytes -= old["size"]
                _safe_unlink(old["path"])
            elif old:
                self._total_bytes -= old["size"]
            self._entries[key] = {"path": str(dest), "size": size}
            self._total_bytes += size
            self._evict_locked()
            return str(dest)

    def _evict_locked(self) -> None:
        """Drop least-recently-used entries until under the size cap."""
        while self._total_bytes > self.max_bytes and self._entries:
            _, entry = self._entries.popitem(last=False)
            self._total_bytes -= entry["size"]
            _safe_unlink(entry["path"])

    def clear(self) -> None:
        with self._lock:
            for entry in self._entries.values():
                _safe_unlink(entry["path"])
            self._entries.clear()
            self._total_bytes = 0

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._total_bytes

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def build_ffmpeg_cmd(
    input_path: str,
    bitrate_kbps: int,
    fmt: str,
    seek_seconds: float = 0.0,
) -> list[str]:
    """
    Build an ffmpeg command that transcodes to stdout (pipe:1).

    seek_seconds > 0 seeks the *input* (-ss before -i), which is fast and
    is how Range requests are honored for transcoded streams.
    Raises TranscodeStreamError for unknown formats.
    """
    muxer = PIPE_FORMATS.get(fmt)
    codec_args = TRANSCODE_FORMATS.get(fmt)
    if muxer is None or codec_args is None:
        raise TranscodeStreamError(f"unsupported transcode format: {fmt!r}")

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
    if seek_seconds and seek_seconds > 0:
        cmd += ["-ss", f"{seek_seconds:.3f}"]
    cmd += [
        "-i",
        input_path,
        "-map_metadata",
        "0",
        "-vn",
        "-b:a",
        f"{bitrate_kbps}k",
        *codec_args,
        "-f",
        muxer,
        "pipe:1",
    ]
    return cmd


def estimate_seek_seconds(
    range_start: int,
    bitrate_kbps: int,
    duration: float | None = None,
) -> float:
    """
    Estimate playback position (seconds) from a Range byte offset.

    Assumes constant bitrate: bytes_per_sec = bitrate_kbps * 1000 / 8.
    Approximate (within ~1s for CBR; close enough for VBR) and clamped
    to [0, duration]. This is the standard approach for seeking in
    transcoded streams.
    """
    if range_start <= 0:
        return 0.0
    bytes_per_sec = max(bitrate_kbps * 1000 / 8, 1.0)
    seek = range_start / bytes_per_sec
    if duration and duration > 0:
        seek = min(seek, max(duration - 1.0, 0.0))
    return max(seek, 0.0)


class ProgressiveTranscoder:
    """
    Pipes ffmpeg's stdout directly to the HTTP response for instant playback.

    - First audio chunk arrives in well under a second (no waiting for a
      full transcode to disk).
    - Closing the stream iterator (client disconnect) kills ffmpeg: no
      orphan processes. Cleanup is idempotent.
    - When streaming from position 0 with a cache + key, output is tee'd
      to a temp file as it streams; on clean completion it is moved into
      the TranscodeCache, so the next identical request serves from disk.
      Partial (disconnected) streams are discarded, never cached.

    Use response_stream() to get a primed, close()-able iterator for an
    HTTP response. Priming reads the first chunk up front so ffmpeg
    startup failures raise TranscodeStreamError *before* we commit to a
    response, letting callers fall back to blocking transcode-then-serve.
    """

    def __init__(
        self,
        input_path: str,
        bitrate_kbps: int,
        fmt: str,
        seek_seconds: float = 0.0,
        cache: "TranscodeCache | None" = None,
        cache_key: "str | None" = None,
        chunk_size: int = 65536,
    ):
        self.input_path = input_path
        self.bitrate_kbps = bitrate_kbps
        self.fmt = fmt
        self.seek_seconds = max(seek_seconds or 0.0, 0.0)
        self.cache = cache
        self.cache_key = cache_key
        self.chunk_size = chunk_size
        self.proc: "subprocess.Popen | None" = None
        self._tmp_path: "str | None" = None
        self._tmp_file = None
        self._bytes_written = 0
        # Only cache full streams; a seeked stream is a partial by definition.
        self._caching = (
            cache is not None and cache_key is not None and self.seek_seconds <= 0
        )

    def start(self) -> "ProgressiveTranscoder":
        """Spawn ffmpeg. Idempotent. Raises TranscodeStreamError on failure."""
        if self.proc is not None:
            return self
        cmd = build_ffmpeg_cmd(
            self.input_path, self.bitrate_kbps, self.fmt, self.seek_seconds
        )
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError) as e:
            raise TranscodeStreamError(f"could not start ffmpeg: {e}")
        if self._caching:
            fd, self._tmp_path = tempfile.mkstemp(
                suffix=f".{self.fmt}", dir=self.cache.cache_dir, prefix="prog-"
            )
            self._tmp_file = os.fdopen(fd, "wb")
        return self

    def chunks(self):
        """
        Yield transcoded audio chunks.

        The generator's finally block guarantees ffmpeg is reaped and the
        partial cache file is handled whether the stream completes, the
        client disconnects (generator closed early), or an error occurs.
        """
        if self.proc is None:
            self.start()
        completed = False
        try:
            while True:
                chunk = self.proc.stdout.read(self.chunk_size)
                if not chunk:
                    break
                self._bytes_written += len(chunk)
                if self._tmp_file is not None:
                    try:
                        self._tmp_file.write(chunk)
                    except OSError:
                        # Cache write failure must never break the stream.
                        pass
                yield chunk
            # Clean EOF: only cache successful, non-empty transcodes.
            try:
                rc = self.proc.wait(timeout=10)
            except (subprocess.SubprocessError, OSError):
                rc = -1
            completed = rc == 0 and self._bytes_written > 0
        finally:
            self._finalize(completed)

    def response_stream(self):
        """
        Return a primed, close()-able iterator of audio chunks for an HTTP
        response. Raises TranscodeStreamError if ffmpeg produces no output.
        """
        self.start()
        gen = self.chunks()
        try:
            first = next(gen)
        except StopIteration:
            try:
                gen.close()
            finally:
                self.close()
            raise TranscodeStreamError("ffmpeg produced no output")
        except BaseException:
            try:
                gen.close()
            finally:
                self.close()
            raise

        outer = self

        class _ResponseStream:
            """Iterator that propagates close() to the ffmpeg process."""

            def __init__(self):
                self._gen = gen
                self._first = first
                self._primed = False

            def __iter__(self):
                return self

            def __next__(self):
                if not self._primed:
                    self._primed = True
                    return self._first
                return next(self._gen)

            def close(self):
                try:
                    self._gen.close()
                finally:
                    outer.close()

        return _ResponseStream()

    def _finalize(self, completed: bool) -> None:
        if self._tmp_file is not None:
            try:
                self._tmp_file.close()
            except OSError:
                pass
            self._tmp_file = None
        self._kill()
        if self._tmp_path is not None:
            if completed and self._caching:
                try:
                    self.cache.put(self.cache_key, self._tmp_path, self.fmt)
                except Exception:
                    _safe_unlink(self._tmp_path)
            else:
                _safe_unlink(self._tmp_path)
            self._tmp_path = None

    def _kill(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.kill()
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            proc.wait(timeout=5)
        except (subprocess.SubprocessError, OSError):
            pass
        for stream in (proc.stdout, proc.stderr, proc.stdin):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass

    def close(self) -> None:
        """Idempotent: kill ffmpeg and discard any partial cache output."""
        self._finalize(False)
