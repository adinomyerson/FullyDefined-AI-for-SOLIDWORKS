"""
Drawings, read only: sheets, views, how model points land on a view, the visible edges and points of a
view, its dimensions, tables and balloons, view letters and where things go on the sheet. Shared by the
drawing tools; the calls that change a drawing are sw_* helpers in the tool modules.

View coordinates: positions ON a view (a cutting line, a detail circle, the edges and points
list_drawing_views reports) are mm on the model, right (x) and up (y) on the sheet from where the model origin
appears in that view; they do not depend on the view's scale or place. Sheet coordinates: positions OF
something on the sheet are mm from the bottom left corner of the sheet.
"""

import logging
import math
import os
from typing import Dict, List, Optional, Tuple

from .com import doc_path, doc_title, doc_type, null_object, value
from .results import Err, ToolError

logger = logging.getLogger(__name__)

# sheet name -> (swDwgPaperSizes_e, width m, height m), landscape
SHEETS = {"A4": (6, 0.297, 0.210), "A3": (8, 0.420, 0.297), "A2": (9, 0.594, 0.420), "A1": (10, 0.841, 0.594),
          "A0": (11, 1.189, 0.841)}
# preferred drawing scales, largest first, as (numerator, denominator)
SCALES = [(10, 1), (5, 1), (2, 1), (1, 1), (1, 2), (1, 5), (1, 10), (1, 20), (1, 50), (1, 100)]
VIEW_TYPES = {1: "sheet", 2: "section", 3: "detail", 4: "projected", 5: "auxiliary", 6: "standard", 7: "named",
              8: "relative"}
DIM_KINDS = {1: "ordinate", 2: "linear", 3: "angular", 4: "arc_length", 5: "radial", 6: "diameter", 7: "ordinate",
             8: "ordinate", 9: "z_ordinate", 10: "chamfer"}
TITLE_ROWS = [("title", "TITLE"), ("drawn_by", "DRAWN BY"), ("date", "DATE"), ("scale", "SCALE"),
              ("material", "MATERIAL"), ("sheet_size", "SHEET")]


# ---------------------------------------------------------------- numbers and scales

def number(given, label: str, low: float = None, high: float = None) -> float:
    """A finite number in [low, high], or a refusal."""
    if isinstance(given, bool) or not isinstance(given, (int, float)):
        raise ToolError(f"{label} must be a number", Err.INPUT)
    given = float(given)
    if not math.isfinite(given):
        raise ToolError(f"{label} must be a finite number", Err.INPUT)
    if low is not None and given < low:
        raise ToolError(f"{label} must be at least {low:g}", Err.INPUT)
    if high is not None and given > high:
        raise ToolError(f"{label} must be at most {high:g}", Err.INPUT)
    return given


def scale_text(factor: float) -> str:
    """0.5 -> '1:2', 2.0 -> '2:1'"""
    return f"{factor:g}:1" if factor >= 1 else f"1:{1.0 / factor:g}"


def parse_scale(text) -> Optional[Tuple[float, float]]:
    """'1:2' -> (1, 2); '2:1' -> (2, 1); None for 'auto' or bad input."""
    if text is None:
        return None
    cleaned = str(text).strip().lower().replace(" ", "")
    if cleaned in ("", "auto"):
        return None
    for sep in (":", "/"):
        if sep in cleaned:
            a, b = cleaned.split(sep, 1)
            try:
                a, b = float(a), float(b)
            except ValueError:
                return None
            return (a, b) if a > 0 and b > 0 else None
    try:
        factor = float(cleaned)
        return (factor, 1.0) if factor > 0 else None
    except ValueError:
        return None


def mm(pair) -> List[float]:
    return [round(pair[0] * 1000, 3) + 0.0, round(pair[1] * 1000, 3) + 0.0]


# ---------------------------------------------------------------- sheets

def sheet_names(drawing) -> List[str]:
    return [str(n) for n in (value(drawing, "GetSheetNames") or [])]


def current_sheet_name(drawing) -> str:
    return str(value(value(drawing, "GetCurrentSheet"), "GetName"))


def _size_name(width: float, height: float) -> str:
    return next((k for k, (_, w, h) in SHEETS.items() if abs(w - width) < 5e-4 and abs(h - height) < 5e-4),
                f"{width * 1000:g} x {height * 1000:g} mm")


