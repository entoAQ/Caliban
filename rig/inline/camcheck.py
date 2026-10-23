#!/usr/bin/env python3
"""Is the camera there, and is it keeping time? -- for a flash timing fault.

flashcam.capture() predicts when the next frames will be exposed from one
frame's SensorTimestamp, assuming every later frame comes exactly PERIOD after
it, and fires the flash into that window. So FlashTiming ("the flash landed
outside the exposure window") has three suspects, and this looks at each:

  1. the camera itself -- missing, or only half there: a loose ribbon can
     leave it listed but dropping frames or failing to start;
  2. the frame clock -- frames not PERIOD apart, or frames lost on the way in
     (the loop then picks up the frame after the lit one);
  3. the Pi -- the trigger going out late. That one is not the camera at all:
     every "flash attempt" line in the loop's journal says how late it fired,
     and the last few are printed here so the two can be told apart.

It opens the camera in exactly the mode capture() uses, so the loop must not
hold it: stop caliban-inline first (the doctor's « Vérifier la caméra » does
that, and starts it again after). No flash is fired.

    sudo systemctl stop caliban-inline
    python3 camcheck.py
    sudo systemctl start caliban-inline

Prints a report, then one JSON line for doctor.py:
{"status": "ok"|"warn"|"fail", "detail": "<one line, French>"}.
"""

import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flashcam  # noqa: E402

FRAMES = 120               # ~3 s at 25 ms
# The flash is aimed at the middle of a ~7 ms window, so a frame more than a
# fraction of that off its predicted time is already a near miss.
JITTER_WARN_MS = 0.5
KERNEL_SINCE = "-12h"
# Pi 4 receives the camera through unicam, Pi 5 through rp1-cfe.
KERNEL_PAT = re.compile(r"unicam|rp1-cfe|cfe |csi|imx708|imx477|dw9807", re.I)
KERNEL_BAD = re.compile(r"error|fail|timeout|timed out|overflow|drop|lost|reset", re.I)


def run(cmd, timeout=30):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, f"{cmd[0]} introuvable"
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]} : pas de réponse après {timeout} s"


def main():
    worst, notes = "ok", []

    def flag(status, text):
        nonlocal worst
        if ["ok", "warn", "fail"].index(status) > ["ok", "warn", "fail"].index(worst):
            worst = status
        notes.append(text)

    def done():
        detail = " ".join(notes) if notes else "Caméra présente, images régulières."
        print(json.dumps({"status": worst, "detail": detail}, ensure_ascii=False))
        return 0 if worst != "fail" else 1

    # 1. Listed by libcamera?
    print("== rpicam-hello --list-cameras")
    rc, out = run(["rpicam-hello", "--list-cameras"])
    print(out or f"(rien, code {rc})")
    if "Available cameras" not in out:
        flag("fail", "Aucune caméra détectée : vérifier la nappe des deux côtés (Pi éteint), puis redémarrer le Pi.")
        print_kernel()
        return done()

    # 2. Kernel complaints about the camera link.
    if print_kernel():
        flag("warn", "Le noyau signale des erreurs de liaison caméra (nappe ?).")

    # 3. Stream in the capture mode and time the frames.
    s = flashcam.load_settings()
    period_ms = s["period_us"] / 1000
    print(f"\n== {FRAMES} images en mode capture ({s['camera']}, {s['size'][0]}x{s['size'][1]}, "
          f"période {period_ms:.1f} ms, flash {s['fire_before_ms']} ms avant)")
    try:
        stamps = stream(s)
    except Exception as e:
        print(f"{type(e).__name__}: {e}")
        flag("fail", f"La caméra est listée mais ne démarre pas ({type(e).__name__}) : "
                     "nappe mal enfoncée ou caméra occupée.")
        return done()
    gaps = [(b - a) / 1e6 for a, b in zip(stamps, stamps[1:])]
    if not gaps:
        flag("fail", "La caméra démarre mais ne livre aucune image.")
        return done()
    # A gap of k periods is k-1 frames lost on the way in; what is left over
    # after rounding is how far the frame clock is off its own rhythm.
    lost = sum(max(0, round(g / period_ms) - 1) for g in gaps)
    jitter = max(abs(g - round(g / period_ms) * period_ms) for g in gaps)
    print(f"écarts : min {min(gaps):.3f} ms, max {max(gaps):.3f} ms ; "
          f"images perdues : {lost} ; décalage max : {jitter:.3f} ms")
    if lost:
        flag("warn", f"{lost} image(s) perdue(s) sur {FRAMES} : nappe ou Pi surchargé.")
    if jitter > JITTER_WARN_MS:
        flag("warn", f"Images irrégulières (décalage jusqu'à {jitter:.2f} ms) : le flash peut tomber à côté.")
    if not lost and jitter <= JITTER_WARN_MS:
        notes.append(f"{s['camera']} présente, {FRAMES} images régulières (décalage max {jitter:.2f} ms).")

    # 4. The loop's own record of how late the trigger went out.
    print("\n== Derniers essais de flash (caliban-inline)")
    # Filtered here rather than with --grep, which needs a journalctl built with PCRE.
    rc, out = run(["journalctl", "--no-pager", "-o", "short-iso", "-u", "caliban-inline",
                   "--since", KERNEL_SINCE, "-n", "5000"])
    lines = [l for l in out.splitlines() if "flash attempt" in l or "flash timing" in l][-12:]
    print("\n".join(lines) or "(aucun)")
    lates = [float(m.group(1)) for l in lines for m in [re.search(r"fired ([+-][\d.]+) ms late", l)] if m]
    if lates and max(lates) > 1.5:
        flag("warn", f"Le Pi a déclenché le flash jusqu'à {max(lates):.1f} ms en retard : Pi trop occupé, pas la caméra.")
    return done()


def print_kernel():
    """Print the kernel's camera-link lines; True if any reads as an error."""
    print(f"\n== Noyau, caméra ({KERNEL_SINCE.lstrip('-')})")
    rc, out = run(["journalctl", "-k", "--no-pager", "-o", "short-iso", "--since", KERNEL_SINCE])
    lines = [l for l in out.splitlines() if KERNEL_PAT.search(l)][-25:]
    print("\n".join(lines) or ("(rien)" if rc == 0 else out[:300]))
    return any(KERNEL_BAD.search(l) for l in lines)


def stream(s):
    """SensorTimestamps of FRAMES consecutive frames, in capture()'s mode."""
    from picamera2 import Picamera2
    cam = Picamera2()
    try:
        cam.configure(cam.create_still_configuration(
            main={"size": tuple(s["size"]), "format": "RGB888"},
            **({"sensor": {"output_size": tuple(s["sensor_size"])}} if s.get("sensor_size") else {}),
            buffer_count=4,
            controls={"ExposureTime": s["exposure_us"], "AnalogueGain": s["analogue_gain"],
                      "AeEnable": False, "AwbEnable": False,
                      "FrameDurationLimits": (s["period_us"], s["period_us"])}))
        cam.start()
        time.sleep(1.0)
        stamps = []
        for _ in range(FRAMES):
            r = cam.capture_request()
            stamps.append(r.get_metadata()["SensorTimestamp"])
            r.release()
        return stamps
    finally:
        try:
            cam.stop()
        finally:
            cam.close()


if __name__ == "__main__":
    sys.exit(main())
