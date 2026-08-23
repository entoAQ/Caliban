#!/usr/bin/env python3
"""Separate background, larvae and MEO by colour, and measure the areas.

    python3 segment.py capture.jpg --bg-region 0.02,0.85,0.12,0.98 --out marked.png

The point is the denominator. With material scattered on a coloured tray, the
gaps are background, and background is measurable -- so MEO becomes a fraction
of the *material present* rather than a fraction of the image. How much got
tipped out stops mattering, which removes one of the larger sources of
operator variance without changing anything about how the sample is handled.

Classification is by hue, not brightness, and that is deliberate. A shadowed
blue tray is still blue; a brightness threshold would call it material, and
the tray's moulded ridges would appear as contamination in every frame. Hue
survives uneven lighting, shadow, and the NoIR colour cast in a way lightness
does not.

The background colour is learned from a region you point at rather than
hardcoded, so it works whatever the tray is and whatever the white balance is
doing that day.

This does not try to be the final answer. It produces a number and a picture
of what it counted, so the threshold can be calibrated against known-ME%
samples and, separately, so the marked-up image can be handed to the vision
model for a judgement it is actually good at: whether the right things got
marked.
"""

import argparse
import sys

import numpy as np
from PIL import Image


def parse_region(text):
    values = [float(v) for v in text.split(",")]
    if len(values) != 4:
        sys.exit(f"Region needs four fractions x0,y0,x1,y1 (got {text!r})")
    return values


def crop(arr, region):
    h, w = arr.shape[:2]
    x0, y0, x1, y1 = region
    return arr[int(h * y0):int(h * y1), int(w * x0):int(w * x1)]


def hue_distance(h, reference):
    """Circular distance on the 0-255 hue wheel."""
    d = np.abs(h.astype(np.int16) - int(reference))
    return np.minimum(d, 256 - d)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("--bg-region", required=True,
                    help="a patch of clean tray, as x0,y0,x1,y1 fractions")
    ap.add_argument("--hue-tol", type=int, default=20,
                    help="how far from the learned background hue still counts "
                         "as background (default 20 of 255)")
    ap.add_argument("--sat-min", type=int, default=60,
                    help="minimum saturation for background; keeps washed-out "
                         "highlights and near-grey material from being counted "
                         "as tray (default 60)")
    ap.add_argument("--meo-max-value", type=int, default=110,
                    help="material darker than this counts as MEO (default 110). "
                         "This is the number to calibrate against lab ME%%.")
    ap.add_argument("--out", help="write a marked-up image showing what was counted")
    args = ap.parse_args()

    rgb = np.asarray(Image.open(args.image).convert("RGB"))
    hsv = np.asarray(Image.open(args.image).convert("HSV"))
    hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    patch = crop(hsv, parse_region(args.bg_region))
    bg_hue = int(np.median(patch[:, :, 0]))
    bg_sat = int(np.median(patch[:, :, 1]))
    print(f"Background learned from that patch: hue {bg_hue}, saturation {bg_sat}")
    if bg_sat < args.sat_min:
        print(f"  WARNING: saturation {bg_sat} is below --sat-min {args.sat_min}.\n"
              "  The tray colour is too washed out to separate reliably. Either\n"
              "  lower --sat-min or use a more saturated background.")

    background = (hue_distance(hue, bg_hue) <= args.hue_tol) & (sat >= args.sat_min)
    material = ~background

    # MEO within the material only. Darkness outside the material is tray
    # shadow, and counting it would make a sparser scatter look dirtier.
    meo = material & (val < args.meo_max_value)

    total = hue.size
    material_px = int(material.sum())
    meo_px = int(meo.sum())

    print(f"\n  background   {background.sum() / total:6.1%} of frame")
    print(f"  material     {material_px / total:6.1%} of frame")
    if material_px == 0:
        sys.exit("\nNo material found. Check --bg-region is on clean tray.")
    print(f"\n  MEO          {meo_px / material_px:6.2%} of material   <- the number")
    print(f"               {meo_px / total:6.2%} of frame")

    # Coverage tells you whether the scatter is thin enough to trust. Once the
    # tray is nearly covered, larvae overlap and MEO hides underneath -- the
    # measurement silently becomes a surface reading again, which is the exact
    # problem the scatter method exists to avoid.
    coverage = material_px / total
    if coverage > 0.85:
        print(f"\n  WARNING: {coverage:.0%} coverage. Too dense -- material is\n"
              "  overlapping and hiding MEO beneath it. Scatter more thinly.")
    elif coverage < 0.25:
        print(f"\n  NOTE: only {coverage:.0%} coverage. Statistically thin; the\n"
              "  estimate will be noisy sample to sample.")

    if args.out:
        # Background white, larvae as they are, MEO in red. Both a check on the
        # thresholds and the image to hand the model when asking whether the
        # right things were marked.
        marked = rgb.copy()
        marked[background] = [255, 255, 255]
        marked[meo] = [220, 30, 30]
        Image.fromarray(marked).save(args.out)
        print(f"\nWrote {args.out} -- white is tray, red is what counted as MEO.")


if __name__ == "__main__":
    main()
