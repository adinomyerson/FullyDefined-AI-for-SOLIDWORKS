"""
Protect the user's files: a copy of a file the server did not create, kept before the server's changes reach it.

A document changes on disk only when it is saved. So its file is copied either right after the first call that
changes it (the file is still the original then), or, before a tool that can save runs, for every open user document
not yet copied that may have unsaved changes (a save, a save with its referenced files, a close that saves, a drawing
that saves its model). Nothing is read before an ordinary tool runs (a refusal touches nothing), and nothing is
blocked.

"Changed" never rests on SOLIDWORKS' changed flag alone: the flag stays off while a sketch is open. A call counts as a
change when its tool is not on the read-only list, when the flag is on, or when the document has an open sketch; a
check that cannot be read counts as changed (one copy too many is harmless, a missing one is not). After a changing
call that failed, the next save copies every open user file not yet copied (nothing is read after a refusal).

A file a tool is about to replace (an output path that exists and that the server did not create: save_document /
copy_file / a flat-pattern export with overwrite: true, or an export that replaces its file without asking) is
copied before the tool runs, with no SOLIDWORKS read, and the result says so. overwrite: true is not the user's
consent to lose their own file.

When the copy of a user's file cannot be made, a tool that would save over or replace that file refuses instead of
running (the copy is tried once more first, so freeing space or fixing permissions and asking again works). Tools
that only change the document in SOLIDWORKS still run, with the plain note.

The pre-save check walks every open document each time, and attaches to a running SOLIDWORKS when this process is
not connected yet (it never starts one).

The server keeps each user file's time on disk. A saving tool records the times before it runs and takes the new ones
after, so the server's own saves are known. A time that changed otherwise means the user saved the file: if the server
has not changed the document since its last save, that version is the user's new original and the next change
copies it; if the server had changed it and no copy was made, the result says plainly that the original is gone.

Which documents are whose, per server process (keyed by the file's normalised path; by title for an unsaved one):
  - "user": a file on disk the server did not write. Its copy is a new, dated file in the backups folder under the
    user's profile (created exclusively: an earlier copy is never overwritten); the result says where, once per
    original;
  - "ours": a file a tool of this server wrote that did not exist before (a tool's output path): never copied;
  - "new": no file yet, first seen after a call that needs no document (the server's create_new_*): nothing said;
  - "unsaved": no file yet, first seen after a call that worked on it (it existed before: the user's): nothing to
    copy, and its first change says so plainly.
"""

import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

from . import com
from .com import doc_path, doc_title, open_documents, value
from .results import Err, Result, ToolError

logger = logging.getLogger(__name__)

# AUTO-SAVE RULE: anything that saves a document without a tool call of its own (an auto-save, a save after every
# feature) must first call before() for that save, or add itself to SAVING_TOOLS, or (core/autosave.py, after after())
# save only when may_save() says so and report the save with saved_by_server(), so the user's changed files are
# copied while they are still the original. A save that skips this loses the original with no copy.
FOLDER_NAME = "FullyDefined backups"
NOT_REPLACED = ("Couldn't back up {file}, so I didn't replace it. Save under a new name, or free space / check "
                "permissions.")
SAVING_TOOLS = {"save_document", "close_document", "create_drawing"}        # tools that can save documents in place
# Tools that never change a document. Any other tool counts as a change of the document it ran on (a tool missing
# here costs at most one copy too many, never a lost original).
READ_ONLY = {
    "connect_solidworks", "get_solidworks_info", "list_open_documents", "open_document", "copy_file",
    "list_materials", "solidworks_guide",
    "get_document_info", "list_custom_properties", "capture_view", "export_step", "export_stl", "export_pdf",
    "get_sketch_status", "list_sketch_entities", "clear_selection", "list_features", "list_dimensions",
    "list_faces", "list_edges", "select_edges", "select_faces", "get_sheet_metal_info", "measure_part",
    "list_equations", "get_color", "measure_distance",
    "list_components", "list_component_faces", "list_component_sketches", "list_mates", "check_interference",
    "list_drawing_views",
}

