"""Patterns and mirrors: the calls that select what to pattern and make the pattern.
The tools do the inputs, messages and measuring."""

import logging

from ..com import value
from .common import sw_clear_selection, sw_select_named

logger = logging.getLogger(__name__)


def sw_select_feature(doc, name: str, mark: int) -> bool:
    return sw_select_named(doc, name, "BODYFEATURE", mark)


def sw_select_face(doc, face, mark: int, append: bool) -> bool:
    """Select a face with a mark."""
    select_data = value(doc.SelectionManager, "CreateSelectData")
    select_data.Mark = mark
    return bool(face.Select4(append, select_data))


def sw_insert_axis(doc, plane_a: str, plane_b: str) -> bool:
    """A reference axis where two standard planes meet. False when the planes could not be selected."""
    sw_clear_selection(doc)
    if not (sw_select_named(doc, plane_a, "PLANE", 0, append=False) and sw_select_named(doc, plane_b, "PLANE", 0)):
        sw_clear_selection(doc)
        return False
    doc.InsertAxis2(True)
    sw_clear_selection(doc)
    return True


def sw_first_that_works(manager, attempts):
    """Try the API variants of different SolidWorks versions. Returns (feature or None, method)."""
    for method, args in attempts:
        try:
            feat = getattr(manager, method)(*args)
            if feat is not None:
                return feat, method
        except Exception as e:
            logger.debug(f"{method} failed: {e}")
    return None, ""


def sw_linear_pattern(doc, count: int, spacing_m: float, count2: int, spacing2_m: float, flip1: bool, flip2: bool):
    """Linear pattern of the selected features (mark 4) along the selected directions (marks 1 and 2)."""
    return sw_first_that_works(doc.FeatureManager, (
        ("FeatureLinearPattern3", (count, spacing_m, count2, spacing2_m, flip1, flip2, "NULL", "NULL", False, False)),
        ("FeatureLinearPattern2", (count, spacing_m, count2, spacing2_m, flip1, flip2, "NULL", "NULL", False)),
    ))


def sw_circular_pattern(doc, count: int, spacing: float, flip: bool, equal_spacing: bool):
    """Circular pattern of the selected features (mark 4) about the selected axis (mark 1)."""
    return sw_first_that_works(doc.FeatureManager, (
        ("FeatureCircularPattern4", (count, spacing, flip, "NULL", False, equal_spacing, False)),
        ("FeatureCircularPattern3", (count, spacing, flip, "NULL", False, equal_spacing)),
    ))


def sw_mirror(doc):
    """Mirror the selected features (mark 1) about the selected plane or face (mark 2)."""
    return sw_first_that_works(doc.FeatureManager, (
        ("InsertMirrorFeature2", (False, False, False, False, 0)),
        ("InsertMirrorFeature", (False, False, False, False)),
    ))
