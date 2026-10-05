#!/usr/bin/env python3
"""Flash-lit capture for the inline rig.

The camera (Camera Module 3) has a rolling shutter: rows start and stop
exposing one after another, about 18 ms from top to bottom in the 2304x1296
mode. A flash lasts ~30 us, so it lights only the rows exposing at that
instant. Fire it while *every* row is exposing and the frame is lit evenly;
fire it a moment too late and the frame comes out half lit.

So the exposure is made much longer than the readout (60 ms against ~18 ms),
which leaves a ~40 ms window per frame in which all rows are exposing, and the
flash is aimed at the middle of it. The tote keeps room light out, so the long
exposure collects almost nothing but the flash: measured 2026-10-05, the frame
without flash read 1/255 against ~190 with it.

Where the window sits was measured on the bench rather than assumed: the
SensorTimestamp libcamera reports for a frame is when its first row ends
exposing and is read out, and a flash 15-105 ms before that timestamp lit
the whole frame, while one 6 ms after it lit only the bottom (rig/inline
history, 2026-10-05). Frames come exactly PERIOD apart, so the next frames'
timestamps can be predicted and the flash fired FIRE_BEFORE_MS ahead of one.

Every capture is checked before it is used: lit at all (the flash fired), and
lit evenly (the timing held). A failed check is retried, then reported -- the
loop turns that into a flash fault on the screen rather than sending Caliban a
dark or half-lit photo to judge.

Settings live in ~/inline_camera.json so a calibration can change them without
touching the code; anything missing takes the defaults below.

    python3 flashcam.py                 # one test capture -> ~/captures/inline/TEST.jpg
"""

import json
import os
import time

HOME = os.path.expanduser("~")
SETTINGS_FILE = os.path.join(HOME, "inline_camera.json")

DEFAULTS = {
    "size": [2304, 1296],          # the binned full-sensor mode: whole field, ~18 ms readout
    "exposure_us": 60000,          # >> readout, so every row is exposing for ~40 ms
    "period_us": 100000,
    "fire_before_ms": 25,          # middle of the all-rows window, before the frame timestamp
    "analogue_gain": 1.0,
    "colour_gains": [1.8, 1.6],    # placeholder until a white balance under the flash
    "lens_position": 2.8,          # dioptres = 1 / metres; ~35 cm. Set by calibration.
    "flash_gpio": 17,
    "pulse_ms": 3,
    "recharge_s": 3.0,             # the TT520III at 1/128 is ready well within this
    "attempts": 3,
    # Acceptance: a frame is "flash-lit" when its mean is this much above the
    # frame taken without flash, and "even" when its top and bottom quarters
    # are within this ratio of each other.
    "min_flash_gain": 20.0,
    "even_ratio": 0.8,
}


class FlashNotFired(Exception):
    """No flash in the frame after every attempt: flash off, asleep, batteries
    flat, or the trigger wiring has come loose."""


class FlashTiming(Exception):
    """The flash fired but kept landing outside the all-rows window."""


def load_settings():
    settings = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE) as f:
            settings.update(json.load(f))
    except FileNotFoundError:
        pass
    return settings


def _boottime_ns():
    # SensorTimestamp is CLOCK_BOOTTIME; compare like with like.
    return time.clock_gettime_ns(time.CLOCK_BOOTTIME)


def _brightness(array):
    """(mean, top-quarter mean, bottom-quarter mean) on a coarse grid."""
    g = array[::16, ::16].mean(axis=2)
    q = g.shape[0] // 4
    return float(g.mean()), float(g[:q].mean()), float(g[-q:].mean())


def capture(path, settings=None, log=print):
    """Take one flash-lit photo to `path` (JPEG). Returns a dict of what was
    measured, for the loop to keep and the screen's health chips to use.

    The camera is opened for each capture and closed after, rather than held
    open between readings five minutes apart: it frees it for preview.py and
    calibration in between, and a camera that wedges cannot outlive one capture.
    """
    from gpiozero import DigitalOutputDevice
    from picamera2 import Picamera2
    from libcamera import controls as lc
    from PIL import Image

    s = settings or load_settings()
    period_ns = s["period_us"] * 1000
    flash = DigitalOutputDevice(s["flash_gpio"])
    cam = Picamera2()
    try:
        config = cam.create_still_configuration(
            main={"size": tuple(s["size"]), "format": "RGB888"},
            buffer_count=4,
            controls={
                "ExposureTime": s["exposure_us"],
                "AnalogueGain": s["analogue_gain"],
                "AeEnable": False,
                "AwbEnable": False,
                "ColourGains": tuple(s["colour_gains"]),
                "FrameDurationLimits": (s["period_us"], s["period_us"]),
                "AfMode": lc.AfModeEnum.Manual,
                "LensPosition": s["lens_position"],
            },
        )
        cam.configure(config)
        cam.start()
        time.sleep(1.0)          # controls settle; the first frames are not trusted

        r = cam.capture_request()
        ambient = _brightness(r.make_array("main"))[0]
        r.release()

        fired_any = False
        for attempt in range(1, s["attempts"] + 1):
            r = cam.capture_request()
            ts = r.get_metadata()["SensorTimestamp"]
            r.release()
            # Aim at the first frame whose firing moment is still comfortably
            # ahead. Counted from now, not from "two frames after this one":
            # a request that was waiting in the queue carries a timestamp from
            # the past, and aiming relative to it would aim at a frame that has
            # already gone by.
            lead_ns = s["fire_before_ms"] * 1_000_000
            margin_ns = 5_000_000
            frames_ahead = -(-(_boottime_ns() + margin_ns + lead_ns - ts) // period_ns)
            target_ts = ts + max(1, frames_ahead) * period_ns
            fire_at = target_ts - lead_ns
            while _boottime_ns() < fire_at:
                pass                 # busy-wait: sleep() is too coarse for a ~40 ms window
            flash.on()
            time.sleep(s["pulse_ms"] / 1000)
            flash.off()

            while True:
                r = cam.capture_request()
                if r.get_metadata()["SensorTimestamp"] >= target_ts - period_ns // 2:
                    array = r.make_array("main")
                    r.release()
                    break
                r.release()

            mean, top, bottom = _brightness(array)
            lit = mean - ambient >= s["min_flash_gain"]
            even = lit and min(top, bottom) >= s["even_ratio"] * max(top, bottom)
            fired_any |= lit
            if even:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                Image.fromarray(array[..., ::-1]).save(path, quality=92)
                return {"mean": round(mean, 1), "top": round(top, 1), "bottom": round(bottom, 1),
                        "ambient": round(ambient, 1), "attempts": attempt}
            log(f"flash attempt {attempt}: mean {mean:.0f} (ambient {ambient:.0f}) "
                f"top {top:.0f} bottom {bottom:.0f} -> {'split' if lit else 'no flash'}")
            time.sleep(s["recharge_s"])

        if fired_any:
            raise FlashTiming(f"flash landed outside the exposure window {s['attempts']} times")
        raise FlashNotFired(f"no flash in {s['attempts']} attempts (ambient {ambient:.0f})")
    finally:
        try:
            cam.stop()
        finally:
            cam.close()
            flash.close()


if __name__ == "__main__":
    out = os.path.join(HOME, "captures", "inline", "TEST.jpg")
    print(capture(out))
    print("saved", out)
