"""
On-the-fly audio transcoding for Reverb.

Clean-room implementation: converts audio streams to lower bitrates or
different formats for bandwidth-constrained playback and client
compatibility. Uses ffmpeg under the hood.

Key pieces:
- Quality presets and format definitions
- Smart bypass: serve the original file when no conversion is needed
- Disk-backed LRU cache keyed by (trackhash, bitrate, format)
"""

import hashlib
import os
import subprocess
import threading
from collections import OrderedDict
from pathlib import Path

# Bitrate presets in kbps. "original" means no transcoding.
TRANSCODE_PRESETS = ("original", "320", "192", "128")

# Container -> ffmpeg audio codec args. Keys are the file extensions we emit.
TRANSCODE_FORMATS: dict[str, list[str]] = {
    "mp3": ["-c:a", "libmp3lame"],
    "opus": ["-c:a", "libopus"],
    "aac": ["-c:a", "aac"],
    "ogg": ["-c:a", "libvorbis"],
}

# Extension -> canonical format name for bypass comparison.
EXT_TO_FORMAT = {
    ".mp3": "mp3",
    ".opus": "opus",
    ".ogg": "ogg",
    ".oga": "ogg",
    ".m4a": "aac",
    ".aac": "aac",
    ".flac": "flac",
    ".wav": "wav",
    ".wma": "wma",
}

DEFAULT_CACHE_MAX_MB = 1024


def parse_bitrate(value: str | int | None) -> int | None:
    """
    Parse a bitrate preset. Returns kbps as int, or None for "original"/unset.
    Accepts "128", "128k", 128, "original", None.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    v = str(value).strip().lower().rstrip("k")
    if v in ("original", "", "0"):
        return None
    try:
        kbps = int(v)
        return kbps if kbps > 0 else None
    except ValueError:
        return None


def normalize_format(fmt: str | None) -> str | None:
    """Normalize a format name to our canonical set. Returns None if unknown."""
    if not fmt:
        return None
    f = str(fmt).strip().lower().lstrip(".")
    return f if f in TRANSCODE_FORMATS else None


def source_format(filepath: str) -> str | None:
    """Best-effort canonical format of a source file from its extension."""
    return EXT_TO_FORMAT.get(Path(filepath).suffix.lower())


def should_transcode(
    track_bitrate: int | None,
    filepath: str,
    req_bitrate: int | None,
    req_format: str | None,
) -> bool:
    """
    Decide whether a transcode is actually needed (smart bypass).

    No transcode when:
    - No bitrate requested (original quality), AND no format requested
      (or the requested format matches the source container)
    - Requested bitrate >= source bitrate AND format matches/unspecified
    """
    src_fmt = source_format(filepath)

    # Format mismatch always needs a transcode (if a format was requested).
    if req_format is not None and req_format != src_fmt:
        return True

    # No bitrate requested -> original quality; only format mattered (handled above).
    if req_bitrate is None:
        return False

    # Requested bitrate at/above source -> pointless to transcode.
    if track_bitrate and req_bitrate >= track_bitrate:
        return False

    return True


def effective_bitrate(
    track_bitrate: int | None, req_bitrate: int | None
) -> int | None:
    """The bitrate to actually encode at: never exceed the source bitrate."""
    if req_bitrate is None:
        return None
    if track_bitrate:
        return min(track_bitrate, req_bitrate)
    return req_bitrate


def transcode_file(
    input_path: str,
    output_path: str,
    bitrate_kbps: int,
    fmt: str,
    timeout: int = 300,
) -> bool:
    """
    Transcode input_path -> output_path with ffmpeg, blocking.

    Returns True on success, False on failure (partial output is removed).
    """
    codec_args = TRANSCODE_FORMATS.get(fmt)
    if codec_args is None:
        return False

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        input_path,
        "-map_metadata",
        "0",
        "-vn",
        "-b:a",
        f"{bitrate_kbps}k",
        *codec_args,
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if result.returncode != 0 or not os.path.exists(output_path):
            _safe_unlink(output_path)
            return False
        return os.path.getsize(output_path) > 0
    except (subprocess.SubprocessError, OSError):
        _safe_unlink(output_path)
        return False


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def cache_key(trackhash: str, bitrate_kbps: int, fmt: str) -> str:
    """Stable cache key for a (track, bitrate, format) triple."""
    raw = f"{trackhash}:{bitrate_kbps}:{fmt}".encode()
    return hashlib.sha256(raw).hexdigest()[:32]


class TranscodeCache:
    """
    Disk-backed LRU cache for transcoded audio.

    Files live under `cache_dir`; bookkeeping (key -> path, size, recency)
    is in memory and rebuilt lazily. Thread-safe.
    """

    def __init__(self, cache_dir: str | Path, max_mb: int = DEFAULT_CACHE_MAX_MB):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_mb * 1024 * 1024
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, dict] = OrderedDict()
        self._total_bytes = 0
        self._scan_existing()

    def _scan_existing(self) -> None:
        """Pick up files left from a previous run (oldest first)."""
        try:
            files = sorted(
                self.cache_dir.iterdir(),
                key=lambda p: p.stat().st_mtime,
            )
        except OSError:
            return
        for p in files:
            if not p.is_file():
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            self._entries[p.stem] = {"path": str(p), "size": size}
            self._total_bytes += size
        self._evict_locked()

    def get(self, key: str) -> str | None:
        """Return the cached file path for key, or None on miss."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            path = entry["path"]
            if not os.path.exists(path):
                # File vanished under us; drop the entry.
                self._total_bytes -= entry["size"]
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return path

    def put(self, key: str, src_path: str, fmt: str) -> str | None:
        """
        Move src_path into the cache under key. Returns the cached path,
        or None if src_path is unusable.
        """
        if not os.path.exists(src_path):
            return None
        dest = self.cache_dir / f"{key}.{fmt}"
        try:
            os.replace(src_path, dest)
            size = dest.stat().st_size
        except OSError:
            return None
        with self._lock:
            old = self._entries.pop(key, None)
            if old and old["path"] != str(dest):
                self._total_bytes -= old["size"]
                _safe_unlink(old["path"])
            elif old:
                self._total_bytes -= old["size"]
            self._entries[key] = {"path": str(dest), "size": size}
            self._total_bytes += size
            self._evict_locked()
            return str(dest)

    def _evict_locked(self) -> None:
        """Drop least-recently-used entries until under the size cap."""
        while self._total_bytes > self.max_bytes and self._entries:
            _, entry = self._entries.popitem(last=False)
            self._total_bytes -= entry["size"]
            _safe_unlink(entry["path"])

    def clear(self) -> None:
        with self._lock:
            for entry in self._entries.values():
                _safe_unlink(entry["path"])
            self._entries.clear()
            self._total_bytes = 0

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._total_bytes

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
