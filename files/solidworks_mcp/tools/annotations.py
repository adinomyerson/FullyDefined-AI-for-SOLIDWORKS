"""Drawing annotations: dimensions on views, tidy dimension placement, title block, bill of materials, balloons.

Reading lives in core/drawing.py. The SolidWorks calls that change a drawing are the sw_* helpers below.
"""

import datetime
import logging
import math
import os


from ..core.api import doc_title, drawing as dwg, Err, ok, tool, ToolError, value
from ..core.api import sw, units

logger = logging.getLogger(__name__)

BOM_TYPES = {"parts_only": 1, "top_level": 2, "indented": 3}
BALLOON_LAYOUTS = {"square": 1, "circle": 2, "top": 3, "bottom": 4, "right": 5, "left": 6}


# ---------------------------------------------------------------- SolidWorks calls


# ---------------------------------------------------------------- tools


def _mm(v, unit):
    """A length given in `unit` as mm, the unit the drawing code works in (no unit: mm, whatever set_units says).
    Anything that is not a number is left as it is, for the tool to refuse."""
    if unit is None or v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return v
    return units.to_m(v, unit) * 1000.0


@tool(
    group="drawing",
    description="Add a dimension to a drawing view between two of its edges or points, chosen by the ids that list_drawing_views(view=...) reports (E3 = an edge, P2 = a point). Two parallel lines, two points, a point and a line, or a circle and a line or circle (measured from the centre) give a distance; two crossing lines give an angle. With only `first`: the length of a line, the diameter of a circle or the radius of an arc. direction forces a horizontal or vertical measurement. The dimension is a reference (driven) one: it shows the model's size and cannot change it. The same dimension again on the same entities is refused; one equal in size to a dimension already in the view is added with a warning naming it. Returns its name, value and place. Several dimensions in one view: give the others in more (one call); if any is refused, none of the call's dimensions stays.",
    params={"view": {"type": "string", "description": "Name of the view"},
            "first": {"type": "string", "description": "Edge or point id, e.g. E1 or P1"},
            "second": {"type": "string", "description": "Second edge or point id (optional)"},
            "direction": {"type": "string", "enum": ["horizontal", "vertical"], "description": "Force the measuring direction (default: SolidWorks decides)"},
            "x_sheet": {"type": "number", "description": "Where the dimension text goes, from the left edge (in unit, mm by default) of the sheet"},
            "y_sheet": {"type": "number", "description": "Where the dimension text goes, from the bottom edge (in unit, mm by default) of the sheet"},
            "more": {"type": "array",
                     "items": {"type": "object",
                               "properties": {"first": {"type": "string"}, "second": {"type": "string"},
                                              "direction": {"type": "string", "enum": ["horizontal", "vertical"]}},
                               "required": ["first"]},
                     "description": "Further dimensions in the same view in this call, e.g. [{\"first\": \"E2\", \"second\": \"E5\"}]; placed beside the first"},
            'unit': {'type': 'string', 'description': 'Unit of the length inputs: mm (the default, whatever set_units says), cm, m, inch or ft. Results are in mm'}},
    required=["view", "first"],
    needs="drawing",
)
def add_drawing_dimension(ctx, view, first, second=None, direction=None, x_sheet=None, y_sheet=None, more=None, unit=None):
    x_sheet = _mm(x_sheet, unit)
    y_sheet = _mm(y_sheet, unit)
    drawing = ctx.doc
    target = dwg.find_view(drawing, view, "add_drawing_dimension")
    asks = [{"first": first, "second": second, "direction": direction, "x_sheet": x_sheet, "y_sheet": y_sheet}]
    if more is not None:
        if not isinstance(more, list) or not all(isinstance(m, dict) and m.get("first") for m in more):
            raise ToolError("more must be a list of {first, second, direction}, each with first", Err.INPUT)
        asks += [{"first": m.get("first"), "second": m.get("second"), "direction": m.get("direction")} for m in more]
    edges, points = dwg.view_entities(target)
    lookup = {row["id"]: row for row in edges}
    lookup.update({row["id"]: row for row in points})
    # every request is checked before anything is added: a refused call changes nothing
    plans = [_dimension_plan(target, lookup, len(edges), len(points), ask, n) for n, ask in enumerate(asks)]

    frame = dwg.view_frame(target)
    outline = [float(v) for v in value(target, "GetOutline")]
    entities = edges + points
    added, results, stacked = [], [], {"wide": 0, "tall": 0}
    try:
        for n, (chosen, mode, ask) in enumerate(plans):
            spots = [dwg.view_to_sheet(frame, *_centre_of(row)) for row in chosen]
            mid = (sum(s[0] for s in spots) / len(spots), sum(s[1] for s in spots) / len(spots))
            if len(chosen) == 2:
                wide = abs(spots[0][0] - spots[1][0]) >= abs(spots[0][1] - spots[1][1])
            else:
                row = chosen[0]
                wide = abs(row["start"][0] - row["end"][0]) >= abs(row["start"][1] - row["end"][1]) \
                    if row["kind"] == "line" else True
            if mode:
                wide = mode == "horizontal"
            # the default place steps outward for each further dimension of the call, so their texts do not meet
            side = "wide" if wide else "tall"
            step = stacked[side]
            stacked[side] += 1
            default = (mid[0], outline[3] + 0.008 + 0.008 * step) if wide else (outline[2] + 0.010 + 0.010 * step, mid[1])
            place = dwg.sheet_place(drawing, ask.get("x_sheet"), ask.get("y_sheet"), default)
            results.append(_add_one(ctx, drawing, target, entities, chosen, mode, place, added, n, len(plans)))
    except ToolError:
        for obj in reversed(added):                        # take back what this call added before the refusal
            sw.drawing.sw_delete_dimension(drawing, obj)
        if added:
            sw.drawing.sw_finish(drawing)
        raise
    rows = dwg.dimension_rows(target)
    first_row, between, equal = results[0]
    data = {"view": str(target.Name), "dimension": first_row, "between": between, "dimensions_in_view": len(rows)}
    if len(results) > 1:
        data["dimensions"] = [r[0] for r in results]
    texts = [f"{r[0].get('name', 'a dimension')} = {r[0].get('value')} {r[0].get('unit', '')} ({r[0].get('kind', '')})"
             for r in results]
    message = f"Added {', '.join(texts)} on {target.Name}"
    equals = [(r[0].get("name"), r[2]) for r in results if r[2]]
    if equals:
        if len(results) == 1:
            data["equal_to"] = equals[0][1]
        else:
            data["equal_to"] = {name: eq for name, eq in equals}
        message += ". WARNING: " + "; ".join(
            f"{', '.join(eq)} in this view already shows the size of {name}" for name, eq in equals) + \
            ": if one measures the same size, delete it with delete_drawing_dimension"
    return ok(message, data)


