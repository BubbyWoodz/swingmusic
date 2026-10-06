"""
Annotation endpoints: scrobble, star, unstar, setRating.
"""

import time

from flask import g, request

from swingmusic.db.userdata import ScrobbleTable
from swingmusic.store.albums import AlbumStore
from swingmusic.store.artists import ArtistStore
from swingmusic.store.tracks import TrackStore

from . import bp, error, ok, require_subsonic_auth


@bp.route("/scrobble.view", methods=["GET", "POST"])
@bp.route("/scrobble", methods=["GET", "POST"])
@require_subsonic_auth
def scrobble():
    """
    Scrobble a track. Params: id, time (ms epoch), submission (bool).
    submission=false = "now playing" notification only.
    """
    song_id = request.args.get("id") or request.form.get("id")
    if not song_id:
        return error(40, "Missing required parameter: id")

    group = TrackStore.trackhashmap.get(song_id)
    if group is None or not group.tracks:
        return error(70, "Song not found.")

    submission = request.args.get("submission") or request.form.get("submission")
    # submission=false means "now playing" — acknowledge without recording
    if submission is not None and str(submission).lower() in ("false", "0", "no"):
        return ok()

    # Record the scrobble
    timestamp_ms = request.args.get("time") or request.form.get("time")
    try:
        timestamp = int(int(timestamp_ms) / 1000) if timestamp_ms else int(time.time())
    except (ValueError, TypeError):
        timestamp = int(time.time())

    track = group.tracks[0]
    duration = int(track.duration or 0)

    scrobble_data = {
        "trackhash": song_id,
        "timestamp": timestamp,
        "duration": duration,
        "source": "subsonic",
        "userid": g.subsonic_user.id,
    }
    ScrobbleTable.add(scrobble_data)

    # Update in-memory play counts (mirrors native /track/log)
    album = AlbumStore.albummap.get(track.albumhash)
    if album:
        album.increment_playcount(duration, timestamp)
    for h in track.artisthashes:
        artist = ArtistStore.artistmap.get(h)
        if artist:
            artist.increment_playcount(duration, timestamp)
    group.increment_playcount(duration, timestamp)

    return ok()


@bp.route("/star.view", methods=["GET", "POST"])
@bp.route("/star", methods=["GET", "POST"])
@require_subsonic_auth
def star():
    """Star a song/album/artist. Maps to Swing favorites."""
    from swingmusic.db.userdata import FavoritesTable

    user = g.subsonic_user
    starred = []

    for param in ("id", "albumId", "artistId"):
        val = request.args.get(param) or request.form.get(param)
        if not val:
            continue
        if param == "id":  # song
            group = TrackStore.trackhashmap.get(val)
            if group:
                FavoritesTable.add({"hash": val, "type": "track", "userid": user.id})
                group.tracks[0].toggle_favorite_user(user.id)
                starred.append(val)
        elif param == "albumId":
            album = AlbumStore.get_album_by_hash(val)
            if album:
                FavoritesTable.add({"hash": val, "type": "album", "userid": user.id})
                album.toggle_favorite_user(user.id)
                starred.append(val)
        elif param == "artistId":
            artist = ArtistStore.get_artist_by_hash(val)
            if artist:
                FavoritesTable.add({"hash": val, "type": "artist", "userid": user.id})
                artist.toggle_favorite_user(user.id)
                starred.append(val)

    if not starred:
        return error(70, "Nothing found to star.")
    return ok()


@bp.route("/unstar.view", methods=["GET", "POST"])
@bp.route("/unstar", methods=["GET", "POST"])
@require_subsonic_auth
def unstar():
    """Unstar a song/album/artist."""
    from swingmusic.db.userdata import FavoritesTable

    user = g.subsonic_user
    unstarred = []

    for param, fav_type in (("id", "track"), ("albumId", "album"), ("artistId", "artist")):
        val = request.args.get(param) or request.form.get(param)
        if not val:
            continue
        try:
            FavoritesTable.remove_item({"hash": val, "type": fav_type})
            unstarred.append(val)
            # Update in-memory
            if param == "id":
                group = TrackStore.trackhashmap.get(val)
                if group and user.id in group.tracks[0].fav_userids:
                    group.tracks[0].toggle_favorite_user(user.id)
        except Exception:
            pass

    if not unstarred:
        return error(70, "Nothing found to unstar.")
    return ok()
