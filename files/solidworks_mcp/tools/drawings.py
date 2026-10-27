"""Drawings: make one, list it, sheets, section and detail views, export to PDF.

Reading lives in core/drawing.py. The SolidWorks calls that change a drawing are the sw_* helpers below.
"""

import logging
import math
import os


from ..core.api import (doc_path, doc_title, doc_type, document, drawing as dwg, Err, ok,
                        tool, ToolError, value)
from ..core.api import sw, units

logger = logging.getLogger(__name__)

# standard view -> (camera direction seen from the model, direction that is up in it)
STANDARD_VIEWS = {"*Front": ((0, 0, 1), (0, 1, 0)), "*Back": ((0, 0, -1), (0, 1, 0)), "*Right": ((1, 0, 0), (0, 1, 0)),
                  "*Left": ((-1, 0, 0), (0, 1, 0)), "*Top": ((0, 1, 0), (0, 0, -1)), "*Bottom": ((0, -1, 0), (0, 0, 1))}
ISO_AXES = ((0.7071, -0.4082), (0.0, 0.8165), (-0.7071, -0.4082))   # where each model axis points (right, up)
SHEET_NAME_BAD = '/\\:*?"<>|'


# ---------------------------------------------------------------- SolidWorks calls


# ---------------------------------------------------------------- helpers

def _axis_vector(given, label: str):
    """(x, y, z) with one component +-1, or a refusal."""
    try:
        v = (float(given["x"]), float(given["y"]), float(given["z"])) if isinstance(given, dict) \
            else tuple(float(c) for c in given)
        if len(v) != 3:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ToolError(f"{label} must be a vector {{x, y, z}}", Err.INPUT)
    length = math.sqrt(sum(c * c for c in v))
    if not math.isfinite(length) or length < 1e-9:
        raise ToolError(f"{label} must not be a zero vector", Err.INPUT)
    v = tuple(c / length for c in v)
    if max(abs(c) for c in v) < 0.9999:
        raise ToolError(f"{label} must point along a model axis for a drawing, e.g. {{x: 0, y: -1, z: 0}} or "
                        f"{{x: 0, y: 0, z: 1}}", Err.INPUT)
    return tuple(int(round(c)) for c in v)


def _cross(a, b):
    return a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]


def _main_view_plan(toward, up):
    """(standard view name, in-plane angle in radians) that shows the model from `toward` with `up` up."""
    for name, (view_toward, view_up) in STANDARD_VIEWS.items():
        if view_toward == toward:
            view_right = _cross(view_up, view_toward)
            along_right = sum(a * b for a, b in zip(up, view_right))
            along_up = sum(a * b for a, b in zip(up, view_up))
            return name, math.pi / 2.0 - math.atan2(along_up, along_right)
    return None, 0.0


def _model_size_m(model):
    try:
        box = [float(v) for v in (model.GetPartBox(True) if doc_type(model) == 1 else model.GetBox(0))]
        return abs(box[3] - box[0]), abs(box[4] - box[1]), abs(box[5] - box[2])
    except Exception as e:
        logger.debug(f"Could not read model size: {e}")
        return None


def _views_from_main(drawing, path: str, plan: dict, factor: float, angle: str, sheet_h: float, margin: float,
                     pad: float, gap: float) -> bool:
    """The main view (a standard view turned in its plane) and two views projected from it: one beside it and
    one below (first angle) or above (third angle)."""
    w, h, d = plan["w"] * factor, plan["h"] * factor, plan["d"] * factor
    main_x = margin + pad + w / 2.0
    if angle == "first":
        main_y = sheet_h - margin - pad - h / 2.0
        other_y = main_y - h / 2.0 - gap - 2 * pad - d / 2.0
    else:
        main_y = margin + pad + h / 2.0
        other_y = main_y + h / 2.0 + gap + 2 * pad + d / 2.0
    side_x = main_x + w / 2.0 + gap + 2 * pad + d / 2.0
    main = sw.drawing.sw_model_view(drawing, path, plan["view"], main_x, main_y)
    if main is None:
        return False
    turn = math.remainder(plan["angle"], 2 * math.pi)
    if abs(turn) > 1e-6:
        main.Angle = turn
    sw.drawing.sw_place_view(main, main_x, main_y)
    for x, y in ((side_x, main_y), (main_x, other_y)):
        sw.drawing.sw_projected_view(drawing, main, x, y)
    sw.common.sw_clear_selection(drawing)
    return True


# set_title_block's table: 100 x 33.2 mm, 10 mm in from the bottom right corner; kept free with 2 mm to spare
TITLE_CORNER = (0.112, 0.0452)                         # from the right edge, from the bottom edge (m)
END_VIEWS = {"front": (0, 0, 1), "back": (0, 0, -1), "right": (1, 0, 0), "left": (-1, 0, 0), "top": (0, 1, 0),
             "bottom": (0, -1, 0)}                     # end_view -> where its camera is, in model axes


def _slots(plan: dict, first_angle: bool, end_view):
    """Where the end view and the third view go: ("right" | "left", "below" | "above"). A view placed beside or
    below the main view shows the model from the side the projection convention says; end_view picks the side."""
    horizontal, vertical = "right", ("below" if first_angle else "above")
    if end_view is None:
        return horizontal, vertical
    camera = END_VIEWS[end_view]
    if camera in (plan["right"], tuple(-c for c in plan["right"])):
        toward_right = camera == plan["right"]
        horizontal = "right" if toward_right != first_angle else "left"
    elif camera in (plan["up"], tuple(-c for c in plan["up"])):
        toward_up = camera == plan["up"]
        vertical = "below" if toward_up == first_angle else "above"
    else:
        raise ToolError(f"end_view {end_view} looks along the main view; give one of the four views around it",
                        Err.INPUT)
    return horizontal, vertical


def _corner_holds_a_view(horizontal: str, vertical: str) -> bool:
    """True when a projected view (not the isometric's free cell) lands in the bottom right corner."""
    return (horizontal == "left" and vertical == "below") or (horizontal == "right" and vertical == "above")


def _grid(plan: dict, factor: float, sheet_w: float, sheet_h: float, horizontal: str, vertical: str, spread: bool,
          margin: float, pad: float, gap: float, sizes=None, bottom=None) -> dict:
    """Centres of the main view, the end view and the third view, and the free cell for the isometric view.
    A view's outline is its model size times the scale plus pad on every side, or (sizes) the measured outlines
    {main: (w, h), end: (w, h), third: (w, h)}. Spread: the gaps between the margins and the views are equal;
    packed: gap wide, from the top left (first angle) corner."""
    if sizes is None:
        w, h, d = (plan[k] * factor + 2 * pad for k in ("w", "h", "d"))
        sizes = {"main": (w, h), "end": (d, h), "third": (w, d)}
    main_w, main_h = sizes["main"]
    end_w = sizes["end"][0]
    third_h = sizes["third"][1]
    col_main = max(main_w, sizes["third"][0])
    row_main = max(main_h, sizes["end"][1])
    cols = [end_w, col_main] if horizontal == "left" else [col_main, end_w]
    rows = [third_h, row_main] if vertical == "above" else [row_main, third_h]       # top to bottom
    bottom = margin if bottom is None else bottom
    room_w, room_h = sheet_w - 2 * margin, sheet_h - margin - bottom
    gx = max(gap, (room_w - sum(cols)) / 3.0) if spread else gap
    gy = max(gap, (room_h - sum(rows)) / 3.0) if spread else gap
    x0 = margin + gx + cols[0] / 2.0
    x1 = x0 + cols[0] / 2.0 + gx + cols[1] / 2.0
    y0 = sheet_h - margin - gy - rows[0] / 2.0
    y1 = y0 - rows[0] / 2.0 - gy - rows[1] / 2.0
    main_col, main_row = (1 if horizontal == "left" else 0), (1 if vertical == "above" else 0)
    xs, ys = (x0, x1), (y0, y1)
    split_x = margin + gx + cols[0] + gx / 2.0
    split_y = sheet_h - margin - gy - rows[0] - gy / 2.0
    free_x = (margin, split_x) if main_col == 1 else (split_x, sheet_w - margin)
    free_y = (split_y, sheet_h - margin) if main_row == 1 else (max(bottom, TITLE_CORNER[1]), split_y)
    return {"main": (xs[main_col], ys[main_row]), "end": (xs[1 - main_col], ys[main_row]),
            "third": (xs[main_col], ys[1 - main_row]), "free": (free_x, free_y)}