def sheet_size(drawing) -> Tuple[float, float, str]:
    """(width m, height m, name) of the current sheet."""
    props = [float(v) for v in value(value(drawing, "GetCurrentSheet"), "GetProperties2")]
    return props[5], props[6], _size_name(props[5], props[6])


def sheet_row(drawing, name: str, active: str = None) -> Dict:
    """name, size, scale, number of views and dimensions of a sheet (read without activating it)."""
    row = {"name": name, "active": name == active}
    sheet = drawing.Sheet(name)
    try:
        props = [float(v) for v in value(sheet, "GetProperties2")]
        row["size"] = _size_name(props[5], props[6])
        if props[3]:
            row["scale"] = scale_text(props[2] / props[3])
    except Exception as e:
        logger.debug(f"Sheet properties of {name}: {e}")
    views = list(value(sheet, "GetViews") or [])
    row["views"] = len(views)
    count = 0
    for view in views:
        try:
            count += int(value(view, "GetDisplayDimensionCount"))
        except Exception:
            pass
    row["dimensions"] = count
    return row


def dangling_dimensions(drawing, name: str) -> List[Dict]:
    """The dimensions of a sheet that are attached to nothing (SolidWorks shows them in red)."""
    found = []
    for view in (value(drawing.Sheet(name), "GetViews") or []):
        try:
            shown = value(view, "GetDisplayDimensions") or []
        except Exception:
            continue
        for display in shown:
            try:
                if bool(value(value(display, "GetAnnotation"), "IsDangling")):
                    dim = display.GetDimension2(0)
                    found.append({"view": str(view.Name), "dimension": str(dim.FullName).split("@")[0],
                                  "value": round(float(dim.SystemValue) * 1000, 4)})
            except Exception as e:
                logger.debug(f"Dangling check of a dimension in {view.Name}: {e}")
    return found


def open_drawings(app) -> list:
    docs = []
    doc = app.GetFirstDocument
    while doc is not None and len(docs) < 1000:
        try:
            if doc_type(doc) == 3:
                docs.append(doc)
        except Exception:
            pass
        doc = doc.GetNext
    return docs


def same_doc(a, b) -> bool:
    path_a, path_b = str(doc_path(a) or ""), str(doc_path(b) or "")
    if path_a and path_b:
        return os.path.normcase(os.path.abspath(path_a)) == os.path.normcase(os.path.abspath(path_b))
    return str(doc_title(a)) == str(doc_title(b))


# ---------------------------------------------------------------- views

def views(drawing) -> list:
    """The views of the current sheet (without the sheet itself)."""
    out = []
    view = value(drawing, "GetFirstView")
    view = value(view, "GetNextView") if view is not None else None
    while view is not None and len(out) < 200:
        out.append(view)
        view = value(view, "GetNextView")
    return out


def find_view(drawing, name, tool: str):
    """A view of the current sheet by name, or a refusal listing the views."""
    found = views(drawing)
    names = [str(v.Name) for v in found]
    if not found:
        raise ToolError("The drawing has no views.", Err.INPUT)
    if not isinstance(name, str) or not name.strip():
        raise ToolError(f"{tool} needs the name of a view. Views: {', '.join(names)}", Err.INPUT)
    wanted = name.strip().lower()
    for view, view_name in zip(found, names):
        if view_name.lower() == wanted:
            return view
    raise ToolError(f"No view named '{name.strip()}'. Views: {', '.join(names)}", Err.INPUT)


def view_frame(view) -> Dict:
    """How model points land on the sheet in this view."""
    m = [float(v) for v in value(view.ModelToViewTransform, "ArrayData")]
    pos = [float(v) for v in view.Position]
    return {"rot": m[0:9], "origin": (m[9], m[10]), "scale": m[12] or 1.0, "pos": (pos[0], pos[1]),
            "angle": float(view.Angle)}


def to_view(frame: Dict, p) -> Tuple[float, float]:
    """Model point (m) -> view coordinates (m on the model, right / up on the sheet)."""
    r = frame["rot"]
    return r[0] * p[0] + r[3] * p[1] + r[6] * p[2], r[1] * p[0] + r[4] * p[1] + r[7] * p[2]


def view_to_sheet(frame: Dict, x: float, y: float) -> Tuple[float, float]:
    return frame["origin"][0] + x * frame["scale"], frame["origin"][1] + y * frame["scale"]


