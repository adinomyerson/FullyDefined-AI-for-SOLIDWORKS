"""Equations and global variables: the calls that add and delete them through IEquationMgr.
Measured on SolidWorks 2026: Add2 returns the new index, or -1 for bad syntax, an unknown name, a duplicate global
variable or a circular reference (nothing is added). A change goes through the indexed Equation put, sent as a
raw IDispatch property put (late binding has no way to call it; SetEquationAndConfigurationOption returns -1 for
everything); bad text leaves the equation as it was and Status reads -1. Delete + Add2 is not a way to change one: a
global variable other equations use cannot be added back once deleted."""

import pythoncom

from ..com import value


def sw_equation_manager(doc):
    return value(doc, "GetEquationMgr")


def sw_add_equation(mgr, index: int, text: str) -> int:
    """Add an equation at index (-1 = at the end) and solve. The new index, or -1 when SolidWorks refused it."""
    try:
        return int(mgr.Add2(index, text, True))
    except Exception:
        return -1


def sw_delete_equation(mgr, index: int) -> bool:
    try:
        count = int(value(mgr, "GetCount"))
        mgr.Delete(index)
        return int(value(mgr, "GetCount")) == count - 1
    except Exception:
        return False


def sw_set_equation(mgr, index: int, text: str) -> bool:
    """Replace equation index with text. True when SolidWorks took it (Status 0); it may rewrite the text."""
    try:
        dispid = mgr._oleobj_.GetIDsOfNames("Equation")
        mgr._oleobj_.Invoke(dispid, 0, pythoncom.DISPATCH_PROPERTYPUT, 0, index, text)
        return int(value(mgr, "Status")) == 0
    except Exception:
        return False
