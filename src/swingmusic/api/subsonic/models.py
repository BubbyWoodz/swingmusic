"""Subsonic model serializers: Swing -> Subsonic JSON shapes. Dependency-free."""
def song_id(track) -> str:
    return track.trackhash


def album_id(album) -> str:
    return album.albumhash


def artist_id(artist) -> str:
    return artist.artisthash


# ---------------------------------------------------------------------------
# Model serializers: Swing -> Subsonic JSON shapes
# ---------------------------------------------------------------------------

def serialize_song(track, base_url: str = "") -> dict:
    """Map a Swing Track to a Subsonic <song>."""
    artists = track.artists or []
    artist_name = artists[0]["name"] if artists else ""
    artist_id_val = artists[0].get("artisthash", "") if artists else ""

    # Duration is in seconds in Swing; Subsonic wants seconds too
    duration = int(track.duration or 0)
    # bitrate is in kbps in Swing already
    bitrate = int(track.bitrate or 0)

    return {
        "id": song_id(track),
        "parent": album_id_from_track(track),
        "isDir": False,
        "title": track.title or "",
        "album": track.album or "",
        "artist": artist_name,
        "track": int(track.track or 0),
        "year": int(track.date or 0) or None,
        "genre": _genre_name(track),
        "coverArt": album_id_from_track(track),
        "size": _file_size(track),
        "contentType": _content_type(track),
        "suffix": _suffix(track),
        "duration": duration,
        "bitRate": bitrate,
        "path": track.filepath or "",
        "isVideo": False,
        "playCount": int(track.playcount or 0),
        "discNumber": int(track.disc or 0),
        "created": _iso_date(track.last_mod),
        "albumId": album_id_from_track(track),
        "artistId": artist_id_val,
        "type": "music",
    }


def album_id_from_track(track) -> str:
    return track.albumhash or ""


def serialize_album(album) -> dict:
    artists = album.albumartists or []
    artist_name = artists[0]["name"] if artists else ""
    return {
        "id": album_id(album),
        "parent": "",
        "isDir": True,
        "title": album.title or "",
        "album": album.title or "",
        "artist": artist_name,
        "year": int(album.date or 0) or None,
        "genre": _genre_name(album),
        "coverArt": album_id(album),
        "duration": int(album.duration or 0),
        "playCount": int(album.playcount or 0),
        "created": _iso_date(album.created_date),
        "songCount": int(album.trackcount or 0),
        "artistId": artists[0].get("artisthash", "") if artists else "",
    }


def serialize_artist(artist, include_albums: bool = False) -> dict:
    data = {
        "id": artist_id(artist),
        "name": artist.name or "",
        "coverArt": f"ar-{artist_id(artist)}",
        "albumCount": int(artist.albumcount or 0),
    }
    if include_albums:
        data["album"] = []
    return data


def _genre_name(obj) -> str | None:
    genres = getattr(obj, "genres", None)
    if isinstance(genres, list) and genres:
        first = genres[0]
        if isinstance(first, dict):
            return first.get("name")
        return str(first)
    if isinstance(genres, str) and genres:
        return genres
    return None


def _file_size(track) -> int:
    # Swing doesn't track file size in memory; return 0 (clients tolerate it)
    return 0


def _suffix(track) -> str:
    fp = (track.filepath or "").lower()
    if "." in fp:
        return fp.rsplit(".", 1)[-1]
    return ""


def _content_type(track) -> str:
    suffix = _suffix(track)
    return {
        "mp3": "audio/mpeg",
        "flac": "audio/flac",
        "m4a": "audio/mp4",
        "ogg": "audio/ogg",
        "opus": "audio/opus",
        "wav": "audio/wav",
        "wma": "audio/x-ms-wma",
    }.get(suffix, "audio/mpeg")


def _iso_date(ts) -> str | None:
    if not ts:
        return None
    try:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return None
