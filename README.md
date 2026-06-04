# CharLUFS

A small macOS desktop app that loudness-normalizes audio files dragged onto it. Drop one file or fifty, hit **Start**, and CharLUFS rewrites each file in place at your chosen LUFS target. Normalization is **linear gain when possible**; on peaky material (raw dialogue with un-edited mouth clicks/plosives) it falls back to **transparent look-ahead peak limiting** only when needed to reach the target under the true-peak ceiling. The pristine original is preserved in a `CharBackup/` folder next to each file, so re-runs always start from the true original. Target loudness (default **-16 LUFS**, -23 to -8) and true-peak ceiling (default **-1.5 dBTP**) are both adjustable in the UI. Sample rate is preserved in == out. No terminal, no Python, no Homebrew.

This repo is the source. The shipped artifact is a notarized `.zip` containing a self-contained `.app` bundle with `ffmpeg` baked in.

## Layout

```
.
├── app_launcher.py        # top-level entry for the py2app bundle
├── src/
│   ├── __main__.py        # dev entry (python -m src)
│   ├── webapp.py          # pywebview UI — drives the React frontend, installs
│   │                      #   the native AppKit drag handler
│   ├── jobqueue.py        # parallel job queue (drives normalize_in_place)
│   ├── normalizer.py      # ebur128 metering (for displayed numbers) + loudnorm
│   │                      #   two-pass in-place normalize + CharBackup logic
│   ├── config.py          # ~/Library/Application Support/CharLUFS/config.json
│   ├── log.py             # rotating file log + in-app ring buffer
│   ├── watcher.py         # DEPRECATED — old folder-watcher mode, kept only
│   │                      #   for tests; not wired into the app any more
│   └── app.py             # DEPRECATED — old Tk UI, same caveat
├── web/                   # pywebview frontend (vanilla React, no build step)
│   ├── index.html
│   ├── app.js             # main UI
│   ├── charlie.js         # Charlie the dog SVG component
│   ├── styles.css
│   └── vendor/            # react.production.min.js + react-dom
├── resources/
│   ├── ffmpeg             # bundled static binary (NOT committed; download at build time)
│   ├── icon.icns          # app icon
│   ├── icon-src/          # source SVG for the icon
│   └── entitlements.plist # hardened-runtime entitlements
├── scripts/
│   ├── build_and_sign.sh  # one-shot build + sign + notarize + zip
│   └── build_icon.py      # regenerate icon.icns from charlie.svg
├── tests/
├── setup.py               # py2app entry
├── pyproject.toml
└── HANDOFF.md             # one-page guide for the producer
```

