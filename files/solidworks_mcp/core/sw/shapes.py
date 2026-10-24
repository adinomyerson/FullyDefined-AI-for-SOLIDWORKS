"""Revolve, sweep, loft and shell: the calls that make the feature.
The tools do the inputs, messages and measuring."""

import logging

from ..com import null_object
from .common import sw_clear_selection

logger = logging.getLogger(__name__)


def sw_revolve(doc, angle_rad: float, cut: bool, flip: bool, merge: bool):
    """Revolve the selected sketch (axis mark 16). The feature, or None."""
    feat = None
    try:
        feat = doc.FeatureManager.FeatureRevolve2(
            True,                  # SingleDir
            True,                  # IsSolid
            False,                 # IsThin
            cut,                   # IsCut
            flip,                  # ReverseDir
            False,                 # BothDirectionUpToSameEntity
            0, 0,                  # Dir1Type, Dir2Type - 0 = blind (angle)
            angle_rad, 0.0,        # Dir1Angle, Dir2Angle
            False, False,          # OffsetReverse1, OffsetReverse2
            0.0, 0.0,              # OffsetDistance1, OffsetDistance2
            0, 0.0, 0.0,           # ThinType, ThinThickness1, ThinThickness2
            merge, True, True)     # Merge, UseFeatScope, UseAutoSelect
    except Exception as e:
        logger.debug(f"FeatureRevolve2 failed: {e}")
    return feat


def sw_sweep(doc, cut: bool, merge: bool, circular: bool, diameter_m: float):
    """Sweep (or swept cut) of the selected profile (mark 1) along the selected path (mark 4). The feature, or None."""
    feat = None
    try:
        if cut:
            feat = doc.FeatureManager.InsertCutSwept5(
                False, False, 0, False, False,      # Propagate, Alignment, TwistCtrlOption (0 = follow path), KeepTangency, BAdvancedSmoothing
                0, 0,                               # StartMatchingType, EndMatchingType
                False, 0.0, 0.0, 0,                 # IsThinBody, Thickness1, Thickness2, ThinType
                0,                                  # PathAlign
                True, True,                         # UseFeatScope, UseAutoSelect
                0.0, True,                          # TwistAngle, BMergeSmoothFaces
                False, False, False,                # AssemblyFeatureScope, AutoSelectComponents, PropagateFeatureToParts
                circular, diameter_m, 0)            # CircularProfile, CircularProfileDiameter, Direction
        else:
            feat = doc.FeatureManager.InsertProtrusionSwept4(
                False, False, 0, False, False,      # Propagate, Alignment, TwistCtrlOption, KeepTangency, BAdvancedSmoothing
                0, 0,                               # StartMatchingType, EndMatchingType
                False, 0.0, 0.0, 0,                 # IsThinBody, Thickness1, Thickness2, ThinType
                0,                                  # PathAlign
                merge, True, True,                  # Merge, UseFeatScope, UseAutoSelect
                0.0, True,                          # TwistAngle, BMergeSmoothFaces
                circular, diameter_m, 0)            # CircularProfile, CircularProfileDiameter, SweepDirection
    except Exception as e:
        logger.debug(f"Sweep call failed (cut={cut}): {e}")
    return feat


def sw_loft(doc, cut: bool, merge: bool):
    """Loft (or lofted cut) through the selected profiles (mark 1). The feature, or None."""
    feat = None
    try:
        if cut:
            feat = doc.FeatureManager.InsertCutBlend(
                False, True, False, 1.0,            # Closed, KeepTangency, ForceNonRational, TessToleranceFactor
                0, 0,                               # StartMatchingType, EndMatchingType
                False, 0.0, 0.0, 0,                 # IsThinBody, Thickness1, Thickness2, ThinType
                True, True)                         # UseFeatScope, UseAutoSelect
        else:
            feat = doc.FeatureManager.InsertProtrusionBlend2(
                False, True, False, 1.0,            # Closed, KeepTangency, ForceNonRational, TessToleranceFactor
                0, 0,                               # StartMatchingType, EndMatchingType
                1.0, 1.0, True, True,               # Start/EndTangentLength, Start/EndTangentDir
                False, 0.0, 0.0, 0,                 # IsThinBody, Thickness1, Thickness2, ThinType
                merge, True, True, 0)               # Merge, UseFeatScope, UseAutoSelect, GuideCurveInfluence
    except Exception as e:
        logger.debug(f"Loft call failed (cut={cut}): {e}")
    return feat


def sw_keep_only_faces(doc) -> int:
    """Only FACES count as openings: anything else still selected (the feature made by the previous call) is
    dropped from the selection. Returns the number of faces selected."""
    opened = 0
    try:
        manager = doc.SelectionManager
        total = int(manager.GetSelectedObjectCount2(-1))
        kinds = [int(manager.GetSelectedObjectType3(i, -1)) for i in range(1, total + 1)]
        opened = sum(1 for kind in kinds if kind == 2)           # swSelFACES
        if opened != total:
            faces = [manager.GetSelectedObject6(i, -1) for i in range(1, total + 1) if kinds[i - 1] == 2]
            sw_clear_selection(doc)
            for face in faces:
                face.Select4(True, null_object())
    except Exception as e:
        logger.debug(f"Could not read the selection: {e}")
    return opened


def sw_shell(doc, thickness_m: float, outward: bool) -> None:
    """Shell the body, removing the selected faces. Raises what SolidWorks raises."""
    doc.InsertFeatureShell(thickness_m, outward)
