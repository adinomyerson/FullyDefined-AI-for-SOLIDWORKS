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


def doc_type(doc) -> int:
    return int(value(doc, "GetType"))


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
    """Front / Top / Right -> the standard plane name; anything else is used as given."""
    text = (plane or "Front").strip()
    standard = {"front": "Front Plane", "top": "Top Plane", "right": "Right Plane"}
    return standard.get(text.lower().replace(" plane", ""), text)
