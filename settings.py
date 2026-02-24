"""
Plume settings — persistence + native macOS settings window.
"""

import json
import os
import shutil
import threading

import objc
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSBox,
    NSButton,
    NSColor,
    NSControlStateValueOff,
    NSControlStateValueOn,
    NSEvent,
    NSEventMaskKeyDown,
    NSFont,
    NSMakeRect,
    NSObject,
    NSOnState,
    NSOffState,
    NSSwitchButton,
    NSTextField,
    NSView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)

# ── macOS key code names ──────────────────────────────────────

KEYCODE_NAMES = {
    0x00: "A", 0x0B: "B", 0x08: "C", 0x02: "D", 0x0E: "E",
    0x03: "F", 0x05: "G", 0x04: "H", 0x22: "I", 0x26: "J",
    0x28: "K", 0x25: "L", 0x2E: "M", 0x2D: "N", 0x1F: "O",
    0x23: "P", 0x0C: "Q", 0x0F: "R", 0x01: "S", 0x11: "T",
    0x20: "U", 0x09: "V", 0x0D: "W", 0x07: "X", 0x10: "Y",
    0x06: "Z",
    0x12: "1", 0x13: "2", 0x14: "3", 0x15: "4", 0x17: "5",
    0x16: "6", 0x1A: "7", 0x1C: "8", 0x19: "9", 0x1D: "0",
    0x24: "Return", 0x30: "Tab", 0x31: "Space", 0x33: "Delete",
    0x35: "Escape", 0x75: "Fwd Delete",
    0x7A: "F1", 0x78: "F2", 0x63: "F3", 0x76: "F4",
    0x60: "F5", 0x61: "F6", 0x62: "F7", 0x64: "F8",
    0x65: "F9", 0x6D: "F10", 0x67: "F11", 0x6F: "F12",
    0x7E: "Up", 0x7D: "Down", 0x7B: "Left", 0x7C: "Right",
    0x18: "=", 0x1B: "-", 0x1E: "]", 0x21: "[",
    0x27: "'", 0x29: ";", 0x2A: "\\", 0x2B: ",",
    0x2C: "/", 0x2F: ".", 0x32: "`",
}

MOD_CTRL = 1 << 18
MOD_OPT = 1 << 19
MOD_SHIFT = 1 << 17
MOD_CMD = 1 << 20
MOD_MASK = MOD_CTRL | MOD_OPT | MOD_SHIFT | MOD_CMD


def format_hotkey(flags, keycode):
    """Human-readable hotkey string, e.g. '⌃ Escape'."""
    parts = []
    if flags & MOD_CTRL:
        parts.append("⌃")
    if flags & MOD_OPT:
        parts.append("⌥")
    if flags & MOD_SHIFT:
        parts.append("⇧")
    if flags & MOD_CMD:
        parts.append("⌘")
    parts.append(KEYCODE_NAMES.get(keycode, f"Key({keycode})"))
    return " ".join(parts)


# ── pynput conversion ────────────────────────────────────────

def hotkey_to_pynput(flags, keycode):
    """Convert macOS modifier flags + keycode to pynput objects.

    Returns (modifier_key_set, trigger_key).
    """
    from pynput import keyboard

    _special = {
        0x35: keyboard.Key.esc, 0x31: keyboard.Key.space,
        0x30: keyboard.Key.tab, 0x24: keyboard.Key.enter,
        0x33: keyboard.Key.backspace, 0x75: keyboard.Key.delete,
        0x7A: keyboard.Key.f1, 0x78: keyboard.Key.f2,
        0x63: keyboard.Key.f3, 0x76: keyboard.Key.f4,
        0x60: keyboard.Key.f5, 0x61: keyboard.Key.f6,
        0x62: keyboard.Key.f7, 0x64: keyboard.Key.f8,
        0x65: keyboard.Key.f9, 0x6D: keyboard.Key.f10,
        0x67: keyboard.Key.f11, 0x6F: keyboard.Key.f12,
        0x7E: keyboard.Key.up, 0x7D: keyboard.Key.down,
        0x7B: keyboard.Key.left, 0x7C: keyboard.Key.right,
    }

    # Trigger key
    if keycode in _special:
        trigger = _special[keycode]
    else:
        name = KEYCODE_NAMES.get(keycode, "")
        trigger = keyboard.KeyCode.from_char(name.lower()) if len(name) == 1 else None

    # Modifier keys
    mod_keys = set()
    if flags & MOD_CTRL:
        mod_keys.update({keyboard.Key.ctrl_l, keyboard.Key.ctrl_r})
    if flags & MOD_OPT:
        mod_keys.update({keyboard.Key.alt_l, keyboard.Key.alt_r})
    if flags & MOD_SHIFT:
        mod_keys.update({keyboard.Key.shift_l, keyboard.Key.shift_r})
    if flags & MOD_CMD:
        mod_keys.update({keyboard.Key.cmd_l, keyboard.Key.cmd_r})

    return mod_keys, trigger


