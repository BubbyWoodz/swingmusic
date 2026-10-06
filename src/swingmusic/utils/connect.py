"""
Connect session registry and per-user playback state (bubbywoodz fork).

Spotify Connect-style device sync. The server is the single source of truth
per user: which device is playing, current track, queue, position, volume.

Design notes (from connect-research.md):
- Session IDs are server-generated UUIDs. Never trust client device IDs
  (Spotify's own docs warn they are unstable).
- Position is always (position_ms, as_of_timestamp) so clients can
  extrapolate the progress bar smoothly between heartbeats.
- The playing device streams audio independently from our server.
  The controller never proxies audio.
- Heartbeat timeout marks devices stale; state freezes; another device
  can take over.

This module is pure logic (no Flask) so it can be unit-tested directly.
The API layer (api/connect) handles HTTP + SSE dispatch.
"""

import time
import uuid
from dataclasses import dataclass, field


# Seconds without a heartbeat before a device is considered stale.
STALE_AFTER = 30

# Max queue length stored per user (mirrors Aurora's queue-copy approach).
MAX_QUEUE = 500


@dataclass
class DeviceSession:
    """A connected client. Identity is server-generated."""

    session_id: str
    userid: int
    device_name: str
    app_type: str  # web | ios | android | desktop | tv | other
    can_play_audio: bool
    volume: int = 100
    last_seen: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)

    @property
    def is_stale(self) -> bool:
        return (time.time() - self.last_seen) > STALE_AFTER

    def todict(self) -> dict:
        return {
            "session_id": self.session_id,
            "device_name": self.device_name,
            "app_type": self.app_type,
            "can_play_audio": self.can_play_audio,
            "volume": self.volume,
            "last_seen": self.last_seen,
            "is_stale": self.is_stale,
        }


@dataclass
class PlaybackState:
    """Canonical per-user playback state."""

    userid: int
    trackhash: str | None = None
    queue: list = field(default_factory=list)
    queue_index: int = 0
    position_ms: int = 0
    as_of: float = field(default_factory=time.time)
    is_playing: bool = False
    volume: int = 100
    active_session_id: str | None = None
    updated_at: float = field(default_factory=time.time)

    def extrapolated_position_ms(self) -> int:
        """Position right now, extrapolating from the last reported point."""
        if not self.is_playing or not self.trackhash:
            return self.position_ms
        elapsed_ms = int((time.time() - self.as_of) * 1000)
        return self.position_ms + elapsed_ms

    def todict(self, extrapolate: bool = True) -> dict:
        pos = self.extrapolated_position_ms() if extrapolate else self.position_ms
        return {
            "trackhash": self.trackhash,
            "queue": self.queue,
            "queue_index": self.queue_index,
            "position_ms": pos,
            "position_reported_ms": self.position_ms,
            "as_of": self.as_of,
            "is_playing": self.is_playing,
            "volume": self.volume,
            "active_session_id": self.active_session_id,
            "updated_at": self.updated_at,
        }