def _fits_spread(plan: dict, factor: float, sheet_w: float, sheet_h: float, margin: float, pad: float,
                 gap: float, bottom=None) -> bool:
    # SolidWorks' outlines run a few per cent past the model at large scales: 5 % spare
    w, h, d = (plan[k] * factor * 1.05 + 2 * pad for k in ("w", "h", "d"))
    bottom = margin if bottom is None else bottom
    return w + d + 3 * gap <= sheet_w - 2 * margin and h + d + 3 * gap <= sheet_h - margin - bottom


def _outline(view):
    """(x0, y0, x1, y1) of a view on the sheet, metres."""
    return [float(v) for v in value(view, "GetOutline")]


def _centre_outline(view, x: float, y: float) -> None:
    """Move a view so the centre of its outline is at (x, y). Its Position is not its outline's centre."""
    box = _outline(view)
    px, py = (float(v) for v in value(view, "Position"))
    sw.drawing.sw_place_view(view, px + x - (box[0] + box[2]) / 2.0, py + y - (box[1] + box[3]) / 2.0)


def _settle(drawing, plan, factor, sheet_w, sheet_h, slots, spread, margin, pad, gap) -> dict:
    """Lay the three views out again from their measured outlines (the main view first: the projected views
    follow it along their alignment). Returns the final grid."""
    found = dwg.views(drawing)
    if len(found) < 3:
        return None
    main, end, third = found[0], found[1], found[2]
    sizes = {}
    for key, view in (("main", main), ("end", end), ("third", third)):
        box = _outline(view)
        sizes[key] = (box[2] - box[0], box[3] - box[1])
    bottom = TITLE_CORNER[1] if _corner_holds_a_view(*slots) else margin
    places = _grid(plan, factor, sheet_w, sheet_h, slots[0], slots[1], spread, margin, pad, gap, sizes, bottom)
    for view, key in ((main, "main"), (end, "end"), (third, "third")):
        _centre_outline(view, *places[key])
    return places


def _grid_views(drawing, path: str, plan: dict, places: dict) -> bool:
    """The main view (a standard view turned in its plane) and the two views projected from it."""
    main_x, main_y = places["main"]
    main = sw.drawing.sw_model_view(drawing, path, plan["view"], main_x, main_y)
    if main is None:
        return False
    turn = math.remainder(plan["angle"], 2 * math.pi)
    if abs(turn) > 1e-6:
        main.Angle = turn
    sw.drawing.sw_place_view(main, main_x, main_y)
    for x, y in (places["end"], places["third"]):
        sw.drawing.sw_projected_view(drawing, main, x, y)
    sw.common.sw_clear_selection(drawing)
    return True


# ---------------------------------------------------------------- tools

_AXIS_POINT = {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}},
               "required": ["x", "y", "z"]}
_SHEET_X = {"type": "number", "description": "Centre of the new view on the sheet, from the left edge (in unit, mm by default)"}
_SHEET_Y = {"type": "number", "description": "Centre of the new view on the sheet, from the bottom edge (in unit, mm by default)"}


def _mm(v, unit):
    """A length given in `unit` as mm, the unit the drawing code works in (no unit: mm, whatever set_units says).
    Anything that is not a number is left as it is, for the tool to refuse."""
    if unit is None or v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return v
    return units.to_m(v, unit) * 1000.0


