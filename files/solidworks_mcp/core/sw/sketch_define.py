"""Defining a sketch: the calls that select sketch entities, add and delete dimensions and relations, fit a spline.
The tools do the inputs, messages and measuring."""

import logging

from ..com import null_object, value
from .common import sw_clear_selection

logger = logging.getLogger(__name__)


def sw_select_entity(target) -> bool:
    """Add a sketch segment or point to the selection."""
    return bool(target.Select4(True, null_object()))


def sw_delete_relations_after(sketch, keep: int) -> bool:
    """Delete the relations added after the first `keep`. True if the count is back."""
    try:
        manager = sketch.RelationManager
        for relation in list(manager.GetRelations(0) or [])[keep:][::-1]:
            manager.DeleteRelation(relation)
        return int(manager.GetRelationsCount(0)) <= keep
    except Exception as e:
        logger.warning(f"Could not remove the new relation: {e}")
        return False


def sw_add_dimension(doc, orientation: str, x_m: float, y_m: float):
    """A driving dimension from the selected entities, its text at (x, y). The display dimension, or None."""
    if orientation == "horizontal":
        return doc.AddHorizontalDimension2(x_m, y_m, 0)
    if orientation == "vertical":
        return doc.AddVerticalDimension2(x_m, y_m, 0)
    return doc.AddDimension2(x_m, y_m, 0)


def sw_delete_dimension(doc, display, name):
    try:
        sw_clear_selection(doc)
        if value(display, "GetAnnotation").Select2(False, 0):
            doc.EditDelete()
    except Exception as e:
        logger.warning(f"Could not delete dimension {name}: {e}")
    sw_clear_selection(doc)


def sw_fit_spline(doc, tol_m: float) -> bool:
    """One spline through the selected chain of lines and arcs."""
    try:
        return bool(doc.FeatureManager.MakeStyledCurves2(tol_m, 0))
    except Exception as e:
        logger.debug(f"MakeStyledCurves2 failed: {e}")
        return False


def sw_undo(doc) -> None:
    try:
        doc.EditUndo2(1)
    except Exception as e:
        logger.debug(f"EditUndo2 failed: {e}")
