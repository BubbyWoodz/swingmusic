"""
Replay utilities for the bubbywoodz Swing Music fork.

Computes Apple Music Replay-style aggregates from the scrobble table.
All functions are pure computations over existing data — no schema changes.

Per Rhydian's scale goal, these run compute-on-demand. If they ever feel
slow with 100k+ scrobbles, add the replay_cache table (see spec).
"""

from collections import defaultdict

from swingmusic.db.libdata import ScrobbleTable
from swingmusic.store.tracks import TrackStore
from swingmusic.utils.stats import (
    get_albums_in_period,
    get_artists_in_period,
    get_tracks_in_period,
)


def get_genres_in_period(start_time: int, end_time: int, userid: int | None = None) -> list[dict]:
    """
    Aggregate playduration by genre for scrobbles in the period.
    Returns top genres sorted by minutes listened, each with name + playduration + playcount.
    """
    scrobbles = ScrobbleTable.get_all_in_period(start_time, end_time, userid)
    genre_stats: dict[str, dict] = defaultdict(lambda: {"playduration": 0, "playcount": 0})

    seen_tracks: dict[str, object] = {}
    for scrobble in scrobbles:
        track = seen_tracks.get(scrobble.trackhash)
        if track is None:
            try:
                track = TrackStore.get_tracks_by_trackhashes([scrobble.trackhash])[0]
            except IndexError:
                continue
            seen_tracks[scrobble.trackhash] = track

        genres = getattr(track, "genres", None) or []
        names = []
        for g in genres:
            if isinstance(g, dict):
                names.append(g.get("name", ""))
            elif isinstance(g, str):
                names.append(g)
        if not names:
            names = ["Unknown"]

        for name in names:
            name = (name or "Unknown").strip() or "Unknown"
            genre_stats[name]["playduration"] += scrobble.duration or 0
            genre_stats[name]["playcount"] += 1

    result = [
        {"name": name, "playduration": s["playduration"], "playcount": s["playcount"]}
        for name, s in genre_stats.items()
    ]
    result.sort(key=lambda x: x["playduration"], reverse=True)
    return result


# Milestone thresholds: (kind, threshold, label)
MINUTE_MILESTONES = [1000, 5000, 10000, 25000, 50000, 100000]
SONG_MILESTONES = [100, 1000, 5000, 10000]


def find_milestones(start_time: int, end_time: int, userid: int | None = None) -> list[dict]:
    """
    Find when listening milestones were crossed within the period.
    Scans scrobbles chronologically, tracking cumulative minutes and song count.
    Returns milestones with the exact timestamp each was crossed.
    """
    scrobbles = ScrobbleTable.get_all_in_period(start_time, end_time, userid)
    scrobbles = sorted(scrobbles, key=lambda s: s.timestamp or 0)

    milestones = []
    total_minutes = 0
    total_songs = 0
    minute_idx = 0
    song_idx = 0

    for scrobble in scrobbles:
        total_minutes += (scrobble.duration or 0) / 60
        total_songs += 1

        while minute_idx < len(MINUTE_MILESTONES) and total_minutes >= MINUTE_MILESTONES[minute_idx]:
            m = MINUTE_MILESTONES[minute_idx]
            milestones.append(
                {
                    "type": "minutes",
                    "threshold": m,
                    "label": f"{m:,} minutes",
                    "timestamp": scrobble.timestamp,
                }
            )
            minute_idx += 1

        while song_idx < len(SONG_MILESTONES) and total_songs >= SONG_MILESTONES[song_idx]:
            m = SONG_MILESTONES[song_idx]
            milestones.append(
                {
                    "type": "songs",
                    "threshold": m,
                    "label": f"{m:,} songs",
                    "timestamp": scrobble.timestamp,
                }
            )
            song_idx += 1

    milestones.sort(key=lambda m: m["timestamp"] or 0)
    return milestones


def get_monthly_number_ones(year: int, userid: int | None = None) -> list[dict]:
    """
    For each month of the year with data, return the #1 song, artist, and album.
    Used for the replay-by-month strip and artist streak detection.
    """
    import calendar

    result = []
    for month in range(1, 13):
        start = int(__import__("datetime").datetime(year, month, 1).timestamp())
        last_day = calendar.monthrange(year, month)[1]
        end = int(
            __import__("datetime").datetime(year, month, last_day, 23, 59, 59).timestamp()
        )

        tracks, total_plays, _ = get_tracks_in_period(start, end, userid)
        artists = get_artists_in_period(start, end, userid)
        albums = get_albums_in_period(start, end, userid)

        if total_plays == 0:
            continue

        top_track = max(tracks, key=lambda t: t.playcount, default=None)
        top_artist = max(artists, key=lambda a: a.playduration, default=None)
        top_album = max(albums, key=lambda a: a.playcount, default=None)

        result.append(
            {
                "month": month,
                "top_song": {
                    "trackhash": top_track.trackhash,
                    "title": top_track.title,
                    "playcount": top_track.playcount,
                }
                if top_track
                else None,
                "top_artist": {
                    "artisthash": top_artist.artisthash,
                    "name": top_artist.name,
                    "playduration": top_artist.playduration,
                }
                if top_artist
                else None,
                "top_album": {
                    "albumhash": top_album.albumhash,
                    "title": top_album.title,
                    "playcount": top_album.playcount,
                }
                if top_album
                else None,
            }
        )
    return result