# ── Settings persistence ──────────────────────────────────────

SETTINGS_DIR = os.path.expanduser("~/Library/Application Support/Plume")
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")

_LEGACY_SETTINGS_FILE = os.path.expanduser("~/.config/plume/settings.json")

DEFAULTS = {
    "hotkey_keycode": 0x35,      # Escape
    "hotkey_modifier_flags": MOD_CTRL,  # Control
    "auto_paste": True,
    "copy_to_clipboard": True,
    "sound_effects": True,
    "live_transcription": False,
    "recording_glow": True,
    "onboarding_complete": False,
}


def load():
    """Load settings from disk, merged with defaults."""
    # One-time migration from legacy path
    if not os.path.isfile(SETTINGS_FILE) and os.path.isfile(_LEGACY_SETTINGS_FILE):
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        shutil.copy2(_LEGACY_SETTINGS_FILE, SETTINGS_FILE)

    if os.path.isfile(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE) as f:
                return {**DEFAULTS, **json.load(f)}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULTS)


def save(settings):
    """Persist settings to disk."""
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)


# ── Native settings window ───────────────────────────────────

W = 400
H = 394
PAD = 24
LABEL_X = PAD
CTRL_X = PAD
ROW_H = 32


def _label(text, frame, bold=False):
    tf = NSTextField.alloc().initWithFrame_(frame)
    tf.setStringValue_(text)
    tf.setBezeled_(False)
    tf.setDrawsBackground_(False)
    tf.setEditable_(False)
    tf.setSelectable_(False)
    if bold:
        tf.setFont_(NSFont.boldSystemFontOfSize_(13))
    else:
        tf.setFont_(NSFont.systemFontOfSize_(13))
    return tf


def _separator(y, width):
    box = NSBox.alloc().initWithFrame_(NSMakeRect(PAD, y, width - 2 * PAD, 1))
    box.setBoxType_(2)  # NSBoxSeparator
    return box


