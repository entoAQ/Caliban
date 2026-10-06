#!/usr/bin/env python3
"""Does the lens actually move when told to?

A focus sweep under the flash (flashcam.py focus) scored every position the
same on 2026-10-06 -- 5.3 to 6.2 from 12 cm to 2 m -- which means either the
lens was not moving or the scene had nothing to focus on. This separates the
two in room light, no flash, autoexposure on: it sets four lens positions in
turn and prints what the camera reports the lens is at, and how sharp the
middle of the frame is.

    sudo systemctl stop caliban-inline      # it needs the camera
    python3 ~/caliban/rig/inline/lens_check.py

Lift the tote or open it up first so there is light, and keep something with
fine detail (product, printed text) under the camera.
"""

import time

from libcamera import controls as lc
from picamera2 import Picamera2

import flashcam


def main():
    cam = Picamera2()
    cam.configure(cam.create_still_configuration(main={"size": (1536, 864), "format": "RGB888"}))
    cam.start()
    time.sleep(1.5)
    md = cam.capture_metadata()
    print(f"lens range reported by the camera: {cam.camera_controls.get('LensPosition')}")
    print(f"before any change: AfMode {md.get('AfState')}  LensPosition {md.get('LensPosition')}")
    for pos in (0.0, 3.0, 6.0, 10.0):
        cam.set_controls({"AfMode": lc.AfModeEnum.Manual, "LensPosition": pos})
        time.sleep(1.5)                       # the lens takes a few frames to travel
        r = cam.capture_request()
        md = r.get_metadata()
        sharp = flashcam._sharpness(r.make_array("main"))
        r.release()
        print(f"asked {pos:5.1f} dioptres -> camera reports {md.get('LensPosition')}  "
              f"sharpness {sharp:8.1f}  (exposure {md.get('ExposureTime')} us)", flush=True)
    cam.stop()
    cam.close()


if __name__ == "__main__":
    main()
