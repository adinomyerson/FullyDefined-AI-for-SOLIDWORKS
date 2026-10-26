"""Documents: the calls that create, open, activate, save and close them.
The tools do the inputs, messages and measuring."""

import logging

import pythoncom
import win32com.client

from ..com import byref_int, null_object, value

logger = logging.getLogger(__name__)


def sw_new_document(app, template_path: str):
    """A new document from a template, shown isometric and zoomed to fit. None when SolidWorks made none."""
    doc = app.NewDocument(template_path, 0, 0, 0)
    if doc is not None:
        try:
            doc.ShowNamedView2("*Isometric", 7)
            doc.ViewZoomtofit2()
        except Exception:
            pass
    return doc


def sw_open(app, path: str, kind: int):
    """Open a file. Returns (document or None, SolidWorks' error code)."""
    errors, warnings = byref_int(), byref_int()
    doc = app.OpenDoc6(path, kind, 0, "", errors, warnings)
    return doc, errors.value


def sw_activate(app, target: str) -> None:
    """Bring a loaded document to the front by path or title; no rebuild."""
    try:
        app.ActivateDoc3(target, False, 1, byref_int())       # 1 = don't rebuild
    except Exception as e:
        logger.debug(f"ActivateDoc3({target}) failed: {e}")


def sw_dependencies(doc) -> list:
    """[name, path, name, path, ...] of the files a drawing or assembly references, or []."""
    try:
        return list(doc.GetDependencies2(True, True, False) or [])
    except Exception as e:
        logger.debug(f"GetDependencies2 failed: {e}")
        return []


def sw_save_as(doc, path: str):
    """Save under a new name; the extension decides the format. Returns the method that worked, or None."""
    def extension():
        errors = byref_int()
        return doc.Extension.SaveAs(path, 0, 0, null_object(), errors, byref_int()) and errors.value == 0

    ways = (("SaveAs", lambda: doc.SaveAs(path)),
            ("Extension.SaveAs", extension),
            ("Extension.SaveAs2", lambda: doc.Extension.SaveAs2(path, 0, 0, None, "", False, byref_int(), byref_int())))
    for name, save in ways:
        try:
            if save():
                return name
        except Exception as e:
            logger.debug(f"saving {path} with {name} raised: {e}")
    return None


def sw_close(app, name: str) -> None:
    app.CloseDoc(name)


# Custom properties (swconst.tlb): swCustomInfoText, swCustomPropertyReplaceValue, the add and delete results
PROPERTY_TEXT, REPLACE_VALUE = 30, 2
ADD_RESULTS = {0: "added or changed", 1: "failed", 2: "the property exists with another type",
               3: "the value does not fit the type", 4: "the property exists with an older type"}
DELETE_RESULTS = {0: "deleted", 1: "not present", 2: "linked to another property"}
PROPERTY_TYPES = {0: "unknown", 30: "text", 64: "date", 3: "number", 5: "double", 11: "yes_or_no", 105: "equation"}


def sw_property_manager(doc, configuration: str):
    """The custom property manager of a configuration, or of the file itself for ""."""
    return doc.Extension.CustomPropertyManager(configuration or "")


def sw_properties(manager) -> list:
    """[(name, value as typed, resolved value, type code)] of every property, in SolidWorks' order."""
    rows = []
    for name in (value(manager, "GetNames") or []):
        typed = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, "")
        resolved = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, "")
        flags = [win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BOOL, False) for _ in range(2)]
        try:
            manager.Get6(name, False, typed, resolved, flags[0], flags[1])
            kind = int(manager.GetType2(name))
        except Exception as e:
            logger.debug(f"Property {name}: {e}")
            kind = 0
        rows.append((str(name), str(typed.value or ""), str(resolved.value or ""), kind))
    return rows


def sw_set_property(manager, name: str, text: str, kind: int = PROPERTY_TEXT) -> int:
    """Add a property (text unless kind says otherwise) or replace its value; SolidWorks' add result (0 = added or
    changed)."""
    return int(manager.Add3(name, kind or PROPERTY_TEXT, text, REPLACE_VALUE))


def sw_delete_property(manager, name: str) -> int:
    """SolidWorks' delete result (0 = deleted)."""
    return int(manager.Delete2(name))