class SettingsWindowController(NSObject):
    """Creates and manages the Plume settings window."""

    window = objc.ivar()

    def initWithCallback_(self, on_save):
        self = objc.super(SettingsWindowController, self).init()
        if self is None:
            return None
        self._on_save = on_save
        self._recording = False
        self._monitor = None
        self._settings = load()
        self._pending_keycode = self._settings["hotkey_keycode"]
        self._pending_flags = self._settings["hotkey_modifier_flags"]
        self._build_window()
        return self

    @objc.python_method
    def _build_window(self):
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H), style, NSBackingStoreBuffered, False,
        )
        self.window.setTitle_("Plume Settings")
        self.window.setReleasedWhenClosed_(False)
        self.window.center()

        cv = self.window.contentView()
        y = H - PAD  # current y position (top-down)

        # ── Hotkey section ──
        y -= 20
        cv.addSubview_(_label("Hotkey", NSMakeRect(LABEL_X, y, 200, 20), bold=True))

        y -= ROW_H + 4
        self._hotkey_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(CTRL_X, y, 200, 28)
        )
        self._hotkey_field.setStringValue_(
            format_hotkey(self._pending_flags, self._pending_keycode)
        )
        self._hotkey_field.setEditable_(False)
        self._hotkey_field.setSelectable_(False)
        self._hotkey_field.setAlignment_(1)  # center
        self._hotkey_field.setFont_(NSFont.monospacedSystemFontOfSize_weight_(13, 0.0))
        cv.addSubview_(self._hotkey_field)

        self._record_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(CTRL_X + 210, y, 130, 28)
        )
        self._record_btn.setTitle_("Record Hotkey")
        self._record_btn.setBezelStyle_(NSBezelStyleRounded)
        self._record_btn.setTarget_(self)
        self._record_btn.setAction_(objc.selector(self.recordClicked_, signature=b"v@:@"))
        cv.addSubview_(self._record_btn)

        # ── Separator ──
        y -= 20
        cv.addSubview_(_separator(y, W))

        # ── Toggles section ──
        y -= 28
        cv.addSubview_(_label("Behavior", NSMakeRect(LABEL_X, y, 200, 20), bold=True))

        y -= ROW_H
        self._auto_paste_cb = self._make_toggle(
            cv, "Auto-paste at cursor", y, self._settings["auto_paste"]
        )

        y -= ROW_H
        self._clipboard_cb = self._make_toggle(
            cv, "Copy to clipboard", y, self._settings["copy_to_clipboard"]
        )

        y -= ROW_H
        self._sounds_cb = self._make_toggle(
            cv, "Sound effects", y, self._settings["sound_effects"]
        )

        y -= ROW_H
        self._live_cb = self._make_toggle(
            cv, "Type live while recording", y,
            self._settings.get("live_transcription", False)
        )

        y -= ROW_H
        self._glow_cb = self._make_toggle(
            cv, "Recording glow indicator", y,
            self._settings.get("recording_glow", True)
        )

        # ── Separator ──
        y -= 20
        cv.addSubview_(_separator(y, W))

        # ── Save / Cancel ──
        btn_y = 16
        cancel = NSButton.alloc().initWithFrame_(NSMakeRect(W - PAD - 170, btn_y, 80, 32))
        cancel.setTitle_("Cancel")
        cancel.setBezelStyle_(NSBezelStyleRounded)
        cancel.setTarget_(self)
        cancel.setAction_(objc.selector(self.cancelClicked_, signature=b"v@:@"))
        cv.addSubview_(cancel)

        save_btn = NSButton.alloc().initWithFrame_(NSMakeRect(W - PAD - 80, btn_y, 80, 32))
        save_btn.setTitle_("Save")
        save_btn.setBezelStyle_(NSBezelStyleRounded)
        save_btn.setKeyEquivalent_("\r")  # Enter = Save
        save_btn.setTarget_(self)
        save_btn.setAction_(objc.selector(self.saveClicked_, signature=b"v@:@"))
        cv.addSubview_(save_btn)

    @objc.python_method
    def _make_toggle(self, parent, title, y, state):
        btn = NSButton.alloc().initWithFrame_(NSMakeRect(CTRL_X, y, W - 2 * PAD, 24))
        btn.setButtonType_(NSSwitchButton)
        btn.setTitle_(title)
        btn.setFont_(NSFont.systemFontOfSize_(13))
        btn.setState_(NSControlStateValueOn if state else NSControlStateValueOff)
        parent.addSubview_(btn)
        return btn

    # ── Actions ──

    def recordClicked_(self, sender):
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def saveClicked_(self, sender):
        self._stop_recording()
        self._settings["hotkey_keycode"] = self._pending_keycode
        self._settings["hotkey_modifier_flags"] = self._pending_flags
        self._settings["auto_paste"] = self._auto_paste_cb.state() == NSControlStateValueOn
        self._settings["copy_to_clipboard"] = self._clipboard_cb.state() == NSControlStateValueOn
        self._settings["sound_effects"] = self._sounds_cb.state() == NSControlStateValueOn
        self._settings["live_transcription"] = self._live_cb.state() == NSControlStateValueOn
        self._settings["recording_glow"] = self._glow_cb.state() == NSControlStateValueOn
        save(self._settings)
        self.window.close()
        if self._on_save:
            self._on_save(self._settings)

    def cancelClicked_(self, sender):
        self._stop_recording()
        self.window.close()

    # ── Hotkey recording ──

    @objc.python_method
    def _start_recording(self):
        self._recording = True
        self._record_btn.setTitle_("Press keys...")
        self._hotkey_field.setStringValue_("Waiting...")
        self._hotkey_field.setTextColor_(NSColor.systemOrangeColor())

        self._monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            NSEventMaskKeyDown, self._capture_key
        )

    @objc.python_method
    def _stop_recording(self):
        if not self._recording:
            return
        self._recording = False
        self._record_btn.setTitle_("Record Hotkey")
        self._hotkey_field.setTextColor_(NSColor.labelColor())
        if self._monitor:
            NSEvent.removeMonitor_(self._monitor)
            self._monitor = None

    @objc.python_method
    def _capture_key(self, event):
        keycode = event.keyCode()
        flags = event.modifierFlags() & MOD_MASK

        if not flags:
            return event  # require at least one modifier

        self._pending_keycode = keycode
        self._pending_flags = flags
        self._hotkey_field.setStringValue_(format_hotkey(flags, keycode))
        self._stop_recording()
        return None  # consume the event

    # ── Show ──

    def show(self):
        self.window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)