## Develop

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -e ".[test]"
python -m src                   # run the app from source
pytest                          # run the tests
```

The venv must be active every time you build or run from source — `python` resolves to `venv/bin/python` once activated, and the build script will refuse to run otherwise.

The end-to-end LUFS tests will run if `ffmpeg` is on `PATH` or `resources/ffmpeg` exists; otherwise they skip.

## Build the `.app` (Developer ID signed + notarized)

1. Drop a static universal2 macOS ffmpeg into `resources/ffmpeg`:
   ```bash
   curl -L -o /tmp/ffmpeg.zip https://evermeet.cx/ffmpeg/getrelease/zip
   unzip -o /tmp/ffmpeg.zip -d resources/
   chmod +x resources/ffmpeg
   ```
2. Ensure your notarytool keychain profile is set up once:
   ```bash
   xcrun notarytool store-credentials "charlufs-profile" \
     --apple-id you@example.com --team-id TEAMID --password APP_SPECIFIC_PW
   ```
3. Build + sign + notarize + zip in one shot:
   ```bash
   TEAM_ID=TEAMID ./scripts/build_and_sign.sh
   ```
   Output: `dist/CharLUFS.app` and a shippable `CharLUFS.zip` at the repo root.
4. Verify the build is properly signed:
   ```bash
   codesign -dv --verbose=4 dist/CharLUFS.app 2>&1 | head -5
   spctl -a -vvv -t install dist/CharLUFS.app
   ```
   You want `Authority=Developer ID Application: …` and `source=Notarized Developer ID`. An `adhoc` flag in the codesign output means the bundle is unsigned — `python setup.py py2app` alone produces an adhoc build, which works locally but Gatekeeper will block it on other machines.

To regenerate the icon from `resources/icon-src/charlie.svg` after editing it:

```bash
python scripts/build_icon.py
```

## Where things live at runtime

- Pristine originals: `<dir>/CharBackup/<filename>` — created next to each file the first time it's normalized. CharLUFS never deletes these automatically.
- Processed-files history (for the Recover button across launches): `~/Library/Application Support/CharLUFS/processed.json`
- Config: `~/Library/Application Support/CharLUFS/config.json` — `target_lufs` is written every time the slider moves but **ignored on launch** (the slider always resets to -16, so the producer doesn't pick up a stale creative setting). `true_peak` **is** restored on launch — it's a delivery-spec ceiling you set once.
- Log: `~/Library/Logs/CharLUFS/normalizer.log` (rotates at 1 MB, 3 backups)

## How processing works

- User drags files onto the app window. A native AppKit drop handler in `src/webapp.py` captures the absolute paths (WKWebView's JS doesn't expose them) and pushes them into `JobQueue`. After it handles the drop, Python dispatches a `charlufs:drag_reset` event to clear the JS-side drag overlay (since WKWebView swallows the drop event before JS sees `drop`/`dragleave`).
- **Measure on drop**: as soon as files are queued, `JobQueue` measures each one in the background with ffmpeg's `ebur128` filter (`normalizer.measure_loudness`), capped at `parallelism` concurrent runs by a semaphore, and shows its input loudness + true peak next to the row. `ebur128` is a faithful BS.1770 meter — it tracks dedicated meters (RX, YouLean) more closely than `loudnorm`'s readout, and applies the correct multichannel weighting (L/C/R at 0 dB, surrounds +1.5 dB, LFE excluded) from the file's channel layout. The measurement is taken against the *source* `normalize_in_place` will use — the `CharBackup/` copy if one exists, else the file itself — so re-dropping an already-normalized file shows the original's level.
- **Normalization engine**: `loudnorm` two-pass in `linear=true` mode. Pass 1 measures the source (loudness, true peak, range, threshold, offset); pass 2 applies a single linear gain to hit the loudness target. When the target can be reached without breaching the true-peak ceiling, this is a pure gain change — no dynamics. When it can't (raw dialogue with isolated transient peaks has crest factors of 20+ dB, so reaching podcast loudness via pure gain alone is often impossible), loudnorm silently reverts to **dynamic mode** — a look-ahead peak limiter that shaves the offending single-sample peaks and lets the body of the dialogue ride up to target. We rely on that: pure linear is the goal, peak limiting is the fallback. We tried a pure-linear-only path in 1.3.0; it under-shot loudness targets so severely on raw dialogue that it wasn't usable for the primary workflow.
- **Sample rate is preserved** (no `-ar` on the in-place path). The legacy `normalize()` (tests only) still forces 48 kHz.
- **Channel count/layout is preserved** (no `-ac`/downmix), so 5.1 in → 5.1 out. ebur128 weights the 5.1 measurement per BS.1770 (L/C/R 0 dB, surrounds +1.5 dB, LFE excluded).
- **True peak is an estimate.** BS.1770 true peak is reconstructed by oversampling (spec mandates ≥4×) — it's not a measured sample. ffmpeg, RX, Ozone, and YouLean each use slightly different oversampling/reconstruction, so TP readings can legitimately differ by 0.1–0.3 dB between meters, especially on transient material. loudnorm enforces the ceiling against its *own* TP estimate, so a downstream meter may read the output a hair above/below the ceiling; lower the ceiling for hard headroom.
- User clicks **Start**. Up to N files are normalized in parallel — N defaults to `min(8, max(2, cpu_count // 2))`, which is 4 on an M3 base, 8 on M3 Max.
- New files dropped while processing auto-join the running pool — free workers pick them up within ~100ms.
- For each file, `normalize_in_place` either copies the current file into `CharBackup/` (first run) or treats the existing backup as the source of truth (re-run), runs loudnorm two-pass from the backup, and atomic-replaces the file at the original path.
- The done-row output level is re-measured with ebur128 (not parsed from loudnorm's summary) so the displayed number matches dedicated meters tightly.
- On success, an entry is upserted into `processed.json`. On next launch, those entries seed the Queue as `done` rows so the **Recover** button is available across sessions.
- **Recover** (per row): delete the file at the original location, copy `CharBackup/<filename>` back over it, drop the history entry. The backup file itself is left in place.
- **Clear** (button): wipe all Pending + Done + Error rows from the queue and drop their entries from `processed.json`. Anything currently encoding is left alone. The on-disk `CharBackup/` folders are never touched — manual recover via Finder still works.

## Settings & fixed choices

- **Target loudness** — adjustable in the UI (slider/presets). Default **-16 LUFS**, range **-23 to -8 LUFS**, 0.5 LU steps. Resets to -16 on every launch (see "Where things live at runtime").
- **True-peak ceiling** — adjustable in the UI (stepper). Default **-1.5 dBTP**, range **-6.0 to -0.5 dBTP**, 0.5 dB steps. Unlike the loudness target, this **persists** across launches (it's a delivery-spec setting). The gain is capped so the output never exceeds this.
- **Processing** — loudnorm two-pass in `linear=true` mode. Pure linear gain when the loudness target is reachable under the TP ceiling; transparent look-ahead peak limiting only when it isn't (necessary for raw dialogue). LRA target 11 LU. Sample rate preserved.
- Output codecs by container:
  - `.wav` → 24-bit PCM (`pcm_s24le`)
  - `.aif` / `.aiff` → 24-bit PCM (`pcm_s24be`)
  - `.flac` → FLAC, sample_fmt s32
  - `.mp3` → libmp3lame 192 kbps CBR
  - `.m4a` / `.aac` → AAC 192 kbps
  - `.ogg` / `.opus` / `.wma` → 24-bit WAV (we don't pretend to round-trip these losslessly). The pristine original keeps its lossy extension in `CharBackup/`.
- Output: same path as input (in-place replace). For lossy inputs the extension changes to `.wav` and the original `.ogg`/`.opus`/`.wma` file is removed from its location (still preserved in the backup folder).

Defaults and ranges live in `src/config.py` (`DEFAULT_TARGET_LUFS`/`MIN`/`MAX_TARGET_LUFS`, `DEFAULT_TRUE_PEAK`/`MIN`/`MAX_TRUE_PEAK`).