def get_artist_streak(year: int, userid: int | None = None) -> list[dict]:
    """
    Detect artists who were #1 for multiple consecutive months ("loyal fan").
    Returns streaks with artist info, month range, and length.
    """
    monthly = get_monthly_number_ones(year, userid)
    streaks = []
    current_artist = None
    current_start = None
    current_months = []

    for entry in monthly:
        top = entry.get("top_artist")
        artist_hash = top["artisthash"] if top else None

        if artist_hash and artist_hash == current_artist:
            current_months.append(entry["month"])
        else:
            if current_artist and len(current_months) >= 2:
                streaks.append(
                    {
                        "artisthash": current_artist,
                        "name": top_name_for(current_artist, monthly),
                        "months": list(current_months),
                        "streak_length": len(current_months),
                    }
                )
            current_artist = artist_hash
            current_start = entry["month"]
            current_months = [entry["month"]] if artist_hash else []

    # Don't forget the last streak
    if current_artist and len(current_months) >= 2:
        streaks.append(
            {
                "artisthash": current_artist,
                "name": top_name_for(current_artist, monthly),
                "months": list(current_months),
                "streak_length": len(current_months),
            }
        )

    return streaks


def top_name_for(artisthash: str, monthly: list[dict]) -> str:
    for entry in monthly:
        top = entry.get("top_artist")
        if top and top["artisthash"] == artisthash:
            return top["name"]
    return ""


def get_first_plays(userid: int | None = None) -> dict:
    """
    Find the first-ever play timestamp for each track/artist/album.
    Returns the earliest scrobble per entity (for "date of first play").
    Used with yearly tops to show when you first played your #1s.
    """
    # Get all scrobbles sorted chronologically; first occurrence wins
    scrobbles = ScrobbleTable.get_all_in_period(0, 2**31 - 1, userid)
    scrobbles = sorted(scrobbles, key=lambda s: s.timestamp or 0)

    first_track: dict[str, int] = {}
    first_artist: dict[str, int] = {}
    first_album: dict[str, int] = {}

    seen_tracks: dict[str, object] = {}
    for scrobble in scrobbles:
        th = scrobble.trackhash
        if th not in first_track:
            first_track[th] = scrobble.timestamp

        track = seen_tracks.get(th)
        if track is None:
            try:
                track = TrackStore.get_tracks_by_trackhashes([th])[0]
            except IndexError:
                continue
            seen_tracks[th] = track

        for artist in getattr(track, "artists", None) or []:
            ah = artist.get("artisthash") if isinstance(artist, dict) else None
            if ah and ah not in first_artist:
                first_artist[ah] = scrobble.timestamp

        albumhash = getattr(track, "albumhash", None)
        if albumhash and albumhash not in first_album:
            first_album[albumhash] = scrobble.timestamp

    return {
        "tracks": first_track,
        "artists": first_artist,
        "albums": first_album,
    }


def compare_years(year: int, userid: int | None = None) -> dict:
    """
    Compare this year's tops vs last year's (year-over-year).
    Returns the #1 song/artist/album for both years and whether they changed.
    """
    import calendar

    def year_bounds(y: int):
        start = int(__import__("datetime").datetime(y, 1, 1).timestamp())
        end = int(__import__("datetime").datetime(y, 12, 31, 23, 59, 59).timestamp())
        return start, end

    def year_tops(y: int):
        start, end = year_bounds(y)
        tracks, _, _ = get_tracks_in_period(start, end, userid)
        artists = get_artists_in_period(start, end, userid)
        albums = get_albums_in_period(start, end, userid)
        return {
            "top_song": max(tracks, key=lambda t: t.playcount, default=None),
            "top_artist": max(artists, key=lambda a: a.playduration, default=None),
            "top_album": max(albums, key=lambda a: a.playcount, default=None),
        }

    current = year_tops(year)
    previous = year_tops(year - 1)

    def _summary(obj, kind: str):
        if obj is None:
            return None
        if kind == "song":
            return {"trackhash": obj.trackhash, "title": obj.title}
        if kind == "artist":
            return {"artisthash": obj.artisthash, "name": obj.name}
        return {"albumhash": obj.albumhash, "title": obj.title}

    return {
        "year": year,
        "current": {
            "top_song": _summary(current["top_song"], "song"),
            "top_artist": _summary(current["top_artist"], "artist"),
            "top_album": _summary(current["top_album"], "album"),
        },
        "previous": {
            "top_song": _summary(previous["top_song"], "song"),
            "top_artist": _summary(previous["top_artist"], "artist"),
            "top_album": _summary(previous["top_album"], "album"),
        },
    }
