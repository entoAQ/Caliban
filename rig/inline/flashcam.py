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
    python3 flashcam.py focus           # sweep the lens under the flash, keep the sharpest
    python3 flashcam.py focus-shots     # centre crops at a few lens positions, to compare by eye
    python3 flashcam.py sweep           # measure the firing window for this camera and save it
"""

import gc
import json
import os
import time

HOME = os.path.expanduser("~")
SETTINGS_FILE = os.path.join(HOME, "inline_camera.json")

# Per camera, by sensor. Each is the camera's fast mode: a short readout lets
# the exposure stay short, which keeps room-light smear down. fire_before_ms
# is where the flash lands in the all-rows window; `flashcam.py sweep`
# measures it and saves the middle of the window to the settings file.
CAMERA_DEFAULTS = {
    # Camera Module 3 (any variant). 1536x864 binned centre crop, ~7.5 ms
    # readout; 15 ms exposure gives a ~7 ms window, measured 2026-10-05.
    "imx708": {
        "size": [1536, 864], "sensor_size": [1536, 864],
        "exposure_us": 15000, "period_us": 25000, "fire_before_ms": 3.5,
        "colour_gains": [1.8, 1.6],
        "lens_position": 2.8,      # dioptres = 1 / metres; set by `focus`
    },
    # HQ Camera. 1332x990 binned crop (the 120 fps mode), readout under
    # ~8.3 ms. Timing not yet measured on this camera: run `sweep`.
    # No focus motor -- focus is the lens ring, set by hand.
    "imx477": {
        "size": [1332, 990], "sensor_size": [1332, 990],
        "exposure_us": 15000, "period_us": 25000, "fire_before_ms": 3.5,
        "colour_gains": [2.0, 1.8],
    },
}

DEFAULTS = {
    "analogue_gain": 1.0,
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


def camera_family():
    """'imx708', 'imx477', ... for the camera plugged in, without opening it.
    Variants share their family's settings (imx708_wide_noir -> imx708)."""
    try:
        from picamera2 import Picamera2
        model = (Picamera2.global_camera_info() or [{}])[0].get("Model", "")
    except Exception:
        model = ""
    for family in CAMERA_DEFAULTS:
        if model.startswith(family):
            return family
    return "imx708"


def load_settings():
    """Common defaults, then the fitted camera's, then ~/inline_camera.json.

    The file holds what calibration measured *for one camera* -- delete it
    when the camera is swapped, or the old camera's focus and colour carry
    over to the new one."""
    settings = dict(DEFAULTS)
    settings["camera"] = camera_family()
    settings.update(CAMERA_DEFAULTS.get(settings["camera"], {}))
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
                # Only on a camera with a focus motor; the HQ camera has none
                # and would refuse the controls.
                **({"AfMode": lc.AfModeEnum.Manual, "LensPosition": s["lens_position"]}
                   if "LensPosition" in cam.camera_controls and "lens_position" in s else {}),
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


def _save_setting(key, value):
    current = {}
    try:
        with open(SETTINGS_FILE) as f:
            current = json.load(f)
    except FileNotFoundError:
        pass
    current[key] = value
    with open(SETTINGS_FILE, "w") as f:
        json.dump(current, f, indent=1)


def _sharpness(array, region=(0.25, 0.25, 0.75, 0.75)):
    """Variance of a Laplacian over the middle of the frame: high when edges
    are crisp, low when blurred. Only comparable between photos of the same
    scene, which is all a focus sweep needs."""
    import numpy as np
    h, w = array.shape[:2]
    x0, y0, x1, y1 = region
    g = array[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)].mean(axis=2)
    lap = 4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(np.var(lap))


