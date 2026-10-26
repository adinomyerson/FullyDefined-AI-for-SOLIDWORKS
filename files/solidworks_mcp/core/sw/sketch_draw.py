"""Drawing geometry in the open sketch: the calls that create lines, circles, arcs, rectangles, points and
centerlines. They run inside the tool's sk.exact_input block. Each returns the new entity, or None.
The tools do the inputs, messages and measuring."""

import logging
from math import cos, sin

from ..com import null_object, value
from .common import sw_clear_selection

logger = logging.getLogger(__name__)


def sw_line(doc, x1_m: float, y1_m: float, x2_m: float, y2_m: float):
    return doc.SketchManager.CreateLine(x1_m, y1_m, 0, x2_m, y2_m, 0)


def sw_circle(doc, x_m: float, y_m: float, r_m: float):
    """A circle from its centre and an edge point."""
    return doc.SketchManager.CreateCircle(x_m, y_m, 0, x_m + r_m, y_m, 0)


def sw_rectangle(doc, x1_m: float, y1_m: float, x2_m: float, y2_m: float):
    return doc.SketchManager.CreateCornerRectangle(x1_m, y1_m, 0, x2_m, y2_m, 0)


def sw_arc(doc, cx_m: float, cy_m: float, r_m: float, a0_rad: float, a1_rad: float):
    """A counter-clockwise arc from the start angle to the end angle."""
    return doc.SketchManager.CreateArc(cx_m, cy_m, 0,
                                       cx_m + r_m * cos(a0_rad), cy_m + r_m * sin(a0_rad), 0,
                                       cx_m + r_m * cos(a1_rad), cy_m + r_m * sin(a1_rad), 0,
                                       1)             # 1 = counter-clockwise


def sw_slot(doc, cx_m: float, cy_m: float, ex_m: float, ey_m: float, width_m: float):
    """A straight slot from its centre and the end of its half centreline (measured: the centre distance is twice
    centre-to-end), centre-to-centre length type, without SolidWorks' own dimensions. It is a construction
    centreline, two arcs and two lines; SolidWorks adds the relations that hold its shape (measured: with its
    centre distance, end radius and position dimensioned it is fully defined)."""
    return doc.SketchManager.CreateSketchSlot(1, 0, width_m, cx_m, cy_m, 0, ex_m, ey_m, 0, 0, 0, 0, 1, False)


def sw_point(doc, x_m: float, y_m: float):
    return doc.SketchManager.CreatePoint(x_m, y_m, 0)


def sw_centerline(doc, x1_m: float, y1_m: float, x2_m: float, y2_m: float):
    return doc.SketchManager.CreateCenterLine(x1_m, y1_m, 0, x2_m, y2_m, 0)


def sw_delete_entities(doc, items) -> None:
    """Take out sketch entities (what was drawn of a polygon that failed)."""
    try:
        sw_clear_selection(doc)
        for item in items:
            item.Select4(True, null_object())
        if items:
            doc.EditDelete()
    except Exception as e:
        logger.debug(f"Could not remove the partial polygon: {e}")


def sw_tie_polygon(doc, lines, circle) -> int:
    """Corners on the construction circle, sides equal. Returns the number of relations added."""
    relations = 0
    try:
        for i, line in enumerate(lines):
            sw_clear_selection(doc)
            if value(line, "GetStartPoint2").Select4(False, null_object()) and circle.Select4(True, null_object()):
                doc.SketchAddConstraints("sgCOINCIDENT")
                relations += 1
            if i > 0:
                sw_clear_selection(doc)
                if lines[0].Select4(False, null_object()) and line.Select4(True, null_object()):
                    doc.SketchAddConstraints("sgSAMELENGTH")
                    relations += 1
    except Exception as e:
        logger.debug(f"Polygon relations failed: {e}")
    return relations
