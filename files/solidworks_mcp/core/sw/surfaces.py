"""Surfaces: the calls that make and read surface bodies.
The tools do the inputs, messages and measuring."""

import logging

logger = logging.getLogger(__name__)


def sw_sheet_bodies(doc) -> list:
    try:
        return list(doc.GetBodies2(1, False) or [])            # 1 = swSheetBody
    except Exception:
        return []


def sw_extrude_surface(doc, depth_m: float, mid_plane: bool, flip: bool) -> None:
    """Surface-Extrude of the selected sketch. The call returns nothing, even when it worked: look at the tree."""
    try:
        doc.FeatureManager.FeatureExtruRefSurface2(
            True, bool(flip), False,
            6 if mid_plane else 0, 0,                      # T1, T2 (6 = mid plane, 0 = blind)
            depth_m, 0.0,                                  # D1, D2
            False, False, False, False, 0.0, 0.0,         # draft
            False, False, False, False,                    # offset reverse, translate surface
            False, False, False, False)                    # cap ends, delete original face, knit
    except Exception as e:
        logger.debug(f"FeatureExtruRefSurface2 failed: {e}")
