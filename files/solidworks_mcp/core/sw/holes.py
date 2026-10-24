"""Holes: the calls that make a Hole Wizard hole and take one out again.
The tools do the inputs, messages and measuring."""

import logging

from ..com import value
from .common import sw_clear_selection

logger = logging.getLogger(__name__)
ISO = 8                                  # swStandardISO


def sw_select_face_at(doc, face, x_m: float, y_m: float, z_m: float) -> bool:
    """Select the face AT the hole position: the selection point is where the hole goes."""
    sw_clear_selection(doc)
    select_data = value(doc.SelectionManager, "CreateSelectData")
    select_data.X, select_data.Y, select_data.Z = x_m, y_m, z_m
    return bool(face.Select4(False, select_data))


def sw_hole_wizard(doc, generic_type: int, fastener_type: int, size_text: str, end_type: int, diameter_m: float,
                   depth_m: float, v):
    """Hole Wizard on the selected face. v is Value1..Value12, one number for all twelve or a list of twelve:
    -1 takes a value from the standard, 0 is "not used". Returns the feature, or None when SolidWorks made none."""
    values = list(v) if isinstance(v, (list, tuple)) else [v] * 12
    candidate = None
    try:
        candidate = doc.FeatureManager.HoleWizard5(
            generic_type, ISO, fastener_type, size_text, end_type, diameter_m, depth_m,
            0.0,                                   # Length (slots)
            *values,                               # Value1..Value12
            "", False,                             # ThreadClass, RevDir
            True, True, True, True, False)         # FeatureScope, AutoSelect, AssemblyFeatureScope, AutoSelectComponents, PropagateFeatureToParts
    except Exception as e:
        logger.debug(f"HoleWizard5 (values {v}) failed: {e}")
    sw_clear_selection(doc)
    return candidate


def sw_delete_with_sketches(doc, name: str) -> None:
    """Delete a feature by name together with its sketches."""
    feat = doc.FeatureByName(name)
    if feat is not None:
        sw_clear_selection(doc)
        if feat.Select2(False, 0):
            doc.Extension.DeleteSelection2(2)          # with its sketches
