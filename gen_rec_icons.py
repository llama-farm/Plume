#!/usr/bin/env python3
"""Regenerate menubar-rec icons: waveform bars + microphone badge."""

import math
import Quartz


def load_png(path):
    url = Quartz.CFURLCreateFromFileSystemRepresentation(
        None, path.encode(), len(path.encode()), False
    )
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    return Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)


def save_png(cgimage, path):
    url = Quartz.CFURLCreateFromFileSystemRepresentation(
        None, path.encode(), len(path.encode()), False
    )
    dest = Quartz.CGImageDestinationCreateWithURL(url, "public.png", 1, None)
    Quartz.CGImageDestinationAddImage(dest, cgimage, None)
    Quartz.CGImageDestinationFinalize(dest)


def draw_mic(ctx, cx, bottom_y, scale):
    """Draw a microphone icon centered at cx, with bottom at bottom_y."""
    # Mic dimensions scaled to icon size
    head_w = 5.0 * scale
    head_h = 7.0 * scale
    stem_h = 2.5 * scale
    stem_w = 1.2 * scale
    arc_r = 4.0 * scale
    arc_w = 1.2 * scale
    base_w = 4.0 * scale
    base_h = 1.0 * scale

    head_r = head_w / 2.0

    # Position from bottom up
    base_bot = bottom_y
    base_top = base_bot + base_h
    stem_bot = base_top
    stem_top = stem_bot + stem_h
    arc_bot = stem_bot + 0.5 * scale
    head_bot = stem_top + 0.5 * scale
    head_top = head_bot + head_h

    Quartz.CGContextSetRGBFillColor(ctx, 0, 0, 0, 1)
    Quartz.CGContextSetRGBStrokeColor(ctx, 0, 0, 0, 1)

    # Mic head (rounded capsule)
    head_rect = Quartz.CGRectMake(cx - head_w / 2, head_bot, head_w, head_h)
    head_path = Quartz.CGPathCreateWithRoundedRect(head_rect, head_r, head_r, None)
    Quartz.CGContextAddPath(ctx, head_path)
    Quartz.CGContextFillPath(ctx)

    # Stem
    stem_rect = Quartz.CGRectMake(cx - stem_w / 2, stem_bot, stem_w, stem_top - stem_bot)
    Quartz.CGContextFillRect(ctx, stem_rect)

    # U-shaped holder arc
    Quartz.CGContextSetLineWidth(ctx, arc_w)
    Quartz.CGContextBeginPath(ctx)
    Quartz.CGContextAddArc(ctx, cx, head_bot + head_h * 0.35, arc_r,
                           -math.pi * 0.15, -math.pi * 0.85, 1)
    Quartz.CGContextStrokePath(ctx)

    # Base
    base_rect = Quartz.CGRectMake(cx - base_w / 2, base_bot, base_w, base_h)
    base_path = Quartz.CGPathCreateWithRoundedRect(base_rect, base_h / 2, base_h / 2, None)
    Quartz.CGContextAddPath(ctx, base_path)
    Quartz.CGContextFillPath(ctx)


def create_rec_icon(idle_path, output_path, size):
    idle_img = load_png(idle_path)

    cs = Quartz.CGColorSpaceCreateDeviceRGB()
    ctx = Quartz.CGBitmapContextCreate(
        None, size, size, 8, size * 4, cs,
        Quartz.kCGImageAlphaPremultipliedLast,
    )

    # Draw original idle icon
    Quartz.CGContextDrawImage(ctx, Quartz.CGRectMake(0, 0, size, size), idle_img)

    # Draw microphone in bottom-right area
    scale = size / 36.0
    mic_cx = size * 0.78
    mic_bottom = size * 0.06
    draw_mic(ctx, mic_cx, mic_bottom, scale)

    img = Quartz.CGBitmapContextCreateImage(ctx)
    save_png(img, output_path)
    print(f"Created {output_path}")


if __name__ == "__main__":
    icons = "icons"
    create_rec_icon(f"{icons}/menubar.png", f"{icons}/menubar-rec.png", 36)
    create_rec_icon(f"{icons}/menubar@2x.png", f"{icons}/menubar-rec@2x.png", 72)