@tool(
    group="drawing",
    description="Create a drawing of a saved part or assembly: front, top and side views, optionally an isometric view and the model's dimensions. The scale is chosen automatically unless given. end_view picks which end of the model the side view shows; spread (on by default) spaces the views evenly over the sheet. Save it with save_document (a .SLDDRW path keeps the drawing) and export it with export_pdf; list_drawing_views shows what is on the sheet. For a model whose 'up' is +Z give front_direction {x:0, y:-1, z:0} and up {x:0, y:0, z:1}: the other views are projected from the main one, the isometric view gets the same 'up', and the automatic scale fits the views on the sheet. The model file is not changed.",
    params={"front_direction": dict(_AXIS_POINT, description="Where the camera of the main view is, seen from the model, along a model axis"),
            "up": dict(_AXIS_POINT, description="The model axis that points up in the main view"),
            "model_path": {"type": "string", "description": "The .SLDPRT / .SLDASM file to draw (default: the active document, which must be saved)"},
            "sheet_size": {"type": "string", "enum": ["A4", "A3", "A2", "A1", "A0"], "default": "A3", "description": "Sheet size, landscape"},
            "projection": {"type": "string", "enum": ["first", "third"], "description": "first = ISO / Europe, third = ANSI / US"},
            "scale": {"type": "string", "description": "e.g. '1:2' or '2:1' (default: automatic)"},
            "isometric": {"type": "boolean", "default": True, "description": "Add an isometric view"},
            "dimensions": {"type": "boolean", "default": False, "description": "Bring the model's dimensions into the views (off by default: dimensions added later with add_drawing_dimension would appear twice)"},
            "end_view": {"type": "string", "enum": list(END_VIEWS),
                         "description": "Which end of the model the projected end view shows (a standard view around the main one, e.g. left or right)"},
            "spread": {"type": "boolean", "default": True,
                       "description": "Spread the views over the sheet with equal gaps and the largest standard scale that fits (false = packed in a corner)"}},
    timeout=300,                    # a big assembly takes a while to load and draw
    required=['projection'],
)
def create_drawing(ctx, projection, front_direction=None, up=None, model_path=None, sheet_size="A3", scale=None, isometric=True, dimensions=False, end_view=None, spread=True):
    app = ctx.app
    size_key = str(sheet_size or "A3").strip().upper()
    if size_key not in dwg.SHEETS:
        raise ToolError(f"sheet_size must be one of: {', '.join(dwg.SHEETS)}", Err.INPUT)
    paper, sheet_w, sheet_h = dwg.SHEETS[size_key]
    angle = str(projection or "first").strip().lower()
    if angle not in ("first", "third"):
        raise ToolError("projection must be first or third", Err.INPUT)
    wanted_scale = dwg.parse_scale(scale)
    if scale is not None and str(scale).strip().lower() not in ("", "auto") and wanted_scale is None:
        raise ToolError(f"Bad scale '{scale}'. Use e.g. 1:2, 2:1 or auto", Err.INPUT)

    if not isinstance(spread, bool):
        raise ToolError("spread must be true or false", Err.INPUT)
    if end_view is not None:
        end_view = str(end_view).strip().lower()
        if end_view not in END_VIEWS:
            raise ToolError(f"end_view must be one of: {', '.join(END_VIEWS)}", Err.INPUT)

    main_plan = None                                       # the main view by direction and up
    if up is not None and front_direction is None:
        raise ToolError("up needs front_direction as well", Err.INPUT)
    if front_direction is not None:
        toward = _axis_vector(front_direction, "front_direction")
        if up is None:
            up = (0, 1, 0) if toward[1] == 0 else (0, 0, 1)
        up_axis = _axis_vector(up, "up")
        if sum(a * b for a, b in zip(toward, up_axis)) != 0:
            raise ToolError("up must be perpendicular to front_direction", Err.INPUT)
        view_name, view_angle = _main_view_plan(toward, up_axis)
        main_plan = {"toward": toward, "up": up_axis, "right": _cross(up_axis, toward), "view": view_name,
                     "angle": view_angle}
    elif spread or end_view is not None:                   # the grid layout: the front view is the main view
        main_plan = {"toward": (0, 0, 1), "up": (0, 1, 0), "right": (1, 0, 0), "view": "*Front", "angle": 0.0}
    slots = _slots(main_plan, angle == "first", end_view) if main_plan is not None else None

    if model_path:                                         # the model to draw
        path = os.path.abspath(str(model_path))
        if not os.path.isfile(path):
            raise ToolError(f"File not found: {path}", Err.NOT_FOUND)
        if os.path.splitext(path)[1].lower() not in (".sldprt", ".sldasm"):
            raise ToolError("model_path must be a part (.SLDPRT) or assembly (.SLDASM) file", Err.INPUT)
        model = None
        try:
            model = app.GetOpenDocumentByName(path)
        except Exception:
            pass
        if model is None:
            if document.load(app, path) is None:
                raise ToolError(f"Failed to open {os.path.basename(path)}", Err.LOAD)
            model = app.GetOpenDocumentByName(path)
    else:
        model = app.ActiveDoc
        if model is None:
            raise ToolError("No document is open. Open or build a part first.", Err.NO_DOCUMENT)
        if doc_type(model) not in (1, 2):
            raise ToolError("The active document is not a part or assembly. Give model_path.", Err.INPUT)
        path = doc_path(model)
        if not path:
            raise ToolError("The part has never been saved. Save it first with save_document(filepath=...): a "
                            "drawing refers to the model's file.", Err.SAVE)
        try:
            if bool(value(model, "GetSaveFlag")):
                saved, _, _ = document.save_in_place(model)
                if not saved:
                    raise ToolError("Could not save the model before drawing it", Err.SAVE)
        except ToolError:
            raise
        except Exception as e:
            logger.debug(f"Save before drawing: {e}")
    model_size = _model_size_m(model) if model is not None else None

    # with a chosen main view the three views share the left 55 % of the sheet, the main view in its top
    # (first angle) or bottom (third angle) left corner, the others projected from it
    margin, pad, gap = 0.012, 0.006, 0.012
    if main_plan is not None:
        size = model_size or (0.1, 0.1, 0.1)

        def extent(axis):
            return max(size[[abs(c) for c in axis].index(1)], 1e-6)

        main_plan["w"], main_plan["h"], main_plan["d"] = (extent(main_plan["right"]), extent(main_plan["up"]),
                                                          extent(main_plan["toward"]))
        room_w, room_h = 0.55 * sheet_w - margin, sheet_h - 2 * margin

        def fits(factor):
            return ((main_plan["w"] + main_plan["d"]) * factor + gap + 4 * pad <= room_w
                    and (main_plan["h"] + main_plan["d"]) * factor + gap + 4 * pad <= room_h)

    # scale: the largest standard scale at which every view fits its cell
    cell_w, cell_h = 0.30 * sheet_w, 0.34 * sheet_h
    if wanted_scale:
        num, den = wanted_scale
    elif main_plan is not None and spread:
        bottom = TITLE_CORNER[1] if _corner_holds_a_view(*slots) else margin
        num, den = next(((a, b) for a, b in dwg.SCALES
                         if _fits_spread(main_plan, a / b, sheet_w, sheet_h, margin, pad, gap, bottom)), dwg.SCALES[-1])
    elif main_plan is not None:
        num, den = next(((a, b) for a, b in dwg.SCALES if fits(a / b)), dwg.SCALES[-1])
    elif model_size:
        x, y, z = model_size
        need_w, need_h = max(x, z, 1e-6), max(y, z, 1e-6)
        num, den = next(((a, b) for a, b in dwg.SCALES if need_w * a / b <= cell_w and need_h * a / b <= cell_h),
                        dwg.SCALES[-1])
    else:
        num, den = 1, 1

    template = document.template(app, "drawing", ctx.setting("exe_path"))
    if not template:
        raise ToolError("No drawing template was found", Err.LOAD)
    drawing = sw.drawing.sw_new_drawing(app, template, paper)
    if drawing is None:
        raise ToolError("Failed to create the drawing document", Err.LOAD)
    sheet = value(drawing, "GetCurrentSheet")
    sw.drawing.sw_set_sheet(sheet, paper, float(num), float(den), angle == "first", sheet_w, sheet_h)

    auto_scale_before = sw.drawing.sw_auto_view_scaling(app, False)
    try:
        places = None
        if main_plan is None:
            created = sw.drawing.sw_standard_views(drawing, path, angle == "first")
        elif spread or end_view is not None:
            places = _grid(main_plan, float(num) / float(den), sheet_w, sheet_h, slots[0], slots[1], spread, margin,
                           pad, gap, bottom=TITLE_CORNER[1] if _corner_holds_a_view(*slots) else margin)
            created = _grid_views(drawing, path, main_plan, places)
            if created:
                places = _settle(drawing, main_plan, float(num) / float(den), sheet_w, sheet_h, slots, spread,
                                 margin, pad, gap) or places
        else:
            created = _views_from_main(drawing, path, main_plan, float(num) / float(den), angle, sheet_h, margin, pad, gap)
    finally:
        if auto_scale_before is not None:
            sw.drawing.sw_auto_view_scaling(app, auto_scale_before)
    if not created or not dwg.views(drawing):
        sw.drawing.sw_close(app, doc_title(drawing))
        raise ToolError(f"SolidWorks could not create views of {os.path.basename(path)}", Err.FEATURE)
    try:                                                    # the sheet really has the scale that is reported
        props = [float(v) for v in value(sheet, "GetProperties2")]
        if abs(props[2] / props[3] - float(num) / float(den)) > 1e-9:
            sw.drawing.sw_set_scale(sheet, float(num), float(den))
    except Exception as e:
        logger.debug(f"Could not verify the sheet scale: {e}")

    iso_note = ""
    if isometric:                                          # in the corner the three views leave free
        try:
            if places is not None:
                (x_lo, x_hi), (y_lo, y_hi) = places["free"]
            else:
                margin = 0.010
                x_lo, x_hi = 0.56 * sheet_w, sheet_w - margin
                y_lo, y_hi = (margin, 0.50 * sheet_h) if angle == "first" else (0.50 * sheet_h, sheet_h - margin)
            iso_x, iso_y = (x_lo + x_hi) / 2.0, (y_lo + y_hi) / 2.0
            iso = sw.drawing.sw_model_view(drawing, path, "*Isometric", iso_x, iso_y)
            if iso is not None and main_plan is not None:  # turned in its plane so the chosen "up" is up
                axis = [abs(c) for c in main_plan["up"]].index(1)
                sign = main_plan["up"][axis]
                on_screen = (ISO_AXES[axis][0] * sign, ISO_AXES[axis][1] * sign)
                turn = math.pi / 2.0 - math.atan2(on_screen[1], on_screen[0])
                if abs(math.remainder(turn, 2 * math.pi)) > 1e-6:
                    iso.Angle = math.remainder(turn, 2 * math.pi)
            if iso is not None:                            # its own smaller scale when it does not fit its corner
                outline = [float(v) for v in value(iso, "GetOutline")]
                width, height = outline[2] - outline[0], outline[3] - outline[1]
                room_w, room_h = x_hi - x_lo, y_hi - y_lo
                if width > room_w or height > room_h:
                    wanted = float(num) / float(den) * min(room_w / width, room_h / height) * 0.95
                    fitted = next((a / b for a, b in dwg.SCALES if a / b <= wanted), wanted)
                    iso.UseSheetScale = False
                    iso.ScaleDecimal = fitted
                    # the outline read just after creating the view can be too small: measure again and step
                    # down the standard scales until it really fits
                    for smaller in [a / b for a, b in dwg.SCALES if a / b < fitted]:
                        outline = [float(v) for v in value(iso, "GetOutline")]
                        if outline[2] - outline[0] <= room_w and outline[3] - outline[1] <= room_h:
                            break
                        fitted = smaller
                        iso.ScaleDecimal = fitted
                    iso_note = f"; the isometric view is at {dwg.scale_text(fitted)} to fit"
                if places is not None:
                    _centre_outline(iso, iso_x, iso_y)
                else:
                    sw.drawing.sw_place_view(iso, iso_x, iso_y)
        except Exception as e:
            logger.warning(f"Isometric view failed: {e}")

    if str(doc_title(drawing)).lower().startswith("draw"):     # named after the model, as SolidWorks' own 3 views do
        sw.drawing.sw_set_title(drawing, os.path.splitext(os.path.basename(path))[0])
    if dimensions:
        sw.drawing.sw_refresh_views(drawing)
    imported = sw.drawing.sw_import_dimensions(drawing) if dimensions else 0
    sw.common.sw_rebuild(drawing)
    sw.common.sw_clear_selection(drawing)

    rows = []
    for view in dwg.views(drawing)[:50]:
        row = {"name": view.Name}
        try:
            outline = [float(v) for v in value(view, "GetOutline")]
            row["on_sheet_mm"] = [round(v * 1000, 1) for v in outline]
            row["inside_sheet"] = outline[0] >= 0 and outline[1] >= 0 and outline[2] <= sheet_w and outline[3] <= sheet_h
        except Exception:
            pass
        rows.append(row)
    title = doc_title(drawing)
    scale_text = f"{num:g}:{den:g}"
    data = {"drawing": title, "model": path, "sheet_size": size_key, "projection": angle, "scale": scale_text,
            "views": rows, "dimensions_imported": imported, "template": document.template_info(drawing, template)}
    data["layout"] = "spread" if spread and main_plan is not None else "packed"
    if slots is not None:
        data["end_view_side"], data["third_view_side"] = slots
        if end_view is not None:
            data["end_view"] = end_view
    if main_plan is not None:
        data["main_view"] = {"front_direction": list(main_plan["toward"]), "up": list(main_plan["up"]),
                             "from_standard_view": main_plan["view"],
                             "turned_deg": round(math.degrees(math.remainder(main_plan["angle"], 2 * math.pi)), 1) + 0.0}
    message = (f"Created drawing {title}: {len(rows)} views of {os.path.basename(path)} on {size_key}, scale "
               f"{scale_text}, {angle} angle{iso_note}")
    if dimensions:
        message += f", {imported} dimensions imported"
    outside = [v["name"] for v in rows if v.get("inside_sheet") is False]
    if outside:
        message += f". WARNING: {', '.join(outside)} extend(s) past the sheet - use a smaller scale"
    return ok(message + ". Save it with save_document (.SLDDRW to keep it, .pdf to export).", data)