def _centre_of(row):
    if "at" in row:
        return row["at"]
    if "center" in row:
        return row["center"]
    return (row["start"][0] + row["end"][0]) / 2.0, (row["start"][1] + row["end"][1]) / 2.0


def _dimension_plan(target, lookup, n_edges, n_points, ask, n):
    """Check one requested dimension: (the chosen entities, the direction, the request). n > 0: in `more`."""
    where = "" if n == 0 else f"more[{n - 1}]: "
    mode = None
    if ask.get("direction") is not None:
        mode = str(ask["direction"]).strip().lower()
        if mode not in ("horizontal", "vertical"):
            raise ToolError(f"{where}direction must be horizontal or vertical", Err.INPUT)
    chosen = []
    for name in ("first", "second"):
        given = ask.get(name)
        if given is None and name == "second":
            continue
        key = str(given).strip().upper() if isinstance(given, str) else ""
        if key not in lookup:
            raise ToolError(f"{where}{name} must be an edge or point id of {target.Name} such as E1 or P1 (it has "
                            f"{n_edges} edges and {n_points} points - see list_drawing_views(view=...)).", Err.INPUT)
        chosen.append(lookup[key])
    if len(chosen) == 2 and chosen[0]["id"] == chosen[1]["id"]:
        raise ToolError(f"{where}first and second are the same entity.", Err.INPUT)
    if len(chosen) == 1 and "at" in chosen[0]:
        raise ToolError(f"{where}A single point cannot be dimensioned; give a second edge or point.", Err.INPUT)
    return chosen, mode, ask


