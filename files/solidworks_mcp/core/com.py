"""
COM helpers. Python reaches SolidWorks late-bound: it looks every member up by
name at call time, which has three traps (see docs/DEVELOPING.md).
"""

import os
import threading
import types

import pythoncom
import win32com.client

# The number of the tool call running now (runner.run counts): what a helper reads once per call is cached under it
call_number = [0]


_call_end = []                          # what to forget when a tool call ends (at_call_end)


def at_call_end(forget):
    """Register a function that drops what a module read once per call; runner.run calls it when every call ends."""
    _call_end.append(forget)
    return forget


def end_call() -> None:
    for forget in _call_end:
        try:
            forget()
        except Exception:
            pass


def call_token() -> tuple:
    """What a once-per-call cache is keyed by: the call and the thread (COM objects read on the server's worker
    thread are no use on another, such as a test's)."""
    return call_number[0], threading.get_ident()


# SolidWorks closed or crashed: the codes Windows gives when the other process is gone (pywin32's com_error, as its
# hresult or the scode inside its excepinfo). Anything else is not taken as SolidWorks gone.
GONE = {-2147023174: "RPC_S_SERVER_UNAVAILABLE", -2147023170: "RPC_S_CALL_FAILED",
        -2147023169: "RPC_S_CALL_FAILED_DNE", -2147417848: "RPC_E_DISCONNECTED",
        -2147221251: "CO_E_OBJNOTCONNECTED"}


def com_codes(e: BaseException) -> list:
    """The COM codes of a pywin32 com_error (hresult, text, excepinfo, argerr): its hresult and the scode inside."""
    if type(e).__name__ != "com_error":
        return []
    args = getattr(e, "args", ()) or ()
    codes = [args[0]] if args and isinstance(args[0], int) else []
    info = args[2] if len(args) > 2 else None
    if isinstance(info, tuple) and len(info) > 5 and isinstance(info[5], int):
        codes.append(info[5])
    return codes


def gone_code(e: BaseException):
    """The name of the code saying SolidWorks closed or crashed (GONE), or None for any other exception."""
    found = [c for c in com_codes(e) if c in GONE]
    return GONE[found[0]] if found else None


# SolidWorks is there but did not take the call (a modal dialog open, or busy with something else): busy, never gone
BUSY = {-2147418111: "RPC_E_CALL_REJECTED", -2147417846: "RPC_E_SERVERCALL_RETRYLATER"}


def busy_code(e: BaseException):
    """The name of the code saying SolidWorks rejected the call because it is busy (BUSY), or None."""
    found = [c for c in com_codes(e) if c in BUSY]
    return BUSY[found[0]] if found else None


def value(obj, name: str):
    """A member that is a property in one SolidWorks version and a method in another.
    Never test with callable(): COM objects are callable."""
    v = getattr(obj, name)
    if isinstance(v, types.MethodType):
        v = v()
    return v


def byref_int(initial: int = 0):
    """An int the API writes back into (errors, warnings)."""
    return win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, initial)


def null_object():
    """A null object argument. Passing None gives "type mismatch"."""
    return win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)


DOC_TYPES = {1: "Part", 2: "Assembly", 3: "Drawing"}


# Each document's type, read once per tool call (a document's type never changes; the object is kept with it so its
# id is not reused). Started again for every call and thread (call_token), and dropped when the call ends.
_types = {"call": None, "map": {}}


def doc_type(doc) -> int:
    if _types["call"] != call_token():
        _types.update(call=call_token(), map={})
    hit = _types["map"].get(id(doc))
    if hit is not None and hit[0] is doc:
        return hit[1]
    kind = int(value(doc, "GetType"))
    _types["map"][id(doc)] = (doc, kind)
    return kind


@at_call_end
def _forget_types() -> None:
    _types.update(call=None, map={})


def doc_title(doc) -> str:
    try:
        return str(value(doc, "GetTitle"))
    except Exception:
        return "Unknown"


def doc_path(doc) -> str:
    try:
        return str(value(doc, "GetPathName") or "")
    except Exception:
        return ""


def same_path(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def open_documents(app):
    """Every open document. GetFirstDocument / GetNext are properties here: do not call them."""
    out = []
    doc = app.GetFirstDocument
    while doc is not None and len(out) < 1000:
        out.append(doc)
        doc = doc.GetNext
    return out


def plane_feature_name(plane: str) -> str:
    """Front / Top / Right -> the standard plane name; anything else is used as given. No SolidWorks read: when a
    document does not have that name (a localized template), the place that uses it asks core/planes.py and tries
    once more."""
    text = (plane or "Front").strip()
    standard = {"front": "Front Plane", "top": "Top Plane", "right": "Right Plane"}
    return standard.get(text.lower().replace(" plane", ""), text)
