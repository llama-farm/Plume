"""
Plume onboarding — first-launch wizard for permissions and usage intro.
"""

import os
import sys
import subprocess

import objc
import Quartz
import sounddevice as sd
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSButton,
    NSColor,
    NSFloatingWindowLevel,
    NSFont,
    NSImage,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSMakeRect,
    NSObject,
    NSProgressIndicator,
    NSProgressIndicatorBarStyle,
    NSTextField,
    NSView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskFullSizeContentView,
    NSWindowStyleMaskTitled,
)
from Foundation import NSTimer

import settings as cfg

# ── Path resolution ───────────────────────────────────────────

_FROZEN = getattr(sys, "frozen", False)

if _FROZEN:
    _BUNDLE_DIR = os.path.normpath(
        os.path.join(os.path.dirname(sys.executable), "..", "Resources")
    )
else:
    _BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Layout constants ─────────────────────────────────────────

W = 540
H = 440
PAD = 32
BRAND_COLOR = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.22, 0.56, 0.84, 1.0)
BRAND_TEAL = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.18, 0.60, 0.72, 1.0)
NAV_H = 56  # height of the bottom navigation bar
PAGE_TOP = H - PAD
PAGE_BOTTOM = NAV_H + 8
NUM_PAGES = 3


# ── Helpers ──────────────────────────────────────────────────

def _label(text, frame, size=13, bold=False, color=None, alignment=0):
    tf = NSTextField.alloc().initWithFrame_(frame)
    tf.setStringValue_(text)
    tf.setBezeled_(False)
    tf.setDrawsBackground_(False)
    tf.setEditable_(False)
    tf.setSelectable_(False)
    if bold:
        tf.setFont_(NSFont.boldSystemFontOfSize_(size))
    else:
        tf.setFont_(NSFont.systemFontOfSize_(size))
    if color:
        tf.setTextColor_(color)
    tf.setAlignment_(alignment)
    return tf


def _multiline_label(text, frame, size=13, color=None, alignment=0):
    tf = _label(text, frame, size=size, color=color, alignment=alignment)
    tf.setLineBreakMode_(0)  # NSLineBreakByWordWrapping
    tf.cell().setWraps_(True)
    return tf


def _check_mic_permission():
    """Return True if microphone access is granted."""
    try:
        AVCaptureDevice = objc.lookUpClass("AVCaptureDevice")
        status = AVCaptureDevice.authorizationStatusForMediaType_("soun")
        return status == 3  # AVAuthorizationStatusAuthorized
    except Exception:
        return False


def _check_accessibility_permission():
    """Return True if accessibility access is granted."""
    try:
        return bool(Quartz.AXIsProcessTrusted())
    except Exception:
        return False


# ── Onboarding window controller ────────────────────────────

