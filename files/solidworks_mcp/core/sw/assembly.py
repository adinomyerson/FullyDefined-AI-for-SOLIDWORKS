"""Assemblies: the calls that add, move and change components.
The tools do the inputs, messages and measuring."""

import logging
import os

import pythoncom
import win32com.client

from ..com import byref_int
from ..com import value
from .common import sw_clear_selection

logger = logging.getLogger(__name__)

_OPEN_TYPES = {".sldprt": 1, ".sldasm": 2}


def sw_open(app, path: str):
    """Load a part or assembly file silently; the document, or None."""
    errors, warnings = byref_int(), byref_int()
    doc = app.OpenDoc6(path, _OPEN_TYPES.get(os.path.splitext(path)[1].lower(), 1), 1, "", errors, warnings)
    return doc if doc is not None and errors.value == 0 else None


def sw_activate(app, title: str) -> None:
    app.ActivateDoc3(title, False, 0, byref_int())


def sw_add_component(asm, path: str, x_m: float, y_m: float, z_m: float):
    """Insert a component with the centre of its bounding box at the point; the component or None."""
    return asm.AddComponent5(path, 0, "", False, "", x_m, y_m, z_m)


def sw_select_component(asm, comp) -> bool:
    sw_clear_selection(asm)
    try:
        return bool(comp.Select4(False, value(asm.SelectionManager, "CreateSelectData"), False))
    except Exception as e:
        logger.debug(f"Could not select {comp.Name2}: {e}")
        return False


def sw_set_fixed(asm, comp, fixed: bool) -> bool:
    """Fix or float a component; True if it now has that state."""
    if bool(value(comp, "IsFixed")) == fixed:
        return True
    if not sw_select_component(asm, comp):
        return False
    try:
        if fixed:
            asm.FixComponent()
        else:
            asm.UnfixComponent()
    finally:
        sw_clear_selection(asm)
    return bool(value(comp, "IsFixed")) == fixed


def sw_write_transform(comp, rows, origin) -> None:
    """Give a component a new placement (IMathUtility.CreateTransform does not work late-bound, so the
    component's own transform object is rewritten)."""
    transform = comp.Transform2
    data = [c for row in rows for c in row] + list(origin) + [1.0, 0.0, 0.0, 0.0]
    transform.ArrayData = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, data)
    comp.Transform2 = transform


def sw_suppression(comp) -> int:
    return int(value(comp, "GetSuppression2"))


def sw_set_solving(asm, comp, suppression: int, solving: int) -> bool:
    """Make a selected sub-assembly flexible (1) or rigid (0), keeping its other properties."""
    if not sw_select_component(asm, comp):
        return False
    try:
        asm.CompConfigProperties4(suppression, solving, bool(comp.Visible), True,
                                  str(comp.ReferencedConfiguration), bool(comp.ExcludeFromBOM))
    finally:
        sw_clear_selection(asm)
    return True


def sw_set_visible(comp, visible: bool) -> None:
    try:
        comp.Visible = 1 if visible else 0
    except Exception as e:
        logger.debug(f"Could not change the visibility of {comp.Name2}: {e}")
