#!/usr/bin/env python3
"""Inline rig capture loop -- photograph the chute, get a reading, feed the screen.

Every few minutes: take a photo, send it to Caliban's /inline/readings, and
write what comes back into ~/inline_state.json for screen.py to show. In
between, forward the operator's confirmations from ~/inline_acks.jsonl to
/inline/acks so they are recorded against the readings they answer.

Like the tray rig's poller, the Pi only ever reaches out: nothing on the plant
network or in the cloud calls in. Unlike it, nobody asks for a photo -- the
loop takes one on its own schedule, and Caliban sets that schedule
(next_in_s in every reply, from system_config.inline_settings).

Setup (see README.md):
    /etc/caliban-inline.env
        CALIBAN_URL=https://your-caliban-host
        INLINE_API_KEY=...                 # must match Caliban's INLINE_API_KEY
        INLINE_FAKE_PHOTO=/path/to.jpg     # optional: send this file instead of
                                           # using the camera, to test the chain
        INLINE_REF_REGION=0.02,0.02,0.10,0.10   # optional, see flash_level()

THE CAMERA PART IS PROVISIONAL. take_photo() shoots a plain still with
rpicam-still. The real capture fires the flash inside the window where every
row of the rolling shutter is exposing at once, and that is written on the
bench with the camera and flash in hand -- it cannot be got right without them.
Everything around it (Caliban, the screen, confirmations, health) is final.
"""

import json
import os
import shutil
import statistics
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone

import requests

CALIBAN_URL = os.environ.get("CALIBAN_URL", "").rstrip("/")
INLINE_API_KEY = os.environ.get("INLINE_API_KEY", "")
FAKE_PHOTO = os.environ.get("INLINE_FAKE_PHOTO", "")
REF_REGION = os.environ.get("INLINE_REF_REGION", "")

HOME = os.path.expanduser("~")
STATE_FILE = os.path.join(HOME, "inline_state.json")
ACK_FILE = os.path.join(HOME, "inline_acks.jsonl")
# How many lines of ACK_FILE Caliban has confirmed receiving.
ACK_SENT_FILE = os.path.join(HOME, "inline_acks.sent")
# Inside ~/captures because screen.py serves photos from there and nowhere
# else. Full-resolution originals stay on the Pi; Caliban keeps a review copy.
CAPTURE_DIR = os.path.join(HOME, "captures", "inline")

DEFAULT_INTERVAL_S = 300
TICK_S = 5                 # how often confirmations are checked for
REQUEST_TIMEOUT = 120      # an analysis with escalated rotations takes a while
HISTORY_KEEP = 36          # three hours at five minutes
CAMERA_TIMEOUT_S = 30


def log(message):
    print(f"{datetime.now().isoformat(timespec='seconds')}  {message}", flush=True)


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.isoformat(timespec="seconds")


def headers():
    return {"X-API-Key": INLINE_API_KEY}


# --- State file ---------------------------------------------------------------

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    """Atomically: screen.py polls every two seconds and must never read half
    a file. rename() over the old one is atomic on the same filesystem."""
    state["updated_at"] = iso(now_utc())
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_FILE)


def set_health(state, **parts):
    health = state.setdefault("health", {"camera": "ok", "flash": "ok", "network": "ok"})
    message = parts.pop("message", None)
    health.update(parts)
    # One line under the chips. Cleared once every part is ok again.
    if message is not None:
        health["message"] = message
    elif all(health.get(k) == "ok" for k in ("camera", "flash", "network")):
        health.pop("message", None)


# --- Camera -------------------------------------------------------------------

def take_photo(reading_id):
    """A photo of the chute at full resolution. Provisional: see the docstring."""
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    path = os.path.join(CAPTURE_DIR, f"{reading_id}.jpg")
    if FAKE_PHOTO:
        shutil.copyfile(FAKE_PHOTO, path)
        return path
    subprocess.run(
        ["rpicam-still", "--nopreview", "--immediate", "-o", path],
        check=True, timeout=CAMERA_TIMEOUT_S, capture_output=True,
    )
    return path


def flash_level(path):
    """Mean brightness of a fixed reference patch in the frame, or None.

    Weakening batteries dim the flash before it stops firing, but the whole
    frame's brightness also moves with the product -- frass is darker than
    larvae, so a dirty stretch would read as a tired flash. A patch that never
    changes (a strip of the tote wall or a white card, chosen once the rig is
    mounted) measures the flash alone. INLINE_REF_REGION is x0,y0,x1,y1 as
    fractions of the frame; unset, there is no early warning, only the
    black-frame refusal when the flash has stopped altogether.
    """
    if not REF_REGION:
        return None
    try:
        from PIL import Image, ImageStat
        x0, y0, x1, y1 = (float(v) for v in REF_REGION.split(","))
        img = Image.open(path).convert("L")
        w, h = img.size
        box = img.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h))).resize((32, 32))
        return round(ImageStat.Stat(box).mean[0], 1)
    except Exception as e:
        log(f"flash level unreadable: {e}")
        return None


# Below this fraction of the recent normal level, the flash is getting weak.
FLASH_LOW_RATIO = 0.85


def judge_flash(state, level):
    if level is None:
        return
    levels = state.setdefault("flash_levels", [])
    levels.append(level)
    del levels[:-60]
    if len(levels) < 6:
        return
    normal = statistics.median(levels[:-2] or levels)
    # Two in a row, so one odd frame does not send someone for batteries.
    if all(v < FLASH_LOW_RATIO * normal for v in levels[-2:]):
        set_health(state, flash="low", message="Flash plus faible que d'habitude — changer les piles du flash")
    elif state.get("health", {}).get("flash") == "low":
        set_health(state, flash="ok")


