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
from AppKit import (
    NSBackingStoreBuffered, NSColor, NSMakeRect,
    NSObject as _NSObject, NSPasteboard, NSScreen, NSSound, NSView, NSWindow,
)

import settings as cfg
import onboarding

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
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, "public.utf8-plain-text")


def simulate_paste() -> bool:
    time.sleep(0.05)
    try:
        down = Quartz.CGEventCreateKeyboardEvent(None, 9, True)  # 9 = 'V'
        Quartz.CGEventSetFlags(down, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        up = Quartz.CGEventCreateKeyboardEvent(None, 9, False)
        Quartz.CGEventSetFlags(up, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
        return True
    except Exception:
        return False


def type_text(text):
    """Type text at cursor position character by character via CGEvents."""
    for char in text:
        down = Quartz.CGEventCreateKeyboardEvent(None, 0, True)
        Quartz.CGEventKeyboardSetUnicodeString(down, len(char), char)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        up = Quartz.CGEventCreateKeyboardEvent(None, 0, False)
        Quartz.CGEventKeyboardSetUnicodeString(up, len(char), char)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
        time.sleep(0.012)


def play_sound(name: str):
    path = f"/System/Library/Sounds/{name}.aiff"
    if os.path.exists(path):
        sound = NSSound.alloc().initWithContentsOfFile_byReference_(path, True)
        if sound:
            sound.play()


# ── Menu bar app ──────────────────────────────────────────────

import rumps

ICON_IDLE = os.path.join(_BUNDLE_DIR, "icons", "menubar.png")
ICON_REC = os.path.join(_BUNDLE_DIR, "icons", "menubar-rec.png")


class _RecordingGlow(_NSObject):
    """Audio-reactive blue glow at the top of the screen while recording."""

    # Audio pipeline constants
    NOISE_FLOOR = 0.008       # RMS below this is silence / room noise
    ATTACK_COEFF = 0.4        # lerp toward target when rising (fast attack)
    RELEASE_COEFF = 0.06      # lerp toward target when falling (slow release)

    def init(self):
        self = objc.super(_RecordingGlow, self).init()
        if self is None:
            return None
        self._window = None
        self._base_layer = None
        self._center_layer = None
        self._timer = None
        self._rms_accum = 0.0   # sum-of-squares accumulator
        self._rms_count = 0     # sample count
        self._smoothed = 0.0    # current display value (0-1)
        return self

    @objc.python_method
    def _ensure_window(self):
        if self._window is not None:
            return

        CAGradientLayer = objc.lookUpClass("CAGradientLayer")
        CABasicAnimation = objc.lookUpClass("CABasicAnimation")

        screen = NSScreen.mainScreen().frame()
        glow_h = 64
        x = screen.origin.x
        y = screen.origin.y + screen.size.height - glow_h
        w = screen.size.width

        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, w, glow_h), 0, NSBackingStoreBuffered, False,
        )
        win.setLevel_(26)
        win.setOpaque_(False)
        win.setBackgroundColor_(NSColor.clearColor())
        win.setIgnoresMouseEvents_(True)
        win.setHidesOnDeactivate_(False)
        win.setHasShadow_(False)
        win.setCollectionBehavior_(1 | 16)  # allSpaces | stationary

        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, w, glow_h))
        view.setWantsLayer_(True)

        # Base ambient glow — full width, gentle breathing pulse
        base = CAGradientLayer.layer()
        base.setFrame_(((0, 0), (w, glow_h)))
        base.setColors_([
            Quartz.CGColorCreateGenericRGB(0.25, 0.55, 1.0, 0.6),
            Quartz.CGColorCreateGenericRGB(0.25, 0.55, 1.0, 0.0),
        ])
        base.setStartPoint_((0.5, 1.0))
        base.setEndPoint_((0.5, 0.0))
        view.layer().addSublayer_(base)
        self._base_layer = base

        # Center glow — audio-reactive, tall + bright in the middle
        center = CAGradientLayer.layer()
        center.setFrame_(((0, 0), (w, glow_h)))
        center.setColors_([
            Quartz.CGColorCreateGenericRGB(0.4, 0.7, 1.0, 1.0),
            Quartz.CGColorCreateGenericRGB(0.35, 0.65, 1.0, 0.4),
            Quartz.CGColorCreateGenericRGB(0.3, 0.6, 1.0, 0.0),
        ])
        center.setLocations_([0.0, 0.4, 1.0])
        center.setStartPoint_((0.5, 1.0))
        center.setEndPoint_((0.5, 0.0))
        center.setOpacity_(0.0)

        mask = CAGradientLayer.layer()
        mask.setFrame_(((0, 0), (w, glow_h)))
        mask.setColors_([
            Quartz.CGColorCreateGenericGray(0, 0.0),
            Quartz.CGColorCreateGenericGray(0, 1.0),
            Quartz.CGColorCreateGenericGray(0, 0.0),
        ])
        mask.setLocations_([0.15, 0.5, 0.85])
        mask.setStartPoint_((0, 0.5))
        mask.setEndPoint_((1, 0.5))
        center.setMask_(mask)

        view.layer().addSublayer_(center)
        win.contentView().addSubview_(view)

        self._window = win
        self._center_layer = center

    # ── ObjC methods (main thread) ──

    def showGlow_(self, _):
        self._ensure_window()
        self._smoothed = 0.0
        self._rms_accum = 0.0
        self._rms_count = 0
        self._window.orderFront_(None)
        NSTimer = objc.lookUpClass("NSTimer")
        self._timer = (
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1.0 / 60, self, "updateGlow:", None, True,
            )
        )

    def hideGlow_(self, _):
        if self._timer:
            self._timer.invalidate()
            self._timer = None
        if self._center_layer:
            self._center_layer.setOpacity_(0.0)
        if self._window:
            self._window.orderOut_(None)

    def updateGlow_(self, timer):
        # ── 1. RMS amplitude from accumulated samples ──
        count = self._rms_count
        if count > 0:
            rms = (self._rms_accum / count) ** 0.5
            self._rms_accum = 0.0
            self._rms_count = 0
        else:
            rms = 0.0

        # ── 2. Noise gate — below floor = dead zero ──
        if rms < self.NOISE_FLOOR:
            rms = 0.0
        else:
            # Subtract floor so the range starts at 0 for just-above-noise
            rms = rms - self.NOISE_FLOOR

        # ── 3. Dynamic mapping — compress low end, excite high end ──
        #   sqrt compresses quiet speech upward;
        #   then square the result to give loud speech a super-linear kick
        normalized = min(1.0, (rms ** 0.5) * 16)
        target = normalized * (0.5 + 0.5 * normalized)  # quadratic boost at top

        # ── 4. Asymmetric lerp — fast attack, slow release ──
        if target > self._smoothed:
            alpha = self.ATTACK_COEFF
        else:
            alpha = self.RELEASE_COEFF
        self._smoothed += alpha * (target - self._smoothed)

        # Snap to zero when very low to avoid lingering ghost glow
        if self._smoothed < 0.01:
            self._smoothed = 0.0

        # ── 5. Map to visual: base + center opacity, vertical spread ──
        v = self._smoothed

        # Base layer: dim idle state, brightens with voice
        if self._base_layer:
            self._base_layer.setOpacity_(0.15 + v * 0.85)

        if self._center_layer:
            self._center_layer.setOpacity_(v)
            # Opaque band stretches down when loud
            mid = 0.2 + v * 0.45  # 0.2 quiet → 0.65 loud
            self._center_layer.setLocations_([0.0, mid, 1.0])

    # ── Thread-safe Python wrappers ──

    @objc.python_method
    def show(self):
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "showGlow:", None, False,
        )

    @objc.python_method
    def hide(self):
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "hideGlow:", None, False,
        )

    @objc.python_method
    def feed_samples(self, samples):
        """Accumulate raw samples for RMS calculation. Called from audio thread."""
        self._rms_accum += float(np.sum(samples ** 2))
        self._rms_count += len(samples)


