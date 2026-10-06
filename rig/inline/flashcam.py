#!/usr/bin/env python3
"""Flash-lit capture for the inline rig.

The camera (Camera Module 3) has a rolling shutter: rows start and stop
exposing one after another. A flash lasts ~30 us, so it lights only the rows
exposing at that instant. Fire it while *every* row is exposing and the frame
is lit evenly; fire it a moment early or late and part of the frame is dark.

The exposure therefore has to be longer than the readout -- but not much
longer, because the chute is never fully dark. Room light is recorded for the
whole exposure while the product moves, and lands on the photo as a smear
over the flash-frozen image. So the rig uses the sensor's fast mode, a
1536x864 binned centre crop read out in ~7.5 ms, with a 15 ms exposure:
a ~7 ms window in which every row is exposing, and a quarter of the room
light a 60 ms exposure collected.

Where the window sits was measured, not assumed (bench, 2026-10-05). The
SensorTimestamp libcamera reports for a frame is when its first row stops
exposing. Swept in 1 ms steps, a flash 3-7 ms before that timestamp lit the
frame evenly; 8 ms just short of it, 10-14 ms lit a shrinking top part --
exactly a 7.5 ms readout. Frames come exactly PERIOD apart, so the next
frames' timestamps can be predicted and the flash fired FIRE_BEFORE_MS
ahead of one, in the middle of the window.

Every capture is checked before it is used: lit at all (the flash fired), and
lit evenly (the timing held). A failed check is retried, then reported -- the
loop turns that into a flash fault on the screen rather than sending Caliban a
dark or half-lit photo to judge.

Settings live in ~/inline_camera.json so a calibration can change them without
touching the code; anything missing takes the defaults below.

    python3 flashcam.py                 # one test capture -> ~/captures/inline/TEST.jpg
    python3 flashcam.py wb              # white balance under the flash, card in the centre
    python3 flashcam.py wb 0.3,0.3,0.7,0.7   # ... or on a region (fractions x0,y0,x1,y1)
"""

import gc
import json
import os
import time

HOME = os.path.expanduser("~")
SETTINGS_FILE = os.path.join(HOME, "inline_camera.json")

