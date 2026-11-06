"""
The one path every tool call takes: shared checks, the tool, clearing the
selection, one log line. Calls run one at a time.
"""

import json
import logging
import time
import traceback

from . import dialogs, files, uiguard, units
from . import com
from .com import doc_type
from .connection import connection
from .registry import TOOLS, ToolDef
from .results import SUPPORT_LINE, Err, Result, ToolError
from . import backup                       # after the others: core/backup.py (a user's file copied at its first change)
from . import autosave                     # after backup: core/autosave.py (save after a change; the copy-first rule)

logger = logging.getLogger("solidworks_mcp.calls")

_NEEDS_TYPES = {"part": {1}, "assembly": {2}, "drawing": {3}, "part_or_assembly": {1, 2}}
_KIND = {1: "a part", 2: "an assembly", 3: "a drawing"}
_WANTED = {"part": "a part document", "assembly": "an assembly document",
           "drawing": "a drawing document", "part_or_assembly": "a part or an assembly"}
_OPEN_FIRST = {"part": "Open or create a part first.", "assembly": "Open or create an assembly first.",
               "drawing": "Open or create a drawing first.",
               "part_or_assembly": "Open a part or an assembly first."}


class Context:
    """What a tool gets as its first argument."""

    def __init__(self, tool: ToolDef):
        self.tool = tool
        self._app = None                    # checked once per call, not on every use
        self._handoff = None                # the document the runner's own check read, for the tool's first ctx.doc

    @property
    def app(self):
        """The SolidWorks application; connects if needed. Checked once per call."""
        if self._app is None:
            connection.connect()
            self._app = connection.app
        self._handoff = None                # the tool used SolidWorks first: its ctx.doc reads the active document
        return self._app

    @property
    def doc(self):
        """The active document, or a refusal if none is open. The tool's first ctx.doc gets the document the
        runner's check has just read, when nothing else touched SolidWorks in between (the same document: the read
        is saved); every later one reads it again."""
        handed, self._handoff = self._handoff, None
        if handed is not None:
            return handed
        doc = self.app.ActiveDoc
        if doc is None:
            raise ToolError("There is no document open. Make one (create_new_part, create_new_assembly) or open one (open_document).", Err.NO_DOCUMENT)
        return doc

    def active_doc(self):
        """The active document, or None."""
        return self.app.ActiveDoc

    def setting(self, name: str):
        """A setting: the user's settings.json over the shipped defaults (core/settings.py)."""
        from . import settings
        return settings.load()[name]

    # lengths: the API works in metres
    def to_m(self, value, unit=None) -> float:
        return units.to_m(value, unit)

    def from_m(self, metres, unit=None) -> float:
        return units.from_m(metres, unit)

    def unit(self, unit=None) -> str:
        return units.parse(unit) if unit is not None else units.default()


def _check_needs(ctx: Context, tool: ToolDef) -> None:
    if tool.needs == "none":
        return
    doc = ctx.doc
    wanted = _NEEDS_TYPES.get(tool.needs)
    if wanted is None:
        ctx._handoff = doc
        return
    kind = doc_type(doc)
    if kind not in wanted:
        raise ToolError(f"{tool.name} works on {_WANTED[tool.needs]}, but the active document is "
                        f"{_KIND.get(kind, 'not a part')}. {_OPEN_FIRST[tool.needs]}", Err.INPUT)
    ctx._handoff = doc


def _clear_selection() -> None:
    try:
        doc = connection.app.ActiveDoc if connection.app is not None else None
        if doc is not None:
            doc.ClearSelection2(True)
    except Exception as e:
        logger.debug(f"Could not clear the selection: {e}")


def _missing_inputs(tool: ToolDef, missing: list) -> ToolError:
    """A required input left out: name it with what it is, and list the tool's inputs, required first."""
    props = tool.schema.get("properties", {})

    def described(key):
        text = str(props.get(key, {}).get("description") or "").strip().rstrip(".")
        return f"{key} ({text})" if text else key

    required = list(tool.required_args)
    inputs = [f"{k} (required)" for k in required] + sorted(k for k in tool.accepted if k not in required)
    first = missing[0]
    head = f"{first} is required" + (f": {props.get(first, {}).get('description', '').strip().rstrip('.')}"
                                     if props.get(first, {}).get("description") else "")
    more = f". Also missing: {', '.join(described(k) for k in missing[1:])}" if len(missing) > 1 else ""
    return ToolError(f"{head}{more}. {tool.name} takes: {', '.join(inputs)}.", Err.INPUT,
                     {"missing": missing, "inputs": sorted(tool.accepted), "required": required})