class ConnectRegistry:
    """
    In-memory registry. Keyed by userid for isolation.
    Transient only — rebuilt from heartbeats after a restart.
    """

    def __init__(self):
        # {userid: {session_id: DeviceSession}}
        self._sessions: dict[int, dict[str, DeviceSession]] = {}
        # {userid: PlaybackState}
        self._states: dict[int, PlaybackState] = {}

    # -- sessions ------------------------------------------------------

    def register(
        self,
        userid: int,
        device_name: str,
        app_type: str = "web",
        can_play_audio: bool = True,
    ) -> DeviceSession:
        session = DeviceSession(
            session_id=str(uuid.uuid4()),
            userid=userid,
            device_name=device_name or "Unknown device",
            app_type=app_type,
            can_play_audio=bool(can_play_audio),
        )
        self._sessions.setdefault(userid, {})[session.session_id] = session
        return session

    def heartbeat(self, userid: int, session_id: str) -> DeviceSession | None:
        session = self._sessions.get(userid, {}).get(session_id)
        if session:
            session.last_seen = time.time()
        return session

    def get_session(self, userid: int, session_id: str) -> DeviceSession | None:
        return self._sessions.get(userid, {}).get(session_id)

    def list_sessions(self, userid: int, include_stale: bool = True) -> list[DeviceSession]:
        sessions = list(self._sessions.get(userid, {}).values())
        # Prune sessions stale for a long time (10x timeout) to bound memory.
        now = time.time()
        for s in sessions:
            if now - s.last_seen > STALE_AFTER * 10:
                self._sessions[userid].pop(s.session_id, None)
        sessions = list(self._sessions.get(userid, {}).values())
        if not include_stale:
            sessions = [s for s in sessions if not s.is_stale]
        return sorted(sessions, key=lambda s: s.last_seen, reverse=True)

    def disconnect(self, userid: int, session_id: str) -> bool:
        """Remove a session. Returns True if it existed."""
        sessions = self._sessions.get(userid, {})
        if session_id in sessions:
            del sessions[session_id]
            # If the removed session was the active player, freeze state
            # (position stays; another device can take over).
            state = self._states.get(userid)
            if state and state.active_session_id == session_id:
                state.active_session_id = None
                state.is_playing = False
                state.as_of = time.time()
                state.updated_at = time.time()
            return True
        return False

    # -- playback state -------------------------------------------------

    def get_state(self, userid: int) -> PlaybackState:
        if userid not in self._states:
            self._states[userid] = PlaybackState(userid=userid)
        return self._states[userid]

    def update_state(self, userid: int, session_id: str, **fields) -> PlaybackState:
        """
        Update playback state from a device heartbeat or command.
        Allowed fields: trackhash, queue, queue_index, position_ms,
        is_playing, volume.
        """
        state = self.get_state(userid)
        now = time.time()

        if "trackhash" in fields:
            # New track resets the position clock.
            if fields["trackhash"] != state.trackhash:
                state.position_ms = 0
            state.trackhash = fields["trackhash"]
        if "queue" in fields and isinstance(fields["queue"], list):
            state.queue = fields["queue"][:MAX_QUEUE]
        if "queue_index" in fields:
            state.queue_index = max(0, int(fields["queue_index"]))
        if "position_ms" in fields:
            state.position_ms = max(0, int(fields["position_ms"]))
            state.as_of = now
        if "is_playing" in fields:
            # Re-anchor the position clock on play/pause transitions.
            if bool(fields["is_playing"]) != state.is_playing:
                state.position_ms = state.extrapolated_position_ms()
                state.as_of = now
            state.is_playing = bool(fields["is_playing"])
        if "volume" in fields:
            state.volume = max(0, min(100, int(fields["volume"])))

        state.active_session_id = session_id
        state.updated_at = now

        # Mirror volume onto the session for the devices list.
        session = self.get_session(userid, session_id)
        if session and "volume" in fields:
            session.volume = state.volume

        return state

    def transfer(self, userid: int, target_session_id: str) -> dict | None:
        """
        Hand playback to another device. Returns the handoff payload the
        target needs to start streaming independently:
        {trackhash, position_ms, as_of, queue, queue_index, is_playing}.
        Returns None if there is nothing to hand off.
        """
        target = self.get_session(userid, target_session_id)
        if not target or not target.can_play_audio:
            return None
        state = self.get_state(userid)
        if not state.trackhash:
            return None

        now = time.time()
        state.active_session_id = target_session_id
        state.as_of = now
        state.updated_at = now

        return {
            "trackhash": state.trackhash,
            "position_ms": state.extrapolated_position_ms(),
            "as_of": now,
            "queue": state.queue,
            "queue_index": state.queue_index,
            "is_playing": state.is_playing,
            "volume": target.volume,
        }


# Module-level singleton (same pattern as _now_playing in scrobble).
registry = ConnectRegistry()