def focus(near=0.5, far=8.0):
    """Find the lens position that makes the scene sharpest, under the flash,
    and save it as lens_position.

    Autofocus is off for readings so every photo is focused the same; this
    is how that fixed position is chosen. A sweep under the flash rather than
    the camera's own autofocus: the tote is too dark for autofocus, and this
    measures the photos exactly as readings will be taken. Put product (or
    anything with fine detail) under the camera at the height the stream will
    be, first. Positions are dioptres, 1 / distance in metres.
    """
    s = load_settings()
    base = dict(s, attempts=3, recharge_s=1.5)
    path = os.path.join(HOME, "captures", "inline", "FOCUS.jpg")

    def measure(pos):
        keep = {}
        capture(path, dict(base, lens_position=pos), log=lambda *_: None, keep=keep)
        v = _sharpness(keep["array"])
        print(f"  lens {pos:5.2f} dioptres (~{100 / pos:4.0f} cm): sharpness {v:8.1f}", flush=True)
        return v

    print("coarse sweep", flush=True)
    step = 0.5
    coarse = {p: measure(p) for p in [round(near + i * step, 2) for i in range(int((far - near) / step) + 1)]}
    best = max(coarse, key=coarse.get)
    print(f"fine sweep around {best}", flush=True)
    around = [round(best + d, 2) for d in (-0.4, -0.3, -0.2, -0.1, 0.1, 0.2, 0.3, 0.4) if near <= best + d <= far]
    fine = {p: measure(p) for p in around}
    fine[best] = coarse[best]
    best = max(fine, key=fine.get)
    _save_setting("lens_position", best)
    print(f"saved lens_position {best} (~{100 / best:.0f} cm) to {SETTINGS_FILE}")


