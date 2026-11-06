"""
Auto-save: after a tool call changes a document the server created and saved, the server saves it again (settings
auto_save, auto_save_minutes, auto_save_versions; on by default, every 5 minutes).

Whose: only files the server created (core/backup.py's "ours"). The user's own files are saved only after the user
has allowed editing them: that comes with the permission to edit; until then user_files_allowed() says no and a
user's file is never auto-saved (nothing is read). Then, never before its backup exists: may_save() (the copy-first
rule) also checks the copy is still on disk.

When: at the end of a successful call of a tool that changes the document it ran on (not a read-only tool, not a
saving tool, which saves by itself), on the active document, after core/backup.py has looked at that call. With
auto_save_minutes N above 0 (5 by default): at that document's first change in this session, then only at the first
such call N minutes or more after its last auto-save; 0: after every change. No timer runs between calls: only a
tool call may drive SOLIDWORKS.

Parts only: an assembly's or a drawing's save also writes every referenced file with unsaved changes (a user's part
among them, with no copy), so they are never auto-saved.

Never: a document with no file yet (new or never saved: nothing is asked or read); a document with a sketch open
(its entities are saved by a later change, after it closes); a user's file (above); an assembly or a drawing; a file
under protected_paths. Settings that are not a real true / false or a number of 0 or more count as their default.

auto_save_versions: each auto-save also copies the saved file to <name>_v<N><ext> next to it (the next free N,
never over an existing file); those copies are the server's own files.

A save that fails never fails the call: the result says plainly that the change is in SOLIDWORKS only. For a file
the server created, the data always says what happened, with no words in the message: "auto_save": saved, not due,
sketch open, unchanged, off, not for assemblies and drawings, protected folder, failed, no backup; a save adds
"auto_saved": path. So the AI can tell a change that is still in SOLIDWORKS only.
If SOLIDWORKS closes or crashes during the save, the result says so in plain words (the change is lost if SOLIDWORKS
closed without saving), the data adds solidworks_gone and com_code, and the connection is dropped, as the runner
does for a call during which SOLIDWORKS closed.

Cost: no SOLIDWORKS read for a document with no file, a user's file, a call before the interval is up, or when
auto_save is off; otherwise the open-sketch check, the changed flag and the save itself.
"""

import logging
import math
import os
import shutil
import time
from pathlib import Path
from typing import Optional

from . import backup, files, settings
from .com import value
from .results import Result

logger = logging.getLogger(__name__)

_last = {}                  # backup entry key -> time of the last auto-save (seconds, monotonic)
_saving = {"path": None}    # the file being auto-saved right now (set on the worker thread, read by the worker's watch)


def saving() -> Optional[str]:
    """The file an auto-save is writing at this moment, or None: a call past its limit or held by a dialog then has
    made its change already (core/worker.py says so, so the AI does not repeat it)."""
    return _saving["path"]


def user_files_allowed(entry: dict) -> bool:
    """May a user's own file be auto-saved? Only once the user has allowed editing it (the permission to edit, not
    built yet): never for now."""
    return False


def _minutes(raw) -> float:
    """auto_save_minutes as a finite number of 0 or more; anything else: the default (logged)."""
    try:
        if not isinstance(raw, bool):
            minutes = float(raw)
            if math.isfinite(minutes) and minutes >= 0:
                return minutes
    except (TypeError, ValueError):
        pass
    logger.warning(f"auto_save_minutes {raw!r} is not a number of 0 or more: {settings.DEFAULTS['auto_save_minutes']}")
    return float(settings.DEFAULTS["auto_save_minutes"])


def _due(key, minutes) -> bool:
    if minutes <= 0 or key not in _last:
        return True
    return time.monotonic() - _last[key] >= minutes * 60


def _sketch_open(doc) -> bool:
    try:
        return value(value(doc, "SketchManager"), "ActiveSketch") is not None
    except Exception:
        return True                                     # unknown: do not save in the middle of it


def _changed(doc) -> bool:
    try:
        return bool(value(doc, "GetSaveFlag"))
    except Exception:
        return True


def version_path(path: str) -> str:
    """The next free <name>_v<N><ext> next to path."""
    p = Path(path)
    n = 1
    while True:
        candidate = p.with_name(f"{p.stem}_v{n}{p.suffix}")
        if not candidate.exists():
            return str(candidate)
        n += 1


def _keep_version(path: str) -> Optional[str]:
    """Copy the saved file to its next version, created exclusively (never over a file). The new path or None."""
    for _ in range(5):
        target = version_path(path)
        try:
            files.check_writable(target)
            made = False
            with open(path, "rb") as src, open(target, "xb") as dst:
                made = True
                shutil.copyfileobj(src, dst)
            shutil.copystat(path, target)
            return target
        except FileExistsError:
            continue
        except Exception as e:
            logger.warning(f"auto-save: no version copy of {path}: {e}")
            if made:
                try:
                    os.remove(target)                   # never leave half a copy
                except OSError:
                    pass
            return None
    return None