def view_to_sketch(frame: Dict, x: float, y: float) -> Tuple[float, float]:
    """View coordinates (m) -> coordinates of the view's own sketch."""
    s = frame["scale"]
    dx = (frame["origin"][0] + x * s - frame["pos"][0]) / s
    dy = (frame["origin"][1] + y * s - frame["pos"][1]) / s
    c, sn = math.cos(-frame["angle"]), math.sin(-frame["angle"])
    return dx * c - dy * sn, dx * sn + dy * c


def _apply(transform, p):
    """Component point -> assembly point."""
    if transform is None:
        return p
    m, s = transform, transform[12] or 1.0
    return ((m[0] * p[0] + m[3] * p[1] + m[6] * p[2]) * s + m[9],
            (m[1] * p[0] + m[4] * p[1] + m[7] * p[2]) * s + m[10],
            (m[2] * p[0] + m[5] * p[1] + m[8] * p[2]) * s + m[11])


def view_model(view):
    """(referenced document or None, its type or 0, file path)."""
    model, path = None, ""
    try:
        model = view.ReferencedDocument
    except Exception:
        model = None
    try:
        path = str(value(view, "GetReferencedModelName") or "")
    except Exception:
        path = ""
    kind = 0
    if model is not None:
        try:
            kind = doc_type(model)
        except Exception:
            kind = 0
    if not kind and path:
        kind = {".sldprt": 1, ".sldasm": 2}.get(os.path.splitext(path)[1].lower(), 0)
    return model, kind, path


def view_entities(view) -> Tuple[List[Dict], List[Dict]]:
    """Visible edges (E1, E2 ...) and their end points (P1, P2 ...) in a view, in a fixed order. Rows carry the
    COM object under "_obj" (never returned to the caller)."""
    frame = view_frame(view)
    _, kind, _ = view_model(view)
    sources = []
    if kind == 2:
        try:
            placed = {}                     # where each component sits in the assembly, by its name in the drawing

            def walk(drawing_component, depth=0):
                try:
                    inner = drawing_component.Component
                    if inner is not None:
                        placed[str(drawing_component.Name)] = [float(v) for v in value(inner.Transform2, "ArrayData")]
                except Exception:
                    pass
                if depth < 12:
                    for child in (value(drawing_component, "GetChildren") or []):
                        walk(child, depth + 1)

            try:
                walk(view.RootDrawingComponent)
            except Exception as e:
                logger.debug(f"Drawing components: {e}")
            for comp in (value(view, "GetVisibleComponents") or []):
                label = str(comp.Name2)                  # a drawing names them "<assembly>-1/<component>"
                sources.append((comp, placed.get(label), label.split("/", 1)[1] if "/" in label else label))
        except Exception as e:
            logger.debug(f"Visible components: {e}")
    else:
        sources.append((null_object(), None, None))

    edges, points = [], {}
    for comp, transform, comp_name in sources:
        try:
            found = view.GetVisibleEntities2(comp, 1) or []
        except Exception as e:
            logger.debug(f"Visible edges: {e}")
            found = []
        for edge in found:
            try:
                curve = value(edge, "GetCurve")
                params = [float(v) for v in value(edge, "GetCurveParams2")]
                start = to_view(frame, _apply(transform, params[0:3]))
                end = to_view(frame, _apply(transform, params[3:6]))
                row = {"_obj": edge, "start": start, "end": end}
                if comp_name:
                    row["component"] = comp_name
                closed = math.dist(params[0:3], params[3:6]) < 1e-9
                if bool(value(curve, "IsLine")):
                    row["kind"], row["length"] = "line", math.dist(params[0:3], params[3:6])
                elif bool(value(curve, "IsCircle")):
                    circle = [float(v) for v in value(curve, "CircleParams")]
                    row["kind"] = "circle" if closed else "arc"
                    row["center"] = to_view(frame, _apply(transform, circle[0:3]))
                    row["radius"] = circle[6]
                else:
                    row["kind"] = "curve"
                if not closed:
                    for which, getter in (("start", "GetStartVertex"), ("end", "GetEndVertex")):
                        try:
                            vertex = value(edge, getter)
                        except Exception:
                            vertex = None
                        if vertex is not None:
                            key = (round(row[which][0] * 1e6), round(row[which][1] * 1e6))
                            points.setdefault(key, {"_obj": vertex, "at": row[which]})
                edges.append(row)
            except Exception as e:
                logger.debug(f"Edge skipped: {e}")

    def edge_key(row):
        ref = row.get("center") or ((row["start"][0] + row["end"][0]) / 2, (row["start"][1] + row["end"][1]) / 2)
        return (round(ref[0] * 1e5), round(ref[1] * 1e5), row["kind"], round(row.get("radius", row.get("length", 0)) * 1e5),
                row.get("component") or "")

    edges.sort(key=edge_key)
    for i, row in enumerate(edges):
        row["id"] = f"E{i + 1}"
    point_rows = [points[k] for k in sorted(points)]
    for i, row in enumerate(point_rows):
        row["id"] = f"P{i + 1}"
    return edges, point_rows