_lock = threading.RLock()
_docs = {}                  # key (normalised path, or ("untitled", title)) -> entry
_written = set()            # normalised paths the server's tools created
_calls = []                 # per running call: {"existed": written paths that existed, "copied": [entries], "times": {}}
_suspect = {"since_failure": False}     # a changing call failed: the next save copies every uncopied user file
_active = {"n": None, "doc": None, "entry": None}   # the document after() registered (core/autosave.py)


def folder() -> Path:
    """Where the copies go: the user's profile, outside AppData (an MSIX-packaged app redirects AppData writes)."""
    return Path.home() / FOLDER_NAME


def _norm(path) -> str:
    from .files import _norm as norm
    return norm(path)


def _mtime(path) -> Optional[float]:
    try:
        return Path(path).stat().st_mtime
    except Exception:
        return None


def _copy(entry: dict) -> None:
    """The dated copy of the entry's file (entry["backup"]), or the reason there is none (entry["problem"])."""
    from . import files
    src, dest = Path(entry["path"]), None
    try:
        if not src.is_file():
            entry["problem"] = "its file is not on disk"
            return
        if entry["mtime"] is not None and _mtime(src) != entry["mtime"]:
            entry["problem"] = "its file was saved before a copy could be made, so the original is no longer on disk"
            return
        target_dir = folder()
        stamp = time.strftime("%Y-%m-%d %H%M%S")
        for n in range(1, 1000):
            dest = target_dir / f"{src.stem} {stamp}{'' if n == 1 else f' ({n})'}{src.suffix}"
            files.check_writable(str(dest), "back up")
            target_dir.mkdir(parents=True, exist_ok=True)
            try:
                with open(src, "rb") as fin, open(dest, "xb") as fout:      # "x": never overwrite a copy
                    try:
                        shutil.copyfileobj(fin, fout)
                    except BaseException:
                        fout.close()
                        dest.unlink()                                       # no half-written copy left behind
                        raise
                break
            except FileExistsError:
                continue
        else:
            entry["problem"] = "no free name for the copy"
            return
        shutil.copystat(src, dest)
        if dest.stat().st_size != src.stat().st_size:
            entry["problem"] = "the copy came out a different size"
            return
        entry["backup"] = str(dest)
        logger.info(f"Backed up {src} to {dest}")
    except ToolError as e:
        entry["problem"] = e.message
    except Exception as e:
        entry["problem"] = f"{type(e).__name__}: {e}"
    if entry["problem"]:
        entry["failed"] = True                          # a copy that could not be made (not: an original already gone)
        logger.warning(f"No backup of {src}: {entry['problem']}")


def _register(doc, tool) -> Optional[dict]:
    path = doc_path(doc)
    if path:
        key = _norm(path)
        entry = _docs.get(key)
        if entry is None:
            kind = "ours" if key in _written else "user"
            entry = _docs[key] = {"key": key, "title": doc_title(doc), "path": path, "kind": kind, "backup": None,
                                  "problem": None, "told": False, "mtime": _mtime(path) if kind == "user" else None,
                                  "server_changed": False}
        return entry
    title = doc_title(doc)
    key = ("untitled", title)
    entry = _docs.get(key)
    if entry is None:
        kind = "new" if getattr(tool, "needs", "none") == "none" else "unsaved"
        entry = _docs[key] = {"key": key, "title": title, "path": "", "kind": kind, "backup": None, "problem": None,
                              "told": False, "mtime": None, "server_changed": False}
    return entry


def _refresh(entry: dict) -> None:
    """A user file's time changed on disk outside the server's saving tools: the user saved it."""
    if entry["kind"] != "user":
        return
    now = _mtime(entry["path"])
    if entry["mtime"] is None or now is None or now == entry["mtime"]:
        if entry["mtime"] is None:
            entry["mtime"] = now
        return
    entry["mtime"] = now
    if entry["server_changed"] or _suspect["since_failure"]:
        if not entry["backup"] and (not entry["told"] or entry.get("failed")):   # the server's changes on disk, no copy
            entry["problem"] = "its file was saved before a copy could be made, so the original is no longer on disk"
            entry["failed"] = False                         # nothing left to retry: the original is gone
        return
    logger.info(f"backup: {entry['path']} was saved outside the server; that version is the original now")
    entry.update(backup=None, problem=None, told=False, failed=False)   # the user's new version: copied at a change


