"""End-to-end audio simulation across formats and edge cases.

Skipped when ffmpeg isn't available. Each test:
  1) synthesizes a source audio file at a non-target loudness
  2) runs the normalizer
  3) measures the output with ffmpeg's ebur128 filter
  4) asserts the integrated loudness is within ±0.7 LU of -16

The ±0.7 tolerance (slightly looser than the unit test's ±0.5) accounts for
codecs like AAC/MP3 where re-encoding adds tiny LUFS drift.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from src import normalizer
from src.watcher import FolderWatcher, WorkerCallbacks


def _ffmpeg() -> str:
    repo_bin = Path(__file__).resolve().parent.parent / "resources" / "ffmpeg"
    if repo_bin.exists():
        return str(repo_bin)
    found = shutil.which("ffmpeg")
    if not found:
        pytest.skip("ffmpeg not available")
    return found


_OUT_I_RE = re.compile(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS")
_TRUE_PEAK_RE = re.compile(r"True peak:\s*\n\s*Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS",
                           re.MULTILINE)


def _measure(ffmpeg: str, path: Path) -> float:
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(path),
         "-af", "ebur128=peak=true", "-f", "null", "-"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )
    matches = _OUT_I_RE.findall(proc.stderr)
    assert matches, f"no I: in ebur128 output:\n{proc.stderr[-500:]}"
    return float(matches[-1])


def _measure_true_peak_dbfs(ffmpeg: str, path: Path) -> float:
    """Return the highest true-peak value (dBFS) reported by ebur128."""
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(path),
         "-af", "ebur128=peak=true", "-f", "null", "-"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )
    m = _TRUE_PEAK_RE.search(proc.stderr)
    assert m, f"no True peak in ebur128 output:\n{proc.stderr[-800:]}"
    return float(m.group(1))


def _synthesize(ffmpeg: str, dst: Path, *,
                duration: float = 6.0,
                channels: int = 2,
                volume_db: float = -10.0,
                codec_args: list[str] | None = None,
                source: str = "sine") -> None:
    """Build a synthetic test asset on disk."""
    if source == "sine":
        if channels == 1:
            input_args = ["-f", "lavfi", "-i", f"sine=frequency=1000:duration={duration}"]
        else:
            # Two slightly-detuned sines panned L/R for a real stereo signal
            input_args = [
                "-f", "lavfi", "-i",
                f"sine=frequency=1000:duration={duration}",
                "-f", "lavfi", "-i",
                f"sine=frequency=1200:duration={duration}",
            ]
    elif source == "noise":
        input_args = ["-f", "lavfi", "-i",
                      f"anoisesrc=color=pink:duration={duration}:amplitude=0.5"]
    else:
        raise ValueError(source)

    af_filters = [f"volume={volume_db}dB"]
    if source == "sine" and channels == 2:
        cmd = [
            ffmpeg, "-hide_banner", "-y",
            *input_args,
            "-filter_complex", f"[0:a][1:a]amerge=inputs=2,volume={volume_db}dB[a]",
            "-map", "[a]",
            "-ac", "2",
            "-ar", "48000",
        ]
    else:
        cmd = [
            ffmpeg, "-hide_banner", "-y",
            *input_args,
            "-af", ",".join(af_filters),
            "-ac", str(channels),
            "-ar", "48000",
        ]
    cmd += codec_args or ["-c:a", "pcm_s16le"]
    cmd.append(str(dst))
    proc = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr[-800:]


def _normalize_and_check(ffmpeg: str, src: Path, expected_out_name: str) -> Path:
    result = normalizer.normalize(src, ffmpeg=ffmpeg)
    assert result.output_path.exists(), f"no output at {result.output_path}"
    assert result.output_path.name == expected_out_name
    measured = _measure(ffmpeg, result.output_path)
    assert abs(measured - normalizer.LUFS_TARGET) <= 0.7, (
        f"{src.name}: expected {normalizer.LUFS_TARGET} ±0.7, got {measured}"
    )
    return result.output_path


# --- Per-format normalization round-trips ---------------------------------


def test_wav_stereo_quiet_source(tmp_path: Path) -> None:
    ffmpeg = _ffmpeg()
    src = tmp_path / "ep.wav"
    _synthesize(ffmpeg, src, channels=2, volume_db=-25,
                codec_args=["-c:a", "pcm_s16le"])
    _normalize_and_check(ffmpeg, src, "ep_normalized.wav")


def test_wav_mono_loud_source(tmp_path: Path) -> None:
    """Mono should normalize and stay mono."""
    ffmpeg = _ffmpeg()
    src = tmp_path / "mono.wav"
    _synthesize(ffmpeg, src, channels=1, volume_db=0,
                codec_args=["-c:a", "pcm_s16le"], source="noise")
    out = _normalize_and_check(ffmpeg, src, "mono_normalized.wav")

    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(out), "-f", "null", "-"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )
    assert "mono" in proc.stderr.lower(), \
        f"expected mono output, got:\n{proc.stderr[-400:]}"


def test_mp3(tmp_path: Path) -> None:
    ffmpeg = _ffmpeg()
    src = tmp_path / "ep.mp3"
    _synthesize(ffmpeg, src, channels=2, volume_db=-5,
                codec_args=["-c:a", "libmp3lame", "-b:a", "192k"], source="noise")
    _normalize_and_check(ffmpeg, src, "ep_normalized.mp3")


def test_m4a(tmp_path: Path) -> None:
    ffmpeg = _ffmpeg()
    src = tmp_path / "zoom.m4a"
    _synthesize(ffmpeg, src, channels=2, volume_db=-20,
                codec_args=["-c:a", "aac", "-b:a", "128k"], source="noise")
    _normalize_and_check(ffmpeg, src, "zoom_normalized.m4a")


def test_flac(tmp_path: Path) -> None:
    ffmpeg = _ffmpeg()
    src = tmp_path / "ep.flac"
    _synthesize(ffmpeg, src, channels=2, volume_db=-15,
                codec_args=["-c:a", "flac"], source="noise")
    _normalize_and_check(ffmpeg, src, "ep_normalized.flac")


def test_aiff(tmp_path: Path) -> None:
    ffmpeg = _ffmpeg()
    src = tmp_path / "ep.aiff"
    _synthesize(ffmpeg, src, channels=2, volume_db=-15,
                codec_args=["-c:a", "pcm_s16be"], source="noise")
    _normalize_and_check(ffmpeg, src, "ep_normalized.aiff")


def test_ogg_rewrites_to_wav(tmp_path: Path) -> None:
    ffmpeg = _ffmpeg()
    src = tmp_path / "ep.ogg"
    _synthesize(ffmpeg, src, channels=2, volume_db=-15,
                codec_args=["-c:a", "libvorbis"], source="noise")
    _normalize_and_check(ffmpeg, src, "ep_normalized.wav")


def test_opus_rewrites_to_wav(tmp_path: Path) -> None:
    ffmpeg = _ffmpeg()
    src = tmp_path / "ep.opus"
    # opus prefers 48k mono/stereo
    _synthesize(ffmpeg, src, channels=2, volume_db=-15,
                codec_args=["-c:a", "libopus", "-b:a", "96k"], source="noise")
    _normalize_and_check(ffmpeg, src, "ep_normalized.wav")


# --- Edge cases ------------------------------------------------------------


def test_already_normalized_output_is_skipped(tmp_path: Path) -> None:
    ffmpeg = _ffmpeg()
    src = tmp_path / "ep_normalized.wav"
    _synthesize(ffmpeg, src, channels=2, volume_db=-10,
                codec_args=["-c:a", "pcm_s16le"])
    assert normalizer.should_process(src) is False


def test_unsupported_extension_is_skipped(tmp_path: Path) -> None:
    txt = tmp_path / "notes.txt"
    txt.write_text("hello")
    assert normalizer.should_process(txt) is False


def test_overwrites_existing_output(tmp_path: Path) -> None:
    ffmpeg = _ffmpeg()
    src = tmp_path / "ep.wav"
    _synthesize(ffmpeg, src, channels=2, volume_db=-10,
                codec_args=["-c:a", "pcm_s16le"])
    pre_existing = tmp_path / "ep_normalized.wav"
    pre_existing.write_bytes(b"stale")
    out = _normalize_and_check(ffmpeg, src, "ep_normalized.wav")
    # Should be replaced with real audio, not the stale 5 bytes
    assert out.stat().st_size > 1000


def test_hot_input_does_not_clip_output(tmp_path: Path) -> None:
    """A signal that would clip if naively gained up should still respect the
    true-peak ceiling (-1.5 dBTP) after normalization."""
    ffmpeg = _ffmpeg()
    src = tmp_path / "hot.wav"
    # Pink noise at 0 dB — already very close to digital ceiling. Naively
    # adding gain to hit -16 LUFS would clip; loudnorm should switch to
    # dynamic mode and keep peaks under -1.5 dBTP.
    _synthesize(ffmpeg, src, channels=2, volume_db=0,
                codec_args=["-c:a", "pcm_s16le"], source="noise")
    result = normalizer.normalize(src, ffmpeg=ffmpeg)
    assert result.output_path.exists()

    true_peak = _measure_true_peak_dbfs(ffmpeg, result.output_path)
    # ffmpeg's loudnorm aims for TP=-1.5 dBTP; allow 0.3 dB of measurement slack
    assert true_peak <= -1.5 + 0.3, (
        f"output exceeded true-peak ceiling: {true_peak} dBTP (limit -1.5)"
    )

    integrated = _measure(ffmpeg, result.output_path)
    # Hot inputs may land slightly shy of -16 because peaks force dynamic mode
    assert abs(integrated - normalizer.LUFS_TARGET) <= 1.5, (
        f"hot input: expected -16 ±1.5, got {integrated}"
    )


def test_filename_with_spaces_and_unicode(tmp_path: Path) -> None:
    ffmpeg = _ffmpeg()
    src = tmp_path / "Épisode 42 — final.wav"
    _synthesize(ffmpeg, src, channels=2, volume_db=-12,
                codec_args=["-c:a", "pcm_s16le"])
    _normalize_and_check(ffmpeg, src, "Épisode 42 — final_normalized.wav")


# --- Watcher integration ---------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.results: list[normalizer.Result] = []
        self.errors: list[tuple[Path, Exception]] = []
        self.statuses: list[str] = []
        self._lock = threading.Lock()
        self.done_event = threading.Event()
        self.expected_count = 0

    def on_status(self, msg: str) -> None:
        with self._lock:
            self.statuses.append(msg)

    def on_done(self, r: normalizer.Result) -> None:
        with self._lock:
            self.results.append(r)
            if len(self.results) >= self.expected_count:
                self.done_event.set()

    def on_error(self, p: Path, e: Exception) -> None:
        with self._lock:
            self.errors.append((p, e))


def test_watcher_processes_two_concurrent_drops(tmp_path: Path) -> None:
    """Two files arriving within 1s should both process (queue, not drop)."""
    ffmpeg = _ffmpeg()

    # Pre-build sources outside the watch dir, then move them in instantly
    staging = tmp_path / "staging"
    staging.mkdir()
    src_a = staging / "a.wav"
    src_b = staging / "b.wav"
    _synthesize(ffmpeg, src_a, channels=2, volume_db=-10,
                codec_args=["-c:a", "pcm_s16le"], duration=3)
    _synthesize(ffmpeg, src_b, channels=2, volume_db=-10,
                codec_args=["-c:a", "pcm_s16le"], duration=3)

    watch = tmp_path / "watch"
    watch.mkdir()

    rec = _Recorder()
    rec.expected_count = 2
    cb = WorkerCallbacks(
        on_status=rec.on_status, on_done=rec.on_done, on_error=rec.on_error,
    )
    w = FolderWatcher(watch, cb)
    w.start()
    try:
        # Move both in rapid succession
        shutil.move(src_a, watch / "a.wav")
        shutil.move(src_b, watch / "b.wav")
        # Allow time for: 2s stable wait + ffmpeg run, twice
        finished = rec.done_event.wait(timeout=60)
        assert finished, (
            f"timed out: results={len(rec.results)}, errors={rec.errors}, "
            f"recent status={rec.statuses[-5:]}"
        )
    finally:
        w.stop()

    out_names = sorted(r.output_path.name for r in rec.results)
    assert out_names == ["a_normalized.wav", "b_normalized.wav"]
    assert rec.errors == []
    for r in rec.results:
        m = _measure(ffmpeg, r.output_path)
        assert abs(m - normalizer.LUFS_TARGET) <= 0.7


def test_watcher_picks_up_existing_files_on_start(tmp_path: Path) -> None:
    """Files already in the folder when the watcher starts should be processed."""
    ffmpeg = _ffmpeg()
    watch = tmp_path / "watch"
    watch.mkdir()
    src = watch / "preexisting.wav"
    _synthesize(ffmpeg, src, channels=2, volume_db=-10,
                codec_args=["-c:a", "pcm_s16le"], duration=3)

    rec = _Recorder()
    rec.expected_count = 1
    cb = WorkerCallbacks(
        on_status=rec.on_status, on_done=rec.on_done, on_error=rec.on_error,
    )
    w = FolderWatcher(watch, cb)
    w.start()
    try:
        assert rec.done_event.wait(timeout=45), (
            f"timed out: results={len(rec.results)} errors={rec.errors}"
        )
    finally:
        w.stop()

    assert len(rec.results) == 1
    assert rec.results[0].output_path.name == "preexisting_normalized.wav"


def test_watcher_skips_already_normalized_file(tmp_path: Path) -> None:
    """A *_normalized file should never get re-normalized."""
    ffmpeg = _ffmpeg()
    watch = tmp_path / "watch"
    watch.mkdir()
    pre = watch / "ep_normalized.wav"
    _synthesize(ffmpeg, pre, channels=2, volume_db=-10,
                codec_args=["-c:a", "pcm_s16le"], duration=2)

    rec = _Recorder()
    rec.expected_count = 1  # we don't expect this to fire
    cb = WorkerCallbacks(
        on_status=rec.on_status, on_done=rec.on_done, on_error=rec.on_error,
    )
    w = FolderWatcher(watch, cb)
    w.start()
    try:
        # Wait briefly; the worker shouldn't pick it up
        rec.done_event.wait(timeout=5)
    finally:
        w.stop()

    assert rec.results == []
    assert rec.errors == []
    # The *_normalized*_normalized double-suffix file must NOT exist
    assert not (watch / "ep_normalized_normalized.wav").exists()


def test_watcher_waits_for_slow_copy(tmp_path: Path) -> None:
    """A file whose size is still growing when first observed must not be processed
    until it stabilizes."""
    ffmpeg = _ffmpeg()
    watch = tmp_path / "watch"
    watch.mkdir()

    final = tmp_path / "src.wav"
    _synthesize(ffmpeg, final, channels=2, volume_db=-12,
                codec_args=["-c:a", "pcm_s16le"], duration=4)
    payload = final.read_bytes()
    assert len(payload) > 5000

    rec = _Recorder()
    rec.expected_count = 1
    cb = WorkerCallbacks(
        on_status=rec.on_status, on_done=rec.on_done, on_error=rec.on_error,
    )
    w = FolderWatcher(watch, cb)
    w.start()
    try:
        target = watch / "slow.wav"
        # Simulate a slow copy: write 25% chunks with delays.
        # The watcher should see the create event then wait for stability.
        chunk = len(payload) // 4
        with target.open("wb") as f:
            f.write(payload[:chunk])
            f.flush()
            threading.Event().wait(0.6)
            f.write(payload[chunk:chunk * 2])
            f.flush()
            threading.Event().wait(0.6)
            f.write(payload[chunk * 2:chunk * 3])
            f.flush()
            threading.Event().wait(0.6)
            f.write(payload[chunk * 3:])
            f.flush()
        assert rec.done_event.wait(timeout=45), (
            f"timed out: results={rec.results} errors={rec.errors} "
            f"recent={rec.statuses[-5:]}"
        )
    finally:
        w.stop()

    assert len(rec.results) == 1
    out = rec.results[0].output_path
    assert out.name == "slow_normalized.wav"
    m = _measure(ffmpeg, out)
    assert abs(m - normalizer.LUFS_TARGET) <= 0.7
