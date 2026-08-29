#!/usr/bin/env python3
"""Bench rig capture for the circular-dish method.

Runs on the Pi 5 with a Camera Module 3 (imx708) on a fixed overhead boom.
Jobs, and the split matters:

  focus         -- run autofocus once, then record and hold the lens position.
  measure       -- let auto-exposure settle on a representative scene, then
                   write what it chose to a settings file.
  whitebalance  -- cancel the colour cast against the paper in frame.
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

import numpy as np
from libcamera import controls as libcontrols
from picamera2 import Picamera2

SETTINGS_FILE = Path.home() / "rig_settings.json"
OUTPUT_DIR = Path.home() / "captures"

# The measured illumination pattern. Kept beside the settings rather than in
# them: it is thousands of numbers, and rig_settings.json is meant to stay
# something you can read and sanity-check with cat.
FLATFIELD_FILE = Path.home() / "rig_flatfield.npy"

# The flat field is stored as a GRID x GRID map. Coarse on purpose -- what is
# being measured varies smoothly across the whole frame, so anything finer than
# this is the reference surface's own texture and the sensor's noise, and
# storing it would print this particular plate into every future capture.
GRID = 24
# The imx708's native array is 16:9, not the 4:3 the OV5647 gave. There is no
# full-sensor 4:3 mode to fall back to, so the framing genuinely changes with
# the camera -- every stored crop, region and exposure predates this and must
# be recalibrated rather than carried over.
RESOLUTION = (4608, 2592)

# The sensor needs a few frames before its output is stable, and the AE/AWB
# algorithms need considerably more than that to converge. Two seconds is
# generous for the locked case and about right for measuring.
SETTLE_SECONDS = 2.0


def _focus_controls(picam2):
    """Hold the lens where `focus` left it, if this camera has a lens to hold.

    Module 3 autofocuses; the OV5647 it replaced could not. An autofocus camera
    left to itself refocuses between shots, which is a drift the old camera was
    incapable of: two captures of the same tray minutes apart can differ in
    precisely the thing being judged -- how sharply fine dark material resolves
    against the substrate. Sharpness is not a nuisance variable here, it is the
    signal.

    So focus gets the same treatment as exposure and white balance: decide
    once, record it, never let the camera think again.

    Returns nothing if the sensor is fixed-focus (setting AfMode on one is an
    error, not a no-op), or if `focus` has not been run -- in which case the
    camera is left on its default rather than pinned to a guess.
    """
    if "LensPosition" not in picam2.camera_controls:
        return {}

    settings = json.loads(SETTINGS_FILE.read_text()) if SETTINGS_FILE.exists() else {}
    position = settings.get("lens_position")
    if position is None:
        return {}

    return {
        "AfMode": libcontrols.AfModeEnum.Manual,
        "LensPosition": float(position),
    }


def _start(controls):
    picam2 = Picamera2()
    config = picam2.create_still_configuration(main={"size": RESOLUTION})
    picam2.configure(config)
    merged = dict(controls)
    merged.update(_focus_controls(picam2))
    picam2.set_controls(merged)
    picam2.start()
    time.sleep(SETTLE_SECONDS)
    return picam2


def focus():
    """Run one autofocus cycle and record where the lens landed.

    Run this before `measure`, and re-run it only if the boom height changes.
    Everything downstream -- the exposure, the white balance region, the crop --
    is measured through this focus, so changing it invalidates them in the same
    way moving the camera does.

    LensPosition is in dioptres: reciprocal metres, so 0 is infinity and 4.0 is
    250mm. That reciprocal is worth keeping in mind, because it means precision
    is not uniform. Near the close end a small dioptre error is a small distance
    error; near infinity the same error is metres. At boom distance we are at
    the forgiving end of that curve.
    """
    picam2 = Picamera2()
    config = picam2.create_still_configuration(main={"size": RESOLUTION})
    picam2.configure(config)
    picam2.start()
    time.sleep(SETTLE_SECONDS)

    if "LensPosition" not in picam2.camera_controls:
        picam2.stop()
        picam2.close()
        sys.exit(
            "This camera is fixed-focus -- there is no lens position to set.\n"
            "Nothing to do, and nothing wrong: focus is already constant."
        )

    picam2.set_controls({"AfMode": libcontrols.AfModeEnum.Auto})
    converged = picam2.autofocus_cycle()
    position = float(picam2.capture_metadata()["LensPosition"])
    picam2.stop()
    picam2.close()

    if not converged:
        sys.exit(
            "Autofocus did not converge. It needs contrasty detail to work on,\n"
            "so an evenly-filled tray of one material can defeat it. Put a\n"
            "printed target or a ruler on the surface at tray height, run this\n"
            "again, then remove it before capturing."
        )

    settings = json.loads(SETTINGS_FILE.read_text()) if SETTINGS_FILE.exists() else {}
    settings.update({
        "lens_position": position,
        "focused_at": datetime.now().isoformat(timespec="seconds"),
    })
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))

    distance = f"{1.0 / position:.3f}m" if position > 0 else "infinity"
    print(f"Lens position {position:.3f} dioptres (~{distance})")
    print(f"Written to {SETTINGS_FILE}")

    # The lens has physical stops. Landing on one usually means the subject is
    # outside the focus range rather than that the range happens to end exactly
    # at the right place -- worth saying, because the image can still look
    # plausible on a small preview while never actually being in focus.
    if position <= 0.05:
        print(
            "\nWARNING: focused at infinity. At boom distance that is almost\n"
            "certainly the lens giving up rather than the right answer."
        )


def measure(ev=0.0, reset_white_balance=False):
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
        "measured_at": datetime.now().isoformat(timespec="seconds"),
    })

    # A white balance already made against a known neutral surface outranks
    # anything AWB guesses from a tray of larvae. White balance describes the
    # light, not the exposure, so re-metering is no reason to throw it away --
    # and discarding it forced the daily calibration into an awkward order,
    # because the bare tray needed for a correction is gone by the time there
    # is a filled tray to meter.
    if reset_white_balance or "white_balanced_at" not in settings:
        settings["colour_gains"] = [float(g) for g in metadata["ColourGains"]]
        settings.pop("white_balanced_at", None)
    else:
        print(f"Keeping the white balance from "
              f"{settings['white_balanced_at']}. Pass --reset-white-balance "
              f"to let AWB decide instead.")

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
    # Corrected rather than raw, so this reports what a capture would actually
    # contain. Reading raw would make it impossible to check the flat field
    # with the same tool that found the problem.
    frame = _apply_flatfield(picam2.capture_array())
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
        frame = _apply_flatfield(picam2.capture_array())
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
    # How neutral it actually got, not merely that it ran. The loop gives up
    # after twelve passes whether or not it converged, so without this a
    # stubbornly coloured reference and a perfect one leave identical settings
    # files -- and the calibration stage has no way to tell the operator which
    # one they have.
    settings["white_balance_spread"] = round(float(max(rgb) - min(rgb)), 2)
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


def flatfield():
    """Measure the ring's illumination pattern and store a correction for it.

    A ring light lights an annulus. Directly beneath its centre the only light
    arriving comes in at a shallow angle from the rim, so the middle of the
    field sits measurably darker than the edges -- on this rig, 22% down on the
    corners, and colour-shifted with it, because proportionally more of what
    reaches the centre is bounce rather than direct.

    That is not a defect to be fixed by moving something. It is what a ring at
    working distance does, and the only physical cure is raising it far enough
    that the tray is effectively lit from a point, which costs the working
    distance and the light that made a ring attractive in the first place.

    So it gets measured instead. Fill the frame with one uniform matte surface,
    run this, and whatever variation comes back can only be the illumination --
    which makes it correctable. This is the same discipline as a flat frame in
    astronomy, and it is sound for the same reason: the correction is measured
    rather than guessed, and it is re-measured whenever the geometry changes.

    Why it matters here specifically: an illumination gradient is one of the
    very few errors that rotation-averaging cannot touch. Rotating the image
    leaves the lamp where it is, so the dim centre stays over the same part of
    the sample in all four rotations and every one of them is wrong in the same
    direction. Averaging four identical biases just gives the bias back.
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
    frame = picam2.capture_array().astype(np.float32)
    picam2.stop()
    picam2.close()

    h, w = frame.shape[:2]
    if frame.max() >= 250:
        sys.exit(
            "The reference surface is clipping. A clipped pixel has lost the\n"
            "very brightness difference this is trying to measure, so the map\n"
            "would read flat exactly where the field is brightest.\n"
            "Re-run 'measure --ev -0.5' and try again."
        )
    if frame.mean() < 40:
        sys.exit(
            "The reference surface is very dark. Sensor noise would dominate\n"
            "the pattern being measured. Use a pale matte surface and enough\n"
            "light that 'measure' settles near gain 1.0."
        )

    # Block-average down to a coarse grid before doing anything else. The thing
    # being measured is illumination, which varies smoothly across the frame;
    # everything sharper than this grid is the surface's own texture, its
    # scratches and marks, and sensor noise. Keeping any of that would bake a
    # negative print of this particular plate into every future capture.
    gh, gw = h // GRID, w // GRID
    coarse = frame[: gh * GRID, : gw * GRID]
    coarse = coarse.reshape(GRID, gh, GRID, gw, 3).mean(axis=(1, 3))

    # Normalise per channel. Correcting each channel separately also cancels
    # the centre's colour shift, which a single luminance map would leave
    # behind -- and that shift is real: the centre is not merely dimmer, it is
    # lit by different light.
    gains = coarse.mean(axis=(0, 1)) / coarse

    # A flat field corrects a gentle bowl, not a hole. Anything asking for more
    # than this is not illumination -- it is a shadow, an obstruction, or a
    # surface that was not uniform after all, and scaling it up would amplify
    # noise into a region that has no signal to recover.
    extreme = float(gains.max())
    gains = np.clip(gains, 0.5, 2.0)

    np.save(FLATFIELD_FILE, gains.astype(np.float32))
    settings["flat_fielded_at"] = datetime.now().isoformat(timespec="seconds")
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))

    print(f"Illumination range across the frame: {coarse.mean(axis=2).min():.0f}"
          f" to {coarse.mean(axis=2).max():.0f} of 255")
    print(f"Largest correction: x{extreme:.2f}")
    print(f"Written to {FLATFIELD_FILE}")

    if extreme > 2.0:
        print(
            "\nWARNING: something needed more than a doubling, which was\n"
            "clipped. That is past what illumination alone explains -- look\n"
            "for an obstruction, or a reference surface that is not uniform."
        )


