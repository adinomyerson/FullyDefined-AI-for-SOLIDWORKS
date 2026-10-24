"""Colours and appearance properties of parts and components.
The tools do the inputs, messages and measuring."""

import logging
from typing import List

import pythoncom
import win32com.client

logger = logging.getLogger(__name__)

DEFAULT_PROPERTIES = (0.8, 0.8, 0.8, 1.0, 1.0, 0.5, 0.3125, 0.0, 0.0)


def sw_set_properties(owner, values) -> None:
    """Write the 9 material property values of a part document or a component."""
    owner.MaterialPropertyValues = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, list(values))


def sw_model_properties(comp) -> List[float]:
    """The colour a component gets from its own file."""
    try:
        values = comp.GetModelMaterialPropertyValues("")
        if values is not None and len(values) >= 9:
            return [float(v) for v in values]
    except Exception as e:
        logger.debug(f"GetModelMaterialPropertyValues failed: {e}")
    return list(DEFAULT_PROPERTIES)
