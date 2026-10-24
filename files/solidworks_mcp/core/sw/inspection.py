"""Measuring and interference checks.
The tools do the inputs, messages and measuring."""

import logging

from ..com import null_object
from .common import sw_clear_selection

logger = logging.getLogger(__name__)


def sw_measure(doc, select):
    """Select two things with select(doc) -> bool and let SolidWorks measure them (minimum distance for arcs
    and cylinders, not centre to centre): the measure object, or None if selecting or measuring failed."""
    sw_clear_selection(doc)
    if not select(doc):
        sw_clear_selection(doc)
        return None
    measure = doc.Extension.CreateMeasure
    try:
        measure.ArcOption = 1                       # 1 = minimum distance
    except Exception as e:
        logger.debug(f"Could not set ArcOption: {e}")
    try:
        done = measure.Calculate(None)
    except TypeError:
        done = measure.Calculate                    # a property on some versions
    sw_clear_selection(doc)
    return measure if done else None


def sw_select_entities(first, second):
    """A select() for sw_measure: two faces or edges of a part."""
    def select(doc) -> bool:
        return bool(first.Select4(False, null_object())) and bool(second.Select4(True, null_object()))
    return select
