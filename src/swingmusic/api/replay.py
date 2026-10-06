"""
Replay API: Apple Music Replay-style listening stats.

All data is per-user (from JWT) and computed from the scrobble table.
No new data collection — everything derives from existing scrobbles.
"""

import calendar
from datetime import datetime

from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

from swingmusic.config import UserConfig
from swingmusic.db.libdata import PlaylistTable
from swingmusic.serializers.album import serialize_for_card as serialize_album_card
from swingmusic.serializers.artist import serialize_for_card as serialize_artist_card
from swingmusic.serializers.track import serialize_track
from swingmusic.store.tracks import TrackStore
from swingmusic.utils.auth import get_current_userid
from swingmusic.utils.dates import get_date_range
from swingmusic.utils.replay import (
    compare_years,
    find_milestones,
    get_artist_streak,
    get_first_plays,
    get_genres_in_period,
    get_monthly_number_ones,
)
from swingmusic.utils.stats import (
    get_albums_in_period,
    get_artists_in_period,
    get_tracks_in_period,
)

bp_tag = Tag(name="Replay", description="Apple Music Replay-style stats")
api = APIBlueprint("replay", __name__, url_prefix="/replay", abp_tags=[bp_tag])


def _month_bounds(year: int, month: int) -> tuple[int, int]:
    start = int(datetime(year, month, 1).timestamp())
    last_day = calendar.monthrange(year, month)[1]
    end = int(datetime(year, month, last_day, 23, 59, 59).timestamp())
    return start, end


def _year_bounds(year: int) -> tuple[int, int]:
    start = int(datetime(year, 1, 1).timestamp())
    end = int(datetime(year, 12, 31, 23, 59, 59).timestamp())
    return start, end


def _period_payload(start: int, end: int, userid: int, limit: int = 15) -> dict:
    """Shared aggregation for a time period."""
    tracks, total_plays, total_seconds = get_tracks_in_period(start, end, userid)
    artists = get_artists_in_period(start, end, userid)
    albums = get_albums_in_period(start, end, userid)
    genres = get_genres_in_period(start, end, userid)

    top_tracks = sorted(tracks, key=lambda t: t.playcount, reverse=True)[:limit]
    top_artists = sorted(artists, key=lambda a: a.playduration, reverse=True)[:limit]
    top_albums = sorted(albums, key=lambda a: a.playcount, reverse=True)[:limit]

    return {
        "total_minutes": round(total_seconds / 60),
        "total_seconds": total_seconds,
        "total_plays": total_plays,
        "artist_count": len(artists),
        "album_count": len(albums),
        "song_count": len(tracks),
        "top_artists": [
            {
                **serialize_artist_card(a),
                "playcount": a.playcount,
                "playduration": a.playduration,
                "minutes": round(a.playduration / 60),
            }
            for a in top_artists
        ],
        "top_songs": [
            {
                **serialize_track(t),
                "playcount": t.playcount,
                "playduration": t.playduration,
            }
            for t in top_tracks
        ],
        "top_albums": [
            {
                **serialize_album_card(a),
                "playcount": a.playcount,
                "playduration": a.playduration,
            }
            for a in top_albums
        ],
        "top_genres": genres[:5],
    }


class MonthlyQuery(BaseModel):
    year: int = Field(description="Year (e.g. 2026)")
    month: int = Field(description="Month 1-12", ge=1, le=12)


@api.get("/monthly")
def get_monthly_replay(query: MonthlyQuery):
    """Monthly Replay: totals, tops, genres, and milestones for one month."""
    userid = get_current_userid()
    start, end = _month_bounds(query.year, query.month)
    payload = _period_payload(start, end, userid)
    payload["year"] = query.year
    payload["month"] = query.month
    payload["milestones"] = find_milestones(start, end, userid)
    return payload, 200


class YearlyQuery(BaseModel):
    year: int = Field(description="Year (e.g. 2026)")


@api.get("/yearly")
def get_yearly_replay(query: YearlyQuery):
    """Yearly Replay: everything in monthly + streaks, first plays, YoY, monthly strip."""
    userid = get_current_userid()
    start, end = _year_bounds(query.year)
    payload = _period_payload(start, end, userid, limit=15)
    payload["year"] = query.year
    payload["milestones"] = find_milestones(start, end, userid)
    payload["artist_streaks"] = get_artist_streak(query.year, userid)
    payload["monthly_number_ones"] = get_monthly_number_ones(query.year, userid)
    payload["year_over_year"] = compare_years(query.year, userid)

    # First-play dates for the year's #1s
    first_plays = get_first_plays(userid)
    tops = {
        "song": payload["top_songs"][0] if payload["top_songs"] else None,
        "artist": payload["top_artists"][0] if payload["top_artists"] else None,
        "album": payload["top_albums"][0] if payload["top_albums"] else None,
    }
    payload["first_plays"] = {
        "top_song": first_plays["tracks"].get(tops["song"]["trackhash"])
        if tops["song"]
        else None,
        "top_artist": first_plays["artists"].get(tops["artist"]["artisthash"])
        if tops["artist"]
        else None,
        "top_album": first_plays["albums"].get(tops["album"]["albumhash"])
        if tops["album"]
        else None,
    }
    return payload, 200


@api.get("/months")
def get_months_with_data(query: YearlyQuery):
    """Lightweight list of months with listening data (for archive picker)."""
    userid = get_current_userid()
    monthly = get_monthly_number_ones(query.year, userid)
    return {
        "year": query.year,
        "months": [m["month"] for m in monthly],
    }, 200


class PlaylistQuery(BaseModel):
    year: int = Field(description="Year for the Replay playlist")
    limit: int = Field(100, description="Max tracks in the playlist", ge=1, le=500)


@api.post("/playlist")
def generate_replay_playlist(query: PlaylistQuery):
    """Generate/refresh the Top-N Replay playlist for a year."""
    userid = get_current_userid()
    start, end = _year_bounds(query.year)
    tracks, _, _ = get_tracks_in_period(start, end, userid)
    top_tracks = sorted(tracks, key=lambda t: t.playcount, reverse=True)[: query.limit]

    if not top_tracks:
        return {"error": "No listening data for this year."}, 404

    name = f"Replay {query.year} — Top {len(top_tracks)}"
    trackhashes = [{"trackhash": t.trackhash} for t in top_tracks]

    # Check for existing Replay playlist for this year and update it
    existing = None
    for pl in PlaylistTable.get_all(userid):
        if pl.name == name:
            existing = pl
            break

    if existing:
        PlaylistTable.update_one(existing.id, {"trackhashes": trackhashes})
        playlist_id = existing.id
    else:
        playlist_id = PlaylistTable.add_one(
            {
                "name": name,
                "trackhashes": trackhashes,
            }
        )

    return {
        "message": f"Replay playlist generated with {len(top_tracks)} tracks.",
        "playlist_id": playlist_id,
        "track_count": len(top_tracks),
    }, 200
