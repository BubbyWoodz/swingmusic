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
    subsonic,  # bubbywoodz fork: Subsonic/OpenSubsonic API
    replay,  # bubbywoodz fork: Replay stats
    connect,  # bubbywoodz fork: Connect device sync
)

from swingmusic.api.plugins import lyrics as lyrics_plugin
from swingmusic.api.sse import api as events

__all__ = [
    "album", "artist", "classical", "collections", "colors", "favorites", "folder", "imgserver", "playlist", "search", "settings",
    "lyrics", "plugins", "scrobble", "home", "getall", "auth", "stream", "track", "backup_and_restore", "events", "debug",
    "subsonic", "replay", "connect",

    "lyrics_plugin",
]
