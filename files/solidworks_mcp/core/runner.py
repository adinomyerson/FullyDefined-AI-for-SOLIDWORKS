"""
The one path every tool call takes: shared checks, the tool, clearing the
selection, one log line. Calls run one at a time.
"""

import json
import logging
import time
import traceback

from . import files, units
from . import com
from .com import doc_type
from .connection import connection
from .registry import TOOLS, ToolDef
from .results import SUPPORT_LINE, Err, Result, ToolError

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

    @property
    def app(self):
        """The SolidWorks application; connects if needed. Checked once per call."""
        if self._app is None:
            connection.connect()
            self._app = connection.app
        return self._app

    @property
    def doc(self):
        """The active document, or a refusal if none is open."""
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
        return
    kind = doc_type(doc)
    if kind not in wanted:
        raise ToolError(f"{tool.name} works on {_WANTED[tool.needs]}, but the active document is "
                        f"{_KIND.get(kind, 'not a part')}. {_OPEN_FIRST[tool.needs]}", Err.INPUT)


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


def run(name: str, arguments: dict) -> Result:
    tool = TOOLS[name]
    args = dict(arguments or {})
    t0 = time.time()
    com.call_number[0] += 1
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
        ctx = Context(tool)
        for key in tool.writes:                 # file safety: never write under protected_paths
            if args.get(key):
                files.check_writable(args[key])
        _check_needs(ctx, tool)
        result = tool.func(ctx, **args)
        if not isinstance(result, Result):
            raise TypeError(f"{name} returned {type(result).__name__}, not a Result")
    except ToolError as e:
        result = e.result()
    except Exception as e:
        logger.error(f"{name} raised:\n{traceback.format_exc()}")
        result = Result(False, f"Internal error in {name} (see log): {type(e).__name__}: {e}\n\n{SUPPORT_LINE}",
                        Err.INTERNAL)
    if result.success and tool.clears_selection:
        _clear_selection()
    com.end_call()                              # nothing read in this call is reused by the next
    ms = int((time.time() - t0) * 1000)
    logger.info(f"{name} {json.dumps(arguments, default=str)[:300]} -> "
                f"{'ok' if result.success else 'error ' + str(int(result.code))} in {ms} ms")
    return result
