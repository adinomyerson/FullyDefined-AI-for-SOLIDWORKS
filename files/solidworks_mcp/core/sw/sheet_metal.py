"""Sheet metal: the calls that make, convert and flatten a sheet metal part.
The tools do the inputs, messages and measuring."""

import logging

import pythoncom
import win32com.client

from ..com import byref_int, null_object, value
from .common import sw_clear_selection

logger = logging.getLogger(__name__)


def sw_base_flange(doc, allowance, thickness_m: float, thicken_dir: bool, radius_m: float, extrude_dist_m: float,
                   flip_extrude: bool):
    """Base flange of the selected sketch (SolidWorks 2026 builds the first flange only with Merge,
    UseFeatScope and UseAutoSelect all False). The feature, or None."""
    feat = None
    try:
        feat = doc.FeatureManager.InsertSheetMetalBaseFlange2(
            thickness_m,                              # Thickness
            thicken_dir,                              # ThickenDir
            radius_m,                                 # Radius
            extrude_dist_m,                           # ExtrudeDist1
            0.0,                                      # ExtrudeDist2
            flip_extrude,                             # FlipExtruDir
            0, 0, 0,                                  # EndCondition1, EndCondition2 (blind), DirToUse
            allowance,                                # PCBA - carries the K-factor
            False,                                    # UseDefaultRelief
            0, 0.001, 0.001, 0.5, False,              # ReliefType, ReliefWidth, ReliefDepth, ReliefRatio, UseReliefRatio
            False, False, False)                      # Merge, UseFeatScope, UseAutoSelect (must be False)
    except Exception as e:
        logger.debug(f"InsertSheetMetalBaseFlange2 failed: {e}")
    return feat


def sw_select_face_and_edges(doc, face, edges) -> bool:
    """Select the fixed face (mark 1) and the bend edges (mark 2) for Convert to Sheet Metal."""
    sw_clear_selection(doc)
    data = value(doc.SelectionManager, "CreateSelectData")
    data.Mark = 1
    selected = bool(face.Select4(False, data))
    for edge in edges:
        data = value(doc.SelectionManager, "CreateSelectData")
        data.Mark = 2
        selected = bool(edge.Select4(True, data)) and selected
    return selected


def sw_convert(doc, thickness_m: float, flip: bool, radius_m: float) -> None:
    """Convert the selected solid to sheet metal. Raises what SolidWorks raises; the return value is not reliable."""
    doc.FeatureManager.InsertConvertToSheetMetal2(thickness_m, flip, False, radius_m, 0.00001, 0, 0.5, 0, 0.5, False)


def sw_show_flat_pattern(feat, shown: bool) -> None:
    """Show (unsuppress) or hide (suppress) the flat pattern."""
    feat.SetSuppression2(1 if shown else 0, 1, None)


def sw_set_k_factor(doc, feat, k_factor: float, k_factor_type: int) -> bool:
    """Set the K-factor through the Sheet-Metal feature definition."""
    data = value(feat, "GetDefinition")
    data.AccessSelections(doc, null_object())
    try:
        data.KFactor = float(k_factor)
        data.BendAllowanceType = k_factor_type
        for index in range(0, 4):
            try:
                data.SetOverrideDefaultParameter2(index, True)
            except Exception:
                pass
        modified = bool(feat.ModifyDefinition(data, doc, null_object()))
    finally:
        try:
            data.ReleaseSelectionAccess()
        except Exception:
            pass
    return modified


def sw_save_copy(doc, path: str) -> bool:
    """Save a copy without renaming the document; options 1 = silent, 2 = copy."""
    return bool(doc.Extension.SaveAs(path, 0, 1 | 2, null_object(), byref_int(), byref_int()))


def sw_open_copy(app, path: str):
    """Open a copy hidden and read-only (part, silent + read only)."""
    return app.OpenDoc6(path, 1, 1 | 2, "", byref_int(), byref_int())


def sw_edge_flange(doc, edge, angle_rad: float, radius_m, position: int, length_m: float):
    """An edge flange on one edge (measured on SolidWorks 2026): InsertSheetMetalEdgeFlange2 needs the profile sketch
    that IModelDoc2.InsertSketchForEdgeFlange makes; the flange it builds has no length until the feature data's
    OffsetDistance is set through ModifyDefinition. Length to the outer virtual sharp. The feature, or None."""
    sw_clear_selection(doc)
    sketch = doc.InsertSketchForEdgeFlange(edge, angle_rad, False)
    if sketch is None:
        return None
    options = 128 | (1 if radius_m is None else 0)        # default relief | default radius
    array = pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH
    feat = doc.FeatureManager.InsertSheetMetalEdgeFlange2(
        win32com.client.VARIANT(array, [edge]), win32com.client.VARIANT(array, [sketch]), options, angle_rad,
        radius_m or 0.0, position, 0.0, 0, 0.5, 0.0005, 0.0005, 1, null_object())
    if feat is None:
        return None
    data = value(feat, "GetDefinition")
    data.AccessSelections(doc, null_object())
    modified = False
    try:
        data.OffsetType = 1                               # blind
        data.OffsetDimType = 1                            # outer virtual sharp
        data.OffsetDistance = length_m
        modified = bool(feat.ModifyDefinition(data, doc, null_object()))
    finally:
        if not modified:                                  # ModifyDefinition releases it when it succeeds
            try:
                data.ReleaseSelectionAccess()
            except Exception:
                pass
    sw_clear_selection(doc)
    return feat if modified else None


