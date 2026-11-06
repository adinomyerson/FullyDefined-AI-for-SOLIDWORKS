"""
The one connection to SolidWorks.

Every tool gets the application through ctx.app, which attaches to a running
SolidWorks, or starts one, and reconnects once if SolidWorks was closed and
reopened since the last call.

Holds the connection: state that must outlive a reload of the other modules.
"""

import logging
import os
import time
from typing import Optional

import pythoncom
import win32com.client

from . import install, settings
from .com import busy_code, gone_code, value
from .results import Err, ToolError

logger = logging.getLogger(__name__)

# The lowest SolidWorks this server supports: RevisionNumber 30.x = SOLIDWORKS 2022 (no API member the tools call is
# newer than 2019; older versions are not supported).
MIN_MAJOR = 30


def year_of(major: int) -> int:
    """RevisionNumber's major -> the SOLIDWORKS year (30 -> 2022, 34 -> 2026)."""
    return major + 1992


def major_of(revision) -> Optional[int]:
    """'34.1.0' -> 34; None when it cannot be read."""
    try:
        return int(str(revision).split(".")[0])
    except (TypeError, ValueError):
        return None


class Connection:
    def __init__(self):
        self.app = None             # the SldWorks application object, or None
        self.pid = None             # the SolidWorks process id, for the dialog guard
        self.revision = None        # RevisionNumber as last read by alive() or _attach (no extra call)

    def alive(self) -> bool:
        if self.app is None:
            return False
        try:
            self.revision = value(self.app, "RevisionNumber")
            return True
        except Exception:
            logger.info("The SolidWorks connection is gone; reconnecting on the next call")
            self.app = None
            self.revision = None
            return False

    def after_error(self, e: BaseException) -> Optional[tuple]:
        """What the exception e says about SolidWorks: ("gone", code) when it closed or crashed, ("busy", what) when
        it is there but did not take the call, None when it answers (the error is the tool's own).
        In order: a gone COM code (com.GONE) is gone; a rejected call (com.BUSY) is busy; with no SolidWorks attached,
        None (nothing read); a modal dialog open in SolidWorks is busy (windows only, no SolidWorks read); then one
        read of RevisionNumber: an answer is None, a rejected read is busy, anything else is gone (a late-bound object
        of a SolidWorks that has closed raises AttributeError, win32com's dynamic __getattr__, not a com_error).
        `what` is {"com_code": name} or {"dialogs": [...]} (core/dialogs.py's list)."""
        code = gone_code(e)
        if code is not None:
            return "gone", code
        code = busy_code(e)
        if code is not None:
            return "busy", {"com_code": code, "dialogs": self._dialogs()}
        if self.app is None:
            return None
        found = self._dialogs()
        if found:
            return "busy", {"com_code": None, "dialogs": found}
        try:
            self.revision = value(self.app, "RevisionNumber")
            return None
        except Exception as probe:
            logger.info(f"SolidWorks does not answer after {type(e).__name__}: {type(probe).__name__}: {probe}")
            code = busy_code(probe)
            if code is not None:
                return "busy", {"com_code": code, "dialogs": []}
            return "gone", gone_code(probe) or "NO_ANSWER"

    def gone(self, e: BaseException) -> Optional[str]:
        """The code saying SolidWorks closed or crashed after the exception e (after_error), or None: busy or
        answering is never gone."""
        state = self.after_error(e)
        return state[1] if state and state[0] == "gone" else None

    def _dialogs(self) -> list:
        """The modal dialogs SolidWorks shows (core/dialogs.py), [] when none or when they cannot be listed."""
        try:
            from . import dialogs
            return dialogs.find_dialogs(self.pid)
        except Exception as e:
            logger.debug(f"Dialog check failed: {e}")
            return []

    def version(self) -> str:
        return str(value(self.app, "RevisionNumber"))

    def _attach(self, start: bool) -> bool:
        """Running instance only, or (start=True) let COM start one."""
        pythoncom.CoInitialize()
        try:
            if start:
                app = win32com.client.Dispatch("SldWorks.Application")
            else:
                app = win32com.client.GetActiveObject("SldWorks.Application")
            revision = value(app, "RevisionNumber")
        except Exception as e:
            logger.debug(f"{'Dispatch' if start else 'GetActiveObject'} failed: {e}")
            return False
        self.revision = revision
        app.Visible = True
        self.app = app
        try:
            self.pid = int(value(app, "GetProcessID"))
        except Exception as e:
            logger.debug(f"No process id: {e}")
            self.pid = None
        try:
            from . import selfcheck
            selfcheck.solidworks(app, self.pid)       # version and language, once per SolidWorks process
        except Exception as e:
            logger.debug(f"Self-check failed: {e}")
        return True

    def check_version(self) -> None:
        """Refuse a SolidWorks older than MIN_MAJOR, from the revision already read (no extra call). An unreadable
        revision is let through: the self-check logs it. The connection stays, so the next call reports the same."""
        major = major_of(self.revision)
        if major is not None and major < MIN_MAJOR:
            raise ToolError(f"This is SOLIDWORKS {year_of(major)} (revision {self.revision}). FullyDefined needs "
                            f"SOLIDWORKS {year_of(MIN_MAJOR)} or later. Update SOLIDWORKS, then try again.",
                            Err.VERSION, {"revision": str(self.revision), "lowest_supported": year_of(MIN_MAJOR)})

    def connect(self) -> dict:
        """Connect if needed. Returns what happened, or raises ToolError (no SolidWorks, or one too old)."""
        if self.alive():
            self.check_version()
            return {"version": self.version(), "launched": False}
        if self._attach(start=False):
            logger.info(f"Attached to SolidWorks {self.version()}")
            self.check_version()
            return {"version": self.version(), "launched": False}
        t0 = time.time()
        if self._attach(start=True):
            logger.info(f"Started SolidWorks {self.version()} through COM")
            self.check_version()
            return {"version": self.version(), "launched": True, "startup_time": int(time.time() - t0)}

        config = settings.load()
        exe = install.find_exe(config["exe_path"])
        if not exe:
            raise ToolError("Could not find SOLIDWORKS on this computer. Install it, or set exe_path in the settings file to the full "
                            "path of sldworks.exe.",
                            Err.NO_SOLIDWORKS)
        logger.info(f"Launching {exe}")
        os.startfile(exe)
        while time.time() - t0 < config["startup_timeout"]:
            time.sleep(config["connection_retry_interval"])
            if self._attach(start=False):
                self.check_version()
                return {"version": self.version(), "launched": True, "startup_time": int(time.time() - t0)}
        raise ToolError(f"SOLIDWORKS started but did not answer within {config['startup_timeout']}s. If it shows a dialog "
                         f"(licence, update, recovery), close it, then call connect_solidworks again.",
                        Err.CONNECTION)


connection = Connection()
