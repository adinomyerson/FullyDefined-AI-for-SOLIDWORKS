"""Sketches and planes: the calls that open and close a sketch and make an offset plane.
The tools do the inputs, messages and measuring."""

from .. import planes
from ..com import null_object, plane_feature_name
from ..results import Err, ToolError
from .common import sw_select_named


def sw_select_plane(doc, text, mark: int, append: bool = True):
    """Select the plane text names (Front / Top / Right, or a plane's own name) with a mark: by the English name
    first, exactly as before; only when that fails, by the name the document uses for that standard plane (a
    localized template). The name selected, or None."""
    name = plane_feature_name(text)
    if sw_select_named(doc, name, "PLANE", mark, append=append):
        return name
    local = planes.fallback_name(doc, text)
    if local and sw_select_named(doc, local, "PLANE", mark, append=append):
        return local
    return None


def sw_plane_feature(doc, text):
    """(name, feature) of the plane text names: the English name first, as before; the document's own name for a
    standard plane only when that is not there. The feature is None when neither is found."""
    name = plane_feature_name(text)
    try:
        feat = doc.FeatureByName(name)
    except Exception:
        feat = None
    if feat is None:
        local = planes.fallback_name(doc, text)
        if local:
            try:
                name, feat = local, doc.FeatureByName(local)
            except Exception:
                feat = None
    return name, feat


def sw_select_origin(doc, append: bool, mark: int) -> bool:
    """Select the origin's point: "Point1@Origin" first, as before; in a document whose origin has another name,
    that name (core/planes)."""
    if sw_select_named(doc, "Point1@Origin", "EXTSKETCHPOINT", mark, append=append):
        return True
    return planes.select_origin(doc, append, mark, after_english=True)


def sw_insert_sketch(doc) -> None:
    """Open a sketch on the selected plane. With a sketch open this would CLOSE it."""
    doc.InsertSketch2(True)


def sw_select_face(face) -> bool:
    return bool(face.Select4(False, null_object()))


def sw_insert_sketch_on_selection(doc) -> None:
    doc.SketchManager.InsertSketch(True)


def sw_insert_offset_plane(doc, flags: int, distance_m: float):
    """Reference plane from the selected plane or face; flags 8 = distance, 256 = flip. The plane, or None."""
    return doc.FeatureManager.InsertRefPlane(flags, distance_m, 0, 0, 0, 0)


def sw_close_sketch(doc) -> None:
    try:
        doc.SketchManager.InsertSketch(True)
    except Exception:
        try:
            doc.InsertSketch2(True)
        except Exception:
            pass


def sw_fully_define(doc, relations: int, scheme: int) -> int:
    """FullyDefineSketch on the open sketch, every entity, with the origin as horizontal and vertical datum. The
    origin must be selected with mark 6 (horizontal + vertical datum): passed as the datum argument, or left null,
    SolidWorks adds sizes but no positions and the sketch stays under-defined. Returns swAutodimStatus_e
    (0 = success)."""
    doc.ClearSelection2(True)
    try:
        if not sw_select_origin(doc, False, 6):
            raise ToolError("Could not select the origin as the datum", Err.SELECTION)
        return int(doc.SketchManager.FullyDefineSketch(True, True, relations, True, scheme, null_object(), scheme,
                                                       null_object(), -1, -1))
    finally:
        doc.ClearSelection2(True)


def sw_edit_sketch(doc, name: str) -> bool:
    """Open an existing sketch for editing: select it by name, then EditSketch. True when it was selected."""
    doc.ClearSelection2(True)
    if not doc.Extension.SelectByID2(name, "SKETCH", 0, 0, 0, False, 0, null_object(), 0):
        return False
    doc.EditSketch()
    doc.ClearSelection2(True)
    return True