@tool(
    group="drawing",
    description="List the views of the active drawing: name, type, scale, place on the sheet and number of dimensions. Given a view name it also lists that view's visible edges (E1, E2, ...), points (P1, P2, ...) and dimensions. Those ids are what add_drawing_dimension takes, and the coordinates are what add_section_view and add_detail_view take. The coordinates are VIEW COORDINATES: mm on the model, x to the right and y up on the sheet, measured from where the model origin appears in that view, independent of the view's scale and position. It also lists the sheets (name, size, views, dimensions, which is active). Changes nothing.",
    params={"view": {"type": "string", "description": "Name of a view, e.g. 'Drawing View1' (default: list all views)"}},
    needs="drawing",
)
def list_drawing_views(ctx, view=None):
    drawing = ctx.doc
    width, height, size_name = dwg.sheet_size(drawing)
    data = {"drawing": doc_title(drawing), "sheet_size": size_name, "sheet_mm": [round(width * 1000, 1), round(height * 1000, 1)]}
    try:
        active = dwg.current_sheet_name(drawing)
        data["active_sheet"] = active
        data["sheets"] = [dwg.sheet_row(drawing, n, active) for n in dwg.sheet_names(drawing)]
    except Exception as e:
        logger.debug(f"Sheet list: {e}")
    if view is None or (isinstance(view, str) and not view.strip()):
        rows = [dwg.view_row(v, width, height) for v in dwg.views(drawing)]
        data["views"] = rows
        return ok(f"{len(rows)} views: {', '.join(r['name'] for r in rows)}", data)
    target = dwg.find_view(drawing, view, "list_drawing_views")
    edges, points = dwg.view_entities(target)
    dims = dwg.dimension_rows(target, include_unreadable=True)
    for d in dims:                                         # attached to nothing (shown in red): 3.8
        if "unreadable" in d:
            d.pop("_obj", None)
            continue
        try:
            d["dangling"] = bool(value(value(d["_obj"], "GetAnnotation"), "IsDangling"))
        except Exception:
            d["dangling"] = False
        d.pop("_obj", None)
    data["view"] = dwg.view_row(target, width, height)
    data["view"]["model_origin_on_sheet_mm"] = dwg.mm(dwg.view_frame(target)["origin"])
    data["edges"] = [dwg.edge_public(e) for e in edges]
    data["points"] = [{"id": p["id"], "at": dwg.mm(p["at"])} for p in points]
    data["dimensions"] = dims
    data["dangling_dimensions"] = [d["name"] for d in dims if d["dangling"]]
    data["coordinates"] = ("edges and points are in view coordinates: mm on the model, right and up on the sheet "
                           "from the model origin as seen in this view")
    dangling = f", {len(data['dangling_dimensions'])} dangling ({', '.join(data['dangling_dimensions'])})"         if data["dangling_dimensions"] else ""
    return ok(f"{target.Name}: {len(edges)} visible edges, {len(points)} points, {len(dims)} dimensions{dangling}", data)


