#!/usr/bin/env bash
# Build, sign, notarize, and zip CharLUFS for distribution.
#
# Requirements: venv active, ffmpeg already in resources/, notarytool
# keychain profile "charlufs-profile" already stored.
#
# Usage:  TEAM_ID=5M4NK28DQV ./scripts/build_and_sign.sh
set -euo pipefail

TEAM_ID="${TEAM_ID:?TEAM_ID env var required (e.g. 5M4NK28DQV)}"
PROFILE="${NOTARY_PROFILE:-charlufs-profile}"
APP="dist/CharLUFS.app"
ENTITLEMENTS="resources/entitlements.plist"

# --- 1. Build ----------------------------------------------------------
rm -rf build dist
python setup.py py2app

if [ ! -d "$APP" ]; then
  echo "Build failed: $APP not found" >&2
  exit 1
fi

# --- 2. Sign every Mach-O bottom-up ------------------------------------
# codesign --deep skips loose .so files in Resources/. We have to find
# and sign each one explicitly.
echo "Signing inner Mach-O binaries…"
find "$APP/Contents" \
  \( -name "*.so" -o -name "*.dylib" -o -name "*.framework" \) -print0 \
  | while IFS= read -r -d '' f; do
      codesign --force --options runtime --timestamp \
        --sign "$TEAM_ID" "$f" >/dev/null
  done

# Sign the embedded Python interpreter explicitly (it's a Mach-O without
# a recognizable extension)
for bin in "$APP/Contents/MacOS/python" \
           "$APP/Contents/Frameworks/Python.framework/Versions/Current/Python"; do
  if [ -f "$bin" ]; then
    codesign --force --options runtime --timestamp \
      --sign "$TEAM_ID" "$bin" >/dev/null
  fi
done

# Sign the bundled ffmpeg
if [ -f "$APP/Contents/Resources/ffmpeg" ]; then
  codesign --force --options runtime --timestamp \
    --sign "$TEAM_ID" "$APP/Contents/Resources/ffmpeg" >/dev/null
fi

# --- 3. Sign the main bundle with entitlements -------------------------
echo "Signing main bundle…"
codesign --force --options runtime --timestamp \
  --entitlements "$ENTITLEMENTS" \
  --sign "$TEAM_ID" "$APP"

codesign --verify --deep --strict --verbose=2 "$APP"

# --- 4. Notarize -------------------------------------------------------
echo "Submitting to notary service…"
NOTARY_ZIP="dist/CharLUFS-notarize.zip"
rm -f "$NOTARY_ZIP"
ditto -c -k --keepParent "$APP" "$NOTARY_ZIP"

xcrun notarytool submit "$NOTARY_ZIP" \
  --keychain-profile "$PROFILE" --wait

xcrun stapler staple "$APP"
xcrun stapler validate "$APP"

# --- 5. Final zip to ship ---------------------------------------------
rm -f "$NOTARY_ZIP" CharLUFS.zip
ditto -c -k --keepParent "$APP" "CharLUFS.zip"

ls -lh CharLUFS.zip
echo "Done: $(pwd)/CharLUFS.zip"
