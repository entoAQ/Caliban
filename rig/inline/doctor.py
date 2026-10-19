#!/usr/bin/env python3
"""The rigs' doctor -- find what is wrong with a rig, and fix it with a button.

One file for both rigs, chosen by DOCTOR_DEVICE in the unit: Ariel, the
inline rig at the destoner (the default, and what most of this describes),
and Prosperos, the tray rig at the lab bench (DOCTOR_DEVICE=prosperos, see
../caliban-doctor-tray.service). Same report, same remote repairs, same
safety rules; each rig gets its own checks and fixes (CHECKS, FIXES).

The people who see the destoner screen go wrong are on the line, not at a
terminal. Everything that has broken on this rig so far has had a known fix --
restart the loop, restart the screen, wake the flash, cycle the Wi-Fi, pull
the code again, reboot -- and every one of them needed someone with SSH.
This puts each check next to its fix, in French, as a button.

Two ways in, one set of checks and fixes:

    On the touchscreen     http://127.0.0.1:8091/ -- a long press on the
                           Caméra/Flash/Réseau chips opens it, and the
                           destoner screen falls back to it on its own when
                           screen.py stops answering.
    From a desk            every minute this posts its checks to Caliban
                           (/inline/doctor/report) and runs whatever repair
                           SGSC's diagnostic page queued for it in reply.
                           Outbound only, like loop.py: nothing calls in.

It is its own service (caliban-doctor), separate from screen.py and loop.py,
precisely so that it is still running when they are not. Standard library
only, for the same reason: a broken Python package must not take the repair
tool down with the thing it repairs.

Only the fixes named in FIXES can run, from either side, and none of them
edits a setting or a key. Root is reached through the scoped sudo rules in
caliban-doctor.sudoers, never by running this as root.

    python3 doctor.py              # serve the page and report to Caliban
    python3 doctor.py check        # print every check once (SSH / Pi Connect)
    python3 doctor.py fix ACTION   # run one fix by name, e.g. restart_loop
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http import server
from socketserver import ThreadingMixIn

PORT = 8091
HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
PAGE_FILE = os.path.join(HERE, "doctor.html")
STATE_FILE = os.path.join(HOME, "inline_state.json")
CAPTURE_DIR = os.path.join(HOME, "captures", "inline")
TEST_PHOTO = os.path.join(CAPTURE_DIR, "TEST.jpg")
AUTOSTART = os.path.join(HOME, ".config", "labwc", "autostart")
# Demo mode is gone from screen.py; a drop-in left from it passes --demo, which
# screen.py now refuses, so the screen crash-loops and a restart cannot help.
DEMO_DROPIN = "/etc/systemd/system/caliban-inline-screen.service.d/demo.conf"
# The last commits that ran clean for a while, oldest first, one per line (see
# note_good_version), and the flag that holds ../update.sh back after a
# rollback. A single line -- the older format -- reads as a list of one.
GOOD_SHA_FILE = os.path.join(HOME, ".doctor-good-sha")
GOOD_KEPT = 5
UPDATE_PAUSE_FILE = os.path.join(HOME, ".caliban-update-paused")
# Clean reports in a row (one a minute) before a version counts as good.
GOOD_AFTER_REPORTS = 30
# After a boot the clock is unsynced until NTP answers; not a fault before this.
CLOCK_GRACE_S = 15 * 60
LOG_UNITS = ["caliban-inline", "caliban-inline-screen", "caliban-doctor",
             "caliban-rig-update", "caliban-rig-netwatch"]
LOG_UNITS_TRAY = ["caliban-rig", "caliban-doctor", "caliban-rig-update", "caliban-rig-netwatch"]
LOG_LINES = 300

CALIBAN_URL = os.environ.get("CALIBAN_URL", "").rstrip("/")
# Which rig this is. Each reports with its own key, and Caliban tells them
# apart by the key, never by anything the rig says about itself.
DEVICE = os.environ.get("DOCTOR_DEVICE", "ariel")
TRAY = DEVICE == "prosperos"
API_KEY = os.environ.get("RIG_API_KEY" if TRAY else "INLINE_API_KEY", "")
ENV_FILE = "/etc/caliban-rig.env" if TRAY else "/etc/caliban-inline.env"

# The tray rig (rig/poller.py and rig/capture.py).
POLLER = "caliban-rig"
RIG_DIR = os.path.abspath(os.path.join(HERE, ".."))
BUSY_FILE = os.path.join(HOME, ".caliban-rig-busy")
# The remote repair being run right now. update.sh restarts this service every
# time new code lands, and a repair cut off that way used to stay "en cours"
# in SGSC for good; the next start reports it as interrupted instead.
INFLIGHT_FILE = os.path.join(HOME, ".caliban-doctor-inflight")
BUSY_STALE_S = 180
RIG_SETTINGS = os.path.join(HOME, "rig_settings.json")
TRAY_CAPTURES = os.path.join(HOME, "captures")
TRAY_TEST_DIR = os.path.join(HOME, "captures-doctor")
TOF_I2C_BUS, TOF_I2C_ADDR = "1", "0x33"

LOOP = "caliban-inline"
SCREEN = "caliban-inline-screen"
UPDATE_TIMER = "caliban-rig-update.timer"

REPORT_EVERY_S = 60
# The loop writes its state at least once per reading or idle recheck -- every
# minute while the line is stopped, every interval (5 min) while it runs. Past
# this, it has stopped doing its job even if systemd says it is running.
STATE_STALE_S = 15 * 60
GIT_FETCH_EVERY_S = 10 * 60
# Freed down to this much free space; never touching anything newer than
# KEEP_DAYS -- the full-resolution originals are the only copy of a reading.
DISK_WARN_PCT, DISK_FAIL_PCT, DISK_TARGET_PCT = 20, 10, 25
KEEP_DAYS = 3


def log(message):
    print(f"{datetime.now().isoformat(timespec='seconds')}  {message}", flush=True)


def run(cmd, timeout=20, cwd=None):
    """(returncode, combined output). Never raises: a missing tool is an
    answer (127), not a crash of the doctor."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, f"{cmd[0]} introuvable"
    except subprocess.TimeoutExpired:
        return 124, f"{' '.join(cmd[:3])} : pas de réponse après {timeout} s"


def last_json(out):
    """The result line a test script printed. run() puts stderr after stdout,
    and libcamera logs INFO lines there, so it is not the last line."""
    for line in reversed(out.splitlines()):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                pass
    return None


