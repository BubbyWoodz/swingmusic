"""
Tests for the Subsonic/OpenSubsonic API (bubbywoodz fork).

These verify the response envelope, auth helpers, and serializer shapes
without requiring a running server.

NOTE: imports the dependency-free models module directly to avoid pulling
in Flask and the full app.
"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).parent.parent / "src"


def _load(modname: str, relpath: str):
    spec = importlib.util.spec_from_file_location(modname, SRC / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


models = _load("subsonic_models", "swingmusic/api/subsonic/models.py")

_envelope_src = (SRC / "swingmusic/api/subsonic/__init__.py").read_text()

song_id = models.song_id
album_id = models.album_id
artist_id = models.artist_id
serialize_song = models.serialize_song
serialize_album = models.serialize_album
serialize_artist = models.serialize_artist


def _envelope(payload: dict, status: str = "ok") -> dict:
    """Replicate the envelope logic for testing (mirrors __init__.py)."""
    return {
        "subsonic-response": {
            "status": status,
            "version": "1.16.1",
            "type": "swingmusic-lean",
            "serverVersion": "1.0.0-lean",
            "openSubsonic": True,
            **payload,
        }
    }


def test_envelope_format_in_source():
    """The envelope in __init__.py must match the expected structure."""
    assert '"subsonic-response"' in _envelope_src
    assert '"openSubsonic": True' in _envelope_src
    assert 'API_VERSION = "1.16.1"' in _envelope_src


def _make_track():
    return SimpleNamespace(
        trackhash="abc123def4",
        albumhash="alb5678901",
        title="Crush Culture",
        album="Sunset Season",
        artists=[{"name": "Conan Gray", "artisthash": "art9999888"}],
        albumartists=[{"name": "Conan Gray", "artisthash": "art9999888"}],
        duration=213,
        bitrate=850,
        date=2018,
        disc=1,
        track=3,
        filepath="/music/Conan Gray/Sunset Season/03 - Crush Culture.flac",
        genres=[{"name": "Pop", "genrehash": "gen123"}],
        playcount=42,
        last_mod=1700000000,
        artisthashes=["art9999888"],
        copyright="",
    )


def _make_album():
    return SimpleNamespace(
        albumhash="alb5678901",
        title="Sunset Season",
        albumartists=[{"name": "Conan Gray", "artisthash": "art9999888"}],
        date=2018,
        duration=1200,
        trackcount=5,
        playcount=100,
        created_date=1700000000,
        genres=[{"name": "Pop", "genrehash": "gen123"}],
    )


def _make_artist():
    return SimpleNamespace(
        artisthash="art9999888",
        name="Conan Gray",
        albumcount=3,
    )


def test_envelope_structure():
    env = _envelope({"ping": {}})
    resp = env["subsonic-response"]
    assert resp["status"] == "ok"
    assert resp["version"] == "1.16.1"
    assert resp["openSubsonic"] is True
    assert resp["type"] == "swingmusic-lean"


def test_envelope_error():
    env = _envelope({"error": {"code": 10, "message": "nope"}}, status="failed")
    assert env["subsonic-response"]["status"] == "failed"
    assert env["subsonic-response"]["error"]["code"] == 10


def test_id_mapping_uses_native_hashes():
    track = _make_track()
    album = _make_album()
    artist = _make_artist()
    assert song_id(track) == "abc123def4"
    assert album_id(album) == "alb5678901"
    assert artist_id(artist) == "art9999888"


def test_serialize_song_shape():
    s = serialize_song(_make_track())
    # Required Subsonic song fields
    for field in ("id", "title", "album", "artist", "duration", "coverArt", "albumId", "artistId"):
        assert field in s, f"missing {field}"
    assert s["id"] == "abc123def4"
    assert s["title"] == "Crush Culture"
    assert s["artist"] == "Conan Gray"
    assert s["albumId"] == "alb5678901"
    assert s["artistId"] == "art9999888"
    assert s["isDir"] is False
    assert s["type"] == "music"


def test_serialize_album_shape():
    a = serialize_album(_make_album())
    for field in ("id", "title", "artist", "coverArt", "songCount"):
        assert field in a, f"missing {field}"
    assert a["id"] == "alb5678901"
    assert a["isDir"] is True
    assert a["songCount"] == 5


def test_serialize_artist_shape():
    a = serialize_artist(_make_artist())
    assert a["id"] == "art9999888"
    assert a["name"] == "Conan Gray"
    assert a["albumCount"] == 3
    # coverArt uses ar- prefix to route correctly
    assert a["coverArt"] == "ar-art9999888"