class OnboardingWindowController(NSObject):
    """3-page onboarding wizard for first-launch setup."""

    window = objc.ivar()

    def initWithCallback_(self, on_complete):
        self = objc.super(OnboardingWindowController, self).init()
        if self is None:
            return None
        self._on_complete = on_complete
        self._current_page = 0
        self._pages = []
        self._dots = []
        self._back_btn = None
        self._next_btn = None
        self._poll_timer = None
        self._mic_status = None
        self._acc_status = None
        self._dl_progress = None
        self._dl_status = None
        self._bg_btn = None
        self._completed = False
        self._build_window()
        return self

    # ── Window construction ──

    @objc.python_method
    def _build_window(self):
        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                 | NSWindowStyleMaskFullSizeContentView)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H), style, NSBackingStoreBuffered, False,
        )
        self.window.setTitle_("")
        self.window.setTitlebarAppearsTransparent_(True)
        self.window.setTitleVisibility_(1)  # NSWindowTitleHidden
        self.window.setMovableByWindowBackground_(True)
        self.window.setReleasedWhenClosed_(False)
        self.window.setLevel_(NSFloatingWindowLevel)
        self.window.center()
        self.window.setDelegate_(self)

        cv = self.window.contentView()

        # Build pages
        self._pages = [
            self._build_page_welcome(),
            self._build_page_permissions(),
            self._build_page_howto(),
        ]
        for page in self._pages:
            cv.addSubview_(page)

        # Navigation bar
        self._build_nav(cv)

        # Show first page
        self._show_page(0)

    @objc.python_method
    def _build_page_welcome(self):
        page = NSView.alloc().initWithFrame_(NSMakeRect(0, NAV_H, W, H - NAV_H))

        # App icon
        icon_size = 112
        icon_x = (W - icon_size) // 2
        icon_y = H - NAV_H - PAD - icon_size - 20

        icon_path = os.path.join(_BUNDLE_DIR, "AppIcon.icns")
        icon_image = NSImage.alloc().initWithContentsOfFile_(icon_path)
        if icon_image:
            iv = NSImageView.alloc().initWithFrame_(
                NSMakeRect(icon_x, icon_y, icon_size, icon_size)
            )
            iv.setImage_(icon_image)
            iv.setImageScaling_(NSImageScaleProportionallyUpOrDown)
            page.addSubview_(iv)

        # Title
        title_y = icon_y - 44
        title = _label(
            "Plume \u2014 AI Dictation",
            NSMakeRect(0, title_y, W, 30),
            size=22, bold=True, alignment=1,  # center
        )
        page.addSubview_(title)

        # Tagline
        tagline_y = title_y - 50
        tagline = _multiline_label(
            "Fast, private speech-to-text that runs entirely on your Mac. "
            "No internet required.",
            NSMakeRect(PAD + 20, tagline_y, W - 2 * (PAD + 20), 44),
            size=14,
            color=NSColor.secondaryLabelColor(),
            alignment=1,
        )
        page.addSubview_(tagline)

        return page

    @objc.python_method
    def _build_page_permissions(self):
        page = NSView.alloc().initWithFrame_(NSMakeRect(0, NAV_H, W, H - NAV_H))

        y = H - NAV_H - PAD - 16

        # Page title
        y -= 28
        title = _label(
            "Permissions",
            NSMakeRect(PAD, y, W - 2 * PAD, 28),
            size=20, bold=True, alignment=1,
        )
        page.addSubview_(title)

        y -= 12
        subtitle = _multiline_label(
            "Plume needs two permissions to work properly.",
            NSMakeRect(PAD, y - 20, W - 2 * PAD, 20),
            size=13, color=NSColor.secondaryLabelColor(), alignment=1,
        )
        page.addSubview_(subtitle)

        y -= 52

        # ── Microphone block ──
        y = self._build_permission_block(
            page, y,
            title="Microphone Access",
            desc="Required to capture your voice for transcription. "
                 "Audio never leaves your Mac.",
            button_title="Grant Microphone Access",
            action="micClicked:",
            is_mic=True,
        )

        y -= 24

        # ── Accessibility block ──
        y = self._build_permission_block(
            page, y,
            title="Accessibility Access",
            desc="Required to register the global hotkey and paste "
                 "transcribed text at your cursor.",
            button_title="Open Accessibility Settings",
            action="accClicked:",
            is_mic=False,
        )

        return page

    @objc.python_method
    def _build_permission_block(self, parent, y, title, desc, button_title, action, is_mic):
        block_x = PAD + 8
        block_w = W - 2 * (PAD + 8)

        # Title
        y -= 22
        lbl = _label(title, NSMakeRect(block_x, y, block_w - 40, 20), size=14, bold=True)
        parent.addSubview_(lbl)

        # Status indicator (right side of title)
        status = _label(
            "\u25CB",  # empty circle
            NSMakeRect(block_x + block_w - 36, y, 36, 20),
            size=14, alignment=2,  # right
        )
        parent.addSubview_(status)
        if is_mic:
            self._mic_status = status
        else:
            self._acc_status = status

        # Description
        y -= 36
        d = _multiline_label(
            desc,
            NSMakeRect(block_x, y, block_w, 34),
            size=12, color=NSColor.secondaryLabelColor(),
        )
        parent.addSubview_(d)

        # Button
        y -= 34
        btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(block_x, y, 220, 28)
        )
        btn.setTitle_(button_title)
        btn.setBezelStyle_(NSBezelStyleRounded)
        btn.setTarget_(self)
        btn.setAction_(objc.selector(
            getattr(self, action.replace(":", "_")),
            signature=b"v@:@",
        ))
        parent.addSubview_(btn)

        return y

    @objc.python_method
    def _build_page_howto(self):
        page = NSView.alloc().initWithFrame_(NSMakeRect(0, NAV_H, W, H - NAV_H))

        y = H - NAV_H - PAD - 16

        # Page title
        y -= 28
        title = _label(
            "How to Use Plume",
            NSMakeRect(PAD, y, W - 2 * PAD, 28),
            size=20, bold=True, alignment=1,
        )
        page.addSubview_(title)

        y -= 12

        steps = [
            ("1", "start recording", "speak",
             "Use the hotkey to begin dictating. You can change this in Settings."),
            ("2", None, None,
             "Speak naturally \u2014 Plume listens on-device and streams text to your cursor in real time."),
            ("3", "when you\u2019re done speaking", None,
             "Plume finishes streaming the transcription to wherever you\u2019re typing."),
        ]

        badge_size = 28
        badge_x = PAD + 12
        text_x = badge_x + badge_size + 14
        text_w = W - text_x - PAD - 8
        heading_h = 20
        detail_h = 30

        for num, hotkey_action, hotkey_verb, detail in steps:
            y -= 10

            # Heading top is at y
            heading_y = y - heading_h

            # Badge circle — aligned to top of heading
            badge_y = heading_y - (badge_size - heading_h) // 2
            badge_view = NSView.alloc().initWithFrame_(
                NSMakeRect(badge_x, badge_y, badge_size, badge_size)
            )
            badge_view.setWantsLayer_(True)
            badge_view.layer().setBackgroundColor_(
                Quartz.CGColorCreateGenericRGB(0.22, 0.56, 0.84, 1.0)
            )
            badge_view.layer().setCornerRadius_(badge_size / 2)
            page.addSubview_(badge_view)

            # Badge number — use monospaced font and vertically center with offset
            # Number label — use a tight-height frame centered in the badge
            lbl_h = 16
            lbl_y = badge_y + (badge_size - lbl_h) // 2
            badge_label = NSTextField.alloc().initWithFrame_(
                NSMakeRect(badge_x, lbl_y, badge_size, lbl_h)
            )
            badge_label.setStringValue_(num)
            badge_label.setBezeled_(False)
            badge_label.setDrawsBackground_(False)
            badge_label.setEditable_(False)
            badge_label.setSelectable_(False)
            badge_label.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(13, 0.5))
            badge_label.setTextColor_(NSColor.whiteColor())
            badge_label.setAlignment_(1)  # center
            badge_label.cell().setLineBreakMode_(5)  # truncate, no wrapping
            page.addSubview_(badge_label)

            # Step heading — build with attributed string for code treatment
            if hotkey_action is not None:
                # Build heading with code-styled hotkey
                heading_parts = self._build_hotkey_heading(
                    hotkey_action, hotkey_verb, text_w, heading_y
                )
                for view in heading_parts:
                    page.addSubview_(view)
            else:
                heading_lbl = _label(
                    "Speak naturally",
                    NSMakeRect(text_x, heading_y, text_w, heading_h),
                    size=14, bold=True,
                )
                page.addSubview_(heading_lbl)

            # Step detail
            y = heading_y - 4
            detail_lbl = _multiline_label(
                detail,
                NSMakeRect(text_x, y - detail_h, text_w, detail_h),
                size=12, color=NSColor.secondaryLabelColor(),
            )
            page.addSubview_(detail_lbl)

            y -= detail_h + 2

        # ── Download progress UI ──
        y -= 6
        bar_x = PAD + 12
        bar_w = W - 2 * (PAD + 12)

        dl_label = _label(
            "Downloading model",
            NSMakeRect(bar_x, y - 14, bar_w, 14),
            size=11, bold=True,
        )
        page.addSubview_(dl_label)
        y -= 16

        self._dl_progress = NSProgressIndicator.alloc().initWithFrame_(
            NSMakeRect(bar_x, y - 14, bar_w, 14)
        )
        self._dl_progress.setStyle_(NSProgressIndicatorBarStyle)
        self._dl_progress.setIndeterminate_(False)
        self._dl_progress.setMinValue_(0)
        self._dl_progress.setMaxValue_(100)
        self._dl_progress.setDoubleValue_(0)
        page.addSubview_(self._dl_progress)
        y -= 18

        self._dl_status = _label(
            "Downloading speech model...",
            NSMakeRect(bar_x, y - 14, bar_w, 14),
            size=11, color=NSColor.secondaryLabelColor(),
        )
        page.addSubview_(self._dl_status)
        y -= 16

        self._bg_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(bar_x, y - 18, 180, 18)
        )
        self._bg_btn.setTitle_("Continue in Background")
        self._bg_btn.setBezelStyle_(0)  # borderless
        self._bg_btn.setBordered_(False)
        self._bg_btn.setFont_(NSFont.systemFontOfSize_(12))
        self._bg_btn.setContentTintColor_(BRAND_COLOR)
        self._bg_btn.setTarget_(self)
        self._bg_btn.setAction_(objc.selector(self.bgClicked_, signature=b"v@:@"))
        page.addSubview_(self._bg_btn)

        return page

    @objc.python_method
    def _build_hotkey_heading(self, action, verb, text_w, heading_y):
        """Build a heading row with inline code-styled hotkey badge."""
        views = []
        badge_x = PAD + 12
        text_x = badge_x + 28 + 14
        x = text_x

        # "Press " prefix
        if verb:
            prefix = f"Press "
        else:
            prefix = "Press "
        pre_lbl = _label(prefix, NSMakeRect(x, heading_y, 42, 20), size=14, bold=True)
        pre_lbl.sizeToFit()
        pre_w = pre_lbl.frame().size.width + 2
        pre_lbl.setFrame_(NSMakeRect(x, heading_y, pre_w, 20))
        views.append(pre_lbl)
        x += pre_w

        # Code badge for hotkey
        hotkey_text = "\u2303 Esc"
        code_font = NSFont.monospacedSystemFontOfSize_weight_(12, 0.3)

        # Measure text width
        code_lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 200, 18))
        code_lbl.setStringValue_(hotkey_text)
        code_lbl.setFont_(code_font)
        code_lbl.sizeToFit()
        code_w = code_lbl.frame().size.width + 12  # padding

        # Code background
        code_bg = NSView.alloc().initWithFrame_(
            NSMakeRect(x, heading_y + 1, code_w, 18)
        )
        code_bg.setWantsLayer_(True)
        code_bg.layer().setBackgroundColor_(
            Quartz.CGColorCreateGenericRGB(1.0, 1.0, 1.0, 0.1)
        )
        code_bg.layer().setCornerRadius_(4)
        views.append(code_bg)

        # Code text
        code_lbl = NSTextField.alloc().initWithFrame_(
            NSMakeRect(x, heading_y + 1, code_w, 18)
        )
        code_lbl.setStringValue_(hotkey_text)
        code_lbl.setBezeled_(False)
        code_lbl.setDrawsBackground_(False)
        code_lbl.setEditable_(False)
        code_lbl.setSelectable_(False)
        code_lbl.setFont_(code_font)
        code_lbl.setTextColor_(NSColor.labelColor())
        code_lbl.setAlignment_(1)
        views.append(code_lbl)
        x += code_w + 3

        # " to start recording" suffix
        suffix = f" to {action}"
        suf_lbl = _label(suffix, NSMakeRect(x, heading_y, text_w - (x - text_x), 20),
                         size=14, bold=True)
        views.append(suf_lbl)

        return views

    @objc.python_method
    def _build_nav(self, parent):
        nav = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W, NAV_H))

        # Back button
        self._back_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(PAD, 14, 80, 28)
        )
        self._back_btn.setTitle_("Back")
        self._back_btn.setBezelStyle_(NSBezelStyleRounded)
        self._back_btn.setTarget_(self)
        self._back_btn.setAction_(objc.selector(self.backClicked_, signature=b"v@:@"))
        nav.addSubview_(self._back_btn)

        # Page dots (centered)
        dot_size = 8
        dot_gap = 12
        total_dot_w = NUM_PAGES * dot_size + (NUM_PAGES - 1) * dot_gap
        dot_x = (W - total_dot_w) / 2
        dot_y = (NAV_H - dot_size) / 2

        self._dots = []
        for i in range(NUM_PAGES):
            dot = NSView.alloc().initWithFrame_(
                NSMakeRect(dot_x + i * (dot_size + dot_gap), dot_y, dot_size, dot_size)
            )
            dot.setWantsLayer_(True)
            dot.layer().setCornerRadius_(dot_size / 2)
            nav.addSubview_(dot)
            self._dots.append(dot)

        # Next button
        self._next_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(W - PAD - 110, 14, 110, 28)
        )
        self._next_btn.setBezelStyle_(NSBezelStyleRounded)
        self._next_btn.setTarget_(self)
        self._next_btn.setAction_(objc.selector(self.nextClicked_, signature=b"v@:@"))
        self._next_btn.setKeyEquivalent_("\r")
        nav.addSubview_(self._next_btn)

        parent.addSubview_(nav)

    # ── Page navigation ──

    @objc.python_method
    def _show_page(self, index):
        self._current_page = index
        for i, page in enumerate(self._pages):
            page.setHidden_(i != index)

        # Update nav
        self._back_btn.setHidden_(index == 0)

        if index == NUM_PAGES - 1:
            self._next_btn.setTitle_("Get Started")
        else:
            self._next_btn.setTitle_("Next")

        # Update dots
        for i, dot in enumerate(self._dots):
            if i == index:
                dot.layer().setBackgroundColor_(
                    Quartz.CGColorCreateGenericRGB(0.22, 0.56, 0.84, 1.0)
                )
            else:
                dot.layer().setBackgroundColor_(
                    Quartz.CGColorCreateGenericRGB(0.7, 0.7, 0.7, 0.5)
                )

        # Start/stop permission polling
        if index == 1:
            self._start_polling()
        else:
            self._stop_polling()

    # ── Button actions ──

    def backClicked_(self, sender):
        if self._current_page > 0:
            self._show_page(self._current_page - 1)

    def nextClicked_(self, sender):
        if self._current_page < NUM_PAGES - 1:
            self._show_page(self._current_page + 1)
        else:
            self._complete()

    def micClicked_(self, sender):
        """Trigger the macOS microphone permission prompt."""
        try:
            sd.InputStream(channels=1, dtype="float32", callback=lambda *a: None).close()
        except Exception:
            pass

    def accClicked_(self, sender):
        """Open Accessibility settings in System Preferences."""
        try:
            subprocess.Popen([
                "open",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
            ])
        except Exception:
            pass

    def bgClicked_(self, sender):
        """'Continue in Background' — close wizard, download continues."""
        self._complete()

    # ── Download progress (called from app.py via performSelectorOnMainThread) ──

    def updateDownloadProgress_(self, pct_number):
        """Update download bar + label. pct_number is an NSNumber (0-100)."""
        pct = int(pct_number.intValue())
        if self._dl_progress:
            self._dl_progress.setDoubleValue_(pct)
        if self._dl_status:
            self._dl_status.setStringValue_(f"Downloading speech model... {pct}%")

    def downloadComplete_(self, _ignored):
        """Show download-complete state on page 3."""
        if self._dl_progress:
            self._dl_progress.setDoubleValue_(100)
        if self._dl_status:
            self._dl_status.setStringValue_("Download complete!")
            self._dl_status.setTextColor_(
                NSColor.colorWithCalibratedRed_green_blue_alpha_(0.2, 0.7, 0.3, 1.0)
            )
        if self._bg_btn:
            self._bg_btn.setHidden_(True)

    def downloadFailed_(self, error_msg):
        """Show error state on page 3. error_msg is an NSString."""
        msg = str(error_msg) if error_msg else "Download failed"
        if self._dl_status:
            self._dl_status.setStringValue_(msg)
            self._dl_status.setTextColor_(NSColor.systemRedColor())
        if self._dl_progress:
            self._dl_progress.setDoubleValue_(0)
        if self._bg_btn:
            self._bg_btn.setHidden_(True)

    # ── Permission polling ──

    @objc.python_method
    def _start_polling(self):
        if self._poll_timer is not None:
            return
        self._update_permission_status()
        self._poll_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.5, self, "pollPermissions:", None, True,
        )

    @objc.python_method
    def _stop_polling(self):
        if self._poll_timer is not None:
            self._poll_timer.invalidate()
            self._poll_timer = None

    def pollPermissions_(self, timer):
        self._update_permission_status()

    @objc.python_method
    def _update_permission_status(self):
        mic_ok = _check_mic_permission()
        acc_ok = _check_accessibility_permission()

        if self._mic_status:
            if mic_ok:
                self._mic_status.setStringValue_("\u2705")
            else:
                self._mic_status.setStringValue_("\u25CB")

        if self._acc_status:
            if acc_ok:
                self._acc_status.setStringValue_("\u2705")
            else:
                self._acc_status.setStringValue_("\u25CB")

    # ── Completion ──

    @objc.python_method
    def _complete(self):
        if self._completed:
            return
        self._completed = True
        self._stop_polling()

        # Save onboarding complete
        s = cfg.load()
        s["onboarding_complete"] = True
        cfg.save(s)

        # Close triggers windowWillClose_ which handles cleanup
        self.window.close()

        if self._on_complete:
            self._on_complete()

    # ── Window delegate ──

    def windowWillClose_(self, notification):
        # Only clean up polling — don't mark complete so onboarding
        # reappears next launch if closed early
        self._stop_polling()
        NSApp.setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory

    # ── Show ──

    def show(self):
        # Temporarily become a regular app so the window can come to front
        NSApp.setActivationPolicy_(0)  # NSApplicationActivationPolicyRegular
        self.window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    def deferredShow_(self, _sender):
        """ObjC-callable variant for performSelector:withObject:afterDelay:."""
        self.show()