def _unknown_inputs(name: str, unknown: list, accepted: set) -> ToolError:
    """A typo like 'depht' must not run with the default: name the valid inputs and the closest match."""
    import difflib
    hints = []
    for key in unknown:
        close = difflib.get_close_matches(str(key), sorted(accepted), n=1, cutoff=0.6)
        hints.append(f"'{key}'" + (f" (did you mean '{close[0]}'?)" if close else ""))
    valid = ", ".join(sorted(accepted)) or "none"
    return ToolError(f"{name} has no input{'s' if len(unknown) > 1 else ''} {', '.join(hints)}. "
                     f"Its inputs: {valid}.", Err.INPUT,
                     {"unknown": unknown, "inputs": sorted(accepted)})


class GuardedContext(Context):
    """The Context of a tool call: the first time the call reaches SolidWorks, SolidWorks' toolbar refresh is held
    off for the rest of the call (core/uiguard.py; run() puts the setting back)."""

    _guarded = False

    @property
    def app(self):
        app = Context.app.fget(self)
        if not self._guarded:
            self._guarded = True
            uiguard.enter(app)
        return app


def _gone(name: str, e: BaseException):
    """The result for a call during which SolidWorks closed or crashed, or was busy (a rejected call, a dialog open),
    or None when SolidWorks answers (the exception is the tool's own: the internal error). Closed: the connection is
    dropped, so the next call attaches to (or starts) SolidWorks afresh. Busy: the connection is kept
    (connection.after_error says which)."""
    state = connection.after_error(e)
    if state is None:
        return None
    kind, what = state
    if kind == "busy":
        found = what["dialogs"]
        data = {"solidworks_busy": True, "dialogs": [d["title"] for d in found]}
        if what["com_code"]:
            data["com_code"] = what["com_code"]
        if found:
            return Result(False, f"SolidWorks is waiting on a dialog: {dialogs.describe(found)}. Answer or close it in "
                                 f"SolidWorks, then check what {name} did before repeating it.", Err.BUSY, data)
        return Result(False, f"SolidWorks is busy and did not take {name}'s call. Wait until it is free (answer any "
                             f"dialog open in SolidWorks), then check what {name} did before repeating it.",
                      Err.BUSY, data)
    connection.app, connection.pid = None, None
    return Result(False, f"SolidWorks closed or stopped answering during {name}. What {name} was doing may be partly "
                         f"made, or lost if SolidWorks closed without saving. Start SolidWorks again "
                         f"(connect_solidworks), open the document and check it before repeating {name}.",
                  Err.CONNECTION, {"solidworks_gone": True, "com_code": what})


def run(name: str, arguments: dict) -> Result:
    """One tool call (_run_call), and afterwards SolidWorks' CommandInProgress put back as the call found it,
    whatever happened: success, refusal, an error, a call that ended after its time limit."""
    depth = uiguard.depth()
    try:
        return _run_call(name, arguments)
    finally:
        uiguard.unwind(depth)


def _run_call(name: str, arguments: dict) -> Result:
    tool = TOOLS[name]
    args = dict(arguments or {})
    t0 = time.time()
    com.call_number[0] += 1
    ctx = None
    try:
        units.check(args.get("unit"))           # on every tool, as before, even one without a unit input
        if "unit" in args and "unit" not in tool.accepted:
            args.pop("unit")                    # checked above on every tool; not used by this one
        unknown = sorted(set(args) - tool.accepted)
        if unknown:
            raise _unknown_inputs(name, unknown, tool.accepted)
        missing = [key for key in tool.required_args if args.get(key) is None]     # an explicit null is no value
        if missing:
            raise _missing_inputs(tool, missing)
        ctx = GuardedContext(tool)
        for key in tool.writes:                 # file safety: never write under protected_paths
            if args.get(key):
                files.check_writable(args[key])
        _check_needs(ctx, tool)
        backup.before(ctx, tool, args)          # before a saving tool: the user's changed files copied first
        result = tool.func(ctx, **args)
        if not isinstance(result, Result):
            raise TypeError(f"{name} returned {type(result).__name__}, not a Result")
    except ToolError as e:
        result = e.result()
    except Exception as e:
        logger.error(f"{name} raised:\n{traceback.format_exc()}")
        result = _gone(name, e) or Result(False, f"Internal error in {name} (see log): {type(e).__name__}: {e}\n\n"
                                                 f"{SUPPORT_LINE}", Err.INTERNAL)
    result = backup.after(result, tool, args, ctx)     # a user's file copied at its first change, said once
    result = autosave.after(result, tool, args, ctx)   # then the changed document saved, when it has a file
    if result.success and tool.clears_selection:
        _clear_selection()
    com.end_call()                              # nothing read in this call is reused by the next
    ms = int((time.time() - t0) * 1000)
    logger.info(f"{name} {json.dumps(arguments, default=str)[:300]} -> "
                f"{'ok' if result.success else 'error ' + str(int(result.code))} in {ms} ms")
    return result
