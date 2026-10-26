"""Drawings: the calls that change a drawing (views, sheets, dimensions, tables, balloons).
The tools do the inputs, messages and measuring."""

import logging
import os
import tempfile

import pythoncom
import win32com.client

from .. import drawing as dwg
from ..com import byref_int
from ..com import doc_path
from ..com import doc_title
from ..com import null_object
from ..com import value
from .common import sw_clear_selection, sw_rebuild

logger = logging.getLogger(__name__)


def sw_dimension(drawing, view, entities, mode, x: float, y: float):
    """Select the entities in the view and add a driven dimension with its text at (x, y): the display
    dimension, or None."""
    sw_clear_selection(drawing)
    drawing.ActivateView(str(view.Name))
    picked = all(bool(view.SelectEntity(obj, i > 0)) for i, obj in enumerate(entities))
    if not picked:
        return None
    maker = {"horizontal": "AddHorizontalDimension2", "vertical": "AddVerticalDimension2"}.get(mode, "AddDimension2")
    try:
        return getattr(drawing, maker)(x, y, 0.0)
    except Exception as e:
        logger.debug(f"{maker}: {e}")
        return None


def sw_align_dimensions(drawing, displays, gap_m: float) -> None:
    """Select display dimensions and let SolidWorks auto-arrange them."""
    try:
        select_data = value(drawing.SelectionManager, "CreateSelectData")
    except Exception:
        select_data = null_object()
    sw_clear_selection(drawing)
    selected = 0
    for display in displays:
        try:
            if value(display, "GetAnnotation").Select3(True, select_data):
                selected += 1
        except Exception as e:
            logger.debug(f"Select dimension: {e}")
    if selected:
        try:
            drawing.Extension.AlignDimensions(0, gap_m)    # 0 = auto arrange
        except Exception as e:
            logger.debug(f"AlignDimensions: {e}")
    sw_clear_selection(drawing)


def sw_delete_dimension(drawing, display) -> bool:
    """Select a drawing dimension and delete it."""
    sw_clear_selection(drawing)
    try:
        if not value(display, "GetAnnotation").Select2(False, 0):
            return False
        return bool(drawing.Extension.DeleteSelection2(0))
    except Exception as e:
        logger.debug(f"Delete dimension: {e}")
        return False
    finally:
        sw_clear_selection(drawing)


def sw_set_diametric(display, diametric: bool) -> None:
    """A round dimension shown as a diameter (True) or a radius (False)."""
    display.Diametric = bool(diametric)


def sw_set_annotation_color(display, colorref: int) -> None:
    """Text colour of a dimension, as a Windows COLORREF (0x00BBGGRR)."""
    value(display, "GetAnnotation").Color = int(colorref)


def sw_precision(display) -> tuple:
    """(decimals of the value, decimals of its tolerance) a dimension shows; -1 = the document's setting."""
    return int(value(display, "GetPrimaryPrecision2")), int(value(display, "GetPrimaryTolPrecision2"))


def sw_set_precision(display, decimals: int, tolerance_decimals: int) -> None:
    """Decimals of the value and its tolerance; the dual-unit ones are left as they are. The tool reads them back."""
    try:
        dual, dual_tol = int(value(display, "GetAlternatePrecision2")), int(value(display, "GetAlternateTolPrecision2"))
        display.SetPrecision3(decimals, dual, tolerance_decimals, dual_tol)
    except Exception as e:
        logger.debug(f"SetPrecision3: {e}")


def sw_remove_alignment(view) -> None:
    view.RemoveAlignment()


def sw_default_alignment(view) -> None:
    view.UseDefaultAlignment()


def sw_move_annotation(display, x_m: float, y_m: float) -> bool:
    """Put a dimension's text at a sheet position (metres); its dimension line follows."""
    try:
        return bool(value(display, "GetAnnotation").SetPosition2(x_m, y_m, 0.0))
    except Exception as e:
        logger.debug(f"SetPosition2: {e}")
        return False


def sw_insert_table(drawing, x: float, y: float, rows: int, columns: int):
    """A general table anchored by its bottom right corner (4)."""
    return drawing.InsertTableAnnotation2(False, x, y, 4, "", rows, columns)


def sw_set_cell(table, row: int, column: int, text: str) -> None:
    dispid = table._oleobj_.GetIDsOfNames("Text")
    table._oleobj_.Invoke(dispid, 0, pythoncom.DISPATCH_PROPERTYPUT, 0, row, column, str(text))


