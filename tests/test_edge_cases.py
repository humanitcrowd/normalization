"""Second-round bug hunt: edge cases not covered by test_audio_simulation.py.

Targets:
  - Corrupt / zero-byte / non-audio files masquerading as audio
  - Very short audio (loudnorm's minimum-content threshold)
  - Uppercase / mixed-case extensions
  - Multi-dot stems (episode.final.wav)
  - Already-quiet files (near -16 LUFS, should be a near no-op)
  - Symlink inputs
  - Config corruption / missing dir
  - Watcher start/stop/start cycle (no leak, second cycle works)
  - find_ffmpeg fallback chain
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from src import config as cfg
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


def _measure_lufs(ffmpeg: str, path: Path) -> float:
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(path),
         "-af", "ebur128=peak=true", "-f", "null", "-"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )
    matches = _OUT_I_RE.findall(proc.stderr)
    assert matches, f"no I: in ebur128 output:\n{proc.stderr[-500:]}"
    return float(matches[-1])


def _make_wav(ffmpeg: str, dst: Path, duration: float = 4.0,
              volume_db: float = -10.0) -> None:
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-y", "-f", "lavfi",
         "-i", f"anoisesrc=color=pink:duration={duration}:amplitude=0.5",
         "-af", f"volume={volume_db}dB",
         "-ar", "48000", "-c:a", "pcm_s16le",
         str(dst)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr[-500:]


# --- Garbage inputs --------------------------------------------------------


def test_zero_byte_wav_raises_and_writes_no_output(tmp_path: Path) -> None:
    """A 0-byte .wav must fail cleanly, not crash, and leave no output file."""
    src = tmp_path / "broken.wav"
    src.write_bytes(b"")
    with pytest.raises(normalizer.NormalizerError):
        normalizer.normalize(src, ffmpeg=_ffmpeg())
    assert not (tmp_path / "broken_normalized.wav").exists()


def test_text_file_with_audio_extension_raises(tmp_path: Path) -> None:
    """A .wav file that's actually a text file must fail without crashing."""
    src = tmp_path / "fake.wav"
    src.write_text("this is definitely not a WAV header")
    with pytest.raises(normalizer.NormalizerError):
        normalizer.normalize(src, ffmpeg=_ffmpeg())
    assert not (tmp_path / "fake_normalized.wav").exists()


def test_watcher_logs_error_for_garbage_and_keeps_running(tmp_path: Path) -> None:
    """A broken file should not take down the worker; subsequent good files
    must still process."""
    ffmpeg = _ffmpeg()
    watch = tmp_path / "watch"
    watch.mkdir()

    rec_results: list[normalizer.Result] = []
    rec_errors: list[tuple[Path, Exception]] = []
    good_done = threading.Event()
    rec_lock = threading.Lock()

    def on_done(r: normalizer.Result) -> None:
        with rec_lock:
            rec_results.append(r)
            if r.output_path.name == "good_normalized.wav":
                good_done.set()

    def on_error(p: Path, e: Exception) -> None:
        with rec_lock:
            rec_errors.append((p, e))

    cb = WorkerCallbacks(
        on_status=lambda s: None, on_done=on_done, on_error=on_error,
    )
    w = FolderWatcher(watch, cb)
    w.start()
    try:
        # Drop broken file first
        (watch / "broken.wav").write_bytes(b"")
        # Then drop a real file
        good_src = tmp_path / "good_src.wav"
        _make_wav(ffmpeg, good_src, duration=3, volume_db=-10)
        shutil.move(good_src, watch / "good.wav")

        assert good_done.wait(timeout=45), (
            f"good file never finished. results={rec_results}, errors={rec_errors}"
        )
    finally:
        w.stop()

    assert any(r.output_path.name == "good_normalized.wav" for r in rec_results)
    assert any(p.name == "broken.wav" for (p, _) in rec_errors)


# --- Short audio -----------------------------------------------------------


