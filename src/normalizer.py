"""Two-pass EBU R128 loudness normalization via ffmpeg."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .log import get_logger

LUFS_TARGET = -16.0
TRUE_PEAK = -1.5
LRA = 11.0
SAMPLE_RATE = 48000

LOSSLESS_CONTAINERS = {".wav", ".aif", ".aiff", ".flac"}
MP3_CONTAINERS = {".mp3"}
AAC_CONTAINERS = {".m4a", ".aac"}
LOSSY_TO_WAV = {".ogg", ".opus", ".wma"}
ALLOWED_EXTS = (
    LOSSLESS_CONTAINERS | MP3_CONTAINERS | AAC_CONTAINERS | LOSSY_TO_WAV
)

NORMALIZED_SUFFIX = "_normalized"

ProgressCb = Callable[[str], None]


class NormalizerError(Exception):
    pass


@dataclass
class Measurement:
    input_i: float
    input_tp: float
    input_lra: float
    input_thresh: float
    target_offset: float


@dataclass
class Result:
    input_path: Path
    output_path: Path
    measured_in: Optional[float]
    measured_out: Optional[float]


def is_normalized_name(path: Path) -> bool:
    return path.stem.endswith(NORMALIZED_SUFFIX)


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in ALLOWED_EXTS


def should_process(path: Path) -> bool:
    if path.name.startswith("."):
        return False
    if not is_supported(path):
        return False
    if is_normalized_name(path):
        return False
    return True


def output_path_for(input_path: Path) -> Path:
    ext = input_path.suffix.lower()
    if ext in LOSSY_TO_WAV:
        out_ext = ".wav"
    else:
        out_ext = input_path.suffix
    return input_path.with_name(f"{input_path.stem}{NORMALIZED_SUFFIX}{out_ext}")


def find_ffmpeg() -> str:
    """Locate the ffmpeg binary.

    In a py2app bundle ffmpeg lives next to the main binary in Resources.
    During development we fall back to repo's resources/ffmpeg, then PATH.
    """
    bundle_resources = os.environ.get("RESOURCEPATH")
    candidates: list[Path] = []
    if bundle_resources:
        candidates.append(Path(bundle_resources) / "ffmpeg")
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent.parent / "Resources" / "ffmpeg")

    repo_root = Path(__file__).resolve().parent.parent
    candidates.append(repo_root / "resources" / "ffmpeg")

    for c in candidates:
        if c.exists() and os.access(c, os.X_OK):
            return str(c)

    from shutil import which
    found = which("ffmpeg")
    if found:
        return found
    raise NormalizerError("ffmpeg not found (looked in app bundle, resources/, and PATH)")


_LOUDNORM_JSON_RE = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.DOTALL)


def _parse_loudnorm_json(stderr: str) -> Optional[Measurement]:
    match = _LOUDNORM_JSON_RE.search(stderr)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return Measurement(
            input_i=float(data["input_i"]),
            input_tp=float(data["input_tp"]),
            input_lra=float(data["input_lra"]),
            input_thresh=float(data["input_thresh"]),
            target_offset=float(data["target_offset"]),
        )
    except (KeyError, ValueError, json.JSONDecodeError):
        return None


def _output_codec_args(input_ext: str) -> list[str]:
    ext = input_ext.lower()
    if ext == ".wav":
        return ["-c:a", "pcm_s24le"]
    if ext in {".aif", ".aiff"}:
        return ["-c:a", "pcm_s24be"]
    if ext == ".flac":
        return ["-c:a", "flac", "-sample_fmt", "s32"]
    if ext == ".mp3":
        return ["-c:a", "libmp3lame", "-b:a", "192k"]
    if ext in {".m4a", ".aac"}:
        return ["-c:a", "aac", "-b:a", "192k"]
    if ext in LOSSY_TO_WAV:
        return ["-c:a", "pcm_s24le"]
    return ["-c:a", "pcm_s24le"]


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )


def measure(ffmpeg: str, input_path: Path) -> Optional[Measurement]:
    cmd = [
        ffmpeg, "-hide_banner", "-nostats", "-i", str(input_path),
        "-af", f"loudnorm=I={LUFS_TARGET}:TP={TRUE_PEAK}:LRA={LRA}:print_format=json",
        "-f", "null", "-",
    ]
    proc = _run(cmd)
    if proc.returncode != 0:
        _log_ffmpeg_failure("measure", input_path, proc.stderr)
        return None
    return _parse_loudnorm_json(proc.stderr)


def apply_two_pass(ffmpeg: str, input_path: Path, output_path: Path,
                   m: Measurement) -> tuple[bool, str]:
    af = (
        f"loudnorm=I={LUFS_TARGET}:TP={TRUE_PEAK}:LRA={LRA}:"
        f"measured_I={m.input_i}:measured_TP={m.input_tp}:"
        f"measured_LRA={m.input_lra}:measured_thresh={m.input_thresh}:"
        f"offset={m.target_offset}:linear=true:print_format=summary"
    )
    cmd = [
        ffmpeg, "-hide_banner", "-y", "-i", str(input_path),
        "-af", af,
        "-ar", str(SAMPLE_RATE),
        *_output_codec_args(output_path.suffix),
        str(output_path),
    ]
    proc = _run(cmd)
    return proc.returncode == 0, proc.stderr


def apply_single_pass(ffmpeg: str, input_path: Path,
                      output_path: Path) -> tuple[bool, str]:
    af = f"loudnorm=I={LUFS_TARGET}:TP={TRUE_PEAK}:LRA={LRA}:print_format=summary"
    cmd = [
        ffmpeg, "-hide_banner", "-y", "-i", str(input_path),
        "-af", af,
        "-ar", str(SAMPLE_RATE),
        *_output_codec_args(output_path.suffix),
        str(output_path),
    ]
    proc = _run(cmd)
    return proc.returncode == 0, proc.stderr


_OUTPUT_I_RE = re.compile(r"Output Integrated:\s*(-?\d+(?:\.\d+)?)\s*LUFS")


def _parse_output_lufs(stderr: str) -> Optional[float]:
    m = _OUTPUT_I_RE.search(stderr)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _log_ffmpeg_failure(stage: str, path: Path, stderr: str) -> None:
    log = get_logger()
    tail = "\n".join(stderr.splitlines()[-20:])
    log.error("ffmpeg %s failed for %s:\n%s", stage, path.name, tail)


def normalize(input_path: Path, ffmpeg: Optional[str] = None,
              progress: Optional[ProgressCb] = None) -> Result:
    """Normalize a single file. Raises NormalizerError on failure."""
    log = get_logger()
    ffmpeg = ffmpeg or find_ffmpeg()

    output_path = output_path_for(input_path)
    input_ext = input_path.suffix.lower()

    if output_path.exists():
        log.info("Output exists, will overwrite: %s", output_path.name)

    if input_ext in LOSSY_TO_WAV:
        log.info("Lossy input %s — writing 24-bit WAV to avoid re-encode loss",
                 input_path.name)

    if progress:
        progress(f"Processing: {input_path.name} (pass 1/2)")
    log.info("Pass 1 (measure): %s", input_path.name)
    measurement = measure(ffmpeg, input_path)

    if measurement is not None:
        if progress:
            progress(f"Processing: {input_path.name} (pass 2/2)")
        log.info("Pass 2 (apply): %s", input_path.name)
        ok, stderr = apply_two_pass(ffmpeg, input_path, output_path, measurement)
    else:
        log.warning("Pass-1 JSON missing for %s; falling back to single-pass",
                    input_path.name)
        if progress:
            progress(f"Processing: {input_path.name} (single-pass fallback)")
        ok, stderr = apply_single_pass(ffmpeg, input_path, output_path)

    if not ok:
        _log_ffmpeg_failure("encode", input_path, stderr)
        raise NormalizerError(
            f"ffmpeg failed for {input_path.name} (see "
            f"~/Library/Logs/PodcastNormalizer/normalizer.log)"
        )

    measured_out = _parse_output_lufs(stderr)
    measured_in = measurement.input_i if measurement else None

    if measured_out is not None:
        log.info("Done: %s (out: %.1f LUFS%s)",
                 output_path.name,
                 measured_out,
                 f", in: {measured_in:.1f} LUFS" if measured_in is not None else "")
    else:
        log.info("Done: %s", output_path.name)

    return Result(
        input_path=input_path,
        output_path=output_path,
        measured_in=measured_in,
        measured_out=measured_out,
    )
