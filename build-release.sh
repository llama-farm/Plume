#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

APP_NAME="Plume"
VERSION="${1:-1.0.0}"
PYTHON=".venv/bin/python3"
PIP=".venv/bin/pip"
ARCH="$(uname -m)"

echo "═══════════════════════════════════════════"
echo "  Building ${APP_NAME} v${VERSION} (${ARCH})"
echo "═══════════════════════════════════════════"

# ── Prerequisites ──
if [ ! -f ".venv/bin/python3" ]; then
    echo "Error: .venv not found. Run setup.sh first."
    exit 1
fi

WHISPER_CLI="whisper.cpp/build/bin/whisper-cli"
if [ ! -f "$WHISPER_CLI" ]; then
    echo "Error: whisper-cli not found. Run setup.sh first."
    exit 1
fi

# ── Install build deps ──
echo "▸ Installing build dependencies..."
$PIP install --quiet pyinstaller

# ── Generate app icon if needed ──
if [ ! -f "AppIcon.icns" ]; then
    echo "▸ Generating app icon..."
    $PYTHON create_icon.py AppIcon.icns
fi

# ── Clean previous build ──
rm -rf build dist

# ── Run PyInstaller ──
echo "▸ Running PyInstaller..."
.venv/bin/pyinstaller \
    --name "$APP_NAME" \
    --windowed \
    --icon AppIcon.icns \
    --noconfirm \
    --clean \
    --noupx \
    --osx-bundle-identifier com.plume.app \
    --hidden-import rumps \
    --hidden-import sounddevice \
    --hidden-import _sounddevice_data \
    --hidden-import numpy \
    --hidden-import objc \
    --hidden-import Quartz \
    --hidden-import AppKit \
    --hidden-import CoreFoundation \
    --hidden-import settings \
    --collect-data sounddevice \
    --collect-data _sounddevice_data \
    app.py

APP_DIR="dist/${APP_NAME}.app"
RESOURCES="${APP_DIR}/Contents/Resources"

# ── Copy icons into Resources ──
echo "▸ Copying menu bar icons..."
mkdir -p "${RESOURCES}/icons"
cp icons/menubar.png icons/menubar-rec.png "${RESOURCES}/icons/"
cp icons/menubar@2x.png icons/menubar-rec@2x.png "${RESOURCES}/icons/" 2>/dev/null || true

# ── Bundle whisper-cli + dylibs ──
echo "▸ Bundling whisper-cli..."
cp "$WHISPER_CLI" "${RESOURCES}/whisper-cli"

# Gather all dylibs that whisper-cli needs
DYLIBS=(
    whisper.cpp/build/src/libwhisper.1.dylib
    whisper.cpp/build/ggml/src/libggml.0.dylib
    whisper.cpp/build/ggml/src/libggml-base.0.dylib
    whisper.cpp/build/ggml/src/libggml-cpu.0.dylib
    whisper.cpp/build/ggml/src/ggml-blas/libggml-blas.0.dylib
    whisper.cpp/build/ggml/src/ggml-metal/libggml-metal.0.dylib
)

for lib in "${DYLIBS[@]}"; do
    if [ -f "$lib" ]; then
        cp "$lib" "${RESOURCES}/"
    fi
done

# Fix whisper-cli rpaths to look next to itself
echo "▸ Fixing dylib paths..."
# Remove old rpaths
for rpath in $(otool -l "${RESOURCES}/whisper-cli" | grep -A2 LC_RPATH | grep "path " | awk '{print $2}'); do
    install_name_tool -delete_rpath "$rpath" "${RESOURCES}/whisper-cli" 2>/dev/null || true
done
# Add @loader_path
install_name_tool -add_rpath @loader_path "${RESOURCES}/whisper-cli"

# Fix dylib IDs and cross-references
for lib in "${RESOURCES}"/*.dylib; do
    name=$(basename "$lib")
    install_name_tool -id "@loader_path/${name}" "$lib" 2>/dev/null || true
    # Fix any transitive dylib references
    for dep in $(otool -L "$lib" | grep "@rpath" | awk '{print $1}'); do
        dep_name=$(basename "$dep")
        install_name_tool -change "$dep" "@loader_path/${dep_name}" "$lib" 2>/dev/null || true
    done
done

# ── Copy settings.py alongside app (PyInstaller may miss it) ──
# PyInstaller should pick it up via --hidden-import, but let's ensure
# the module is available in the bundle
FRAMEWORKS="${APP_DIR}/Contents/Frameworks"
if [ ! -f "${FRAMEWORKS}/settings.py" ]; then
    cp settings.py "${FRAMEWORKS}/" 2>/dev/null || true
fi

# ── Custom Info.plist ──
echo "▸ Writing Info.plist..."
cat > "${APP_DIR}/Contents/Info.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Plume</string>
    <key>CFBundleDisplayName</key>
    <string>Plume</string>
    <key>CFBundleIdentifier</key>
    <string>com.plume.app</string>
    <key>CFBundleVersion</key>
    <string>${VERSION}</string>
    <key>CFBundleShortVersionString</key>
    <string>${VERSION}</string>
    <key>CFBundleExecutable</key>
    <string>Plume</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSMinimumSystemVersion</key>
    <string>13.0</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSMicrophoneUsageDescription</key>
    <string>Plume needs microphone access to transcribe your speech.</string>
    <key>NSAppleEventsUsageDescription</key>
    <string>Plume needs to simulate keyboard events to paste transcribed text.</string>
</dict>
</plist>
PLIST

# ── Code sign ──
echo "▸ Code signing..."
codesign --force --deep --sign - "${APP_DIR}"

# ── Create distributable zip ──
echo "▸ Creating release archive..."
cd dist
ZIP_NAME="${APP_NAME}-${VERSION}-macOS-${ARCH}.zip"
ditto -c -k --keepParent "${APP_NAME}.app" "${ZIP_NAME}"
cd "$SCRIPT_DIR"

SIZE=$(du -sh "dist/${ZIP_NAME}" | cut -f1)
echo ""
echo "═══════════════════════════════════════════"
echo "  Build complete!"
echo "  dist/${ZIP_NAME} (${SIZE})"
echo "═══════════════════════════════════════════"
