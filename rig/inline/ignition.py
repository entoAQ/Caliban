"""Cam1 -> Ignition: Ariel's requests to the destoner, as rows in the plant's
dbo.CameraEntries (SQL Server). The table and its rules are in
ignition_camera_entries.sql; this is the writing side.

What it writes, after each reading (loop.py calls offer()):
    a Step +1 / -1 row when the screen is asking for a turn: +1 when the
    reading's instruction is AUGMENTER with a turn to make, -1 on DIMINUER.
    An AUGMENTER held at the handle's limit, or with too few photos since the
    last turn, is Step 0, exactly as the screen shows no turn then.
    otherwise a Step 0 row every HEARTBEAT_S, so Ignition can tell the camera
    is alive -- with CameraState NO_PRODUCT while the line is stopped or the
    chute empty, FAULT while the camera, flash or analysis is failing.
Only one Step row is open at a time: no new one until Ignition has answered
the last (AppliedTimeUtc) or OPEN_FOR_S has passed (its ValidUntilUtc).

SHADOW (2026-11-13): Ignition reads and records, but does not move the fan.
Nothing here reads Ignition's answers back into the decision: the settle time
and the turn limits still come from the operator's confirmations on the
screen. Before Ignition acts on these rows, an applied step (AppliedStep,
HzAfter) has to count as a confirmation too -- otherwise, once Ignition answers
within seconds, the next reading asks again before the product has settled.

Never in the way of a reading: offer() only hands the row to a background
thread, which keeps one connection open and gives up on a slow server after
a few seconds. A failure is logged and shown in state["ignition"] (for the
doctor); the reading is not affected. Gentle with the plant's server: at most
one INSERT per reading that asks for a step, a heartbeat every 5 min, and one
small SELECT by primary key before a step while the previous one may be open.

Setup: /etc/caliban-inline.env, the same names as sgsc-app's ignition bridge:
    IGNITION_SQL_SERVER=dru-svr-sql-p01.sanimax.int   # unset: nothing is written
    IGNITION_SQL_PORT=1433                            # optional
    IGNITION_SQL_DATABASE=...
    IGNITION_SQL_USER=CameraUser
    IGNITION_SQL_PASSWORD=...
    IGNITION_CAMERA_ID=1                              # optional, Cam1
Needs pymssql (sudo apt install python3-pymssql, or pip).
"""

import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

SERVER = os.environ.get("IGNITION_SQL_SERVER", "").strip()
PORT = int(os.environ.get("IGNITION_SQL_PORT") or 1433)
DATABASE = os.environ.get("IGNITION_SQL_DATABASE", "").strip()
USER = os.environ.get("IGNITION_SQL_USER", "").strip()
PASSWORD = os.environ.get("IGNITION_SQL_PASSWORD", "")
CAMERA_ID = int(os.environ.get("IGNITION_CAMERA_ID") or 1)
ENABLED = bool(SERVER and DATABASE and USER)

HEARTBEAT_S = 300     # a row at least this often, Step 0 when nothing to ask
OPEN_FOR_S = 120      # a Step row's validity: ValidUntilUtc = ReadingTimeUtc + this
LOGIN_TIMEOUT_S = 5
QUERY_TIMEOUT_S = 10

# Fixed, so the same ReadingId always gives the same CameraEntryId: a retried
# INSERT hits the primary key instead of making a second row.
_NAMESPACE = uuid.UUID("6f1d3c52-8a4e-4b7e-9c1a-2d5e0b7f4a10")

# For the doctor (copied into the state file by loop.py): enabled, ok, when,
# the last row written, or the last error.
status = {"enabled": ENABLED}

_lock = threading.Condition()
_offer = None          # the newest row not yet handled; an older one is dropped
_seen_reading = None   # the reading id offered last, so one reading = one row
_thread = None
_log = print


def _utc_naive(dt):
    """datetime2 has no time zone: UTC, without the offset."""
    return dt.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)


def _step(reading):
    instruction = reading.get("instruction") or ""
    if instruction == "decrease":
        return -1
    if instruction.startswith("increase") and reading.get("turn_basis") != "limit":
        return 1
    return 0


def _camera_state(state):
    health = state.get("health") or {}
    if health.get("camera") == "fail" or health.get("flash") == "fail" or health.get("network") == "down":
        return "FAULT"
    line = state.get("line") or {}
    if line.get("running") is False or line.get("product") is False:
        return "NO_PRODUCT"
    return "FAULT"     # no reading came of this call, and nothing says why