def sw_insert_bom(view, x: float, y: float, bom_type: int, configuration: str):
    try:
        # anchor 2 = top right corner; 0 = flat numbering
        return view.InsertBomTable4(False, x, y, 2, bom_type, configuration, "", False, 0, False)
    except Exception as e:
        logger.debug(f"InsertBomTable4: {e}")
        return None


PART_NUMBER_COLUMN = 201         # swBomTableColumnType_PartNumber


def sw_bom_column_property(table, column: int) -> str:
    """The custom property a BOM column shows ('' for another kind of column). Measured: a column given a property
    keeps column type 0, so the property itself is read, not the type."""
    try:
        return str(table.GetColumnCustomProperty(column) or "")
    except Exception as e:
        logger.debug(f"BOM column {column}: {e}")
        return ""


def sw_part_number_column(table):
    """The index of the BOM's part number column, or None."""
    try:
        return next((c for c in range(int(table.ColumnCount))
                     if int(table.GetColumnType2(c, False)) == PART_NUMBER_COLUMN), None)
    except Exception as e:
        logger.debug(f"BOM part number column: {e}")
        return None


def sw_delete_column(table, column: int) -> None:
    try:
        table.DeleteColumn(column)
    except Exception as e:
        logger.debug(f"Could not delete column {column}: {e}")


def sw_add_bom_column(table, prop: str, title: str):
    """A new last column showing a custom property of each row's component; its index, or None."""
    last = int(table.ColumnCount) - 1
    try:
        if not table.InsertColumn2(4, last, title, 1):        # swTableItemInsertPosition_Last, single line tight
            return None
        column = int(table.ColumnCount) - 1
        if not table.SetColumnCustomProperty(column, prop):
            return None
        table.SetColumnTitle2(column, title, False)
        return column
    except Exception as e:
        logger.debug(f"BOM column {prop}: {e}")
        return None


CENTER_MARK_ANNOTATION = 13      # swAnnotationType_e: swCenterMarkSym


def _method(obj, name: str, *args):
    """A method call by dispatch id. Measured: drawing.InsertCenterLine2() through pywin32's late binding gives
    'Member not found'; invoked as a method by its id it works."""
    dispid = obj._oleobj_.GetIDsOfNames(name)
    return obj._oleobj_.Invoke(dispid, 0, pythoncom.DISPATCH_METHOD, True, *args)


def sw_center_mark_points(view) -> list:
    """(x, y) of every centre mark in a view, in the view's own sketch coordinates (m), one per mark of a group.
    Measured: the view's GetCenterMarks / GetCenterMarkCount give nothing; its annotations do."""
    points = []
    for note in (value(view, "GetAnnotations") or []):
        try:
            if int(value(note, "GetType")) != CENTER_MARK_ANNOTATION:
                continue
            mark = value(note, "GetSpecificAnnotation")
            for i in range(max(int(value(mark, "GroupCount")), 1)):
                if bool(mark.IsDeleted(i)):
                    continue
                p = [float(v) for v in mark.GetPosition(i)]
                points.append((p[0], p[1]))
        except Exception as e:
            logger.debug(f"Centre mark: {e}")
    return points


def sw_circle_axis(edge):
    """The axis of a circular edge in its part's coordinates, or None."""
    try:
        return [float(v) for v in value(value(edge, "GetCurve"), "CircleParams")][3:6]
    except Exception as e:
        logger.debug(f"Circle axis: {e}")
        return None


def sw_center_mark(drawing, view, edge) -> bool:
    """A single centre mark on a round edge of the view, sized by the drafting standard; True when made."""
    sw_clear_selection(drawing)
    try:
        drawing.ActivateView(str(view.Name))
        made = bool(view.SelectEntity(edge, False)) and drawing.InsertCenterMark3(2, False, False) is not None
    except Exception as e:
        logger.debug(f"InsertCenterMark3: {e}")
        made = False
    sw_clear_selection(drawing)
    return made


def sw_centerline_count(view) -> int:
    """How many centrelines a view has. A centreline gives no ends or position through the API (its annotation's
    position is None), only what it is attached to."""
    return len(value(view, "GetCenterLines") or [])


def sw_auto_centerlines(drawing, view) -> bool:
    """Centrelines on the view's cylinders seen from the side: InsertCenterLine2 with the view selected. False
    when the view could not be selected or the call failed (not the same as nothing to put a centreline on)."""
    sw_clear_selection(drawing)
    called = False
    try:
        # measured: on a view whose edges nothing has read since it was made, the command finds nothing
        drawing.ActivateView(str(view.Name))
        view.GetVisibleEntities2(null_object(), 1)
        if drawing.Extension.SelectByID2(str(view.Name), "DRAWINGVIEW", 0, 0, 0, False, 0, null_object(), 0):
            _method(drawing, "InsertCenterLine2")
            called = True
    except Exception as e:
        logger.debug(f"InsertCenterLine2: {e}")
    sw_clear_selection(drawing)
    return called


