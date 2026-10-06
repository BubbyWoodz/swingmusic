"""
Tests for the play-validity gate (bubbywoodz fork, Feature 3).

Tests the Last.fm rule logic and timestamp policy as pure functions.
Mirrors the logic in src/swingmusic/api/scrobble/__init__.py::log_track.

Rhydian's decisions:
1. Threshold: FIXED (Last.fm rule — half duration or 4 min, whichever first)
2. Sub-threshold skips: KEPT with counted=false
3. Backfill window: 30 days
"""

import time

# Constants from the implementation (verified by test_constants_match_source)
BACKFILL_WINDOW_SECONDS = 30 * 24 * 3600
FUTURE_SKEW_TOLERANCE = 600


def _is_counted(track_duration: int, listened_duration: int) -> bool:
    """
    Mirror of the play-validity gate in log_track.
    Returns True if the play counts toward charts/Replay.
    """
    threshold = min(track_duration / 2, 240) if track_duration > 30 else float("inf")
    return bool(listened_duration >= threshold)


def _validate_timestamp(timestamp: int, now: int) -> str | None:
    """
    Mirror of the timestamp policy in log_track.
    Returns an error message or None if valid.
    """
    if timestamp > now + FUTURE_SKEW_TOLERANCE:
        return "Timestamp is in the future."
    if timestamp < now - BACKFILL_WINDOW_SECONDS:
        return "Timestamp is older than the 30-day backfill window."
    return None


def test_constants_match_source():
    """Our test constants must match the source file."""
    from pathlib import Path

    src = (
        Path(__file__).parent.parent
        / "src"
        / "swingmusic"
        / "api"
        / "scrobble"
        / "__init__.py"
    )
    text = src.read_text()
    assert "30 * 24 * 3600" in text  # 30-day backfill
    assert "now + 600" in text  # 10-minute future tolerance
    assert "min(track_len / 2, 240)" in text  # Last.fm rule
    assert "track_len > 30" in text


def test_threshold_half_duration():
    """A 180s track counts at 90s (half)."""
    assert _is_counted(180, 90) is True
    assert _is_counted(180, 89) is False


def test_threshold_four_minute_cap():
    """A 600s (10min) track counts at 240s (4min cap), not 300s."""
    assert _is_counted(600, 240) is True
    assert _is_counted(600, 239) is False


def test_threshold_short_track_never_counts():
    """Tracks ≤30s never count (Last.fm rule)."""
    assert _is_counted(30, 30) is False
    assert _is_counted(25, 25) is False
    assert _is_counted(31, 16) is True  # 31s track, half = 15.5s


def test_threshold_exact_boundary():
    """Exactly at threshold counts."""
    assert _is_counted(200, 100) is True  # half of 200
    assert _is_counted(480, 240) is True  # 4min cap


def test_timestamp_future_rejected():
    """Timestamps >10min in the future are rejected."""
    now = int(time.time())
    assert _validate_timestamp(now + 601, now) is not None
    assert _validate_timestamp(now + 599, now) is None
    assert _validate_timestamp(now, now) is None


def test_timestamp_backfill_window():
    """Timestamps older than 30 days are rejected."""
    now = int(time.time())
    assert _validate_timestamp(now - BACKFILL_WINDOW_SECONDS + 100, now) is None
    assert _validate_timestamp(now - BACKFILL_WINDOW_SECONDS - 100, now) is not None


def test_sub_threshold_kept_not_dropped():
    """
    Sub-threshold plays are KEPT with counted=false (not dropped).
    This is verified by the source containing the counted flag logic.
    """
    from pathlib import Path

    src = (
        Path(__file__).parent.parent
        / "src"
        / "swingmusic"
        / "api"
        / "scrobble"
        / "__init__.py"
    )
    text = src.read_text()
    assert 'scrobble_data["counted"] = counted' in text
    assert '"not-counted"' in text
