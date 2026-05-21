# CharLUFS

A small macOS desktop app that loudness-normalizes any audio file dropped into a watched folder. The producer drags a file in; the app drops a `_normalized` sibling next to it. The target loudness is adjustable from the UI (default **-16 LUFS**, range -23 to -8). No terminal, no Python, no Homebrew.

This repo is the source. The shipped artifact is a notarized `.zip` containing a self-contained `.app` bundle with `ffmpeg` baked in.

## Layout

```
.
├── app_launcher.py        # top-level entry for the py2app bundle
├── src/
│   ├── __main__.py        # dev entry (python -m src)
│   ├── webapp.py          # pywebview UI (default) — drives the React frontend
│   ├── app.py             # legacy Tk UI (CHARLUFS_TK=1 to use it)
│   ├── watcher.py         # watchdog handler + size-stable debounce
│   ├── normalizer.py      # ffmpeg two-pass loudnorm
│   ├── config.py          # ~/Library/Application Support/CharLUFS/config.json
│   └── log.py             # rotating file log + in-app ring buffer
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
python -m src                   # run the app from source (pywebview UI)
CHARLUFS_TK=1 python -m src     # legacy Tk UI (rollback)
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

- Watched folder: `~/CharLUFS/` (or whatever was last picked)
- Config: `~/Library/Application Support/CharLUFS/config.json`
- Log: `~/Library/Logs/CharLUFS/normalizer.log` (rotates at 1 MB, 3 backups)

## Hardcoded choices

- Target loudness: **adjustable in the UI** — default **-16 LUFS integrated**, range **-23 to -8 LUFS** in 0.5 LU steps (snapped). True peak **-1.5 dBTP**, LRA **11 LU**.
- Output sample rate: **48 kHz**
- Output codecs by container:
  - `.wav` → 24-bit PCM (`pcm_s24le`)
  - `.aif` / `.aiff` → 24-bit PCM (`pcm_s24be`)
  - `.flac` → FLAC, sample_fmt s32
  - `.mp3` → libmp3lame 192 kbps CBR
  - `.m4a` / `.aac` → AAC 192 kbps
  - `.ogg` / `.opus` / `.wma` → 24-bit WAV (we don't pretend to round-trip these losslessly)
- Output filename: `<stem>_normalized<ext>`, same folder as input
- Files starting with `.`, files already containing `_normalized`, and unsupported extensions are skipped

The default target and the slider range live in `src/config.py` (`DEFAULT_TARGET_LUFS`, `MIN_TARGET_LUFS`, `MAX_TARGET_LUFS`).