def _add_one(ctx, drawing, target, entities, chosen, mode, place, added, n, total):
    """Add one planned dimension; refuse it (taken back out) when the same one is already there. Returns (its row,
    the ids between, the names of equal dimensions already in the view)."""
    where = "" if n == 0 else f"more[{n - 1}]: "
    before = dwg.dimension_rows(target)
    for d in before:
        d["_ids"] = set(dwg.attached_ids(ctx.app, d["_obj"], entities))
    names_before = {d["name"] for d in before}
    display = sw.drawing.sw_dimension(drawing, target, [row["_obj"] for row in chosen], mode, place[0], place[1])
    sw.drawing.sw_finish(drawing)
    if display is None:
        raise ToolError(f"{where}SolidWorks could not put a dimension between these two. Parallel lines, points and "
                        f"circles can be dimensioned; two crossing lines give an angle." +
                        (" Nothing of this call was added." if total > 1 else ""), Err.FEATURE)
    rows = dwg.dimension_rows(target)
    new = [d for d in rows if d["name"] not in names_before]
    row = new[-1] if new else (rows[-1] if rows else {})
    between = [c["id"] for c in chosen]

    def same_size(d):
        return d.get("kind") == row.get("kind") and abs(d.get("value", 1e9) - row.get("value", -1e9)) < 1e-4

    twin = [d["name"] for d in before if same_size(d) and d["_ids"] == set(between)]
    if twin and new:                                       # the same dimension again: take it back out
        sw.drawing.sw_delete_dimension(drawing, row["_obj"])
        sw.drawing.sw_finish(drawing)
        raise ToolError(f"{where}{' and '.join(between)} already have this dimension: {twin[0]} = {row.get('value')} "
                        f"{row.get('unit', '')} on {target.Name}. Nothing was added (move it with arrange_dimensions, "
                        f"or delete it with delete_drawing_dimension first).", Err.INPUT, {"existing": twin[0]})
    if new:
        added.append(row["_obj"])
    equal = [d["name"] for d in before if same_size(d)]
    row.pop("_obj", None)
    return row, between, equal


SHEET_MARGIN_MM = 5.0              # dimension texts stay this far inside the sheet edge


def _onto_sheet(drawing, target, rows, sheet_w_mm: float, sheet_h_mm: float):
    """Dimension texts past the sheet edge go to the other side of their view (a row that would leave the sheet
    wraps), else to the nearest place inside the margin. Returns (names moved, names still off the sheet)."""
    lo_x, lo_y = SHEET_MARGIN_MM, SHEET_MARGIN_MM
    hi_x, hi_y = sheet_w_mm - SHEET_MARGIN_MM, sheet_h_mm - SHEET_MARGIN_MM

    def inside(p):
        return lo_x <= p[0] <= hi_x and lo_y <= p[1] <= hi_y

    try:
        box = [float(v) * 1000 for v in value(target, "GetOutline")]
        cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
    except Exception:
        cx, cy = sheet_w_mm / 2.0, sheet_h_mm / 2.0
    moved, stuck = [], []
    for row in rows:
        p = row["at_sheet"]
        if inside(p):
            continue
        x, y = p
        if not lo_x <= x <= hi_x:
            x = 2 * cx - x                                 # mirrored to the other side of the view
        if not lo_y <= y <= hi_y:
            y = 2 * cy - y
        if not inside((x, y)):
            x, y = min(max(x, lo_x), hi_x), min(max(y, lo_y), hi_y)
        if sw.drawing.sw_move_annotation(row["_obj"], x / 1000.0, y / 1000.0):
            moved.append(row["name"])
        else:
            stuck.append(row["name"])
    return moved, stuck


