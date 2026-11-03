"""
SolidWorks refreshes its toolbars each time it goes idle between two API calls, which makes long batches of calls
slow in a sketch of a few dozen entities. CommandInProgress tells SolidWorks a command is running, which stops the
refresh. The runner turns it on when a tool call first reaches
SolidWorks and always turns it off again when the call ends: success, refusal, error, a call that ends after its time
limit, or a call inside another call (only the outermost one sets and resets it). It is found on only when an earlier
server process died in the middle of a call; that is logged and cleared, never kept.
"""

import logging
import threading

from .com import value

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_state = {"depth": 0, "app": None, "before": None}


def enter(app) -> None:
    """One more call in progress; the first one saves SolidWorks' setting and turns it on."""
    with _lock:
        _state["depth"] += 1
        if _state["depth"] > 1:
            return
        try:
            if bool(value(app, "CommandInProgress")):
                # nobody sets it between commands: on here means a server that died mid-call left it on
                logger.info("CommandInProgress was on before the call (left over): it is put back off after")
            app.CommandInProgress = True
            before = False
        except Exception as e:
            logger.debug(f"Could not set CommandInProgress: {e}")
            before = None
        _state.update(app=app, before=before)


def leave() -> None:
    """One call fewer; the last one puts SolidWorks' setting back as it found it."""
    with _lock:
        if _state["depth"] <= 0:
            return
        _state["depth"] -= 1
        if _state["depth"] > 0:
            return
        app, before = _state["app"], _state["before"]
        _state.update(app=None, before=None)
        if before is None or app is None:
            return
        try:
            app.CommandInProgress = before
        except Exception as e:                      # SolidWorks gone: a new SolidWorks starts with its own setting
            logger.debug(f"Could not restore CommandInProgress: {e}")


def depth() -> int:
    return _state["depth"]


def unwind(to: int) -> None:
    """Leave until the depth is back to `to` (the runner's safety net for a call that entered and did not leave)."""
    while depth() > to:
        leave()