def sudo_systemctl(verb, unit):
    # Exactly the forms listed in the sudoers files; -n so a missing rule
    # fails at once instead of waiting for a password nobody will type.
    return run(["sudo", "-n", "/usr/bin/systemctl", verb, unit], timeout=60)


def is_active(unit):
    return run(["systemctl", "is-active", unit])[1] == "active"


def http_get(url, timeout=15, headers=None):
    """(status or None, parsed JSON or text). None = could not connect."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            status = r.status
    except urllib.error.HTTPError as e:
        body, status = e.read().decode("utf-8", "replace"), e.code
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, str(getattr(e, "reason", e))
    try:
        return status, json.loads(body)
    except ValueError:
        return status, body[:300]


def http_post_json(url, obj, timeout=30, headers=None):
    data = json.dumps(obj).encode()
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, str(getattr(e, "reason", e))


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def age_s(iso):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    except (TypeError, ValueError):
        return None


def minutes(seconds):
    return f"{int(seconds // 60)} min" if seconds is not None else "?"


# --- Checks -------------------------------------------------------------------
#
# Each returns {id, label, status, detail, fix?, manual?}:
#   status   ok | warn | fail | off (not applicable / not set up)
#   detail   one line saying what was found, in plain French
#   fix      the FIXES key that should cure it, if a button can
#   manual   what a person has to do with their hands, when a button cannot
#
# `full` is False for the minute-by-minute report to Caliban: the slow or
# intrusive parts (listing the cameras, git fetch) only run when someone is
# actually looking.

def check(id, label, status, detail, fix=None, manual=None):
    return {"id": id, "label": label, "status": status, "detail": detail,
            "fix": fix, "manual": manual}


def check_power(full):
    rc, out = run(["vcgencmd", "get_throttled"])
    if rc != 0 or "=" not in out:
        return check("power", "Alimentation", "off", "Mesure indisponible.")
    bits = int(out.split("=")[1], 16)
    rc, temp = run(["vcgencmd", "measure_temp"])
    t = temp.replace("temp=", "").replace("'C", " °C") if rc == 0 else "?"
    if bits & 0x1:
        return check("power", "Alimentation", "fail", f"Tension trop basse en ce moment ({t}).",
                     manual="Vérifier que le bloc d'alimentation officiel 27 W est branché, et pas une rallonge USB.")
    if bits & 0x10000:
        return check("power", "Alimentation", "warn", f"La tension a été trop basse depuis le démarrage ({t}).",
                     manual="Vérifier le bloc d'alimentation et son câble.")
    try:
        if float(t.split()[0]) >= 80:
            return check("power", "Alimentation", "warn", f"Le Pi chauffe : {t}.",
                         manual="Dégager les aérations du boîtier.")
    except ValueError:
        pass
    # The hardware watchdog (install-doctor.sh): without it a hard freeze
    # needs someone on site to pull the plug.
    wd = run(["systemctl", "show", "-p", "RuntimeWatchdogUSec", "--value"])[1].strip()
    if wd in ("0", "infinity", ""):
        return check("power", "Alimentation", "warn",
                     f"Tension correcte, {t}, mais pas de redémarrage automatique si le Pi se fige.",
                     manual="Prévenir l'AQ (install-doctor.sh).")
    return check("power", "Alimentation", "ok", f"Tension correcte, {t}.")


def check_disk(full):
    u = shutil.disk_usage("/")
    free_pct = 100 * u.free / u.total
    detail = f"{u.free / 1e9:.1f} Go libres ({free_pct:.0f} %)."
    if free_pct < DISK_FAIL_PCT:
        return check("disk", "Espace disque", "fail", detail, fix="free_disk")
    if free_pct < DISK_WARN_PCT:
        return check("disk", "Espace disque", "warn", detail, fix="free_disk")
    return check("disk", "Espace disque", "ok", detail)


def check_clock(full):
    rc, out = run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    if rc != 0:
        return check("clock", "Horloge", "off", "État inconnu.")
    if out.strip() != "yes":
        # The Pi has no clock battery: every boot starts unsynced until NTP
        # answers. Offering "Redémarrer le Pi" then just started it over
        # (2026-10-16, right after a reboot), so not before CLOCK_GRACE_S.
        try:
            with open("/proc/uptime") as f:
                up = float(f.read().split()[0])
        except (OSError, ValueError, IndexError):
            up = None
        if up is not None and up < CLOCK_GRACE_S:
            return check("clock", "Horloge", "ok",
                         f"Synchronisation en cours (Pi démarré il y a {max(1, round(up / 60))} min).")
        # A wrong clock breaks HTTPS to Caliban outright; it fixes itself once
        # the network is back, and a reboot forces it.
        return check("clock", "Horloge", "warn", "Heure non synchronisée avec Internet.", fix="reboot")
    return check("clock", "Horloge", "ok", "Synchronisée.")


def check_wifi(full):
    rc, out = run(["nmcli", "-t", "-f", "TYPE,STATE,CONNECTION", "device"])
    if rc != 0:
        return check("wifi", "Wi-Fi", "off", "État inconnu.")
    wifi = [l.split(":") for l in out.splitlines() if l.startswith("wifi:")]
    up = [w for w in wifi if len(w) >= 3 and w[1] == "connected"]
    if not up:
        return check("wifi", "Wi-Fi", "fail", "Pas connecté au Wi-Fi de l'usine.", fix="cycle_wifi",
                     manual="Si ça ne revient pas : le Wi-Fi de l'usine est-il en panne ailleurs aussi ?")
    status, _ = http_get("https://www.google.com/generate_204", timeout=10)
    if status is None:
        return check("wifi", "Wi-Fi", "fail", f"Connecté à « {up[0][2]} », mais pas d'accès à Internet.",
                     fix="cycle_wifi")
    # The watchdog that brings the Pi back on its own (../netwatch.sh). Without
    # it, an outage lasts until someone presses a button at the destoner.
    if run(["systemctl", "is-enabled", "caliban-rig-netwatch.timer"])[1] != "enabled":
        return check("wifi", "Wi-Fi", "warn",
                     f"Connecté à « {up[0][2]} », mais la reconnexion automatique n'est pas installée.",
                     manual="Prévenir l'AQ (install-doctor.sh).")
    return check("wifi", "Wi-Fi", "ok", f"Connecté à « {up[0][2]} ».")


def check_caliban(full):
    if not CALIBAN_URL:
        return check("caliban", "Prospero (nuage)", "fail", "Adresse de Prospero absente sur ce Pi.",
                     manual=f"Installation incomplète : prévenir l'AQ (fichier {ENV_FILE}).")
    status, body = http_get(f"{CALIBAN_URL}/health", timeout=20)
    if status is None:
        return check("caliban", "Prospero (nuage)", "fail", "Prospero ne répond pas.",
                     manual="Si le Wi-Fi est bon, la panne est dans le nuage, pas ici : prévenir l'AQ.")
    if status != 200:
        return check("caliban", "Prospero (nuage)", "fail", f"Prospero répond en erreur ({status}).",
                     manual="La panne est dans le nuage, pas ici : prévenir l'AQ.")
    sha = (body.get("build_sha") or "")[:7] if isinstance(body, dict) else ""
    return check("caliban", "Prospero (nuage)", "ok", f"Répond (version {sha or '?'}).")


def check_key(full):
    if not API_KEY:
        return check("key", "Clé d'accès", "fail", "Clé absente sur ce Pi.",
                     manual=f"Installation incomplète : prévenir l'AQ (fichier {ENV_FILE}).")
    status, body = http_get(f"{CALIBAN_URL}/doctor/ping", timeout=20, headers={"X-API-Key": API_KEY})
    if status is None:
        return check("key", "Clé d'accès", "off", "Non vérifiable sans Prospero.")
    if status == 401:
        return check("key", "Clé d'accès", "fail", "Prospero refuse la clé de ce Pi.",
                     manual="La clé a changé dans Azure : prévenir l'AQ.")
    if status != 200:
        return check("key", "Clé d'accès", "off", f"Non vérifiable ({status}).")
    return check("key", "Clé d'accès", "ok", "Acceptée.")


_git_fetched_at = 0


def check_update(full):
    global _git_fetched_at
    if not os.path.isdir(os.path.join(REPO, ".git")):
        return check("update", "Logiciel", "off", "Pas un dépôt git.")
    if full and time.time() - _git_fetched_at > GIT_FETCH_EVERY_S:
        if run(["git", "fetch", "--quiet"], timeout=60, cwd=REPO)[0] == 0:
            _git_fetched_at = time.time()
    head = run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO)[1]
    rc, behind = run(["git", "rev-list", "--count", "HEAD..@{u}"], cwd=REPO)
    dirty = run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO)[1]
    timer = run(["systemctl", "is-enabled", UPDATE_TIMER])[1]
    if os.path.exists(UPDATE_PAUSE_FILE):
        return check("update", "Logiciel", "warn",
                     f"Version {head} : revenu en arrière, mises à jour automatiques en pause.",
                     fix="update_code",
                     manual="Une fois le problème corrigé sur GitHub, « Remettre le logiciel à jour » les relance.")
    if dirty:
        return check("update", "Logiciel", "warn",
                     f"Version {head}, modifiée à la main sur ce Pi : les mises à jour sont bloquées.",
                     fix="update_code")
    if rc == 0 and behind.isdigit() and int(behind) > 0 and full:
        return check("update", "Logiciel", "warn",
                     f"Version {head}, {behind} mise(s) à jour en attente.", fix="update_code")
    if timer != "enabled":
        return check("update", "Logiciel", "warn",
                     f"Version {head}. Les mises à jour automatiques ne sont pas activées.",
                     manual="Installation incomplète : prévenir l'AQ (caliban-rig-update.timer).")
    return check("update", "Logiciel", "ok", f"Version {head}, à jour.")


def check_screen(full):
    if os.path.exists(DEMO_DROPIN):
        return check("screen", "Écran destoner", "fail", "Ancien réglage DÉMO resté en place : l'écran ne peut pas démarrer.",
                     manual="Prévenir l'AQ (retirer demo.conf).")
    if not is_active(SCREEN):
        return check("screen", "Écran destoner", "fail", "Le service de l'écran est arrêté.", fix="restart_screen")
    status, _ = http_get("http://127.0.0.1:8090/state", timeout=5)
    if status != 200:
        return check("screen", "Écran destoner", "fail", "Le service de l'écran ne répond pas.", fix="restart_screen")
    return check("screen", "Écran destoner", "ok", "Répond.")


# The touchscreen polls screen.py every 2 s, or this page every 10 s when it
# fell back here; a minute of neither with Chromium running is a frozen screen
# (2026-10-16: stuck on 13 h 16 after a screen restart, every check green).
PAGE_FROZEN_S = 60
doctor_page_seen = None


def kiosk_running():
    return run(["pgrep", "-f", "--", "--kiosk"])[0] == 0


def check_kiosk(full):
    try:
        with open(AUTOSTART) as f:
            autostart = "kiosk.sh" in f.read()
    except OSError:
        autostart = False
    if not autostart:
        return check("kiosk", "Navigateur plein écran", "fail",
                     "Le plein écran n'est plus lancé au démarrage.", fix="restore_kiosk")
    if not kiosk_running():
        return check("kiosk", "Navigateur plein écran", "fail", "Le navigateur n'est pas ouvert.", fix="reboot")
    status, body = http_get("http://127.0.0.1:8090/state", timeout=5)
    seen = (body.get("server") or {}).get("page_seen_s") if status == 200 and isinstance(body, dict) else None
    here = doctor_page_seen is not None and time.monotonic() - doctor_page_seen < PAGE_FROZEN_S
    if isinstance(seen, (int, float)) and seen > PAGE_FROZEN_S and not here:
        return check("kiosk", "Navigateur plein écran", "fail",
                     f"Ouvert, mais l'écran ne bouge plus depuis {round(seen / 60)} min (figé).",
                     fix="restart_kiosk", manual="Si ça ne revient pas : « Redémarrer le Pi ».")
    return check("kiosk", "Navigateur plein écran", "ok", "Ouvert.")


def check_loop(full):
    state = load_state()
    if not is_active(LOOP):
        enabled = run(["systemctl", "is-enabled", LOOP])[1]
        return check("loop", "Lectures automatiques", "fail",
                     "Arrêtées." + (" (Pas lancées au démarrage non plus.)" if enabled != "enabled" else
                                    " Peut-être volontairement, pour un réglage de la caméra."),
                     fix="restart_loop")
    age = age_s(state.get("updated_at"))
    if age is None or age > STATE_STALE_S:
        return check("loop", "Lectures automatiques", "fail",
                     f"En marche, mais rien de nouveau depuis {minutes(age)}.", fix="restart_loop")
    r = state.get("reading") or {}
    line = state.get("line") or {}
    if line.get("running") is False:
        what = "ligne arrêtée, en attente"
    elif line.get("product") is False:
        what = "pas de produit sur la chute"
    else:
        what = f"dernière lecture il y a {minutes(age_s(r.get('at')))}" if r.get("at") else "pas encore de lecture"
    return check("loop", "Lectures automatiques", "ok", f"En marche : {what}.")


def check_camera(full):
    h = (load_state().get("health") or {})
    cam = h.get("camera", "ok")
    if full and not is_active(LOOP):
        # Only while the loop is off: it holds the camera otherwise, and
        # nothing here should touch the camera underneath a reading.
        rc, out = run(["rpicam-hello", "--list-cameras"], timeout=20)
        if rc == 0 and "Available cameras" not in out:
            return check("camera", "Caméra", "fail", "Aucune caméra détectée.", fix="reboot",
                         manual="Vérifier que la nappe de la caméra est bien enfoncée des deux côtés (Pi éteint).")
    if cam == "low":
        return check("camera", "Caméra", "warn", h.get("message") or "Trop de lumière sous la couverture.",
                     manual="Vérifier que le bac de couverture est bien en place sur la caméra.")
    if cam not in ("ok", None):
        return check("camera", "Caméra", "fail", "Pas de photo à la dernière lecture.", fix="restart_loop",
                     manual="Si ça revient : vérifier la nappe de la caméra (Pi éteint), puis redémarrer le Pi.")
    return check("camera", "Caméra", "ok", "Photos normales.")


def check_flash(full):
    state = load_state()
    h = state.get("health") or {}
    flash = h.get("flash", "ok")
    levels = state.get("flash_levels") or []
    lvl = f" (force {levels[-1]:.2f})" if levels and isinstance(levels[-1], (int, float)) else ""
    if flash == "low":
        return check("flash", "Flash", "warn", f"Plus faible que d'habitude{lvl}.",
                     manual="Changer les 4 piles AA du flash (piles rechargeables chargées).")
    if flash not in ("ok", None):
        return check("flash", "Flash", "fail", "Ne se déclenche pas.", fix="test_flash",
                     manual="1. Appuyer sur un bouton du flash pour le réveiller. "
                            "2. S'il ne s'allume pas : changer les piles. 3. Puis « Tester le flash ».")
    last = age_s(state.get("last_flash_at"))
    return check("flash", "Flash", "ok", f"Dernier éclair il y a {minutes(last)}{lvl}." if last is not None else "OK.")


# Seconds per burst above which the detector is called slow: a burst of 8
# took 5 s on 2026-10-18, so 20 s means something is wrong (heat, memory).
DETECTOR_SLOW_S = 20


def check_detector(full):
    """The MEO detector (meo_detect.py) on the last burst, as the loop recorded
    it. Caliban refuses readings without it when it decides with the
    detector (inline_settings.decider = yolo)."""
    d = load_state().get("detector") or {}
    if not d:
        return check("detector", "Détecteur MEO", "off", "Pas encore utilisé depuis le démarrage.")
    if not d.get("ok"):
        err = d.get("error") or "erreur inconnue"
        if err.startswith("absent"):
            return check("detector", "Détecteur MEO", "fail", "Pas installé sur ce Pi.",
                         manual="Prévenir l'AQ : installer onnxruntime sur Ariel "
                                "(pip3 install --user --break-system-packages --no-deps onnxruntime "
                                "flatbuffers protobuf packaging), puis redémarrer les lectures.")
        return check("detector", "Détecteur MEO", "fail", f"En erreur à la dernière lecture ({err[:80]}).",
                     fix="restart_loop")
    s, n = d.get("s"), d.get("photos")
    detail = f"{n} photo(s) en {s} s (modèle {d.get('version')}), il y a {minutes(age_s(d.get('at')))}."
    if s is not None and s > DETECTOR_SLOW_S:
        return check("detector", "Détecteur MEO", "warn", "Lent : " + detail,
                     manual="Vérifier la température du Pi (vcgencmd measure_temp) et la mémoire libre (free -h).")
    return check("detector", "Détecteur MEO", "ok", detail)


def tray_busy():
    """A capture is in progress (poller.py's busy marker, fresh)."""
    try:
        return time.time() - os.path.getmtime(BUSY_FILE) < BUSY_STALE_S
    except OSError:
        return False


def check_disk_tray(full):
    # The originals in ~/captures are the only copy until moved to SharePoint
    # (monthly reminder issue), so there is no button that deletes them.
    u = shutil.disk_usage("/")
    free_pct = 100 * u.free / u.total
    rc, size = run(["du", "-sh", TRAY_CAPTURES], timeout=30)
    held = f" Photos du banc : {size.split()[0]}." if rc == 0 and size else ""
    detail = f"{u.free / 1e9:.1f} Go libres ({free_pct:.0f} %).{held}"
    manual = "Déplacer les photos de ~/captures vers SharePoint (voir le rappel mensuel sur GitHub)."
    if free_pct < DISK_FAIL_PCT:
        return check("disk", "Espace disque", "fail", detail, manual=manual)
    if free_pct < DISK_WARN_PCT:
        return check("disk", "Espace disque", "warn", detail, manual=manual)
    return check("disk", "Espace disque", "ok", detail)


def check_poller(full):
    if not is_active(POLLER):
        return check("poller", "Prise des photos", "fail", "Le service du banc est arrêté.", fix="restart_poller")
    # poller.py logs at least "idle" every 10 minutes while waiting.
    rc, recent = run(["journalctl", "-u", POLLER, "--since", "-15min", "-q", "--no-pager", "-o", "cat"])
    if rc == 0 and not recent.strip():
        return check("poller", "Prise des photos", "fail",
                     "En marche, mais muet depuis 15 min : probablement bloqué.", fix="restart_poller")
    rc, failed = run(["journalctl", "-u", POLLER, "--since", "-2h", "-q", "--no-pager", "-o", "cat",
                      "--grep", "capture failed"])
    n = len([l for l in failed.splitlines() if l.strip()]) if rc == 0 else 0
    if n:
        return check("poller", "Prise des photos", "warn", f"{n} capture(s) échouée(s) depuis 2 h.",
                     manual="« Envoyer le journal » pour voir pourquoi.")
    return check("poller", "Prise des photos", "ok",
                 "Capture en cours." if tray_busy() else "En attente des demandes.")


def check_camera_tray(full):
    if tray_busy():
        return check("camera", "Caméra", "ok", "Utilisée par une capture en cours.")
    # The poller opens the camera only for a capture, so listing it between
    # captures touches nothing.
    rc, out = run(["rpicam-hello", "--list-cameras"], timeout=20)
    if rc == 127:
        return check("camera", "Caméra", "off", "Vérification indisponible.")
    if "Available cameras" not in out:
        return check("camera", "Caméra", "fail", "Aucune caméra détectée.", fix="reboot",
                     manual="Si ça revient après le redémarrage : vérifier la nappe de la caméra (Pi éteint).")
    model = out.split("Available cameras", 1)[1].split(":", 2)[-1].strip().split()[0] if ":" in out else ""
    return check("camera", "Caméra", "ok", f"Détectée{f' ({model})' if model else ''}.")


def check_tof(full):
    if tray_busy():
        return check("tof", "Capteur de hauteur", "ok", "Utilisé par une capture en cours.")
    rc, out = run(["i2cdetect", "-y", "-r", TOF_I2C_BUS, TOF_I2C_ADDR, TOF_I2C_ADDR])
    if rc == 127:
        return check("tof", "Capteur de hauteur", "off", "Vérification indisponible.")
    if TOF_I2C_ADDR[2:] not in out.split("\n", 1)[-1]:
        # poller.py stopped reading it on 2026-10-08 (1112c50): absent is
        # unused, not broken. Back to "warn" when shoot() reads it again.
        return check("tof", "Capteur de hauteur", "off", "Absent (non utilisé par les captures).")
    return check("tof", "Capteur de hauteur", "ok", "Répond.")


def check_calibration(full):
    try:
        age = time.time() - os.path.getmtime(RIG_SETTINGS)
    except OSError:
        return check("calibration", "Calibration", "fail", "Aucune calibration enregistrée.",
                     manual="Calibrer le banc depuis SGSC (Dépistage visuel → Calibrer le banc).")
    hours = age / 3600
    return check("calibration", "Calibration", "ok",
                 f"Dernière il y a {hours:.0f} h." if hours < 48 else f"Dernière il y a {hours / 24:.0f} jours.")


CHECKS_ARIEL = [check_power, check_disk, check_clock, check_wifi, check_caliban, check_key,
                check_update, check_screen, check_kiosk, check_loop, check_camera, check_flash,
                check_detector]
CHECKS_TRAY = [check_power, check_disk_tray, check_clock, check_wifi, check_caliban, check_key,
               check_update, check_poller, check_camera_tray, check_tof, check_calibration]
CHECKS = CHECKS_TRAY if TRAY else CHECKS_ARIEL


def _safe(fn, full):
    try:
        return fn(full)
    except Exception as e:
        name = fn.__name__.removeprefix("check_")
        return check(name, name, "off", f"Vérification impossible : {type(e).__name__}: {e}")


# A report right after a repair catches services still coming back (the
# browser in lwrespawn's 1 s gap, 2026-10-16) and offered "Redémarrer le Pi"
# for it. For SETTLE_S after a repair, a failing check waits for the next
# report instead: shown as "en cours", with no button, not counted.
SETTLE_S = 75
last_fix_done = 0.0


def settled(report):
    since = time.monotonic() - last_fix_done
    if since >= SETTLE_S:
        return report
    for c in report["checks"]:
        if c["status"] in ("fail", "warn"):
            c.update(status="off", fix=None, manual=None,
                     detail="Vérification en cours après la réparation (prochain rapport).")
    report["overall"] = "ok"     # nothing failing or warning is left
    return report


def run_checks(full=True):
    # In parallel: with the network down, the Wi-Fi, Caliban and key checks
    # each wait out their timeout, and one after the other that is most of a
    # minute of blank page for someone standing at the screen.
    with ThreadPoolExecutor(max_workers=len(CHECKS)) as pool:
        out = list(pool.map(lambda fn: _safe(fn, full), CHECKS))
    worst = "fail" if any(c["status"] == "fail" for c in out) else \
            "warn" if any(c["status"] == "warn" for c in out) else "ok"
    return {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "overall": worst, "checks": out}


# --- Fixes --------------------------------------------------------------------
#
# Each returns (ok, French sentence saying what happened). Run one at a time
# (fix_lock): two repairs racing each other over the same service is a fault
# of its own.

fix_lock = threading.Lock()


def fix_restart_screen():
    rc, out = sudo_systemctl("restart", SCREEN)
    if rc != 0:
        return False, f"Échec du redémarrage de l'écran : {out[:200]}"
    time.sleep(3)
    return is_active(SCREEN), "Écran redémarré." if is_active(SCREEN) else "L'écran ne repart pas."


def fix_restart_loop():
    rc, out = sudo_systemctl("restart", LOOP)
    if rc != 0:
        return False, f"Échec du redémarrage des lectures : {out[:200]}"
    time.sleep(3)
    return (True, "Lectures redémarrées : la prochaine photo est prise tout de suite.") if is_active(LOOP) \
        else (False, "Les lectures ne repartent pas : voir le journal (prévenir l'AQ).")


def fix_restart_kiosk():
    if not kiosk_running():
        return False, "Le navigateur n'est pas ouvert : redémarrer le Pi."
    # lwrespawn (from the autostart line) reopens it at once. A frozen browser
    # may never act on a polite TERM -- a stopped one cannot -- so KILL what is
    # still there after a few seconds. Only those PIDs: lwrespawn's new one
    # may already be up.
    old = run(["pgrep", "-f", "chromium"])[1].split()
    run(["pkill", "-f", "chromium"])
    for _ in range(5):
        time.sleep(1)
        old = [p for p in old if os.path.exists(f"/proc/{p}")]
        if not old:
            break
    else:
        run(["kill", "-KILL", *old])
    # Report only once the new one is up: the report sent straight after this
    # re-runs check_kiosk, which during lwrespawn's 1 s gap read "pas ouvert"
    # and offered a reboot (2026-10-16).
    for _ in range(20):
        time.sleep(1)
        if kiosk_running():
            return True, "Navigateur rouvert."
    return False, "Le navigateur ne s'est pas rouvert : « Redémarrer le Pi »."


def fix_restore_kiosk():
    line = "/usr/bin/lwrespawn /home/ttownshend/caliban/rig/inline/kiosk.sh &\n"
    os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
    with open(AUTOSTART, "w") as f:
        f.write(line)
    return True, "Lancement automatique rétabli. Redémarrer le Pi pour qu'il prenne effet."


def fix_cycle_wifi():
    rc1, o1 = run(["sudo", "-n", "/usr/bin/nmcli", "radio", "wifi", "off"])
    time.sleep(5)
    rc2, o2 = run(["sudo", "-n", "/usr/bin/nmcli", "radio", "wifi", "on"])
    if rc1 or rc2:
        return False, f"Impossible de relancer le Wi-Fi : {(o1 or o2)[:200]}"
    for _ in range(12):
        time.sleep(5)
        if check_wifi(False)["status"] == "ok":
            return True, "Wi-Fi relancé et reconnecté."
    return False, "Wi-Fi relancé, mais toujours pas de connexion."


def restart_rig_services():
    """After the code under them changed. The tray poller's sudo rule allows
    restart, not try-restart; callers have already refused during a capture."""
    if TRAY:
        sudo_systemctl("restart", POLLER)
    else:
        sudo_systemctl("try-restart", LOOP)
        sudo_systemctl("try-restart", SCREEN)


BUSY_MESSAGE = "Une capture est en cours : réessayer dans une minute."


def fix_update_code():
    if TRAY and tray_busy():
        return False, BUSY_MESSAGE
    if run(["git", "fetch", "--quiet"], timeout=90, cwd=REPO)[0] != 0:
        return False, "Impossible de joindre GitHub : vérifier le Wi-Fi."
    # The Pi only ever consumes this repo. Anything changed by hand here is
    # what is blocking update.sh's --ff-only pull, and is not worth keeping on
    # a device nobody edits on purpose. Calibration lives in ~, not the repo.
    rc, out = run(["git", "reset", "--hard", "@{u}"], cwd=REPO)
    if rc != 0:
        return False, f"Échec de la mise à jour : {out[:200]}"
    try:
        os.remove(UPDATE_PAUSE_FILE)
    except OSError:
        pass
    head = run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO)[1]
    restart_rig_services()
    # This process is now running the old code; systemd restarts it on the new.
    threading.Timer(5, lambda: os._exit(0)).start()
    return True, f"Logiciel remis à la version {head} de GitHub ; services redémarrés."


