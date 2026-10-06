"""
Tests for Connect device sync (bubbywoodz fork, Feature 4).

Tests the session registry and playback state logic as pure functions
in src/swingmusic/utils/connect.py.
"""

import time

from swingmusic.utils.connect import ConnectRegistry, STALE_AFTER


def _reg() -> ConnectRegistry:
    return ConnectRegistry()


def test_register_generates_server_session_id():
    r = _reg()
    s1 = r.register(1, "Phone", app_type="ios")
    s2 = r.register(1, "Phone", app_type="ios")
    # Server-generated: unique even for identical client-declared names.
    assert s1.session_id != s2.session_id
    assert len(s1.session_id) == 36  # UUID4


def test_sessions_isolated_per_user():
    r = _reg()
    r.register(1, "Alice phone")
    r.register(2, "Bob phone")
    assert len(r.list_sessions(1)) == 1
    assert len(r.list_sessions(2)) == 1
    assert r.list_sessions(1)[0].device_name == "Alice phone"


def test_heartbeat_refreshes_last_seen():
    r = _reg()
    s = r.register(1, "Phone")
    s.last_seen = time.time() - 100  # simulate old heartbeat
    assert s.is_stale
    assert r.heartbeat(1, s.session_id) is not None
    assert not s.is_stale


def test_heartbeat_unknown_session_returns_none():
    r = _reg()
    assert r.heartbeat(1, "nonexistent") is None


def test_disconnect_removes_session():
    r = _reg()
    s = r.register(1, "Phone")
    assert r.disconnect(1, s.session_id) is True
    assert r.disconnect(1, s.session_id) is False  # idempotent
    assert r.list_sessions(1) == []


def test_disconnect_active_player_freezes_state():
    r = _reg()
    s = r.register(1, "Phone")
    r.update_state(1, s.session_id, trackhash="abc", is_playing=True,
                   position_ms=5000)
    r.disconnect(1, s.session_id)
    state = r.get_state(1)
    assert state.is_playing is False
    assert state.active_session_id is None
    # Track is preserved so another device can take over from here.
    assert state.trackhash == "abc"


def test_position_extrapolation():
    r = _reg()
    s = r.register(1, "Phone")
    r.update_state(1, s.session_id, trackhash="abc", is_playing=True,
                   position_ms=10000)
    state = r.get_state(1)
    # Playing: extrapolated position moves forward with wall clock.
    time.sleep(0.05)
    assert state.extrapolated_position_ms() > 10000

    r.update_state(1, s.session_id, is_playing=False)
    frozen = state.extrapolated_position_ms()
    time.sleep(0.02)
    # Paused: position is frozen.
    assert state.extrapolated_position_ms() == frozen


def test_new_track_resets_position():
    r = _reg()
    s = r.register(1, "Phone")
    r.update_state(1, s.session_id, trackhash="aaa", position_ms=60000)
    r.update_state(1, s.session_id, trackhash="bbb")
    assert r.get_state(1).position_ms == 0


def test_volume_clamped():
    r = _reg()
    s = r.register(1, "Phone")
    r.update_state(1, s.session_id, volume=150)
    assert r.get_state(1).volume == 100
    r.update_state(1, s.session_id, volume=-5)
    assert r.get_state(1).volume == 0
    # Mirrored onto the session for the devices list.
    assert s.volume == 0


def test_queue_capped():
    r = _reg()
    s = r.register(1, "Phone")
    r.update_state(1, s.session_id, queue=["t"] * 1000)
    assert len(r.get_state(1).queue) == 500


def test_transfer_handoff_payload():
    r = _reg()
    phone = r.register(1, "Phone")
    tv = r.register(1, "TV", app_type="tv")
    r.update_state(1, phone.session_id, trackhash="abc",
                   queue=["abc", "def"], queue_index=0,
                   position_ms=30000, is_playing=True)
    handoff = r.transfer(1, tv.session_id)
    assert handoff is not None
    assert handoff["trackhash"] == "abc"
    assert handoff["position_ms"] >= 30000  # extrapolated forward
    assert handoff["queue"] == ["abc", "def"]
    assert handoff["is_playing"] is True
    # Active player moved to the TV.
    assert r.get_state(1).active_session_id == tv.session_id


def test_transfer_requires_audio_capable_target():
    r = _reg()
    phone = r.register(1, "Phone")
    remote = r.register(1, "Remote", app_type="other", can_play_audio=False)
    r.update_state(1, phone.session_id, trackhash="abc")
    assert r.transfer(1, remote.session_id) is None


def test_transfer_with_nothing_playing_returns_none():
    r = _reg()
    tv = r.register(1, "TV", app_type="tv")
    assert r.transfer(1, tv.session_id) is None


def test_stale_sessions_flagged_not_dropped_immediately():
    r = _reg()
    s = r.register(1, "Phone")
    s.last_seen = time.time() - (STALE_AFTER + 1)
    sessions = r.list_sessions(1)
    assert len(sessions) == 1
    assert sessions[0].is_stale is True
    # Excluding stale hides it.
    assert r.list_sessions(1, include_stale=False) == []


def test_state_dict_shape():
    r = _reg()
    s = r.register(1, "Phone")
    r.update_state(1, s.session_id, trackhash="abc", is_playing=True)
    d = r.get_state(1).todict()
    for key in ("trackhash", "queue", "queue_index", "position_ms",
                "as_of", "is_playing", "volume", "active_session_id"):
        assert key in d