@tool(
    group="drawing",
    description="Spread the dimensions of a drawing view (or of every view) so they stop overlapping; use it after create_drawing(dimensions=true) or after adding dimensions. Returns where each dimension text ended up and any pairs still closer than 2 mm.",
    params={"view": {"type": "string", "description": "Name of one view (default: every view)"},
            "spacing": {"type": "number", "description": "Distance between stacked dimension lines (in unit; default 6 mm)"},
            'unit': {'type': 'string', 'description': 'Unit of the length inputs: mm (the default, whatever set_units says), cm, m, inch or ft. Results are in mm'}},
    needs="drawing",
)
def arrange_dimensions(ctx, view=None, spacing=None, unit=None):
    spacing = _mm(spacing, unit)
    drawing = ctx.doc
    gap = 6.0
    if spacing is not None:
        try:
            gap = dwg.number(spacing, "spacing", 1, 100)
        except ToolError as e:
            raise ToolError(f"{e.message} (mm)", Err.INPUT)
    if view is None or (isinstance(view, str) and not view.strip()):
        targets = dwg.views(drawing)
    else:
        targets = [dwg.find_view(drawing, view, "arrange_dimensions")]
    sheet_w, sheet_h, _ = dwg.sheet_size(drawing)
    report, moved_total, total, wrapped, off_sheet = [], 0, 0, [], []
    for target in targets:
        before = dwg.dimension_rows(target)
        if not before:
            continue
        total += len(before)
        sw.drawing.sw_align_dimensions(drawing, [row["_obj"] for row in before], gap / 1000.0)
        moved_in, stuck = _onto_sheet(drawing, target, dwg.dimension_rows(target), sheet_w * 1000, sheet_h * 1000)
        wrapped += [[str(target.Name), name] for name in moved_in]
        after = {d["name"]: d for d in dwg.dimension_rows(target)}
        for name in stuck:
            off_sheet.append([str(target.Name), name])
        for name, d in after.items():
            p = d["at_sheet"]
            if not (0 <= p[0] <= sheet_w * 1000 and 0 <= p[1] <= sheet_h * 1000) and [str(target.Name), name] not in off_sheet:
                off_sheet.append([str(target.Name), name])
        moved, rows = 0, []
        for row in before:
            now = after.get(row["name"], row)
            if math.dist(now["at_sheet"], row["at_sheet"]) > 0.05:
                moved += 1
            rows.append({"name": row["name"], "value": row["value"], "unit": row["unit"], "at_sheet": now["at_sheet"]})
        moved_total += moved
        report.append({"view": str(target.Name), "dimensions": rows, "moved": moved})
    sw.drawing.sw_finish(drawing)
    if not total:
        where = targets[0].Name if len(targets) == 1 else "the drawing"
        raise ToolError(f"There are no dimensions in {where} to arrange.", Err.INPUT)
    stacked = []                                           # texts that still share a place
    for entry in report:
        rows = entry["dimensions"]
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                if math.dist(rows[i]["at_sheet"], rows[j]["at_sheet"]) < 2.0:
                    stacked.append([entry["view"], rows[i]["name"], rows[j]["name"]])
    data = {"views": report, "dimensions": total, "moved": moved_total, "spacing": gap, "still_stacked": stacked,
            "moved_onto_sheet": wrapped, "off_sheet": off_sheet}
    message = f"Arranged {total} dimensions in {len(report)} view(s); {moved_total} moved"
    if wrapped:
        message += f"; {len(wrapped)} that ran past the sheet edge moved back onto it"
    if off_sheet:
        message += f". WARNING: {len(off_sheet)} could not be placed on the sheet: {off_sheet}"
    if stacked:
        message += f". WARNING: {len(stacked)} pair(s) still sit closer than 2 mm"
    return ok(message, data)


