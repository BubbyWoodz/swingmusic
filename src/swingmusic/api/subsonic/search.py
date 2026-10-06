"""
Search endpoints: search, search2, search3.
"""

from flask import request

from swingmusic.lib import searchlib

from . import (
    bp,
    error,
    ok,
    require_subsonic_auth,
    serialize_album,
    serialize_artist,
    serialize_song,
)


def _run_search(query: str, limit: int = 20):
    """Returns (artists, albums, songs) using Swing's TopResults search."""
    if not query:
        return [], [], []
    results = searchlib.TopResults().search(query, limit=limit)
    # TopResults.search returns a dict with tracks/albums/artists keys
    # (shape varies by version — handle both dict and object)
    if isinstance(results, dict):
        tracks = results.get("tracks") or results.get("songs") or []
        albums = results.get("albums") or []
        artists = results.get("artists") or []
    else:
        tracks = getattr(results, "tracks", []) or []
        albums = getattr(results, "albums", []) or []
        artists = getattr(results, "artists", []) or []
    return artists, albums, tracks


def _search2_response(query: str, artist_count: int, album_count: int, song_count: int):
    artists, albums, tracks = _run_search(
        query, limit=max(artist_count, album_count, song_count, 20)
    )
    return {
        "searchResult2": {
            "artist": [serialize_artist(a) for a in artists[:artist_count]],
            "album": [serialize_album(a) for a in albums[:album_count]],
            "song": [serialize_song(t) for t in tracks[:song_count]],
        }
    }


@bp.route("/search.view", methods=["GET", "POST"])
@bp.route("/search", methods=["GET", "POST"])
@require_subsonic_auth
def search_legacy():
    """Legacy search (v1) — delegates to search2 logic."""
    return _search2_view()


@bp.route("/search2.view", methods=["GET", "POST"])
@bp.route("/search2", methods=["GET", "POST"])
@require_subsonic_auth
def _search2_view():
    query = request.args.get("query") or request.form.get("query") or ""
    try:
        artist_count = int(request.args.get("artistCount") or request.form.get("artistCount") or 20)
        album_count = int(request.args.get("albumCount") or request.form.get("albumCount") or 20)
        song_count = int(request.args.get("songCount") or request.form.get("songCount") or 20)
    except (ValueError, TypeError):
        return error(40, "Invalid count parameters.")
    return ok(_search2_response(query, artist_count, album_count, song_count))


@bp.route("/search3.view", methods=["GET", "POST"])
@bp.route("/search3", methods=["GET", "POST"])
@require_subsonic_auth
def search3():
    """ID3-based search — same data, ID3 wrapper."""
    query = request.args.get("query") or request.form.get("query") or ""
    try:
        artist_count = int(request.args.get("artistCount") or request.form.get("artistCount") or 20)
        album_count = int(request.args.get("albumCount") or request.form.get("albumCount") or 20)
        song_count = int(request.args.get("songCount") or request.form.get("songCount") or 20)
    except (ValueError, TypeError):
        return error(40, "Invalid count parameters.")
    artists, albums, tracks = _run_search(
        query, limit=max(artist_count, album_count, song_count, 20)
    )
    return ok(
        {
            "searchResult3": {
                "artist": [serialize_artist(a) for a in artists[:artist_count]],
                "album": [serialize_album(a) for a in albums[:album_count]],
                "song": [serialize_song(t) for t in tracks[:song_count]],
            }
        }
    )
