# Podcast Normalizer

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
│   ├── config.py          # ~/Library/Application Support/PodcastNormalizer/config.json
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

The end-to-end LUFS test will run if `ffmpeg` is on `PATH` or `resources/ffmpeg` exists; otherwise it skips.

## Build the `.app` and `.dmg`

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
   Output: `dist/Podcast Normalizer.app`.
3. Sign:
   - With Apple Developer ID:
     ```bash
     codesign --deep --force --options runtime \
       --sign "Developer ID Application: …" "dist/Podcast Normalizer.app"
     xcrun notarytool submit "Podcast Normalizer.app" --keychain-profile … --wait
     xcrun stapler staple "dist/Podcast Normalizer.app"
     ```
   - Ad-hoc (free, but the user must right-click → Open the first time):
     ```bash
     codesign --deep --force --sign - "dist/Podcast Normalizer.app"
     ```
4. Wrap in a DMG:
   ```bash
   create-dmg \
     --volname "Podcast Normalizer" \
     --window-size 500 300 --icon-size 100 \
     --app-drop-link 380 150 \
     "Podcast Normalizer.dmg" "dist/Podcast Normalizer.app"
   ```

## Where things live at runtime

- Watched folder: `~/Podcast Normalize/` (or whatever was last picked)
- Config: `~/Library/Application Support/PodcastNormalizer/config.json`
- Log: `~/Library/Logs/PodcastNormalizer/normalizer.log` (rotates at 1 MB, 3 backups)

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
