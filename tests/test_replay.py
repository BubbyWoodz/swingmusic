"""
Tests for Replay utilities (bubbywoodz fork).

Tests the pure logic of milestone detection and streak detection
using synthetic data. DB-backed functions are tested via integration.

The threshold constants are verified against the source file to prevent drift.
"""

from pathlib import Path
from types import SimpleNamespace

# Mirrors swingmusic/utils/replay.py — verified by test_thresholds_match_source
MINUTE_MILESTONES = [1000, 5000, 10000, 25000, 50000, 100000]
SONG_MILESTONES = [100, 1000, 5000, 10000]


def _make_scrobble(timestamp: int, duration: int = 180, trackhash: str = "abc"):
    return SimpleNamespace(
        timestamp=timestamp,
        duration=duration,
        trackhash=trackhash,
    )


def test_thresholds_match_source():
    """Our test constants must match the source file."""
    src = Path(__file__).parent.parent / "src" / "swingmusic" / "utils" / "replay.py"
    text = src.read_text()
    assert "MINUTE_MILESTONES = [1000, 5000, 10000, 25000, 50000, 100000]" in text
    assert "SONG_MILESTONES = [100, 1000, 5000, 10000]" in text


def test_milestone_thresholds_sane():
    """Milestone thresholds should be in ascending order."""
    assert MINUTE_MILESTONES == sorted(MINUTE_MILESTONES)
    assert SONG_MILESTONES == sorted(SONG_MILESTONES)
    assert MINUTE_MILESTONES[0] == 1000
    assert SONG_MILESTONES[0] == 100


def test_find_milestones_logic():
    """Simulate the milestone scan logic with synthetic data."""
    scrobbles = [_make_scrobble(1700000000 + i * 200, 180) for i in range(400)]

    total_minutes = 0
    total_songs = 0
    minute_idx = 0
    song_idx = 0
    found = []

    for s in scrobbles:
        total_minutes += s.duration / 60
        total_songs += 1
        while minute_idx < len(MINUTE_MILESTONES) and total_minutes >= MINUTE_MILESTONES[minute_idx]:
            found.append(("minutes", MINUTE_MILESTONES[minute_idx]))
            minute_idx += 1
        while song_idx < len(SONG_MILESTONES) and total_songs >= SONG_MILESTONES[song_idx]:
            found.append(("songs", SONG_MILESTONES[song_idx]))
            song_idx += 1

    # 400 scrobbles * 180s = 72000s = 1200 minutes -> crosses 1000-minute milestone
    # 400 scrobbles -> crosses 100-song milestone
    assert ("minutes", 1000) in found
    assert ("songs", 100) in found
    assert ("minutes", 5000) not in found  # not enough


def test_streak_detection_logic():
    """Two consecutive months with same #1 artist = streak."""
    monthly = [
        {"month": 1, "top_artist": {"artisthash": "a1", "name": "Artist One"}},
        {"month": 2, "top_artist": {"artisthash": "a1", "name": "Artist One"}},
        {"month": 3, "top_artist": {"artisthash": "a2", "name": "Artist Two"}},
        {"month": 4, "top_artist": {"artisthash": "a2", "name": "Artist Two"}},
        {"month": 5, "top_artist": {"artisthash": "a2", "name": "Artist Two"}},
    ]

    streaks = []
    current_artist = None
    current_months = []
    for entry in monthly:
        top = entry.get("top_artist")
        ah = top["artisthash"] if top else None
        if ah and ah == current_artist:
            current_months.append(entry["month"])
        else:
            if current_artist and len(current_months) >= 2:
                streaks.append({"artisthash": current_artist, "months": list(current_months)})
            current_artist = ah
            current_months = [entry["month"]] if ah else []
    if current_artist and len(current_months) >= 2:
        streaks.append({"artisthash": current_artist, "months": list(current_months)})

    assert len(streaks) == 2
    assert streaks[0]["artisthash"] == "a1"
    assert streaks[0]["months"] == [1, 2]
    assert streaks[1]["artisthash"] == "a2"
    assert streaks[1]["months"] == [3, 4, 5]