def edge_public(row: Dict) -> Dict:
    out = {"id": row["id"], "kind": row["kind"]}
    if row["kind"] == "circle":
        out["center"] = mm(row["center"])
        out["diameter"] = round(row["radius"] * 2000, 3)
    else:
        out["start"], out["end"] = mm(row["start"]), mm(row["end"])
        if row["kind"] == "arc":
            out["center"] = mm(row["center"])
            out["radius"] = round(row["radius"] * 1000, 3)
        elif row["kind"] == "line":
            out["length"] = round(row["length"] * 1000, 3)
    if row.get("component"):
        out["component"] = row["component"]
    return out


def dimension_rows(view, include_unreadable: bool = False) -> List[Dict]:
    """The dimensions shown in a view. With include_unreadable, one that cannot be read is still listed as
    {name, unreadable: true, dangling} (it has no value or position, so tools that move texts leave it out)."""
    rows = []
    try:
        shown = value(view, "GetDisplayDimensions") or []
    except Exception:
        shown = []
    for display in shown:
        try:
            dim = display.GetDimension2(0)
            kind = DIM_KINDS.get(int(display.Type2), "other")
            raw = float(dim.SystemValue)
            row = {"name": str(dim.FullName).split("@")[0], "kind": kind,
                   "value": round(math.degrees(raw), 4) if kind == "angular" else round(raw * 1000, 4),
                   "unit": "deg" if kind == "angular" else "mm", "driven": bool(int(dim.DrivenState) == 1),
                   "_obj": display}
            position = [float(v) for v in value(value(display, "GetAnnotation"), "GetPosition")]
            row["at_sheet"] = [round(position[0] * 1000, 2), round(position[1] * 1000, 2)]
            rows.append(row)
        except Exception as e:
            logger.debug(f"Dimension skipped: {e}")
            if include_unreadable:
                try:
                    name = str(display.GetDimension2(0).FullName).split("@")[0]
                except Exception:
                    name = "?"
                try:
                    dangling = bool(value(value(display, "GetAnnotation"), "IsDangling"))
                except Exception:
                    dangling = True
                rows.append({"name": name, "unreadable": True, "dangling": dangling, "_obj": display})
    return rows


def attached_ids(app, display, entities: List[Dict]) -> List[str]:
    """The ids (E3, P2, ...) of the view entities a drawing dimension is attached to, from `entities` (view_entities'
    edges and points); an attached entity that is not among them is left out."""
    try:
        attached = list(value(value(display, "GetAnnotation"), "GetAttachedEntities3") or [])
    except Exception as e:
        logger.debug(f"Attached entities: {e}")
        return []
    ids = []
    for obj in attached:
        for row in entities:
            try:
                if int(app.IsSame(obj, row["_obj"])) == 1:
                    ids.append(row["id"])
                    break
            except Exception:
                pass
    return ids


def view_row(view, sheet_w: float, sheet_h: float) -> Dict:
    row = {"name": str(view.Name), "type": VIEW_TYPES.get(int(view.Type), "other")}
    try:
        outline = [float(v) for v in value(view, "GetOutline")]
        row["on_sheet_mm"] = [round(v * 1000, 1) for v in outline]
        row["inside_sheet"] = outline[0] >= 0 and outline[1] >= 0 and outline[2] <= sheet_w and outline[3] <= sheet_h
    except Exception:
        pass
    try:
        row["scale"] = scale_text(float(view.ScaleDecimal))
    except Exception:
        pass
    try:
        base = value(view, "GetBaseView")
        if base is not None:
            row["from_view"] = str(base.Name)
    except Exception:
        pass
    try:
        if int(view.Type) == 2:
            row["label"] = str(value(value(view, "GetSection"), "GetLabel"))
        elif int(view.Type) == 3:
            row["label"] = str(value(value(view, "GetDetail"), "GetLabel"))
    except Exception:
        pass
    _, kind, path = view_model(view)
    if path:
        row["model"] = os.path.basename(path)
    try:
        row["dimensions"] = int(value(view, "GetDisplayDimensionCount"))
    except Exception:
        pass
    return row


