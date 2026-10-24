"""Mates: the calls that add, edit, suppress and delete mates.
The tools do the inputs, messages and measuring."""

import logging

import pythoncom
import win32com.client

from ..com import null_object, value
from .common import sw_clear_selection, sw_rebuild

logger = logging.getLogger(__name__)


def sw_add_mate(asm, kind: int, align: int, flip: bool, distance_m: float, upper_m: float, lower_m: float,
                angle_rad: float):
    """Add a mate between the selected references: (mate or None, swAddMateError_e)."""
    status = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    mate = None
    try:
        mate = asm.AddMate5(kind, align, bool(flip), distance_m,
                            upper_m, lower_m,               # different limits make a limit mate
                            1.0, 1.0,                       # gear ratio
                            angle_rad, angle_rad, angle_rad,
                            False, False, 0,                # for positioning only, lock rotation, width option
                            status)
    except Exception as e:
        logger.debug(f"AddMate5 failed: {e}")
    return mate, int(status.value)


def sw_mate_data(feat):
    """The mate's specific feature (type, alignment, entities)."""
    return value(feat, "GetSpecificFeature2")


def sw_mate_entities(mate) -> list:
    """The entities a mate joins."""
    return [mate.MateEntity(i) for i in range(int(value(mate, "GetMateEntityCount")))]


def sw_modify_mate(asm, feat, values: dict) -> bool:
    """Write new definition values (Distance, Angle, FlipDimension, MateAlignment, limits) and rebuild."""
    definition = value(feat, "GetDefinition")
    for key, val in values.items():
        setattr(definition, key, val)
    done = bool(feat.ModifyDefinition(definition, asm, null_object()))
    sw_rebuild(asm)
    return done


def sw_set_suppressed(asm, feat, suppress: bool) -> None:
    feat.SetSuppression2(0 if suppress else 1, 1, win32com.client.VARIANT(pythoncom.VT_VARIANT, None))  # this config
    sw_rebuild(asm)
    sw_clear_selection(asm)
