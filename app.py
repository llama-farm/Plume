#!/usr/bin/env python3
"""
Plume — macOS menu bar app for local speech-to-text.

Configurable hotkey to start/stop recording. Transcribes with whisper.cpp
(large-v3-turbo model) and pastes text at cursor position.
"""

import fcntl
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave

import numpy as np
import sounddevice as sd
import objc
import Quartz

import settings as cfg

# ── Path resolution ───────────────────────────────────────────

_FROZEN = getattr(sys, "frozen", False)

if _FROZEN:
    # PyInstaller .app bundle: resources are in Contents/Resources
    _BUNDLE_DIR = os.path.normpath(
        os.path.join(os.path.dirname(sys.executable), "..", "Resources")
    )
else:
    _BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))

_DATA_DIR = os.path.expanduser("~/Library/Application Support/Plume")
os.makedirs(_DATA_DIR, exist_ok=True)

# ── Configuration ──────────────────────────────────────────────

MODEL_DIR = os.path.join(_DATA_DIR, "models")
MODEL_PATH = os.path.join(MODEL_DIR, "ggml-large-v3-turbo.bin")
MODEL_URL = (
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
    "ggml-large-v3-turbo.bin"
)

WHISPER_SAMPLE_RATE = 16000
CHANNELS = 1
DEBOUNCE_SECS = 0.5

def _get_native_sample_rate():
    """Query the current default input device's sample rate."""
    info = sd.query_devices(kind="input")
    return int(info["default_samplerate"])


def _find_whisper_bin():
    """Locate whisper-cli binary in bundle or dev tree."""
    candidates = []
    if _FROZEN:
        candidates.append(os.path.join(_BUNDLE_DIR, "whisper-cli"))
    candidates.extend([
        os.path.join(_BUNDLE_DIR, "whisper.cpp", "build", "bin", "whisper-cli"),
        os.path.join(_BUNDLE_DIR, "whisper.cpp", "build", "bin", "main"),
    ])
    return next((p for p in candidates if os.path.isfile(p)), None)


WHISPER_BIN = _find_whisper_bin()


def _resample(audio: np.ndarray, orig_rate: int, target_rate: int) -> np.ndarray:
    if orig_rate == target_rate:
        return audio
    duration = len(audio) / orig_rate
    target_len = int(duration * target_rate)
    indices = np.linspace(0, len(audio) - 1, target_len)
    return np.interp(indices, np.arange(len(audio)), audio.flatten()).astype(
        np.float32
    ).reshape(-1, 1)


# ── Model download ────────────────────────────────────────────