def focus_shots(positions=(0.0, 2.0, 3.5, 5.0, 8.0)):
    """Save a 100% crop of the middle of a flash photo at each lens position,
    for comparing by eye -- the arbiter when the sharpness score is in doubt
    (2026-10-06: the score barely moved across the whole lens range although
    the camera reported the lens moving)."""
    from PIL import Image
    s = load_settings()
    out_dir = os.path.join(HOME, "captures", "inline", "focus")
    os.makedirs(out_dir, exist_ok=True)
    for pos in positions:
        keep = {}
        capture(os.path.join(out_dir, "full.jpg"), dict(s, lens_position=pos, attempts=3, recharge_s=1.5),
                log=lambda *_: None, keep=keep)
        a = keep["array"]
        h, w = a.shape[:2]
        crop = a[h // 2 - 200:h // 2 + 200, w // 2 - 300:w // 2 + 300, ::-1]
        name = os.path.join(out_dir, f"lens_{pos:04.1f}.png")
        Image.fromarray(crop).save(name)
        print(f"lens {pos:4.1f} dioptres (~{100 / pos if pos else float('inf'):.0f} cm): "
              f"sharpness {_sharpness(a):7.1f} -> {name}", flush=True)
    print(f"compare the crops in {out_dir} at 100% zoom")


def sweep():
    """Measure where the flash must land on this camera, and save it.

    Fires once per firing moment from 2 ms after the frame timestamp to the
    full exposure before it, 1 ms apart, and reports each photo whole, split
    or unlit. The middle of the longest run of whole photos is saved as
    fire_before_ms. Needed once per camera model: the window depends on how
    fast the sensor reads its rows out.
    """
    s = load_settings()
    whole = []
    leads = list(range(-2, s["exposure_us"] // 1000 + 2))
    print(f"camera {s['camera']}, {s['size'][0]}x{s['size'][1]}, exposure {s['exposure_us'] / 1000:.0f} ms", flush=True)
    for lead in leads:
        notes = []
        try:
            capture(os.path.join(HOME, "captures", "inline", "SWEEP.jpg"),
                    dict(s, fire_before_ms=lead, attempts=1), log=notes.append)
            verdict = "WHOLE"
            whole.append(lead)
        except FlashTiming:
            verdict = "split"
        except FlashNotFired:
            verdict = "no flash"
        detail = notes[-1].split("strips ")[-1].split(" fired")[0] if notes else ""
        print(f"  fire {lead:3d} ms before the timestamp: {verdict:8s} {detail}", flush=True)
        time.sleep(2.0)
    if not whole:
        print("no whole photo at any moment -- is the flash on? nothing saved")
        return
    runs, run = [], [whole[0]]
    for v in whole[1:]:
        if v == run[-1] + 1:
            run.append(v)
        else:
            runs.append(run); run = [v]
    runs.append(run)
    best = max(runs, key=len)
    middle = (best[0] + best[-1]) / 2
    _save_setting("fire_before_ms", middle)
    print(f"whole from {best[0]} to {best[-1]} ms before -> saved fire_before_ms {middle} to {SETTINGS_FILE}")


def white_balance(region=(0.3, 0.3, 0.7, 0.7), passes=8):
    """Set colour_gains so a white or grey card under the flash comes out
    neutral, and save them to SETTINGS_FILE.

    Iterates from the camera's usual gains in damped steps rather than
    measuring once at flat gains (1, 1): through the ISP's colour matrix,
    flat gains on the HQ camera pushed red below zero, it clipped to 0, and
    a one-shot ratio overshot to the 8.0 ceiling (2026-10-13). Small steps
    from a sensible start keep every channel on scale, the same way the tray
    rig's whitebalance converges.

    Measured under the flash because the flash is the light every reading is
    taken in. On a NoIR camera it can only partly correct the colour. Redo it
    whenever the camera, the flash, its diffuser, the tote lining or the
    flash's angle changes. A white card may need the aperture closed down so
    it does not white out; the aperture changes how much light, not its
    colour, so open it again afterwards.
    """
    s = load_settings()
    x0, y0, x1, y1 = region
    gains = list(CAMERA_DEFAULTS.get(s["camera"], {}).get("colour_gains", [1.8, 1.6]))
    for step in range(1, passes + 1):
        keep = {}
        capture(os.path.join(HOME, "captures", "inline", "WB.jpg"),
                dict(s, colour_gains=gains), keep=keep)
        a = keep["array"]
        h, w = a.shape[:2]
        patch = a[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)].reshape(-1, 3).mean(axis=0)
        b, g, r = (float(v) for v in patch)
        print(f"pass {step}: card R {r:.0f}  G {g:.0f}  B {b:.0f}  (gains {gains[0]:.2f}, {gains[1]:.2f})", flush=True)
        if max(r, g, b) > 235:
            print("  the card is whiting out -- close the aperture further and run this again. Nothing saved.")
            return
        if min(r, g, b) < 15:
            print("  a channel is near zero -- the card is too dark or not in the centre of the view. Nothing saved.")
            return
        if abs(r - g) <= 0.03 * g and abs(b - g) <= 0.03 * g:
            break
        # Half a correction step each pass (square root of the ratio): the
        # ISP's colour matrix couples the channels, so a full step overshoots.
        gains = [max(0.5, min(8.0, gains[0] * (g / r) ** 0.5)),
                 max(0.5, min(8.0, gains[1] * (g / b) ** 0.5))]
    else:
        print("  not fully neutral after every pass -- saving the closest; run again to refine.")
    _save_setting("colour_gains", [round(gains[0], 3), round(gains[1], 3)])
    print(f"saved colour_gains {[round(gains[0], 3), round(gains[1], 3)]} to {SETTINGS_FILE}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "sweep":
        sweep()
    elif len(sys.argv) > 1 and sys.argv[1] in ("focus", "focus-shots") and load_settings()["camera"] == "imx477":
        print("The HQ camera has no focus motor: focus it by hand on the lens ring "
              "(live view: python3 ~/caliban/rig/preview.py).")
    elif len(sys.argv) > 1 and sys.argv[1] == "focus":
        focus()
    elif len(sys.argv) > 1 and sys.argv[1] == "focus-shots":
        focus_shots()
    elif len(sys.argv) > 1 and sys.argv[1] == "wb":
        region = tuple(float(v) for v in sys.argv[2].split(",")) if len(sys.argv) > 2 else (0.3, 0.3, 0.7, 0.7)
        white_balance(region)
    else:
        out = os.path.join(HOME, "captures", "inline", "TEST.jpg")
        print(capture(out))
        print("saved", out)
