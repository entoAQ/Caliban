#!/usr/bin/env python3
"""Whole-dish IR index across a series of known-contamination samples.

Answers one question: does the infrared index climb with real MEO content, and
how tightly? That is the whole test. If it tracks, you have a measurement
rather than an estimate; if it does not, the technique is finished and you have
found out cheaply.

    python3 ir_series.py MEO000=0 MEO004=4 MEO008=8 MEO014=14 MEO100=100

Each argument is the capture label and its known lab ME%. Both frames of each
pair are found under ~/captures by that label.

Deliberately a whole-dish average rather than per-pixel classification. Telling
individual pixels apart needs the two materials to separate cleanly, which is a
demanding bar. Estimating a proportion does not: pixel noise averages away over
a million of them, and what matters is whether the dish-level number moves
with the dish-level truth.
"""

import argparse
import csv
import sys
from pathlib import Path

import numpy as np
from PIL import Image

CAPTURES = Path.home() / "captures"
GAMMA = 2.2
DARK_FLOOR = 0.01

# Weighted toward red: every channel sees 850nm with no IR-cut filter, but the
# red filter transmits it best and so carries the most signal per unit noise.
WEIGHTS = np.array([0.6, 0.25, 0.15])


def linear(path):
    arr = np.asarray(Image.open(path).convert("RGB"), dtype=np.float64) / 255.0
    return (arr ** GAMMA) @ WEIGHTS


def find_pair(label):
    """The visible and IR frames for one label, most recent if several."""
    def newest(band):
        matches = sorted(CAPTURES.glob(f"{label}_*_{band}.jpg"))
        return matches[-1] if matches else None

    vis, ir = newest("visible"), newest("ir")
    if not vis or not ir:
        missing = "visible" if not vis else "ir"
        sys.exit(f"No {missing} frame found for {label} in {CAPTURES}")
    return vis, ir


def measure(label, region, flat=None):
    vis_path, ir_path = find_pair(label)
    vis, ir = linear(vis_path), linear(ir_path)
    if vis.shape != ir.shape:
        sys.exit(f"{label}: frames differ in size. Same crop for both.")

    index = (ir - vis) / np.maximum(vis, DARK_FLOOR)
    if flat is not None:
        index = index / np.maximum(flat, DARK_FLOOR)

    h, w = index.shape
    fx0, fy0, fx1, fy1 = region
    patch = index[int(h * fy0):int(h * fy1), int(w * fx0):int(w * fx1)]

    # Clipped pixels have lost their signal; averaging them in quietly drags
    # the result toward whatever the sensor's ceiling happens to be.
    raw_ir = np.asarray(Image.open(ir_path).convert("RGB"))
    ir_patch = raw_ir[int(h * fy0):int(h * fy1), int(w * fx0):int(w * fx1)]
    clipped = float((ir_patch.max(axis=2) >= 253).mean())

    return {
        "label": label,
        "index": float(patch.mean()),
        "sd": float(patch.std()),
        "clipped": clipped,
        "file": vis_path.name,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("samples", nargs="+", metavar="LABEL=PCT",
                    help="capture label and its known lab ME%%, e.g. MEO004=4")
    ap.add_argument("--region", default="0.25,0.08,0.92,0.92",
                    help="dish interior as x0,y0,x1,y1 fractions of the frame")
    ap.add_argument("--flat", nargs=2, metavar=("VISIBLE", "IR"),
                    help="optional flat-field pair, shot at the levelled surface height")
    ap.add_argument("--csv", default=str(Path.home() / "ir_series.csv"))
    args = ap.parse_args()

    region = tuple(float(v) for v in args.region.split(","))
    if len(region) != 4:
        sys.exit("--region needs four fractions: x0,y0,x1,y1")

    flat = None
    if args.flat:
        fv, fi = linear(args.flat[0]), linear(args.flat[1])
        flat = (fi - fv) / np.maximum(fv, DARK_FLOOR)

    rows = []
    for spec in args.samples:
        if "=" not in spec:
            sys.exit(f"Expected LABEL=PCT, got {spec!r}")
        label, pct = spec.split("=", 1)
        row = measure(label, region, flat)
        row["real_pct"] = float(pct)
        rows.append(row)

    rows.sort(key=lambda r: r["real_pct"])

    print(f"Region {region}{'  (flat-fielded)' if flat is not None else ''}\n")
    print(f"  {'sample':10s} {'lab ME%':>8s} {'index':>8s} {'sd':>7s}  {'clipped':>7s}")
    for r in rows:
        flag = f"{r['clipped']:6.1%}" + (" !" if r["clipped"] > 0.01 else "  ")
        print(f"  {r['label']:10s} {r['real_pct']:8.1f} {r['index']:8.3f} "
              f"{r['sd']:7.3f}  {flag}")

    idx = [r["index"] for r in rows]
    pct = [r["real_pct"] for r in rows]

    # Monotonicity first, and separately from the fit. A relationship that
    # rises everywhere is usable even if it is not a straight line -- you can
    # calibrate any monotonic curve. One that doubles back cannot be
    # calibrated at all, and a good R-squared would hide that.
    rising = all(b > a for a, b in zip(idx, idx[1:]))
    falling = all(b < a for a, b in zip(idx, idx[1:]))
    print()
    if rising or falling:
        print(f"  Monotonic ({'rising' if rising else 'falling'}) -- the index "
              f"orders the samples correctly.")
    else:
        print("  NOT monotonic -- the index does not order the samples by MEO\n"
              "  content, so no calibration curve can recover it. Check the\n"
              "  captures before concluding: a disturbed dish or a changed\n"
              "  exposure would do this too.")

    if len(rows) >= 3:
        slope, intercept = np.polyfit(pct, idx, 1)
        predicted = np.polyval([slope, intercept], pct)
        ss_res = float(np.sum((np.array(idx) - predicted) ** 2))
        ss_tot = float(np.sum((np.array(idx) - np.mean(idx)) ** 2))
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
        span = max(idx) - min(idx)
        print(f"  Linear fit: index = {slope:+.4f} x ME%% {intercept:+.3f}, "
              f"R² = {r2:.3f}")
        print(f"  Full-range swing: {span:.3f} index units across "
              f"{min(pct):.0f}-{max(pct):.0f}%% ME")

        # What the fit is worth in the units that matter: how finely can this
        # resolve ME%, given how much the index varies within a single dish?
        if slope != 0:
            typical_sd = float(np.mean([r["sd"] for r in rows]))
            print(f"  Within-dish spread of {typical_sd:.3f} corresponds to "
                  f"{abs(typical_sd / slope):.1f} points of ME%%.")
            print("  That is pixel-level scatter, not the error on a dish average --\n"
                  "  averaging a million pixels shrinks it enormously. The honest\n"
                  "  error bar comes from re-shooting the same dish several times.")

    with open(args.csv, "w", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["label", "real_pct", "index", "sd", "clipped", "file"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nWrote {args.csv}")


if __name__ == "__main__":
    main()
