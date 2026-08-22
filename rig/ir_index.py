#!/usr/bin/env python3
"""Two-band index from a paired visible/IR capture.

Takes the two frames of a difference pair -- identical scene, identical
exposure, the only variable being whether the 850nm LEDs were lit -- and
computes how much infrared each pixel returned relative to its visible
brightness. That ratio is a material property: it does not care how brightly
lit the scene was, only how the material responds at one wavelength versus
another.

    python3 ir_index.py PILES_visible.jpg PILES_ir.jpg --out index.png \
        --region frass=0.32,0.30,0.48,0.60 \
        --region larvae=0.60,0.30,0.76,0.60

Why the subtraction works: both frames carry the same ambient illumination, so
it cancels, leaving only what the LEDs contributed. That is what lets this run
under uncontrolled room lighting instead of requiring a darkened enclosure.

Two things to be careful about when reading the output.

Linearisation matters. JPEG pixels are gamma-encoded, and subtracting two
gamma-encoded images gives a number with no physical meaning. Everything here
happens in linear space and only the preview image is re-encoded.

And the IR LEDs sit on the camera board, firing on-axis, while the room lights
are diffuse and angled. So part of any difference between two materials may be
their response to on-axis versus diffuse illumination -- surface texture and
gloss -- rather than their spectral reflectance. That does not make a working
discriminator less useful, but it does mean the mechanism is not proven to be
spectral, and a different lighting geometry at the line might not reproduce it.
"""

import argparse
import sys

import numpy as np
from PIL import Image

GAMMA = 2.2

# Below this, a pixel carries too little signal for the ratio to mean anything
# -- dividing a small number by a small number amplifies noise into nonsense.
DARK_FLOOR = 0.01


def linear(path):
    """Load an image as linear-light float, 0..1."""
    arr = np.asarray(Image.open(path).convert("RGB"), dtype=np.float64) / 255.0
    return arr ** GAMMA


