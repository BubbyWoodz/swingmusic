"""Tests for progressive (piped) transcoding."""

import os
import shutil
import subprocess
import tempfile
import time

import pytest

from swingmusic.lib.transcode import (
    ProgressiveTranscoder,
    TranscodeCache,
    TranscodeStreamError,
    build_ffmpeg_cmd,
    cache_key,
    estimate_seek_seconds,
)


def _make_wav(path: str, seconds: int = 4) -> None:
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


class TestBuildFfmpegCmd:
    def test_basic(self):
        cmd = build_ffmpeg_cmd("/m/s.flac", 128, "mp3")
        assert cmd[0] == "ffmpeg"
        assert "-nostdin" in cmd
        assert "pipe:1" in cmd
        assert cmd[-1] == "pipe:1"
        bi = cmd.index("-b:a")
        assert cmd[bi + 1] == "128k"
        fi = cmd.index("-f")
        assert cmd[fi + 1] == "mp3"
        assert "-ss" not in cmd

    def test_seek_before_input(self):
        cmd = build_ffmpeg_cmd("/m/s.flac", 128, "mp3", seek_seconds=30.5)
        ss = cmd.index("-ss")
        assert cmd[ss + 1] == "30.500"
        assert ss < cmd.index("-i")  # input seeking = fast

    def test_pipe_formats(self):
        assert build_ffmpeg_cmd("/m/s.flac", 96, "opus")[
            build_ffmpeg_cmd("/m/s.flac", 96, "opus").index("-f") + 1
        ] == "opus"
        aac = build_ffmpeg_cmd("/m/s.flac", 128, "aac")
        assert aac[aac.index("-f") + 1] == "adts"
        ogg = build_ffmpeg_cmd("/m/s.flac", 128, "ogg")
        assert ogg[ogg.index("-f") + 1] == "ogg"

    def test_bad_format(self):
        with pytest.raises(TranscodeStreamError):
            build_ffmpeg_cmd("/m/s.flac", 128, "xyz")


class TestEstimateSeekSeconds:
    def test_basic_math(self):
        # 128k -> 16000 bytes/sec, so 16000 bytes -> ~1s.
        assert estimate_seek_seconds(16000, 128) == pytest.approx(1.0)

    def test_zero(self):
        assert estimate_seek_seconds(0, 128) == 0.0
        assert estimate_seek_seconds(-5, 128) == 0.0

    def test_clamped_to_duration(self):
        assert estimate_seek_seconds(10**9, 128, duration=240) == pytest.approx(239.0)

    def test_no_duration(self):
        assert estimate_seek_seconds(32000, 128) == pytest.approx(2.0)


class TestProgressiveTranscoder:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.src = os.path.join(self.tmpdir, "src.wav")
        _make_wav(self.src, seconds=4)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_first_chunk_fast(self):
        t = ProgressiveTranscoder(self.src, 128, "mp3")
        start = time.monotonic()
        stream = t.response_stream()
        first = next(stream)
        elapsed = time.monotonic() - start
        try:
            assert len(first) > 0
            # First audio must arrive quickly (well under the old 2-5s blocking wait).
            assert elapsed < 5.0
        finally:
            stream.close()
        print(f"\n[perf] time-to-first-audio: {elapsed:.2f}s")

    def test_full_stream_caches(self):
        cache = TranscodeCache(os.path.join(self.tmpdir, "cache"))
        key = cache_key("track1", 128, "mp3")
        t = ProgressiveTranscoder(self.src, 128, "mp3", cache=cache, cache_key=key)
        data = b"".join(t.response_stream())
        assert len(data) > 0
        cached = cache.get(key)
        assert cached is not None
        assert os.path.getsize(cached) > 0

    def test_disconnect_kills_process(self):
        long_src = os.path.join(self.tmpdir, "long.wav")
        _make_wav(long_src, seconds=90)
        t = ProgressiveTranscoder(long_src, 128, "mp3")
        stream = t.response_stream()
        next(stream)  # prime: ffmpeg is definitely still running on a 90s file
        proc = t.proc
        assert proc is not None and proc.poll() is None
        stream.close()
        assert proc.poll() is not None  # reaped, no orphan
        time.sleep(0.2)
        assert proc.poll() is not None

    def test_close_without_streaming_kills_process(self):
        long_src = os.path.join(self.tmpdir, "long.wav")
        _make_wav(long_src, seconds=90)
        t = ProgressiveTranscoder(long_src, 128, "mp3")
        t.start()
        proc = t.proc
        assert proc is not None and proc.poll() is None
        t.close()
        assert proc.poll() is not None

    def test_disconnect_discards_partial_cache(self):
        cache = TranscodeCache(os.path.join(self.tmpdir, "cache"))
        key = cache_key("track2", 128, "mp3")
        long_src = os.path.join(self.tmpdir, "long.wav")
        _make_wav(long_src, seconds=90)
        t = ProgressiveTranscoder(long_src, 128, "mp3", cache=cache, cache_key=key)
        stream = t.response_stream()
        next(stream)
        stream.close()  # disconnect mid-stream: partial output must not be cached
        assert cache.get(key) is None

    def test_bad_input_raises(self):
        t = ProgressiveTranscoder("/nonexistent/file.wav", 128, "mp3")
        with pytest.raises(TranscodeStreamError):
            t.response_stream()
        # No orphan left behind.
        assert t.proc is None or t.proc.poll() is not None

    def test_seek_produces_shorter_output(self):
        long_src = os.path.join(self.tmpdir, "long.wav")
        _make_wav(long_src, seconds=30)
        t = ProgressiveTranscoder(long_src, 128, "mp3", seek_seconds=15.0)
        data = b"".join(t.response_stream())
        assert len(data) > 0
        # 15s at 128k ~= 240KB; must be clearly less than the ~30s full output.
        assert len(data) < 20 * 16000

    def test_opus_pipe(self):
        t = ProgressiveTranscoder(self.src, 96, "opus")
        data = b"".join(t.response_stream())
        assert len(data) > 0
