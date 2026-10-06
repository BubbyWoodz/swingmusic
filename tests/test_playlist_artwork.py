"""
Tests for custom playlist artwork (reverb fork).

Covers the artwork validation/normalization helper used by the
dedicated /playlists/<id>/artwork endpoints: validate format,
cap dimensions, normalize mode.

playlistlib pulls heavy deps (Flask, stores) at import time, so we
load it with stubbed modules — same pattern as test_subsonic.py.
"""

import importlib.util
import io
import sys
import types
from pathlib import Path

import pytest
from PIL import Image

SRC = Path(__file__).parent.parent / "src"


def _stub(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


# Stub the heavy dependency chain before loading playlistlib.
# (Stubs are created inside _load_playlistlib and cleaned up after.)


def _load_playlistlib():
    # Save any real modules so we can restore them after (avoid polluting
    # other tests when the full suite runs).
    saved = {}
    for name in (
        "swingmusic",
        "swingmusic.models",
        "swingmusic.models.track",
        "swingmusic.store",
        "swingmusic.store.albums",
        "swingmusic.store.tracks",
    ):
        if name in sys.modules:
            saved[name] = sys.modules[name]

    try:
        _stub("swingmusic")
        sys.modules["swingmusic"].settings = types.SimpleNamespace()
        _stub("swingmusic.models")
        _stub("swingmusic.models.track", Track=object)
        _stub("swingmusic.store")
        _stub("swingmusic.store.albums", AlbumStore=object)
        _stub("swingmusic.store.tracks", TrackStore=object)

        spec = importlib.util.spec_from_file_location(
            "playlistlib_under_test", SRC / "swingmusic/lib/playlistlib.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["playlistlib_under_test"] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        for name in (
            "swingmusic",
            "swingmusic.models",
            "swingmusic.models.track",
            "swingmusic.store",
            "swingmusic.store.albums",
            "swingmusic.store.tracks",
            "playlistlib_under_test",
        ):
            sys.modules.pop(name, None)
        sys.modules.update(saved)


pl = _load_playlistlib()
prepare_artwork = pl.prepare_artwork
ARTWORK_ALLOWED_FORMATS = pl.ARTWORK_ALLOWED_FORMATS
ARTWORK_MAX_DIM = pl.ARTWORK_MAX_DIM


def _roundtrip(size, fmt, mode="RGB", color=(255, 0, 0)):
    """Simulate an upload: save to bytes and re-open (sets .format)."""
    img = Image.new(mode, size, color)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    buf.seek(0)
    return Image.open(buf)


def test_allowed_formats_constant():
    assert ARTWORK_ALLOWED_FORMATS == {"JPEG", "PNG", "WEBP"}
    assert ARTWORK_MAX_DIM == 1500


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_valid_formats_accepted(fmt):
    img = _roundtrip((100, 100), fmt)
    out = prepare_artwork(img)
    assert out.size == (100, 100)


@pytest.mark.parametrize("fmt", ["GIF", "BMP", "TIFF"])
def test_invalid_formats_rejected(fmt):
    img = _roundtrip((100, 100), fmt)
    with pytest.raises(ValueError, match="Unsupported image format"):
        prepare_artwork(img)


def test_unknown_format_rejected():
    img = Image.new("RGB", (100, 100))
    img.format = None
    with pytest.raises(ValueError, match="Unsupported image format"):
        prepare_artwork(img)


def test_large_image_resized_to_cap():
    img = _roundtrip((3000, 2000), "JPEG")
    out = prepare_artwork(img)
    assert max(out.size) == ARTWORK_MAX_DIM
    # aspect ratio preserved: 3000x2000 -> 1500x1000
    assert out.size == (1500, 1000)


def test_small_image_untouched():
    img = _roundtrip((800, 600), "PNG")
    out = prepare_artwork(img)
    assert out.size == (800, 600)


def test_exactly_at_cap_untouched():
    img = _roundtrip((1500, 1500), "WEBP")
    out = prepare_artwork(img)
    assert out.size == (1500, 1500)


def test_portrait_image_resized():
    img = _roundtrip((1000, 3000), "JPEG")
    out = prepare_artwork(img)
    assert out.size == (500, 1500)


def test_palette_mode_converted_to_rgb():
    img = _roundtrip((100, 100), "PNG", mode="P")
    out = prepare_artwork(img)
    assert out.mode == "RGB"
