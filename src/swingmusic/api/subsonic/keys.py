"""
Subsonic API key management (uses Swing's JWT auth, not Subsonic auth).

These endpoints let users generate/revoke per-user Subsonic API keys.
Keys are stored in user.extra["subsonic_api_key"].
"""

import secrets

from flask import jsonify
from flask_jwt_extended import get_jwt_identity, jwt_required

from swingmusic.db.userdata import UserTable

from . import bp


def _generate_key() -> str:
    """Generate a secure random API key (OpenSubsonic compatible)."""
    return "ss_" + secrets.token_urlsafe(32)


@bp.route("/keys/generate", methods=["POST"])
@jwt_required()
def generate_key():
    """Generate (or regenerate) the current user's Subsonic API key.
    Returns the key ONCE — it cannot be retrieved again."""
    identity = get_jwt_identity()
    userid = identity.get("id") if isinstance(identity, dict) else identity
    user = UserTable.get_by_id(userid)
    if not user:
        return jsonify({"error": "User not found."}), 404

    new_key = _generate_key()
    extra = dict(user.extra or {})
    extra["subsonic_api_key"] = new_key
    UserTable.update_one({"id": user.id, "extra": extra})

    return jsonify({"apiKey": new_key, "message": "Save this key now — it won't be shown again."}), 200


@bp.route("/keys/revoke", methods=["POST"])
@jwt_required()
def revoke_key():
    """Revoke the current user's Subsonic API key."""
    identity = get_jwt_identity()
    userid = identity.get("id") if isinstance(identity, dict) else identity
    user = UserTable.get_by_id(userid)
    if not user:
        return jsonify({"error": "User not found."}), 404

    extra = dict(user.extra or {})
    extra.pop("subsonic_api_key", None)
    UserTable.update_one({"id": user.id, "extra": extra})

    return jsonify({"message": "API key revoked."}), 200


@bp.route("/keys/status", methods=["GET"])
@jwt_required()
def key_status():
    """Check whether the current user has an API key (does not reveal it)."""
    identity = get_jwt_identity()
    userid = identity.get("id") if isinstance(identity, dict) else identity
    user = UserTable.get_by_id(userid)
    if not user:
        return jsonify({"error": "User not found."}), 404

    has_key = bool((user.extra or {}).get("subsonic_api_key"))
    return jsonify({"hasKey": has_key}), 200
