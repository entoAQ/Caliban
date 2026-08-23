#!/usr/bin/env python3
"""Bench rig capture for the circular-dish method.

Runs on the Pi 5 with the Waveshare OV5647 NoIR board on a fixed overhead
boom. Two jobs, and the split matters:

  measure       -- let auto-exposure settle on a representative scene, then
                   write what it chose to a settings file.
  whitebalance  -- cancel the NoIR colour cast against the paper in frame.
  capture       -- shoot using exactly those values, every time, forever.

The whole point of the rig over the old webcam is that the webcam ran
everything on auto through the browser, so exposure and white balance drifted
between shots and there was no way to stop them. A model asked to judge "is
that dark patch frass or shadow" cannot do it consistently if the camera
reprocesses every frame differently. So: measure once, lock, and never let
the camera think again.

White balance especially. This is a NoIR sensor -- no IR-cut filter -- so
infrared leaks unevenly across the colour channels and pushes everything
toward magenta under any IR-rich light. That is not correctable by asking for
"auto white balance", because it is out-of-band energy rather than a colour
temperature shift. Locking the gains at least makes the cast *constant*, which
is what the calibration tile in frame is there to reference.

Usage:
    python3 capture.py measure
    python3 capture.py whitebalance
    python3 capture.py capture LOT12345
    python3 capture.py capture LOT12345 --band ir
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from picamera2 import Picamera2

SETTINGS_FILE = Path.home() / "rig_settings.json"
OUTPUT_DIR = Path.home() / "captures"
RESOLUTION = (2592, 1944)

# The sensor needs a few frames before its output is stable, and the AE/AWB
# algorithms need considerably more than that to converge. Two seconds is
# generous for the locked case and about right for measuring.
SETTLE_SECONDS = 2.0


def _start(controls):
    picam2 = Picamera2()
    config = picam2.create_still_configuration(main={"size": RESOLUTION})
    picam2.configure(config)
    picam2.set_controls(controls)
    picam2.start()
    time.sleep(SETTLE_SECONDS)
    return picam2


def measure(ev=0.0):
    """Let the camera decide, then record what it decided.

    Run this once with the rig framed on a filled dish under the lighting you
    intend to keep. Re-run it only if the lighting or geometry changes -- and
    if you do re-run it, understand that images captured before and after are
    no longer strictly comparable.

    `ev` biases the result in photographic stops: -1.0 halves the exposure,
    +1.0 doubles it. Useful when the auto-exposure meters a scene whose
    average is not what matters -- but reach for it sparingly. If something
    bright in frame is clipping, it is usually better to make that thing
    darker than to underexpose the dish, which is the subject.
    """
    picam2 = _start({"AeEnable": True, "AwbEnable": True})
    metadata = picam2.capture_metadata()
    picam2.stop()
    picam2.close()

    exposure = int(metadata["ExposureTime"] * (2.0 ** ev))

    # Update rather than replace. The crop is a property of the rig's geometry,
    # not of its exposure, and silently discarding it here meant re-measuring
    # after a lighting change also un-cropped every capture -- a change nobody
    # asked for, in a different subsystem, with no message to say so.
    settings = json.loads(SETTINGS_FILE.read_text()) if SETTINGS_FILE.exists() else {}
    settings.update({
        "exposure_time": exposure,
        "ev_bias": ev,
        "analogue_gain": float(metadata["AnalogueGain"]),
        "colour_gains": [float(g) for g in metadata["ColourGains"]],
        "measured_at": datetime.now().isoformat(timespec="seconds"),
    })
    # The colour gains just came from AWB, so any previous correction is gone.
    # Drop the timestamp with it rather than leave one that claims otherwise.
    settings.pop("white_balanced_at", None)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))

    print(json.dumps(settings, indent=2))
    print(f"\nWritten to {SETTINGS_FILE}")

    # Analogue gain amplifies noise along with signal. At the sensor's ceiling
    # of 8.0 the image is visibly grainy and colour-smeared, which is exactly
    # the degradation that makes fine material judgements unreliable. The fix
    # is light on the bench, not a setting.
    if settings["analogue_gain"] > 2.0:
        print(
            f"\nWARNING: analogue gain settled at {settings['analogue_gain']:.2f}.\n"
            "The scene is underlit. Add light and re-measure -- you want this\n"
            "near 1.0 for the cleanest image the sensor can give."
        )


def sample(region):
    """Report what a region contains, without changing anything.

    For finding the right --region for `whitebalance`. Guessing at regions by
    running whitebalance itself would rewrite the settings on every attempt,
    each guess building on the last one's correction -- so this exists purely
    to look.
    """
    if not SETTINGS_FILE.exists():
        sys.exit(f"No settings at {SETTINGS_FILE}. Run 'measure' first.")

    settings = json.loads(SETTINGS_FILE.read_text())
    picam2 = _start(
        {
            "AeEnable": False,
            "ExposureTime": settings["exposure_time"],
            "AnalogueGain": settings["analogue_gain"],
            "AwbEnable": False,
            "ColourGains": tuple(settings["colour_gains"]),
        }
    )
    frame = picam2.capture_array()
    picam2.stop()
    picam2.close()

    h, w = frame.shape[:2]
    fx0, fy0, fx1, fy1 = region
    x0, x1 = int(w * fx0), int(w * fx1)
    y0, y1 = int(h * fy0), int(h * fy1)
    patch = frame[y0:y1, x0:x1].astype(float)
    r, g, b = (patch[:, :, i].mean() for i in range(3))

    print(f"Region   {region}  =  pixels x {x0}-{x1}, y {y0}-{y1}")
    print(f"Mean     R={r:.0f} G={g:.0f} B={b:.0f}   peak {patch.max():.0f}/255")

    spread = max(r, g, b) - min(r, g, b)
    if patch.max() >= 250:
        print("\nCLIPPED -- too bright to use as a reference.")
    elif max(r, g, b) < 60:
        print("\nToo dark -- this is not the patch.")
    elif spread > 60:
        print(f"\nStrongly coloured (spread {spread:.0f}). Probably the cardboard\n"
              "or the dish rather than a neutral patch.")
    else:
        print(f"\nUsable. Spread {spread:.0f} -- whitebalance will drive this toward 0.")


def whitebalance(region):
    """Neutralise the IR colour cast against the white paper in the scene.

    Leave the rig exactly as it is for a normal capture -- dish in place,
    paper underneath, lighting untouched -- and run this. It samples a patch
    of the background paper, measures how far the channels are from equal,
    and corrects the gains so that white reads white.

    Sampling the scene's own paper rather than a separately presented card is
    deliberate. A card held up to the lens sits under different light at a
    different distance and clips, and its exposure has nothing to do with the
    exposure real captures use. The paper already in frame is lit exactly as
    the dish is, at the settings the captures will actually run at.

    Auto white balance cannot do this job. AWB corrects for illuminant colour
    temperature and constrains itself to gains plausible for real light
    sources. What this sensor has instead is infrared landing unevenly across
    the channels, mostly inflating red -- not a colour temperature at all, and
    outside the range AWB will reach for. Hence measuring the error directly
    and applying whatever gains cancel it, plausible or not.

    Run after `measure`, which resets the gains to whatever AWB last chose.
    Order matters in that direction only -- `measure` undoes this correction,
    but this does not disturb the exposure `measure` set.
    """
    import numpy as np

    if not SETTINGS_FILE.exists():
        sys.exit(f"No settings at {SETTINGS_FILE}. Run 'measure' first.")

    settings = json.loads(SETTINGS_FILE.read_text())
    current = tuple(settings["colour_gains"])

    # Start at the locked exposure and shorten it until the reference is no
    # longer clipped. The paper is usually blown out at the capture exposure,
    # because that exposure is metered on a scene dominated by a black dish --
    # which is correct for the dish and useless for a white reference.
    #
    # Shortening is safe: colour gains are linear multipliers applied before
    # the gamma curve, so the ratio between channels is the same whatever the
    # shutter time. A darker frame gives the same colour answer with headroom
    # to measure it in.
    exposure = settings["exposure_time"]
    picam2 = _start(
        {
            "AeEnable": False,
            "ExposureTime": exposure,
            "AnalogueGain": settings["analogue_gain"],
            "AwbEnable": False,
            "ColourGains": current,
        }
    )

    fx0, fy0, fx1, fy1 = region

    def sample():
        """Mean R, G, B of the reference region, gamma-encoded 0-255.

        Channel order is RGB despite libcamera calling the format BGR888:
        libcamera names formats in memory-byte order, which comes out reversed
        from numpy's index order, so R sits at index 0.
        """
        time.sleep(0.4)
        picam2.capture_array()  # discard -- controls take a frame to land
        frame = picam2.capture_array()
        h, w = frame.shape[:2]
        x0, x1 = int(w * fx0), int(w * fx1)
        y0, y1 = int(h * fy0), int(h * fy1)
        patch = frame[y0:y1, x0:x1].astype(float)
        return [patch[:, :, i].mean() for i in range(3)], patch.max()

    (rgb, peak) = sample()
    if peak < 60:
        picam2.stop()
        picam2.close()
        sys.exit(
            f"Reference is too dark (peak {peak:.0f}/255).\n"
            "The sampled region is probably on the dish rather than the paper.\n"
            f"Region sampled was {region}. Pass --region x0,y0,x1,y1 as\n"
            "fractions of the frame to point it at clean paper instead."
        )

    # Shorten the exposure until the reference stops clipping, then hold it
    # there for the whole convergence. The paper is normally blown out at the
    # capture exposure -- that exposure is metered on a scene dominated by a
    # black dish, correct for the dish and useless for a white reference.
    # Holding it fixed afterwards matters: if exposure moved between passes it
    # would be a second variable confounding the one being solved for.
    while peak >= 250 and exposure > 200:
        exposure = int(exposure / 2)
        picam2.set_controls({"ExposureTime": exposure})
        (rgb, peak) = sample()

    if peak >= 250:
        picam2.stop()
        picam2.close()
        sys.exit(
            "Reference still clipped at minimum exposure. The sampled region\n"
            "is probably under a direct highlight -- try --region somewhere\n"
            "more evenly lit."
        )

    print(f"Measured at      {exposure} us "
          f"(captures use {settings['exposure_time']} us)")
    print(f"Region sampled   {region}\n")

    # Converge rather than solve in one step. The obvious approach -- scale
    # each gain by how far its channel is from green -- assumes the channels
    # are independent, and they are not: the ISP applies a colour correction
    # matrix tuned for this sensor, which mixes all three on the way out. So
    # changing the red gain moves green and blue too, and a full-size
    # correction overshoots and oscillates. Damping the step and repeating
    # converges on the fixed point without needing to model the matrix.
    #
    # Linearising before comparing, because gains are linear multipliers
    # applied upstream of the gamma curve. Per pixel rather than on the means,
    # since the average of a curve is not the curve of an average.
    gains = list(current)
    DAMPING = 0.5

    for step in range(12):
        spread = max(rgb) - min(rgb)
        print(f"  pass {step}: R={rgb[0]:6.1f} G={rgb[1]:6.1f} B={rgb[2]:6.1f}"
              f"   spread {spread:5.1f}   gains {gains[0]:.3f}, {gains[1]:.3f}")
        if spread < 3.0:
            break

        lin = [(v / 255.0) ** 2.2 for v in rgb]
        gains = [
            float(np.clip(gains[0] * (lin[1] / lin[0]) ** DAMPING, 0.05, 32.0)),
            float(np.clip(gains[1] * (lin[1] / lin[2]) ** DAMPING, 0.05, 32.0)),
        ]
        picam2.set_controls({"ColourGains": tuple(gains)})
        (rgb, peak) = sample()

    picam2.stop()
    picam2.close()

    settings["colour_gains"] = gains
    settings["white_balanced_at"] = datetime.now().isoformat(timespec="seconds")
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))

    print(f"\nColour gains     {current[0]:.2f}, {current[1]:.2f}"
          f"  ->  {gains[0]:.2f}, {gains[1]:.2f}")
    print(f"\nWritten to {SETTINGS_FILE}")


def setcrop(region):
    """Record the crop applied to saved captures.

    A fixed crop rather than finding the dish in each frame. The box holds the
    dish in one place, so the dish is in one place -- and a constant is
    trustworthy in a way that a detector is not: circle-finding fails
    occasionally and silently, and a mangled frame in the middle of a corpus
    is worse than no crop at all.

    Trimming the background is worth doing. Those pixels are irrelevant to the
    estimate, they survive into whatever the vision model is sent, and after
    downscaling they cost resolution that would otherwise land on the sample.

    Only saved captures are cropped. `sample` and `whitebalance` still see the
    whole sensor frame, so the reference patch can sit outside the crop and
    keep working.
    """
    if not SETTINGS_FILE.exists():
        sys.exit(f"No settings at {SETTINGS_FILE}. Run 'measure' first.")
    settings = json.loads(SETTINGS_FILE.read_text())
    settings["crop"] = list(region)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))

    w = int(RESOLUTION[0] * (region[2] - region[0]))
    h = int(RESOLUTION[1] * (region[3] - region[1]))
    print(f"Crop set to {region}  ->  {w}x{h} px")
    print("\nLeave the dish rim visible: the prompt asks the model to judge "
          "only\ninside the dish, which it can only do if it can see the edge.")


def capture(lot_number, band):
    """Shoot one frame with the locked settings."""
    if not SETTINGS_FILE.exists():
        sys.exit(f"No settings at {SETTINGS_FILE}. Run 'measure' first.")

    settings = json.loads(SETTINGS_FILE.read_text())
    picam2 = _start(
        {
            "AeEnable": False,
            "ExposureTime": settings["exposure_time"],
            "AnalogueGain": settings["analogue_gain"],
            "AwbEnable": False,
            "ColourGains": tuple(settings["colour_gains"]),
        }
    )

    OUTPUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = OUTPUT_DIR / f"{lot_number}_{stamp}_{band}.jpg"

    crop = settings.get("crop")
    if crop:
        from PIL import Image

        frame = picam2.capture_array()
        h, w = frame.shape[:2]
        x0, x1 = int(w * crop[0]), int(w * crop[2])
        y0, y1 = int(h * crop[1]), int(h * crop[3])
        # Quality 95 rather than the default: this image is the measurement,
        # and JPEG artefacts land hardest on exactly the fine dark detail
        # being judged.
        Image.fromarray(frame[y0:y1, x0:x1]).save(str(path), quality=95)
    else:
        picam2.capture_file(str(path))

    picam2.stop()
    picam2.close()

    print(path)
    return str(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    mea = sub.add_parser("measure", help="settle on auto and record the values")
    mea.add_argument(
        "--ev",
        type=float,
        default=0.0,
        help="exposure bias in stops; -1 halves, +1 doubles (default: 0)",
    )
    smp = sub.add_parser("sample", help="report what a region contains (changes nothing)")
    smp.add_argument("--region", required=True, help="x0,y0,x1,y1 as fractions of the frame")

    crp = sub.add_parser("setcrop", help="set the crop applied to saved captures")
    crp.add_argument("--region", required=True, help="x0,y0,x1,y1 as fractions of the frame")

    wb = sub.add_parser(
        "whitebalance", help="cancel the IR cast against the paper in frame"
    )
    # Default is a strip up the left edge, clear of both the dish and the
    # corners -- corner vignetting is strong enough on this lens to skew a
    # reference taken there.
    wb.add_argument(
        "--region",
        default="0.02,0.30,0.12,0.70",
        help="x0,y0,x1,y1 as fractions of the frame (default: left edge strip)",
    )

    cap = sub.add_parser("capture", help="shoot with the locked values")
    cap.add_argument("lot_number")
    # Named rather than inferred: nothing on this board reports whether the
    # IR LEDs are lit, so the operator asserting which band this is remains
    # the only reliable source of that fact.
    cap.add_argument(
        "--band",
        default="visible",
        choices=["visible", "ir"],
        help="which band this frame is (default: visible)",
    )

    args = parser.parse_args()

    def parsed_region():
        region = tuple(float(v) for v in args.region.split(","))
        if len(region) != 4:
            sys.exit("--region needs four comma-separated fractions: x0,y0,x1,y1")
        return region

    if args.command == "measure":
        measure(args.ev)
    elif args.command == "sample":
        sample(parsed_region())
    elif args.command == "setcrop":
        setcrop(parsed_region())
    elif args.command == "whitebalance":
        whitebalance(parsed_region())
    else:
        capture(args.lot_number, args.band)


if __name__ == "__main__":
    main()