_ROUND_KINDS = {5: "radius", 6: "diameter"}           # IDisplayDimension.Type2 of round dimensions


def _find_dimensions(drawing, name, views):
    """[(view, row)] of the dimensions called `name` (as list_drawing_views shows them, ignoring case)."""
    wanted = str(name or "").strip().lower()
    if not wanted:
        raise ToolError("Give the dimension name as list_drawing_views(view=...) shows it, e.g. RD1", Err.INPUT)
    found = []
    for view in views:
        for row in dwg.dimension_rows(view):
            if row["name"].lower() == wanted:
                found.append((view, row))
    return found


def _known(views) -> str:
    names = [f"{row['name']} ({v.Name})" for v in views for row in dwg.dimension_rows(v)]
    return ", ".join(names[:40]) or "none"


@tool(
    group="drawing",
    description="Delete a dimension from a drawing view by the name list_drawing_views(view=...) gives, e.g. RD1. Only the drawing changes; the model dimension stays in the part.",
    params={"view": {"type": "string", "description": "Name of the view the dimension is in"},
            "dimension": {"type": "string", "description": "Dimension name, e.g. RD1 or D1"}},
    required=["view", "dimension"],
    needs="drawing",
)
def delete_drawing_dimension(ctx, view, dimension):
    drawing = ctx.doc
    target = dwg.find_view(drawing, view, "delete_drawing_dimension")
    found = _find_dimensions(drawing, dimension, [target])
    if not found:
        raise ToolError(f"{target.Name} has no dimension named '{dimension}'. Its dimensions: {_known([target])}",
                        Err.INPUT)
    _, row = found[0]
    count = len(dwg.dimension_rows(target))
    if not sw.drawing.sw_delete_dimension(drawing, row["_obj"]) or len(dwg.dimension_rows(target)) != count - 1:
        raise ToolError(f"SolidWorks could not delete {row['name']}", Err.FEATURE)
    sw.drawing.sw_finish(drawing)
    data = {"view": str(target.Name), "deleted": row["name"], "value": row["value"], "unit": row["unit"],
            "dimensions_left": count - 1}
    return ok(f"Deleted {row['name']} ({row['value']} {row['unit']}) from {target.Name}; {count - 1} left", data)


