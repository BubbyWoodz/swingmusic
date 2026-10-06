"""Tests for on-the-fly transcoding (reverb fork)."""

import os
import shutil
import subprocess
import tempfile

import pytest

from swingmusic.lib.transcode import (
    TranscodeCache,
    cache_key,
    effective_bitrate,
    normalize_format,
    parse_bitrate,
    should_transcode,
    source_format,
    transcode_file,
)


def _make_wav(path: str, seconds: int = 2) -> None:
    """Generate a small test WAV with ffmpeg."""
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-c:a",
            "pcm_s16le",
            path,
        ],
        check=True,
        timeout=60,
    )


class TestParseBitrate:
    def test_none_and_original(self):
        assert parse_bitrate(None) is None
        assert parse_bitrate("original") is None
        assert parse_bitrate("") is None
        assert parse_bitrate("0") is None

    def test_numeric_strings(self):
        assert parse_bitrate("128") == 128
        assert parse_bitrate("320") == 320
        assert parse_bitrate("192") == 192

    def test_k_suffix(self):
        assert parse_bitrate("128k") == 128
        assert parse_bitrate("320K") == 320

    def test_int_input(self):
        assert parse_bitrate(128) == 128
        assert parse_bitrate(0) is None

    def test_garbage(self):
        assert parse_bitrate("banana") is None
        assert parse_bitrate("12.5") is None


class TestNormalizeFormat:
    def test_known(self):
        assert normalize_format("mp3") == "mp3"
        assert normalize_format("opus") == "opus"
        assert normalize_format("aac") == "aac"
        assert normalize_format("ogg") == "ogg"

    def test_case_and_dots(self):
        assert normalize_format("MP3") == "mp3"
        assert normalize_format(".mp3") == "mp3"

    def test_unknown(self):
        assert normalize_format("flac") is None  # not a transcode target
        assert normalize_format("wma") is None
        assert normalize_format(None) is None
        assert normalize_format("") is None


class TestSourceFormat:
    def test_extensions(self):
        assert source_format("/music/song.mp3") == "mp3"
        assert source_format("/music/song.opus") == "opus"
        assert source_format("/music/song.m4a") == "aac"
        assert source_format("/music/song.flac") == "flac"

    def test_case_insensitive(self):
        assert source_format("/music/SONG.MP3") == "mp3"

    def test_unknown(self):
        assert source_format("/music/song.xyz") is None


class TestShouldTranscode:
    def test_original_no_format(self):
        # No bitrate, no format -> serve original.
        assert should_transcode(320, "/m/s.mp3", None, None) is False

    def test_format_match_no_bitrate(self):
        # Format matches source -> no transcode even with format requested.
        assert should_transcode(320, "/m/s.mp3", None, "mp3") is False

    def test_format_mismatch(self):
        assert should_transcode(320, "/m/s.flac", None, "mp3") is True
        assert should_transcode(320, "/m/s.mp3", None, "opus") is True

    def test_bitrate_at_or_above_source(self):
        # Requesting >= source bitrate is pointless.
        assert should_transcode(128, "/m/s.mp3", 128, "mp3") is False
        assert should_transcode(128, "/m/s.mp3", 320, "mp3") is False

    def test_bitrate_below_source(self):
        assert should_transcode(320, "/m/s.mp3", 128, "mp3") is True

    def test_unknown_source_bitrate(self):
        # Unknown source bitrate + requested bitrate -> transcode to be safe.
        assert should_transcode(None, "/m/s.mp3", 128, "mp3") is True
        assert should_transcode(0, "/m/s.mp3", 128, "mp3") is True


class TestEffectiveBitrate:
    def test_none_passthrough(self):
        assert effective_bitrate(320, None) is None

    def test_caps_at_source(self):
        assert effective_bitrate(192, 320) == 192
        assert effective_bitrate(320, 128) == 128

    def test_unknown_source(self):
        assert effective_bitrate(None, 128) == 128


class TestCacheKey:
    def test_stable(self):
        assert cache_key("abc", 128, "mp3") == cache_key("abc", 128, "mp3")

    def test_differs_by_param(self):
        assert cache_key("abc", 128, "mp3") != cache_key("abc", 192, "mp3")
        assert cache_key("abc", 128, "mp3") != cache_key("abc", 128, "opus")
        assert cache_key("abc", 128, "mp3") != cache_key("def", 128, "mp3")


class TestTranscodeCache:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_cache(self, max_mb=1024):
        return TranscodeCache(os.path.join(self.tmpdir, "cache"), max_mb=max_mb)

    def _write_src(self, name="src.mp3", size=1024):
        p = os.path.join(self.tmpdir, name)
        with open(p, "wb") as f:
            f.write(b"x" * size)
        return p

    def test_miss(self):
        cache = self._make_cache()
        assert cache.get("nope") is None

    def test_put_get_roundtrip(self):
        cache = self._make_cache()
        src = self._write_src()
        out = cache.put("k1", src, "mp3")
        assert out is not None
        assert os.path.exists(out)
        assert not os.path.exists(src)  # moved, not copied
        assert cache.get("k1") == out

    def test_lru_eviction(self):
        # Tiny cap: only ~1.5KB fits.
        cache = self._make_cache(max_mb=0)  # 0 MB -> evict aggressively
        cache.max_bytes = 1500
        src1 = self._write_src("a.mp3", 1000)
        src2 = self._write_src("b.mp3", 1000)
        cache.put("k1", src1, "mp3")
        cache.put("k2", src2, "mp3")
        # k1 should have been evicted as least-recently-used.
        assert cache.get("k1") is None
        assert cache.get("k2") is not None

    def test_overwrite_key(self):
        cache = self._make_cache()
        src1 = self._write_src("a.mp3", 100)
        src2 = self._write_src("b.mp3", 200)
        p1 = cache.put("k", src1, "mp3")
        p2 = cache.put("k", src2, "mp3")
        assert p1 == p2  # same dest path
        assert os.path.getsize(p2) == 200
        assert len(cache) == 1

    def test_missing_file_on_get(self):
        cache = self._make_cache()
        src = self._write_src()
        out = cache.put("k", src, "mp3")
        os.unlink(out)
        assert cache.get("k") is None  # entry dropped
        assert len(cache) == 0

    def test_clear(self):
        cache = self._make_cache()
        cache.put("k", self._write_src(), "mp3")
        cache.clear()
        assert len(cache) == 0
        assert cache.total_bytes == 0


class TestTranscodeFile:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_mp3_transcode(self):
        src = os.path.join(self.tmpdir, "src.wav")
        out = os.path.join(self.tmpdir, "out.mp3")
        _make_wav(src)
        assert transcode_file(src, out, 128, "mp3") is True
        assert os.path.getsize(out) > 0

    def test_opus_transcode(self):
        src = os.path.join(self.tmpdir, "src.wav")
        out = os.path.join(self.tmpdir, "out.opus")
        _make_wav(src)
        assert transcode_file(src, out, 96, "opus") is True
        assert os.path.getsize(out) > 0

    def test_bad_format(self):
        src = os.path.join(self.tmpdir, "src.wav")
        out = os.path.join(self.tmpdir, "out.xyz")
        _make_wav(src)
        assert transcode_file(src, out, 128, "xyz") is False

    def test_missing_input(self):
        out = os.path.join(self.tmpdir, "out.mp3")
        assert transcode_file("/nonexistent/file.wav", out, 128, "mp3") is False
        assert not os.path.exists(out)