def _apply_flatfield(frame):
    """Divide out the measured illumination pattern, if one has been measured.

    The map is stored coarse and stretched back up here. Bilinear is right for
    that: illumination genuinely is smooth, so interpolating between measured
    points is a fair reconstruction rather than an invention.

    Silently does nothing when no map exists. That is deliberate -- the rig has
    to keep working before this has been run, and a missing flat field degrades
    the result rather than invalidating it.
    """
    if not FLATFIELD_FILE.exists():
        return frame

    from PIL import Image

    gains = np.load(FLATFIELD_FILE)
    h, w = frame.shape[:2]
    out = np.empty_like(frame)

    # A channel at a time. The full-size float map is 46MB per channel on this
    # sensor, and holding three of them alongside the frame is enough to matter
    # on a Pi that is also running the poller.
    for c in range(3):
        full = Image.fromarray(gains[:, :, c], mode="F").resize((w, h), Image.BILINEAR)
        corrected = frame[:, :, c].astype(np.float32) * np.asarray(full)
        out[:, :, c] = np.clip(corrected, 0, 255).astype(np.uint8)

    return out


# The centre of the frame, which on an empty tray is guaranteed to be tray.
# Fixed rather than chosen, because a daily procedure that needs a judgement
# call is a daily procedure that drifts.
WB_REGION = (0.35, 0.35, 0.65, 0.65)


