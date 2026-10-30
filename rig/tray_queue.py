#!/usr/bin/env python3
"""The tray rig's capture queue, straight from Supabase instead of through Caliban.

Postgres for the queue: LISTEN to be woken, claim_capture_command to take work,
UPDATE to finish it. It logs in as tray_rig, which can read and finish capture
commands and nothing else (sgsc-app database/2026-10-30-tray-rig-role.sql).
Photos go to the rig-upload Edge Function, because Postgres can't write to Storage
and the service key never goes on this Pi.

Kept apart from poller.py so it can run and be tested without a camera.
"""

import io
import os
import select
import time

import psycopg2
import psycopg2.extras
import requests

DB_URL = os.environ.get("TRAY_RIG_DB_URL", "")
UPLOAD_URL = os.environ.get("RIG_UPLOAD_URL",
                            "https://pzjsxzgojucqaippctjy.supabase.co/functions/v1/rig-upload")
RIG_API_KEY = os.environ.get("RIG_API_KEY", "")

# Claim at least this often with nothing announced: it covers a notification
# missed across a reconnect, and it is the only thing that runs the queue's
# stale-claim reclaim. Direct to Postgres, so it adds nothing to the API log quota
# the old 3 s poll filled (2026-09-27).
SAFETY_CLAIM_SECONDS = 20
UPLOAD_TIMEOUT = 30

# What is kept in Storage: as Caliban's review_copy made it. The 12MP original
# stays on the rig in ~/captures, named in capture_commands.original_filename.
REVIEW_MAX_EDGE = 2048
REVIEW_QUALITY = 90

_listen = None
_last_claim = 0.0


def _connect():
    # Keepalives so a connection dropped by the Wi-Fi or the pooler shows up as an
    # error instead of a LISTEN that silently hears nothing.
    conn = psycopg2.connect(DB_URL, connect_timeout=15, keepalives=1, keepalives_idle=30,
                            keepalives_interval=10, keepalives_count=3)
    conn.autocommit = True
    return conn


def _listener():
    global _listen
    if _listen is None or _listen.closed:
        _listen = _connect()
        with _listen.cursor() as cur:
            cur.execute("LISTEN capture_commands")
    return _listen


def _drop_listener():
    global _listen
    try:
        if _listen is not None:
            _listen.close()
    except psycopg2.Error:
        pass
    _listen = None


def _woken(conn):
    conn.poll()
    woken = bool(conn.notifies)
    del conn.notifies[:]
    return woken


def claim(wait):
    """Take the oldest command, or report there is none. Returns (command or
    None, True).

    Holds for up to `wait` seconds, ending early on a notification or when the
    safety claim falls due. wait=0 claims at once, which drains a backlog: one
    notification can stand for several commands."""
    global _last_claim
    try:
        conn = _listener()
        woken = _woken(conn)
        remaining = min(wait, SAFETY_CLAIM_SECONDS - (time.monotonic() - _last_claim))
        if wait and not woken and remaining > 0:
            select.select([conn], [], [], remaining)
            woken = _woken(conn)
            if not woken and time.monotonic() - _last_claim < SAFETY_CLAIM_SECONDS:
                return None, True
        _last_claim = time.monotonic()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, lot_id, lot_number, kind FROM public.claim_capture_command()")
            row = cur.fetchone()
    except psycopg2.Error:
        # The next claim reconnects. The poller's own handler logs and backs off.
        _drop_listener()
        raise
    if not row or not row["id"]:
        return None, True
    return {
        "id": str(row["id"]),
        "lot_id": str(row["lot_id"]) if row["lot_id"] else None,
        "lot_number": row["lot_number"],
        "kind": row["kind"] or "capture",
    }, True


def review_copy(path):
    """The photo as it is kept in Storage. Anything unreadable goes as it came,
    rather than failing the capture over it."""
    from PIL import Image

    with open(path, "rb") as f:
        contents = f.read()
    try:
        img = Image.open(io.BytesIO(contents)).convert("RGB")
    except Exception as e:
        print(f"review copy skipped, stored as captured: {type(e).__name__}: {e}", flush=True)
        return contents
    if max(img.size) > REVIEW_MAX_EDGE:
        img.thumbnail((REVIEW_MAX_EDGE, REVIEW_MAX_EDGE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=REVIEW_QUALITY)
    return buf.getvalue()


def _store(command_id, band, path):
    resp = requests.post(
        UPLOAD_URL,
        params={"command_id": command_id, "band": band},
        headers={"X-API-Key": RIG_API_KEY, "Content-Type": "image/jpeg"},
        data=review_copy(path),
        timeout=UPLOAD_TIMEOUT,
    )
    if not resp.ok:
        raise RuntimeError(f"Envoi de la photo : {resp.status_code} {resp.text[:300]}")
    return resp.json()["path"]


def _finish(command_id, **fields):
    """Mark a command finished, on its own connection: a failure can be reported
    from the watchdog's thread while the main one is stuck in the camera."""
    columns = ", ".join(f"{k} = %s" for k in fields)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE public.capture_commands SET {columns}, completed_at = now() WHERE id = %s",
                        [*fields.values(), command_id])
            if cur.rowcount != 1:
                raise RuntimeError(f"Commande {command_id} introuvable.")
    finally:
        conn.close()


def complete_result(command_id, result):
    """A command that produced measurements rather than a photo."""
    _finish(command_id, status="done", result=psycopg2.extras.Json(result))


def complete_photos(command_id, visible_path, ir_path):
    """Storage first, row second, and the row only if storage succeeded: the
    reverse would mark done a command whose photo does not exist."""
    image_path = _store(command_id, "visible", visible_path)
    ir_image_path = _store(command_id, "ir", ir_path) if ir_path else None
    _finish(command_id, status="done", image_path=image_path, ir_image_path=ir_image_path,
            original_filename=os.path.basename(visible_path),
            ir_original_filename=os.path.basename(ir_path) if ir_path else None)


def fail(command_id, message):
    _finish(command_id, status="failed", error=(message or "Erreur inconnue")[:1000])