DEFAULTS = {
    "size": [1536, 864],           # fast mode: binned centre crop, ~7.5 ms readout
    "sensor_size": [1536, 864],    # forces that sensor mode rather than a scaled full frame
    "exposure_us": 15000,          # readout + ~7 ms in which every row is exposing
    "period_us": 25000,
    "fire_before_ms": 3.5,         # middle of the measured 0-7 ms window
    "analogue_gain": 1.0,
    "colour_gains": [1.8, 1.6],    # placeholder until a white balance under the flash
    "lens_position": 2.8,          # dioptres = 1 / metres; ~35 cm. Set by calibration.
    "flash_gpio": 17,
    # How long the trigger is held closed. The flash fires as it closes, so
    # this does not move the moment of firing -- it only gives a marginal
    # closure longer to register. 3 ms missed intermittently on the bench
    # (2026-10-06) where 20 ms did not.
    "pulse_ms": 20,
    # After a miss, wait this long and fire again, up to `attempts` in all.
    # On the bench (2026-10-06, transistor trigger) about 1 shot in 11 missed
    # at random; 10 s apart, six tries make a whole reading lost to it
    # vanishingly rare, and a flash that is really off is still reported
    # within about a minute.
    "recharge_s": 10.0,
    "attempts": 6,
    # Acceptance: a frame is "flash-lit" when its mean is this much above the
    # frame taken without flash, and "whole" when every one of `bands`
    # horizontal strips got at least `band_min_share` of the best-lit strip's
    # flash. A timing miss leaves strips at room-light level (share ~0); the
    # flash sitting nearer one end of the view only makes a smooth gradient
    # (~0.8 measured 2026-10-06), which must pass. An earlier top-vs-bottom
    # ratio of 0.8 failed exactly that gradient, six times in a row.
    "min_flash_gain": 20.0,
    "bands": 8,
    "band_min_share": 0.35,
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


def _bands(array, n):
    """Mean brightness of n horizontal strips, top to bottom. Rows are what a
    rolling shutter exposes one after another, so a flash that missed part of
    the exposure shows up as whole strips left dark."""
    g = array[::8, ::8].mean(axis=2)
    edges = [round(i * g.shape[0] / n) for i in range(n + 1)]
    return [float(g[edges[i]:edges[i + 1]].mean()) for i in range(n)]


def capture(path, settings=None, log=print, keep=None):
    """Take one flash-lit photo to `path` (JPEG). Returns a dict of what was
    measured, for the loop to keep and the screen's health chips to use.

    The camera is opened for each capture and closed after, rather than held
    open between readings five minutes apart: it frees it for preview.py and
    calibration in between, and a camera that wedges cannot outlive one capture.

    keep: a dict to receive the accepted frame as keep["array"] (BGR, as
    picamera2 delivers "RGB888"), for calibration to measure.
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
            **({"sensor": {"output_size": tuple(s["sensor_size"])}} if s.get("sensor_size") else {}),
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
        dark = r.make_array("main")
        r.release()
        ambient = _brightness(dark)[0]
        amb_bands = _bands(dark, s["bands"])

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
            lead_ns = int(s["fire_before_ms"] * 1_000_000)
            margin_ns = 5_000_000
            frames_ahead = -(-(_boottime_ns() + margin_ns + lead_ns - ts) // period_ns)
            target_ts = ts + max(1, frames_ahead) * period_ns
            fire_at = target_ts - lead_ns
            # The window is ~7 ms wide and aimed at its middle, so a pause of
            # more than ~3.5 ms between the wait ending and the flash firing
            # misses it -- seen on 2026-10-06, the flash visibly firing outside
            # the exposure. Python's garbage collector is the classic source of
            # such a pause, so it is held off for these few milliseconds; the
            # scheduler is the other, which caliban-inline.service handles by
            # running the loop at real-time priority.
            gc.disable()
            try:
                while _boottime_ns() < fire_at:
                    pass             # busy-wait: sleep() is far too coarse for this
                flash.on()
                fired_at = _boottime_ns()
            finally:
                gc.enable()
            time.sleep(s["pulse_ms"] / 1000)
            flash.off()
            late_ms = (fired_at - fire_at) / 1e6

            while True:
                r = cam.capture_request()
                if r.get_metadata()["SensorTimestamp"] >= target_ts - period_ns // 2:
                    array = r.make_array("main")
                    r.release()
                    break
                r.release()

            mean, top, bottom = _brightness(array)
            lit = mean - ambient >= s["min_flash_gain"]
            # The flash's own share in each strip, room light taken off: the
            # light under the tote is not even (brighter where the chute comes
            # in), and left in it would make a half-lit frame look whole.
            shares = [b - a for b, a in zip(_bands(array, s["bands"]), amb_bands)]
            even = lit and min(shares) >= s["band_min_share"] * max(shares)
            fired_any |= lit
            if even:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                Image.fromarray(array[..., ::-1]).save(path, quality=92)
                if keep is not None:
                    keep["array"] = array
                coarse = array[::8, ::8]
                return {"mean": round(mean, 1), "top": round(top, 1), "bottom": round(bottom, 1),
                        "ambient": round(ambient, 1), "attempts": attempt, "late_ms": round(late_ms, 2),
                        # Share of the frame blown out in some channel: detail
                        # there is gone, and pale frass on pale larvae is the
                        # first thing lost. Aim well under a few percent.
                        "clipped_pct": round(float((coarse >= 250).any(axis=2).mean()) * 100, 1),
                        # The share of the photo that is room light, i.e. smear.
                        "ambient_share": round(ambient / mean, 2) if mean else None}
            # How late the trigger went out, against a margin of ~3.5 ms: a
            # large number here means the Pi was busy, not that the flash failed.
            log(f"flash attempt {attempt}: mean {mean:.0f} (ambient {ambient:.0f}) "
                f"strips {' '.join(f'{v:.0f}' for v in shares)} fired {late_ms:+.2f} ms late "
                f"-> {'split' if lit else 'no flash'}")
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


def white_balance(region=(0.3, 0.3, 0.7, 0.7)):
    """Set colour_gains so a white or grey card under the flash comes out
    neutral, and save them to SETTINGS_FILE.

    Measured under the flash because the flash is the light every reading is
    taken in -- a white balance in room light would be for the wrong light.
    On the NoIR camera (no infrared filter) it can only partly correct the
    colour: the flash carries infrared, and how much each material reflects
    differs, so no single pair of gains is right for all of them. Redo it
    whenever the camera, the flash, its diffuser or the tote lining changes.
    """
    s = load_settings()
    x0, y0, x1, y1 = region
    gains = [1.0, 1.0]
    for step in (1, 2):            # measure with flat gains, then confirm with the result
        keep = {}
        capture(os.path.join(HOME, "captures", "inline", "WB.jpg"),
                dict(s, colour_gains=gains), keep=keep)
        a = keep["array"]
        h, w = a.shape[:2]
        patch = a[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)].reshape(-1, 3).mean(axis=0)
        b, g, r = (float(v) for v in patch)
        print(f"pass {step}: card R {r:.0f}  G {g:.0f}  B {b:.0f}  (gains {gains[0]:.2f}, {gains[1]:.2f})")
        if max(r, g, b) > 235:
            print("  the card is close to white-out: the ratio is unreliable. Use a grey card, "
                  "or add diffusion over the flash, and run this again.")
        if step == 1:
            gains = [max(0.5, min(8.0, gains[0] * g / r)), max(0.5, min(8.0, gains[1] * g / b))]
    current = {}
    try:
        with open(SETTINGS_FILE) as f:
            current = json.load(f)
    except FileNotFoundError:
        pass
    current["colour_gains"] = [round(gains[0], 3), round(gains[1], 3)]
    with open(SETTINGS_FILE, "w") as f:
        json.dump(current, f, indent=1)
    print(f"saved colour_gains {current['colour_gains']} to {SETTINGS_FILE}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "wb":
        region = tuple(float(v) for v in sys.argv[2].split(",")) if len(sys.argv) > 2 else (0.3, 0.3, 0.7, 0.7)
        white_balance(region)
    else:
        out = os.path.join(HOME, "captures", "inline", "TEST.jpg")
        print(capture(out))
        print("saved", out)
