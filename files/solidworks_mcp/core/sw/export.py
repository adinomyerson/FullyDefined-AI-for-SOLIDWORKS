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


# The STL export options are user preferences (swconst.tlb), not SaveAs arguments
STL_TOGGLES = {"binary": 69, "show_info": 70, "keep_coordinates": 71, "one_file": 72, "preview": 191}
STL_INTEGERS = {"quality": 78, "unit": 211}         # swSTLQuality_e; swLengthUnit_e
STL_DOUBLES = {"deviation": 2, "angle": 3}          # metres; radians
STL_QUALITY = {"coarse": 1, "fine": 2, "custom": 3}


def _stl_preferences(app) -> dict:
    """Every STL preference as it is now: (kind, number) -> value."""
    found = {}
    for kind, numbers, read in (("toggle", STL_TOGGLES, app.GetUserPreferenceToggle),
                                ("integer", STL_INTEGERS, app.GetUserPreferenceIntegerValue),
                                ("double", STL_DOUBLES, app.GetUserPreferenceDoubleValue)):
        for number in numbers.values():
            found[(kind, number)] = read(number)
    return found


def _set_stl_preferences(app, wanted: dict) -> None:
    write = {"toggle": app.SetUserPreferenceToggle, "integer": app.SetUserPreferenceIntegerValue,
             "double": app.SetUserPreferenceDoubleValue}
    for (kind, number), setting in wanted.items():
        write[kind](number, setting)


def sw_save_stl_copy(app, doc, path: str, quality: str, binary: bool, unit_code: int, deviation_m=None,
                     angle_rad=None):
    """Write the STL file as a copy with the options set for the call and every STL preference put back after,
    also when the export fails. Assemblies go to one file, in the model's own coordinates (not moved into positive
    space), with no preview or information dialog. Returns (saved, errors, restored)."""
    before = _stl_preferences(app)
    wanted = {("toggle", STL_TOGGLES["binary"]): binary, ("toggle", STL_TOGGLES["show_info"]): False,
              ("toggle", STL_TOGGLES["keep_coordinates"]): True, ("toggle", STL_TOGGLES["one_file"]): True,
              ("toggle", STL_TOGGLES["preview"]): False,
              ("integer", STL_INTEGERS["quality"]): STL_QUALITY[quality],
              ("integer", STL_INTEGERS["unit"]): unit_code}
    if quality == "custom":
        wanted[("double", STL_DOUBLES["deviation"])] = deviation_m
        wanted[("double", STL_DOUBLES["angle"])] = angle_rad
    saved, errors = False, byref_int()
    try:
        _set_stl_preferences(app, wanted)
        saved = bool(doc.Extension.SaveAs(path, 0, _SILENT | _AS_COPY, null_object(), errors, byref_int()))
    except Exception as e:
        logger.debug(f"STL export failed: {e}")
    finally:
        try:
            _set_stl_preferences(app, before)
        except Exception as e:
            logger.warning(f"Could not put the STL settings back: {e}")
    try:
        restored = _same_stl_preferences(_stl_preferences(app), before)
    except Exception:
        restored = False
    return saved, errors, restored


def _same_stl_preferences(now: dict, before: dict) -> bool:
    """Every STL preference as before. The deviation within 2%: SolidWorks rescales it to the active model by itself
    (an unsaved assembly's moved about 1% right after it was put back), which is not a setting left changed."""
    deviation = ("double", STL_DOUBLES["deviation"])
    if {k: v for k, v in now.items() if k != deviation} != {k: v for k, v in before.items() if k != deviation}:
        return False
    a, b = now.get(deviation), before.get(deviation)
    return a == b or (bool(a) and bool(b) and abs(a - b) <= 0.02 * abs(b))
