"""
Debug endpoints for the bubbywoodz RAM-optimization fork.

These are additive diagnostics for measuring memory efficiency. They do not
modify any application state and are not part of the public API surface.
"""
import gc
import tracemalloc

from flask_openapi3 import APIBlueprint, Tag

from swingmusic.store.albums import AlbumStore
from swingmusic.store.artists import ArtistStore
from swingmusic.store.tracks import TrackStore

bp_tag = Tag(name="Debug", description="Memory diagnostics (fork only)")
api = APIBlueprint("debug", __name__, url_prefix="/debug", abp_tags=[bp_tag])

# Start tracemalloc on first import so snapshots are available.
tracemalloc.start(10)


@api.get("/memory")
def memory_stats():
    """
    Returns process memory stats and per-subsystem estimates.
    Used to validate the RAM optimizations in the bubbywoodz fork.
    """
    gc.collect()
    current, peak = tracemalloc.get_traced_memory()

    # Count objects in the in-memory stores
    track_groups = len(TrackStore.trackhashmap)
    track_count = sum(len(g.tracks) for g in TrackStore.trackhashmap.values())
    album_count = len(AlbumStore.albummap)
    artist_count = len(ArtistStore.artistmap)

    # Sample per-track extra-dict size
    extra_bytes = 0
    sampled = 0
    for group in list(TrackStore.trackhashmap.values())[:50]:
        for t in group.tracks:
            try:
                import sys as _sys
                extra_bytes += _sys.getsizeof(t.extra)
                for k, v in t.extra.items():
                    extra_bytes += _sys.getsizeof(k) + _sys.getsizeof(v)
                sampled += 1
            except Exception:
                pass
    avg_extra = extra_bytes / sampled if sampled else 0

    return {
        "tracemalloc_current_mb": round(current / 1024 / 1024, 1),
        "tracemalloc_peak_mb": round(peak / 1024 / 1024, 1),
        "track_groups": track_groups,
        "track_count": track_count,
        "album_count": album_count,
        "artist_count": artist_count,
        "avg_extra_bytes_per_track": round(avg_extra, 0),
        "extra_sampled": sampled,
    }
