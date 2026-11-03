#!/bin/bash
# ==============================================================================
# VideoDownloader macOS One-Click Application (.app) & Disk Image (.dmg) Builder
# Inspired by Manna-main best practices:
#   - Supports Apple Silicon (arm64) and Intel (x86_64) builds
#   - Portable Python-build-standalone support
#   - PyInstaller --windowed bundle generation
#   - Ad-hoc deep code signing (codesign -s - --force --deep)
#   - Beautiful drag-and-drop installer .dmg (Application shortcut + custom layout)
# ==============================================================================

set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

APP_NAME="VideoDownloader"
VERSION="3.0.0"
BUNDLE_ID="com.videodownloader.desktop"
SIGN_ID="${SIGN_ID:-VideoDownloader Local Signing}"

ARCH="$(uname -m)"
if [ "$ARCH" = "arm64" ]; then
    ARCH_LABEL="AppleSilicon"
else
    ARCH_LABEL="Intel"
fi

echo "=========================================================="
echo "  Building $APP_NAME v$VERSION for macOS ($ARCH_LABEL)"
echo "=========================================================="

DIST="$ROOT/dist/mac"
WORK="$ROOT/build/mac"
rm -rf "$DIST" "$WORK"
mkdir -p "$DIST" "$WORK"

PYTHON_BIN="$(which python3 || echo "")"
if [ -z "$PYTHON_BIN" ]; then
    echo "[ERROR] python3 not found on PATH. Please install Python 3.11+." >&2
    exit 1
fi

echo "[1/5] Ensuring required packaging dependencies..."
"$PYTHON_BIN" -m pip install -q -U pip
"$PYTHON_BIN" -m pip install -q -U pyinstaller PyQt6 PyQt6-Fluent-Widgets pillow

echo "[2/5] Compiling $APP_NAME.app with PyInstaller..."
"$PYTHON_BIN" -m PyInstaller \
    --noconfirm \
    --clean \
    --windowed \
    --distpath "$DIST" \
    --workpath "$WORK" \
    --name "$APP_NAME" \
    --icon "$ROOT/videodownloader/assets/icon.png" \
    --osx-bundle-identifier "$BUNDLE_ID" \
    --add-data "$ROOT/videodownloader/assets:videodownloader/assets" \
    --collect-all yt_dlp \
    --collect-all qfluentwidgets \
    --collect-all PyQt6 \
    --collect-all imageio_ffmpeg \
    --hidden-import videodownloader.desktop_app \
    --hidden-import videodownloader.ui.backdrop \
    --hidden-import videodownloader.ui.stats_dialog \
    --hidden-import videodownloader.ui.command_palette \
    --paths "$ROOT" \
    "$ROOT/run_desktop.py"

APP_PATH="$DIST/$APP_NAME.app"

if [ -f "$ROOT/packaging/mac/Info.plist" ]; then
    echo "[3/5] Injecting optimized Info.plist..."
    cp "$ROOT/packaging/mac/Info.plist" "$APP_PATH/Contents/Info.plist"
    plutil -replace CFBundleShortVersionString -string "$VERSION" "$APP_PATH/Contents/Info.plist"
    plutil -replace CFBundleVersion -string "$VERSION" "$APP_PATH/Contents/Info.plist"
fi

echo "[4/5] Code-signing $APP_NAME.app..."
if security find-certificate -c "$SIGN_ID" >/dev/null 2>&1; then
    echo "Found signing certificate '$SIGN_ID', applying certified sign..."
    codesign --force --deep --sign "$SIGN_ID" "$APP_PATH"
else
    echo "No custom certificate found, applying standard ad-hoc signature..."
    codesign --force --deep --sign - "$APP_PATH"
fi
codesign --verify --deep --strict "$APP_PATH"

echo "[5/5] Generating drag-and-drop installer DMG..."
DMG_NAME="$APP_NAME-$VERSION-$ARCH_LABEL.dmg"
FINAL_DMG="$ROOT/dist/$DMG_NAME"
STAGE="$WORK/dmg_stage"
rm -rf "$STAGE" "$FINAL_DMG"
mkdir -p "$STAGE"

ditto "$APP_PATH" "$STAGE/$APP_NAME.app"
ln -s /Applications "$STAGE/Applications"

RW_DMG="$WORK/temp.dmg"
hdiutil create -quiet -volname "$APP_NAME" -srcfolder "$STAGE" -fs HFS+ -format UDRW -ov "$RW_DMG"

# Adjust Finder window layout if running on an interactive macOS desktop
MNT="$(hdiutil attach -readwrite -noverify -noautoopen "$RW_DMG" | awk -F'\t' '/\/Volumes\//{print $NF}')"
if [ "${CI:-}" != "true" ]; then
    osascript >/dev/null 2>&1 <<EOF || true
tell application "Finder"
  tell disk (do shell script "basename " & quoted form of "$MNT")
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set the bounds of container window to {200, 120, 780, 480}
    set opts to the icon view options of container window
    set arrangement of opts to not arranged
    set icon size of opts to 110
    set text size of opts to 13
    set position of item "$APP_NAME.app" of container window to {150, 160}
    set position of item "Applications" of container window to {430, 160}
    update without registering applications
    delay 1
    close
  end tell
end tell
EOF
fi
sync
hdiutil detach -quiet "$MNT" || hdiutil detach -force -quiet "$MNT" 2>/dev/null || true

hdiutil convert -quiet "$RW_DMG" -format UDZO -imagekey zlib-level=9 -ov -o "$FINAL_DMG"
rm -f "$RW_DMG"

echo "=========================================================="
echo "  [SUCCESS] macOS Build Completed!"
echo "  App: $APP_PATH"
echo "  DMG: $FINAL_DMG"
echo "=========================================================="
