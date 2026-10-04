from typing import Any

from swingmusic.config import UserConfig
from swingmusic.models import Album as AlbumModel, Artist as ArtistModel, Track as TrackModel
from swingmusic.models.favorite import Favorite
from swingmusic.models.lastfm import SimilarArtist
from swingmusic.models.logger import TrackLog
from swingmusic.models.playlist import Playlist
from swingmusic.models.plugins import Plugin
from swingmusic.models.user import User


def row_to_dict(row: Any):
    d = row.__dict__
    del d["_sa_instance_state"]
    return d


def track_to_dataclass(track: dict, config: UserConfig):
    # PERF (bubbywoodz): strip lyrics from the in-memory Track. Lyrics are
    # kept in the DB's extra JSON (see lib/taglib.py) and loaded on demand
    # by lib/lyrics.py:get_lyrics_from_tags. Holding full lyrics for every
    # track in RAM was ~800KB for 575 tracks (~36% of extra-dict memory).
    extra = track.get("extra")
    if isinstance(extra, dict) and "lyrics" in extra:
        track = {**track, "extra": {k: v for k, v in extra.items() if k != "lyrics"}}
    return TrackModel(**track, config=config)


def tracks_to_dataclasses(tracks: Any):
    return [track_to_dataclass(track, UserConfig()) for track in tracks]


def album_to_dataclass(album: Any):
    return AlbumModel(**album._asdict())


def albums_to_dataclasses(albums: Any):
    return [album_to_dataclass(album) for album in albums]


def artist_to_dataclass(artist: Any):
    return ArtistModel(**artist._asdict())


def artists_to_dataclasses(artists: Any):
    return [artist_to_dataclass(artist) for artist in artists]


# SECTION: User data helpers
def similar_artist_to_dataclass(entry: Any):
    entry_dict = row_to_dict(entry)
    del entry_dict["id"]

    return SimilarArtist(**entry_dict)


def similar_artists_to_dataclass(entries: Any):
    return [similar_artist_to_dataclass(entry) for entry in entries]


def favorite_to_dataclass(entry: Any):
    entry_dict = row_to_dict(entry)
    del entry_dict["id"]

    return Favorite(**entry_dict)


def favorites_to_dataclass(entries: Any):
    return [favorite_to_dataclass(entry) for entry in entries]


def user_to_dataclass(entry: Any):
    return User(**row_to_dict(entry))


# def user_to_dataclasses(entries: Any):
#     return [user_to_dataclass(entry) for entry in entries]


def plugin_to_dataclass(entry: Any):
    entry_dict = row_to_dict(entry)
    del entry_dict["id"]
    return Plugin(**entry_dict)


def plugin_to_dataclasses(entries: Any):
    return [plugin_to_dataclass(entry) for entry in entries]


def tracklog_to_dataclass(entry: Any):
    return TrackLog(**row_to_dict(entry))


def tracklog_to_dataclasses(entries: Any):
    return [tracklog_to_dataclass(entry) for entry in entries]


def playlist_to_dataclass(entry: Any):
    entry_dict = row_to_dict(entry)
    return Playlist(**entry_dict)


def playlists_to_dataclasses(entries: Any):
    return [playlist_to_dataclass(entry) for entry in entries]
