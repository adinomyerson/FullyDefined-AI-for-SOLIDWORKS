"""
One SolidWorks, one driver at a time across server processes.

Each chat in Claude Desktop or Claude Code starts its own server process, and they all talk to the same
SolidWorks. A Windows named mutex in the user's session is held for the length of every tool call, so two
chats cannot run calls at the same time: the second waits up to lock_wait_s, then is refused, naming the
process and tool that hold the lock. A note in the temp folder says who holds it. If the holder dies, Windows
hands the mutex on (abandoned), so a crash never leaves SolidWorks locked.

A mutex belongs to the thread that took it: acquire and release on the same thread (the worker).
"""

import json
import logging
import os
import tempfile
import time

import win32api
import win32event

from .results import Err, ToolError

logger = logging.getLogger(__name__)

NAME = "Local\\solidworks-mcp-call"
NOTE = os.path.join(tempfile.gettempdir(), "solidworks-mcp-lock.json")


def _holder(note_path: str) -> str:
    try:
        with open(note_path, encoding="utf-8") as f:
            note = json.load(f)
        return (f"server process {note['pid']} (running {note['tool']}, for {time.time() - note['since']:.0f} s)")
    except Exception:
        return "another server process"


class CallLock:
    def __init__(self, name: str = NAME, note: str = NOTE):
        self._handle = win32event.CreateMutex(None, False, name)
        self.note = note

    def acquire(self, tool: str, wait_s: float):
        """Take the lock for one call, or raise ToolError (BUSY) naming who holds it."""
        status = win32event.WaitForSingleObject(self._handle, int(max(0.0, wait_s) * 1000))
        if status == win32event.WAIT_ABANDONED:
            logger.warning("The previous holder of the SolidWorks lock ended without releasing it; taking it over")
        elif status != win32event.WAIT_OBJECT_0:
            holder = _holder(self.note)
            raise ToolError(f"SolidWorks is in use by {holder}: another chat is driving it. Only one chat can "
                            f"drive SolidWorks at a time; try again when it is done.", Err.BUSY,
                            {"holder": holder, "waited_s": wait_s})
        try:
            with open(self.note, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "tool": tool, "since": time.time()}, f)
        except OSError as e:
            logger.debug(f"Could not write the lock note: {e}")

    def release(self):
        win32event.ReleaseMutex(self._handle)

    def close(self):
        win32api.CloseHandle(self._handle)


_lock = None


def locked(tool: str, fn, wait_s: float):
    """Run fn() holding the process-wide SolidWorks lock (on the calling thread)."""
    global _lock
    if _lock is None:
        _lock = CallLock()
    _lock.acquire(tool, wait_s)
    try:
        return fn()
    finally:
        _lock.release()