@tool(
    group="drawing",
    description="Change how a drawing dimension looks: kind shows a round dimension as a radius or a diameter; text_color sets its text colour as {r, g, b} (0-255). The dimension is found by name among the views of the active sheet (list_drawing_views).",
    params={"dimension": {"type": "string", "description": "Dimension name, e.g. RD1"},
            "kind": {"type": "string", "enum": ["radius", "diameter"], "description": "For a round dimension"},
            "text_color": {"type": "object",
                           "properties": {"r": {"type": "integer"}, "g": {"type": "integer"}, "b": {"type": "integer"}},
                           "required": ["r", "g", "b"], "description": "Colour of the dimension, 0-255 each"}},
    required=["dimension"],
    needs="drawing",
)
def set_dimension_style(ctx, dimension, kind=None, text_color=None):
    drawing = ctx.doc
    if kind is None and text_color is None:
        raise ToolError("Give kind (radius or diameter) and / or text_color {r, g, b}", Err.INPUT)
    if kind is not None:
        kind = str(kind).strip().lower()
        if kind not in ("radius", "diameter"):
            raise ToolError("kind must be radius or diameter", Err.INPUT)
    colorref = None
    if text_color is not None:
        keys = ("r", "g", "b")
        if not isinstance(text_color, dict) or set(text_color) != set(keys) or any(
                isinstance(text_color[k], bool) or not isinstance(text_color[k], int) or not 0 <= text_color[k] <= 255
                for k in keys):
            raise ToolError("text_color must be {r, g, b} with whole numbers from 0 to 255", Err.INPUT)
        colorref = text_color["r"] | (text_color["g"] << 8) | (text_color["b"] << 16)
    views = dwg.views(drawing)
    found = _find_dimensions(drawing, dimension, views)
    if not found:
        raise ToolError(f"No dimension named '{dimension}' on this sheet. Dimensions: {_known(views)}", Err.INPUT)
    if len(found) > 1:
        raise ToolError(f"{dimension} is in {len(found)} views ({', '.join(str(v.Name) for v, _ in found)}); "
                        f"rename or delete one first", Err.INPUT)
    view, row = found[0]
    display = row["_obj"]
    current = _ROUND_KINDS.get(int(display.Type2))
    if kind is not None and current is None:
        raise ToolError(f"{row['name']} is a {row['kind']} dimension: only a round (radius or diameter) dimension "
                        f"can change kind", Err.INPUT)
    old_diametric, old_color = bool(display.Diametric), int(value(display, "GetAnnotation").Color)

    def put_back():
        try:
            sw.drawing.sw_set_diametric(display, old_diametric)
            sw.drawing.sw_set_annotation_color(display, old_color)
            sw.common.sw_redraw(drawing)
        except Exception as e:
            logger.debug(f"Could not put the dimension back: {e}")

    if kind is not None and kind != current:
        sw.drawing.sw_set_diametric(display, kind == "diameter")
    if colorref is not None:
        sw.drawing.sw_set_annotation_color(display, colorref)
    sw.common.sw_redraw(drawing)
    after = next((r for r in dwg.dimension_rows(view) if r["name"] == row["name"]), row)
    data = {"view": str(view.Name), "dimension": row["name"], "kind": _ROUND_KINDS.get(int(after["_obj"].Type2),
                                                                                       after["kind"]),
            "value": after["value"], "unit": after["unit"]}
    if kind is not None and data["kind"] != kind:
        put_back()
        raise ToolError(f"SolidWorks did not show {row['name']} as a {kind}; nothing was changed", Err.FEATURE, data)
    if colorref is not None:
        data["text_color"] = dict(text_color)
    return ok(f"{row['name']} in {view.Name}: {data['kind']} {data['value']} {data['unit']}"
              + (f", colour {text_color}" if colorref is not None else ""), data)