@tool(
    group="drawing",
    description="Add a sheet to the active drawing, after the last one, and make it active (activate: false keeps the current sheet active). A blank sheet has the size of the current sheet unless sheet_size is given, and has no views or title block. With copy_from {drawing, sheet} the sheet is copied, with its views, dimensions and annotations, from that sheet of an open drawing (drawing defaults to this one; open another drawing first with open_document). The copy keeps its source's size, so sheet_size cannot be combined with copy_from. After a copy the result says how many views and dimensions came across and lists every dangling dimension (not attached to the model) in dangling_dimensions. A name that is empty, already used, or contains any of / \\ : * ? \" < > | is refused and nothing changes. list_drawing_views shows the sheets; activate_sheet switches between them.",
    params={"name": {"type": "string", "description": "Name of the new sheet (not used yet; at most 50 characters)"},
            "sheet_size": {"type": "string", "enum": ["A4", "A3", "A2", "A1", "A0"], "description": "Size of a blank sheet, landscape (default: the size of the current sheet)"},
            "copy_from": {"type": "object", "description": "Copy a sheet instead of adding a blank one",
                          "properties": {"drawing": {"type": "string", "description": "Name or path of an open drawing (default: this drawing)"},
                                         "sheet": {"type": "string", "description": "Name of the sheet to copy"}},
                          "required": ["sheet"]},
            "activate": {"type": "boolean", "default": True, "description": "Make the new sheet the active sheet"}},
    required=["name"],
    needs="drawing",
)
def add_sheet(ctx, name, sheet_size=None, copy_from=None, activate=True):
    drawing, app = ctx.doc, ctx.app
    sheets_before = dwg.sheet_names(drawing)
    active_before = dwg.current_sheet_name(drawing)
    if not isinstance(name, str) or not name.strip():
        raise ToolError(f"add_sheet needs the name of the new sheet. Sheets now: {', '.join(sheets_before)}", Err.INPUT)
    name = name.strip()
    if len(name) > 50 or any(c in name for c in SHEET_NAME_BAD):
        raise ToolError(f"'{name}' is not a usable sheet name (at most 50 characters, none of / \\ : * ? \" < > |). "
                        f"Nothing was changed.", Err.INPUT)
    if name.lower() in [n.lower() for n in sheets_before]:
        raise ToolError(f"A sheet named '{name}' already exists. Sheets: {', '.join(sheets_before)}. Nothing was "
                        f"changed.", Err.INPUT)
    if not isinstance(activate, bool):
        raise ToolError("activate must be true or false", Err.INPUT)
    size_key = None
    if sheet_size is not None:
        size_key = str(sheet_size).strip().upper() if isinstance(sheet_size, str) else None
        if size_key not in dwg.SHEETS:
            raise ToolError(f"sheet_size must be one of {', '.join(dwg.SHEETS)} (landscape). Nothing was changed.",
                            Err.INPUT)
    source = source_sheet = None
    if copy_from is not None:
        if not isinstance(copy_from, dict):
            raise ToolError("copy_from must be {drawing, sheet}", Err.INPUT)
        extra = sorted(set(copy_from) - {"drawing", "sheet"})
        if extra:
            raise ToolError(f"copy_from has no input '{extra[0]}'. It takes drawing and sheet.", Err.INPUT)
        if size_key is not None:
            raise ToolError("sheet_size cannot be combined with copy_from: a copied sheet keeps the size of its "
                            "source. Nothing was changed.", Err.INPUT)
        wanted = copy_from.get("sheet")
        if not isinstance(wanted, str) or not wanted.strip():
            raise ToolError("copy_from needs the name of the sheet to copy: {sheet: '...'}", Err.INPUT)
        ref = copy_from.get("drawing")
        if ref is None:
            source = drawing
        else:
            if not isinstance(ref, str) or not ref.strip():
                raise ToolError("copy_from.drawing must be the name or path of an open drawing", Err.INPUT)
            key = ref.strip().lower()

            def stem(t):
                return os.path.splitext(os.path.basename(t))[0].lower()

            candidates = []
            for doc in dwg.open_drawings(app):
                path = str(doc_path(doc) or "")
                title = str(doc_title(doc)).lower()
                if (path and os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(ref.strip()))) \
                        or (path and stem(path) == stem(key)) or title == key or title.startswith(key + " - "):
                    candidates.append(doc)
            if not candidates:
                names = [str(doc_title(d)) for d in dwg.open_drawings(app)]
                raise ToolError(f"No open drawing '{ref.strip()}'. Open drawings: {', '.join(names) or 'none'}. Open "
                                f"it first with open_document. Nothing was changed.", Err.INPUT)
            if len(candidates) > 1:
                raise ToolError(f"'{ref.strip()}' matches {len(candidates)} open drawings; give the full path. Nothing "
                                f"was changed.", Err.INPUT)
            source = candidates[0]
        source_names = dwg.sheet_names(source)
        source_sheet = next((n for n in source_names if n.lower() == wanted.strip().lower()), None)
        if source_sheet is None:
            raise ToolError(f"The drawing has no sheet '{wanted.strip()}'. Sheets: {', '.join(source_names)}. Nothing "
                            f"was changed.", Err.INPUT)

    copied_from = source_row = source_dangling = None
    if source is None:                                     # a blank sheet
        props = [float(v) for v in value(value(drawing, "GetCurrentSheet"), "GetProperties2")]
        paper, width, height = dwg.SHEETS[size_key] if size_key is not None else (int(props[0]), props[5], props[6])
        made = sw.drawing.sw_new_sheet(drawing, name, paper, props[2], props[3], bool(props[4]), width, height)
        if not made or name not in dwg.sheet_names(drawing):
            sw.drawing.sw_remove_sheet(drawing, name)
            sw.drawing.sw_activate_sheet(drawing, active_before)
            raise ToolError(f"SolidWorks did not add the sheet '{name}'. Nothing was changed.", Err.INTERNAL)
    else:                                                  # a copy, pasted after the last sheet here
        same = dwg.same_doc(source, drawing)
        source_active = dwg.current_sheet_name(source)
        source_row = dwg.sheet_row(source, source_sheet, source_active)
        source_dangling = dwg.dangling_dimensions(source, source_sheet)
        if not same and not sw.drawing.sw_activate_doc(app, source):
            raise ToolError("Could not bring the source drawing to the front. Nothing was changed.", Err.INTERNAL)
        sw.drawing.sw_activate_sheet(source, source_sheet)
        picked = sw.drawing.sw_copy_sheet(source, source_sheet)
        if not same:
            sw.drawing.sw_activate_sheet(source, source_active)
            sw.drawing.sw_activate_doc(app, drawing)
        if not picked:
            sw.drawing.sw_activate_sheet(drawing, active_before)
            raise ToolError(f"SolidWorks could not select the sheet '{source_sheet}'. Nothing was changed.", Err.SELECTION)
        active_now = app.ActiveDoc
        if active_now is None or not dwg.same_doc(active_now, drawing):
            raise ToolError("Could not bring this drawing back to the front. Nothing was pasted.", Err.INTERNAL)
        sw.drawing.sw_activate_sheet(drawing, sheets_before[-1])
        pasted = sw.drawing.sw_paste_sheet(drawing)
        new = [n for n in dwg.sheet_names(drawing) if n not in sheets_before]
        if not pasted or len(new) != 1:
            for n in new:
                sw.drawing.sw_remove_sheet(drawing, n)
            sw.drawing.sw_activate_sheet(drawing, active_before)
            raise ToolError("SolidWorks did not paste the sheet. Nothing was changed.", Err.INTERNAL)
        sw.drawing.sw_rename_sheet(drawing, new[0], name)
        if name not in dwg.sheet_names(drawing):
            sw.drawing.sw_remove_sheet(drawing, new[0])
            sw.drawing.sw_activate_sheet(drawing, active_before)
            raise ToolError(f"SolidWorks did not accept the name '{name}'. Nothing was changed.", Err.INTERNAL)
        copied_from = {"drawing": doc_title(source) if not same else doc_title(drawing), "sheet": source_sheet}
        sw.common.sw_rebuild(drawing)

    sw.common.sw_clear_selection(drawing)
    sw.drawing.sw_activate_sheet(drawing, name if activate else active_before)
    active = dwg.current_sheet_name(drawing)
    rows = [dwg.sheet_row(drawing, n, active) for n in dwg.sheet_names(drawing)]
    row = next(r for r in rows if r["name"] == name)
    data = {"sheet": row, "sheets": rows, "active_sheet": active}
    message = (f"Added sheet '{name}' ({row.get('size', '?')}, {row['views']} views, {row['dimensions']} dimensions) "
               f"after '{sheets_before[-1]}'. "
               f"{'It is the active sheet.' if active == name else 'The active sheet is still ' + active + '.'}")
    if source is not None:
        dangling = dwg.dangling_dimensions(drawing, name)
        data.update({"copied_from": copied_from, "views_in_source": source_row["views"],
                     "dimensions_in_source": source_row["dimensions"], "dangling_dimensions": dangling,
                     "dangling_in_source": source_dangling,
                     "matches_source": row["views"] == source_row["views"] and row["dimensions"] == source_row["dimensions"]})
        message += (f" Copied from '{source_sheet}': {row['views']} of {source_row['views']} views, "
                    f"{row['dimensions']} of {source_row['dimensions']} dimensions.")
        if dangling:
            message += (f" WARNING: {len(dangling)} dangling dimension(s) (not attached): "
                        + ", ".join(f"{d['dimension']} in {d['view']} = {d['value']}" for d in dangling)
                        + (f" ({len(source_dangling)} already dangling in the source sheet)" if source_dangling else "") + ".")
        else:
            message += " Dangling dimensions: none."
        if not data["matches_source"]:
            message += " WARNING: the copy does not have the same number of views and dimensions as its source."
    return ok(message, data)


@tool(
    group="drawing",
    description="Make a sheet of the active drawing the active one, which is the sheet the other drawing tools (set_title_block, add_section_view, add_drawing_dimension and the rest) work on. Returns the sheet's size and views. An unknown name is refused and the active sheet stays as it was.",
    params={"name": {"type": "string", "description": "Name of the sheet (see list_drawing_views)"}},
    required=["name"],
    needs="drawing",
)
def activate_sheet(ctx, name):
    drawing = ctx.doc
    names = dwg.sheet_names(drawing)
    if not isinstance(name, str) or not name.strip():
        raise ToolError(f"activate_sheet needs the name of a sheet. Sheets: {', '.join(names)}", Err.INPUT)
    wanted = name.strip()
    match = next((n for n in names if n == wanted), None) or next((n for n in names if n.lower() == wanted.lower()), None)
    if match is None:
        raise ToolError(f"No sheet named '{wanted}'. Sheets: {', '.join(names)}", Err.INPUT)
    before = dwg.current_sheet_name(drawing)
    if match != before:
        sw.drawing.sw_activate_sheet(drawing, match)
        sw.common.sw_clear_selection(drawing)
        if dwg.current_sheet_name(drawing) != match:
            sw.drawing.sw_activate_sheet(drawing, before)
            raise ToolError(f"SolidWorks did not switch to the sheet '{match}'.", Err.INTERNAL)
    rows = [dwg.sheet_row(drawing, n, match) for n in names]
    row = next(r for r in rows if r["name"] == match)
    view_names = [str(v.Name) for v in dwg.views(drawing)]
    return ok(f"'{match}' is the active sheet ({row.get('size', '?')}, {len(view_names)} views)"
              + ("" if match != before else " - it already was") + ".",
              {"sheet": row, "previous": before, "views": view_names, "sheets": rows, "active_sheet": match})