def sw_auto_balloons(drawing, view, layout: int) -> bool:
    sw_clear_selection(drawing)
    if not drawing.Extension.SelectByID2(str(view.Name), "DRAWINGVIEW", 0, 0, 0, False, 0, null_object(), 0):
        return False
    try:
        options = value(drawing, "CreateAutoBalloonOptions")
        options.Layout = layout
        options.IgnoreMultiple = True
        options.Style = 1               # circular
        options.Size = 2                # two characters
        options.UpperTextContent = 1    # item number
        drawing.AutoBalloon5(options)
    except Exception as e:
        logger.debug(f"AutoBalloon5: {e}")
    return True


def sw_new_drawing(app, template: str, paper: int):
    return app.NewDocument(template, paper, 0.0, 0.0)


def sw_close(app, title: str) -> None:
    app.CloseDoc(title)


def sw_set_sheet(sheet, paper: int, num: float, den: float, first_angle: bool, width: float, height: float) -> None:
    try:
        sheet.SetProperties2(paper, 13, num, den, first_angle, width, height, True)
    except Exception as e:
        logger.debug(f"SetProperties2 failed: {e}")
        sw_set_scale(sheet, num, den)


def sw_set_scale(sheet, num: float, den: float) -> None:
    try:
        sheet.SetScale(num, den, False, False)
    except Exception as e:
        logger.debug(f"SetScale failed: {e}")


def sw_auto_view_scaling(app, on):
    """Switch SolidWorks' "automatically scale new drawing views" (86) on or off; returns the old state, or
    None if it could not be read. With it on, inserting views changed the sheet scale behind our back."""
    try:
        before = bool(app.GetUserPreferenceToggle(86))
        app.SetUserPreferenceToggle(86, on)
        return before
    except Exception as e:
        logger.debug(f"Automatic view scaling: {e}")
        return None


def sw_standard_views(drawing, path: str, first_angle: bool):
    return drawing.Create1stAngleViews2(path) if first_angle else drawing.Create3rdAngleViews2(path)


def sw_model_view(drawing, path: str, view_name: str, x: float, y: float):
    return drawing.CreateDrawViewFromModelView3(path, view_name, x, y, 0)


def sw_place_view(view, x: float, y: float) -> None:
    view.Position = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [x, y])


def sw_refresh_views(drawing) -> None:
    """Make SolidWorks work out the views' outlines and places now. A new or moved detail view keeps a stale
    outline (55 mm instead of 98 in draw.detail_views) until the drawing is painted, which never happens while the
    window is minimised; no redraw or rebuild helps, a 1 x 1 pixel render does."""
    path = os.path.join(tempfile.gettempdir(), f"swmcp_refresh_{os.getpid()}.bmp")
    try:
        drawing.SaveBMP(path, 1, 1)
    except Exception as e:
        logger.debug(f"Refresh render: {e}")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def sw_projected_view(drawing, from_view, x: float, y: float) -> None:
    sw_clear_selection(drawing)
    if drawing.Extension.SelectByID2(from_view.Name, "DRAWINGVIEW", 0, 0, 0, False, 0, null_object(), 0):
        try:
            drawing.CreateUnfoldedViewAt3(x, y, 0, False)
        except Exception as e:
            logger.warning(f"Projected view failed: {e}")


def sw_import_dimensions(drawing) -> int:
    """The model's dimensions marked for drawings, into every view: how many came in."""
    try:
        items = drawing.InsertModelAnnotations3(0, 32768, True, True, False, False)   # entire model, for drawing
        return len(items) if items else 0
    except Exception as e:
        logger.warning(f"Importing dimensions failed: {e}")
        return 0


def sw_activate_sheet(drawing, name: str) -> None:
    drawing.ActivateSheet(name)


def sw_finish(drawing) -> None:
    """Leave the drawing with nothing selected and no view being edited."""
    try:
        sw_clear_selection(drawing)
    except Exception:
        pass
    try:
        sw_activate_sheet(drawing, dwg.current_sheet_name(drawing))
    except Exception as e:
        logger.debug(f"ActivateSheet: {e}")
    sw_rebuild(drawing)
    try:
        sw_clear_selection(drawing)
    except Exception:
        pass


def sw_new_sheet(drawing, name: str, paper: int, num: float, den: float, first_angle: bool, width: float,
                 height: float) -> bool:
    return bool(drawing.NewSheet4(name, paper, 13, num, den, first_angle, "", width, height, "", 0, 0, 0, 0, 1, 1))


