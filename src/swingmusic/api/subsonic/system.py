"""
System endpoints: ping, getLicense, getOpenSubsonicExtensions.
"""

from flask import request

from . import bp, error, ok, require_subsonic_auth


@bp.route("/ping.view", methods=["GET", "POST"])
@bp.route("/ping", methods=["GET", "POST"])
def ping():
    """No auth required — always returns ok (per Subsonic spec)."""
    return ok()


@bp.route("/getLicense.view", methods=["GET", "POST"])
@bp.route("/getLicense", methods=["GET", "POST"])
@require_subsonic_auth
def get_license():
    return ok({"license": {"valid": True, "email": "", "licenseExpires": "2099-12-31T00:00:00"}})


@bp.route("/getOpenSubsonicExtensions.view", methods=["GET", "POST"])
@bp.route("/getOpenSubsonicExtensions", methods=["GET", "POST"])
@require_subsonic_auth
def get_extensions():
    return ok(
        {
            "openSubsonicExtensions": [
                {"name": "formPost", "versions": [1]},
                {"name": "apiKeyAuth", "versions": [1]},
                {"name": "albumArtist", "versions": [1]},
            ]
        }
    )
