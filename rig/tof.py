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

It also measures bulk density, which is the one thing here that is a real
measurement rather than a check: mass over a physically sensed volume. What it
is a measurement *of* depends on how the sample is presented, and that matters
more than the arithmetic. Poured and settled, the envelope volume is what bulk
density conventionally means and the number is comparable with the lab's.
Scattered thinly for photography, the envelope is mostly air and the number
comes out far lower -- still consistent, still regressable, but a different
quantity wearing the same name. Do not mix the two in one dataset.

Depth decides whether it is worth doing at all. Height precision is about
2.5mm, so a 6mm scattered layer carries 40 percent error and a 40mm poured bed
carries 6.

Usage:
    python3 tof.py reference          # empty, flat, level tray -- once
    python3 tof.py area --volume-ml 500   # known volume in the tray -- once
    python3 tof.py read               # anything: tray, sample, nothing
    python3 tof.py density --mass-g 250
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

# The sensor drifts as it warms, and not evenly across its zones -- measured
# here as a whole-field shift of ~2.7mm plus a band of ~4mm along one edge,
# between a reading taken seconds after power-up and one a minute later. That
# is larger than the tilt this tool exists to detect.
#
# It only matters for the reference, which is taken once and then compared
# against for months. A cold reference bakes the warm-up into every later
# reading as a permanent false tilt. `read` does not wait, because by the time
# anyone is reading the sensor has been up for a while.
WARMUP_SECONDS = 90

# Measured on this rig: four repeat reads of an untouched tray agreed on mean
# height to about 0.4mm, but a reading taken against a reference from a
# separate run sat 2.5mm off. The between-run figure is the honest one, since
# that is how a density measurement is actually taken.
HEIGHT_PRECISION_MM = 2.5


def _sensor():
    """Open the lidar.

    The vendor driver is not packaged, so it is imported from wherever the
    repository was cloned. Adding the directory to sys.path rather than
    requiring a particular working directory, because this gets run from the
    rig directory, from cron, and from systemd, and only one of those has a
    predictable cwd.
    """
    root = Path.home() / "DFRobot_MatrixLidar"
    if not root.exists():
        sys.exit(
            f"Driver not found at {root}.\n"
            "Clone it with:\n"
            "    git clone https://github.com/DFRobot/DFRobot_MatrixLidar ~/DFRobot_MatrixLidar"
        )

    # Both the repository root and the module's own directory. The shipped
    # examples import as `python.raspberry.DFRobot_matrixLidar`, which only
    # resolves from the repository root, but that form leaves the package
    # prefix dependent on where the clone happens to sit. Try the plain module
    # first and fall back, so either layout works.
    sys.path.insert(0, str(root / "python" / "raspberry"))
    sys.path.insert(0, str(root))
    try:
        from DFRobot_matrixLidar import DFRobot_matrixLidar_i2c
    except ImportError:
        from python.raspberry.DFRobot_matrixLidar import DFRobot_matrixLidar_i2c

    lidar = DFRobot_matrixLidar_i2c(I2C_ADDR)
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

    # 8 means the 8x8 grid. At 4x4 the zones are wider and the reference would
    # not transfer, so this is not a setting to vary casually.
    for _ in range(10):
        if lidar.set_Ranging_Mode(GRID) == 0:
            break
        time.sleep(0.5)
    else:
        sys.exit(f"Lidar answered but would not enter {GRID}x{GRID} mode.")

    return lidar


def _frame(lidar):
    """One 8x8 frame in millimetres, with no-returns as NaN.

    get_all_data returns bytes, not distances: each zone is a little-endian
    16-bit pair, so 64 zones arrive as 128 bytes. Reassembling them is the
    caller's job, which is easy to miss -- the raw list looks like plausible
    readings if you do not notice it is twice as long as it should be.
    """
    raw = np.asarray(lidar.get_all_data(), dtype=np.uint8)
    if raw.size != GRID * GRID * 2:
        raise RuntimeError(
            f"Expected {GRID * GRID * 2} bytes from the lidar, got {raw.size}."
        )
    mm = (raw[1::2].astype(np.uint16) << 8) | raw[0::2]
    out = mm.astype(float).reshape(GRID, GRID)
    out[out >= SENTINEL] = np.nan
    return out


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

    print(f"Warming up for {WARMUP_SECONDS}s before measuring.")
    deadline = time.time() + WARMUP_SECONDS
    while time.time() < deadline:
        _frame(lidar)  # keep it ranging, which is what actually warms it
        time.sleep(0.2)

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


def _load_reference():
    if not REFERENCE_FILE.exists():
        sys.exit(f"No reference at {REFERENCE_FILE}. Run 'reference' first, "
                 "on an empty flat tray.")
    stored = json.loads(REFERENCE_FILE.read_text())
    grid = np.array([[np.nan if v is None else v for v in row]
                     for row in stored["grid_mm"]])
    return stored, grid


