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

The photo itself is flashcam.py: a long exposure with the flash fired while
every row of the rolling shutter is exposing, checked for "lit" and "lit
evenly" before it is sent anywhere.
"""

import json
import os
import shutil
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

import requests

import flashcam
import screen

CALIBAN_URL = os.environ.get("CALIBAN_URL", "").rstrip("/")
INLINE_API_KEY = os.environ.get("INLINE_API_KEY", "")
FAKE_PHOTO = os.environ.get("INLINE_FAKE_PHOTO", "")

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
# A capture is a few seconds; with every flash retry used (6 tries, 10 s
# apart, flashcam.DEFAULTS) about 65 s. Past this, libcamera has wedged inside
# this process and nothing here can free it -- see watchdog.
CAMERA_WATCHDOG_S = 120


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

class watchdog:
    """Exit if the camera never returns, so systemd restarts the loop with a
    fresh camera session. Same reasoning as the tray poller's watchdog: after a
    CSI fault libcamera waits forever for a frame, inside this process."""

    def __enter__(self):
        def fire():
            log(f"watchdog: capture exceeded {CAMERA_WATCHDOG_S}s -- restarting")
            os._exit(3)
        self.timer = threading.Timer(CAMERA_WATCHDOG_S, fire)
        self.timer.daemon = True
        self.timer.start()

    def __exit__(self, *exc):
        self.timer.cancel()
        return False


# Room light's share of a photo above which the screen says so. Room light is
# recorded over the whole exposure while the product moves, so it lands as a
# smear over the flash-frozen image; ~0.2 was measured with the tote in place
# (2026-10-05). Much above that, the cover has probably been knocked.
AMBIENT_SHARE_WARN = 0.35


def take_photo(reading_id):
    """A flash-lit photo of the chute -> (path, what flashcam measured).
    Raises flashcam.FlashNotFired / FlashTiming when the flash did not light
    it properly."""
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    path = os.path.join(CAPTURE_DIR, f"{reading_id}.jpg")
    if FAKE_PHOTO:
        shutil.copyfile(FAKE_PHOTO, path)
        return path, {}
    with watchdog():
        measured = flashcam.capture(path, log=log)
    if measured.get("attempts", 1) > 1:
        log(f"{reading_id}: needed {measured['attempts']} flash attempts")
    return path, measured


# Flash strength (flashcam.flash_level: against the empty-chute reference,
# ~1.0 healthy) below which the batteries are tiring. Two readings in a row,
# so one odd frame does not send someone for batteries.
FLASH_LOW_LEVEL = 0.85

# OFF since 2026-10-13. The level is measured on bare chute against a photo of
# the chute clean, and a chute that has run product is never that clean: a
# film of grey dust darkens it without changing its colour. The first day read
# 0.70-0.78 all day, stopped or running, with no trend -- dust, not batteries,
# and the screen told the floor to change good batteries. Levels are still
# recorded (flash_levels, and the log) to design a replacement that needs
# nothing fitted to the chute. A flash that does not fire is still caught --
# FlashNotFired / a black photo -- and does not depend on any of this.
FLASH_LOW_WARN = False


def judge_flash(state, level):
    if level is None:
        return
    levels = state.setdefault("flash_levels", [])
    levels.append(level)
    del levels[:-60]
    if FLASH_LOW_WARN and len(levels) >= 2 and all(v < FLASH_LOW_LEVEL for v in levels[-2:]):
        set_health(state, flash="low", message="Flash plus faible que d'habitude — changer les piles du flash")
    elif state.get("health", {}).get("flash") == "low":
        set_health(state, flash="ok")


# --- Caliban ------------------------------------------------------------------

def request_reading(reading_id, paths, covers=None):
    """POST the photo, or a burst of them (the first as `file`, the rest as
    `more`, judged together by a group prompt), with each photo's product
    coverage when measured. Returns (reply or None,
    http_error_detail or None). Raises requests.RequestException when
    Caliban could not be reached."""
    paths = [paths] if isinstance(paths, str) else list(paths)
    # The operator's last confirmation, so Caliban starts its decision window
    # after it (app/main.py inline_decision): the readings from before an
    # adjustment describe a setting that is gone. Sent as seconds ago on this
    # Pi's own clock, so it is right even while the clock itself is not.
    data = {"reading_id": reading_id}
    if covers and any(c is not None for c in covers):
        data["couverture"] = ",".join("" if c is None else f"{c:.3f}" for c in covers)
    acks = screen.read_acks()
    if acks:
        try:
            at = datetime.fromisoformat(acks[-1]["at"])
            data["since_ack_s"] = str(max(0, int((now_utc() - at).total_seconds())))
            data["last_ack_instruction"] = acks[-1].get("instruction") or ""
        except (KeyError, TypeError, ValueError):
            pass
    handles = [open(p, "rb") for p in paths]
    try:
        files = [("file" if i == 0 else "more", (os.path.basename(p), h, "image/jpeg"))
                 for i, (p, h) in enumerate(zip(paths, handles))]
        resp = requests.post(
            f"{CALIBAN_URL}/inline/readings",
            headers=headers(),
            files=files,
            data=data,
            timeout=REQUEST_TIMEOUT,
        )
    finally:
        for h in handles:
            h.close()
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


# --- Mirror for SGSC -----------------------------------------------------------
#
# What the touchscreen shows, sent to Caliban (/inline/state) so SGSC's
# destoner-mirror.html can show the same screen anywhere. Sent when it would
# change and at least once a minute, so the mirror can tell a quiet line from
# a rig that has stopped reporting. Best-effort: a failure costs the mirror's
# freshness, never a reading.

MIRROR_EVERY_S = 60
_mirror = {"sig": None, "at": 0.0, "failing": False}


def mirror_state(state):
    acks = screen.read_acks()
    by_reading, setting = screen.summarise_acks(acks)
    payload = {k: v for k, v in state.items() if k not in ("flash_levels", "updated_at")}
    shown = screen.shown_reading(state, by_reading, acks[-1] if acks else None)
    payload["shown"] = shown
    payload["ack"] = by_reading.get(shown.get("id")) if shown.get("id") else None
    payload["setting"] = setting
    sig = json.dumps(payload, sort_keys=True, default=str)
    if sig == _mirror["sig"] and time.monotonic() - _mirror["at"] < MIRROR_EVERY_S:
        return
    try:
        resp = requests.post(f"{CALIBAN_URL}/inline/state", headers=headers(),
                             json={**payload, "device": "ariel"}, timeout=15)
        resp.raise_for_status()
        _mirror.update(sig=sig, at=time.monotonic())
        if _mirror["failing"]:
            log("mirror state reaching Caliban again")
        _mirror["failing"] = False
    except requests.RequestException as e:
        if not _mirror["failing"]:
            log(f"mirror state not sent: {type(e).__name__}: {e}")
        _mirror["failing"] = True


# --- One reading --------------------------------------------------------------

# While the line is stopped or the chute empty, look again this often. The
# motion check costs no flash and no Azure call, so it can be frequent.
IDLE_RECHECK_S = 60


def line_running(state, cam_settings):
    """True / False / None (too dark to tell). Updates state["line"] and,
    during a stop, keeps the flash awake. A camera fault here is not a reason
    to skip a reading: it returns None and the flash capture goes ahead."""
    if FAKE_PHOTO:
        return True
    try:
        with watchdog():
            m = flashcam.motion_check(cam_settings)
    except Exception as e:
        log(f"motion check failed: {type(e).__name__}: {e}")
        return None
    running = m["running"]
    line = state.setdefault("line", {})
    if running is False:
        if line.get("running") is not False:
            line["since"] = iso(now_utc())
            log(f"line stopped (motion {m['score']}, light {m['brightness']})")
        line.update(running=False, motion=m["score"])
        # The TT520III falls into a sleep only its button wakes after ~10 min
        # with no trigger; a stop must not leave it there for the restart.
        # A photo rather than a bare trigger: over the stopped (usually empty)
        # chute it is the cleanest measure of the batteries, and it catches a
        # flash that has gone to sleep anyway -- before the line restarts.
        last = state.get("last_flash_at")
        if not last or (now_utc() - datetime.fromisoformat(last)).total_seconds() >= cam_settings["keepalive_s"]:
            try:
                with watchdog():
                    m = flashcam.capture(os.path.join(CAPTURE_DIR, "KEEPALIVE.jpg"),
                                         dict(cam_settings, attempts=2), log=log)
                state["last_flash_at"] = iso(now_utc())
                log(f"keep-alive flash (flash level {m.get('flash_level')})")
                judge_flash(state, m.get("flash_level"))
                if state.get("health", {}).get("flash") == "fail":
                    set_health(state, flash="ok")
            except flashcam.FlashNotFired:
                log("keep-alive: the flash did not fire")
                set_health(state, flash="fail",
                           message="Le flash ne se déclenche pas — vérifier qu'il est allumé (appuyer sur un bouton) et ses piles")
            except Exception as e:
                log(f"keep-alive failed: {type(e).__name__}: {e}")
    else:
        if line.get("running") is False:
            log(f"line running again (motion {m['score']})")
        line.update(running=True if running else None, motion=m["score"], since=None)
    return running


def take_reading(state):
    """Returns seconds until the next one."""
    reading_id = datetime.now().strftime("IL-%Y%m%d-%H%M%S")
    interval = state.get("interval_s") or DEFAULT_INTERVAL_S
    cam_settings = flashcam.load_settings()

    # No flash and no analysis while nothing is moving: a stopped line, or an
    # empty chute. Too dark to tell (None) is treated as running -- a wasted
    # reading is cheaper than a missed one.
    if line_running(state, cam_settings) is False:
        return IDLE_RECHECK_S

    try:
        path, measured = take_photo(reading_id)
    except flashcam.FlashNotFired as e:
        log(f"no flash for {reading_id}: {e}")
        set_health(state, camera="ok", flash="fail",
                   message="Le flash ne se déclenche pas — vérifier qu'il est allumé et ses piles")
        return interval
    except flashcam.FlashTiming as e:
        log(f"flash timing for {reading_id}: {e}")
        set_health(state, camera="ok", flash="fail", message="Flash mal synchronisé — prévenir l'AQ")
        return interval
    except Exception as e:
        log(f"camera failed for {reading_id}: {type(e).__name__}: {e}")
        set_health(state, camera="fail", message="Caméra : aucune photo — vérifier le câble et la caméra")
        return interval
    state["last_capture"] = measured
    state["last_flash_at"] = iso(now_utc())

    # Product in view? Compared with the empty-chute reference; without one,
    # coverage is None and the photo goes as before.
    cov = measured.get("coverage")
    line = state.setdefault("line", {})
    if cov is not None and cov < cam_settings["min_coverage"]:
        log(f"{reading_id}: product covers {cov:.0%} of the view -- not analysed")
        line.update(product=False, coverage=cov)
        return IDLE_RECHECK_S
    line.update(product=True, coverage=cov)

    share = measured.get("ambient_share")
    if share is not None and share > AMBIENT_SHARE_WARN:
        log(f"{reading_id}: room light is {share:.0%} of the photo")
        set_health(state, camera="low",
                   message=f"Trop de lumière sous la couverture ({share:.0%}) — vérifier qu'elle est bien en place")
    else:
        set_health(state, camera="ok")
    level = measured.get("flash_level")

    # A burst for a group prompt (Caliban inline_settings.group_size, sent
    # back with every reply): more photos of the same stream a few seconds
    # apart, judged together as one tray-sized sample. A photo that fails, or
    # shows too little product, is left out; the rest still go.
    # Flow-sized (group_target_cover set): after group_size photos it keeps
    # going until the photos' summed product coverage reaches the target, up
    # to group_max -- so light flow gets more photos and heavy flow fewer, and
    # every reading is about the same amount of product. A photo whose
    # coverage cannot be measured ends the extension: the burst then stays
    # at group_size, as before.
    paths, covers = [path], [cov]
    n_min = int(state.get("group_size") or 1)
    n_max = max(n_min, int(state.get("group_max") or 0))
    target = float(state.get("group_target_cover") or 0)

    def more_wanted():
        if len(paths) < n_min:
            return True
        if not target or len(paths) >= n_max or any(c is None for c in covers):
            return False
        return sum(covers) < target

    k = 1
    while more_wanted() and k < n_max + 2:
        k += 1
        time.sleep(int(state.get("group_spacing_s") or 12))
        try:
            p, m_k = take_photo(f"{reading_id}-{k}")
        except Exception as e:
            log(f"{reading_id}-{k}: burst photo failed: {type(e).__name__}: {e}")
            continue
        c_k = m_k.get("coverage")
        if c_k is not None and c_k < cam_settings["min_coverage"]:
            log(f"{reading_id}-{k}: product covers {c_k:.0%} -- left out of the burst")
            continue
        paths.append(p)
        covers.append(c_k)
    if target:
        known = [c for c in covers if c is not None]
        log(f"{reading_id}: burst of {len(paths)}, product cover {sum(known):.2f} (target {target:.2f})")
    state["last_flash_at"] = iso(now_utc())

    try:
        reply, error = request_reading(reading_id, paths, covers)
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
        # What the instruction was decided on: the median of the last few
        # readings (Caliban inline_decision), over decision_n of them, and
        # whether that is enough to act on (decision_min).
        "decision_pct": reply.get("decision_pct"),
        "decision_n": reply.get("decision_n"),
        "decision_min": reply.get("decision_min"),
        # On the lab ME% scale through Caliban's calibration line, when set;
        # the screen shows this rather than the prompt's band.
        "calibrated_pct": reply.get("calibrated_pct"),
    }
    state["mode"] = reply.get("mode") or "shadow"
    state["thresholds"] = reply.get("thresholds") or state.get("thresholds")
    # How to take the next reading. An older Caliban sends neither: one photo.
    state["group_size"] = int(reply.get("group_size") or 1)
    state["group_spacing_s"] = int(reply.get("group_spacing_s") or 12)
    state["group_max"] = int(reply.get("group_max") or 0)
    state["group_target_cover"] = float(reply.get("group_target_cover") or 0)
    # An instruction stays on screen until someone confirms it, even after the
    # readings settle back: someone walking past five minutes later still has
    # to see that the destoner was meant to be changed. screen.shown_reading
    # decides when this has expired or been answered.
    instruction = reply.get("instruction") or ""
    if instruction.startswith("increase") or instruction == "decrease":
        state["pending"] = dict(state["reading"])
    history = state.setdefault("history", [])
    # The trend's dots on the same scale as its decision line and thresholds:
    # calibrated when Caliban calibrates, the raw value otherwise.
    shown_pct = reply.get("calibrated_pct")
    history.append({"id": reading_id, "at": iso(taken),
                    "estimate_pct": shown_pct if shown_pct is not None else reply.get("estimate_pct"),
                    "decision_pct": reply.get("decision_pct"),
                    "instruction": reply.get("instruction")})
    del history[:-HISTORY_KEEP]
    state["interval_s"] = int(reply.get("next_in_s") or DEFAULT_INTERVAL_S)
    log(f"{reading_id}: {reply.get('band')} -> {reply.get('instruction')} ({state['mode']}) "
        f"coverage {cov} flash {level}")
    return state["interval_s"]


def main():
    if not CALIBAN_URL or not INLINE_API_KEY:
        sys.exit("CALIBAN_URL and INLINE_API_KEY must both be set.")
    log(f"inline loop -> {CALIBAN_URL}" + (f"  (fake photo {FAKE_PHOTO})" if FAKE_PHOTO else ""))

    state = load_state()
    # A "low" left in the state file from before FLASH_LOW_WARN was turned off
    # would otherwise stay on the screen until a usable level came in.
    if not FLASH_LOW_WARN and state.get("health", {}).get("flash") == "low":
        set_health(state, flash="ok")
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
            mirror_state(state)
        except Exception as e:
            log(f"unexpected error: {type(e).__name__}: {e}")
            traceback.print_exc()
            next_at = now_utc() + timedelta(seconds=60)
        time.sleep(TICK_S)


if __name__ == "__main__":
    main()
