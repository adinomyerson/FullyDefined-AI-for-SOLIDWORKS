"""
Documents, shared by the document and drawing tools: the template for a new document, saving a
document in place, and loading a file quietly. Shared so no tool calls another tool.
"""

import contextlib
import logging
import os

from . import install
from .com import byref_int

logger = logging.getLogger(__name__)

# swUserPreferenceStringValue_e: swDefaultTemplatePart / Assembly / Drawing
TEMPLATE_PREFS = {"part": 8, "assembly": 9, "drawing": 10}
OPEN_TYPES = {".sldprt": 1, ".sldasm": 2, ".slddrw": 3}
SILENT, SAVE_REFERENCED = 1, 4                  # swSaveAsOptions_e


# swUserPreferenceIntegerValue_e and their enums (swconst.tlb): what a template sets that a user may have changed
UNIT_SYSTEM, LENGTH_UNIT, DRAFTING_STANDARD = 263, 47, 13
UNIT_SYSTEMS = {1: "CGS", 2: "MKS", 3: "IPS", 4: "Custom", 5: "MMGS"}
LENGTH_UNITS = {0: "mm", 1: "cm", 2: "m", 3: "in", 4: "ft", 5: "ft-in", 6: "angstrom", 7: "nm", 8: "micron",
                9: "mil", 10: "uin"}
STANDARDS = {1: "ANSI", 2: "ISO", 3: "DIN", 4: "JIS", 5: "BS", 6: "GOST", 7: "GB", 8: "user-defined"}


def template_info(doc, path: str) -> dict:
    """What a new document got from its template (read only): the file, the unit system, the length
    unit and the drafting standard. A value that cannot be read is None."""
    def read(pref, names):
        try:
            number = int(doc.Extension.GetUserPreferenceInteger(pref, 0))
        except Exception as e:
            logger.debug(f"Cannot read document preference {pref}: {e}")
            return None
        return names.get(number, f"unknown ({number})")

    return {"file": os.path.basename(path or ""), "unit_system": read(UNIT_SYSTEM, UNIT_SYSTEMS),
            "length_unit": read(LENGTH_UNIT, LENGTH_UNITS), "standard": read(DRAFTING_STANDARD, STANDARDS)}


@contextlib.contextmanager
def length_unit_during(doc, unit: str):
    """Inside the block the document's length unit is `unit` ("mm" or "inch"); afterwards, also when the block
    fails, it is put back. Measured on SolidWorks 2026: setting the length unit turns the unit system into
    Custom; putting back the length unit first and the unit system last restores both exactly, and leaves the
    user's other unit settings (decimals, mass unit) alone. Yields a dict: changed (the unit had to be switched)
    and, after the block, restored (both settings read back as they were)."""
    ext = doc.Extension
    read = lambda pref: int(ext.GetUserPreferenceInteger(pref, 0))      # noqa: E731
    before = {LENGTH_UNIT: read(LENGTH_UNIT), UNIT_SYSTEM: read(UNIT_SYSTEM)}
    wanted = {"mm": 0, "inch": 3}[unit]                                  # swLengthUnit_e
    state = {"changed": before[LENGTH_UNIT] != wanted, "restored": True}
    if not state["changed"]:
        yield state
        return
    ext.SetUserPreferenceInteger(LENGTH_UNIT, 0, wanted)
    try:
        yield state
    finally:
        for pref in (LENGTH_UNIT, UNIT_SYSTEM):                         # this order: see the docstring
            try:
                ext.SetUserPreferenceInteger(pref, 0, before[pref])
            except Exception as e:
                logger.warning(f"Could not put document preference {pref} back: {e}")
        try:
            state["restored"] = all(read(pref) == value for pref, value in before.items())
        except Exception:
            state["restored"] = False
        if not state["restored"]:
            logger.warning(f"The document's unit settings were not put back exactly: wanted {before}")


def template(app, kind: str, exe_path: str = "auto") -> str:
    """SolidWorks' own default template (works for any version), else one found on disk."""
    try:
        path = app.GetUserPreferenceStringValue(TEMPLATE_PREFS[kind])
        if path and os.path.isfile(path):
            return path
    except Exception as e:
        logger.debug(f"No default {kind} template from SolidWorks: {e}")
    return install.find_template(kind, install.find_exe(exe_path)) or ""


def save_in_place(doc, options: int = SILENT):
    """Save a document that already has a file. Returns (saved, error code, warning code)."""
    errors, warnings = byref_int(), byref_int()
    saved = doc.Save3(options, errors, warnings)
    return bool(saved) and errors.value == 0, errors.value, warnings.value


def load(app, path: str):
    """Load a part, assembly or drawing file silently; the document, or None. It need not become active."""
    errors, warnings = byref_int(), byref_int()
    doc = app.OpenDoc6(path, OPEN_TYPES.get(os.path.splitext(path)[1].lower(), 1), 1, "", errors, warnings)
    return doc if doc is not None and errors.value == 0 else None
