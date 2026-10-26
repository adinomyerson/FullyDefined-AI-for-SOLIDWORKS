"""Assemblies: the calls that add, move and change components.
The tools do the inputs, messages and measuring."""

import logging
import os

import pythoncom
import win32com.client

from ..com import byref_int
from ..com import null_object
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


def sw_delete_component(asm, comp) -> None:
    """Delete a component; SolidWorks deletes the mates that use it with it."""
    if sw_select_component(asm, comp):
        asm.Extension.DeleteSelection2(0)
    sw_clear_selection(asm)


def sw_configurations(app, path: str):
    """(configuration names, active one) of a file on disk, without opening it."""
    try:
        names = [str(n) for n in (app.GetConfigurationNames(path) or [])]
    except Exception as e:
        logger.debug(f"Could not read the configurations of {path}: {e}")
        names = []
    try:
        active = str(app.GetActiveConfigurationName(path) or "")
    except Exception:
        active = ""
    return names, active


def sw_replace_component(asm, comp, path: str, configuration: str, all_instances: bool) -> bool:
    """Replace a selected top-level component by another file in the named configuration, re-attaching the mates
    SolidWorks can (ReplaceComponents2: 1 = swReplaceComponentsConfiguration_ManuallySelect)."""
    ok = False
    if sw_select_component(asm, comp):
        try:
            ok = bool(asm.ReplaceComponents2(path, configuration, all_instances, 1, True))
        except Exception as e:
            logger.debug(f"ReplaceComponents2 failed: {e}")
    sw_clear_selection(asm)
    return ok


EXPLODE_AXES = {"x": 0, "y": 1, "z": 2}     # AddExplodeStep2's ExplDirIndex: the triad's axis (measured)


def sw_exploded_views(asm) -> list:
    """The exploded views of the active configuration."""
    try:
        config = value(asm, "GetActiveConfiguration")
        return [str(n) for n in (asm.GetExplodedViewNames2(str(config.Name)) or [])]
    except Exception as e:
        logger.debug(f"Exploded views: {e}")
        return []


def sw_create_exploded_view(asm) -> None:
    try:
        asm.CreateExplodedView()
    except Exception as e:
        logger.debug(f"CreateExplodedView: {e}")


def sw_add_explode_step(asm, comps, axis: str, distance_m: float, reverse: bool):
    """One step of the exploded view being edited: the selected components (mark 1) move along an axis of the
    triad, which lies along the assembly axes when no direction is selected (measured: a selected plane is
    ignored). The step, or None. Which way + goes is measured by the tool."""
    sw_clear_selection(asm)
    step = None
    try:
        data = value(asm.SelectionManager, "CreateSelectData")
        data.Mark = 1
        if all(bool(c.Select4(True, data, False)) for c in comps):
            error = byref_int()
            config = value(asm, "GetActiveConfiguration")
            step = config.AddExplodeStep2(distance_m, EXPLODE_AXES[axis], bool(reverse), 0.0, 0, False, False,
                                          False, error)
    except Exception as e:
        logger.debug(f"AddExplodeStep2: {e}")
    sw_clear_selection(asm)
    return step


def sw_explode_steps(asm) -> list:
    """The explode steps of the active configuration, in order."""
    try:
        config = value(asm, "GetActiveConfiguration")
        return [config.GetExplodeStep(i) for i in range(int(value(config, "GetNumberOfExplodeSteps")))]
    except Exception as e:
        logger.debug(f"Explode steps: {e}")
        return []


def sw_delete_explode_step(asm, step) -> None:
    try:
        value(asm, "GetActiveConfiguration").DeleteExplodeStep(str(value(step, "Name")))
    except Exception as e:
        logger.debug(f"DeleteExplodeStep: {e}")


def sw_delete_exploded_view(asm, name: str) -> None:
    sw_clear_selection(asm)
    try:
        if asm.Extension.SelectByID2(name, "EXPLODEDVIEWS", 0, 0, 0, False, 0, null_object(), 0):
            asm.Extension.DeleteSelection2(0)
    except Exception as e:
        logger.debug(f"Delete exploded view {name}: {e}")
    sw_clear_selection(asm)


def sw_show_exploded(asm, name: str, shown: bool) -> bool:
    try:
        return bool(asm.ShowExploded2(bool(shown), name))
    except Exception as e:
        logger.debug(f"ShowExploded2: {e}")
        return False


def sw_is_exploded(asm, name: str):
    """True / False, or None when SolidWorks does not say. Measured: IsExploded(name) with a plain string gives
    'Type mismatch'; the name goes by reference."""
    try:
        return bool(asm.Extension.IsExploded(win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, name)))
    except Exception as e:
        logger.debug(f"IsExploded: {e}")
        return None