@tool(
    group="drawing",
    description="Put a title block in the bottom right corner of the active sheet, or update the one already there (title, drawn by, date, scale, material, sheet size; every input is optional). The first call fills missing fields from the drawing (title = the model's name, date = today, scale = the sheet scale, material = the drawn part's material, sheet size = the sheet); later calls change only the fields given. Returns the block as it now reads.",
    params={"title": {"type": "string", "description": "Drawing title"},
            "drawn_by": {"type": "string", "description": "Who drew it"},
            "date": {"type": "string", "description": "Date text, e.g. 2026-10-10"},
            "scale": {"type": "string", "description": "Scale text, e.g. 1:2"},
            "material": {"type": "string", "description": "Material text"},
            "sheet_size": {"type": "string", "description": "Sheet size text, e.g. A3 (must be the real sheet size)"}},
    needs="drawing",
)
def set_title_block(ctx, title=None, drawn_by=None, date=None, scale=None, material=None, sheet_size=None):
    drawing = ctx.doc
    given = {"title": title, "drawn_by": drawn_by, "date": date, "scale": scale, "material": material,
             "sheet_size": sheet_size}
    for key, text in given.items():
        if text is None:
            continue
        if not isinstance(text, str) or "\n" in text or "\r" in text or len(text) > 80:
            raise ToolError(f"{key} must be one line of text of at most 80 characters", Err.INPUT)
        given[key] = text.strip()
    width, height, size_name = dwg.sheet_size(drawing)
    if given["sheet_size"] is not None and given["sheet_size"].upper() != size_name.upper():
        raise ToolError(f"The sheet is {size_name}, so the title block cannot say {given['sheet_size']}. The sheet size "
                        f"is chosen when the drawing is made: create_drawing(sheet_size=...).", Err.INPUT)
    if given["sheet_size"] is not None:
        given["sheet_size"] = size_name

    table = dwg.title_table(drawing)
    created = table is None
    if created:
        views = dwg.views(drawing)
        model, kind, path = dwg.view_model(views[0]) if views else (None, 0, "")
        props = [float(v) for v in value(value(drawing, "GetCurrentSheet"), "GetProperties2")]
        found_material = "-"
        if kind == 1 and model is not None:
            try:
                parts = str(model.MaterialIdName or "").split("|")
                if len(parts) >= 2 and parts[1].strip():
                    found_material = parts[1].strip()
            except Exception:
                pass
        elif kind == 2:
            found_material = "see parts list"
        values = {"title": os.path.splitext(os.path.basename(path))[0] if path else doc_title(drawing), "drawn_by": "",
                  "date": datetime.date.today().isoformat(), "scale": f"{props[2]:g}:{props[3]:g}",
                  "material": found_material, "sheet_size": size_name}
    else:
        values = {key: dwg.cell(table, i, 1) for i, (key, _) in enumerate(dwg.TITLE_ROWS)}
    for key, text in given.items():
        if text is not None:
            values[key] = text

    if created:
        margin = 0.010
        table = sw.drawing.sw_insert_table(drawing, width - margin, margin, len(dwg.TITLE_ROWS), 2)
        if table is None:
            raise ToolError("SolidWorks could not insert the title block table.", Err.FEATURE)
        for column, column_width in ((0, 0.028), (1, 0.072)):
            try:
                table.SetColumnWidth(column, column_width, 0)
            except Exception as e:
                logger.debug(f"Column width: {e}")
    for i, (key, heading) in enumerate(dwg.TITLE_ROWS):
        if created:
            sw.drawing.sw_set_cell(table, i, 0, heading)
        if created or given[key] is not None:
            sw.drawing.sw_set_cell(table, i, 1, values[key])
    sw.drawing.sw_finish(drawing)

    read_back = {key: dwg.cell(table, i, 1) for i, (key, _) in enumerate(dwg.TITLE_ROWS)}
    data = {"title_block": read_back, "created": created}
    message = ("Added the title block" if created else "Updated the title block") + ": " + \
        ", ".join(f"{k}={v!r}" for k, v in read_back.items())
    box = dwg.table_box(table)
    if box:
        data["on_sheet_mm"] = [round(v * 1000, 1) for v in box]
        hits = dwg.overlaps(box, dwg.view_boxes(drawing))
        if hits:
            data["overlaps"] = hits
            message += f". WARNING: it overlaps {', '.join(hits)} - use a larger sheet or a smaller scale in create_drawing"
    return ok(message, data)