def view_boxes(drawing, skip: str = None) -> List[Tuple[str, List[float]]]:
    boxes = []
    for view in views(drawing):
        if skip and str(view.Name) == skip:
            continue
        try:
            boxes.append((str(view.Name), [float(v) for v in value(view, "GetOutline")]))
        except Exception:
            pass
    return boxes


def overlaps(box, others, slack: float = 0.0005) -> List[str]:
    return [name for name, o in others
            if box[0] < o[2] - slack and o[0] < box[2] - slack and box[1] < o[3] - slack and o[1] < box[3] - slack]


def free_spot(size: Tuple[float, float], others, sheet_w: float, sheet_h: float, near: Tuple[float, float],
              keep_free=(), along: str = None, gap: float = 0.005, margin: float = 0.005,
              step: float = 0.002) -> Optional[Tuple[float, float]]:
    """The centre (m) nearest `near` for a box of `size` (w, h) that stays `margin` inside the sheet and `gap` clear
    of every box in `others` ((name, box) pairs) and `keep_free` (plain boxes). along "x" keeps near's x (a view
    aligned above or below its parent), "y" keeps near's y (beside it). None when nothing fits."""
    w, h = size
    blocked = [o for _, o in others] + list(keep_free)
    lo_x, hi_x = margin + w / 2.0, sheet_w - margin - w / 2.0
    lo_y, hi_y = margin + h / 2.0, sheet_h - margin - h / 2.0
    if lo_x > hi_x or lo_y > hi_y:
        return None

    def axis(lo, hi, fixed):
        if fixed is not None:
            return [fixed] if lo - 1e-9 <= fixed <= hi + 1e-9 else []
        count = int((hi - lo) / step)
        return [lo + i * step for i in range(count + 1)] + [hi]

    best = None
    for x in axis(lo_x, hi_x, near[0] if along == "x" else None):
        for y in axis(lo_y, hi_y, near[1] if along == "y" else None):
            box = (x - w / 2.0 - gap, y - h / 2.0 - gap, x + w / 2.0 + gap, y + h / 2.0 + gap)
            if any(box[0] < o[2] and o[0] < box[2] and box[1] < o[3] and o[1] < box[3] for o in blocked):
                continue
            d = math.hypot(x - near[0], y - near[1])
            if best is None or d < best[0]:
                best = (d, (x, y))
    return best[1] if best else None


def used_labels(drawing) -> set:
    used = set()
    for view in views(drawing):
        try:
            if int(view.Type) == 2:
                used.add(str(value(value(view, "GetSection"), "GetLabel")).upper())
            elif int(view.Type) == 3:
                used.add(str(value(value(view, "GetDetail"), "GetLabel")).upper())
        except Exception:
            pass
    return used


def pick_label(drawing, label) -> str:
    """The section / detail letter: the one given, or the next free one; or a refusal."""
    used = used_labels(drawing)
    if label is None or (isinstance(label, str) and not label.strip()):
        for letter in "ABCDEFGHJKLMNPQRSTUVWXYZ":
            if letter not in used:
                return letter
        raise ToolError("No free view letter is left; give a label.", Err.INPUT)
    if not isinstance(label, str) or len(label.strip()) > 3 or not label.strip().isalnum():
        raise ToolError("label must be one to three letters or digits, e.g. A", Err.INPUT)
    text = label.strip().upper()
    if text in used:
        raise ToolError(f"The label {text} is already used by another view.", Err.INPUT)
    return text


def sheet_place(drawing, x, y, default: Tuple[float, float]) -> Tuple[float, float]:
    """A sheet position in m from optional mm inputs, or a refusal."""
    width, height, _ = sheet_size(drawing)
    if (x is None) != (y is None):
        raise ToolError("Give both x_sheet and y_sheet, or neither.", Err.INPUT)
    if x is None:
        return default
    try:
        px, py = number(x, "x_sheet", 0, width * 1000), number(y, "y_sheet", 0, height * 1000)
    except ToolError as e:
        raise ToolError(f"{e.message} (the sheet is {width * 1000:g} x {height * 1000:g} mm)", Err.INPUT)
    return px / 1000.0, py / 1000.0


