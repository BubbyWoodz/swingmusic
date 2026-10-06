"""
Connect API: Spotify Connect-style device sync (bubbywoodz fork).

- POST /connect/register — register a device, get a server-generated session_id
- POST /connect/heartbeat — device heartbeat + playback state update
- GET  /connect/state — current per-user playback state (extrapolated position)
- GET  /connect/devices — list this user's connected devices
- DELETE /connect/devices/<session_id> — remotely disconnect a device
- PUT  /connect/play | /connect/pause — remote play/pause
- POST /connect/next | /connect/previous — remote track skip
- PUT  /connect/seek — remote seek {position_ms}
- PUT  /connect/volume — remote volume {volume_percent}
- PUT  /connect/transfer — hand playback to another device {session_id}

State changes are pushed to all of the user's clients over the existing
SSE event bus (/events/stream) as `connect_state` events.
"""

from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

from swingmusic.events import events
from swingmusic.utils.auth import get_current_userid
from swingmusic.utils.connect import registry

bp_tag = Tag(name="Connect", description="Spotify Connect-style device sync")
api = APIBlueprint("connect", __name__, url_prefix="/connect", abp_tags=[bp_tag])

VALID_APP_TYPES = {"web", "ios", "android", "desktop", "tv", "other"}


def _broadcast(userid: int):
    """Push the latest state to all of the user's SSE listeners."""
    events.dispatch(
        "connect_state",
        {"userid": userid, "state": registry.get_state(userid).todict()},
    )


class RegisterBody(BaseModel):
    device_name: str = Field("Unknown device", description="Human-readable device name")
    app_type: str = Field("web", description="web | ios | android | desktop | tv | other")
    can_play_audio: bool = Field(True, description="Whether this device can output audio")


class HeartbeatBody(BaseModel):
    session_id: str = Field(description="Server-generated session id from /register")
    trackhash: str | None = Field(None, description="Currently playing track")
    position_ms: int | None = Field(None, description="Playback position in ms")
    is_playing: bool | None = Field(None, description="Playing or paused")
    volume: int | None = Field(None, description="Device volume 0-100")
    queue: list[str] | None = Field(None, description="Up to 500 trackhashes")
    queue_index: int | None = Field(None, description="Index into queue")


class SeekBody(BaseModel):
    position_ms: int = Field(description="Seek target in milliseconds")


class VolumeBody(BaseModel):
    volume_percent: int = Field(description="Volume 0-100")


class TransferBody(BaseModel):
    session_id: str = Field(description="Target device session id")


@api.post("/register")
def register_device(body: RegisterBody):
    """Register a device. Returns a server-generated session_id."""
    userid = get_current_userid()
    app_type = body.app_type if body.app_type in VALID_APP_TYPES else "other"
    session = registry.register(
        userid,
        device_name=body.device_name[:120],
        app_type=app_type,
        can_play_audio=body.can_play_audio,
    )
    return {"session_id": session.session_id, "device": session.todict()}, 201


@api.post("/heartbeat")
def heartbeat(body: HeartbeatBody):
    """
    Device heartbeat. Updates last-seen and (optionally) playback state.
    Call every ~5s while playing, ~30s otherwise.
    """
    userid = get_current_userid()
    session = registry.heartbeat(userid, body.session_id)
    if session is None:
        return {"msg": "Unknown session. Re-register."}, 404

    fields: dict = {}
    if body.trackhash is not None:
        fields["trackhash"] = body.trackhash
    if body.position_ms is not None:
        fields["position_ms"] = body.position_ms
    if body.is_playing is not None:
        fields["is_playing"] = body.is_playing
    if body.volume is not None:
        fields["volume"] = body.volume
    if body.queue is not None:
        fields["queue"] = body.queue
    if body.queue_index is not None:
        fields["queue_index"] = body.queue_index

    if fields:
        registry.update_state(userid, body.session_id, **fields)
        _broadcast(userid)

    return {"msg": "ok"}, 200


@api.get("/state")
def get_state():
    """Current per-user playback state, with extrapolated position."""
    userid = get_current_userid()
    return registry.get_state(userid).todict(), 200


@api.get("/devices")
def list_devices():
    """All devices registered for this user (stale-flagged, newest first)."""
    userid = get_current_userid()
    return {"devices": [s.todict() for s in registry.list_sessions(userid)]}, 200


class SessionIdPath(BaseModel):
    session_id: str = Field(description="Device session id")


@api.delete("/devices/<session_id>")
def disconnect_device(path: SessionIdPath):
    """Remotely disconnect a device. If it was the player, state freezes."""
    userid = get_current_userid()
    if registry.disconnect(userid, path.session_id):
        _broadcast(userid)
        return {"msg": "disconnected"}, 200
    return {"msg": "Device not found."}, 404


def _command(userid: int, session_id: str | None, **fields):
    """Apply a remote-control command to the user's playback state."""
    state = registry.get_state(userid)
    # Commands target the active player by default.
    target = session_id or state.active_session_id
    if target is None:
        # No active player: still record the intent so a device picking up
        # control starts from a sane state.
        target = "controller"
    registry.update_state(userid, target, **fields)
    _broadcast(userid)
    return registry.get_state(userid).todict()


@api.put("/play")
def remote_play():
    """Remote play."""
    userid = get_current_userid()
    return _command(userid, None, is_playing=True), 200


@api.put("/pause")
def remote_pause():
    """Remote pause."""
    userid = get_current_userid()
    return _command(userid, None, is_playing=False), 200


@api.post("/next")
def remote_next():
    """Remote next track (advances queue_index if a queue is present)."""
    userid = get_current_userid()
    state = registry.get_state(userid)
    fields: dict = {"is_playing": True, "position_ms": 0}
    if state.queue and state.queue_index + 1 < len(state.queue):
        fields["queue_index"] = state.queue_index + 1
        fields["trackhash"] = state.queue[state.queue_index + 1]
    return _command(userid, None, **fields), 200


@api.post("/previous")
def remote_previous():
    """Remote previous track (goes back in queue if possible)."""
    userid = get_current_userid()
    state = registry.get_state(userid)
    fields: dict = {"is_playing": True, "position_ms": 0}
    # If we're more than 3s in, restart the track (Spotify behavior).
    if state.extrapolated_position_ms() > 3000:
        fields["position_ms"] = 0
    elif state.queue and state.queue_index > 0:
        fields["queue_index"] = state.queue_index - 1
        fields["trackhash"] = state.queue[state.queue_index - 1]
    return _command(userid, None, **fields), 200


@api.put("/seek")
def remote_seek(body: SeekBody):
    """Remote seek."""
    userid = get_current_userid()
    return _command(userid, None, position_ms=max(0, body.position_ms)), 200


@api.put("/volume")
def remote_volume(body: VolumeBody):
    """Remote volume (0-100). No mute op — mute is volume 0 (Spotify parity)."""
    userid = get_current_userid()
    vol = max(0, min(100, body.volume_percent))
    return _command(userid, None, volume=vol), 200


@api.put("/transfer")
def transfer_playback(body: TransferBody):
    """
    Hand playback to another device. The target reads the handoff payload
    and starts streaming from this server independently, seeking to
    position_ms. The queue is copied so playback survives the controller
    disconnecting (Aurora lesson).
    """
    userid = get_current_userid()
    handoff = registry.transfer(userid, body.session_id)
    if handoff is None:
        return {"msg": "Nothing to transfer (no active playback or bad target)."}, 404
    _broadcast(userid)
    return handoff, 200
