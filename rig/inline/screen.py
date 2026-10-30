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
                          the destoner setting to make when there is one,
                          recent history, and the health of each part.
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
"""

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
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
# When the touchscreen's page last asked for /state (it adds ?page=1; the
# doctor's probe does not). A browser that is open but frozen stops asking,
# and nothing else shows it: Chromium is still running. Starts as "now" so a
# fresh restart is not taken for a frozen page before it has had a chance.
page_seen = time.monotonic()

# What an operator can answer. 'adjusted' and 'clean_rejects' are the two
# outcomes the tray operator screen already offers after DIMINUER; 'adjusted'
# also answers AUGMENTER. 'set' answers an instruction that names a value
# ("Régler à 4,2"): the operator sets the destoner by its own gauge and says
# so, and that value becomes the setting the screen shows until the next one.
# The destoner has a physical gauge, not a sensor, so this is the record of
# what it was set to -- shown permanently so anyone passing can check the
# gauge still agrees.
# 'aq_notified' answers "Ne plus tourner la poignée : aviser l'AQ", where there
# is no turn to confirm; it is not a turn, and the next run of turns starts afresh.
ACK_CHOICES = ("adjusted", "clean_rejects", "set", "aq_notified")

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


# The network watchdog's own record of an outage (../netwatch.sh, run every
# minute by caliban-rig-netwatch.timer). It decides "offline" -- neither
# Caliban nor the internet answers -- so the screen says exactly what the
# watchdog is acting on. The files exist only during an outage.
NETWATCH_RUN = "/run/caliban-netwatch"
NETWATCH_STATE = "/var/lib/caliban-netwatch"
NETWATCH_WIFI_AFTER_S, NETWATCH_REBOOT_AFTER_S = 5 * 60, 15 * 60


def read_connection():
    """None while online; otherwise when the outage started and what the
    watchdog has done and will do about it."""
    try:
        with open(os.path.join(NETWATCH_RUN, "down_since")) as f:
            since = int(f.read().strip())
    except (OSError, ValueError):
        return None
    try:
        with open(os.path.join(NETWATCH_STATE, "last_reboot")) as f:
            last_reboot = int(f.read().strip())
    except (OSError, ValueError):
        last_reboot = 0
    return {
        "offline": True,
        "since": datetime.fromtimestamp(since, timezone.utc).isoformat(timespec="seconds"),
        "wifi_cycled": os.path.exists(os.path.join(NETWATCH_RUN, "wifi_cycled")),
        # The watchdog reboots at most once an hour; after one, the next
        # outage minute brings no new action until that hour is up.
        "rebooted_recently": time.time() - last_reboot < 3600,
        "wifi_after_s": NETWATCH_WIFI_AFTER_S,
        "reboot_after_s": NETWATCH_REBOOT_AFTER_S,
    }


def read_acks():
    """Every ack in file order. The file grows by one line per confirmation --
    a few hundred a week -- so reading it whole each poll is cheap."""
    acks = []
    try:
        with open(ACK_FILE) as f:
            for line in f:
                try:
                    a = json.loads(line)
                    if a.get("reading_id"):
                        acks.append(a)
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    return acks


def summarise_acks(acks):
    """(reading_id -> latest ack, latest setting ack or None)."""
    by_reading, setting = {}, None
    for a in acks:
        by_reading[a["reading_id"]] = a
        if a.get("setting") is not None:
            setting = a
    return by_reading, setting


# How long an unconfirmed instruction stays on screen after the readings have
# settled back. Long enough that someone passing on a round still sees it; not
# so long that a change nobody made is still demanded an hour later.
PENDING_MAX_S = 20 * 60


def _actionable(instruction):
    return isinstance(instruction, str) and (instruction.startswith("increase") or instruction == "decrease")


def _age_s(iso_at):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso_at)).total_seconds()
    except (TypeError, ValueError):
        return None


def shown_reading(state, by_reading, last_ack):
    """The reading whose instruction is on screen -- the one a tap answers.

    The latest reading when it asks for something. Otherwise the last reading
    that did (state["pending"], kept by loop.py), as long as nobody has
    confirmed it or anything since, and it is under PENDING_MAX_S old: the
    readings settling back is not the same as someone having changed the
    destoner. Otherwise the latest reading, whatever it says.
    """
    latest = state.get("reading") or {}
    if _actionable(latest.get("instruction")):
        return latest
    pending = state.get("pending") or {}
    if not pending.get("id") or pending["id"] in by_reading:
        return latest
    age = _age_s(pending.get("at"))
    if age is None or age > PENDING_MAX_S:
        return latest
    if last_ack and (_age_s(last_ack.get("at")) or 0) <= age:
        return latest
    return pending


def append_ack(ack):
    with ack_lock:
        with open(ACK_FILE, "a") as f:
            f.write(json.dumps(ack, ensure_ascii=False) + "\n")


# --- HTTP -------------------------------------------------------------------

class Handler(server.BaseHTTPRequestHandler):
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
            global page_seen
            if "page=1" in self.path:
                page_seen = time.monotonic()
            state = read_state()
            acks = read_acks()
            by_reading, setting = summarise_acks(acks)
            shown = shown_reading(state, by_reading, acks[-1] if acks else None)
            state["shown"] = shown
            state["ack"] = by_reading.get(shown.get("id")) if shown.get("id") else None
            state["setting"] = setting
            state["server"] = {"version": page_version(), "now": now_iso(),
                               "page_seen_s": round(time.monotonic() - page_seen)}
            state["connection"] = read_connection()
            self._json(200, state)
        elif path == "/photo":
            self._photo()
        else:
            self._send(404, b"not found", "text/plain")

    def _photo(self):
        photo = (read_state().get("reading") or {}).get("photo")
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

        # Only the instruction on screen can be answered. A tap that lands just
        # as a new reading replaces it would otherwise confirm an instruction
        # the operator never saw.
        state = read_state()
        acks = read_acks()
        shown = shown_reading(state, summarise_acks(acks)[0], acks[-1] if acks else None)
        if not reading_id or reading_id != shown.get("id"):
            return self._json(409, {"error": "Lecture remplacée — vérifiez l'écran."})

        # Which instruction this answers travels with the ack: loop.py tells
        # Caliban, so AUGMENTER again after a confirmed AUGMENTER can alert AQ.
        ack = {"reading_id": reading_id, "choice": choice, "at": now_iso(),
               "instruction": shown.get("instruction")}
        # And what it was decided on and how far it said to turn: Caliban
        # sizes the next turn from what this one did (app/main.py inline_turn).
        for key in ("decision_pct", "turn_cm", "turn_qt", "turn_unit"):
            if shown.get(key) is not None:
                ack[key] = shown[key]
        if choice == "set":
            # The value comes from the instruction on screen, never from the
            # request: the operator confirms the number they were shown.
            target = shown.get("target") or {}
            if target.get("value") is None:
                return self._json(409, {"error": "Aucune valeur à régler pour cette lecture."})
            ack["setting"] = target["value"]
            ack["unit"] = target.get("unit") or ""

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
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    log(f"destoner screen on http://127.0.0.1:{args.port}/")
    try:
        Server(("127.0.0.1", args.port), Handler).serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