def _dirty(doc) -> bool:
    try:
        return bool(value(doc, "GetSaveFlag"))
    except Exception:
        return True                                         # unknown: as changed


def _sketch_open(doc) -> bool:
    """A sketch open for editing: its entities are unsaved changes that SOLIDWORKS' changed flag does not show."""
    try:
        return value(value(doc, "SketchManager"), "ActiveSketch") is not None
    except Exception:
        return True                                         # unknown: as changed


def _may_have_changed(doc) -> bool:
    return _suspect["since_failure"] or _dirty(doc) or _sketch_open(doc)


def _changes(tool) -> bool:
    """A call of this tool counts as a change by itself. Saving tools are judged before they run (before())."""
    name = getattr(tool, "name", None)
    return name not in READ_ONLY and name not in SAVING_TOOLS


def _solidworks():
    """The running SOLIDWORKS: this process's connection, else attach to one already running (never start one)."""
    from .connection import connection
    if connection.app is None:
        try:
            connection._attach(start=False)
        except Exception as e:
            logger.debug(f"backup: no running SOLIDWORKS to check before saving: {e}")
    return connection.app


ALL = "all"


def _saved_over(tool, args: dict, app):
    """The documents this saving call writes in place: ALL, or a set of keys (the active document, or create_drawing's
    model). A Save As under another name writes no open document (its target is handled by _keep_replaced)."""
    name = getattr(tool, "name", None)
    if name == "save_document" and args.get("save_referenced") is True:
        return ALL
    if name == "close_document" and args.get("save") is not True:
        return set()
    if name == "create_drawing" and args.get("model_path"):
        return {_norm(str(args["model_path"]))}
    try:
        active = app.ActiveDoc if app is not None else None
        path = doc_path(active) if active is not None else ""
    except Exception:
        return ALL                                          # unknown: as every document
    if not path:
        return set()
    if name == "save_document" and args.get("filepath") and _norm(str(args["filepath"])) != _norm(path):
        return set()                                        # a Save As or an export: the open file is not written
    return {_norm(path)}


def before(ctx, tool, args: dict) -> None:
    """Called by the runner before the tool. Every tool: which of its output paths already exist (no SolidWorks
    read). A tool that can save: every open user document not yet copied that may have unsaved changes is copied
    first, and every user file's time is recorded so the save's own writes are known afterwards."""
    with _lock:
        existed = {_norm(args[k]) for k in getattr(tool, "writes", ()) or () if (args or {}).get(k)
                   and os.path.exists(str(args[k]))}
        call = {"n": com.call_number[0], "existed": existed, "copied": [], "times": None, "replaced": []}
        _calls.append(call)
        _keep_replaced(tool, args or {}, call)
        if getattr(tool, "name", None) not in SAVING_TOOLS:
            return
        for entry in _docs.values():
            _refresh(entry)                                 # outside saves since the last look (file times only)
        try:
            app = _solidworks()
            docs = open_documents(app) if app is not None else []
        except Exception as e:
            logger.warning(f"backup: the open documents could not be listed before saving: {e}")
            docs = []
        targets = _saved_over(tool, args or {}, app)
        for doc in docs:
            try:
                entry = _register(doc, tool)
                _refresh(entry)
                if entry["kind"] != "user" or entry["backup"]:
                    continue
                retry = bool(entry.get("failed"))           # its copy failed before: try again before saving over it
                if (not entry["told"] or retry) and (retry or _may_have_changed(doc)):
                    entry.update(problem=None, told=False, failed=False)
                    _copy(entry)                            # only user documents not yet copied are asked
                    call["copied"].append(entry)
                    if entry.get("failed") and (targets is ALL or entry["key"] in targets):
                        call["refused"] = True
                        call["copied"].remove(entry)
                        entry["told"] = True
                        raise ToolError(NOT_REPLACED.format(file=entry["path"]), Err.SAVE,
                                        {"path": entry["path"], "problem": entry["problem"]})
            except ToolError:
                raise
            except Exception as e:
                logger.warning(f"backup: one open document could not be checked before saving: {e}")
        _suspect["since_failure"] = False
        call["times"] = {key: e["mtime"] for key, e in _docs.items() if e["kind"] == "user"}