def _into_free_space(drawing, new_view, along: str, data: dict) -> None:
    """A section or detail view placed by default that leaves the sheet, overlaps a view or the title-block corner
    goes to the nearest free place: first along its alignment (`along` "x" / "y", None for a free view), else
    anywhere, breaking the alignment. Nothing fits: it stays, and the warnings say so (data["free_space"])."""
    sheet_w, sheet_h, _ = dwg.sheet_size(drawing)
    name = str(new_view.Name)
    sw.drawing.sw_refresh_views(drawing)
    box = [float(v) for v in value(new_view, "GetOutline")]
    others = dwg.view_boxes(drawing, skip=name)
    corner = (sheet_w - TITLE_CORNER[0], 0.0, sheet_w, TITLE_CORNER[1])
    inside = box[0] >= 0 and box[1] >= 0 and box[2] <= sheet_w and box[3] <= sheet_h
    if inside and not dwg.overlaps(box, others) and not dwg.overlaps(box, [("corner", corner)]):
        return
    size, centre = (box[2] - box[0], box[3] - box[1]), ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
    spot, broken = None, False
    if along:
        spot = dwg.free_spot(size, others, sheet_w, sheet_h, centre, keep_free=[corner], along=along)
    if spot is None:
        spot = dwg.free_spot(size, others, sheet_w, sheet_h, centre, keep_free=[corner])
        broken = spot is not None and along is not None
    if spot is None:
        data["free_space"] = False
        return
    if broken:
        sw.drawing.sw_remove_alignment(new_view)
    position = [float(v) for v in value(new_view, "Position")]
    sw.drawing.sw_place_view(new_view, position[0] + spot[0] - centre[0], position[1] + spot[1] - centre[1])
    sw.common.sw_redraw(drawing)
    sw.drawing.sw_refresh_views(drawing)
    data["moved_to_free_space"] = True
    if broken:
        data["alignment"] = "broken"


def _placed_warnings(drawing, new_view, row: dict, data: dict, message: str) -> str:
    if data.get("moved_to_free_space"):
        message += (" (moved into free space on the sheet" + ("; its alignment to the parent view is broken"
                                                             if data.get("alignment") == "broken" else "") + ")")
    if data.get("free_space") is False:
        message += ". WARNING: no free place on the sheet fits it"
    if row.get("inside_sheet") is False:
        message += ". WARNING: it extends past the sheet - give x_sheet / y_sheet"
    hits = dwg.overlaps([v / 1000.0 for v in row.get("on_sheet_mm", [0, 0, 0, 0])],
                        dwg.view_boxes(drawing, skip=str(new_view.Name)))
    if hits:
        data["overlaps"] = hits
        message += f". WARNING: it overlaps {', '.join(hits)} - give x_sheet / y_sheet"
    return message


@tool(
    group="drawing",
    description="Add a section view to the active drawing by cutting an existing view along a straight line. (x1, y1) and (x2, y2) are two points on the cutting line in view coordinates (mm on the model, from the model origin as seen in that view; see list_drawing_views); the cut runs the whole line, right across the view. A horizontal line puts the section below the view, a vertical one beside it, unless x_sheet / y_sheet (sheet mm) say otherwise; a default place that is taken or off the sheet moves to the nearest free place (along the alignment first, else anywhere, breaking the alignment). Returns the new view's name and label.",
    params={"view": {"type": "string", "description": "Name of the view to cut"},
            "x1": {"type": "number", "description": "First point on the cutting line, x (view coordinates, in unit, mm by default)"},
            "y1": {"type": "number", "description": "First point on the cutting line, y"},
            "x2": {"type": "number", "description": "Second point on the cutting line, x"},
            "y2": {"type": "number", "description": "Second point on the cutting line, y"},
            "label": {"type": "string", "description": "Section letter (default: next free letter)"},
            "x_sheet": _SHEET_X, "y_sheet": _SHEET_Y,
            'unit': {'type': 'string', 'description': 'Unit of the length inputs: mm (the default, whatever set_units says), cm, m, inch or ft. Results are in mm'}},
    required=["view", "x1", "y1", "x2", "y2"],
    needs="drawing",
)
def add_section_view(ctx, view, x1, y1, x2, y2, label=None, x_sheet=None, y_sheet=None, unit=None):
    x1 = _mm(x1, unit)
    y1 = _mm(y1, unit)
    x2 = _mm(x2, unit)
    y2 = _mm(y2, unit)
    x_sheet = _mm(x_sheet, unit)
    y_sheet = _mm(y_sheet, unit)
    drawing = ctx.doc
    parent = dwg.find_view(drawing, view, "add_section_view")
    coords = []
    for given, label_name in ((x1, "x1"), (y1, "y1"), (x2, "x2"), (y2, "y2")):
        try:
            coords.append(dwg.number(given, label_name, -1e6, 1e6) / 1000.0)
        except ToolError as e:
            raise ToolError(f"{e.message} (the cutting line, in view coordinates, mm)", Err.INPUT)
    ax, ay, bx, by = coords
    if math.hypot(bx - ax, by - ay) < 1e-5:
        raise ToolError("The cutting line has no length.", Err.INPUT)
    text = dwg.pick_label(drawing, label)

    frame = dwg.view_frame(parent)
    outline = [float(v) for v in value(parent, "GetOutline")]
    sa, sb = dwg.view_to_sheet(frame, ax, ay), dwg.view_to_sheet(frame, bx, by)
    grow = 0.004                                           # the cut runs right across the view
    box = (outline[0] - grow, outline[1] - grow, outline[2] + grow, outline[3] + grow)
    dx, dy = sb[0] - sa[0], sb[1] - sa[1]
    t_lo, t_hi = -1e9, 1e9
    for start, delta, low, high in ((sa[0], dx, box[0], box[2]), (sa[1], dy, box[1], box[3])):
        if abs(delta) < 1e-12:
            if start < low or start > high:
                t_lo, t_hi = 1.0, 0.0
        else:
            t1, t2 = (low - start) / delta, (high - start) / delta
            t_lo, t_hi = max(t_lo, min(t1, t2)), min(t_hi, max(t1, t2))
    if t_lo >= t_hi:
        raise ToolError(f"The cutting line does not cross {parent.Name}. Use view coordinates (mm on the model from "
                        f"the model origin) - list_drawing_views(view=...) shows the edges.", Err.INPUT)
    ax, ay, bx, by = ax + (bx - ax) * t_lo, ay + (by - ay) * t_lo, ax + (bx - ax) * t_hi, ay + (by - ay) * t_hi
    sa, sb = dwg.view_to_sheet(frame, ax, ay), dwg.view_to_sheet(frame, bx, by)
    horizontal = abs(sb[0] - sa[0]) >= abs(sb[1] - sa[1])  # default place: beside the parent, across the cut
    w, h = outline[2] - outline[0], outline[3] - outline[1]
    cx, cy = (outline[0] + outline[2]) / 2.0, (outline[1] + outline[3]) / 2.0
    sheet_w, sheet_h, _ = dwg.sheet_size(drawing)
    if horizontal:
        default = (cx, outline[1] - 0.02 - h / 2.0)
        if default[1] - h / 2.0 < 0:
            default = (cx, outline[3] + 0.02 + h / 2.0)
    else:
        default = (outline[2] + 0.02 + w / 2.0, cy)
        if default[0] + w / 2.0 > sheet_w:
            default = (outline[0] - 0.02 - w / 2.0, cy)
    place = dwg.sheet_place(drawing, x_sheet, y_sheet, default)

    before = {str(v.Name) for v in dwg.views(drawing)}
    sw.common.sw_clear_selection(drawing)
    if not sw.drawing.sw_activate_view(drawing, str(parent.Name)):
        raise ToolError(f"Could not activate {parent.Name}.", Err.INTERNAL)
    line = sw.drawing.sw_sketch_line(drawing, dwg.view_to_sketch(frame, ax, ay), dwg.view_to_sketch(frame, bx, by))
    created = None
    if line is not None:
        sw.common.sw_clear_selection(drawing)
        if sw.drawing.sw_select_segment(line):
            created = sw.drawing.sw_section_view(drawing, place[0], place[1], text)
    new_views = [v for v in dwg.views(drawing) if str(v.Name) not in before]
    if created is None or not new_views:
        if line is not None:
            sw.drawing.sw_delete_segment(drawing, line)
        sw.drawing.sw_finish(drawing)
        raise ToolError("SolidWorks could not make the section view. The cutting line must pass through the model in "
                        "that view.", Err.FEATURE)
    sw.drawing.sw_finish(drawing)
    section = new_views[-1]
    sw.drawing.sw_refresh_views(drawing)
    placing = {}
    if x_sheet is None:
        _into_free_space(drawing, section, "x" if horizontal else "y", placing)
    row = dwg.view_row(section, sheet_w, sheet_h)
    data = {"view": row, "label": row.get("label", text), "from_view": str(parent.Name),
            "cutting_line": {"start": dwg.mm((ax, ay)), "end": dwg.mm((bx, by))}, **placing}
    message = f"Added {section.Name} (section {data['label']}-{data['label']}) cut from {parent.Name}"
    return ok(_placed_warnings(drawing, section, row, data, message), data)