def sw_remove_sheet(drawing, name: str) -> None:
    """Take a sheet out again (an add that did not finish)."""
    try:
        names = dwg.sheet_names(drawing)
        if name not in names or len(names) < 2:
            return
        sw_activate_sheet(drawing, next(n for n in names if n != name))
        sw_clear_selection(drawing)
        if drawing.Extension.SelectByID2(name, "SHEET", 0, 0, 0, False, 0, null_object(), 0):
            drawing.Extension.DeleteSelection2(0)
        sw_clear_selection(drawing)
    except Exception as e:
        logger.debug(f"Could not remove sheet {name}: {e}")


def sw_activate_doc(app, doc) -> bool:
    """Bring an open drawing to the front (SolidWorks activates by file path, or by name when unsaved)."""
    path, title = str(doc_path(doc) or ""), str(doc_title(doc))
    full_title = title
    try:
        suffix = " - " + dwg.current_sheet_name(doc)
        if title.endswith(suffix):
            title = title[:-len(suffix)]
    except Exception:
        pass
    for target in dict.fromkeys((path, title, full_title, title.split(' - ')[0])):
        if not target:
            continue
        try:
            app.ActivateDoc3(target, False, 1, byref_int())
        except Exception as e:
            logger.debug(f"ActivateDoc3({target}) failed: {e}")
        active = app.ActiveDoc
        if active is not None and dwg.same_doc(active, doc):
            return True
    return False


def sw_copy_sheet(source, sheet: str) -> bool:
    sw_clear_selection(source)
    picked = source.Extension.SelectByID2(sheet, "SHEET", 0, 0, 0, False, 0, null_object(), 0)
    if picked:
        source.EditCopy()
    sw_clear_selection(source)
    return bool(picked)


def sw_paste_sheet(drawing) -> bool:
    return bool(drawing.PasteSheet(1, 1))                  # 1 = after the active sheet


def sw_rename_sheet(drawing, old: str, new: str) -> None:
    drawing.Sheet(old).SetName(new)


def sw_activate_view(drawing, name: str) -> bool:
    return bool(drawing.ActivateView(name))


def sw_sketch_line(drawing, a, b):
    manager = drawing.SketchManager
    manager.AddToDB = True
    try:
        return manager.CreateLine(a[0], a[1], 0.0, b[0], b[1], 0.0)
    finally:
        manager.AddToDB = False


def sw_sketch_circle(drawing, centre, radius: float):
    manager = drawing.SketchManager
    manager.AddToDB = True
    try:
        return manager.CreateCircleByRadius(centre[0], centre[1], 0.0, radius)
    finally:
        manager.AddToDB = False


def sw_select_segment(segment) -> bool:
    for method, args in (("Select4", (False, null_object())), ("Select2", (False, 0)), ("Select", (False,))):
        try:
            if getattr(segment, method)(*args):
                return True
        except Exception as e:
            logger.debug(f"{method}: {e}")
    return False


def sw_delete_segment(drawing, segment) -> None:
    try:
        sw_clear_selection(drawing)
        if sw_select_segment(segment):
            drawing.EditDelete()
    except Exception as e:
        logger.debug(f"Could not delete the helper sketch entity: {e}")


def sw_section_view(drawing, x: float, y: float, label: str):
    try:
        return drawing.CreateSectionViewAt5(x, y, 0.0, label, 0, null_object(), 0.0)
    except Exception as e:
        logger.debug(f"CreateSectionViewAt5: {e}")
        return None


def sw_detail_view(drawing, x: float, y: float, factor: float, label: str):
    try:
        # style 0 = per standard, 1 = full outline shown, pinned, no profile scaling
        return drawing.CreateDetailViewAt4(x, y, 0.0, 0, factor, 1.0, label, 1, True, False, False, 0)
    except Exception as e:
        logger.debug(f"CreateDetailViewAt4: {e}")
        return None


def sw_save_pdf_copy(drawing, path: str):
    """Write a PDF copy; the drawing keeps its own name and file. (saved, error code)."""
    errors = byref_int()
    try:
        saved = bool(drawing.Extension.SaveAs(path, 0, 1 | 2, null_object(), errors, byref_int()))  # silent, copy
    except Exception as e:
        logger.debug(f"PDF export failed: {e}")
        saved = False
    return saved, errors.value


def sw_set_title(doc, title: str) -> bool:
    """Name a new, unsaved document (SolidWorks names a drawing after its model only when it makes the standard
    three views itself)."""
    try:
        return bool(doc.SetTitle2(title))
    except Exception as e:
        logger.debug(f"SetTitle2 failed: {e}")
        return False