def test_very_short_clip_either_normalizes_or_fails_cleanly(tmp_path: Path) -> None:
    """1-second clip: loudnorm may or may not produce a measurement. Either
    way the app must not crash and must not leave a half-written output."""
    ffmpeg = _ffmpeg()
    src = tmp_path / "short.wav"
    _make_wav(ffmpeg, src, duration=1.0, volume_db=-10)
    try:
        result = normalizer.normalize(src, ffmpeg=ffmpeg)
    except normalizer.NormalizerError:
        # Acceptable: ffmpeg refused. Confirm no half-output left behind.
        assert not (tmp_path / "short_normalized.wav").exists()
        return
    # Success: output exists and is roughly at target. Tolerance is wider
    # because 1s is below loudnorm's preferred 3s minimum.
    assert result.output_path.exists()
    measured = _measure_lufs(ffmpeg, result.output_path)
    assert abs(measured - normalizer.LUFS_TARGET) <= 2.0


# --- Filename edge cases ---------------------------------------------------


def test_uppercase_extension_processes(tmp_path: Path) -> None:
    ffmpeg = _ffmpeg()
    src = tmp_path / "EP.WAV"
    _make_wav(ffmpeg, src, duration=3, volume_db=-12)
    assert normalizer.should_process(src) is True
    out = normalizer.output_path_for(src)
    assert out.name == "EP_normalized.WAV"
    result = normalizer.normalize(src, ffmpeg=ffmpeg)
    assert result.output_path.exists()


def test_multidot_stem(tmp_path: Path) -> None:
    """`episode.final.wav` must produce `episode.final_normalized.wav`."""
    ffmpeg = _ffmpeg()
    src = tmp_path / "episode.final.wav"
    _make_wav(ffmpeg, src, duration=3, volume_db=-10)
    result = normalizer.normalize(src, ffmpeg=ffmpeg)
    assert result.output_path.name == "episode.final_normalized.wav"
    assert result.output_path.exists()


def test_stem_containing_normalized_is_processed(tmp_path: Path) -> None:
    """`my_normalized_audio.wav` does NOT end in `_normalized`, so it must be
    processed (not skipped)."""
    src = tmp_path / "my_normalized_audio.wav"
    assert normalizer.should_process(src) is True


def test_no_extension_skipped(tmp_path: Path) -> None:
    src = tmp_path / "episode42"
    src.touch()
    assert normalizer.should_process(src) is False


# --- Already-quiet input ---------------------------------------------------


def test_input_already_near_target_is_near_noop(tmp_path: Path) -> None:
    """A signal already near -16 LUFS should come out near -16 LUFS with very
    small applied offset."""
    ffmpeg = _ffmpeg()
    src = tmp_path / "already.wav"
    # Pink noise at -10 dB ≈ -22 LUFS; pre-shape with volume so input lands
    # near -16. Quickest path: build then measure once to confirm the source.
    _make_wav(ffmpeg, src, duration=4, volume_db=-3)
    src_lufs = _measure_lufs(ffmpeg, src)
    # Even if source isn't exactly -16, output must still hit -16 ±0.7
    result = normalizer.normalize(src, ffmpeg=ffmpeg)
    out_lufs = _measure_lufs(ffmpeg, result.output_path)
    assert abs(out_lufs - normalizer.LUFS_TARGET) <= 0.7, (
        f"source={src_lufs:.2f} → output={out_lufs:.2f}"
    )


# --- Symlinks --------------------------------------------------------------


def test_symlink_to_audio_processes(tmp_path: Path) -> None:
    """A symlink in the watched folder pointing to a real audio file should
    still get normalized. Output lands next to the symlink."""
    ffmpeg = _ffmpeg()
    real = tmp_path / "real" / "audio.wav"
    real.parent.mkdir()
    _make_wav(ffmpeg, real, duration=3, volume_db=-10)

    link = tmp_path / "link.wav"
    os.symlink(real, link)
    assert link.is_symlink()
    assert normalizer.should_process(link) is True

    result = normalizer.normalize(link, ffmpeg=ffmpeg)
    assert result.output_path.name == "link_normalized.wav"
    assert result.output_path.parent == tmp_path  # next to the symlink
    assert result.output_path.exists()


# --- Config robustness ----------------------------------------------------


def test_load_returns_default_when_config_missing(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "does_not_exist.json")
    c = cfg.load()
    assert c.watch_folder == cfg.DEFAULT_WATCH_FOLDER


def test_load_returns_default_when_config_is_garbage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = tmp_path / "config.json"
    bad.write_text("{this is not valid json")
    monkeypatch.setattr(cfg, "CONFIG_PATH", bad)
    c = cfg.load()
    assert c.watch_folder == cfg.DEFAULT_WATCH_FOLDER