def parse_region(text):
    """name=x0,y0,x1,y1"""
    if "=" not in text:
        sys.exit(f"--region needs name=x0,y0,x1,y1 (got {text!r})")
    name, coords = text.split("=", 1)
    values = [float(v) for v in coords.split(",")]
    if len(values) != 4:
        sys.exit(f"--region {name} needs four fractions, got {len(values)}")
    return name, values


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("visible", help="frame with the IR LEDs OFF")
    ap.add_argument("ir", help="frame with the IR LEDs ON")
    ap.add_argument("--out", help="write a false-colour index image here")
    ap.add_argument("--region", action="append", default=[],
                    help="name=x0,y0,x1,y1 as fractions; repeatable")
    ap.add_argument("--flat", nargs=2, metavar=("VISIBLE", "IR"),
                    help="a pair shot of a uniform card filling the dish, used "
                         "to divide out the IR illumination field")
    args = ap.parse_args()

    vis = linear(args.visible)
    ir = linear(args.ir)
    if vis.shape != ir.shape:
        sys.exit(f"Frames differ in size: {vis.shape} vs {ir.shape}. They must be "
                 "the same capture geometry with the same crop.")

    # Weighted toward red. All three Bayer channels see 850nm because there is
    # no IR-cut filter, but the red channel's filter transmits it best, so it
    # carries the most infrared signal per unit noise.
    weights = np.array([0.6, 0.25, 0.15])
    vis_y = vis @ weights
    ir_y = ir @ weights

    # What the LEDs added, as a fraction of what was already there. Ambient
    # light is present in both terms of the numerator and cancels.
    contribution = ir_y - vis_y
    index = np.where(vis_y > DARK_FLOOR, contribution / np.maximum(vis_y, DARK_FLOOR), np.nan)

    # Report clipping inside the sampled regions, not across the whole frame.
    # The dish rim is bright plastic directly under on-axis LEDs and blows out
    # readily, but it is nowhere near the material being measured -- a
    # frame-wide figure raises an alarm about pixels that do not matter.
    clipped_mask = ir.max(axis=2) >= 0.99
    print(f"Index = (IR frame - visible frame) / visible frame, in linear light")
    print(f"Higher means the material returned more 850nm relative to its "
          f"visible brightness.")

    if args.flat:
        flat_vis, flat_ir = linear(args.flat[0]), linear(args.flat[1])
        if flat_vis.shape != vis.shape:
            sys.exit("Flat-field frames must match the sample frames in size.")
        flat_index = ((flat_ir @ weights) - (flat_vis @ weights)) / \
            np.maximum(flat_vis @ weights, DARK_FLOOR)

        # A uniform card should read the same everywhere. Where it does not,
        # the difference is the IR illumination field -- the LEDs sit on the
        # camera board, so they light the centre of the dish far harder than
        # the edges, and identical material scores differently depending only
        # on where it happens to lie. Dividing by the card's own index
        # normalises that away: after this, a uniform surface reads 1.0
        # everywhere and a material reads its response relative to the card.
        index = index / np.maximum(flat_index, DARK_FLOOR)
        falloff = np.nanpercentile(flat_index, [5, 95])
        print(f"\nFlat-fielded. Illumination varied {falloff[0]:.2f}-{falloff[1]:.2f} "
              f"across the frame ({falloff[1] / max(falloff[0], 1e-9):.1f}x) -- "
              f"that spread is now removed.")
        print("Values below are relative to the flat card, so 1.0 means "
              "'same as the card'.")
    print()

    stats = []
    for spec in args.region:
        name, (fx0, fy0, fx1, fy1) = parse_region(spec)
        h, w = index.shape
        y0, y1 = int(h * fy0), int(h * fy1)
        x0, x1 = int(w * fx0), int(w * fx1)
        patch = index[y0:y1, x0:x1]
        region_clipped = float(clipped_mask[y0:y1, x0:x1].mean())
        patch = patch[~np.isnan(patch)]
        if patch.size == 0:
            print(f"  {name:12s}  no usable pixels (too dark?)")
            continue
        mean, sd = patch.mean(), patch.std()
        stats.append((name, mean, sd))
        flag = f"  [{region_clipped:.1%} CLIPPED]" if region_clipped > 0.01 else ""
        print(f"  {name:12s}  index {mean:+.3f}  sd {sd:.3f}{flag}")

    # The whole question in one number: are the two materials further apart
    # than their own internal variation? Below about 1, any threshold that
    # separates them on average still misclassifies a great many pixels.
    if len(stats) == 2:
        (n1, m1, s1), (n2, m2, s2) = stats
        pooled = np.sqrt((s1 ** 2 + s2 ** 2) / 2)
        separation = abs(m1 - m2) / pooled if pooled > 0 else float("inf")
        print(f"\n  {n1} vs {n2}: separation {separation:.2f} "
              f"(difference in means over pooled spread)")
        if separation >= 2.0:
            print("  Strong -- a threshold on this index would classify pixels well.")
        elif separation >= 1.0:
            print("  Moderate -- real, but a per-pixel threshold would misclassify\n"
                  "  a noticeable fraction. Might still work on area averages.")
        else:
            print("  Weak for per-pixel segmentation -- the materials overlap\n"
                  "  more than they differ.")

        # Separation is the bar for deciding pixel by pixel what something is.
        # Estimating an area fraction from the mean over a whole dish is a much
        # lower bar: pixel noise averages out over a million of them, and what
        # matters is the gap between the pure-material means against the
        # variation between dishes, not within one.
        contrast = abs(m1 - m2) / max(abs(m1), abs(m2))
        print(f"\n  As a bulk index: {contrast:.0%} swing between the two pure\n"
              f"  materials. That is the dynamic range a dish-average would\n"
              f"  move across, and it does not depend on separating pixels.")

    if args.out:
        # Percentile-stretched so the preview shows the structure rather than
        # being dominated by whatever the extremes happen to be.
        finite = index[~np.isnan(index)]
        lo, hi = np.percentile(finite, [2, 98])
        shown = np.clip((np.nan_to_num(index, nan=lo) - lo) / max(hi - lo, 1e-9), 0, 1)
        Image.fromarray((shown * 255).astype(np.uint8)).save(args.out)
        print(f"\nWrote {args.out} -- bright means more infrared returned.")


if __name__ == "__main__":
    main()
