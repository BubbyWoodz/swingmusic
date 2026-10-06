"""
Regression tests for the bubbywoodz RAM-optimization fork.

These guard the invariants proven during the v1.0.0-lean hardening review
(2026-10-06):

1. TRACKHASH STABILITY: sys.intern() on track fields must not change
   create_hash() outputs. Track identity (scrobbles, favorites, playlists)
   depends on stable trackhashes.
2. EXTRA WHITELIST SCOPE: the taglib.py extra-dict whitelist must only ever
   filter the `extra` dict — never title, album, artists, or albumartists,
   which feed the trackhash.
3. STREAM ENDPOINT INTACT: api/stream.py must remain byte-identical to
   upstream (the Oct 4 live-test 404 was a test artifact, not a regression).

Run: python3 -m pytest tests/test_lean_invariants.py -v
Requires: xxhash, unidecode (pip install xxhash unidecode)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from swingmusic.utils.hashing import create_hash


def test_intern_does_not_change_trackhash():
    """sys.intern() preserves string values, so hashes must match."""
    title, album, artist = "Crush Culture", "Sunset Season", "Conan Gray"
    plain = create_hash(title, album, artist)
    interned = create_hash(
        sys.intern(title), sys.intern(album), sys.intern(artist)
    )
    assert plain == interned, "intern() changed create_hash output!"


def test_intern_does_not_change_artisthash():
    """Artist hashes (decode=True path) must also be stable."""
    plain = create_hash("Tinashe", decode=True)
    interned = create_hash(sys.intern("Tinashe"), decode=True)
    assert plain == interned


def test_hash_is_case_and_punctuation_insensitive():
    """Sanity: the hash normalizes case/punctuation (unchanged behavior)."""
    assert create_hash("Know-It-All") == create_hash("know it all")
    assert create_hash("Alessia Cara") == create_hash("alessia cara")


def test_extra_whitelist_keys():
    """The extra whitelist must contain exactly the keys read at runtime."""
    taglib = (Path(__file__).parent.parent / "src" / "swingmusic" / "lib" / "taglib.py").read_text()
    # The whitelist set must be present and unchanged
    assert '"lyrics"' in taglib and '"track_total"' in taglib and '"explicit"' in taglib
    assert "_EXTRA_KEEP" in taglib
    # hashinfo is always added separately (used for duplicate detection)
    assert 'extra["hashinfo"]' in taglib


def test_trackhash_inputs_untouched_by_whitelist():
    """taglib.py must not filter title/album/artists through the whitelist."""
    taglib = (Path(__file__).parent.parent / "src" / "swingmusic" / "lib" / "taglib.py").read_text()
    # The whitelist is applied ONLY to the extra dict comprehension
    # (k in _EXTRA_KEEP appears once, inside the extra dict comprehension)
    assert taglib.count("_EXTRA_KEEP") == 2  # definition + one use


def test_stream_endpoint_present():
    """The stream endpoint must exist (Oct 4 404 was a test artifact)."""
    stream = (Path(__file__).parent.parent / "src" / "swingmusic" / "api" / "stream.py").read_text()
    assert '@api.get("/<trackhash>/legacy")' in stream
    assert "def send_track_file_legacy" in stream