class PlumeApp(rumps.App):
    def __init__(self):
        super().__init__(
            name="Plume - AI Dictation",
            icon=ICON_IDLE,
            template=True,
            quit_button=None,
        )

        # Snapshot before load() which may migrate legacy settings into place
        self._is_fresh_install = not os.path.isfile(cfg.SETTINGS_FILE) and not os.path.isfile(cfg._LEGACY_SETTINGS_FILE)
        self._settings = cfg.load()
        self.recording = False
        self.audio_data: list[np.ndarray] = []
        self.stream: sd.InputStream | None = None
        self.lock = threading.Lock()
        self.last_toggle_time = 0.0
        self._live_stop = None
        self._live_thread = None
        self._live_fully_typed = False
        self._glow = _RecordingGlow.alloc().init()
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

        self._onboarding_ctrl = None

        self.menu = [
            self.status_item,
            None,
            rumps.MenuItem("Settings...", callback=self._open_settings),
            None,
            rumps.MenuItem("Quit Plume", callback=self._quit),
        ]

        # Onboarding gate: show wizard on first launch, skip for existing users
        if not self._settings.get("onboarding_complete", False):
            if not self._is_fresh_install:
                # Existing user upgrading — silently mark complete
                self._settings["onboarding_complete"] = True
                cfg.save(self._settings)
                self._finish_setup()
            else:
                # Defer show to after the run loop starts via a one-shot rumps.Timer
                self._onboarding_timer = rumps.Timer(self._deferred_show_onboarding, 0.5)
                self._onboarding_timer.start()
        else:
            self._finish_setup()

    # ── Onboarding ──

    @objc.python_method
    def _deferred_show_onboarding(self, _timer):
        """One-shot timer callback: create and show the onboarding wizard."""
        self._onboarding_timer.stop()
        self._onboarding_ctrl = (
            onboarding.OnboardingWindowController.alloc()
            .initWithCallback_(self._on_onboarding_complete)
        )
        self._onboarding_ctrl.show()

    @objc.python_method
    def _on_onboarding_complete(self):
        self._settings = cfg.load()
        self._finish_setup()

    @objc.python_method
    def _finish_setup(self):
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
                title="Plume - AI Dictation",
                subtitle="Speech model downloaded",
                message="You're all set — use your hotkey to start dictating.",
            )
        except Exception as e:
            self.status_item.title = "Model download failed"
            rumps.notification(
                title="Plume - AI Dictation",
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
                title="Plume - AI Dictation",
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
        self._live_stop = threading.Event()
        self._live_thread = None
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
                title="Plume - AI Dictation",
                subtitle="Cannot open microphone",
                message=str(e),
            )
            return
        self._native_rate = native_rate
        self.recording = True
        if self._settings.get("sound_effects", True):
            play_sound("Tink")
        self.icon = ICON_REC
        if self._settings.get("recording_glow", True):
            self._glow.show()
        hotkey_str = cfg.format_hotkey(
            self._settings["hotkey_modifier_flags"],
            self._settings["hotkey_keycode"],
        )
        self.status_item.title = f"Recording... {hotkey_str} to stop"
        self._live_fully_typed = False
        if self._settings.get("live_transcription", False):
            self._live_thread = threading.Thread(
                target=self._live_output_loop, daemon=True
            )
            self._live_thread.start()

    def _audio_cb(self, indata, frames, time_info, status):
        self.audio_data.append(indata.copy())
        self._glow.feed_samples(indata)

    def _live_output_loop(self):
        """Transcribe audio in chunks and paste each one at the cursor."""
        interval = 3.0
        chunk_start = 0
        first_chunk = True

        while not self._live_stop.wait(interval):
            current_len = len(self.audio_data)
            if current_len <= chunk_start:
                continue
            chunk = self.audio_data[chunk_start:current_len]
            chunk_start = current_len
            text = self._transcribe_chunk(chunk)
            if text:
                if not first_chunk:
                    type_text(" ")
                type_text(text)
                first_chunk = False

        # Final chunk: transcribe remaining audio after stop
        if len(self.audio_data) > chunk_start:
            chunk = self.audio_data[chunk_start:]
            text = self._transcribe_chunk(chunk)
            if text:
                if not first_chunk:
                    type_text(" ")
                type_text(text)
        self._live_fully_typed = True

    def _transcribe_chunk(self, chunk):
        """Transcribe a list of audio buffers. Returns text or empty string."""
        audio = np.concatenate(chunk, axis=0)
        if len(audio) < self._native_rate * 0.3:
            return ""
        audio = _resample(audio, self._native_rate, WHISPER_SAMPLE_RATE)
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            with wave.open(tmp.name, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)
                wf.setframerate(WHISPER_SAMPLE_RATE)
                wf.writeframes((audio * 32767).astype(np.int16).tobytes())
            return run_whisper(tmp.name)
        except Exception:
            return ""
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    def _stop_and_transcribe(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self.recording = False
        # Stop live output thread
        if self._live_stop:
            self._live_stop.set()
        if self._live_thread:
            self._live_thread.join(timeout=15)
            self._live_thread = None

        # If the live thread already typed everything, we're done
        if self._live_fully_typed:
            self._live_fully_typed = False
            if self._settings.get("sound_effects", True):
                play_sound("Pop")
            self._set_idle("Dictated")
            return

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
                            title="Plume - AI Dictation",
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
        self._glow.hide()
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