def _download_model(progress_cb=None):
    """Download the whisper model to MODEL_PATH. Calls progress_cb(percent)."""
    os.makedirs(MODEL_DIR, exist_ok=True)
    tmp_path = MODEL_PATH + ".download"

    try:
        req = urllib.request.Request(MODEL_URL, headers={"User-Agent": "Plume/1.0"})
        with urllib.request.urlopen(req) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 1024)  # 1 MB chunks
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb and total > 0:
                        progress_cb(int(downloaded * 100 / total))

        os.rename(tmp_path, MODEL_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── Transcription engine ──────────────────────────────────────

def run_whisper(wav_path: str) -> str:
    out_base = wav_path.rsplit(".", 1)[0]
    out_txt = out_base + ".txt"

    try:
        subprocess.run(
            [
                WHISPER_BIN,
                "-m", MODEL_PATH,
                "-f", wav_path,
                "--no-timestamps",
                "-t", str(min(os.cpu_count() or 4, 8)),
                "-l", "en",
                "-otxt",
                "-of", out_base,
            ],
            capture_output=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return ""

    if not os.path.isfile(out_txt):
        return ""

    try:
        text = open(out_txt).read().strip()
    finally:
        try:
            os.unlink(out_txt)
        except OSError:
            pass

    for artifact in ["[BLANK_AUDIO]", "(silence)", "[silence]"]:
        text = text.replace(artifact, "")
    return text.strip()


def copy_to_clipboard(text: str):
    proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
    proc.communicate(text.encode("utf-8"))


def simulate_paste() -> bool:
    time.sleep(0.05)
    result = subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to keystroke "v" using command down'],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def play_sound(name: str):
    path = f"/System/Library/Sounds/{name}.aiff"
    if os.path.exists(path):
        subprocess.Popen(["afplay", path],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ── Menu bar app ──────────────────────────────────────────────

import rumps

ICON_IDLE = os.path.join(_BUNDLE_DIR, "icons", "menubar.png")
ICON_REC = os.path.join(_BUNDLE_DIR, "icons", "menubar-rec.png")


class PlumeApp(rumps.App):
    def __init__(self):
        super().__init__(
            name="Plume",
            icon=ICON_IDLE,
            template=True,
            quit_button=None,
        )

        self._settings = cfg.load()
        self.recording = False
        self.audio_data: list[np.ndarray] = []
        self.stream: sd.InputStream | None = None
        self.lock = threading.Lock()
        self.last_toggle_time = 0.0
        self._tap = None
        self._tap_source = None
        self._tap_loop = None
        self._tap_thread = None
        self._settings_ctrl = None
        self._native_rate = _get_native_sample_rate()
        self._model_ready = os.path.isfile(MODEL_PATH)

        hotkey_str = cfg.format_hotkey(
            self._settings["hotkey_modifier_flags"],
            self._settings["hotkey_keycode"],
        )

        if self._model_ready:
            status_text = f"Ready — {hotkey_str} to dictate"
        else:
            status_text = "Downloading speech model..."

        self.status_item = rumps.MenuItem(status_text)
        self.status_item.set_callback(None)

        self.menu = [
            self.status_item,
            None,
            rumps.MenuItem("Settings...", callback=self._open_settings),
            None,
            rumps.MenuItem("Quit Plume", callback=self._quit),
        ]

        self._start_hotkey_listener()

        if not self._model_ready:
            threading.Thread(target=self._download_model_bg, daemon=True).start()

    # ── Model download ──

    def _download_model_bg(self):
        def on_progress(pct):
            self.status_item.title = f"Downloading speech model... {pct}%"

        try:
            _download_model(progress_cb=on_progress)
            self._model_ready = True
            self._set_idle("Ready")
            rumps.notification(
                title="Plume",
                subtitle="Speech model downloaded",
                message="You're all set — use your hotkey to start dictating.",
            )
        except Exception as e:
            self.status_item.title = "Model download failed"
            rumps.notification(
                title="Plume",
                subtitle="Model download failed",
                message=str(e)[:200],
            )

    # ── Settings ──

    def _open_settings(self, _):
        self._settings_ctrl = cfg.SettingsWindowController.alloc().initWithCallback_(
            self._on_settings_saved
        )
        self._settings_ctrl.show()

    @objc.python_method
    def _on_settings_saved(self, new_settings):
        self._settings = new_settings
        self._restart_hotkey_listener()
        self._set_idle("Ready")

    # ── Hotkey listener (Quartz CGEventTap — suppresses hotkey from reaching apps) ──

    @objc.python_method
    def _start_hotkey_listener(self):
        target_keycode = self._settings["hotkey_keycode"]
        target_flags = self._settings["hotkey_modifier_flags"]
        app = self
        suppressed_keydown = [False]

        MOD_MASK = (
            Quartz.kCGEventFlagMaskControl
            | Quartz.kCGEventFlagMaskAlternate
            | Quartz.kCGEventFlagMaskShift
            | Quartz.kCGEventFlagMaskCommand
        )

        local_tap = [None]

        def tap_callback(proxy, event_type, event, refcon):
            if event_type not in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp):
                tap = local_tap[0]
                if tap is not None:
                    try:
                        if not Quartz.CGEventTapIsEnabled(tap):
                            Quartz.CGEventTapEnable(tap, True)
                    except Exception:
                        pass
                return event

            keycode = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode
            )
            flags = Quartz.CGEventGetFlags(event) & MOD_MASK

            if event_type == Quartz.kCGEventKeyDown:
                if keycode == target_keycode and flags == target_flags:
                    suppressed_keydown[0] = True
                    now = time.time()
                    if now - app.last_toggle_time >= DEBOUNCE_SECS:
                        app.last_toggle_time = now
                        threading.Thread(target=app._toggle, daemon=True).start()
                    return None

            elif event_type == Quartz.kCGEventKeyUp:
                if keycode == target_keycode and suppressed_keydown[0]:
                    suppressed_keydown[0] = False
                    return None

            return event

        mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
        )

        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,
            mask,
            tap_callback,
            None,
        )

        if tap is None:
            rumps.notification(
                title="Plume",
                subtitle="Cannot register hotkey",
                message="Grant Accessibility permission in System Settings",
            )
            return

        local_tap[0] = tap
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)

        self._tap = tap
        self._tap_source = source

        ready = threading.Event()

        def run_tap():
            loop = Quartz.CFRunLoopGetCurrent()
            self._tap_loop = loop
            Quartz.CFRunLoopAddSource(loop, source, Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, True)
            ready.set()
            Quartz.CFRunLoopRun()
            local_tap[0] = None

        t = threading.Thread(target=run_tap, daemon=True)
        t.start()
        self._tap_thread = t
        ready.wait(timeout=2.0)

    @objc.python_method
    def _stop_hotkey_listener(self):
        if self._tap is not None:
            try:
                Quartz.CGEventTapEnable(self._tap, False)
            except Exception:
                pass

        if self._tap_loop is not None:
            Quartz.CFRunLoopStop(self._tap_loop)

        if self._tap_thread is not None:
            self._tap_thread.join(timeout=2.0)
            self._tap_thread = None

        self._tap_loop = None
        self._tap_source = None
        self._tap = None

    @objc.python_method
    def _restart_hotkey_listener(self):
        self._stop_hotkey_listener()
        self._start_hotkey_listener()

    # ── Recording toggle ──

    def _toggle(self):
        if not self._model_ready:
            return
        with self.lock:
            if self.recording:
                self._stop_and_transcribe()
            else:
                self._start_recording()

    def _start_recording(self):
        self.audio_data = []
        try:
            native_rate = _get_native_sample_rate()
            self.stream = sd.InputStream(
                samplerate=native_rate,
                channels=CHANNELS,
                dtype="float32",
                callback=self._audio_cb,
            )
            self.stream.start()
        except Exception as e:
            print(f"[audio] failed to open mic: {e}", flush=True)
            self._set_idle("Mic unavailable")
            rumps.notification(
                title="Plume",
                subtitle="Cannot open microphone",
                message=str(e),
            )
            return
        self._native_rate = native_rate
        self.recording = True
        if self._settings.get("sound_effects", True):
            play_sound("Tink")
        self.icon = ICON_REC
        hotkey_str = cfg.format_hotkey(
            self._settings["hotkey_modifier_flags"],
            self._settings["hotkey_keycode"],
        )
        self.status_item.title = f"Recording... {hotkey_str} to stop"

    def _audio_cb(self, indata, frames, time_info, status):
        self.audio_data.append(indata.copy())

    def _stop_and_transcribe(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self.recording = False
        self.status_item.title = "Transcribing..."

        if not self.audio_data:
            self._set_idle("No audio captured")
            return

        audio = np.concatenate(self.audio_data, axis=0)
        if len(audio) < self._native_rate * 0.3:
            self._set_idle("Too short")
            return

        audio = _resample(audio, self._native_rate, WHISPER_SAMPLE_RATE)

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            with wave.open(tmp.name, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)
                wf.setframerate(WHISPER_SAMPLE_RATE)
                wf.writeframes((audio * 32767).astype(np.int16).tobytes())

            text = run_whisper(tmp.name)
            if text:
                s = self._settings
                pasted = False

                if s.get("copy_to_clipboard", True):
                    copy_to_clipboard(text)

                if s.get("auto_paste", True):
                    if not s.get("copy_to_clipboard", True):
                        copy_to_clipboard(text)
                    pasted = simulate_paste()
                    if not pasted:
                        rumps.notification(
                            title="Plume — Paste Failed",
                            subtitle="Text is on your clipboard (Cmd+V)",
                            message="Enable Accessibility: System Settings → "
                                    "Privacy & Security → Accessibility → Plume",
                        )

                if self._settings.get("sound_effects", True):
                    play_sound("Pop")

                display = text if len(text) <= 60 else text[:57] + "..."
                self._set_idle(f"Last: {display}")
            else:
                self._set_idle("No speech detected")
        finally:
            os.unlink(tmp.name)

    def _set_idle(self, detail: str = "Ready"):
        self.icon = ICON_IDLE
        hotkey_str = cfg.format_hotkey(
            self._settings["hotkey_modifier_flags"],
            self._settings["hotkey_keycode"],
        )
        self.status_item.title = f"{detail} — {hotkey_str} to dictate"

    def _quit(self, _):
        rumps.quit_application()


# ── Entry point ────────────────────────────────────────────────

_lock_fp = None


def _ensure_single_instance():
    global _lock_fp
    lock_path = os.path.join(tempfile.gettempdir(), "plume.lock")
    _lock_fp = open(lock_path, "w")
    try:
        fcntl.flock(_lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fp.write(str(os.getpid()))
        _lock_fp.flush()
    except IOError:
        rumps.alert(
            title="Plume",
            message="Plume is already running.\n"
                    "Check your menu bar for the waveform icon.",
        )
        sys.exit(0)


def main():
    _ensure_single_instance()

    if not WHISPER_BIN:
        rumps.alert(
            title="Plume",
            message="whisper-cli not found.\n"
                    "Run: bash setup.sh" if not _FROZEN else
                    "The app bundle is incomplete — please re-download.",
        )
        sys.exit(1)

    PlumeApp().run()


if __name__ == "__main__":
    main()