def _keep_replaced(tool, args: dict, call: dict) -> None:
    """Every existing file this call is about to replace, that the server did not create: copied now (files only, no
    SOLIDWORKS read). A tool with an overwrite input replaces a file only with overwrite: true (else it refuses, or
    saves a document in place, which the pre-save check covers)."""
    if "overwrite" in (getattr(tool, "accepted", None) or ()) and args.get("overwrite") is not True:
        return
    for k in getattr(tool, "writes", ()) or ():
        value_ = args.get(k)
        if not value_ or not isinstance(value_, str) or not os.path.isfile(value_):
            continue
        key = _norm(value_)
        if key in _written:
            continue                                    # the server's own output: nothing of the user's
        entry = _docs.get(key)
        if entry is not None:
            if entry["kind"] != "user":
                continue
            _refresh(entry)                             # a save by the user since: a new original, not kept yet
            if entry["backup"]:
                continue                                # its original is kept already
        kept = {"key": key, "title": Path(value_).name, "path": value_, "kind": "replaced", "backup": None,
                "problem": None, "told": False, "mtime": None, "server_changed": False}
        _copy(kept)
        if kept.get("failed"):
            call["refused"] = True
            raise ToolError(NOT_REPLACED.format(file=value_), Err.SAVE, {"path": value_, "problem": kept["problem"]})
        call["replaced"].append(kept)
        if entry is not None and kept["backup"]:
            entry.update(backup=kept["backup"], problem=None, told=True, server_changed=True)


def note_for(entry: dict, saving: bool = False) -> str:
    if entry["kind"] == "unsaved":
        return "This document has never been saved, so there is no file to back up."
    if entry["kind"] == "replaced":
        return (f"Backed up {entry['path']}, which this call replaces, to {entry['backup']}." if entry["backup"] else
                f"Could not back up {entry['path']} before replacing it ({entry['problem']}).")
    if entry["backup"]:
        return (f"Backed up your original of {entry['title']} to {entry['backup']} before saving." if saving else
                f"Backed up your original to {entry['backup']} before the first change.")
    if "saved before" in (entry["problem"] or ""):
        return f"No backup of {entry['path']}: {entry['problem']}."
    return (f"Could not back up {entry['path']} ({entry['problem']}); the changes are only in SOLIDWORKS until it is "
            f"saved.")


def _tell(result: Result, entry: dict, saving: bool = False) -> None:
    entry["told"] = True
    note = note_for(entry, saving)
    result.message = f"{result.message.rstrip()} {note}" if result.message else note
    result.data = dict(result.data or {})
    result.data.setdefault("backup", []).append({"path": entry["backup"], "of": entry["path"] or entry["title"],
                                                 "problem": entry["problem"]})