def _state():
    """What the rig currently believes, in a form a browser can render.

    Calibration used to be judged by reading the console. Behind a button
    nobody is reading a console, so every stage has to hand back enough for
    the page to say whether it passed and, when it did not, what to change on
    the bench.
    """
    settings = json.loads(SETTINGS_FILE.read_text()) if SETTINGS_FILE.exists() else {}
    return {
        "exposure_time": settings.get("exposure_time"),
        "analogue_gain": settings.get("analogue_gain"),
        "colour_gains": settings.get("colour_gains"),
        "white_balance_spread": settings.get("white_balance_spread"),
        "white_balanced_at": settings.get("white_balanced_at"),
        "lens_position": settings.get("lens_position"),
        "focused_at": settings.get("focused_at"),
        "flat_fielded_at": settings.get("flat_fielded_at"),
        "flat_field_present": FLATFIELD_FILE.exists(),
        "crop": settings.get("crop"),
    }


def calib_empty():
    """Stage one: everything the empty tray defines.

    Metered down half a stop first. A bare pale tray under any other exposure
    tends to clip, and a clipped pixel has lost the very brightness difference
    the flat field exists to measure.
    """
    measure(ev=-0.5, reset_white_balance=True)
    flatfield()
    whitebalance(WB_REGION)
    return _state()


