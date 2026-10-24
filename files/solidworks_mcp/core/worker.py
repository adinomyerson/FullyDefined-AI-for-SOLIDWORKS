"""
The one thread that talks to SolidWorks.

COM objects may only be used on the thread that made them, and a SolidWorks call can take minutes or
never return (a dialog is waiting for someone). So every tool call runs on one dedicated worker thread,
and the server's main thread only waits for it with a time limit:

  - a call that does not finish within its limit returns an error;
  - a call that is stuck behind a SolidWorks dialog returns an error naming the dialog, as soon as it is seen;
  - while such a call is still stuck, other calls are refused with a clear "busy" message instead of
    queueing behind it. A thread cannot be killed: the job ends when someone answers the dialog.

Holds the thread: state that must outlive a reload of the other modules.
"""

import asyncio
import concurrent.futures
import logging
import threading
import time

import pythoncom

from . import dialogs, settings
from .connection import connection

logger = logging.getLogger(__name__)


class WorkerError(Exception):
    """The call did not run to the end: busy, timed out, or waiting on a dialog."""

    def __init__(self, message: str, code: int, data=None):
        super().__init__(message)
        self.message, self.code, self.data = message, code, data or {}


def _com_init():
    pythoncom.CoInitialize()


class Worker:
    def __init__(self):
        self._pool = None
        self._abandoned = None            # (future, job) of a call that was given up on but still runs
        self.thread_ids = set()

    def _executor(self):
        if self._pool is None:
            self._pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="solidworks", initializer=_com_init)
        return self._pool

    def busy_with(self):
        """The name and age (seconds) of an abandoned call that is still running, else None."""
        if self._abandoned is None:
            return None
        future, job = self._abandoned
        if future.done():
            self._abandoned = None
            return None
        started = job["started"] or job["queued"]
        return job["name"], time.monotonic() - started

    def _run_job(self, job, fn):
        job["started"] = time.monotonic()
        self.thread_ids.add(threading.get_ident())
        return fn()

    def _dialogs(self):
        try:
            return dialogs.find_dialogs(connection.pid)
        except Exception as e:
            logger.debug(f"Dialog check failed: {e}")
            return []

    def _abandon(self, future, job, why):
        self._abandoned = (future, job)

        def finished(f):
            exc = f.exception()
            logger.warning(f"The abandoned call {job['name']} ended after being given up on ({why})"
                           + (f" with {type(exc).__name__}: {exc}" if exc else ""))
        future.add_done_callback(finished)

    async def run(self, name, fn, timeout=None):
        """Run fn() on the worker thread. Returns what fn returns; raises WorkerError."""
        from .results import Err
        config = settings.load()
        limit = float(timeout or config["call_timeout_s"])
        check_after = float(config["dialog_check_after_s"])
        poll = max(0.05, float(config["dialog_poll_s"]))

        busy = self.busy_with()
        if busy is not None:
            found = self._dialogs()
            note = f' SolidWorks shows: {dialogs.describe(found)}.' if found else ""
            raise WorkerError(
                f"SolidWorks is still busy with {busy[0]} (started {busy[1]:.0f} s ago) and cannot take another "
                f"call.{note} If a dialog is open in SolidWorks, answer it; this call then ends and the next "
                f"one works.", Err.BUSY, {"busy_with": busy[0], "dialogs": [d["title"] for d in found]})

        job = {"name": name, "queued": time.monotonic(), "started": None}
        future = self._executor().submit(self._run_job, job, fn)
        awaited = asyncio.wrap_future(future)
        try:
            while True:
                try:
                    return await asyncio.wait_for(asyncio.shield(awaited), poll)
                except asyncio.TimeoutError:
                    pass
                if job["started"] is None:
                    continue                       # queued behind another call: its own limit starts when it runs
                running = time.monotonic() - job["started"]
                if running >= check_after:
                    found = self._dialogs()
                    if found:
                        self._abandon(future, job, "a dialog is open")
                        raise WorkerError(
                            f"SolidWorks is waiting on a dialog: {dialogs.describe(found)}. Answer or close it in "
                            f"SolidWorks. {name} is still waiting for it; until it ends, other calls are refused.",
                            Err.BUSY, {"dialogs": [d["title"] for d in found]})
                if running >= limit:
                    self._abandon(future, job, "time limit")
                    found = self._dialogs()
                    note = f" SolidWorks shows: {dialogs.describe(found)}." if found else ""
                    raise WorkerError(
                        f"{name} did not finish within {limit:.0f} s and was given up on.{note} It may still be "
                        f"running in SolidWorks; other calls are refused until it ends.",
                        Err.TIMEOUT, {"limit_s": limit})
        except asyncio.CancelledError:
            if not future.done():
                self._abandon(future, job, "cancelled by the client")
            raise


worker = Worker()