def _detail_circle(parent, detail_name: str):
    """((x, y) centre on the sheet, radius on the sheet) in metres of the detail circle on `parent` that makes the
    view `detail_name`, from GetDetailCircleInfo: per circle the centre, then a point on the circle. None if unread."""
    try:
        info = [float(v) for v in value(parent, "GetDetailCircleInfo")]
        count = int(info[0])
        if count < 1:
            return None
        stride = (len(info) - 1) // count
        index = count - 1
        try:
            circles = list(value(parent, "GetDetailCircles") or [])
            names = [str(value(value(c, "GetDetailView"), "Name")) for c in circles]
            if detail_name in names:
                index = names.index(detail_name)
        except Exception:
            pass
        block = info[1 + index * stride:1 + (index + 1) * stride]
        centre, edge = block[0:2], block[3:5]
        return (centre[0], centre[1]), math.dist(centre, edge)
    except Exception as e:
        logger.debug(f"Detail circle info: {e}")
        return None


@tool(
    group="drawing",
    description="Add a detail view to the active drawing: a circle on an existing view, shown enlarged at its own scale. The circle's centre (x, y) and radius are in view coordinates (mm on the model; see list_drawing_views). scale is the detail's own scale, e.g. '2:1' or '5:1' (default: twice the scale of the source view). Without x_sheet / y_sheet it goes beside the view, or to the nearest free place on the sheet. Returns the new view's name, label and scale.",
    params={"view": {"type": "string", "description": "Name of the view to take the detail from"},
            "x": {"type": "number", "description": "Circle centre x (view coordinates, in unit, mm by default)"},
            "y": {"type": "number", "description": "Circle centre y (view coordinates, in unit, mm by default)"},
            "radius": {"type": "number", "description": "Circle radius on the model (in unit, mm by default)"},
            "scale": {"type": "string", "description": "Scale of the detail view, e.g. '2:1' (default: twice the view's scale)"},
            "label": {"type": "string", "description": "Detail letter (default: next free letter)"},
            "x_sheet": _SHEET_X, "y_sheet": _SHEET_Y,
            'unit': {'type': 'string', 'description': 'Unit of the length inputs: mm (the default, whatever set_units says), cm, m, inch or ft. Results are in mm'}},
    required=["view", "x", "y", "radius"],
    needs="drawing",
)
def add_detail_view(ctx, view, x, y, radius, scale=None, label=None, x_sheet=None, y_sheet=None, unit=None):
    x = _mm(x, unit)
    y = _mm(y, unit)
    radius = _mm(radius, unit)
    x_sheet = _mm(x_sheet, unit)
    y_sheet = _mm(y_sheet, unit)
    drawing = ctx.doc
    parent = dwg.find_view(drawing, view, "add_detail_view")
    try:
        cx, cy = dwg.number(x, "x", -1e6, 1e6), dwg.number(y, "y", -1e6, 1e6)
        rad = dwg.number(radius, "radius", 0, 1e6)
        if rad <= 0:
            raise ToolError("radius must be larger than 0", Err.INPUT)
    except ToolError as e:
        raise ToolError(f"{e.message} (the detail circle, in view coordinates, mm)", Err.INPUT)
    cx, cy, rad = cx / 1000.0, cy / 1000.0, rad / 1000.0
    frame = dwg.view_frame(parent)
    if scale is None or str(scale).strip().lower() in ("", "auto"):
        factor = frame["scale"] * 2.0
    else:
        parsed = dwg.parse_scale(scale)
        if parsed is None:
            raise ToolError(f"Bad scale '{scale}'. Use e.g. 2:1 or 5:1", Err.INPUT)
        factor = parsed[0] / parsed[1]
    if not 0.001 <= factor <= 1000:
        raise ToolError("scale must be between 1:1000 and 1000:1", Err.INPUT)
    text = dwg.pick_label(drawing, label)
    outline = [float(v) for v in value(parent, "GetOutline")]
    centre = dwg.view_to_sheet(frame, cx, cy)
    reach = rad * frame["scale"]
    if (centre[0] + reach < outline[0] or centre[0] - reach > outline[2]
            or centre[1] + reach < outline[1] or centre[1] - reach > outline[3]):
        raise ToolError(f"The detail circle does not touch {parent.Name}. Use view coordinates (mm on the model from "
                        f"the model origin) - list_drawing_views(view=...) shows the edges.", Err.INPUT)
    sheet_w, sheet_h, _ = dwg.sheet_size(drawing)
    size = rad * factor
    default = (outline[2] + 0.02 + size, min(max(centre[1], size + 0.01), sheet_h - size - 0.01))
    if default[0] + size > sheet_w:
        default = (min(max(centre[0], size + 0.01), sheet_w - size - 0.01), outline[1] - 0.02 - size)
    if default[1] - size < 0:
        default = (sheet_w / 2.0, sheet_h / 2.0)
    place = dwg.sheet_place(drawing, x_sheet, y_sheet, default)

    before = {str(v.Name) for v in dwg.views(drawing)}
    sw.common.sw_clear_selection(drawing)
    if not sw.drawing.sw_activate_view(drawing, str(parent.Name)):
        raise ToolError(f"Could not activate {parent.Name}.", Err.INTERNAL)
    circle = sw.drawing.sw_sketch_circle(drawing, dwg.view_to_sketch(frame, cx, cy), rad)
    created = None
    if circle is not None:
        sw.common.sw_clear_selection(drawing)
        if sw.drawing.sw_select_segment(circle):
            created = sw.drawing.sw_detail_view(drawing, place[0], place[1], factor, text)
    new_views = [v for v in dwg.views(drawing) if str(v.Name) not in before]
    if created is None or not new_views:
        if circle is not None:
            sw.drawing.sw_delete_segment(drawing, circle)
        sw.drawing.sw_finish(drawing)
        raise ToolError("SolidWorks could not make the detail view.", Err.FEATURE)
    detail = new_views[-1]
    try:
        if abs(float(detail.ScaleDecimal) - factor) > 1e-9:
            detail.ScaleDecimal = factor
    except Exception as e:
        logger.debug(f"Detail scale: {e}")
    sw.drawing.sw_refresh_views(drawing)
    try:                                                   # the circle's centre exactly at the place (3.15)
        seen = dwg.view_to_sheet(dwg.view_frame(detail), cx, cy)
        px, py = (float(v) for v in value(detail, "Position"))
        sw.drawing.sw_place_view(detail, px + place[0] - seen[0], py + place[1] - seen[1])
    except Exception as e:
        logger.debug(f"Detail placement: {e}")
    sw.drawing.sw_finish(drawing)
    sw.drawing.sw_refresh_views(drawing)
    placing = {}
    if x_sheet is None:
        _into_free_space(drawing, detail, None, placing)
    row = dwg.view_row(detail, sheet_w, sheet_h)
    data = {"view": row, "label": row.get("label", text), "from_view": str(parent.Name), "scale": row.get("scale"),
            "circle": {"center": dwg.mm((cx, cy)), "radius": round(rad * 1000, 3)}, **placing}
    read = _detail_circle(parent, str(detail.Name))
    if read is not None:                                   # the circle as SolidWorks has it, in view coordinates
        frame = dwg.view_frame(parent)
        (sx, sy), r_sheet = read
        data["circle"] = {"x": round((sx - frame["origin"][0]) / frame["scale"] * 1000, 3),
                          "y": round((sy - frame["origin"][1]) / frame["scale"] * 1000, 3),
                          "radius": round(r_sheet / frame["scale"] * 1000, 3)}
        data["circle"]["center"] = [data["circle"]["x"], data["circle"]["y"]]
    try:                                                   # where the enlarged circle is seen on the sheet
        seen = dwg.view_to_sheet(dwg.view_frame(detail), cx, cy)
        data["visible_on_sheet"] = {"x": round(seen[0] * 1000, 2), "y": round(seen[1] * 1000, 2),
                                    "radius": round(rad * factor * 1000, 2)}
    except Exception as e:
        logger.debug(f"Detail position: {e}")
    message = f"Added {detail.Name} (detail {data['label']}, scale {row.get('scale')}) of {parent.Name}"
    return ok(_placed_warnings(drawing, detail, row, data, message), data)