def calib_focus():
    """Stage two: lock focus on the printed sheet."""
    focus()
    return _state()


def calib_filled():
    """Stage three: expose for the real subject.

    Keeps stage one's white balance, which describes the light rather than the
    exposure -- and a correction measured against a bare tray beats anything
    AWB guesses from a frame full of tan larvae.
    """
    measure()
    return _state()


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

    from PIL import Image

    frame = _apply_flatfield(picam2.capture_array())

    crop = settings.get("crop")
    if crop:
        h, w = frame.shape[:2]
        x0, x1 = int(w * crop[0]), int(w * crop[2])
        y0, y1 = int(h * crop[1]), int(h * crop[3])
        frame = frame[y0:y1, x0:x1]

    # Quality 95 rather than the default: this image is the measurement, and
    # JPEG artefacts land hardest on exactly the fine dark detail being judged.
    Image.fromarray(frame).save(str(path), quality=95)

    picam2.stop()
    picam2.close()

    print(path)
    return str(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("focus", help="autofocus once, then record and hold the lens position")

    sub.add_parser(
        "flatfield",
        help="measure the ring's illumination pattern from a uniform surface",
    )

    # The three daily calibration stages. Exposed as subcommands rather than
    # left inside calibrate.sh so the bench script and the SGSC button run the
    # same code -- two implementations of a calibration would drift, and the
    # drift would show up as the button quietly disagreeing with the bench.
    for name, helptext in (
        ("calib-empty", "stage 1: flat field and white balance from an empty tray"),
        ("calib-focus", "stage 2: lock focus on the calibration sheet"),
        ("calib-filled", "stage 3: expose for the filled tray"),
    ):
        p_ = sub.add_parser(name, help=helptext)
        p_.add_argument("--json", action="store_true",
                        help="print the resulting state as JSON")

    mea = sub.add_parser("measure", help="settle on auto and record the values")
    mea.add_argument(
        "--ev",
        type=float,
        default=0.0,
        help="exposure bias in stops; -1 halves, +1 doubles (default: 0)",
    )
    mea.add_argument(
        "--reset-white-balance",
        action="store_true",
        help="discard an existing white balance and take AWB's guess instead",
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

    if args.command in ("calib-empty", "calib-focus", "calib-filled"):
        state = {
            "calib-empty": calib_empty,
            "calib-focus": calib_focus,
            "calib-filled": calib_filled,
        }[args.command]()
        if args.json:
            print(json.dumps(state, indent=2))
    elif args.command == "focus":
        focus()
    elif args.command == "flatfield":
        flatfield()
    elif args.command == "measure":
        measure(args.ev, args.reset_white_balance)
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