def _note(result: Result, text: str) -> None:
    result.message = f"{result.message.rstrip()} {text}" if result.message else text


def after(result: Result, tool=None, args: dict = None, ctx=None) -> Result:
    """Called by the runner after core/backup.py's after(): the auto-save of the call's document, when it is due.
    Never fails the call."""
    try:
        return _after(result, tool)
    except Exception as e:
        logger.warning(f"auto-save skipped: {type(e).__name__}: {e}")
        return result


PARTS = (".sldprt",)        # an assembly's or drawing's save also writes its referenced files with unsaved changes


def _say(result: Result, status: str) -> Result:
    """The auto-save's status in the data, no words: saved, not due, sketch open, unchanged, off, an assembly or
    drawing, protected, failed, no backup."""
    result.data = dict(result.data or {})
    result.data["auto_save"] = status
    return result


def _after(result: Result, tool) -> Result:
    if not result.success or tool is None or not backup.changes(tool):
        return result
    doc, entry = backup.active_this_call()
    if doc is None or entry is None or not entry.get("path"):
        return result                                   # no file: nothing to save in place (no read)
    if entry.get("kind") != "ours" and not (entry.get("kind") == "user" and user_files_allowed(entry)):
        return result                                   # a user's own file: not without their permission (no read)
    conf = settings.load()
    if conf.get("auto_save", True) is not True:
        return _say(result, "off")
    if not entry["path"].lower().endswith(PARTS):
        return _say(result, "not for assemblies and drawings (their save would also write other files)")
    key = entry["key"]
    if not _due(key, _minutes(conf.get("auto_save_minutes", settings.DEFAULTS["auto_save_minutes"]))):
        return _say(result, "not due")
    try:
        if files.protected_root(entry["path"]) is not None:
            return _say(result, "protected folder")
    except Exception:
        return _say(result, "protected folder")
    if _sketch_open(doc):                       # first: the changed flag stays off while a sketch is open
        return _say(result, "sketch open")
    if not _changed(doc):
        return _say(result, "unchanged")
    why = backup.may_save(entry)
    if why:
        _note(result, f"Not auto-saved: {why}; the change is in SOLIDWORKS only until it is saved.")
        return _say(result, "no backup")
    from . import document
    _saving["path"] = entry["path"]
    try:
        saved, error, warning = document.save_in_place(doc)
        error = f"SOLIDWORKS error code {error}" if error else "SOLIDWORKS did not save it"
    except Exception as e:
        from .connection import connection
        gone = connection.gone(e)                       # a gone code, or a non-COM error and SOLIDWORKS not answering
        if gone is not None:
            return _gone_while_saving(result, entry["path"], gone)
        saved, error = False, f"{type(e).__name__}: {e}"
    finally:
        _saving["path"] = None
    _last[key] = time.monotonic()                       # a failed save is not retried at every call
    if not saved:
        logger.warning(f"auto-save of {entry['path']} failed: {error}")
        _note(result, f"Auto-save of {entry['path']} failed ({error}); the change is in SOLIDWORKS only until it is "
                      f"saved.")
        return _say(result, "failed")
    version = _keep_version(entry["path"]) if conf.get("auto_save_versions") is True else None
    backup.saved_by_server(entry, also_written=version)
    result.data = dict(result.data or {})
    result.data["auto_saved"] = entry["path"]
    if version:
        result.data["auto_saved_version"] = version
    return _say(result, "saved")



def _gone_while_saving(result: Result, path: str, code: str) -> Result:
    """SOLIDWORKS closed or crashed during the auto-save: the change is not in SOLIDWORKS any more, so the result
    must not say it is. The connection is dropped (the next call attaches to, or starts, SOLIDWORKS afresh), as the
    runner does for a call during which SOLIDWORKS closed."""
    from .connection import connection
    connection.app, connection.pid = None, None
    logger.warning(f"auto-save of {path}: SOLIDWORKS closed or stopped answering ({code})")
    _note(result, f"SOLIDWORKS closed or stopped answering while auto-saving {path}, so this change was not saved "
                  f"and is lost if SOLIDWORKS closed without saving. Start SOLIDWORKS again (connect_solidworks), "
                  f"open {path} and check it before going on.")
    result.data = dict(result.data or {})
    result.data.update(solidworks_gone=True, com_code=code)
    return _say(result, "failed")

def forget() -> None:
    """Tests: a new session."""
    _last.clear()