def after(result: Result, tool=None, args: dict = None, ctx=None) -> Result:
    """Called by the runner with every call's result (it pairs with before). Copies made before a save are told, and
    the files the save wrote get their new times; then, for a successful call that used SolidWorks: the paths it
    created become ours, the active document is registered, and at a user's document's first change its file is
    copied (still the original: not saved) and told once. After a failed changing call, the next save copies every
    open user file not yet copied (nothing is read here)."""
    with _lock:
        mine = bool(_calls) and _calls[-1]["n"] == com.call_number[0]      # before() ran for this very call
        call = _calls.pop() if mine else {"existed": set(), "copied": [], "times": None, "replaced": []}
        for entry in call["replaced"]:
            _tell(result, entry)
            if result.success and not (entry["key"] in _docs and _docs[entry["key"]]["kind"] == "user"):
                _written.add(entry["key"])              # the server's content now; the user's file is kept above
        for entry in call["copied"]:
            _tell(result, entry, saving=True)
        for key, then in (call["times"] or {}).items():     # the server's own save: not an outside one
            entry = _docs.get(key)
            now = _mtime(entry["path"]) if entry else None
            if entry is not None and now is not None and now != then:
                entry.update(mtime=now, server_changed=False)
        if not result.success:
            if not call.get("refused"):
                _mark_touched(tool, ctx)
            return result
        for key in getattr(tool, "writes", ()) or ():
            if (args or {}).get(key):
                path = _norm(args[key])
                if path not in call["existed"] and not (path in _docs and _docs[path]["kind"] == "user"):
                    _written.add(path)                  # a file this call created: ours
        if ctx is None or getattr(ctx, "_app", None) is None:
            return result                               # the call never reached SolidWorks: nothing to look at
        try:
            doc = ctx._app.ActiveDoc
            entry = _register(doc, tool) if doc is not None else None
        except Exception as e:
            logger.debug(f"backup: could not register the document: {e}")
            return result
        _active.update(n=com.call_number[0], doc=doc, entry=entry)
        if entry is None or entry["kind"] not in ("user", "unsaved"):
            return result
        _refresh(entry)
        if entry["told"]:
            if _changes(tool):
                entry["server_changed"] = True          # a later outside save holds the server's changes
            return result
        if entry["problem"] and not entry["backup"]:
            _tell(result, entry)                        # saved with the server's changes before a copy: said plainly
            return result
        if not (_changes(tool) or _may_have_changed(doc)):
            return result                               # a read-only call on an unchanged document
        entry["server_changed"] = True
        if entry["kind"] == "user" and not entry["backup"]:
            _copy(entry)
        _tell(result, entry)
        return result


def changes(tool) -> bool:
    """A successful call of this tool changes the document it ran on (not read-only, not a saving tool)."""
    return _changes(tool)


def active_this_call():
    """(document, entry) that after() registered in the running call, or (None, None): no SOLIDWORKS read."""
    with _lock:
        if _active["n"] != com.call_number[0]:
            return None, None
        return _active["doc"], _active["entry"]


def may_save(entry: dict) -> Optional[str]:
    """None when a save in place by the server itself (an auto-save) keeps the copy-first rule for this document;
    else why not, in plain words. A user's file is saved over only once its original is copied and that copy is still
    on disk."""
    with _lock:
        if entry is None or not entry.get("path"):
            return "it has no file yet"
        if entry["kind"] == "user":
            _refresh(entry)
            if not entry["backup"]:
                return f"no backup of your original {entry['path']} could be made, so it is not saved over"
            if not os.path.isfile(entry["backup"]):         # never saved over before its copy exists
                return f"the backup of your original ({entry['backup']}) is no longer on disk, so it is not saved over"
        return None


def saved_by_server(entry: dict, also_written: str = None) -> None:
    """After a save in place outside the saving tools (an auto-save): the file's new time is the server's own save,
    not the user's; also_written (a versioned copy) is the server's output."""
    with _lock:
        if entry is not None and entry["kind"] == "user":
            entry.update(mtime=_mtime(entry["path"]), server_changed=False)
        if also_written:
            _written.add(_norm(also_written))


def _mark_touched(tool, ctx) -> None:
    """A failed call of a tool that changes documents and reached SolidWorks: a document may be half-changed in a way
    the flag does not show. Nothing is read here (a refusal touches nothing): the next save copies every open user
    file not yet copied."""
    if _changes(tool) and ctx is not None and getattr(ctx, "_app", None) is not None:
        _suspect["since_failure"] = True


def forget() -> None:
    """Tests: a new session."""
    with _lock:
        _docs.clear()
        _written.clear()
        _calls.clear()
        _suspect["since_failure"] = False
        _active.update(n=None, doc=None, entry=None)
