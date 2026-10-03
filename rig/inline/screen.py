#!/usr/bin/env python3
"""Destoner screen for the inline rig -- serves the touchscreen beside it.

The inline rig photographs the product on its own, every few minutes, and the
operator standing at the destoner needs to see what it concluded. This serves
that one screen, to Chromium in kiosk mode on the same Pi, and records the
operator's confirmation that an instruction was carried out.

Why the Pi serves it rather than SGSC: the screen is display-first and has no
keyboard. An SGSC page needs a logged-in account, and anything that ends the
session -- an expiry, a stray tap on Déconnexion -- leaves a screen nobody on
the line can log back into. A page served from localhost needs no account at
all, and it keeps working when the plant Wi-Fi drops: it can say "no
connection, last reading 12 min ago" rather than going blank.

The contract with the rest of the rig is two files in the home directory:

    ~/inline_state.json   written by the capture loop, read here. What the
                          screen shows: the latest reading and its instruction,
                          recent history, the pressure target and live value
                          when there is one, and the health of each part.
    ~/inline_acks.jsonl   appended here, one line per confirmation. The capture
                          loop forwards these to Caliban, so a confirmation is a
                          record against the reading it answers, not just a
                          change on this screen.

Files rather than a socket or a shared process, so either side can restart
without the other noticing, and so the state can be read with `cat` when
someone asks why the screen says what it says.

Listens on 127.0.0.1 only. The page is for the screen bolted to this Pi; an
acknowledgement must come from someone standing at the destoner, not from
anything else on the plant network.

    python3 screen.py            # serve ~/inline_state.json
    python3 screen.py --demo     # cycle through sample states, no files touched
"""

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http import server
from socketserver import ThreadingMixIn

PORT = 8090
HOME = os.path.expanduser("~")
STATE_FILE = os.path.join(HOME, "inline_state.json")
ACK_FILE = os.path.join(HOME, "inline_acks.jsonl")
# Photos are only ever served from here. The path comes from the state file,
# and a state file is not something to trust with arbitrary filesystem reads.
CAPTURE_DIR = os.path.realpath(os.path.join(HOME, "captures"))
PAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screen.html")

# What an operator can answer. 'adjusted' and 'clean_rejects' are the two
# outcomes the tray operator screen already offers after DIMINUER; 'adjusted'
# also answers AUGMENTER. 'confirmed' is for a target the pressure sensor has
# verified -- the operator acknowledges, the sensor vouches for the value.
ACK_CHOICES = ("adjusted", "clean_rejects", "confirmed")

ack_lock = threading.Lock()


def log(message):
    print(f"{datetime.now().isoformat(timespec='seconds')}  {message}", flush=True)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def page_version():
    """Changes whenever screen.html does, so an open page can reload itself
    after an update instead of showing the old screen until someone reboots."""
    try:
        st = os.stat(PAGE_FILE)
        return f"{int(st.st_mtime)}-{st.st_size}"
    except OSError:
        return "missing"


def read_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        # A half-written file during a save. The writer replaces it atomically,
        # so this should not happen, but a blank screen is the wrong answer to
        # it either way: report it and let the next poll try again.
        return {"state_error": f"{type(e).__name__}: {e}"}


def read_acks():
    """reading_id -> the most recent ack for it."""
    acks = {}
    try:
        with open(ACK_FILE) as f:
            for line in f:
                try:
                    a = json.loads(line)
                    acks[a["reading_id"]] = a
                except (ValueError, KeyError):
                    continue
    except FileNotFoundError:
        pass
    return acks


def append_ack(ack):
    with ack_lock:
        with open(ACK_FILE, "a") as f:
            f.write(json.dumps(ack, ensure_ascii=False) + "\n")


# --- Demo -------------------------------------------------------------------
#
# Cycles through the states the screen has to handle, so the layout can be
# judged on the real display before the camera exists. Acks are kept in memory
# and forgotten on restart; nothing is written.

DEMO_SECONDS = 15
demo_acks = {}


def _ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _history(values):
    out = []
    for i, v in enumerate(values):
        ins = "increase_minor" if v >= 8 else "decrease" if v < 3 else "hold"
        out.append({"at": _ago(5 * (len(values) - i)), "estimate_pct": v, "instruction": ins})
    return out


