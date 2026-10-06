"""
Subsonic / OpenSubsonic REST API for the bubbywoodz Swing Music fork.

Mounts at /rest/ alongside Swing's native API (untouched). Both APIs read
from the same in-memory stores — no data duplication.

Implements Subsonic API 1.16.1 with OpenSubsonic extensions.
Reference: https://opensubsonic.netlify.app/

Auth (in order of preference):
  1. apiKey=<key>      — per-user API key (OpenSubsonic extension, recommended)
  2. u=<user>&p=<pass> — plaintext password, verified via Swing's check_password
  3. u=<user>&p=enc:<hex> — hex-encoded password
  Token auth (t=/s=) is intentionally unsupported (error 41): Swing stores
  PBKDF2 hashes, not plaintext, so MD5 token verification is impossible.
  Clients should use API keys instead.

IDs: Swing's native hashes are used directly as Subsonic IDs —
  trackhash -> song id, albumhash -> album id, artisthash -> artist id.
  They are stable, unique, and already indexed.
"""

from functools import wraps

from flask import Blueprint, g, jsonify, request

from swingmusic.config import UserConfig
from swingmusic.db.userdata import UserTable
from swingmusic.utils.auth import check_password

bp = Blueprint("subsonic", __name__, url_prefix="/rest")

API_VERSION = "1.16.1"
SERVER_TYPE = "swingmusic-lean"


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def _envelope(payload: dict, status: str = "ok") -> dict:
    """Wrap payload in the standard subsonic-response envelope."""
    return {
        "subsonic-response": {
            "status": status,
            "version": API_VERSION,
            "type": SERVER_TYPE,
            "serverVersion": "1.0.0-lean",
            "openSubsonic": True,
            **payload,
        }
    }


def ok(payload: dict | None = None):
    return jsonify(_envelope(payload or {})), 200


def error(code: int, message: str):
    """Standard Subsonic error codes: 0 generic, 10 auth, 20 trial,
    30 version, 40 wrong param, 41 token auth, 50 not found, 60 exists, 70 share."""
    return jsonify(_envelope({"error": {"code": code, "message": message}}, status="failed")), 200


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _find_user_by_apikey(key: str):
    """Look up a user by their Subsonic API key (stored in user.extra)."""
    if not key:
        return None
    for user in UserTable.get_all():
        extra = user.extra or {}
        if extra.get("subsonic_api_key") == key:
            return user
    return None


def _find_user_by_username(username: str):
    if not username:
        return None
    for user in UserTable.get_all():
        if user.username == username:
            return user
    return None


def require_subsonic_auth(f):
    """Authenticate via apiKey, or u+p / u+p=enc:hex. Sets g.subsonic_user."""

    @wraps(f)
    def wrapper(*args, **kwargs):
        config = UserConfig()
        if not getattr(config, "subsonicEnabled", False):
            return error(10, "Subsonic API is disabled on this server.")

        # 1. API key (OpenSubsonic, preferred)
        api_key = request.args.get("apiKey") or request.form.get("apiKey")
        if api_key:
            user = _find_user_by_apikey(api_key)
            if user:
                g.subsonic_user = user
                return f(*args, **kwargs)
            return error(10, "Invalid API key.")

        # 2. Username + password
        username = request.args.get("u") or request.form.get("u")
        password = request.args.get("p") or request.form.get("p")
        if username and password:
            # Hex-encoded password support (p=enc:...)
            if password.startswith("enc:"):
                try:
                    password = bytes.fromhex(password[4:]).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    return error(10, "Invalid encoded password.")
            user = _find_user_by_username(username)
            if user:
                verified, _ = check_password(password, user.password)
                if verified:
                    g.subsonic_user = user
                    return f(*args, **kwargs)
            return error(10, "Wrong username or password.")

        # 3. Token auth explicitly unsupported
        if request.args.get("t") or request.form.get("t"):
            return error(
                41,
                "Token authentication is not supported. "
                "Use an API key (see server settings) or password auth instead.",
            )

        return error(10, "Authentication required.")

    return wrapper


from .models import (
    album_id,
    album_id_from_track,
    artist_id,
    serialize_album,
    serialize_artist,
    serialize_song,
    song_id,
)

# Import route modules to register endpoints on the blueprint.
# NOTE: imported at the end to avoid circular imports (modules import from this package).
from . import annotation, browse, keys, media, search, system  # noqa: E402,F401
