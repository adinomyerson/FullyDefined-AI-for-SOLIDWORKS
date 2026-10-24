"""Editing the feature tree.
The tools do the inputs, messages and measuring."""

import logging

logger = logging.getLogger(__name__)


def sw_display_dimension(display):
    """The dimension behind a display dimension."""
    return display.GetDimension2(0)


def sw_set_dimension_value(dim, value_m: float) -> int:
    """Set a dimension's system value (m or rad) in this configuration; 0 means SolidWorks took it."""
    return int(dim.SetSystemValue3(value_m, 1, None))


def sw_delete_selected(doc, feat, absorbed: bool = False) -> bool:
    """Select one feature alone and delete it; with absorbed also the sketch it was made from
    (swDelete_Absorbed). False if it could not be selected."""
    doc.ClearSelection2(True)
    if feat is None or not feat.Select2(False, 0):
        return False
    doc.Extension.DeleteSelection2(2 if absorbed else 0)
    doc.ClearSelection2(True)
    return True
