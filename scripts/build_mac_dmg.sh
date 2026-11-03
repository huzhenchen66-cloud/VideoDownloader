#!/usr/bin/env bash
# Build VideoDownloader.app & VideoDownloader-<ver>-macOS-arm64.dmg on macOS (local Mac or GitHub Actions macos-latest).
# Inspired by Manna's clean Apple Silicon `.app` + `hdiutil` `.dmg` packaging pipeline.

set -euo pipefail

cd "$(dirname "$0")/.."
VERSION=$(python3 -c "import videodownloader; print(videodownloader.__version__)")

echo "[1/4] Generating brand iconset (.icns)..."
python3 scripts/generate_assets.py
ICONSET_DIR="build/VideoDownloader.iconset"
rm -rf "$ICONSET_DIR"
mkdir -p "$ICONSET_DIR"

SRC_PNG="videodownloader/assets/icon.png"
for sz in 16 32 128 256 512; do
  sips -z "$sz" "$sz" "$SRC_PNG" --out "${ICONSET_DIR}/icon_${sz}x${sz}.png" >/dev/null
  sz2=$((sz * 2))
  if [ "$sz2" -le 512 ]; then
    sips -z "$sz2" "$sz2" "$SRC_PNG" --out "${ICONSET_DIR}/icon_${sz}x${sz}@2x.png" >/dev/null
  fi
done
cp "$SRC_PNG" "${ICONSET_DIR}/icon_512x512@2x.png"
iconutil -c icns "$ICONSET_DIR" -o "build/VideoDownloader.icns"

echo "[2/4] Building VideoDownloader.app bundle via PyInstaller..."
python3 -m PyInstaller --noconfirm --clean --onedir --windowed --noupx \
  --name "VideoDownloader" \
  --icon "build/VideoDownloader.icns" \
  --osx-bundle-identifier "io.github.videodownloader.desktop" \
  --add-data "videodownloader/assets:videodownloader/assets" \
  --add-data "videodownloader/web_static:videodownloader/web_static" \
  --collect-data qfluentwidgets \
  --collect-data imageio_ffmpeg \
  --copy-metadata imageio_ffmpeg \
  --collect-all yt_dlp_ejs \
  --collect-all curl_cffi \
  --hidden-import PyQt6.QtMultimedia \
  --hidden-import PyQt6.QtMultimediaWidgets \
  --hidden-import PyQt6.QtNetwork \
  --hidden-import pynput.keyboard._darwin \
  run_desktop.py

echo "[3/4] Running ad-hoc codesign & post-build smoke test..."
codesign --force --deep --sign - "dist/VideoDownloader.app"
"dist/VideoDownloader.app/Contents/MacOS/VideoDownloader" --smoke-test "build/mac_smoke_report.json"

echo "[4/4] Packaging Drag-to-Applications DMG..."
STAGE_DIR="build/dmg_stage"
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"
cp -R "dist/VideoDownloader.app" "$STAGE_DIR/"
ln -s /Applications "$STAGE_DIR/Applications"

OUT_DMG="dist/VideoDownloader-${VERSION}-macOS-arm64.dmg"
rm -f "$OUT_DMG"
hdiutil create -volname "VideoDownloader" -srcfolder "$STAGE_DIR" -ov -format UDZO "$OUT_DMG"
echo "[OK] Built macOS DMG: $OUT_DMG"
