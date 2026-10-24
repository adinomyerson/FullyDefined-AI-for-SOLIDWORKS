"""Documents: the calls that create, open, activate, save and close them.
The tools do the inputs, messages and measuring."""

import logging

from ..com import byref_int, null_object

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
