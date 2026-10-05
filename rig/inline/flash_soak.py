#!/usr/bin/env python3
"""Does the flash stay awake when fired at a steady interval?

The TT520III sleeps when idle, and a sleeping flash ignores the trigger --
it was found asleep within ~10 minutes on 2026-10-05, far sooner than the
30 minutes its manual gives. Whether a regular trigger keeps it awake decides
how the rig deals with that, so this measures it: one flash photo every
INTERVAL seconds for MINUTES minutes, one line per shot.

Stop the loop first (it holds the camera and the flash pin while capturing):

    sudo systemctl stop caliban-inline
    python3 ~/caliban/rig/inline/flash_soak.py 120 20     # every 2 min, 20 min
"""

import datetime
import sys
import time

import flashcam


def main():
    interval = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    minutes = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    settings = dict(flashcam.load_settings(), attempts=1)
    shots = minutes * 60 // interval + 1
    print(f"one shot every {interval}s for {minutes} min ({shots} shots)", flush=True)
    for i in range(shots):
        t = datetime.datetime.now().strftime("%H:%M:%S")
        try:
            m = flashcam.capture("/tmp/flash_soak.jpg", settings, log=lambda *_: None)
            print(f"{t}  shot {i}: FIRED     mean {m['mean']:.0f}  ambient {m['ambient']:.0f}", flush=True)
        except flashcam.FlashNotFired:
            print(f"{t}  shot {i}: no flash", flush=True)
        except Exception as e:
            print(f"{t}  shot {i}: error {type(e).__name__}: {e}", flush=True)
        if i < shots - 1:
            time.sleep(interval)


if __name__ == "__main__":
    main()
