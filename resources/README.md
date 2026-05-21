# Build resources

## `ffmpeg` (not committed)

Static universal2 macOS build. Download from <https://evermeet.cx/ffmpeg/> before running the build:

```bash
curl -L -o /tmp/ffmpeg.zip https://evermeet.cx/ffmpeg/getrelease/zip
unzip -o /tmp/ffmpeg.zip -d resources/
chmod +x resources/ffmpeg
```

## `icon.icns` / `icon.png` (committed)

The shipped app icon, plus a PNG copy for the README. Both are generated from `icon-src/charlie.svg` by:

```bash
python ../scripts/build_icon.py
```

That script renders the SVG over a rounded-square `#1C1D20` panel and writes both the `.icns` (every macOS size, from 16 up to 1024@2x) and the `.png`. Edit `icon-src/charlie.svg` and re-run to refresh.

## `entitlements.plist` (committed)

Hardened-runtime entitlements applied during signing. The build script (`scripts/build_and_sign.sh`) passes this to `codesign` when sealing the main bundle.

## `Info.plist.template` (committed)

Consumed by `setup.py` (py2app) and copied into `CharLUFS.app/Contents/Info.plist` at build time.