def demo_state():
    scenes = [
        ("hold", dict(band="3-8", estimate_pct=4.6, instruction="hold")),
        ("increase", dict(band="8-10", estimate_pct=8.9, instruction="increase_minor")),
        ("increase_target", dict(band="10-13", estimate_pct=11.2, instruction="increase_medium",
                                 target={"value": 4.2, "unit": "psi", "tolerance": 0.2})),
        ("decrease", dict(band="<3", estimate_pct=1.8, instruction="decrease")),
        ("alert", dict(band=">13", estimate_pct=14.5, instruction="increase_major",
                       alert=True, alert_reason="streak", alert_streak=2)),
        ("stale", None),
        ("flash_low", dict(band="3-8", estimate_pct=5.1, instruction="hold")),
    ]
    tick = int(time.time() // DEMO_SECONDS)
    name, reading = scenes[tick % len(scenes)]
    health = {"camera": "ok", "flash": "ok", "network": "ok"}
    history = _history([3.2, 4.1, 5.0, 6.4, 7.2, 7.9, 8.6, 9.1, 6.0, 4.8, 3.9, 4.6])
    pressure = None

    if name == "stale":
        reading = dict(band="3-8", estimate_pct=5.0, instruction="hold")
        reading["at"] = _ago(38)
        health["network"] = "down"
        health["message"] = "Pas de connexion à Caliban"
    else:
        reading["at"] = _ago(1)
    if name == "flash_low":
        health["flash"] = "low"
        health["message"] = "Piles du flash faibles — changer les piles"
    if reading.get("target"):
        # A live value creeping up to the target, so the button can be seen
        # going from waiting to ready within one scene.
        phase = (time.time() % DEMO_SECONDS) / DEMO_SECONDS
        pressure = {"value": round(3.5 + 0.8 * min(1.0, phase * 1.6), 2), "unit": "psi", "at": now_iso()}

    reading["id"] = f"DEMO-{tick}-{name}"
    return {
        "reading": reading,
        "history": history,
        "health": health,
        "pressure": pressure,
        "next_at": (datetime.now(timezone.utc) + timedelta(seconds=DEMO_SECONDS - time.time() % DEMO_SECONDS)).isoformat(timespec="seconds"),
        "updated_at": now_iso(),
        "demo": True,
    }


# --- HTTP -------------------------------------------------------------------

class Handler(server.BaseHTTPRequestHandler):
    demo = False

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            try:
                with open(PAGE_FILE, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError as e:
                self._send(500, f"screen.html unreadable: {e}".encode(), "text/plain")
        elif path == "/state":
            state = demo_state() if self.demo else read_state()
            reading = state.get("reading") or {}
            acks = demo_acks if self.demo else read_acks()
            state["ack"] = acks.get(reading.get("id")) if reading.get("id") else None
            state["server"] = {"version": page_version(), "now": now_iso()}
            self._json(200, state)
        elif path == "/photo":
            self._photo()
        else:
            self._send(404, b"not found", "text/plain")

    def _photo(self):
        photo = ((read_state() if not self.demo else {}).get("reading") or {}).get("photo")
        if not photo:
            return self._send(404, b"no photo", "text/plain")
        real = os.path.realpath(photo)
        if not real.startswith(CAPTURE_DIR + os.sep):
            return self._send(403, b"outside captures", "text/plain")
        try:
            with open(real, "rb") as f:
                self._send(200, f.read(), "image/jpeg")
        except OSError:
            self._send(404, b"photo missing", "text/plain")

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/ack":
            return self._send(404, b"not found", "text/plain")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(length, 10000)) or b"{}")
        except ValueError:
            return self._json(400, {"error": "corps JSON invalide"})

        choice = body.get("choice")
        reading_id = body.get("reading_id")
        if choice not in ACK_CHOICES:
            return self._json(400, {"error": f"choix inconnu : {choice!r}"})

        # Only the reading on screen can be answered. A tap that lands just as
        # a new reading replaces the old one would otherwise confirm an
        # instruction the operator never saw.
        state = demo_state() if self.demo else read_state()
        current = (state.get("reading") or {}).get("id")
        if not reading_id or reading_id != current:
            return self._json(409, {"error": "Lecture remplacée — vérifiez l'écran."})

        ack = {"reading_id": reading_id, "choice": choice, "at": now_iso()}
        # What the sensor read at the moment of confirmation, when there is
        # one. This is the setting the next reading should be judged against.
        pressure = state.get("pressure")
        if pressure and pressure.get("value") is not None:
            ack["pressure"] = pressure.get("value")
            ack["pressure_unit"] = pressure.get("unit")

        if self.demo:
            demo_acks[reading_id] = ack
        else:
            append_ack(ack)
        log(f"ack {choice} for {reading_id}")
        self._json(200, {"ok": True, "ack": ack})

    def log_message(self, *args):
        # The page polls every couple of seconds; per-request lines would bury
        # the journal. Acks are logged explicitly above.
        pass


class Server(ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--demo", action="store_true", help="cycle through sample states")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    Handler.demo = args.demo
    log(f"destoner screen on http://127.0.0.1:{args.port}/" + ("  (demo)" if args.demo else ""))
    try:
        Server(("127.0.0.1", args.port), Handler).serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
