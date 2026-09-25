"""Live updates for the admin portal: server-sent events fed by PostgreSQL LISTEN/NOTIFY.

Every insert into activity_events notifies the channel admin_events (sql/004_admin.sql). One
thread per replica holds a connection listening on it and hands each notice to the event loop,
which copies it into the queue of every open stream. Each stream also looks for rows it has not
sent whenever it is idle, so a notice lost while the listener reconnects still arrives. A stream
that falls behind is closed; the browser reconnects with Last-Event-ID and the rows it missed are
replayed from the table.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import AsyncIterator

from . import events
from .config import settings

log = logging.getLogger("rag.stream")

CHANNEL = "admin_events"
KEEPALIVE_SECONDS = 15.0
QUEUE_SIZE = 1000


def format_event(event: dict) -> str:
    body = {k: event.get(k) for k in ("id", "kind", "ref_type", "ref_id")}
    return f"id: {event['id']}\nevent: activity\ndata: {json.dumps(body)}\n\n"


class Hub:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ listener thread -------------

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._loop = loop
        self._stop.clear()
        self._thread = threading.Thread(target=self._listen, name="admin-events", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._thread = None

    def _listen(self) -> None:
        import psycopg

        backoff = 1.0
        while not self._stop.is_set():
            try:
                with psycopg.connect(settings.database_url, autocommit=True, connect_timeout=5) as conn:
                    conn.execute(f"LISTEN {CHANNEL}")
                    log.info("listening for activity events on %s", CHANNEL)
                    backoff = 1.0
                    while not self._stop.is_set():
                        for notice in conn.notifies(timeout=1.0):
                            self._hand_over(notice.payload)
            except Exception as exc:  # noqa: BLE001 - the database went away; streams catch up when idle
                log.warning("activity listener lost the database (%s); retrying in %.0fs", exc, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30.0)

    def _hand_over(self, payload: str) -> None:
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            return
        if self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self.publish, event)
            except RuntimeError:  # the loop closed during shutdown
                pass

    # ------------------------------------------------------------ fan-out (event loop) --------

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: dict) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # This stream fell behind: None ends it, and the browser reconnects and replays
                queue.get_nowait()
                queue.put_nowait(None)
                self._subscribers.discard(queue)


hub = Hub()


async def stream(last_id: int | None, expires_at: float) -> AsyncIterator[str]:
    """The text/event-stream body for one admin: activity events newer than last_id (the browser's
    Last-Event-ID) until the session expires, with a comment line whenever it is idle."""
    queue = hub.subscribe()
    try:
        if last_id is None:
            last_id = await asyncio.to_thread(events.latest_id)
            # An id without data moves the browser's Last-Event-ID without dispatching an event
            yield f"retry: 5000\nid: {last_id}\n\n"
        else:
            yield "retry: 5000\n\n"
            for event in await asyncio.to_thread(events.after, last_id):
                yield format_event(event)
                last_id = event["id"]
        while time.time() < expires_at:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
            except TimeoutError:
                missed = await asyncio.to_thread(events.after, last_id)
                for event in missed:
                    yield format_event(event)
                    last_id = event["id"]
                if not missed:
                    yield ": keepalive\n\n"
                continue
            if event is None:
                return
            if int(event.get("id") or 0) <= last_id:
                continue
            yield format_event(event)
            last_id = int(event["id"])
    finally:
        hub.unsubscribe(queue)