@tool(
    group="drawing",
    description="Add a bill of materials table to a drawing of an assembly: item number, part name and quantity per component. bom_type: top_level (default; a sub-assembly counts as one item), parts_only or indented. The table goes in the top right corner of the sheet unless x_sheet / y_sheet give its top right corner. Returns the table's rows.",
    params={"view": {"type": "string", "description": "A view of the assembly (default: the first one)"},
            "bom_type": {"type": "string", "enum": ["top_level", "parts_only", "indented"], "default": "top_level",
                         "description": "What the table lists"},
            "x_sheet": {"type": "number", "description": "Top right corner of the table, from the left edge (in unit, mm by default) of the sheet"},
            "y_sheet": {"type": "number", "description": "Top right corner of the table, from the bottom edge (in unit, mm by default) of the sheet"},
            'unit': {'type': 'string', 'description': 'Unit of the length inputs: mm (the default, whatever set_units says), cm, m, inch or ft. Results are in mm'}},
    needs="drawing",
)
def add_bom_table(ctx, view=None, bom_type="top_level", x_sheet=None, y_sheet=None, unit=None):
    x_sheet = _mm(x_sheet, unit)
    y_sheet = _mm(y_sheet, unit)
    drawing = ctx.doc
    key = str(bom_type or "top_level").strip().lower().replace(" ", "_").replace("-", "_")
    if key not in BOM_TYPES:
        raise ToolError("bom_type must be top_level, parts_only or indented", Err.INPUT)
    target = dwg.assembly_view(drawing, view, "add_bom_table")
    width, height, _ = dwg.sheet_size(drawing)
    place = dwg.sheet_place(drawing, x_sheet, y_sheet, (width - 0.010, height - 0.010))
    if any(kind == "BomFeat" for kind, _ in dwg.tables(drawing)):
        raise ToolError("The drawing already has a bill of materials table.", Err.INPUT)
    configuration = ""
    try:
        configuration = str(target.ReferencedConfiguration or "")
    except Exception:
        pass
    table = sw.drawing.sw_insert_bom(target, place[0], place[1], BOM_TYPES[key], configuration)
    sw.drawing.sw_finish(drawing)
    if table is None:
        raise ToolError("SolidWorks could not insert the bill of materials.", Err.FEATURE)
    rows = dwg.table_rows(table)
    data = {"view": str(target.Name), "bom_type": key, "header": rows[0] if rows else [], "rows": rows[1:],
            "items": max(len(rows) - 1, 0)}
    message = f"Added a bill of materials ({key}) with {data['items']} items from {target.Name}"
    box = dwg.table_box(table)
    if box:
        data["on_sheet_mm"] = [round(v * 1000, 1) for v in box]
        hits = dwg.overlaps(box, dwg.view_boxes(drawing))
        if hits:
            data["overlaps"] = hits
            message += f". WARNING: it overlaps {', '.join(hits)} - give x_sheet / y_sheet"
    return ok(message, data)


@tool(
    group="drawing",
    description="Add item balloons to a view of an assembly in the active drawing: one per component, numbered as in the bill of materials (add_bom_table). Default view: the isometric view, if there is one. SolidWorks balloons each item once per sheet, so a second view only gets balloons for items the first one did not show. Returns the item numbers.",
    params={"view": {"type": "string", "description": "A view of the assembly (default: the isometric view, else the first view)"},
            "layout": {"type": "string", "enum": ["square", "circle", "top", "bottom", "left", "right"], "default": "square",
                       "description": "Where the balloons go around the view"}},
    needs="drawing",
)
def add_balloons(ctx, view=None, layout="square"):
    drawing = ctx.doc
    key = str(layout or "square").strip().lower()
    if key not in BALLOON_LAYOUTS:
        raise ToolError(f"layout must be one of: {', '.join(BALLOON_LAYOUTS)}", Err.INPUT)
    target = dwg.assembly_view(drawing, view, "add_balloons", prefer_isometric=True)
    if dwg.balloons(target):
        raise ToolError(f"{target.Name} already has balloons.", Err.INPUT)
    if not sw.drawing.sw_auto_balloons(drawing, target, BALLOON_LAYOUTS[key]):
        raise ToolError(f"Could not select {target.Name}.", Err.INTERNAL)
    sw.drawing.sw_finish(drawing)
    rows = dwg.balloons(target)
    if not rows:
        elsewhere = [str(v.Name) for v in dwg.views(drawing) if dwg.balloons(v)]
        if elsewhere:
            raise ToolError(f"SolidWorks added no balloons to {target.Name}: every item it shows already has its balloon "
                            f"in {', '.join(elsewhere)} (each item is ballooned once per sheet).", Err.INPUT)
        raise ToolError(f"SolidWorks added no balloons to {target.Name}.", Err.FEATURE)
    has_bom = any(kind == "BomFeat" for kind, _ in dwg.tables(drawing))
    data = {"view": str(target.Name), "balloons": rows, "count": len(rows), "items": [r["item"] for r in rows],
            "layout": key}
    message = f"Added {len(rows)} balloons to {target.Name}: items {', '.join(data['items'])}"
    if not has_bom:
        message += ". There is no bill of materials yet - add_bom_table adds the matching table"
    return ok(message, data)
