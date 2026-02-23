# Plume

Local speech-to-text for macOS. Lives in your menu bar, transcribes with [whisper.cpp](https://github.com/ggerganov/whisper.cpp), and pastes text at your cursor.

All processing happens on-device — nothing is sent to the cloud.

## Features

- **Global hotkey** (default: `Ctrl+Escape`) to start/stop recording
- **Auto-paste** transcribed text at cursor position
- **Menu bar app** — runs quietly in the background
- **GPU-accelerated** transcription via Metal
- **Configurable** — change hotkey, toggle clipboard/paste/sounds in Settings
- **Auto-downloads** the whisper large-v3-turbo model on first launch (~1.6 GB)

## Install

### From GitHub Releases (recommended)

1. Download `Plume-x.x.x-macOS-arm64.zip` from [Releases](../../releases/latest)
2. Unzip and drag **Plume.app** to `/Applications`
3. Launch Plume — it will download the speech model on first run
4. Grant **Accessibility** and **Microphone** permissions when prompted

> **Note:** Plume is ad-hoc signed (not notarized). On first launch you may need to right-click → Open, or allow it in System Settings → Privacy & Security.

### Build from source

Requires macOS 13+, Xcode command-line tools, Python 3.11+, and CMake.

```bash
# Clone the repo
git clone https://github.com/llama-farm/Plume.git
cd Plume

# Set up whisper.cpp, model, and Python dependencies
bash setup.sh

# Run directly (development)
.venv/bin/python3 app.py

# Or build a distributable .app
bash build-release.sh 1.0.0
# Output: dist/Plume-1.0.0-macOS-arm64.zip
```

## Permissions

Plume needs two macOS permissions (prompted automatically):

| Permission | Why |
|---|---|
| **Microphone** | To capture audio for transcription |
| **Accessibility** | To register the global hotkey and paste text |

Grant these in **System Settings → Privacy & Security**.

## How it works

1. Press the hotkey → Plume starts recording from your microphone
2. Press the hotkey again → recording stops, audio is sent to whisper.cpp
3. Transcribed text is copied to clipboard and pasted at your cursor

The app uses the `large-v3-turbo` Whisper model for fast, accurate transcription with strong technical vocabulary support. Transcription runs locally via whisper.cpp with Metal GPU acceleration.

## Settings

Click the Plume menu bar icon → **Settings** to configure:

- **Hotkey** — click "Record Hotkey" and press your preferred key combination
- **Auto-paste at cursor** — automatically paste after transcription
- **Copy to clipboard** — copy transcribed text to clipboard
- **Sound effects** — audio feedback for start/stop recording

Settings are stored in `~/.config/plume/settings.json`.

## Architecture

| Component | Role |
|---|---|
| `app.py` | Main app — menu bar UI, recording, transcription orchestration |
| `settings.py` | Settings persistence + native macOS settings window (AppKit) |
| `whisper.cpp/` | Speech-to-text engine (built from source with Metal) |
| `icons/` | Menu bar icons (idle + recording states) |

## License

MIT
