#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

APP_NAME="Plume"
APP_DIR="${APP_NAME}.app"
PYTHON=".venv/bin/python3"

echo "═══════════════════════════════════════════"
echo "  Building ${APP_NAME}.app"
echo "═══════════════════════════════════════════"

# ── 1. Generate app icon ──
if [ ! -f "AppIcon.icns" ]; then
    echo "▸ Generating app icon..."
    $PYTHON create_icon.py AppIcon.icns
else
    echo "✓ App icon exists"
fi

# ── 2. Compile native launcher ──
echo "▸ Compiling native launcher..."

INCLUDE_DIR=$($PYTHON -c "import sysconfig; print(sysconfig.get_config_var('INCLUDEPY'))")
LIB_DIR=$($PYTHON -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR'))")
LDVERSION=$($PYTHON -c "import sysconfig; print(sysconfig.get_config_var('LDVERSION'))")
PYVER=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
SITE_PACKAGES="${SCRIPT_DIR}/.venv/lib/python${PYVER}/site-packages"

cc -o "${APP_NAME}-bin" launcher.c \
    -I"${INCLUDE_DIR}" \
    -L"${LIB_DIR}" \
    -lpython"${LDVERSION}" \
    -Wl,-rpath,"${LIB_DIR}" \
    -DSCRIPT_PATH="${SCRIPT_DIR}/app.py" \
    -DVENV_SITE_PACKAGES="${SITE_PACKAGES}" \
    -DVENV_PREFIX="${SCRIPT_DIR}/.venv" \
    -O2

echo "✓ Native launcher compiled"

# ── 3. Create .app bundle structure ──
echo "▸ Creating app bundle..."
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"

# ── 4. Info.plist ──
cat > "$APP_DIR/Contents/Info.plist" << 'PLIST'
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
    <string>2.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>2.0</string>
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

# ── 5. Copy binary and icon ──
mv "${APP_NAME}-bin" "$APP_DIR/Contents/MacOS/Plume"
cp AppIcon.icns "$APP_DIR/Contents/Resources/AppIcon.icns"

# ── 6. Ad-hoc code sign ──
echo "▸ Code signing..."
codesign --force --deep --sign - "$APP_DIR" 2>/dev/null || true
echo "✓ Built ${APP_DIR}"
echo ""

# ── 7. Install prompt ──
echo "To install to /Applications:"
echo "  bash build-app.sh --install"
echo ""
echo "Or just double-click ${APP_DIR} to run."

# ── Optional: install to /Applications ──
if [[ "${1:-}" == "--install" ]]; then
    echo ""
    echo "▸ Removing old VoiceType if present..."
    rm -rf "/Applications/VoiceType.app"
    echo "▸ Installing to /Applications..."
    rm -rf "/Applications/${APP_DIR}"
    cp -R "$APP_DIR" "/Applications/${APP_DIR}"
    codesign --force --deep --sign - "/Applications/${APP_DIR}" 2>/dev/null || true
    echo "✓ Installed to /Applications/${APP_NAME}.app"
    echo ""
    echo "  Launch Plume from Spotlight or Launchpad."
fi
