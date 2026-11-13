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
Only one Step row is open at a time: no new one until OPEN_FOR_S has passed
(its ValidUntilUtc), answered or not -- after an applied step the settle time
holds the next one back longer still.

Ignition's answers, read back (IGNITION_CONFIRMS):
    unset / 0  not read at all. Shadow, since 2026-11-13: Ignition records,
               the fan is not moved, the operator's screen decides as before.
    log        read and logged ("ignition: answered ..."), nothing else: for
               checking Ignition's side before it is trusted.
    1          also a confirmation, as if the operator had tapped the screen:
               a line in ~/inline_acks.jsonl (screen.py's own file), so the
               settle time, the run of steps, the screen's pending instruction
               and the record in Supabase (inline_acks) all work as they do for
               a tap. Without this, once Ignition answers within seconds, the
               next reading asks again before the product has settled.
    Counted as a confirmation: an applied step (AppliedStep +1/-1), as
    "adjusted" on that reading with its instruction; and a change of fan Hz
    nobody asked for (HzBefore differs from the last answer's HzAfter: a
    manual change, a start-up setpoint), as "adjusted" with instruction
    "manual_hz", which starts a new settle and ends any run of steps. A
    refusal (AppliedStep 0, same Hz) is logged only.
    Supabase keeps reading_id, choice and time of a confirmation, not who made
    it; the Ignition table (AppliedStep, HzBefore/HzAfter, by ReadingId) is the
    record of which ones were Ignition's. The ack line on the Pi says so too
    ("by": "ignition").
    Answers are read once per reading at most (one SELECT on the index, the
    last hour of Cam1's rows). Which rows have been seen is kept in
    ~/ignition_seen.json, so a restart never confirms the same answer twice;
    on the very first start every answer already there counts as seen.

Never in the way of a reading: offer() only hands the row to a background
thread, which keeps one connection open and gives up on a slow server after
a few seconds. A failure is logged and shown in state["ignition"] (for the
doctor); the reading is not affected. Gentle with the plant's server: only
ever INSERTs, never UPDATE or DELETE; at most one Step row per OPEN_FOR_S and
one heartbeat per HEARTBEAT_S; on start, one SELECT of Cam1's newest row
so the timing carries on across restarts; after a failure, nothing at all
for RETRY_AFTER_S.

Setup: /etc/caliban-inline.env, the same names as sgsc-app's ignition bridge:
    IGNITION_SQL_SERVER=dru-svr-sql-p01.sanimax.int   # unset: nothing is written
    IGNITION_SQL_PORT=1433                            # optional
    IGNITION_SQL_DATABASE=...
    IGNITION_SQL_USER=CameraUser
    IGNITION_SQL_PASSWORD=...
    IGNITION_CAMERA_ID=1                              # optional, Cam1
    IGNITION_CONFIRMS=log                             # optional, see above
Needs pymssql (sudo apt install python3-pymssql, or pip).
"""

import json
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
CONFIRMS = os.environ.get("IGNITION_CONFIRMS", "").strip().lower()
CONFIRMS = CONFIRMS if CONFIRMS in ("log", "1") else ""

HOME = os.path.expanduser("~")
ACK_FILE = os.path.join(HOME, "inline_acks.jsonl")      # screen.py's, same lines
SEEN_FILE = os.path.join(HOME, "ignition_seen.json")
ANSWERS_LOOKBACK_S = 3600   # answers looked for among this last hour of rows
SEEN_KEPT = 500

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
                   "step": _step(reading), "decision_pct": pct,
                   # What a confirmation of this step carries, as a tap would.
                   "ack": {k: reading.get(k) for k in ("instruction", "decision_pct", "turn_cm",
                                                       "turn_qt", "turn_unit") if reading.get(k) is not None}}
        else:
            # No reading this time (stopped line, empty chute, a fault): only
            # ever a heartbeat, and only if one is due.
            row = {"reading_id": now.strftime("HB-%Y%m%d-%H%M%S"), "at": now,
                   "camera_state": _camera_state(state), "step": 0, "decision_pct": None}
        # A confirmation must name an IL- reading (Supabase's rule); a manual
        # Hz change seen on a heartbeat's answer goes on the latest one.
        row["latest_reading"] = reading.get("id")
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
_resumed = False            # _resume_from_table() done since this start
_steps_written = {}         # CameraEntryId -> the reading's fields, for its confirmation
_last_move_at = None        # when a fan move (applied step, Hz change) was last seen
RETRY_AFTER_S = 60          # after a failure, nothing is tried for this long
_retry_at = None


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
    """True for OPEN_FOR_S after the last Step row, answered or not: never more
    than one Step row per OPEN_FOR_S. After an applied step the settle time
    holds the next one back far longer anyway; after a refusal (a limit, an
    interlock) this stops a row every reading for as long as it lasts."""
    if _open_step is None:
        return False
    return (now - _open_step[1]).total_seconds() < OPEN_FOR_S


def _resume_from_table():
    """Once per start: carry on from Cam1's newest row in the table, so a
    restart (or a loop restarting over and over) writes no extra rows. One
    SELECT, on the (CameraId, ReadingTimeUtc) index."""
    global _last_written_at, _open_step, _resumed
    cur = _connect().cursor()
    cur.execute("SELECT TOP 1 CameraEntryId, ReadingTimeUtc, Step, AppliedTimeUtc FROM dbo.CameraEntries "
                "WHERE CameraId = %s ORDER BY ReadingTimeUtc DESC", (CAMERA_ID,))
    got = cur.fetchone()
    if got:
        entry_id, at, step, applied = got
        at = at.replace(tzinfo=timezone.utc)
        _last_written_at = at
        if step and applied is None:
            _open_step = (str(entry_id), at)
    _resumed = True


def _write(row):
    global _last_written_at, _open_step
    if not _resumed:
        _resume_from_table()
    now = datetime.now(timezone.utc)
    step = row["step"]
    if step != 0 and _step_still_open(now):
        step = 0    # one request at a time; this reading still counts as a heartbeat
    if step != 0 and _last_move_at is not None and row["at"] <= _last_move_at:
        step = 0    # decided before the fan's last move was seen: it would ask twice
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
        _steps_written[entry_id] = dict(row.get("ack") or {}, reading_id=row["reading_id"])
        while len(_steps_written) > 50:
            _steps_written.pop(next(iter(_steps_written)))
    status.update(ok=True, at=now.isoformat(timespec="seconds"), error=None,
                  last={"reading_id": row["reading_id"], "step": step, "camera_state": row["camera_state"]})
    if step != 0:
        _log(f"ignition: {row['reading_id']} Step {step:+d} ({row['decision_pct']} %)")
    elif not status.get("written"):
        _log(f"ignition: writing to {SERVER}/{DATABASE} as Cam{CAMERA_ID} "
             f"(first row {row['reading_id']}, {row['camera_state']})")
    status["written"] = status.get("written", 0) + 1


# --- Ignition's answers (IGNITION_CONFIRMS) ---------------------------------------

_seen = None    # {"ids": [CameraEntryId, ...], "hz": last HzAfter seen}


def _load_seen():
    try:
        with open(SEEN_FILE) as f:
            got = json.load(f)
        return {"ids": list(got.get("ids") or []), "hz": got.get("hz")}
    except (OSError, ValueError):
        return None


def _save_seen():
    tmp = SEEN_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"ids": _seen["ids"][-SEEN_KEPT:], "hz": _seen["hz"]}, f)
    os.replace(tmp, SEEN_FILE)


def _num(v):
    try:
        return None if v is None else round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _append_ack(ack):
    """The same line screen.py writes for a tap; loop.py forwards it."""
    with open(ACK_FILE, "a") as f:
        f.write(json.dumps(ack, ensure_ascii=False) + "\n")


def _read_answers(latest_reading):
    """Ignition's answers not seen yet, oldest first: logged, and with
    IGNITION_CONFIRMS=1 the moves made confirmations."""
    global _seen, _last_move_at
    first_start = False
    if _seen is None:
        _seen = _load_seen()
        if _seen is None:
            _seen, first_start = {"ids": [], "hz": None}, True
    since = _utc_naive(datetime.now(timezone.utc) - timedelta(seconds=ANSWERS_LOOKBACK_S))
    cur = _connect().cursor()
    cur.execute("SELECT CameraEntryId, ReadingId, Step, AppliedTimeUtc, AppliedStep, HzBefore, HzAfter, "
                "IgnitionNote FROM dbo.CameraEntries WHERE CameraId = %s AND ReadingTimeUtc >= %s "
                "AND AppliedTimeUtc IS NOT NULL ORDER BY AppliedTimeUtc", (CAMERA_ID, since))
    rows = cur.fetchall()
    seen_ids = set(_seen["ids"])
    new = [r for r in rows if str(r[0]) not in seen_ids]
    if not new:
        return
    if first_start:
        # Answers from before this code ran are history, not news.
        _seen["ids"] += [str(r[0]) for r in new]
        _seen["hz"] = _num(new[-1][6])
        _save_seen()
        return
    now = datetime.now(timezone.utc)
    for entry_id, reading_id, step, applied_at, applied, hz_before, hz_after, note in new:
        entry_id = str(entry_id)
        hz_before, hz_after = _num(hz_before), _num(hz_after)
        applied = int(applied) if applied is not None else 0
        _log(f"ignition: answered {reading_id} Step {step:+d} -> AppliedStep {applied:+d}, "
             f"Hz {hz_before} -> {hz_after}" + (f" ({note})" if note else ""))
        # Ignition is meant to write UTC; a local time would be ~5 h out.
        lag = (now - applied_at.replace(tzinfo=timezone.utc)).total_seconds() if applied_at else 0
        if abs(lag) > 600:
            _log(f"ignition: AppliedTimeUtc {applied_at} is {lag / 3600:+.1f} h from now -- is Ignition writing UTC?")
        ack = None
        if applied in (1, -1):
            fields = _steps_written.get(entry_id) or {
                "reading_id": reading_id, "instruction": "increase_medium" if applied > 0 else "decrease"}
            ack = dict(fields, choice="adjusted")
        elif (hz_before is not None and _seen["hz"] is not None and abs(hz_before - _seen["hz"]) >= 0.01) \
                or (hz_before is not None and hz_after is not None and abs(hz_after - hz_before) >= 0.01):
            # The fan moved without a step from us: by hand, or Ignition's own
            # start-up setpoint. Counting starts again from here all the same.
            _log(f"ignition: fan Hz changed outside a step ({_seen['hz']} -> {hz_before} -> {hz_after})")
            ack = {"reading_id": reading_id if str(reading_id).startswith("IL-") else latest_reading,
                   "instruction": "manual_hz", "choice": "adjusted"}
        if hz_after is not None:
            _seen["hz"] = hz_after
        _seen["ids"].append(entry_id)
        if ack:
            _last_move_at = now
        if ack and CONFIRMS == "1" and str(ack.get("reading_id") or "").startswith("IL-"):
            ack.update(at=now.isoformat(timespec="seconds"), by="ignition",
                       hz_before=hz_before, hz_after=hz_after)
            _append_ack(ack)
            _log(f"ignition: counted as a confirmation of {ack['reading_id']} ({ack['instruction']})")
        status["last_answer"] = {"reading_id": reading_id, "applied_step": applied,
                                 "hz_after": hz_after, "note": note}
    _save_seen()


def _writer():
    global _offer, _retry_at
    failing = False
    while True:
        with _lock:
            while _offer is None:
                _lock.wait()
            row, _offer = _offer, None
        if _retry_at is not None and datetime.now(timezone.utc) < _retry_at:
            continue    # server failing: this row is dropped, a later one tries
        try:
            # Answers first: a move seen now means this row's reading was
            # decided before it, and must not ask again (_write).
            if CONFIRMS:
                if not _resumed:
                    _resume_from_table()
                _read_answers(row.get("latest_reading"))
            _write(row)
            _retry_at = None
            if failing:
                _log("ignition: writing again")
            failing = False
        except Exception as e:
            _drop_connection()
            _retry_at = datetime.now(timezone.utc) + timedelta(seconds=RETRY_AFTER_S)
            status.update(ok=False, at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          error=f"{type(e).__name__}: {e}"[:200])
            if not failing:
                _log(f"ignition: row not written: {type(e).__name__}: {e}")
            failing = True