def assembly_view(drawing, view, tool: str, prefer_isometric: bool = False):
    """A view of an assembly: the one named, else the first (or the isometric one); or a refusal."""
    if view is not None and not (isinstance(view, str) and not view.strip()):
        target = find_view(drawing, view, tool)
        if view_model(target)[1] != 2:
            raise ToolError(f"{target.Name} shows a part, not an assembly. {tool} needs a view of an assembly.",
                            Err.INPUT)
        return target
    candidates = [v for v in views(drawing) if view_model(v)[1] == 2]
    if not candidates:
        raise ToolError(f"{tool} needs a drawing of an assembly; this drawing has no view of one.", Err.INPUT)
    if prefer_isometric:
        for candidate in candidates:
            try:
                if "isometric" in str(value(candidate, "GetOrientationName")).lower():
                    return candidate
            except Exception:
                pass
    return candidates[0]


# ---------------------------------------------------------------- tables and balloons

def tables(drawing) -> List[Tuple[str, object]]:
    """[(feature type, table annotation)] for the general and BOM tables of the drawing."""
    found = []

    def take(feature):
        kind = str(value(feature, "GetTypeName2"))
        if kind in ("GeneralTableFeature", "BomFeat"):
            for table in (value(value(feature, "GetSpecificFeature2"), "GetTableAnnotations") or []):
                found.append((kind, table))

    try:
        feature = value(drawing, "FirstFeature")
    except Exception:
        feature = None
    guard = 0
    while feature is not None and guard < 2000:
        guard += 1
        try:
            take(feature)
        except Exception as e:
            logger.debug(f"Table feature: {e}")
        try:
            sub = value(feature, "GetFirstSubFeature")
        except Exception:
            sub = None
        while sub is not None and guard < 2000:
            guard += 1
            try:
                take(sub)
            except Exception as e:
                logger.debug(f"Table sub-feature: {e}")
            try:
                sub = value(sub, "GetNextSubFeature")
            except Exception:
                sub = None
        try:
            feature = value(feature, "GetNextFeature")
        except Exception:
            feature = None
    return found


def cell(table, row: int, column: int) -> str:
    try:
        return str(table.Text(row, column) or "")
    except Exception:
        return ""


def table_rows(table) -> List[List[str]]:
    return [[cell(table, r, c) for c in range(int(table.ColumnCount))] for r in range(int(table.RowCount))]


def table_box(table) -> Optional[List[float]]:
    """Outline of a table on the sheet (m), from its anchor corner and its size."""
    try:
        position = [float(v) for v in value(value(table, "GetAnnotation"), "GetPosition")]
        width = sum(float(table.GetColumnWidth(c)) for c in range(int(table.ColumnCount)))
        height = sum(float(table.GetRowHeight(r)) for r in range(int(table.RowCount)))
        anchor = int(table.AnchorType)
        x0 = position[0] if anchor in (1, 3) else position[0] - width
        y0 = position[1] - height if anchor in (1, 2) else position[1]
        return [x0, y0, x0 + width, y0 + height]
    except Exception as e:
        logger.debug(f"Table outline: {e}")
        return None


def title_table(drawing):
    """The title block of the CURRENT sheet, or None. (Searching the whole drawing found sheet 1's block
    from any sheet, so sheets 2 and 3 overwrote it.)"""
    try:
        on_sheet = list(value(value(drawing, "GetFirstView"), "GetTableAnnotations") or [])
    except Exception as e:
        logger.debug(f"Sheet tables: {e}")
        on_sheet = []
    for table in on_sheet:
        try:
            if (int(table.ColumnCount) == 2 and int(table.RowCount) == len(TITLE_ROWS)
                    and cell(table, 0, 0).strip().upper() == TITLE_ROWS[0][1]):
                return table
        except Exception:
            pass
    return None


def balloons(view) -> List[Dict]:
    rows = []
    try:
        notes = value(view, "GetNotes") or []
    except Exception:
        notes = []
    for note in notes:
        try:
            if not bool(value(note, "IsBomBalloon")):
                continue
            position = [float(v) for v in value(value(note, "GetAnnotation"), "GetPosition")]
            rows.append({"item": str(note.GetBomBalloonText(True)),
                         "at_sheet": [round(position[0] * 1000, 1), round(position[1] * 1000, 1)]})
        except Exception as e:
            logger.debug(f"Balloon skipped: {e}")
    rows.sort(key=lambda r: (len(r["item"]), r["item"]))
    return rows