def test_save_then_load_roundtrip(tmp_path: Path,
                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "APP_SUPPORT", tmp_path)
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "DEFAULT_WATCH_FOLDER", tmp_path / "default")
    chosen = tmp_path / "my_folder"
    cfg.save(cfg.Config(watch_folder=chosen))
    loaded = cfg.load()
    assert loaded.watch_folder == chosen
    # And the JSON is valid
    data = json.loads((tmp_path / "config.json").read_text())
    assert data["watch_folder"] == str(chosen)


def test_load_falls_back_when_json_has_no_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = tmp_path / "config.json"
    bad.write_text("{}")
    monkeypatch.setattr(cfg, "CONFIG_PATH", bad)
    c = cfg.load()
    assert c.watch_folder == cfg.DEFAULT_WATCH_FOLDER


# --- Watcher lifecycle ----------------------------------------------------


def test_watcher_start_stop_start_cycle(tmp_path: Path) -> None:
    """Stopping and restarting a watcher must not leak threads or break
    subsequent processing."""
    ffmpeg = _ffmpeg()
    watch = tmp_path / "watch"
    watch.mkdir()

    threads_before = threading.active_count()

    # First cycle
    rec1_done = threading.Event()
    rec1_results: list[normalizer.Result] = []

    def cb1_done(r: normalizer.Result) -> None:
        rec1_results.append(r)
        rec1_done.set()

    w1 = FolderWatcher(watch, WorkerCallbacks(
        on_status=lambda s: None, on_done=cb1_done, on_error=lambda p, e: None,
    ))
    w1.start()
    src1 = tmp_path / "src1.wav"
    _make_wav(ffmpeg, src1, duration=3, volume_db=-10)
    shutil.move(src1, watch / "first.wav")
    assert rec1_done.wait(timeout=30)
    w1.stop()
    # Allow daemon worker to wind down
    threading.Event().wait(1.5)

    # Second cycle on the same folder
    rec2_done = threading.Event()
    rec2_results: list[normalizer.Result] = []

    def cb2_done(r: normalizer.Result) -> None:
        rec2_results.append(r)
        rec2_done.set()

    w2 = FolderWatcher(watch, WorkerCallbacks(
        on_status=lambda s: None, on_done=cb2_done, on_error=lambda p, e: None,
    ))
    w2.start()
    try:
        src2 = tmp_path / "src2.wav"
        _make_wav(ffmpeg, src2, duration=3, volume_db=-10)
        shutil.move(src2, watch / "second.wav")
        assert rec2_done.wait(timeout=30)
    finally:
        w2.stop()

    threading.Event().wait(1.5)
    threads_after = threading.active_count()

    # Allow for a single straggler daemon thread; we should not be leaking
    # one per cycle.
    assert threads_after - threads_before <= 2, (
        f"thread leak: before={threads_before}, after={threads_after}"
    )
    assert rec1_results and rec2_results


# --- ffmpeg resolution ----------------------------------------------------


def test_find_ffmpeg_uses_repo_resources_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If resources/ffmpeg exists and is executable, it wins over PATH."""
    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    # Point the repo_root candidate at our tmp dir by patching the module path
    # constants via env: easiest is to inject via a candidate list patch.
    monkeypatch.setattr(
        normalizer, "__file__",
        str(tmp_path / "fake_pkg" / "normalizer.py"),
    )
    (tmp_path / "fake_pkg").mkdir()
    (tmp_path / "resources").mkdir(exist_ok=True)
    # Move the fake into the resources dir relative to fake __file__
    shutil.move(fake, tmp_path / "resources" / "ffmpeg")
    (tmp_path / "resources" / "ffmpeg").chmod(0o755)

    found = normalizer.find_ffmpeg()
    assert found == str(tmp_path / "resources" / "ffmpeg")


def test_find_ffmpeg_raises_when_truly_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no bundle, no resources/ffmpeg, and a PATH that has no ffmpeg,
    we should raise NormalizerError — not crash."""
    monkeypatch.delenv("RESOURCEPATH", raising=False)
    monkeypatch.setattr(
        normalizer, "__file__",
        str(tmp_path / "empty_pkg" / "normalizer.py"),
    )
    (tmp_path / "empty_pkg").mkdir()
    # No resources/ffmpeg created
    monkeypatch.setenv("PATH", str(tmp_path / "nothing_here"))
    with pytest.raises(normalizer.NormalizerError, match="ffmpeg not found"):
        normalizer.find_ffmpeg()
