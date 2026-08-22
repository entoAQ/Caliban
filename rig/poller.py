#!/usr/bin/env python3
"""Bench rig poller -- turns a queued command into a photograph.

Asks Caliban for work every few seconds, and when a command appears, shoots
the dish and uploads the result. Runs as a systemd service so the rig is
ready whenever someone clicks the button, including after a power cut.

The direction of travel is the point. Nothing reaches into the plant: not
the operator's browser, not Caliban, not Supabase. The Pi reaches out, which
is the only direction that works through NAT and the only direction that
needs no firewall changes anyone has to be asked for.

Setup:
    export CALIBAN_URL=https://your-caliban-host
    export RIG_API_KEY=...            # must match Caliban's RIG_API_KEY
    python3 poller.py
"""

import os
import sys
import time
import traceback
from datetime import datetime

import requests

import capture

CALIBAN_URL = os.environ.get("CALIBAN_URL", "").rstrip("/")
RIG_API_KEY = os.environ.get("RIG_API_KEY", "")

POLL_SECONDS = 3.0
REQUEST_TIMEOUT = 30

# How long to wait after switching the lamp before shooting the IR frame.
# The IR LEDs are not under software control: a photoresistor on the LED
# boards brings them up when the scene goes dark, and it has its own lag.
# Measured on the bench -- adjust if the IR frame comes out unlit.
IR_SETTLE_SECONDS = 3.0


def log(message):
    print(f"{datetime.now().isoformat(timespec='seconds')}  {message}", flush=True)


def headers():
    return {"X-API-Key": RIG_API_KEY}


def claim():
    """Ask for a command. Returns the command dict, or None if idle."""
    resp = requests.get(
        f"{CALIBAN_URL}/capture-commands/next",
        headers=headers(),
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("command")


def shoot(lot_number):
    """Capture the visible frame, and the IR frame if the rig has IR boards.

    Returns (visible_path, ir_path_or_None).

    IR is best-effort on purpose. A missing IR frame is a complete result --
    the rig is useful without the IR boards attached, and refusing to deliver
    a perfectly good visible capture because the second one failed would be
    the wrong trade for an operator standing at the bench waiting."""
    visible = capture.capture(lot_number, "visible")

    ir = None
    if os.environ.get("RIG_CAPTURE_IR") == "1":
        try:
            # Nothing to switch here: the lamp is on a smart plug or a
            # physical switch, and the LED boards follow the room. This just
            # allows time for whatever is driving the lamp to have acted.
            time.sleep(IR_SETTLE_SECONDS)
            ir = capture.capture(lot_number, "ir")
        except Exception as e:
            log(f"IR capture failed, continuing with visible only: {e}")

    return visible, ir


def upload(command_id, visible_path, ir_path):
    files = {"file": (os.path.basename(visible_path), open(visible_path, "rb"), "image/jpeg")}
    if ir_path:
        files["ir_file"] = (os.path.basename(ir_path), open(ir_path, "rb"), "image/jpeg")
    try:
        resp = requests.post(
            f"{CALIBAN_URL}/capture-commands/{command_id}/complete",
            headers=headers(),
            files=files,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
    finally:
        for _, handle, _ in files.values():
            handle.close()


def report_failure(command_id, message):
    """Tell the queue this one failed, so the operator sees an error instead
    of a spinner that only resolves when the stale-claim timeout fires."""
    try:
        requests.post(
            f"{CALIBAN_URL}/capture-commands/{command_id}/fail",
            headers=headers(),
            data={"error": message[:1000]},
            timeout=REQUEST_TIMEOUT,
        )
    except Exception as e:
        log(f"could not report failure for {command_id}: {e}")


def main():
    if not CALIBAN_URL or not RIG_API_KEY:
        sys.exit("CALIBAN_URL and RIG_API_KEY must both be set.")

    log(f"polling {CALIBAN_URL} every {POLL_SECONDS}s")
    idle_since_log = 0

    while True:
        try:
            command = claim()

            if not command:
                # Quiet by default. A line every three seconds saying nothing
                # happened would make the journal useless for finding the
                # times something did.
                idle_since_log += 1
                if idle_since_log >= 200:
                    log("idle")
                    idle_since_log = 0
                time.sleep(POLL_SECONDS)
                continue

            idle_since_log = 0
            log(f"claimed {command['id']} for lot {command['lot_number']}")

            try:
                visible, ir = shoot(command["lot_number"])
                upload(command["id"], visible, ir)
                log(f"completed {command['id']}")
            except Exception as e:
                log(f"capture failed for {command['id']}: {type(e).__name__}: {e}")
                traceback.print_exc()
                report_failure(command["id"], f"{type(e).__name__}: {e}")

        except requests.RequestException as e:
            # Network trouble is expected and temporary -- plant Wi-Fi drops,
            # Caliban restarts, DNS hiccups. Back off a little and carry on;
            # anything unclaimed is still in the queue when we return.
            log(f"network error: {e}")
            time.sleep(POLL_SECONDS * 3)

        except Exception as e:
            log(f"unexpected error: {type(e).__name__}: {e}")
            traceback.print_exc()
            time.sleep(POLL_SECONDS * 3)


if __name__ == "__main__":
    main()