@tool(
    group="drawing",
    description="Move a drawing view so that its outline centre (see list_drawing_views) lands at x_sheet, y_sheet (mm from the lower left corner). Projected views follow. A view aligned to its parent moves only along that alignment unless keep_alignment is false, which breaks it. Refused if a moved view would leave the sheet. Warns, naming them, when the moved view now overlaps other views.",
    params={"view": {"type": "string", "description": "Name of the view (list_drawing_views)"},
            "x_sheet": {"type": "number", "description": "New centre of the view's outline, from the left edge (in unit, mm by default)"},
            "y_sheet": {"type": "number", "description": "New centre of the view's outline, from the bottom edge (in unit, mm by default)"},
            "keep_alignment": {"type": "boolean", "default": True,
                               "description": "false = break the alignment to the parent view and move freely"},
            'unit': {'type': 'string', 'description': 'Unit of the length inputs: mm (the default, whatever set_units says), cm, m, inch or ft. Results are in mm'}},
    required=["view", "x_sheet", "y_sheet"],
    needs="drawing",
)
def move_drawing_view(ctx, view, x_sheet, y_sheet, keep_alignment=True, unit=None):
    x_sheet = _mm(x_sheet, unit)
    y_sheet = _mm(y_sheet, unit)
    drawing = ctx.doc
    if not isinstance(keep_alignment, bool):
        raise ToolError("keep_alignment must be true or false", Err.INPUT)
    target = dwg.find_view(drawing, view, "move_drawing_view")
    sheet_w, sheet_h, _ = dwg.sheet_size(drawing)
    try:
        tx = dwg.number(x_sheet, "x_sheet", 0, sheet_w * 1000) / 1000.0
        ty = dwg.number(y_sheet, "y_sheet", 0, sheet_h * 1000) / 1000.0
    except ToolError as e:
        raise ToolError(f"{e.message} (mm on the {sheet_w * 1000:g} x {sheet_h * 1000:g} mm sheet)", Err.INPUT)
    name = str(target.Name)

    def boxes():
        return {str(v.Name): [float(c) for c in value(v, "GetOutline")] for v in dwg.views(drawing)}

    before = boxes()
    box = before[name]
    dx, dy = tx - (box[0] + box[2]) / 2.0, ty - (box[1] + box[3]) / 2.0
    aligned = int(value(target, "GetAlignment")) == 2
    alignment, ignored = ("none" if not aligned else "kept"), None
    if aligned and keep_alignment:
        base = value(target, "GetBaseView")
        base_box = before.get(str(base.Name)) if base is not None else None
        if base_box is not None:
            beside = abs((base_box[1] + base_box[3]) - (box[1] + box[3])) < abs((base_box[0] + base_box[2]) - (box[0] + box[2]))
            if beside and abs(dy) > 1e-6:
                ignored, dy = "y_sheet", 0.0
            elif not beside and abs(dx) > 1e-6:
                ignored, dx = "x_sheet", 0.0
    position = [float(v) for v in value(target, "Position")]
    if aligned and not keep_alignment:
        sw.drawing.sw_remove_alignment(target)
        alignment = "broken"
    sw.drawing.sw_place_view(target, position[0] + dx, position[1] + dy)
    sw.common.sw_redraw(drawing)
    after = boxes()
    # only the views this move shifted: one that was already off the sheet does not block it
    shifted = [n for n in after if n == name or max(abs(a - b) for a, b in zip(after[n], before.get(n, after[n]))) > 1e-6]
    off = [n for n in shifted if (b := after[n])[0] < -1e-6 or b[1] < -1e-6 or b[2] > sheet_w + 1e-6 or b[3] > sheet_h + 1e-6]
    if off:
        sw.drawing.sw_place_view(target, position[0], position[1])
        if alignment == "broken":
            sw.drawing.sw_default_alignment(target)
        sw.common.sw_redraw(drawing)
        raise ToolError(f"Not moved: {', '.join(off)} would leave the {sheet_w * 1000:g} x {sheet_h * 1000:g} mm sheet. "
                        f"Choose a centre further inside.", Err.INPUT, {"off_sheet": off})
    moved_with = [n for n in after if n != name and max(abs(a - b) for a, b in zip(after[n], before[n])) > 1e-6]
    new = after[name]
    data = {"view": name, "centre": [round((new[0] + new[2]) / 2 * 1000, 2), round((new[1] + new[3]) / 2 * 1000, 2)],
            "on_sheet_mm": [round(v * 1000, 1) for v in new], "alignment": alignment, "moved_with": moved_with}
    message = f"Moved {name}: its centre is now at ({data['centre'][0]}, {data['centre'][1]}) mm"
    if ignored:
        data["ignored"] = ignored
        message += f"; {ignored} was not used: the view stays aligned to its parent (keep_alignment: false moves it freely)"
    if alignment == "broken":
        message += "; its alignment to the parent view is broken"
    if moved_with:
        message += f"; {', '.join(moved_with)} moved with it"
    # the views it now covers: a moved view and the views that followed it, against every view that stayed
    stayed = [(n, b) for n, b in after.items() if n not in shifted]
    hits = sorted({o for n in shifted for o in dwg.overlaps(after[n], stayed)})
    if hits:
        data["overlaps"] = hits
        message += f". WARNING: it now overlaps {', '.join(hits)} - choose another centre (or a smaller sheet scale)"
    return ok(message, data)




@tool(
    group="export",
    description="Export the active drawing as a PDF. The drawing itself is not saved, renamed or changed. Without filepath the PDF is written next to the saved drawing under the same name. Reports the file size.",
    params={"filepath": {"type": "string", "description": "Where to write it, e.g. C:\\Work\\door.pdf (default: next to the drawing)"}},
    needs="drawing",
    writes=("filepath",),
    timeout=300,                    # a big drawing takes a while to render; an owner-less popup only hits the limit
)
def export_pdf(ctx, filepath=None):
    drawing = ctx.doc
    path_before, title_before = doc_path(drawing), doc_title(drawing)
    if filepath is None or (isinstance(filepath, str) and not filepath.strip()):
        if not path_before:
            raise ToolError("Give a filepath ending in .pdf (the drawing has not been saved, so there is no default "
                            "place).", Err.INPUT)
        path = os.path.splitext(path_before)[0] + ".pdf"
    elif not isinstance(filepath, str):
        raise ToolError("filepath must be text ending in .pdf", Err.INPUT)
    else:
        path = os.path.abspath(filepath.strip())
    if os.path.splitext(path)[1].lower() != ".pdf":
        raise ToolError("filepath must end in .pdf", Err.INPUT)
    if os.path.isdir(path):
        raise ToolError(f"filepath is a folder: {path}", Err.INPUT)
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            raise ToolError(f"Cannot create the folder {folder} ({e.strerror or e})", Err.SAVE)
    temp = path + ".tmp.pdf"
    saved, error = sw.drawing.sw_save_pdf_copy(drawing, temp)
    good = False
    if saved and os.path.isfile(temp):
        with open(temp, "rb") as f:
            good = f.read(5) == b"%PDF-"
    if not good:
        try:
            if os.path.isfile(temp):
                os.remove(temp)
        except OSError:
            pass
        raise ToolError(f"SolidWorks did not write the PDF (error code {error}).", Err.EXPORT)
    try:
        os.replace(temp, path)
    except OSError as e:
        try:
            os.remove(temp)
        except OSError:
            pass
        raise ToolError(f"Could not write {path} ({e.strerror or e}). Is the PDF open in a viewer?", Err.SAVE)
    with open(path, "rb") as f:
        content = f.read()
    pages = (content.count(b"/Type /Page") + content.count(b"/Type/Page")
             - content.count(b"/Type /Pages") - content.count(b"/Type/Pages"))
    _, _, size_name = dwg.sheet_size(drawing)
    data = {"path": path, "bytes": len(content), "document": title_before, "sheet_size": size_name,
            "views": len(dwg.views(drawing))}
    if pages > 0:
        data["pages"] = pages
    message = f"Exported {title_before} to {path} (PDF, {len(content)} bytes, sheet {size_name})"
    if doc_title(drawing) != title_before or doc_path(drawing) != path_before:
        message += ". WARNING: the drawing's name or path changed - check get_document_info."
    return ok(message, data)
