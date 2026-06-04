"""EBU R128 loudness normalization via ffmpeg.

The in-place path (`normalize_in_place`, used by the app) applies a single
linear gain measured with the ebur128 meter — no compression or limiting.
The legacy `normalize` (`_normalized` sibling output, used by tests) still
uses loudnorm's two-pass.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .log import get_logger

BACKUP_DIR_NAME = "CharBackup"

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
    measured_in: float | None
    measured_out: float | None


def is_normalized_name(path: Path) -> bool:
    return path.stem.endswith(NORMALIZED_SUFFIX)


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in ALLOWED_EXTS


def should_process(path: Path) -> bool:
    if path.name.startswith("."):
        return False
    if not is_supported(path):
        return False
    return not is_normalized_name(path)


def output_path_for(input_path: Path) -> Path:
    ext = input_path.suffix.lower()
    out_ext = ".wav" if ext in LOSSY_TO_WAV else input_path.suffix
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
        bundle_root = Path(sys.executable).resolve().parent.parent
        candidates.append(bundle_root / "Resources" / "ffmpeg")

    repo_root = Path(__file__).resolve().parent.parent
    candidates.append(repo_root / "resources" / "ffmpeg")

    for c in candidates:
        if c.exists() and os.access(c, os.X_OK):
            return str(c)

    from shutil import which
    found = which("ffmpeg")
    if found:
        return found
    raise NormalizerError(
        "ffmpeg not found (looked in app bundle, resources/, and PATH)"
    )


_LOUDNORM_JSON_RE = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.DOTALL)


def _parse_loudnorm_json(stderr: str) -> Measurement | None:
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


def measure(ffmpeg: str, input_path: Path,
            target_lufs: float = LUFS_TARGET,
            true_peak: float = TRUE_PEAK) -> Measurement | None:
    cmd = [
        ffmpeg, "-hide_banner", "-nostats", "-i", str(input_path),
        "-af", f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={LRA}:print_format=json",
        "-f", "null", "-",
    ]
    proc = _run(cmd)
    if proc.returncode != 0:
        _log_ffmpeg_failure("measure", input_path, proc.stderr)
        return None
    return _parse_loudnorm_json(proc.stderr)


# ffmpeg's ebur128 filter is a faithful BS.1770 meter — it tracks dedicated
# meters (RX, YouLean) more closely than loudnorm's readout, and it applies
# the correct multichannel weighting (L/C/R at 0 dB, surrounds +1.5 dB, LFE
# excluded) from the file's channel layout. We use it for the numbers we
# *display*; loudnorm remains the engine that actually applies the gain.
_EBUR128_I_RE = re.compile(r"\bI:\s*(-?\d+(?:\.\d+)?)\s*LUFS")
_EBUR128_TP_RE = re.compile(r"\bPeak:\s*([+-]?\d+(?:\.\d+)?)\s*dBFS")


def measure_loudness(input_path: Path,
                     ffmpeg: str | None = None) -> tuple[float | None, float | None]:
    """Measure integrated loudness and true peak with the ebur128 filter.

    Returns (integrated_lufs, true_peak_dbtp); either may be None if the file
    is silent or parsing fails. The filter prints running values per frame and
    a final Summary block — we take the last match of each, which is the
    summary figure.
    """
    ffmpeg = ffmpeg or find_ffmpeg()
    cmd = [
        ffmpeg, "-hide_banner", "-nostats", "-i", str(input_path),
        "-af", "ebur128=peak=true", "-f", "null", "-",
    ]
    proc = _run(cmd)
    i_matches = _EBUR128_I_RE.findall(proc.stderr)
    tp_matches = _EBUR128_TP_RE.findall(proc.stderr)
    integrated = float(i_matches[-1]) if i_matches else None
    true_peak = float(tp_matches[-1]) if tp_matches else None
    if integrated is None:
        _log_ffmpeg_failure("ebur128", input_path, proc.stderr)
    return integrated, true_peak


def apply_two_pass(ffmpeg: str, input_path: Path, output_path: Path,
                   m: Measurement,
                   target_lufs: float = LUFS_TARGET,
                   true_peak: float = TRUE_PEAK,
                   sample_rate: int | None = SAMPLE_RATE) -> tuple[bool, str]:
    af = (
        f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={LRA}:"
        f"measured_I={m.input_i}:measured_TP={m.input_tp}:"
        f"measured_LRA={m.input_lra}:measured_thresh={m.input_thresh}:"
        f"offset={m.target_offset}:linear=true:print_format=summary"
    )
    cmd = [ffmpeg, "-hide_banner", "-y", "-i", str(input_path), "-af", af]
    if sample_rate is not None:
        cmd += ["-ar", str(sample_rate)]
    cmd += [*_output_codec_args(output_path.suffix), str(output_path)]
    proc = _run(cmd)
    return proc.returncode == 0, proc.stderr


def apply_single_pass(ffmpeg: str, input_path: Path,
                      output_path: Path,
                      target_lufs: float = LUFS_TARGET,
                      true_peak: float = TRUE_PEAK,
                      sample_rate: int | None = SAMPLE_RATE) -> tuple[bool, str]:
    af = f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={LRA}:print_format=summary"
    cmd = [ffmpeg, "-hide_banner", "-y", "-i", str(input_path), "-af", af]
    if sample_rate is not None:
        cmd += ["-ar", str(sample_rate)]
    cmd += [*_output_codec_args(output_path.suffix), str(output_path)]
    proc = _run(cmd)
    return proc.returncode == 0, proc.stderr


_OUTPUT_I_RE = re.compile(r"Output Integrated:\s*(-?\d+(?:\.\d+)?)\s*LUFS")


def _parse_output_lufs(stderr: str) -> float | None:
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


def _run_normalization(input_path: Path, output_path: Path, ffmpeg: str,
                       display_name: str,
                       progress: ProgressCb | None,
                       target_lufs: float,
                       true_peak: float = TRUE_PEAK,
                       sample_rate: int | None = SAMPLE_RATE,
                       ) -> tuple[float | None, float | None]:
    """Two-pass normalize from input_path to output_path. Returns (in_lufs, out_lufs).

    Pass 1 measures with loudnorm (its own measurement, slightly different
    from ebur128's). Pass 2 applies in `linear=true` mode and silently
    reverts to dynamic (transparent peak limiting) only when reaching the
    loudness target would breach the true-peak ceiling — necessary on raw
    dialogue tracks where un-edited transient peaks have huge crest factor.
    """
    log = get_logger()

    if progress:
        progress(f"Measuring {display_name}")
    log.info("Pass 1 (measure, target %.1f LUFS, TP %.1f dBTP): %s",
             target_lufs, true_peak, display_name)
    measurement = measure(ffmpeg, input_path, target_lufs=target_lufs,
                          true_peak=true_peak)

    if measurement is not None:
        if progress:
            progress(f"Normalizing {display_name}")
        log.info("Pass 2 (apply): %s", display_name)
        ok, stderr = apply_two_pass(ffmpeg, input_path, output_path,
                                    measurement, target_lufs=target_lufs,
                                    true_peak=true_peak, sample_rate=sample_rate)
    else:
        log.warning("Pass-1 JSON missing for %s; falling back to single-pass",
                    display_name)
        if progress:
            progress(f"Normalizing {display_name} (single-pass)")
        ok, stderr = apply_single_pass(ffmpeg, input_path, output_path,
                                       target_lufs=target_lufs,
                                       true_peak=true_peak,
                                       sample_rate=sample_rate)

    if not ok:
        _log_ffmpeg_failure("encode", input_path, stderr)
        raise NormalizerError(
            f"ffmpeg failed for {display_name} (see "
            f"~/Library/Logs/CharLUFS/normalizer.log)"
        )

    measured_out = _parse_output_lufs(stderr)
    measured_in = measurement.input_i if measurement else None
    return measured_in, measured_out


def normalize(input_path: Path, ffmpeg: str | None = None,
              progress: ProgressCb | None = None,
              target_lufs: float = LUFS_TARGET) -> Result:
    """Normalize a single file to a `_normalized` sibling. Raises NormalizerError."""
    log = get_logger()
    ffmpeg = ffmpeg or find_ffmpeg()

    output_path = output_path_for(input_path)
    input_ext = input_path.suffix.lower()

    if output_path.exists():
        log.info("Output exists, will overwrite: %s", output_path.name)
    if input_ext in LOSSY_TO_WAV:
        log.info("Lossy input %s — writing 24-bit WAV to avoid re-encode loss",
                 input_path.name)

    measured_in, measured_out = _run_normalization(
        input_path, output_path, ffmpeg, input_path.name, progress, target_lufs,
    )

    if measured_out is not None:
        log.info("Done: %s (out: %.1f LUFS%s)",
                 output_path.name, measured_out,
                 f", in: {measured_in:.1f} LUFS" if measured_in is not None else "")
    else:
        log.info("Done: %s", output_path.name)

    return Result(
        input_path=input_path, output_path=output_path,
        measured_in=measured_in, measured_out=measured_out,
    )


def backup_path_for(path: Path) -> Path:
    """Where the pristine original of `path` lives (or will live)."""
    return path.parent / BACKUP_DIR_NAME / path.name


def source_path_for(path: Path) -> Path:
    """The file that normalize_in_place will actually read from: the pristine
    backup if it already exists, otherwise the file itself. Used so the
    measure-on-drop level reflects the true original, not an
    already-normalized current file."""
    backup = backup_path_for(path)
    return backup if backup.exists() else path


def normalize_in_place(path: Path, ffmpeg: str | None = None,
                       progress: ProgressCb | None = None,
                       target_lufs: float = LUFS_TARGET,
                       true_peak: float = TRUE_PEAK) -> Result:
    """Normalize a file in place via loudnorm's two-pass linear mode,
    preserving the pristine original in a sibling `CharBackup/` folder.

    loudnorm applies a single linear gain to hit the loudness target. On
    peaky/dialogue material it can transparently revert to dynamic mode
    (look-ahead peak limiting) to push the file to target while respecting
    the true-peak ceiling — that's intentional and necessary, since raw
    dialogue typically has high enough crest factor that pure linear gain
    can't reach normal podcast loudness without breaching the ceiling.

    Sample rate is preserved (no `-ar`); channel count/layout is preserved.

    Behavior around the backup:
      - If `<dir>/CharBackup/<name>` already exists, it is treated as the
        source of truth (the pristine original) and we re-process from
        there. Re-runs are therefore idempotent w.r.t. the original audio.
      - Otherwise the current file is copied into the backup folder first.

    For lossy containers (.ogg/.opus/.wma) the output is a WAV with the
    same stem (the original lossy file is removed from its location since
    the pristine copy now lives in the backup folder).
    """
    log = get_logger()
    ffmpeg = ffmpeg or find_ffmpeg()

    if not is_supported(path):
        raise NormalizerError(f"unsupported format: {path.suffix}")
    if not path.exists():
        raise NormalizerError(f"file not found: {path}")

    backup = backup_path_for(path)
    backup.parent.mkdir(parents=True, exist_ok=True)

    if backup.exists():
        log.info("Using existing backup as source: %s", backup)
    else:
        shutil.copy2(path, backup)
        log.info("Backed up original: %s -> %s", path.name, backup)

    source_ext = backup.suffix.lower()
    out_ext = ".wav" if source_ext in LOSSY_TO_WAV else backup.suffix
    final_path = path.with_name(f"{path.stem}{out_ext}")

    # Write to a hidden temp file in the same dir, then atomic-replace.
    temp_path = path.with_name(f".{path.stem}_charlufs_tmp{out_ext}")
    if temp_path.exists():
        temp_path.unlink()

    try:
        measured_in, measured_out = _run_normalization(
            backup, temp_path, ffmpeg, path.name, progress, target_lufs,
            true_peak=true_peak, sample_rate=None,
        )
    except Exception:
        if temp_path.exists():
            with contextlib.suppress(OSError):
                temp_path.unlink()
        raise

    os.replace(str(temp_path), str(final_path))

    # If extension changed (lossy -> .wav), the original file at the old
    # extension is now stale; remove it (the pristine copy lives in backup).
    if final_path != path and path.exists():
        with contextlib.suppress(OSError):
            path.unlink()

    if measured_out is not None:
        log.info("Done in place: %s (out %.1f LUFS%s)",
                 final_path.name, measured_out,
                 f", in: {measured_in:.1f} LUFS" if measured_in is not None else "")
    else:
        log.info("Done in place: %s", final_path.name)

    return Result(
        input_path=path, output_path=final_path,
        measured_in=measured_in, measured_out=measured_out,
    )
