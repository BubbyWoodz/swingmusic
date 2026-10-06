"""
Browse endpoints: getArtists, getArtist, getAlbum, getSong,
getAlbumList, getAlbumList2.
"""

from flask import request

from swingmusic.store.albums import AlbumStore
from swingmusic.store.artists import ArtistStore
from swingmusic.store.tracks import TrackStore

from . import (
    album_id,
    artist_id,
    bp,
    error,
    ok,
    require_subsonic_auth,
    serialize_album,
    serialize_artist,
    serialize_song,
    song_id,
)


def _first_letter(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "#"
    ch = name[0].upper()
    return ch if ch.isalpha() else "#"


@bp.route("/getArtists.view", methods=["GET", "POST"])
@bp.route("/getArtists", methods=["GET", "POST"])
@require_subsonic_auth
def get_artists():
    artists = ArtistStore.get_flat_list()
    # Group by first letter, sorted
    grouped: dict[str, list] = {}
    for artist in sorted(artists, key=lambda a: (a.name or "").lower()):
        letter = _first_letter(artist.name)
        grouped.setdefault(letter, []).append(
            {"id": artist_id(artist), "name": artist.name or ""}
        )
    index = [{"name": letter, "artist": grouped[letter]} for letter in sorted(grouped)]
    return ok({"artists": {"index": index}})


@bp.route("/getArtist.view", methods=["GET", "POST"])
@bp.route("/getArtist", methods=["GET", "POST"])
@require_subsonic_auth
def get_artist():
    artist_id_param = request.args.get("id") or request.form.get("id")
    if not artist_id_param:
        return error(40, "Missing required parameter: id")
    artist = ArtistStore.get_artist_by_hash(artist_id_param)
    if artist is None:
        return error(70, "Artist not found.")
    albums = AlbumStore.get_albums_by_artisthash(artist_id_param)
    data = serialize_artist(artist, include_albums=True)
    data["album"] = [serialize_album(a) for a in albums]
    return ok({"artist": data})


@bp.route("/getAlbum.view", methods=["GET", "POST"])
@bp.route("/getAlbum", methods=["GET", "POST"])
@require_subsonic_auth
def get_album():
    album_id_param = request.args.get("id") or request.form.get("id")
    if not album_id_param:
        return error(40, "Missing required parameter: id")
    album = AlbumStore.get_album_by_hash(album_id_param)
    if album is None:
        return error(70, "Album not found.")
    tracks = AlbumStore.get_album_tracks(album_id_param)
    data = serialize_album(album)
    data["song"] = [serialize_song(t) for t in tracks]
    return ok({"album": data})


@bp.route("/getSong.view", methods=["GET", "POST"])
@bp.route("/getSong", methods=["GET", "POST"])
@require_subsonic_auth
def get_song():
    song_id_param = request.args.get("id") or request.form.get("id")
    if not song_id_param:
        return error(40, "Missing required parameter: id")
    group = TrackStore.trackhashmap.get(song_id_param)
    if group is None or not group.tracks:
        return error(70, "Song not found.")
    return ok({"song": serialize_song(group.tracks[0])})


@bp.route("/getAlbumList.view", methods=["GET", "POST"])
@bp.route("/getAlbumList", methods=["GET", "POST"])
@bp.route("/getAlbumList2.view", methods=["GET", "POST"])
@bp.route("/getAlbumList2", methods=["GET", "POST"])
@require_subsonic_auth
def get_album_list():
    list_type = request.args.get("type") or request.form.get("type") or "alphabeticalByName"
    size = request.args.get("size") or request.form.get("size") or 10
    offset = request.args.get("offset") or request.form.get("offset") or 0
    try:
        size = max(1, min(int(size), 500))
        offset = max(0, int(offset))
    except (ValueError, TypeError):
        return error(40, "Invalid size/offset.")

    albums = AlbumStore.get_flat_list()

    if list_type == "newest":
        albums = sorted(albums, key=lambda a: a.created_date or 0, reverse=True)
    elif list_type == "recent":
        albums = sorted(albums, key=lambda a: a.lastplayed or 0, reverse=True)
    elif list_type == "random":
        import random

        albums = random.sample(albums, min(len(albums), size + offset))
    elif list_type == "alphabeticalByName":
        albums = sorted(albums, key=lambda a: (a.title or "").lower())
    elif list_type == "alphabeticalByArtist":
        albums = sorted(
            albums,
            key=lambda a: (
                (a.albumartists[0]["name"] if a.albumartists else "").lower(),
                (a.title or "").lower(),
            ),
        )
    elif list_type == "byYear":
        year_from = request.args.get("fromYear") or request.form.get("fromYear")
        year_to = request.args.get("toYear") or request.form.get("toYear")
        try:
            yf = int(year_from) if year_from else 0
            yt = int(year_to) if year_to else 9999
        except (ValueError, TypeError):
            return error(40, "Invalid fromYear/toYear.")
        albums = sorted(
            [a for a in albums if yf <= int(a.date or 0) <= yt],
            key=lambda a: int(a.date or 0),
            reverse=True,
        )
    elif list_type == "byGenre":
        genre = request.args.get("genre") or request.form.get("genre") or ""
        albums = [
            a
            for a in albums
            if any(
                (g.get("name", "") if isinstance(g, dict) else str(g)).lower() == genre.lower()
                for g in (a.genres or [])
            )
        ]
    else:
        return error(40, f"Unsupported list type: {list_type}")

    page = albums[offset : offset + size]
    key = "albumList2" if request.path.endswith("getAlbumList2.view") or request.path.endswith("getAlbumList2") else "albumList"
    return ok({key: {"album": [serialize_album(a) for a in page]}})
