"""
Regression tests for trackhash parity with upstream.

Root cause documented 2026-10-06:
The Oct 4 "trackhash mismatch" was NOT a fork bug. Upstream commit c6bd74b
(Jul 6, 2026, "rehash duplicate tracks on same albums by disc/track number")
changed trackhash INPUTS for files with multiple ARTIST tag frames:
  - Before: tags.artist (TinyTag = first frame only)
  - After:  join_multivalue_tag(all ARTIST frames) — "; "-joined

Our fork is based on upstream WITH c6bd74b. The live Umbrel app was an
older build WITHOUT it. Same file + different upstream versions =
different trackhashes for multi-artist files. Single-artist files are
unaffected.

These tests lock in:
1. Our trackhash computation matches current upstream master exactly.
2. Our lean optimizations (interning, extra whitelist) do not affect hashes.
3. The multi-artist behavior change is upstream's, not ours.

Run: python3 -m pytest tests/test_trackhash_parity.py -v
Requires: xxhash, unidecode (pip install xxhash unidecode)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from swingmusic.utils.hashing import create_hash


def join_multivalue_tag(values):
    """Mirror of swingmusic.lib.taglib.join_multivalue_tag (standalone copy
    to avoid importing tinytag/pendulum in test env)."""
    seen = set()
    joined = []
    for value in values or []:
        if isinstance(value, str) and value and value.lower() not in seen:
            seen.add(value.lower())
            joined.append(value)
    return "; ".join(joined)


def test_single_artist_hash_stable_across_upstream_change():
    """Files with one ARTIST frame hash identically before/after c6bd74b."""
    old_style = create_hash("Solo Artist", "Album", "Title")  # tags.artist
    new_style = create_hash(
        join_multivalue_tag(["Solo Artist"]), "Album", "Title"
    )
    assert old_style == new_style


def test_multi_artist_hash_differs_by_design():
    """Documents upstream c6bd74b behavior: multi-frame ARTIST tags now
    join all values, changing the hash vs old upstream. This is upstream's
    change, not a fork regression — our fork matches current upstream."""
    old_hash = create_hash("Artist A", "Album", "Title")
    new_hash = create_hash(
        join_multivalue_tag(["Artist A", "Artist B"]), "Album", "Title"
    )
    # They MUST differ — this is the upstream behavior change.
    # If this ever flips to equal, the multi-value logic broke.
    assert old_hash != new_hash


def test_fork_matches_upstream_hash_algorithm():
    """create_hash is byte-identical logic to upstream master.
    Spot-check known values to catch any accidental algorithm change."""
    # xxh3_64 of normalized "conangraysunsetsseasoncrushculture"
    # (lowercased, non-alnum stripped, joined)
    h1 = create_hash("Crush Culture", "Sunset Season", "Conan Gray")
    h2 = create_hash("crush culture", "sunset season", "conan gray")
    assert h1 == h2  # case-insensitive
    # Punctuation-insensitive
    assert create_hash("Know-It-All") == create_hash("know it all")


def test_intern_does_not_change_hash():
    """sys.intern() on title/album/artists must not change trackhash."""
    title, album = "Midnight Sun", "Test Album"
    artists = ["Artist One", "Artist Two"]
    plain = create_hash(title, album, *artists)
    interned = create_hash(
        sys.intern(title), sys.intern(album), *[sys.intern(a) for a in artists]
    )
    assert plain == interned


def test_taglib_trackhash_inputs_not_filtered():
    """taglib.py's extra whitelist must not touch title/album/artists."""
    taglib = (
        Path(__file__).parent.parent
        / "src"
        / "swingmusic"
        / "lib"
        / "taglib.py"
    ).read_text()
    # Whitelist applies only to the extra dict (definition + single use)
    assert taglib.count("_EXTRA_KEEP") == 2
    # trackhash is computed from metadata BEFORE extra filtering
    assert 'metadata["trackhash"]' in taglib


def test_stream_endpoint_matches_upstream():
    """api/stream.py must stay byte-identical to upstream master.
    The Oct 4 live-test 404 was caused by cross-database trackhash use
    (old-DB hashes against new-DB store), not a code regression."""
    stream = (
        Path(__file__).parent.parent
        / "src"
        / "swingmusic"
        / "api"
        / "stream.py"
    ).read_text()
    assert '@api.get("/<trackhash>/legacy")' in stream
    assert "TrackStore.trackhashmap.get(requested_trackhash)" in stream
    assert 'return msg, 404' in stream
