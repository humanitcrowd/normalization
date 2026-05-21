# CharLUFS

A small macOS desktop app that loudness-normalizes audio files dragged onto it. Drop one file or fifty, hit **Start**, and CharLUFS rewrites each file in place at your chosen LUFS target — the pristine original is preserved in a `char backup/` folder next to each file, so re-runs always start from the true original. The target loudness is adjustable from the UI (default **-16 LUFS**, range -23 to -8). No terminal, no Python, no Homebrew.

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
│   ├── normalizer.py      # ffmpeg two-pass loudnorm + in-place + backup logic
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

- Pristine originals: `<dir>/char backup/<filename>` — created next to each file the first time it's normalized. CharLUFS never deletes these automatically.
- Processed-files history (for the Recover button across launches): `~/Library/Application Support/CharLUFS/processed.json`
- Config (target LUFS): `~/Library/Application Support/CharLUFS/config.json`
- Log: `~/Library/Logs/CharLUFS/normalizer.log` (rotates at 1 MB, 3 backups)

## How processing works

- User drags files onto the app window. A native AppKit drop handler in `src/webapp.py` captures the absolute paths (WKWebView's JS doesn't expose them) and pushes them into `JobQueue`.
- User clicks **Start**. Up to N files are normalized in parallel — N defaults to `min(8, max(2, cpu_count // 2))`, which is 4 on an M3 base, 8 on M3 Max.
- For each file, `normalize_in_place` either copies the current file into `char backup/` (first run) or treats the existing backup as the source of truth (re-run), processes from the backup, and atomic-replaces the file at the original path.
- On success, an entry is upserted into `processed.json`. On next launch, those entries seed the Queue as `done` rows so the **Recover** button is available across sessions.
- Recover: delete the (post-normalize) file at the original location, copy `char backup/<filename>` back over it, drop the history entry. The backup file itself is left in place.

## Hardcoded choices

- Target loudness: **adjustable in the UI** — default **-16 LUFS integrated**, range **-23 to -8 LUFS** in 0.5 LU steps (snapped). True peak **-1.5 dBTP**, LRA **11 LU**.
- Output sample rate: **48 kHz**
- Output codecs by container:
  - `.wav` → 24-bit PCM (`pcm_s24le`)
  - `.aif` / `.aiff` → 24-bit PCM (`pcm_s24be`)
  - `.flac` → FLAC, sample_fmt s32
  - `.mp3` → libmp3lame 192 kbps CBR
  - `.m4a` / `.aac` → AAC 192 kbps
  - `.ogg` / `.opus` / `.wma` → 24-bit WAV (we don't pretend to round-trip these losslessly). The pristine original keeps its lossy extension in `char backup/`.
- Output: same path as input (in-place replace). For lossy inputs the extension changes to `.wav` and the original `.ogg`/`.opus`/`.wma` file is removed from its location (still preserved in the backup folder).

The default target and the slider range live in `src/config.py` (`DEFAULT_TARGET_LUFS`, `MIN_TARGET_LUFS`, `MAX_TARGET_LUFS`).
