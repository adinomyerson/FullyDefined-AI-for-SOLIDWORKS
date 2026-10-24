"""Weldments: the calls that make structural members.
Measured on SolidWorks 2026: a profile is a .sldlfp file per type with one configuration per size;
InsertStructuralWeldment5 makes the members with connected-segments option 1 (option 0 makes nothing)."""

import logging

import pythoncom
import win32com.client

from ..com import value
from .common import sw_clear_selection

logger = logging.getLogger(__name__)


def sw_profile_sizes(app, path: str) -> list:
    """The sizes (configurations) of a profile file, without opening it."""
    try:
        return [str(n) for n in (app.GetConfigurationNames(path) or [])]
    except Exception as e:
        logger.debug(f"GetConfigurationNames({path}): {e}")
        return []


def sw_weldment_feature(doc):
    """The Weldment feature (it makes the cut list), added when the part has none. The feature, or None."""
    for feat in _features(doc):
        if str(value(feat, "GetTypeName2")) == "WeldmentFeature":
            return feat
    return value(doc.FeatureManager, "InsertWeldmentFeature")


def _features(doc) -> list:
    out, feat = [], value(doc, "FirstFeature")
    while feat is not None and len(out) < 5000:
        out.append(feat)
        feat = value(feat, "GetNextFeature")
    return out


def sw_update_cut_list(doc) -> None:
    for feat in _features(doc):
        if str(value(feat, "GetTypeName2")) == "SolidBodyFolder":
            try:
                value(value(feat, "GetSpecificFeature2"), "UpdateCutList")
            except Exception as e:
                logger.debug(f"UpdateCutList: {e}")


def sw_cut_list_folders(doc) -> list:
    """The cut list folder features under Solid Bodies."""
    folders = []
    for feat in _features(doc):
        if str(value(feat, "GetTypeName2")) == "SolidBodyFolder":
            sub = value(feat, "GetFirstSubFeature")
            while sub is not None:
                if str(value(sub, "GetTypeName2")) == "CutListFolder":
                    folders.append(sub)
                sub = value(sub, "GetNextSubFeature")
    return folders


def sw_property(feat, name: str) -> str:
    """A cut list property's resolved text ('' when it has none)."""
    try:
        text = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, "")
        resolved = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, "")
        flags = [win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BOOL, False) for _ in range(2)]
        feat.CustomPropertyManager.Get6(name, False, text, resolved, flags[0], flags[1])
        return str(resolved.value or "")
    except Exception as e:
        logger.debug(f"Property {name}: {e}")
        return ""


def sw_close_if_opened(app, path: str, was_open: bool) -> None:
    """SolidWorks opens the profile file to build a member and leaves it open: close it again unless it was
    open before."""
    if was_open:
        return
    try:
        if app.GetOpenDocumentByName(path) is not None:
            app.CloseDoc(path)
    except Exception as e:
        logger.debug(f"Could not close {path}: {e}")


def sw_structural_member(doc, segments: list, path: str, size: str, corner: int):
    """Structural members along sketch segments, in one group. The feature, or None."""
    sw_clear_selection(doc)
    fm = doc.FeatureManager
    group = value(fm, "CreateStructuralMemberGroup")
    array = pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH
    group.Segments = win32com.client.VARIANT(array, segments)
    group.ApplyCornerTreatment = corner != 0
    if corner:
        group.CornerTreatmentType = corner
    feat = fm.InsertStructuralWeldment5(path, 1, True, win32com.client.VARIANT(array, [group]), size)
    sw_clear_selection(doc)
    return feat


def sw_extreme_point(body, direction) -> list:
    """The point of the body farthest along a unit direction, in metres, or None. Measured on SolidWorks 2026 with
    pywin32: IBody2.GetExtremePoint(x, y, z) returns (ok, px, py, pz); by-ref outputs are refused."""
    try:
        got = body.GetExtremePoint(float(direction[0]), float(direction[1]), float(direction[2]))
        if got and got[0]:
            return [float(v) for v in got[1:4]]
    except Exception as e:
        logger.debug(f"GetExtremePoint: {e}")
    return None