def fix_free_disk():
    u = shutil.disk_usage("/")
    need = u.total * DISK_TARGET_PCT / 100 - u.free
    if need <= 0:
        return True, "Assez d'espace, rien à effacer."
    cutoff = time.time() - KEEP_DAYS * 86400
    files = []
    for root, _, names in os.walk(CAPTURE_DIR):
        for n in names:
            p = os.path.join(root, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if st.st_mtime < cutoff:
                files.append((st.st_mtime, st.st_size, p))
    freed = count = 0
    for _, size, p in sorted(files):
        if freed >= need:
            break
        try:
            os.remove(p)
            freed += size
            count += 1
        except OSError:
            pass
    ok = shutil.disk_usage("/").free >= u.total * DISK_WARN_PCT / 100
    return ok, f"{count} vieille(s) photo(s) effacée(s), {freed / 1e6:.0f} Mo libérés." + \
        ("" if ok else " Toujours trop plein : prévenir l'AQ.")


TEST_SCRIPT = """
import json, sys
sys.path.insert(0, %r)
import flashcam
try:
    m = flashcam.capture(%r, dict(flashcam.load_settings(), attempts=3))
    print(json.dumps({"ok": True, "flash_level": m.get("flash_level"), "clipped_pct": m.get("clipped_pct")}))
except flashcam.FlashNotFired as e:
    print(json.dumps({"ok": False, "why": "nofire", "error": str(e)}))
except Exception as e:
    print(json.dumps({"ok": False, "why": "camera", "error": type(e).__name__ + ": " + str(e)}))
""" % (HERE, TEST_PHOTO)


def fix_test_flash():
    # The loop holds the camera and the flash's GPIO; it has to let go first,
    # and is put back only if it was running -- a loop stopped on purpose for
    # bench work stays stopped.
    was_running = is_active(LOOP)
    if was_running:
        sudo_systemctl("stop", LOOP)
        time.sleep(2)
    try:
        rc, out = run([sys.executable, "-c", TEST_SCRIPT], timeout=120, cwd=HERE)
    finally:
        if was_running:
            sudo_systemctl("start", LOOP)
    res = last_json(out)
    if res is None:
        return False, f"Test impossible : {out[-200:]}"
    if res.get("ok"):
        return True, "Photo prise, le flash fonctionne.", {"attachment_file": TEST_PHOTO}
    if res.get("why") == "nofire":
        return False, "Le flash ne s'est pas déclenché : le réveiller (un bouton) ou changer les piles, puis réessayer."
    return False, f"La caméra n'a pas pris de photo : {res.get('error', '')[:150]}"


def fix_send_logs():
    """The rig's recent journals, for reading from a desk."""
    cmd = ["journalctl", "--no-pager", "-o", "short-iso", "-n", str(LOG_LINES)]
    for u in (LOG_UNITS_TRAY if TRAY else LOG_UNITS):
        cmd += ["-u", u]
    rc, out = run(cmd, timeout=30)
    if rc != 0 or not out.strip():
        return False, f"Journal illisible : {out[:200]}"
    head = run(["git", "log", "--oneline", "-3"], cwd=REPO)[1]
    if TRAY:
        try:
            with open(RIG_SETTINGS) as f:
                about = f"== Calibration (rig_settings.json)\n{f.read()[:4000]}"
        except OSError:
            about = "== Calibration : aucune"
    else:
        state = load_state()
        state.pop("history", None)
        state.pop("flash_levels", None)
        about = (f"== État de l'écran (inline_state.json)\n"
                 f"{json.dumps(state, ensure_ascii=False, indent=1)[:4000]}")
    text = f"== Version\n{head}\n\n{about}\n\n== Journal, {LOG_LINES} dernières lignes\n{out}"
    return True, f"{len(out.splitlines())} lignes de journal envoyées.", {"output": text}


def note_good_version(report):
    """Called after every report. A version that has run with no failing
    check for GOOD_AFTER_REPORTS reports in a row becomes the rollback point."""
    clean = report.get("overall") != "fail"
    note_good_version.streak = note_good_version.streak + 1 if clean else 0
    if note_good_version.streak < GOOD_AFTER_REPORTS:
        return
    head = run(["git", "rev-parse", "HEAD"], cwd=REPO)[1].strip()
    good = read_good_versions()
    if good[-1:] == [head]:
        return
    write_good_versions([s for s in good if s != head] + [head])
    log(f"version {head[:7]} ran clean for {GOOD_AFTER_REPORTS} reports: rollback point")


note_good_version.streak = 0


def read_good_versions():
    try:
        with open(GOOD_SHA_FILE) as f:
            return [line.strip() for line in f if line.strip()]
    except OSError:
        return []


def write_good_versions(good):
    with open(GOOD_SHA_FILE, "w") as f:
        f.write("".join(sha + "\n" for sha in good[-GOOD_KEPT:]))


def fix_rollback_code():
    if TRAY and tray_busy():
        return False, BUSY_MESSAGE
    # Back past the version in place even when it has itself run clean: a
    # version can pass every check and still be wrong (2026-10-16: the only
    # remembered version was the current one, so the button refused).
    head = run(["git", "rev-parse", "HEAD"], cwd=REPO)[1].strip()
    earlier = [s for s in read_good_versions() if s != head]
    if not earlier:
        return False, "Aucune version précédente connue (il faut 30 minutes sans panne pour en retenir une)."
    good = earlier[-1]
    # Before the reset: update.sh must not pull the bad version straight back.
    open(UPDATE_PAUSE_FILE, "w").close()
    rc, out = run(["git", "reset", "--hard", good], cwd=REPO)
    if rc != 0:
        return False, f"Échec du retour en arrière : {out[:200]}"
    # Left on purpose: never offered as a rollback point again. If it comes
    # back through an update and runs clean, it is remembered afresh.
    write_good_versions(earlier)
    restart_rig_services()
    threading.Timer(5, lambda: os._exit(0)).start()
    return True, (f"Revenu à la version {good[:7]} (au lieu de {head[:7]}). Mises à jour automatiques "
                  "en pause jusqu'à « Remettre le logiciel à jour ».")


def fix_restart_poller():
    if tray_busy():
        return False, BUSY_MESSAGE
    rc, out = sudo_systemctl("restart", POLLER)
    if rc != 0:
        return False, f"Échec du redémarrage : {out[:200]}"
    time.sleep(3)
    return (True, "Service du banc redémarré.") if is_active(POLLER) \
        else (False, "Le service du banc ne repart pas : « Envoyer le journal ».")


TRAY_TEST_SCRIPT = """
import json, sys
from pathlib import Path
sys.path.insert(0, %r)
try:
    import capture
    capture.OUTPUT_DIR = Path(%r)
    print(json.dumps({"ok": True, "path": capture.capture("DOCTOR-TEST", "visible")}))
except SystemExit as e:
    print(json.dumps({"ok": False, "error": str(e)}))
except Exception as e:
    print(json.dumps({"ok": False, "error": type(e).__name__ + ": " + str(e)}))
""" % (RIG_DIR, TRAY_TEST_DIR)


def fix_test_photo_tray():
    """A photo with the bench's locked settings, of whatever is on the tray.
    The poller is stopped for it, so no operator's capture can collide with
    it, and started again only if it was running."""
    if tray_busy():
        return False, BUSY_MESSAGE
    was_running = is_active(POLLER)
    if was_running:
        sudo_systemctl("stop", POLLER)
    try:
        shutil.rmtree(TRAY_TEST_DIR, ignore_errors=True)
        rc, out = run([sys.executable, "-c", TRAY_TEST_SCRIPT], timeout=90, cwd=RIG_DIR)
    finally:
        if was_running:
            sudo_systemctl("start", POLLER)
    res = last_json(out)
    if res is None:
        return False, f"Photo impossible : {out[-200:]}"
    if not res.get("ok"):
        return False, f"Photo impossible : {res.get('error', '')[:200]}"
    return True, "Photo prise avec les réglages du banc.", {"attachment_file": res["path"]}


def fix_reboot():
    # Delayed so the answer reaches whoever pressed the button first.
    threading.Timer(3, lambda: run(["sudo", "-n", "/usr/bin/systemctl", "reboot"])).start()
    return True, "Redémarrage du Pi : l'écran revient dans environ 1 minute."


# Order matters for repair_all: the network before anything that needs it,
# the services before what they report.
FIXES = {
    "cycle_wifi":     ("Relancer le Wi-Fi", fix_cycle_wifi),
    "free_disk":      ("Libérer de l'espace", fix_free_disk),
    "update_code":    ("Remettre le logiciel à jour", fix_update_code),
    "restart_screen": ("Redémarrer l'écran", fix_restart_screen),
    "restore_kiosk":  ("Rétablir le plein écran", fix_restore_kiosk),
    "restart_kiosk":  ("Rouvrir le navigateur", fix_restart_kiosk),
    "restart_loop":   ("Redémarrer les lectures", fix_restart_loop),
    "test_flash":     ("Tester le flash", fix_test_flash),
    "test_photo":     ("Photo de contrôle", fix_test_flash),
    "send_logs":      ("Envoyer le journal", fix_send_logs),
    "rollback_code":  ("Revenir à la version précédente", fix_rollback_code),
    "reboot":         ("Redémarrer le Pi", fix_reboot),
}

FIXES_TRAY = {
    "cycle_wifi":     ("Relancer le Wi-Fi", fix_cycle_wifi),
    "update_code":    ("Remettre le logiciel à jour", fix_update_code),
    "restart_poller": ("Redémarrer le service du banc", fix_restart_poller),
    "test_photo":     ("Photo de contrôle", fix_test_photo_tray),
    "send_logs":      ("Envoyer le journal", fix_send_logs),
    "rollback_code":  ("Revenir à la version précédente", fix_rollback_code),
    "reboot":         ("Redémarrer le Pi", fix_reboot),
}
if TRAY:
    FIXES = FIXES_TRAY

# Never applied by "Tout réparer": the reboot is its own decision, the flash
# test only means something after someone has woken the flash by hand, and
# the code update restarts this process from under the rest of the run.
NOT_IN_REPAIR_ALL = {"reboot", "test_flash", "update_code",
                     "test_photo", "send_logs", "rollback_code"}
# These end this process: say "started" to Caliban before running them.
ENDS_PROCESS = {"reboot", "update_code", "rollback_code"}


def repair_all():
    before = run_checks(full=True)
    wanted = {c["fix"] for c in before["checks"] if c["status"] in ("fail", "warn") and c.get("fix")}
    done = []
    for name, (label, fn) in FIXES.items():
        if name in wanted and name not in NOT_IN_REPAIR_ALL:
            ok, msg = fn()[:2]
            done.append(f"{'✓' if ok else '✗'} {msg}")
    after = run_checks(full=True)
    still = [c["label"] for c in after["checks"] if c["status"] == "fail"]
    if not done:
        summary = "Rien à réparer automatiquement."
    else:
        summary = " ".join(done)
    if still:
        summary += " Toujours en panne : " + ", ".join(still) + "."
        left = {c["fix"] for c in after["checks"] if c["status"] == "fail"}
        if "reboot" in left or any(f in NOT_IN_REPAIR_ALL for f in left):
            summary += " Voir les boutons ci-dessous."
    return not still, summary


def apply_fix(name):
    global last_fix_done
    if name == "repair_all":
        fn = repair_all
    elif name in FIXES:
        fn = FIXES[name][1]
    else:
        return False, f"Réparation inconnue : {name}", {}
    if not fix_lock.acquire(timeout=1):
        return False, "Une autre réparation est déjà en cours : patientez.", {}
    try:
        log(f"fix {name}")
        out = fn()
        ok, msg, extra = out[0], out[1], (out[2] if len(out) > 2 else {})
        log(f"fix {name}: {'ok' if ok else 'FAILED'} -- {msg}")
        return ok, msg, extra
    except Exception as e:
        traceback.print_exc()
        return False, f"Erreur pendant la réparation : {type(e).__name__}: {e}", {}
    finally:
        last_fix_done = time.monotonic()
        fix_lock.release()


# --- Remote: report to Caliban, run what SGSC queued ---------------------------

pending_results = []
results_lock = threading.Lock()


def upload_attachment(cid, path):
    """Send a repair's photo to Caliban, which files it against the request."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        return f"photo introuvable : {e}"
    req = urllib.request.Request(f"{CALIBAN_URL}/doctor/attachment?command_id={int(cid)}",
                                 data=data, method="POST",
                                 headers={"Content-Type": "image/jpeg", "X-API-Key": API_KEY})
    try:
        urllib.request.urlopen(req, timeout=60).read()
        return None
    except (urllib.error.URLError, OSError) as e:
        return f"photo non envoyée : {e}"


def report_once():
    report = settled(run_checks(full=False))
    note_good_version(report)
    with results_lock:
        results, pending_results[:] = list(pending_results), []
    status, body = http_post_json(f"{CALIBAN_URL}/doctor/report",
                                  {"report": report, "results": results},
                                  headers={"X-API-Key": API_KEY})
    if status != 200 or not isinstance(body, dict):
        with results_lock:
            pending_results[:0] = results     # try them again next time
        return []
    return body.get("commands") or []


def reporter():
    if not CALIBAN_URL or not API_KEY:
        log("no CALIBAN_URL / API_KEY: not reporting to Caliban")
        return
    try:
        with open(INFLIGHT_FILE) as f:
            cut = json.load(f)
        os.remove(INFLIGHT_FILE)
        log(f"remote request #{cut['id']} ({cut['action']}) was interrupted by a restart")
        pending_results.append({"id": cut["id"], "ok": False, "detail":
            "Interrompu : le diagnostic a redémarré pendant la réparation "
            "(mise à jour automatique ?). Vérifier l'état ci-dessus, relancer si besoin."})
    except (OSError, ValueError, KeyError, TypeError):
        pass
    while True:
        try:
            for cmd in report_once():
                action, cid = cmd.get("action"), cmd.get("id")
                log(f"remote request #{cid}: {action}")
                if action in ENDS_PROCESS:
                    with results_lock:
                        pending_results.append({"id": cid, "ok": True, "detail": "Lancé."})
                    report_once()
                else:
                    with open(INFLIGHT_FILE, "w") as f:
                        json.dump({"id": cid, "action": action}, f)
                ok, msg, extra = apply_fix(action)
                if extra.get("attachment_file"):
                    problem = upload_attachment(cid, extra["attachment_file"])
                    if problem:
                        msg += f" ({problem})"
                result = {"id": cid, "ok": ok, "detail": msg}
                if extra.get("output"):
                    result["output"] = extra["output"][-60000:]
                with results_lock:
                    pending_results.append(result)
                try:
                    os.remove(INFLIGHT_FILE)
                except OSError:
                    pass
                report_once()
        except Exception as e:
            log(f"report failed: {type(e).__name__}: {e}")
        time.sleep(REPORT_EVERY_S)


# --- HTTP (the touchscreen) -----------------------------------------------------

class Handler(server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            try:
                with open(PAGE_FILE, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError as e:
                self._send(500, f"doctor.html unreadable: {e}".encode(), "text/plain")
        elif path == "/checks":
            global doctor_page_seen
            doctor_page_seen = time.monotonic()
            report = settled(run_checks(full=True))
            report["fixes"] = {k: v[0] for k, v in FIXES.items()}
            report["device"] = DEVICE
            self._json(200, report)
        elif path == "/test-photo":
            photo = TEST_PHOTO
            if TRAY:
                found = sorted(os.listdir(TRAY_TEST_DIR)) if os.path.isdir(TRAY_TEST_DIR) else []
                photo = os.path.join(TRAY_TEST_DIR, found[-1]) if found else ""
            try:
                with open(photo, "rb") as f:
                    self._send(200, f.read(), "image/jpeg")
            except OSError:
                self._send(404, b"no test photo", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/fix":
            return self._send(404, b"not found", "text/plain")
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 1000)) or b"{}")
        except ValueError:
            return self._json(400, {"ok": False, "message": "Requête invalide."})
        ok, msg, _ = apply_fix(str(body.get("action")))
        self._json(200, {"ok": ok, "message": msg})

    def log_message(self, *args):
        pass


class Server(ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    args = sys.argv[1:]
    if args[:1] == ["check"]:
        # Ask the running service first: it has /etc/caliban-inline.env
        # (root-only, loaded by systemd), and a shell typing this does not --
        # checked directly, Caliban and the key would read as missing when
        # they are not (2026-10-13, first install).
        status, report = http_get(f"http://127.0.0.1:{PORT}/checks", timeout=90)
        if status != 200 or not isinstance(report, dict):
            print("(caliban-doctor ne répond pas : vérification directe, sans la clé)\n")
            report = run_checks(full=True)
        for c in report["checks"]:
            mark = {"ok": "OK  ", "warn": "!!  ", "fail": "XX  ", "off": "--  "}[c["status"]]
            print(f"{mark}{c['label']:<24} {c['detail']}" + (f"   [fix: {c['fix']}]" if c.get("fix") else ""))
            if c.get("manual") and c["status"] != "ok":
                print(f"{'':28} -> {c['manual']}")
        return
    if args[:1] == ["fix"] and len(args) == 2:
        ok, msg, extra = apply_fix(args[1])
        print(msg)
        if extra.get("output"):
            print(extra["output"])
        sys.exit(0 if ok else 1)
    if args:
        sys.exit(__doc__)

    threading.Thread(target=reporter, daemon=True).start()
    log(f"doctor ({DEVICE}) on http://127.0.0.1:{PORT}/ -> reporting to {CALIBAN_URL or '(nowhere)'}")
    Server(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
