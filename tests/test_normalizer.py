"""Unit + integration tests for the normalizer.

The end-to-end LUFS test is gated on an ffmpeg binary being available on PATH
(or in resources/ffmpeg). It generates a synthetic -30 LUFS sine, normalizes,
and asserts the output measures -16 LUFS within ±0.5.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src import normalizer


# --- Pure logic tests (no ffmpeg required) ---------------------------------


def test_should_process_filters_by_extension(tmp_path: Path) -> None:
    assert normalizer.should_process(tmp_path / "ep.wav") is True
    assert normalizer.should_process(tmp_path / "ep.mp3") is True
    assert normalizer.should_process(tmp_path / "notes.txt") is False


def test_should_process_skips_normalized_outputs(tmp_path: Path) -> None:
    assert normalizer.should_process(tmp_path / "ep_normalized.wav") is False
    assert normalizer.should_process(tmp_path / "ep_normalized.mp3") is False


def test_should_process_skips_dotfiles(tmp_path: Path) -> None:
    assert normalizer.should_process(tmp_path / ".DS_Store") is False
    assert normalizer.should_process(tmp_path / ".hidden.wav") is False


def test_output_path_for_keeps_lossless_container(tmp_path: Path) -> None:
    assert (
        normalizer.output_path_for(tmp_path / "ep.wav").name == "ep_normalized.wav"
    )
    assert (
        normalizer.output_path_for(tmp_path / "ep.flac").name == "ep_normalized.flac"
    )
    assert (
        normalizer.output_path_for(tmp_path / "ep.aiff").name == "ep_normalized.aiff"
    )


def test_output_path_for_keeps_mp3_and_aac(tmp_path: Path) -> None:
    assert normalizer.output_path_for(tmp_path / "ep.mp3").name == "ep_normalized.mp3"
    assert normalizer.output_path_for(tmp_path / "ep.m4a").name == "ep_normalized.m4a"


def test_output_path_for_rewrites_lossy_to_wav(tmp_path: Path) -> None:
    assert normalizer.output_path_for(tmp_path / "ep.ogg").name == "ep_normalized.wav"
    assert normalizer.output_path_for(tmp_path / "ep.opus").name == "ep_normalized.wav"
    assert normalizer.output_path_for(tmp_path / "ep.wma").name == "ep_normalized.wav"


def test_parse_loudnorm_json_extracts_measurement() -> None:
    stderr = """
    [Parsed_loudnorm_0 @ 0x123]
    {
            "input_i" : "-30.10",
            "input_tp" : "-12.30",
            "input_lra" : "0.10",
            "input_thresh" : "-40.20",
            "output_i" : "-16.50",
            "output_tp" : "-1.50",
            "output_lra" : "0.00",
            "output_thresh" : "-26.60",
            "normalization_type" : "linear",
            "target_offset" : "-0.50"
    }
    """
    m = normalizer._parse_loudnorm_json(stderr)
    assert m is not None
    assert m.input_i == pytest.approx(-30.10)
    assert m.input_tp == pytest.approx(-12.30)
    assert m.target_offset == pytest.approx(-0.50)


def test_parse_loudnorm_json_returns_none_on_garbage() -> None:
    assert normalizer._parse_loudnorm_json("nothing here") is None


def test_parse_output_lufs_extracts_value() -> None:
    stderr = """
    [Parsed_loudnorm_0 @ 0x456]
    Input Integrated:    -30.1 LUFS
    Output Integrated:   -16.0 LUFS
    """
    assert normalizer._parse_output_lufs(stderr) == pytest.approx(-16.0)


# --- End-to-end test (requires ffmpeg) -------------------------------------


def _ffmpeg_or_skip() -> str:
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
        stdin=subprocess.DEVNULL,
        capture_output=True, text=True, check=False,
    )
    matches = _OUT_I_RE.findall(proc.stderr)
    assert matches, f"could not find I: in ebur128 output:\n{proc.stderr[-500:]}"
    return float(matches[-1])


def test_end_to_end_wav_normalizes_to_target(tmp_path: Path) -> None:
    ffmpeg = _ffmpeg_or_skip()

    # 10s sine at 1 kHz; loudnorm will renormalize to -16 regardless of source level
    src = tmp_path / "test.wav"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-y", "-f", "lavfi",
         "-i", "sine=frequency=1000:duration=10",
         "-af", "volume=-10dB",
         "-ar", "48000", "-c:a", "pcm_s16le",
         str(src)],
        stdin=subprocess.DEVNULL,
        check=True, capture_output=True,
    )

    result = normalizer.normalize(src, ffmpeg=ffmpeg)
    assert result.output_path.exists()
    assert result.output_path.name == "test_normalized.wav"

    measured = _measure_lufs(ffmpeg, result.output_path)
    assert abs(measured - normalizer.LUFS_TARGET) <= 0.5, (
        f"expected {normalizer.LUFS_TARGET} ±0.5, got {measured}"
    )
