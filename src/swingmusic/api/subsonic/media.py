"""
Media endpoints: stream, download, getCoverArt.
"""

import os
from pathlib import Path

from flask import request, send_from_directory

from swingmusic.api.imgserver import find_thumbnail
from swingmusic.config import UserConfig
from swingmusic.store.albums import AlbumStore
from swingmusic.store.tracks import TrackStore
from swingmusic.utils.files import guess_mime_type

from . import bp, error, ok, require_subsonic_auth


def _find_track_file(trackhash: str):
    """Find the best playable file for a trackhash (highest bitrate first)."""
    group = TrackStore.trackhashmap.get(trackhash)
    if group is None:
        return None
    tracks = sorted(group.tracks, key=lambda x: x.bitrate or 0, reverse=True)
    for t in tracks:
        if t.filepath and os.path.exists(t.filepath):
            return t
    return None


@bp.route("/stream.view", methods=["GET", "POST"])
@bp.route("/stream", methods=["GET", "POST"])
@require_subsonic_auth
def stream():
    song_id = request.args.get("id") or request.form.get("id")
    if not song_id:
        return error(40, "Missing required parameter: id")
    track = _find_track_file(song_id)
    if track is None:
        return error(70, "Song not found.")
    # TODO: transcoding via maxBitRate/format params (future)
    return send_from_directory(
        Path(track.filepath).parent,
        Path(track.filepath).name,
        mimetype=guess_mime_type(track.filepath),
        conditional=True,
        as_attachment=False,
    )


@bp.route("/download.view", methods=["GET", "POST"])
@bp.route("/download", methods=["GET", "POST"])
@require_subsonic_auth
def download():
    song_id = request.args.get("id") or request.form.get("id")
    if not song_id:
        return error(40, "Missing required parameter: id")
    track = _find_track_file(song_id)
    if track is None:
        return error(70, "Song not found.")
    return send_from_directory(
        Path(track.filepath).parent,
        Path(track.filepath).name,
        mimetype=guess_mime_type(track.filepath),
        as_attachment=True,
    )


@bp.route("/getCoverArt.view", methods=["GET", "POST"])
@bp.route("/getCoverArt", methods=["GET", "POST"])
@require_subsonic_auth
def get_cover_art():
    art_id = request.args.get("id") or request.form.get("id")
    if not art_id:
        return error(40, "Missing required parameter: id")

    # Artist coverArt ids are prefixed with "ar-"
    if art_id.startswith("ar-"):
        artist_hash = art_id[3:]
        # Use the artist's first album's art as fallback
        albums = AlbumStore.get_albums_by_artisthash(artist_hash)
        if not albums:
            return error(70, "Cover art not found.")
        art_id = albums[0].albumhash

    # Try as albumhash first
    album = AlbumStore.get_album_by_hash(art_id)
    if album is not None:
        # find_thumbnail needs (albumhash, pathhash); use first track's pathhash
        tracks = AlbumStore.get_album_tracks(art_id)
        pathhash = tracks[0].pathhash if tracks else ""
        parent, name, _ = find_thumbnail(art_id, pathhash)
        if parent and name:
            return send_from_directory(parent, name)

    # Try as trackhash (song coverArt)
    group = TrackStore.trackhashmap.get(art_id)
    if group and group.tracks:
        track = group.tracks[0]
        parent, name, _ = find_thumbnail(track.albumhash, track.pathhash)
        if parent and name:
            return send_from_directory(parent, name)

    return error(70, "Cover art not found.")