def offer(state, log=print):
    """After each take_reading: hand Ignition's row for it to the writer.
    Cheap and never raises; the writer decides whether it is written."""
    global _seen_reading, _offer, _thread, _log
    if not ENABLED:
        return
    try:
        _log = log
        now = datetime.now(timezone.utc)
        reading = state.get("reading") or {}
        fresh = reading.get("id") and reading.get("id") != _seen_reading
        if fresh:
            _seen_reading = reading["id"]
            try:
                at = datetime.fromisoformat(reading["at"])
            except (KeyError, TypeError, ValueError):
                at = now
            pct = reading.get("decision_pct")
            pct = round(float(pct), 2) if isinstance(pct, (int, float)) and abs(pct) < 1000 else None
            row = {"reading_id": reading["id"], "at": at, "camera_state": "OK",
                   "step": _step(reading), "decision_pct": pct}
        else:
            # No reading this time (stopped line, empty chute, a fault): only
            # ever a heartbeat, and only if one is due.
            row = {"reading_id": now.strftime("HB-%Y%m%d-%H%M%S"), "at": now,
                   "camera_state": _camera_state(state), "step": 0, "decision_pct": None}
        with _lock:
            _offer = row
            if _thread is None or not _thread.is_alive():
                _thread = threading.Thread(target=_writer, name="ignition", daemon=True)
                _thread.start()
            _lock.notify()
    except Exception as e:
        log(f"ignition: row not offered: {type(e).__name__}: {e}")


# --- The writer thread ----------------------------------------------------------

_conn = None
_last_written_at = None     # UTC datetime of the last row written
_open_step = None           # (CameraEntryId, ReadingTimeUtc) of the last Step row


def _connect():
    global _conn
    if _conn is None:
        import pymssql
        _conn = pymssql.connect(server=SERVER, port=PORT, user=USER, password=PASSWORD,
                                database=DATABASE, login_timeout=LOGIN_TIMEOUT_S,
                                timeout=QUERY_TIMEOUT_S, autocommit=True, appname="Cam1-Ariel")
    return _conn


def _drop_connection():
    global _conn
    try:
        if _conn is not None:
            _conn.close()
    except Exception:
        pass
    _conn = None


def _step_still_open(now):
    """True while the last Step row is unanswered and still valid."""
    if _open_step is None:
        return False
    entry_id, at = _open_step
    if (now - at).total_seconds() >= OPEN_FOR_S:
        return False
    cur = _connect().cursor()
    cur.execute("SELECT AppliedTimeUtc FROM dbo.CameraEntries WHERE CameraEntryId = %s", (entry_id,))
    got = cur.fetchone()
    return got is not None and got[0] is None


def _write(row):
    global _last_written_at, _open_step
    now = datetime.now(timezone.utc)
    step = row["step"]
    if step != 0 and _step_still_open(now):
        step = 0    # one request at a time; this reading still counts as a heartbeat
    heartbeat_due = _last_written_at is None or (now - _last_written_at).total_seconds() >= HEARTBEAT_S
    if step == 0 and not heartbeat_due:
        return
    at = row["at"]
    entry_id = str(uuid.uuid5(_NAMESPACE, f"cam{CAMERA_ID}:{row['reading_id']}"))
    import pymssql
    try:
        _connect().cursor().execute(
            "INSERT INTO dbo.CameraEntries (CameraEntryId, CameraId, ReadingId, ReadingTimeUtc, "
            "ValidUntilUtc, CameraState, Step, DecisionMePct) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (entry_id, CAMERA_ID, row["reading_id"], _utc_naive(at),
             _utc_naive(at + timedelta(seconds=OPEN_FOR_S)), row["camera_state"], step,
             row["decision_pct"]))
    except pymssql.IntegrityError:
        pass    # already there: an earlier try went through before the connection broke
    _last_written_at = now
    if step != 0:
        _open_step = (entry_id, at)    # open until its ValidUntilUtc
    status.update(ok=True, at=now.isoformat(timespec="seconds"), error=None,
                  last={"reading_id": row["reading_id"], "step": step, "camera_state": row["camera_state"]})
    if step != 0:
        _log(f"ignition: {row['reading_id']} Step {step:+d} ({row['decision_pct']} %)")


def _writer():
    global _offer
    failing = False
    while True:
        with _lock:
            while _offer is None:
                _lock.wait()
            row, _offer = _offer, None
        try:
            _write(row)
            if failing:
                _log("ignition: writing again")
            failing = False
        except Exception as e:
            _drop_connection()
            status.update(ok=False, at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          error=f"{type(e).__name__}: {e}"[:200])
            if not failing:
                _log(f"ignition: row not written: {type(e).__name__}: {e}")
            failing = True
