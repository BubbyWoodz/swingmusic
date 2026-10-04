"""
This module combines all API blueprints into a single Flask app instance.
"""

from swingmusic.api import (
    album,
    artist,
    classical,
    collections,
    colors,
    favorites,
    folder,
    imgserver,
    playlist,
    search,
    settings,
    lyrics,
    plugins,
    scrobble,
    home,
    getall,
    auth,
    stream,
    track,
    backup_and_restore,
    debug,  # bubbywoodz fork: memory diagnostics
)

from swingmusic.api.plugins import lyrics as lyrics_plugin
from swingmusic.api.sse import api as events

__all__ = [
    "album", "artist", "classical", "collections", "colors", "favorites", "folder", "imgserver", "playlist", "search", "settings",
    "lyrics", "plugins", "scrobble", "home", "getall", "auth", "stream", "track", "backup_and_restore", "events", "debug",

    "lyrics_plugin",
]
