#!/usr/bin/env python3
"""Time-of-flight geometry check for the bench rig.

A DFRobot SEN0628 (VL53L5CX, 8x8 zones) mounted on the boom beside the camera,
looking at the same tray. It answers three questions the camera cannot:

  Is the rig still where it was?   The whole calibration -- focus, exposure,
      flat field, scale -- is only valid for one geometry. Nothing currently
      notices when the boom gets knocked, and a knocked boom does not look
      wrong in a photograph. It just quietly makes every number afterwards
      disagree with every number before.

  Is the tray square to the camera?   If one end of the tray is nearer, that
      end images larger, so the same frass covers more pixels there. That is a
      spatial bias in the exact quantity being measured, and like the
      illumination gradient it is fixed relative to the tray -- so
      rotation-averaging cannot touch it.

  How deep is the bed?   Which is the beginning of an answer to the
      segregation problem: fines percolate downward, so what the camera sees on
      top under-represents the bulk, and the discrepancy grows with depth.

Everything here works on *differences from a recorded flat reference*, never on
raw distances. A flat surface does not read uniform on this sensor and never
will: the outer zones look outward at an angle, so they measure a longer slant
path to the same plane. Chasing that with trigonometry means knowing the exact
field angle of every zone and the exact mounting angle of the board.
Subtracting a reference absorbs the slant geometry, the per-zone bias and the
mounting angle together, in one measurement, without needing to know any of
them separately.

Usage:
    python3 tof.py reference        # empty, flat, level tray -- once
    python3 tof.py read             # anything: tray, sample, nothing
    python3 tof.py read --tolerance 3
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

REFERENCE_FILE = Path.home() / "rig_tof_reference.json"

I2C_BUS = 1
I2C_ADDR = 0x33
GRID = 8

# The sensor's no-valid-return value. It is not a distance and must never be
# averaged with real ones -- a single 4000 mixed into a cell's history drags
# that cell metres away from the truth and looks like a genuine reading.
SENTINEL = 4000

# Frames are noisy one at a time. A median over this many rejects the
# occasional dropout without smearing a real change, which a mean would not.
FRAMES = 16


def _sensor():
    """Open the lidar.

    The vendor driver is not packaged, so it is imported from wherever the
    repository was cloned. Adding the directory to sys.path rather than
    requiring a particular working directory, because this gets run from the
    rig directory, from cron, and from systemd, and only one of those has a
    predictable cwd.
    """
    vendor = Path.home() / "DFRobot_MatrixLidar" / "python" / "raspberry"
    if not vendor.exists():
        sys.exit(
            f"Driver not found at {vendor}.\n"
            "Clone it with:\n"
            "    git clone https://github.com/DFRobot/DFRobot_MatrixLidar ~/DFRobot_MatrixLidar"
        )
    sys.path.insert(0, str(vendor))

    from DFRobot_MatrixLidar import DFRobot_MatrixLidar_I2C

    lidar = DFRobot_MatrixLidar_I2C(I2C_BUS, I2C_ADDR)
    for _ in range(10):
        if lidar.begin() == 0:
            break
        time.sleep(0.5)
    else:
        sys.exit(
            f"No response from the lidar at {I2C_ADDR:#04x}.\n"
            f"Check it appears in: i2cdetect -y {I2C_BUS}\n"
            "Remember the working wiring is the opposite of the label names --\n"
            "C/R goes to SDA (pin 3), D/T goes to SCL (pin 5)."
        )
    lidar.set_matrix(GRID, GRID)
    return lidar


def _frame(lidar):
    """One 8x8 frame in millimetres, with no-returns as NaN."""
    raw = np.asarray(lidar.get_all_data(), dtype=float).reshape(GRID, GRID)
    raw[raw >= SENTINEL] = np.nan
    return raw


def _measure(lidar):
    """Median of several frames, per cell, ignoring no-returns."""
    stack = []
    for _ in range(FRAMES):
        stack.append(_frame(lidar))
        time.sleep(0.05)
    stack = np.stack(stack)

    with np.errstate(all="ignore"):
        grid = np.nanmedian(stack, axis=0)
    valid = np.isfinite(stack).mean(axis=0)
    return grid, valid


def _plane(grid):
    """Least-squares plane through the grid, in millimetres per cell step.

    Fitted on cell indices rather than real angles on purpose. The reference
    subtraction has already removed the projection, so what remains is a
    difference field, and a difference field is linear in cell index to the
    accuracy anything here needs. Introducing trigonometry would mean
    introducing the field angles, which are exactly what the reference was
    taken to avoid needing.
    """
    ys, xs = np.mgrid[0:GRID, 0:GRID]
    ok = np.isfinite(grid)
    A = np.column_stack([xs[ok].ravel(), ys[ok].ravel(), np.ones(ok.sum())])
    coef, *_ = np.linalg.lstsq(A, grid[ok].ravel(), rcond=None)
    fit = coef[0] * xs + coef[1] * ys + coef[2]
    return coef, grid - fit


def reference():
    """Record the flat baseline. Empty, flat, level tray, boom where it lives."""
    lidar = _sensor()
    grid, valid = _measure(lidar)

    weak = valid < 0.5
    if weak.any():
        print(f"WARNING: {weak.sum()} of {GRID * GRID} zones returned nothing "
              "for most frames.")
        print("Dark, angled or distant surfaces absorb the pulse. Edge zones "
              "are the usual\nculprits -- they look furthest and most "
              "obliquely.")

    if not np.isfinite(grid).all():
        print("\nZones with no reading at all are stored as unusable and "
              "skipped from now on.")

    REFERENCE_FILE.write_text(json.dumps({
        "grid_mm": [[None if not np.isfinite(v) else round(float(v), 1)
                     for v in row] for row in grid],
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
    }, indent=2))

    finite = grid[np.isfinite(grid)]
    print(f"\nDistance to the tray: {finite.mean():.0f} mm mean, "
          f"{finite.min():.0f}-{finite.max():.0f} mm across the grid")
    print("That spread is mostly slant path, not tilt -- outer zones look "
          "outward and\nso measure further to the same plane. It is what the "
          "reference exists to cancel.")
    print(f"\nWritten to {REFERENCE_FILE}")
    print("Re-record this whenever the boom moves. It is only true for the "
          "position it\nwas taken in.")


def read(tolerance):
    """Compare the current scene against the reference."""
    if not REFERENCE_FILE.exists():
        sys.exit(f"No reference at {REFERENCE_FILE}. Run 'reference' first, "
                 "on an empty flat tray.")

    ref = np.array([[np.nan if v is None else v for v in row]
                    for row in json.loads(REFERENCE_FILE.read_text())["grid_mm"]])

    lidar = _sensor()
    grid, _ = _measure(lidar)

    # Nearer than the reference is positive, so a bed of larvae reads as a
    # positive height rather than a negative distance. Depth should not need a
    # sign flip in the reader's head.
    delta = ref - grid

    finite = delta[np.isfinite(delta)]
    if finite.size < GRID * GRID / 2:
        sys.exit("Too few zones returned to say anything. Check nothing is "
                 "blocking the sensor.")

    coef, residual = _plane(delta)
    tilt_x = coef[0] * (GRID - 1)
    tilt_y = coef[1] * (GRID - 1)
    worst = np.nanmax(np.abs(residual))

    print(f"Mean height above reference : {finite.mean():+6.1f} mm")
    print(f"Tilt across the field       : {tilt_x:+6.1f} mm left-right, "
          f"{tilt_y:+6.1f} mm near-far")
    print(f"Worst cell off the plane    : {worst:6.1f} mm")
    print(f"Zones reporting             : {np.isfinite(grid).sum()}/{GRID * GRID}")

    print()
    for row in delta:
        print("  " + " ".join("   ." if not np.isfinite(v) else f"{v:6.1f}"
                              for v in row))

    print()
    if max(abs(tilt_x), abs(tilt_y)) > tolerance:
        print(f"OUT OF TOLERANCE: tilt exceeds {tolerance} mm across the field.")
        print("Either the tray is not sitting flat or the boom has moved. If "
              "it is the boom,\nthe camera calibration went with it -- "
              "re-run focus, measure, flatfield and\nsetcrop, and re-record "
              "this reference afterwards.")
    else:
        print(f"Within tolerance ({tolerance} mm). Geometry unchanged.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("reference", help="record the flat baseline (empty tray)")

    rd = sub.add_parser("read", help="compare the current scene to the reference")
    rd.add_argument(
        "--tolerance", type=float, default=3.0,
        help="millimetres of tilt across the field before complaining "
             "(default 3, about 1 percent of working distance)",
    )

    args = parser.parse_args()
    if args.command == "reference":
        reference()
    else:
        read(args.tolerance)


if __name__ == "__main__":
    main()
