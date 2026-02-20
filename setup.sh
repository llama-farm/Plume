#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "═══════════════════════════════════════════"
echo "  Voice Type — Setup"
echo "═══════════════════════════════════════════"

# ── 1. Build whisper.cpp with Metal (GPU) support ──
if [ ! -f "whisper.cpp/build/bin/whisper-cli" ]; then
    echo ""
    echo "▸ Cloning whisper.cpp..."
    if [ ! -d "whisper.cpp" ]; then
        git clone --depth 1 https://github.com/ggerganov/whisper.cpp.git
    fi

    echo "▸ Building whisper.cpp with Metal acceleration..."
    cd whisper.cpp
    cmake -B build -DWHISPER_METAL=ON -DCMAKE_BUILD_TYPE=Release
    cmake --build build -j$(sysctl -n hw.ncpu) --config Release
    cd "$SCRIPT_DIR"
    echo "✓ whisper.cpp built successfully"
else
    echo "✓ whisper.cpp already built"
fi

# ── 2. Download model (large-v3-turbo — fast + excellent technical vocabulary) ──
MODEL_FILE="models/ggml-large-v3-turbo.bin"
if [ ! -f "$MODEL_FILE" ]; then
    echo ""
    echo "▸ Downloading large-v3-turbo model (~1.6 GB)..."
    echo "  (This model excels at technical/programming vocabulary)"
    bash whisper.cpp/models/download-ggml-model.sh large-v3-turbo
    mv whisper.cpp/models/ggml-large-v3-turbo.bin models/
    echo "✓ Model downloaded"
else
    echo "✓ Model already downloaded"
fi

# ── 3. Python venv + dependencies ──
echo ""
if [ ! -d ".venv" ]; then
    echo "▸ Creating Python virtual environment..."
    python3 -m venv .venv
fi
echo "▸ Installing Python dependencies..."
.venv/bin/pip install --quiet sounddevice numpy pynput
echo "✓ Python dependencies installed"

# ── 4. Permissions reminder ──
echo ""
echo "═══════════════════════════════════════════"
echo "  Setup complete!"
echo "═══════════════════════════════════════════"
echo ""
echo "  macOS permissions needed (will prompt on first run):"
echo "    • Accessibility — for global hotkey + paste"
echo "    • Microphone    — for audio capture"
echo ""
echo "  Grant these in: System Settings → Privacy & Security"
echo ""
echo "  Run with:  ./run.sh"
echo "═══════════════════════════════════════════"
