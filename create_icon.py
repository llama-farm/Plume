#!/usr/bin/env python3
"""Generate Plume app icon — feather quill made of audio waveform bars."""

import math
import os
import subprocess
import sys
import tempfile

import Quartz


def create_icon_image(size):
    cs = Quartz.CGColorSpaceCreateDeviceRGB()
    ctx = Quartz.CGBitmapContextCreate(
        None, size, size, 8, size * 4, cs,
        Quartz.kCGImageAlphaPremultipliedFirst,
    )

    # ── Background ──
    r = size * 0.20
    rect = Quartz.CGRectMake(0, 0, size, size)
    path = Quartz.CGPathCreateWithRoundedRect(rect, r, r, None)
    Quartz.CGContextAddPath(ctx, path)
    Quartz.CGContextSetRGBFillColor(ctx, 0.06, 0.06, 0.14, 1.0)
    Quartz.CGContextFillPath(ctx)

    # Subtle inner
    inset = size * 0.025
    inner = Quartz.CGRectMake(inset, inset, size - 2 * inset, size - 2 * inset)
    ip = Quartz.CGPathCreateWithRoundedRect(inner, r * 0.9, r * 0.9, None)
    Quartz.CGContextAddPath(ctx, ip)
    Quartz.CGContextSetRGBFillColor(ctx, 0.08, 0.08, 0.18, 1.0)
    Quartz.CGContextFillPath(ctx)

    # ── Rotate slightly for feather sweep ──
    cx, cy = size / 2.0, size / 2.0
    angle = math.radians(10)
    Quartz.CGContextTranslateCTM(ctx, cx, cy)
    Quartz.CGContextRotateCTM(ctx, angle)
    Quartz.CGContextTranslateCTM(ctx, -cx, -cy)

    # ── Waveform bars (feather barbs) ──
    bar_w = size * 0.062
    gap = size * 0.032
    n = 7
    heights = [0.10, 0.18, 0.29, 0.44, 0.29, 0.18, 0.10]
    bar_base = size * 0.34

    for i, h in enumerate(heights):
        bh = size * h
        bx = cx + (i - n / 2.0 + 0.5) * (bar_w + gap) - bar_w / 2.0

        # Slight arc: outer bars sit a touch lower
        t = (i - (n - 1) / 2.0) / ((n - 1) / 2.0)  # -1 to 1
        arc_offset = -abs(t) * size * 0.025
        by = bar_base + arc_offset

        # Color: warm teal → cool cyan across bars
        p = i / (n - 1)
        cr = 0.20 + 0.25 * (1 - p)
        cg = 0.72 + 0.08 * p
        cb = 0.85 + 0.15 * p
        Quartz.CGContextSetRGBFillColor(ctx, cr, cg, cb, 1.0)

        br = Quartz.CGRectMake(bx, by, bar_w, bh)
        bp = Quartz.CGPathCreateWithRoundedRect(br, bar_w / 2, bar_w / 2, None)
        Quartz.CGContextAddPath(ctx, bp)
        Quartz.CGContextFillPath(ctx)

    # ── Quill nib (tapered point below center) ──
    nib_top = bar_base
    nib_bottom = bar_base - size * 0.16
    nib_w = size * 0.022

    Quartz.CGContextSetRGBFillColor(ctx, 0.45, 0.82, 0.95, 0.85)
    Quartz.CGContextBeginPath(ctx)
    Quartz.CGContextMoveToPoint(ctx, cx - nib_w, nib_top)
    Quartz.CGContextAddCurveToPoint(
        ctx,
        cx - nib_w * 0.6, nib_top - size * 0.08,
        cx - nib_w * 0.15, nib_top - size * 0.13,
        cx, nib_bottom,
    )
    Quartz.CGContextAddCurveToPoint(
        ctx,
        cx + nib_w * 0.15, nib_top - size * 0.13,
        cx + nib_w * 0.6, nib_top - size * 0.08,
        cx + nib_w, nib_top,
    )
    Quartz.CGContextClosePath(ctx)
    Quartz.CGContextFillPath(ctx)

    return Quartz.CGBitmapContextCreateImage(ctx)


def save_png(cgimage, path):
    url = Quartz.CFURLCreateFromFileSystemRepresentation(
        None, path.encode(), len(path.encode()), False
    )
    dest = Quartz.CGImageDestinationCreateWithURL(url, "public.png", 1, None)
    Quartz.CGImageDestinationAddImage(dest, cgimage, None)
    Quartz.CGImageDestinationFinalize(dest)


def main():
    output = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "AppIcon.icns"
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        iconset = os.path.join(tmpdir, "AppIcon.iconset")
        os.makedirs(iconset)

        for s in [16, 32, 128, 256, 512]:
            img = create_icon_image(s)
            save_png(img, os.path.join(iconset, f"icon_{s}x{s}.png"))
            img2x = create_icon_image(s * 2)
            save_png(img2x, os.path.join(iconset, f"icon_{s}x{s}@2x.png"))

        subprocess.run(
            ["iconutil", "-c", "icns", iconset, "-o", output], check=True
        )

    print(f"Created {output}")


if __name__ == "__main__":
    main()
