"""Calls every area needs: clear the selection, rebuild the top level, redraw.
The tools do the inputs, messages and measuring."""

import logging

from ..com import null_object

logger = logging.getLogger(__name__)


def sw_clear_selection(doc) -> None:
    doc.ClearSelection2(True)


def sw_rebuild(doc) -> None:
    """Rebuild the top level only: rebuilding everything marks every model file modified."""
    try:
        doc.ForceRebuild3(True)
    except Exception as e:
        logger.debug(f"ForceRebuild3 failed: {e}")


def sw_redraw(doc) -> None:
    try:
        doc.GraphicsRedraw2()
    except Exception as e:
        logger.debug(f"GraphicsRedraw2 failed: {e}")


def sw_select_named(doc, name: str, kind: str, mark: int, append: bool = True) -> bool:
    """Select a sketch, plane, axis, sketch segment or feature by name and type with a mark."""
    return bool(doc.Extension.SelectByID2(name, kind, 0, 0, 0, append, mark, null_object(), 0))
