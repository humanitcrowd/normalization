# CharLUFS

A small macOS desktop app that loudness-normalizes any audio file dropped into a watched folder to **-16 LUFS integrated** (EBU R128). The producer drags a file in; the app drops a `_normalized` sibling next to it. No terminal, no Python, no Homebrew.

This repo is the source. The shipped artifact is a `.dmg` containing a self-contained `.app` bundle with `ffmpeg` baked in.

## Layout

```
.
├── src/
│   ├── __main__.py        # entry point
│   ├── app.py             # Tk window + UI loop
│   ├── watcher.py         # watchdog handler + size-stable debounce
│   ├── normalizer.py      # ffmpeg two-pass loudnorm
│   ├── config.py          # ~/Library/Application Support/CharLUFS/config.json
│   └── log.py             # rotating file log + in-app ring buffer
├── resources/
│   ├── ffmpeg             # bundled static binary (NOT committed; download at build time)
│   ├── icon.icns          # app icon (optional during dev)
│   └── Info.plist.template
├── tests/
├── setup.py               # py2app entry
├── pyproject.toml
└── HANDOFF.md             # one-page guide for the producer
```

## Develop

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -e ".[test]"
python -m src                   # run the app from source
pytest                          # run the tests
```

The end-to-end LUFS tests will run if `ffmpeg` is on `PATH` or `resources/ffmpeg` exists; otherwise they skip.

## Build the `.app` and `.dmg` (Developer ID signed + notarized)

1. Drop a static universal2 macOS ffmpeg into `resources/ffmpeg`:
   ```bash
   curl -L -o /tmp/ffmpeg.zip https://evermeet.cx/ffmpeg/getrelease/zip
   unzip -o /tmp/ffmpeg.zip -d resources/
   chmod +x resources/ffmpeg
   ```
2. Build the app:
   ```bash
   python setup.py py2app
   ```
   Output: `dist/CharLUFS.app`.
3. Sign with hardened runtime + timestamp:
   ```bash
   codesign --deep --force --options runtime --timestamp \
     --sign "Developer ID Application: Your Name (TEAMID)" \
     "dist/CharLUFS.app"
   ```
4. Notarize and staple:
   ```bash
   ditto -c -k --keepParent "dist/CharLUFS.app" "dist/CharLUFS.zip"
   xcrun notarytool submit "dist/CharLUFS.zip" \
     --keychain-profile "charlufs-profile" --wait
   xcrun stapler staple "dist/CharLUFS.app"
   ```
5. Wrap in a DMG, sign + notarize that too:
   ```bash
   create-dmg \
     --volname "CharLUFS" \
     --window-size 500 300 --icon-size 100 \
     --app-drop-link 380 150 \
     "CharLUFS.dmg" "dist/CharLUFS.app"

   codesign --force --timestamp \
     --sign "Developer ID Application: Your Name (TEAMID)" \
     "CharLUFS.dmg"
   xcrun notarytool submit "CharLUFS.dmg" \
     --keychain-profile "charlufs-profile" --wait
   xcrun stapler staple "CharLUFS.dmg"
   ```

## Where things live at runtime

- Watched folder: `~/CharLUFS/` (or whatever was last picked)
- Config: `~/Library/Application Support/CharLUFS/config.json`
- Log: `~/Library/Logs/CharLUFS/normalizer.log` (rotates at 1 MB, 3 backups)

## Hardcoded choices

- Target: **-16 LUFS integrated**, true peak **-1.5 dBTP**, LRA **11 LU**
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

To change the target LUFS, edit `LUFS_TARGET` in `src/normalizer.py`.
