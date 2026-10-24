"""
What a tool returns, and how it reaches the client as text.

A tool returns ok(...) or raises ToolError(...). The text format is the one the
server has always used, because clients and models read it:

    [SUCCESS] message                [ERROR] message
    Details: {json}                  Error Code: 10 (INPUT)
                                     Details: {json}
"""

import json
from enum import IntEnum
from typing import Any, Dict, Optional


# The end of every unexpected internal error (a tool that crashed), never of an ordinary refusal.
SUPPORT_LINE = ("This is a bug in the SolidWorks MCP server, not something you did. Please run "
                "`solidworks-mcp doctor` (or `python -m solidworks_mcp doctor` in the server's folder) and send "
                "the report it writes, with what you asked, to the contact in the README's Support section.")


class Err(IntEnum):
    """Error codes, as they appear in a result ("Error Code: 10 (INPUT)"). Names and numbers are the contract."""
    OK = 0
    # what the caller asked for
    INPUT = 10
    NOT_FOUND = 11          # a file that is not there
    LOAD = 12
    SAVE = 13
    EXPORT = 15
    # the session
    CONNECTION = 20
    NO_SOLIDWORKS = 21
    NO_DOCUMENT = 22
    TEMPLATE = 23
    BUSY = 24
    TIMEOUT = 25
    # the model
    SKETCH = 30
    FEATURE = 31
    SELECTION = 32
    # a bug in the server
    INTERNAL = 99


class Result:
    def __init__(self, success: bool, message: str, code: Err = Err.OK,
                 data: Optional[Dict[str, Any]] = None, image: Optional[bytes] = None):
        self.success = success
        self.message = message
        self.code = Err(code)
        self.data = data or {}
        self.image = image          # PNG bytes, sent as its own content block

    def text(self, limit: int = 0) -> str:
        """The result for the model. Longer than limit characters (0: no limit): the longest lists in the data lose
        whole records from their end, and the message says how many of each were left out."""
        data, message = self.data, self.message
        if limit and limit > 0 and data and len(self._render(message, data)) > limit:
            data, omitted = shorten_records(data, limit - len(message) - 300)
            if omitted:
                data["omitted"] = omitted
                message += (" [shortened: " + ", ".join(f"{n} {k}" for k, n in omitted.items())
                            + " left out (whole records). Filter, or ask for less, to see them.]")
        return self._render(message, data)

    def _render(self, message, data) -> str:
        lines = [f"[{'SUCCESS' if self.success else 'ERROR'}] {message}"]
        if not self.success:
            lines.append(f"Error Code: {int(self.code)} ({self.code.name})")
        if data:
            # compact: no indentation, no unicode escapes (a list_faces result is 59% smaller)
            lines.append("Details: " + _dumps(data))
        return "\n".join(lines)


def _dumps(data) -> str:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False, default=str)


def shorten_records(data: Dict, budget: int):
    """A copy of data whose top-level lists (and lists one level down) are cut from the end, whole records only,
    until it fits budget characters. Returns (the copy, {list name: records left out})."""
    out = json.loads(_dumps(data))
    lists = [(k, out) for k, v in out.items() if isinstance(v, list)]
    lists += [(k2, v) for v in out.values() if isinstance(v, dict) for k2, v2 in v.items() if isinstance(v2, list)]
    omitted: Dict[str, int] = {}
    while lists and len(_dumps(out)) > max(budget, 200):
        key, owner = max(lists, key=lambda kv: len(_dumps(kv[1][kv[0]])))
        items = owner[key]
        if len(items) <= 1:
            lists.remove((key, owner))
            continue
        cut = max(1, len(items) // 10)                  # a tenth at a time: few re-renders even for long lists
        del items[-cut:]
        omitted[key] = omitted.get(key, 0) + cut
    return out, omitted


def cap_text(text: str, limit: int) -> str:
    """Cut a result longer than limit characters, saying so and what to do (limit 0 or less: no limit)."""
    if not limit or limit <= 0 or len(text) <= limit:
        return text
    return (text[:limit] + f"\n[truncated: {limit} of {len(text)} characters shown. Add a filter or ask for less "
            f"(one feature, face or component) to see the rest.]")


class ToolError(Exception):
    """A clean refusal or failure: the message is for the model, so it says what to do."""

    def __init__(self, message: str, code: Err = Err.INPUT, data: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.code = Err(code)
        self.data = data

    def result(self) -> Result:
        return Result(False, self.message, self.code, self.data)


def ok(message: str, data: Optional[Dict[str, Any]] = None, image: Optional[bytes] = None) -> Result:
    return Result(True, message, Err.OK, data, image)