def _height(lidar, ref):
    """Mean height of whatever is on the tray, above the reference plane.

    Mean over the whole sensed field, not over the covered part of it. That is
    deliberate and it is what makes partial coverage harmless: mean height
    times sensed area is the true volume of material above the plane whether
    it is spread thinly or heaped in one corner, because the bare zones
    contribute a genuine zero rather than a missing value.
    """
    grid, _ = _measure(lidar)
    delta = ref - grid
    finite = delta[np.isfinite(delta)]
    if finite.size < GRID * GRID / 2:
        sys.exit("Too few zones returned to say anything. Check nothing is "
                 "blocking the sensor.")
    return float(finite.mean()), delta


def area(volume_ml):
    """Calibrate how much tray the sensor actually sees, from a known volume.

    The alternative was computing it from the sensor's field of view and the
    mounting height, which needs the exact per-zone angles and the exact
    mounting angle -- the same two things the flat reference exists to avoid
    needing. So it is measured the same way: pour in a known volume, read the
    height it produces, and the area follows. That single number absorbs the
    field of view, the working distance and any mounting tilt at once.

    Use something that lies flat and fills the field -- water in a shallow
    tray, or rice levelled off. A heap in the middle gives the same volume and
    the same answer in principle, but leaves the outer zones reading zero,
    where the noise is proportionally largest.
    """
    stored, ref = _load_reference()
    lidar = _sensor()
    height, _ = _height(lidar, ref)

    if height < 5.0:
        sys.exit(
            f"Only {height:.1f} mm of material. Height precision is around\n"
            "2.5 mm, so this would put a large error straight into the area\n"
            "constant and from there into every density afterwards. Use a\n"
            "deeper layer -- 20 mm or more."
        )

    area_mm2 = (volume_ml * 1000.0) / height
    stored["sensed_area_mm2"] = round(area_mm2, 0)
    stored["area_calibrated_at"] = datetime.now().isoformat(timespec="seconds")
    REFERENCE_FILE.write_text(json.dumps(stored, indent=2))

    print(f"Mean height   : {height:.1f} mm")
    print(f"Sensed area   : {area_mm2 / 100:.0f} cm2 "
          f"({area_mm2 ** 0.5:.0f} mm square equivalent)")
    print(f"Written to {REFERENCE_FILE}")


def density(mass_g):
    """Bulk density from mass and the volume the sensor measures.

    This is a real measurement, unlike the vision estimate: mass over volume,
    with the volume physically sensed. What it is a measurement *of* depends
    entirely on how the sample is presented, and that distinction matters more
    than the arithmetic.

    Poured and left to settle, the envelope volume is what bulk density
    conventionally means, and this number is comparable with the lab's.
    Scattered thinly for photography, the envelope is mostly air, so the
    number comes out far lower -- still consistent, still regressable against
    lab values, but a different quantity wearing the same name. Do not mix the
    two in one dataset.

    Depth is what decides whether it works at all. Height precision is about
    2.5 mm, so a 6 mm scattered layer carries 40% error and a 40 mm poured bed
    carries 6%.
    """
    stored, ref = _load_reference()
    area_mm2 = stored.get("sensed_area_mm2")
    if not area_mm2:
        sys.exit("No area calibration. Run 'area --volume-ml N' once, with a "
                 "known volume in the tray.")

    lidar = _sensor()
    height, delta = _height(lidar, ref)

    volume_ml = height * area_mm2 / 1000.0
    if volume_ml <= 0:
        sys.exit("Measured volume is zero or negative. Is there anything in "
                 "the tray, and is the reference still valid?")

    density_g_l = mass_g / (volume_ml / 1000.0)

    print(f"Mean depth    : {height:6.1f} mm")
    print(f"Volume        : {volume_ml:6.1f} mL")
    print(f"Mass          : {mass_g:6.1f} g")
    print(f"Bulk density  : {density_g_l:6.0f} g/L")

    # Precision is dominated by the height term; mass and area are far better
    # known. Stating it as a percentage is what stops the number being read as
    # more exact than it is.
    error_pct = 100.0 * HEIGHT_PRECISION_MM / height
    print(f"\nUncertainty   : about {error_pct:.0f}% "
          f"(±{HEIGHT_PRECISION_MM} mm on {height:.1f} mm of depth)")
    if error_pct > 15:
        print(
            "\nThat is too coarse to be worth much. The sample is too shallow:\n"
            "depth is the whole game here, and the fix is pouring it into a\n"
            "heap or a smaller container rather than reading a thin scatter."
        )


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

    ar = sub.add_parser("area", help="calibrate the sensed area from a known volume")
    ar.add_argument("--volume-ml", type=float, required=True,
                    help="volume currently in the tray, in millilitres")

    dn = sub.add_parser("density", help="bulk density from mass and sensed volume")
    dn.add_argument("--mass-g", type=float, required=True,
                    help="mass of the material in the tray, in grams")

    rd = sub.add_parser("read", help="compare the current scene to the reference")
    rd.add_argument(
        "--tolerance", type=float, default=3.0,
        help="millimetres of tilt across the field before complaining "
             "(default 3, about 1 percent of working distance)",
    )

    args = parser.parse_args()
    if args.command == "reference":
        reference()
    elif args.command == "area":
        area(args.volume_ml)
    elif args.command == "density":
        density(args.mass_g)
    else:
        read(args.tolerance)


if __name__ == "__main__":
    main()
