"""Export: the call that writes a copy of the document in another format.
The tools do the inputs, messages and measuring."""

import logging

from ..com import byref_int, null_object

logger = logging.getLogger(__name__)

_STEP_AP = 75                   # swUserPreferenceIntegerValue_e: swStepAP
_SILENT, _AS_COPY = 1, 2        # swSaveAsOptions_e: the document keeps its own name and file


def sw_save_step_copy(app, doc, path: str, ap214: bool):
    """Write the STEP file as a copy, with the protocol set for the call and put back after.
    Returns (saved, errors) where errors.value is SolidWorks' error code."""
    previous = None
    try:
        previous = int(app.GetUserPreferenceIntegerValue(_STEP_AP))
        app.SetUserPreferenceIntegerValue(_STEP_AP, 214 if ap214 else 203)
    except Exception as e:
        logger.debug(f"Could not set the STEP protocol: {e}")

    saved, errors = False, byref_int()
    try:
        saved = bool(doc.Extension.SaveAs(path, 0, _SILENT | _AS_COPY, null_object(), errors, byref_int()))
    except Exception as e:
        logger.debug(f"STEP export failed: {e}")
    finally:
        if previous is not None:
            try:
                app.SetUserPreferenceIntegerValue(_STEP_AP, previous)
            except Exception:
                pass
    return saved, errors