def sw_sketched_bend(doc, sketch_feat, angle_rad: float, position: int, radius_m, reverse: bool = False):
    """A sketched bend along the one line of a sketch on a flat face (measured on SolidWorks 2026): there is no insert
    call; CreateDefinition(swFmSketchBend = 35) with the sketch selected makes it with CreateFeature. SolidWorks
    picks the side that stays: SetFixedFace and a selected face (any mark) are both ignored. The feature, or None."""
    sw_clear_selection(doc)
    if not sketch_feat.Select2(False, 0):
        return None
    fm = doc.FeatureManager
    data = fm.CreateDefinition(35)
    if data is None:
        return None
    data.BendAngle = angle_rad
    data.PositionType = position
    data.ReverseDirection = bool(reverse)
    data.UseDefaultBendRadius = radius_m is None
    if radius_m is not None:
        data.BendRadius = radius_m
    feat = fm.CreateFeature(data)
    sw_clear_selection(doc)
    return feat


def sw_export_flat_dxf(doc, path: str, model_path: str, bend_lines: bool = False) -> bool:
    """The flat pattern as a DXF: IPartDoc.ExportToDWG2 with the sheet metal action (1) and the geometry option (1),
    plus bend lines (4). Measured on SolidWorks 2026: it marks the document modified; the file also holds bend notes
    as MTEXT; everything is on layer 0, bend lines included."""
    options = 1 | (4 if bend_lines else 0)
    return bool(doc.ExportToDWG2(path, model_path, 1, True, null_object(), False, False, options, null_object()))


def sw_hem(doc, edge, kind: int, length_m: float, gap_m: float, angle_rad: float, radius_m: float):
    """A hem on the selected edge (measured on SolidWorks 2026): InsertSheetMetalHem2 reads the edge from the selection;
    position inside (0); a teardrop over about 225 degrees is refused by SolidWorks. The feature, or None."""
    sw_clear_selection(doc)
    if not edge.Select4(False, value(doc.SelectionManager, "CreateSelectData")):
        return None
    feat = doc.FeatureManager.InsertSheetMetalHem2(kind, 0, False, length_m, gap_m, angle_rad, radius_m, 0.0,
                                                   null_object(), True, 0, 0, True, 0.5, 0.0005, 0.0005)
    sw_clear_selection(doc)
    return feat


def sw_miter_flange(doc, sketch_feat, edges, radius_m, gap_m: float):
    """A miter flange (measured on SolidWorks 2026): InsertSheetMetalMiterFlange reads the profile sketch (mark 0) and the
    edges (mark 1, a chain) from the selection; flange position 1. The feature, or None."""
    sw_clear_selection(doc)
    if not sketch_feat.Select2(False, 0):
        return None
    for edge in edges:
        data = value(doc.SelectionManager, "CreateSelectData")
        data.Mark = 1
        if not edge.Select4(True, data):
            sw_clear_selection(doc)
            return None
    feat = doc.FeatureManager.InsertSheetMetalMiterFlange(radius_m is None, radius_m or 0.0, gap_m, True, True, 0.5,
                                                         0.0005, 0.0005, 0, False, 1, 0.0, 0.0, null_object())
    sw_clear_selection(doc)
    return feat


def sw_tab(doc, sketch_feat, thickness_m: float, thicken_dir: bool):
    """A tab: InsertSheetMetalBaseFlange2 on a closed sketch on the sheet's face, with Merge, UseFeatScope and
    UseAutoSelect all True (measured on SolidWorks 2026; the first flange needs them all False). The feature, or None."""
    sw_clear_selection(doc)
    if not sketch_feat.Select2(False, 0):
        return None
    feat = doc.FeatureManager.InsertSheetMetalBaseFlange2(thickness_m, thicken_dir, 0.001, 0.01, 0.0, False, 0, 0, 0,
                                                          null_object(), False, 0, 0.001, 0.001, 0.5, False,
                                                          True, True, True)
    sw_clear_selection(doc)
    return feat


def sw_forming_tool(app, doc, face, point_m, path: str, rotation_rad: float):
    """A forming tool from the design library on a face, placed at a point (measured on SolidWorks 2026):
    InsertFormToolFeature with the face selected at the point. It pushes away from that face; its Flip argument is
    ignored. SolidWorks opens the tool file and leaves it open: it is closed again unless it was open before.
    The feature, or None."""
    was_open = app.GetOpenDocumentByName(path) is not None
    sw_clear_selection(doc)
    data = value(doc.SelectionManager, "CreateSelectData")
    data.X, data.Y, data.Z = point_m
    try:
        if not face.Select4(False, data):
            return None
        return doc.FeatureManager.InsertFormToolFeature(path, False, rotation_rad, "", False, True, True, True, False)
    finally:
        sw_clear_selection(doc)
        if not was_open:
            try:
                if app.GetOpenDocumentByName(path) is not None:
                    app.CloseDoc(path)
            except Exception as e:
                logger.debug(f"Could not close {path}: {e}")
