"""
Wakes the rig's long-poll the moment a capture command is queued.

The rig used to ask for work every 3 s, and every ask was a Supabase RPC:
~28,800 calls a day, nearly all answering "nothing to do", which on its own
filled the free tier's 1 GB of log ingestion (2026-09-27). Now the rig holds
a request open for up to a minute (see capture_commands_next), and this module
tells that request when something has actually arrived.

Commands are inserted straight into Supabase by the browser, never through
Caliban, so the only way to hear about one is to listen to the table: a
Realtime subscription to INSERTs on capture_commands. Each gunicorn worker
runs its own, because the rig's request can land on either.

The listener is an accelerator, never the source of truth. While it is not
subscribed, healthy() is False and the endpoint answers at once, which puts
the rig back on its old 3 s poll. Anything queued while it was down is caught
by the claim made when it comes back, and by the endpoint's periodic safety
claim, which also keeps the queue's stale-claim reclaim running.

Requires capture_commands in the supabase_realtime publication
(rig/command_wakeup.sql).
"""
import asyncio
import os
import time

from realtime import AsyncRealtimeClient
from realtime.types import ChannelStates

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

CHECK_SECONDS = 5
# How long a dropped subscription gets to recover on the library's own
# reconnect before the whole client is thrown away and rebuilt.
REBUILD_AFTER_SECONDS = 60
RETRY_SECONDS = 30

_wake = asyncio.Event()
_healthy = False
# Set only by Realtime's own "Subscribed to PostgreSQL" system message. A
# joined channel is not enough: without the table in the publication the join
# still succeeds and no INSERT ever arrives, and the rig would then wait on the
# safety claim for every capture instead of falling back to its 3 s poll.
_pg_subscribed = False


def healthy():
    return _healthy


def pending():
    """Something may have been queued since the last claim."""
    return _wake.is_set()


def consume():
    """Called just before claiming: a command queued after this point sets
    the flag again, so it is never lost between the clear and the claim."""
    _wake.clear()


async def wait(timeout):
    """Wait for a queued command. True if woken, False on timeout."""
    try:
        await asyncio.wait_for(_wake.wait(), timeout)
        return True
    except asyncio.TimeoutError:
        return False


def _on_insert(_payload):
    _wake.set()


def _on_system(payload):
    global _pg_subscribed
    if payload.extension == "postgres_changes" and payload.status == "ok":
        _pg_subscribed = True


async def run():
    """Keep one subscription alive for the life of the worker."""
    global _healthy, _pg_subscribed
    while True:
        client = None
        _pg_subscribed = False
        try:
            client = AsyncRealtimeClient(f"{SUPABASE_URL}/realtime/v1", token=SUPABASE_SERVICE_KEY)
            await client.connect()
            channel = client.channel("capture-commands-wakeup")
            channel.on_postgres_changes(
                "INSERT", schema="public", table="capture_commands", callback=_on_insert
            )
            channel.on_system(_on_system)
            await channel.subscribe()

            unhealthy_since = time.monotonic()
            while True:
                joined = client.is_connected and channel.state == ChannelStates.JOINED
                if not joined:
                    # A rejoin confirms itself again with a fresh system message.
                    _pg_subscribed = False
                ok = joined and _pg_subscribed
                if ok and not _healthy:
                    print("[command wakeup] listening")
                    # Anything queued while nobody was listening.
                    _wake.set()
                elif not ok and _healthy:
                    print("[command wakeup] subscription lost")
                    unhealthy_since = time.monotonic()
                _healthy = ok
                if not ok and time.monotonic() - unhealthy_since > REBUILD_AFTER_SECONDS:
                    print("[command wakeup] not subscribed, rebuilding the client")
                    break
                await asyncio.sleep(CHECK_SECONDS)
        except asyncio.CancelledError:
            _healthy = False
            raise
        except Exception as e:
            print(f"[command wakeup failed] {type(e).__name__}: {e}")

        _healthy = False
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass
        await asyncio.sleep(RETRY_SECONDS)