# --- Caliban ------------------------------------------------------------------

def request_reading(reading_id, path):
    """POST the photo. Returns (reply or None, http_error_detail or None).
    Raises requests.RequestException when Caliban could not be reached."""
    with open(path, "rb") as f:
        resp = requests.post(
            f"{CALIBAN_URL}/inline/readings",
            headers=headers(),
            files={"file": (os.path.basename(path), f, "image/jpeg")},
            data={"reading_id": reading_id},
            timeout=REQUEST_TIMEOUT,
        )
    if resp.ok:
        return resp.json(), None
    try:
        detail = resp.json().get("detail")
    except ValueError:
        detail = resp.text[:200]
    return None, f"{resp.status_code}: {detail}"


def forward_acks():
    """Send confirmations Caliban has not acknowledged yet. Safe to repeat:
    Caliban ignores a confirmation it already has."""
    try:
        with open(ACK_FILE) as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
    except FileNotFoundError:
        return
    try:
        with open(ACK_SENT_FILE) as f:
            sent = int(f.read().strip() or 0)
    except (OSError, ValueError):
        sent = 0
    if sent >= len(lines):
        return
    batch = []
    for l in lines[sent:]:
        try:
            batch.append(json.loads(l))
        except ValueError:
            log(f"skipping unreadable confirmation line: {l[:80]}")
    if batch:
        resp = requests.post(f"{CALIBAN_URL}/inline/acks", headers=headers(),
                             json=batch, timeout=30)
        resp.raise_for_status()
    with open(ACK_SENT_FILE, "w") as f:
        f.write(str(len(lines)))
    log(f"forwarded {len(batch)} confirmation(s)")


# --- One reading --------------------------------------------------------------

def take_reading(state):
    """Returns seconds until the next one."""
    reading_id = datetime.now().strftime("IL-%Y%m%d-%H%M%S")
    interval = state.get("interval_s") or DEFAULT_INTERVAL_S

    try:
        path = take_photo(reading_id)
    except Exception as e:
        log(f"camera failed for {reading_id}: {type(e).__name__}: {e}")
        set_health(state, camera="fail", message="Caméra : aucune photo — vérifier le câble et la caméra")
        return interval
    set_health(state, camera="ok")
    level = flash_level(path)

    try:
        reply, error = request_reading(reading_id, path)
    except requests.RequestException as e:
        log(f"Caliban unreachable for {reading_id}: {type(e).__name__}: {e}")
        set_health(state, network="down", message="Pas de connexion à Caliban — lecture non analysée")
        # Sooner than a full interval: the network usually comes back quickly,
        # and every skipped reading is a gap on the screen.
        return min(interval, 60)
    set_health(state, network="ok")

    if error:
        log(f"reading {reading_id} refused: {error}")
        if error.startswith("422"):
            # A black or blank frame. On this rig that is nearly always the
            # flash: batteries flat, or the flash asleep.
            set_health(state, flash="fail", message="Photo noire — flash éteint ou piles vides")
        else:
            set_health(state, message=f"Analyse refusée ({error[:80]})")
        return interval

    judge_flash(state, level)
    if state.get("health", {}).get("flash") == "fail":
        set_health(state, flash="ok")

    taken = now_utc()
    state["reading"] = {
        "id": reading_id,
        "at": iso(taken),
        "band": reply.get("band"),
        "estimate_pct": reply.get("estimate_pct"),
        "instruction": reply.get("instruction"),
        "alert": reply.get("alert"),
        "alert_reason": reply.get("alert_reason"),
        "alert_streak": reply.get("alert_streak"),
        "target": reply.get("target"),
        "photo": path,
    }
    state["mode"] = reply.get("mode") or "shadow"
    history = state.setdefault("history", [])
    history.append({"at": iso(taken), "estimate_pct": reply.get("estimate_pct"),
                    "instruction": reply.get("instruction")})
    del history[:-HISTORY_KEEP]
    state["interval_s"] = int(reply.get("next_in_s") or DEFAULT_INTERVAL_S)
    log(f"{reading_id}: {reply.get('band')} -> {reply.get('instruction')} ({state['mode']})")
    return state["interval_s"]


def main():
    if not CALIBAN_URL or not INLINE_API_KEY:
        sys.exit("CALIBAN_URL and INLINE_API_KEY must both be set.")
    log(f"inline loop -> {CALIBAN_URL}" + (f"  (fake photo {FAKE_PHOTO})" if FAKE_PHOTO else ""))

    state = load_state()
    state.pop("demo", None)
    next_at = now_utc()

    while True:
        try:
            if now_utc() >= next_at:
                wait = take_reading(state)
                next_at = now_utc() + timedelta(seconds=wait)
                state["next_at"] = iso(next_at)
                save_state(state)
            try:
                forward_acks()
            except requests.RequestException as e:
                # Kept in the file; the next tick tries again.
                log(f"confirmations not forwarded yet: {type(e).__name__}: {e}")
        except Exception as e:
            log(f"unexpected error: {type(e).__name__}: {e}")
            traceback.print_exc()
            next_at = now_utc() + timedelta(seconds=60)
        time.sleep(TICK_S)


if __name__ == "__main__":
    main()
