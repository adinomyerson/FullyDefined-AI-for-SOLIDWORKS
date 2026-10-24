"""
Sheet metal: base flange, convert a solid, read the settings.

API facts, measured on SolidWorks 2026 SP4.1:
- IFeatureManager.InsertSheetMetalBaseFlange2 only builds the first flange with Merge, UseFeatScope
  and UseAutoSelect all False. With any of them True it returns Nothing.
- The K-factor is not an argument. It travels in an ICustomBendAllowance
  (FeatureManager.CreateCustomBendAllowance, Type = 2 for "K-factor"), passed as PCBA. Setting it
  afterwards through the feature data does not take.
- The bend count is the number of "SketchBend" sub-features of the base flange.
- The sketch is the OUTSIDE of the part; the material is built inward.
"""

import logging
import math
import os
import shutil
import tempfile

from ..core.api import (
    doc_path, doc_title, document, Err, geometry, model, ok, sketch as sk, sw, tool, ToolError, units, value)

logger = logging.getLogger(__name__)

K_FACTOR_TYPE = 2       # ICustomBendAllowance.Type value for "K-factor" (checked by measuring the flat pattern)
_POINT = {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}},
          "required": ["x", "y", "z"]}


def _number(v, label):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ToolError(f"{label} must be a number", Err.INPUT)


def _check_k_factor(k):
    if isinstance(k, bool) or not isinstance(k, (int, float)) or not math.isfinite(k) or not (0.0 <= k <= 1.0):
        raise ToolError("k_factor must be a number from 0 to 1", Err.INPUT)


# ---------------------------------------------------------------- reading a sheet metal part

def _sm_features(doc) -> dict:
    """Names of the sheet metal features in the tree (None when absent)."""
    found = {"sheet_metal": None, "base_flange": None, "flat_pattern": None}
    for name, kind, _feat in model.tree_features(doc):
        if kind == "SheetMetal" and found["sheet_metal"] is None:
            found["sheet_metal"] = name
        elif kind == "SMBaseFlange" and found["base_flange"] is None:
            found["base_flange"] = name
        elif kind == "FlatPattern" and found["flat_pattern"] is None:
            found["flat_pattern"] = name
    return found


def _sm_settings(doc) -> dict:
    """Thickness, bend radius and K-factor (metres / number) read from the Sheet-Metal feature."""
    names = _sm_features(doc)
    out = {"thickness_m": None, "bend_radius_m": None, "k_factor": None, "allowance_type": None}
    if names["sheet_metal"] is None:
        return out
    try:
        data = value(doc.FeatureByName(names["sheet_metal"]), "GetDefinition")
        out["thickness_m"] = float(value(data, "Thickness"))
        out["bend_radius_m"] = float(value(data, "BendRadius"))
        out["k_factor"] = float(value(data, "KFactor"))
        out["allowance_type"] = int(value(data, "BendAllowanceType"))
    except Exception as e:
        logger.debug(f"Could not read the sheet metal settings: {e}")
    return out


def _sm_bend_count(doc):
    """Number of bends: the SketchBend sub-features of the base flange."""
    names = _sm_features(doc)
    if names["base_flange"] is None:
        return None
    try:
        count, guard = 0, 0
        sub = value(doc.FeatureByName(names["base_flange"]), "GetFirstSubFeature")
        while sub is not None and guard < 500:
            guard += 1
            if value(sub, "GetTypeName2") == "SketchBend":
                count += 1
            sub = value(sub, "GetNextSubFeature")
        return count
    except Exception as e:
        logger.debug(f"Could not count the bends: {e}")
        return None


def _point_key(point) -> tuple:
    return (round(float(point.X) * 1e6), round(float(point.Y) * 1e6), round(float(point.Z) * 1e6))


def _profile_kind(doc, sketch_name: str) -> tuple:
    """Look at a sketch and say what it is. Returns (kind, detail): kind is "closed" (one or more
    closed loops), "open" (one open chain) or "error" (detail is the reason). For the first two,
    detail is the number of non-construction segments."""
    try:
        sketch = value(doc.FeatureByName(sketch_name), "GetSpecificFeature2")
        segments = list(value(sketch, "GetSketchSegments") or [])
    except Exception as e:
        return "error", f"could not read {sketch_name}: {e}"

    degree, parent = {}, {}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(a, b):
        parent.setdefault(a, a)
        parent.setdefault(b, b)
        parent[find(a)] = find(b)

    count = circles = 0
    for seg in segments:
        try:
            if bool(value(seg, "ConstructionGeometry")):
                continue
            kind = int(value(seg, "GetType"))
        except Exception:
            continue
        count += 1
        if kind == 1:
            try:
                if int(value(seg, "IsCircle")) == 1:
                    circles += 1
                    continue
            except Exception:
                pass
        try:
            a, b = _point_key(value(seg, "GetStartPoint2")), _point_key(value(seg, "GetEndPoint2"))
        except Exception:
            circles += 1                    # a closed ellipse has no end points: it counts as a closed loop
            continue
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1
        union(a, b)

    if count == 0:
        return "error", f"{sketch_name} has no geometry (construction lines do not count)"
    ends = [key for key, deg in degree.items() if deg == 1]
    if [key for key, deg in degree.items() if deg > 2]:
        return "error", (f"{sketch_name} has lines that meet in a point of three or more lines; "
                         f"a profile must be a single chain or closed loops")
    if not ends:
        return "closed", count
    components = {find(key) for key in degree}
    if len(ends) == 2 and len(components) == 1 and circles == 0:
        return "open", count
    return "error", (f"{sketch_name} mixes open and closed geometry, or has more than one open chain. "
                     f"Use either closed loops (a plate) or one open chain (a bent profile)")


def _flat_pattern_feature(doc):
    name = _sm_features(doc)["flat_pattern"]
    return doc.FeatureByName(name) if name else None


def _body_extents_mm(doc):
    """(number of solid bodies, their joint bounding box sizes in mm, largest first)."""
    boxes = [[float(v) for v in value(body, "GetBodyBox")] for body in geometry.solid_bodies(doc)]
    if not boxes:
        return 0, None
    lo = [min(b[i] for b in boxes) for i in range(3)]
    hi = [max(b[i + 3] for b in boxes) for i in range(3)]
    return len(boxes), sorted([round((hi[i] - lo[i]) * 1000.0, 4) for i in range(3)], reverse=True)


def _measure_flat(doc):
    """Show the flat pattern for a moment, measure it, put the suppression state back.
    Returns {"pieces", "size_mm" (largest first), "was_flat"}, or None when there is no flat pattern."""
    feat = _flat_pattern_feature(doc)
    if feat is None:
        return None
    was_suppressed = bool(value(feat, "IsSuppressed"))
    try:
        if was_suppressed:
            sw.sheet_metal.sw_show_flat_pattern(feat, True)     # unsuppress: show the flat pattern
        pieces, size = _body_extents_mm(doc)
    finally:
        if was_suppressed:
            sw.sheet_metal.sw_show_flat_pattern(feat, False)    # suppress again: back to folded
    return {"pieces": pieces, "size_mm": size, "was_flat": not was_suppressed}


def _apply_k_factor(doc, k_factor: float) -> bool:
    """Set the K-factor through the Sheet-Metal feature definition (checked by measuring the flat pattern)."""
    name = _sm_features(doc)["sheet_metal"]
    if name is None:
        return False
    return sw.sheet_metal.sw_set_k_factor(doc, doc.FeatureByName(name), k_factor, K_FACTOR_TYPE)


def _feature_errors(doc, known_names):
    """Text for the first new feature that does not rebuild, else None."""
    for name, _kind, feat in model.tree_features(doc):
        if name in known_names:
            continue
        try:
            code = value(feat, "GetErrorCode2")
            if isinstance(code, (tuple, list)):
                code = code[0]
            if code:
                return f"{name} fails to rebuild (SolidWorks error code {int(code)})"
        except Exception:
            pass
    return None


def _pick_edges(doc, points, unit, label) -> list:
    """The edge nearest to each point."""
    try:
        pts = geometry.points_to_m(points, unit)
    except (ValueError, TypeError) as e:
        raise ToolError(f"{label}: {e}", Err.INPUT)
    if not pts:
        raise ToolError(f"{label} is required: give points on the edges", Err.INPUT)
    edges = geometry.all_edges(doc)
    chosen, used = [], set()
    for p in pts:
        if not edges:
            raise ToolError("The model has no solid body edges", Err.SELECTION)
        dist, index = min((geometry.distance_to(e, p), i) for i, e in enumerate(edges))
        if dist > geometry.PICK_TOLERANCE_M:
            raise ToolError(f"{label}: no edge within 0.5 mm of {geometry.point_in_unit(p, unit)} "
                            f"(nearest is {dist * 1000:.3f} mm away)", Err.SELECTION)
        if index not in used:
            used.add(index)
            chosen.append(edges[index])
    return chosen


def _conv(ctx, m, unit):
    return round(ctx.from_m(m, unit), 6) if m is not None else None


def _set_flat(ctx, flat: bool, unit):
    doc = ctx.doc
    if _sm_features(doc)["sheet_metal"] is None:
        raise ToolError("This part is not sheet metal.", Err.INPUT)
    if doc.SketchManager.ActiveSketch is not None:
        raise ToolError("A sketch is open. Close it first (close_sketch).", Err.SKETCH)
    feat = _flat_pattern_feature(doc)
    if feat is None:
        raise ToolError("This sheet metal part has no flat pattern feature.", Err.INPUT)
    was_flat = not bool(value(feat, "IsSuppressed"))
    if was_flat != flat:
        sw.sheet_metal.sw_show_flat_pattern(feat, flat)
        model.rebuild(doc)
    now_flat = not bool(value(feat, "IsSuppressed"))
    if now_flat != flat:
        raise ToolError(f"SolidWorks did not {'flatten' if flat else 'fold'} the part.", Err.FEATURE)
    pieces, size = _body_extents_mm(doc)
    data = {"state": "flat" if flat else "folded", "changed": was_flat != flat, "pieces": pieces,
            "size": [_conv(ctx, v / 1000.0, unit) for v in size] if size else None, "unit": units.label(unit)}
    word = "Flattened" if flat else "Folded"
    if not data["changed"]:
        word = "Already flat" if flat else "Already folded"
    return ok(f"{word}: {data['size']}{data['unit']} (largest first)" + (
        "; the document is marked modified" if data["changed"] else ""), data)


@tool(
    group="sheetmetal",
    description="Show the flat pattern of the active sheet metal part (unsuppress it). Returns the flat size, largest first. The document is marked modified. unflatten folds it up again.",
    params={"unit": {"type": "string", "description": "mm, cm, m, inch or ft"}},
    needs="part",
)
def flatten(ctx, unit=None):
    return _set_flat(ctx, True, unit)


@tool(
    group="sheetmetal",
    description="Fold the active sheet metal part again (suppress its flat pattern). Returns the folded size, largest first.",
    params={"unit": {"type": "string", "description": "mm, cm, m, inch or ft"}},
    needs="part",
)
def unflatten(ctx, unit=None):
    return _set_flat(ctx, False, unit)


# DXF header $INSUNITS (code 70) -> the unit the file's coordinates are in
_DXF_UNITS = {1: "inch", 2: "ft", 4: "mm", 5: "cm", 6: "m"}


def _dxf_unit(path, fallback=None):
    """The length unit a DXF file is written in, from its $INSUNITS header value (what a reader of the file
    sees); `fallback` when the file does not say."""
    try:
        with open(path, encoding="latin-1") as f:
            lines = [line.strip() for line in f.read().splitlines()]
        at = lines.index("$INSUNITS")
        return _DXF_UNITS.get(int(lines[at + 2]), fallback)
    except (OSError, ValueError, IndexError):
        return fallback


BEND_LAYER = "BEND"


def _dxf_lines(raw: list) -> list:
    """[(index of the LINE's layer value line, rounded end points)] for the LINE entities of a DXF (raw: its lines,
    code and value alternating)."""
    pairs = [(raw[i].strip(), i + 1) for i in range(0, len(raw) - 1, 2)]
    out, in_entities, current = [], False, None
    for code, at in pairs:
        val = raw[at].strip()
        if code == "0":
            if current and current[0] is not None:
                out.append((current[0], tuple(sorted([(current[1].get("10"), current[1].get("20")),
                                                      (current[1].get("11"), current[1].get("21"))]))))
            current = [None, {}] if in_entities and val == "LINE" else None
            if val == "ENDSEC":
                in_entities = False
        elif code == "2" and val == "ENTITIES":
            in_entities = True
        elif current is not None:
            if code == "8":
                current[0] = at
            elif code in ("10", "20", "11", "21"):
                current[1][code] = round(float(val), 6)
    return out


def _final_dxf(with_bends: str, without: str) -> tuple:
    """The file to write from the two exports: the bend lines on layer BEND; when they cannot be moved there, the
    export without them (a cutter must never get them on layer 0). Returns (text, lines moved; -1 = left out)."""
    text, moved = _bend_layer(with_bends, without)
    if moved:
        return text, moved
    if len(_dxf_lines(with_bends.splitlines(keepends=True))) != len(_dxf_lines(without.splitlines(keepends=True))):
        return without, -1
    return with_bends, 0


def _bend_layer(with_bends: str, without: str) -> tuple:
    """Put the bend lines of a flat-pattern DXF on their own layer: the LINEs of the export with bend lines that the
    export without them lacks go to layer BEND, which is added to the layer table (a new handle from $HANDSEED).
    SolidWorks puts everything on layer 0, where a laser or water-jet program would cut along the bends. Returns (the
    new text, how many lines moved); the text unchanged when there are none or the file is not as expected."""
    raw = with_bends.splitlines(keepends=True)
    plain = {ends for _at, ends in _dxf_lines(without.splitlines(keepends=True))}
    bends = [at for at, ends in _dxf_lines(raw) if ends not in plain]
    if not bends:
        return with_bends, 0
    strip = [line.strip() for line in raw]
    eol = raw[0][len(raw[0].rstrip("\r\n")):] or "\n"

    def line(code, val):
        return [f"{code:>3}{eol}", f"{val}{eol}"]
    try:
        seed_at = strip.index("$HANDSEED") + 2
        handle = int(strip[seed_at], 16)
        table = next(i for i in range(1, len(strip) - 1, 2) if strip[i - 1] == "0" and strip[i] == "TABLE"
                     and strip[i + 1] == "2" and strip[i + 2] == "LAYER")
        owner = strip[table + 4] if strip[table + 3] == "5" else "0"
        count_at = next(i for i in range(table + 3, len(strip), 2) if strip[i] == "70") + 1
        end = next(i for i in range(table + 1, len(strip) - 1, 2) if strip[i] == "0" and strip[i + 1] == "ENDTAB")
        plot_style = next((strip[i + 1] for i in range(table + 1, end, 2) if strip[i] == "390"), None)
        have_layer = any(strip[i] == "2" and strip[i + 1] == BEND_LAYER for i in range(table + 1, end, 2))
    except (ValueError, StopIteration, IndexError):
        return with_bends, 0
    for at in bends:
        raw[at] = BEND_LAYER + eol
    if have_layer:                                      # already in the table (a layer map, say): only the lines move
        return "".join(raw), len(bends)
    record = (line(0, "LAYER") + line(5, f"{handle:X}") + line(330, owner) + line(100, "AcDbSymbolTableRecord")
              + line(100, "AcDbLayerTableRecord") + line(2, BEND_LAYER) + line(70, 0) + line(62, 1)
              + line(6, "Continuous") + line(370, -3) + (line(390, plot_style) if plot_style else []))
    raw[end:end] = record                               # before the layer table's ENDTAB; earlier indexes stay
    raw[count_at] = f"{int(strip[count_at]) + 1}{eol}"
    raw[seed_at] = f"{handle + 1:X}{eol}"
    return "".join(raw), len(bends)


@tool(
    group="sheetmetal",
    description="Export the active sheet metal part's flat pattern as a DXF: outline and holes on layer 0, bend lines on their own layer BEND (so a cutting program can leave them out), bend notes as text. Forming tools are not drawn (a dimple leaves only the outline). The file is in the document's length unit (inches from an inch template; the file's $INSUNITS says which) unless dxf_unit asks for mm or inch: then the document's unit is switched for the export and put back. The part must be saved, and an existing file needs overwrite. Returns path, bytes, flat size, dxf_unit (the unit of the file's coordinates) and bend_lines (how many are on layer BEND). SolidWorks marks the part modified.",
    params={"filepath": {"type": "string", "description": "Where to write it, ending in .dxf"},
            "overwrite": {"type": "boolean", "default": False, "description": "Replace an existing file"},
            "unit": {"type": "string", "description": "Unit of the flat size (mm, cm, m, inch or ft)"},
            "dxf_unit": {"type": "string", "enum": ["mm", "inch"],
                         "description": "Unit of the DXF file's coordinates (default: the document's length unit)"}},
    required=["filepath"],
    needs="part",
    writes=("filepath",),
)
def export_flat_pattern_dxf(ctx, filepath, overwrite=False, unit=None, dxf_unit=None):
    doc = ctx.doc
    if not isinstance(overwrite, bool):
        raise ToolError("overwrite must be true or false", Err.INPUT)
    if dxf_unit is not None and dxf_unit not in ("mm", "inch"):
        raise ToolError(f"dxf_unit must be mm or inch, not {dxf_unit!r}", Err.INPUT)
    if not isinstance(filepath, str) or not filepath.strip().lower().endswith(".dxf"):
        raise ToolError("filepath must be a file path ending in .dxf", Err.INPUT)
    path = os.path.abspath(filepath.strip())
    if _sm_features(doc)["sheet_metal"] is None:
        raise ToolError("This part is not sheet metal: it has no flat pattern.", Err.INPUT)
    if _flat_pattern_feature(doc) is None:
        raise ToolError("This sheet metal part has no flat pattern feature.", Err.INPUT)
    model_path = doc_path(doc)
    if not model_path:
        raise ToolError("Save the part first (save_document): the DXF export works from the saved file.", Err.SAVE)
    if os.path.isdir(path):
        raise ToolError(f"filepath is a folder: {path}", Err.INPUT)
    existed = os.path.exists(path)
    if existed and not overwrite:
        raise ToolError(f"Nothing was written: {path} already exists. Choose another name, or call again with "
                        f"overwrite: true.", Err.SAVE, {"path": path})
    folder = os.path.dirname(path)
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as e:
        raise ToolError(f"Cannot create the folder {folder} ({e.strerror or e})", Err.SAVE)
    switch = {"changed": False, "restored": True}
    # twice, into a scratch folder: with the bend lines, and without them to tell them from the outline (they go on
    # their own layer). The file is written only from the pair, so bend lines never reach it on layer 0.
    scratch = tempfile.mkdtemp(prefix="swmcp_dxf_")
    bends_path, plain_path = os.path.join(scratch, "bends.dxf"), os.path.join(scratch, "plain.dxf")
    try:
        try:
            if dxf_unit is None:
                sw.sheet_metal.sw_export_flat_dxf(doc, bends_path, model_path, bend_lines=True)
                sw.sheet_metal.sw_export_flat_dxf(doc, plain_path, model_path, bend_lines=False)
            else:
                with document.length_unit_during(doc, dxf_unit) as switch:
                    sw.sheet_metal.sw_export_flat_dxf(doc, bends_path, model_path, bend_lines=True)
                    sw.sheet_metal.sw_export_flat_dxf(doc, plain_path, model_path, bend_lines=False)
        except Exception as e:
            logger.debug(f"Flat pattern DXF: {e}")
        unit_note = "" if switch["restored"] else (" WARNING: the document's length unit could not be put back; "
                                                   "check Tools > Options > Document Properties > Units.")
        texts = []
        for p in (bends_path, plain_path):
            if os.path.isfile(p):
                with open(p, encoding="latin-1", newline="") as f:
                    texts.append(f.read())
        if len(texts) != 2:
            raise ToolError("SolidWorks could not export the flat pattern (both with and without its bend lines, "
                            "to put them on their own layer). Nothing was written." + unit_note, Err.SAVE)
        text, moved = _final_dxf(*texts)
        try:
            with open(path, "w", encoding="latin-1", newline="") as f:
                f.write(text)
        except OSError as e:
            raise ToolError(f"Cannot write {path} ({e.strerror or e})", Err.SAVE)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    got = _dxf_unit(path)
    if dxf_unit is not None and got != dxf_unit:
        raise ToolError(f"SolidWorks wrote {path} in {got or 'an unknown unit'}, not {dxf_unit}." + unit_note,
                        Err.EXPORT, {"path": path, "dxf_unit": got})
    flat = _measure_flat(doc)
    data = {"path": path, "bytes": os.path.getsize(path), "replaced": existed, "unit": units.label(unit),
            "dxf_unit": got, "bend_lines": max(moved, 0), "bend_layer": BEND_LAYER if moved > 0 else None}
    if moved < 0:
        unit_note += " WARNING: the bend lines could not be put on their own layer, so they were left out."
    if not switch["restored"]:
        data["document_unit_restored"] = False
    if flat and flat["size_mm"]:
        data["flat_size"] = [_conv(ctx, v / 1000.0, unit) for v in flat["size_mm"]]
    bends = f"; {moved} bend line{'' if moved == 1 else 's'} on layer {BEND_LAYER}" if moved > 0 else ""
    return ok(f"Flat pattern written to {path} ({data['bytes']} bytes, coordinates in {got or 'the document unit'})"
              + bends
              + (f"; flat size {data['flat_size']}{data['unit']}" if "flat_size" in data else "") + unit_note, data)


def _sketch_points_in_model(sketch):
    """(segment end points in model metres, sketch normal) of a sketch's non-construction segments."""
    a = [float(v) for v in value(sketch.ModelToSketchTransform, "ArrayData")]
    rot, move = (a[0:3], a[3:6], a[6:9]), a[9:12]
    origin = [-(move[0] * rot[i][0] + move[1] * rot[i][1] + move[2] * rot[i][2]) for i in range(3)]
    points = []
    for seg in value(sketch, "GetSketchSegments") or []:
        if bool(value(seg, "ConstructionGeometry")):
            continue
        for getter in ("GetStartPoint2", "GetEndPoint2"):
            try:
                p = value(seg, getter)
                q = (float(p.X), float(p.Y), float(p.Z))
                points.append(tuple(origin[i] + q[0] * rot[i][0] + q[1] * rot[i][1] + q[2] * rot[i][2] for i in range(3)))
            except Exception:
                pass
    return points, tuple(rot[i][2] for i in range(3))


@tool(
    group="sheetmetal",
    description="Add a miter flange: an open profile sketch (lines and arcs on a plane across a sheet edge, starting at that edge) swept along a chain of edges, mitred at the corners. edges: a point on each edge of the chain (default: the one edge the profile starts on). Returns the feature and the volume added. Refused while flattened.",
    params={"sketch": {"type": "string", "description": "Name of the open profile sketch"},
            "edges": {"type": "array", "items": _POINT,
                      "description": "A point on each edge of the chain, the profile's edge first (default: that edge alone)"},
            "radius": {"type": "number", "description": "Bend radius (default: the part's)"},
            "gap": {"type": "number", "description": "Rip gap at corners (default 0.1 mm)"},
            "unit": {"type": "string", "description": "mm, cm, m, inch or ft"}},
    required=["sketch"],
    needs="part",
    clears_selection=True,
)
def miter_flange(ctx, sketch, edges=None, radius=None, gap=None, unit=None):
    doc = ctx.doc
    if _sm_features(doc)["sheet_metal"] is None:
        raise ToolError("This part is not sheet metal. Start it with sheet_metal_base_flange or "
                        "convert_to_sheet_metal.", Err.INPUT)
    if doc.SketchManager.ActiveSketch is not None:
        raise ToolError("A sketch is open. Close it first (close_sketch).", Err.SKETCH)
    flat_feat = _flat_pattern_feature(doc)
    if flat_feat is not None and not bool(value(flat_feat, "IsSuppressed")):
        raise ToolError("The part is flattened (its flat pattern is shown). Fold it first.", Err.INPUT)
    radius_m = None
    if radius is not None:
        _number(radius, "radius")
        radius_m = ctx.to_m(radius, unit)
        if radius_m <= 0:
            raise ToolError("radius must be larger than 0", Err.INPUT)
    gap_m = 0.0001
    if gap is not None:
        _number(gap, "gap")
        gap_m = ctx.to_m(gap, unit)
        if gap_m <= 0:
            raise ToolError("gap must be larger than 0", Err.INPUT)
    names = model.sketch_names(doc)
    name = model.match_name(sketch, names)
    if name is None:
        raise ToolError(f"No sketch named '{sketch}'. Sketches: {', '.join(names) or 'none'}", Err.INPUT)
    sk.require_defined(doc, name)
    users = model.sketch_users(doc, name)
    if users:
        raise ToolError(f"{name} is already used by {', '.join(users)}. Draw the profile in a new sketch.", Err.INPUT)
    kind, detail = _profile_kind(doc, name)
    if kind != "open":
        raise ToolError(f"A miter flange needs an open profile (one chain of lines and arcs); {name} is "
                        f"{'closed' if kind == 'closed' else detail}.", Err.INPUT)
    sketch_feat = doc.FeatureByName(name)
    points, normal = _sketch_points_in_model(value(sketch_feat, "GetSpecificFeature2"))
    edge = None
    for e in geometry.all_edges(doc):                      # straight, across the sketch plane, through a profile end
        geo = geometry.edge_geometry(e)
        if geo["type"] != "line" or "direction" not in geo:
            continue
        if abs(abs(sum(a * b for a, b in zip(geo["direction"], normal))) - 1.0) > 1e-6:
            continue
        if any(geometry.distance_to(e, p) <= 1e-6 for p in points):
            edge = e
            break
    if edge is None:
        raise ToolError(f"No straight sheet edge runs across the plane of {name} through an end of its profile. "
                        f"Draw the profile on a plane across the edge, starting at the edge.", Err.SELECTION)
    chain = [edge]
    if edges is not None:
        if not isinstance(edges, list) or not edges:
            raise ToolError("edges must be a list of points, one on each edge of the chain", Err.INPUT)
        picked = _pick_edges(doc, edges, unit, "edges")

        def ends(e):
            g = geometry.edge_geometry(e)
            return sorted([tuple(round(c, 9) for c in g["start"]), tuple(round(c, 9) for c in g["end"])])

        same = [c for c in picked if ends(c) == ends(edge)]
        if not same:
            raise ToolError(f"The chain must include the edge the profile of {name} starts on.", Err.SELECTION)
        chain = [edge] + [c for c in picked if ends(c) != ends(edge)]
    known = [row[0] for row in model.tree_features(doc)]
    volume_before = model.volume_mm3(doc)
    feat = None
    try:
        feat = sw.sheet_metal.sw_miter_flange(doc, sketch_feat, chain, radius_m, gap_m)
    except Exception as e:
        logger.debug(f"Miter flange: {e}")
    model.rebuild(doc)
    problem = None if feat is not None else "SolidWorks made no miter flange from that profile"
    problem = problem or _feature_errors(doc, known)
    volume_after = model.volume_mm3(doc)
    if problem is None and volume_before is not None and volume_after is not None and \
            abs(volume_after - volume_before) < 1e-6:
        problem = "the flange added no material"
    if problem:
        model.remove_new_features(doc, known)
        model.rebuild(doc)
        raise ToolError(f"Not created: {problem}.", Err.FEATURE)
    flat = _measure_flat(doc)
    data = {"feature": str(feat.Name), "sketch": name, "edges": len(chain), "unit": units.label(unit),
            "volume_added_mm3": round(volume_after - volume_before, 3) if volume_before is not None else None}
    if flat and flat["size_mm"]:
        data["flat_size"] = [_conv(ctx, v / 1000.0, unit) for v in flat["size_mm"]]
    return ok(f"{feat.Name} from {name}: {data['volume_added_mm3']} mm3 added"
              + (f"; flat size {data['flat_size']}{data['unit']}" if "flat_size" in data else ""), data)


@tool(
    group="sheetmetal",
    description="Add a tab to a sheet metal part: a closed sketch drawn on the sheet's flat face (it may overlap the part) becomes sheet of the part's thickness, merged into the part. Returns the feature and the volume added. Refused while flattened.",
    params={"sketch": {"type": "string", "description": "Name of a closed sketch on a flat face of the sheet"}},
    required=["sketch"],
    needs="part",
    clears_selection=True,
)
def tab(ctx, sketch):
    doc = ctx.doc
    if _sm_features(doc)["sheet_metal"] is None:
        raise ToolError("This part is not sheet metal. Start it with sheet_metal_base_flange or "
                        "convert_to_sheet_metal.", Err.INPUT)
    if doc.SketchManager.ActiveSketch is not None:
        raise ToolError("A sketch is open. Close it first (close_sketch).", Err.SKETCH)
    flat_feat = _flat_pattern_feature(doc)
    if flat_feat is not None and not bool(value(flat_feat, "IsSuppressed")):
        raise ToolError("The part is flattened (its flat pattern is shown). Fold it first.", Err.INPUT)
    names = model.sketch_names(doc)
    name = model.match_name(sketch, names)
    if name is None:
        raise ToolError(f"No sketch named '{sketch}'. Sketches: {', '.join(names) or 'none'}", Err.INPUT)
    sk.require_defined(doc, name)
    users = model.sketch_users(doc, name)
    if users:
        raise ToolError(f"{name} is already used by {', '.join(users)}. Draw the tab in a new sketch.", Err.INPUT)
    kind, detail = _profile_kind(doc, name)
    if kind != "closed":
        raise ToolError(f"A tab needs a closed sketch; {name} is {'open' if kind == 'open' else detail}.", Err.INPUT)
    thickness = _sm_settings(doc)["thickness_m"]
    if not thickness:
        raise ToolError("Could not read the sheet thickness.", Err.INTERNAL)
    sketch_feat = doc.FeatureByName(name)
    known = [row[0] for row in model.tree_features(doc)]
    volume_before = model.volume_mm3(doc)
    extents_before = _body_extents_mm(doc)[1]
    problem, feat = None, None
    for thicken_dir in (False, True):                      # into the sheet: the side depends on the face
        try:
            feat = sw.sheet_metal.sw_tab(doc, sketch_feat, thickness, thicken_dir)
        except Exception as e:
            logger.debug(f"Tab: {e}")
            feat = None
        model.rebuild(doc)
        problem = None if feat is not None else "SolidWorks made no tab from that sketch (does it touch the sheet?)"
        problem = problem or _feature_errors(doc, known)
        if problem is None and model.solid_body_count(doc) != 1:
            problem = "the tab does not touch the part (it made a separate body)"
        if problem is None and extents_before and _body_extents_mm(doc)[1][2] - extents_before[2] > 1e-4:
            problem = "the tab came out on the wrong side of the sheet"
        if problem is None:
            break
        model.remove_new_features(doc, known)
        model.rebuild(doc)
        if "separate body" in problem:
            break
    volume_after = model.volume_mm3(doc)
    if problem is None and volume_before is not None and abs(volume_after - volume_before) < 1e-6:
        problem = "the tab added no material (is the sketch inside the part?)"
        model.remove_new_features(doc, known)
        model.rebuild(doc)
    if problem:
        raise ToolError(f"Not created: {problem}.", Err.FEATURE)
    data = {"feature": str(feat.Name), "sketch": name,
            "volume_added_mm3": round(volume_after - volume_before, 3) if volume_before is not None else None}
    return ok(f"{feat.Name} from {name}: {data['volume_added_mm3']} mm3 added", data)


def _forming_root():
    """The installed forming tools folder, or None."""
    base = os.environ.get("ProgramData", r"C:\ProgramData")
    for year in ("2026", "2025", "2024"):
        folder = os.path.join(base, "SOLIDWORKS", f"SOLIDWORKS {year}", "design library", "forming tools")
        if os.path.isdir(folder):
            return folder
    return None


def _forming_tools(root) -> dict:
    """'category/name' -> path of every .sldftp in the library (names lower case)."""
    found = {}
    for folder, _dirs, files in os.walk(root):
        for name in files:
            if name.lower().endswith(".sldftp"):
                rel = os.path.relpath(os.path.join(folder, name), root)
                key = os.path.splitext(rel)[0].replace("\\", "/").lower()
                found[key] = os.path.join(folder, name)
    return found


@tool(
    group="sheetmetal",
    description="Press a forming tool from the installed design library (e.g. dimple, louvers/louver, lances/...) into a flat face of a sheet metal part at a point. It pushes away from that face; flip presses from the other side, toward it. rotation turns it on the face. An unknown name is refused with the list. Returns the feature and its height. Refused while flattened.",
    params={"tool": {"type": "string", "description": "Library name, e.g. dimple or embosses/dimple"},
            "point": dict(_POINT, description="A point on a flat face of the sheet, where the tool goes"),
            "rotation": {"type": "number", "default": 0, "description": "Rotation on the face in degrees"},
            "flip": {"type": "boolean", "default": False, "description": "Press the other way"},
            "unit": {"type": "string", "description": "mm, cm, m, inch or ft"}},
    required=["tool", "point"],
    needs="part",
    clears_selection=True,
)
def forming_tool(ctx, tool, point, rotation=0, flip=False, unit=None):
    doc = ctx.doc
    if _sm_features(doc)["sheet_metal"] is None:
        raise ToolError("This part is not sheet metal. Start it with sheet_metal_base_flange or "
                        "convert_to_sheet_metal.", Err.INPUT)
    if doc.SketchManager.ActiveSketch is not None:
        raise ToolError("A sketch is open. Close it first (close_sketch).", Err.SKETCH)
    flat_feat = _flat_pattern_feature(doc)
    if flat_feat is not None and not bool(value(flat_feat, "IsSuppressed")):
        raise ToolError("The part is flattened (its flat pattern is shown). Fold it first.", Err.INPUT)
    if not isinstance(flip, bool):
        raise ToolError("flip must be true or false", Err.INPUT)
    _number(rotation, "rotation")
    root = _forming_root()
    if root is None:
        raise ToolError("No forming tool library is installed (design library\\forming tools).", Err.NOT_FOUND)
    library = _forming_tools(root)
    wanted = str(tool or "").strip().replace("\\", "/").lower()
    if wanted.endswith(".sldftp"):
        wanted = wanted[:-7]
    matches = [k for k in library if k == wanted or k.split("/")[-1] == wanted]
    if len(matches) != 1:
        listed = ", ".join(sorted(library))
        why = f"'{tool}' matches {len(matches)} tools: give category/name" if matches else f"No forming tool '{tool}'"
        raise ToolError(f"{why}. Tools: {listed}", Err.INPUT)
    path = library[matches[0]]
    try:
        p = geometry.points_to_m(point, unit)[0]
    except (IndexError, TypeError, ValueError):
        raise ToolError("point must be a point {x, y, z}", Err.INPUT)
    on_face = [f for dist, f in geometry.faces_at_point(doc, *p) if dist <= geometry.FACE_POINT_TOLERANCE_M]
    if len(on_face) != 1:
        raise ToolError(f"The point is not inside one face of the sheet ({len(on_face)} faces there): give a point "
                        f"inside a flat face, away from its edges.", Err.SELECTION)
    if not value(value(on_face[0], "GetSurface"), "IsPlane"):
        raise ToolError("The point is on a face that is not flat: a forming tool needs a flat face.", Err.SELECTION)
    face = on_face[0]
    if flip:            # the Flip argument is ignored (measured): the tool pushes away from the face it is put on
        thickness = _sm_settings(doc)["thickness_m"] or 0.0
        normal = [float(v) for v in value(face, "Normal")]
        p = tuple(c - n * thickness for c, n in zip(p, normal))
        behind = [f for dist, f in geometry.faces_at_point(doc, *p) if dist <= geometry.FACE_POINT_TOLERANCE_M]
        if len(behind) != 1 or not value(value(behind[0], "GetSurface"), "IsPlane"):
            raise ToolError("flip: there is no flat face on the other side of the sheet at that point.", Err.SELECTION)
        face = behind[0]
    known = [row[0] for row in model.tree_features(doc)]
    box_before = [float(v) for v in value(geometry.solid_bodies(doc)[0], "GetBodyBox")]
    feat = None
    try:
        feat = sw.sheet_metal.sw_forming_tool(ctx.app, doc, face, p, path, math.radians(rotation))
    except Exception as e:
        logger.debug(f"Forming tool: {e}")
    model.rebuild(doc)
    problem = None if feat is not None else "SolidWorks did not place the forming tool there"
    problem = problem or _feature_errors(doc, known)
    if problem:
        model.remove_new_features(doc, known)
        model.rebuild(doc)
        raise ToolError(f"Not created: {problem} (is there room for it on that face?).", Err.FEATURE)
    box_after = [float(v) for v in value(geometry.solid_bodies(doc)[0], "GetBodyBox")]
    height = max(max(box_before[i] - box_after[i], box_after[i + 3] - box_before[i + 3], 0.0) for i in range(3))
    data = {"feature": str(feat.Name), "tool": matches[0], "file": path, "unit": units.label(unit),
            "height": round(ctx.from_m(height, unit), 4), "rotation": rotation, "flip": flip}
    return ok(f"{feat.Name}: {matches[0]} pressed in, {data['height']}{data['unit']} high", data)


HEM_TYPES = {"open": 0, "closed": 1, "teardrop": 2}     # swHemTypes_e
TEARDROP_ANGLE = 200.0                                   # degrees; SolidWorks refuses about 225 and more


@tool(
    group="sheetmetal",
    description="Fold a sheet metal edge back on itself (a hem), inside the old edge: closed, open (with a gap) or teardrop (angle, default 200 degrees; radius, default the part's). length is the folded-back leg of a closed or open hem (a teardrop has none). Returns the volume added. Refused while flattened.",
    params={"edge": dict(_POINT, description="A point on the edge (within 0.5 mm)"),
            "type": {"type": "string", "enum": list(HEM_TYPES), "description": "Kind of hem"},
            "length": {"type": "number", "description": "Length of the folded-back leg"},
            "gap": {"type": "number", "description": "Gap of an open hem (default 1 mm)"},
            "angle": {"type": "number", "description": "Teardrop only: roll angle in degrees, 181-225 (default 200)"},
            "radius": {"type": "number", "description": "Teardrop only: radius (default: the part's bend radius)"},
            "unit": {"type": "string", "description": "mm, cm, m, inch or ft"}},
    required=['edge', 'type'],
    needs="part",
    clears_selection=True,
)
def hem(ctx, edge, type, length=None, gap=None, angle=None, radius=None, unit=None):
    doc = ctx.doc
    if _sm_features(doc)["sheet_metal"] is None:
        raise ToolError("This part is not sheet metal. Start it with sheet_metal_base_flange or "
                        "convert_to_sheet_metal.", Err.INPUT)
    if doc.SketchManager.ActiveSketch is not None:
        raise ToolError("A sketch is open. Close it first (close_sketch).", Err.SKETCH)
    flat_feat = _flat_pattern_feature(doc)
    if flat_feat is not None and not bool(value(flat_feat, "IsSuppressed")):
        raise ToolError("The part is flattened (its flat pattern is shown). Fold it first.", Err.INPUT)
    kind = str(type or "closed").strip().lower()
    if kind not in HEM_TYPES:
        raise ToolError(f"type must be one of: {', '.join(HEM_TYPES)}", Err.INPUT)
    if kind == "teardrop":
        if length is not None:
            raise ToolError("A teardrop hem has no leg length: its size is its angle and radius.", Err.INPUT)
        length_m = 0.010                    # the API takes a leg length; a teardrop does not use it
    else:
        if length is None:
            raise ToolError(f"Give length: the leg of the {kind} hem (the user's; there is no default).", Err.INPUT)
        _number(length, "length")
        length_m = ctx.to_m(length, unit)
        if length_m <= 0:
            raise ToolError("length must be larger than 0", Err.INPUT)
        units.check_size(length_m, "length")
    gap_m = 0.0
    if gap is not None:
        if kind != "open":
            raise ToolError("gap is only for an open hem", Err.INPUT)
        _number(gap, "gap")
        gap_m = ctx.to_m(gap, unit)
        if gap_m <= 0:
            raise ToolError("gap must be larger than 0", Err.INPUT)
    elif kind == "open":
        gap_m = 0.001
    if (angle is not None or radius is not None) and kind != "teardrop":
        raise ToolError("angle and radius are only for a teardrop hem", Err.INPUT)
    settings = _sm_settings(doc)
    radius_m = settings["bend_radius_m"] or 0.001
    if radius is not None:
        _number(radius, "radius")
        radius_m = ctx.to_m(radius, unit)
        if radius_m <= 0:
            raise ToolError("radius must be larger than 0", Err.INPUT)
    angle_deg = TEARDROP_ANGLE
    if angle is not None:
        _number(angle, "angle")
        if not 180 < angle <= 225:
            raise ToolError("A teardrop angle must be more than 180 and at most 225 degrees (SolidWorks refuses "
                            "more: the leg runs into the sheet)", Err.INPUT)
        angle_deg = float(angle)
    angle_rad = math.radians(angle_deg) if kind == "teardrop" else 0.0
    picked = _pick_edges(doc, [edge], unit, "edge")[0]
    known = [row[0] for row in model.tree_features(doc)]
    volume_before = model.volume_mm3(doc)
    feat = None
    try:
        feat = sw.sheet_metal.sw_hem(doc, picked, HEM_TYPES[kind], length_m, gap_m, angle_rad, radius_m)
    except Exception as e:
        logger.debug(f"Hem: {e}")
    model.rebuild(doc)
    problem = None if feat is not None else "SolidWorks made no hem on that edge"
    problem = problem or _feature_errors(doc, known)
    volume_after = model.volume_mm3(doc)
    if problem is None and volume_before is not None and volume_after is not None and \
            abs(volume_after - volume_before) < 1e-6:
        problem = "the hem added no material"
    if problem:
        model.remove_new_features(doc, known)
        model.rebuild(doc)
        raise ToolError(f"Not created: {problem}. Is it an outer edge of the sheet, with room for the hem?",
                        Err.FEATURE)
    data_def = value(feat, "GetDefinition")
    data = {"feature": str(feat.Name), "type": kind, "length": round(ctx.from_m(float(data_def.Length), unit), 6),
            "gap": round(ctx.from_m(float(data_def.GapDistance), unit), 6), "unit": units.label(unit),
            "volume_added_mm3": round(volume_after - volume_before, 3) if volume_before is not None else None}
    if kind == "teardrop":
        data.pop("length")
        data["angle"] = round(math.degrees(float(data_def.Angle)), 4)
        data["radius"] = round(ctx.from_m(float(data_def.Radius), unit), 6)
        return ok(f"{feat.Name}: teardrop hem, {data['angle']} deg at radius {data['radius']}{data['unit']}", data)
    return ok(f"{feat.Name}: {kind} hem, leg {data['length']}{data['unit']}, gap {data['gap']}{data['unit']}", data)


BEND_POSITIONS = {"centerline": 4, "inside": 1, "outside": 2, "bend_outside": 3}   # swFlangePositionTypes_e


def _sketch_line_in_model(sketch):
    """(start, end, sketch normal) in model metres of the only line of a sketch, or a refusal."""
    segments = [s for s in (value(sketch, "GetSketchSegments") or []) if not bool(value(s, "ConstructionGeometry"))]
    if len(segments) != 1 or int(value(segments[0], "GetType")) != 0:
        raise ToolError(f"A sketched bend needs a sketch with exactly one line; it has {len(segments)} segment(s)"
                        + ("" if len(segments) != 1 else " and it is not a line"), Err.INPUT)
    a = [float(v) for v in value(sketch.ModelToSketchTransform, "ArrayData")]
    rot, move = (a[0:3], a[3:6], a[6:9]), a[9:12]
    origin = [-(move[0] * rot[i][0] + move[1] * rot[i][1] + move[2] * rot[i][2]) for i in range(3)]

    def to_model(point):
        return tuple(origin[i] + point[0] * rot[i][0] + point[1] * rot[i][1] + point[2] * rot[i][2] for i in range(3))

    line = segments[0]
    start = [float(value(value(line, "GetStartPoint2"), k)) for k in ("X", "Y", "Z")]
    end = [float(value(value(line, "GetEndPoint2"), k)) for k in ("X", "Y", "Z")]
    return to_model(start), to_model(end), tuple(rot[i][2] for i in range(3))


@tool(
    group="sheetmetal",
    description="Bend sheet metal along the single line of a sketch on its flat face, toward the sketch's side (reverse bends the other way). SolidWorks chooses the side that stays; fixed_point in the result is on it. position: centerline (the line is the middle of the bend), inside, outside or bend_outside. Refused while flattened.",
    params={"sketch": {"type": "string", "description": "Name of a sketch with one line, on a flat face of the sheet"},
            "angle": {"type": "number", "description": "Bend angle in degrees (1-179)"},
            "position": {"type": "string", "enum": list(BEND_POSITIONS), "default": "centerline",
                         "description": "Where the bend sits on the line"},
            "radius": {"type": "number", "description": "Bend radius (default: the part's)"},
            "reverse": {"type": "boolean", "default": False, "description": "Bend away from the sketch's side"},
            "unit": {"type": "string", "description": "mm, cm, m, inch or ft"}},
    required=["sketch", "angle"],
    needs="part",
    clears_selection=True,
)
def sketched_bend(ctx, sketch, angle, position="centerline", radius=None, reverse=False, unit=None):
    doc = ctx.doc
    if not isinstance(reverse, bool):
        raise ToolError("reverse must be true or false", Err.INPUT)
    if _sm_features(doc)["sheet_metal"] is None:
        raise ToolError("This part is not sheet metal. Start it with sheet_metal_base_flange or "
                        "convert_to_sheet_metal.", Err.INPUT)
    if doc.SketchManager.ActiveSketch is not None:
        raise ToolError("A sketch is open. Close it first (close_sketch).", Err.SKETCH)
    flat_feat = _flat_pattern_feature(doc)
    if flat_feat is not None and not bool(value(flat_feat, "IsSuppressed")):
        raise ToolError("The part is flattened (its flat pattern is shown). Fold it first.", Err.INPUT)
    _number(angle, "angle")
    if not 1 <= angle <= 179:
        raise ToolError("angle must be from 1 to 179 degrees", Err.INPUT)
    pos = str(position or "centerline").strip().lower()
    if pos not in BEND_POSITIONS:
        raise ToolError(f"position must be one of: {', '.join(BEND_POSITIONS)}", Err.INPUT)
    radius_m = None
    if radius is not None:
        _number(radius, "radius")
        radius_m = ctx.to_m(radius, unit)
        if radius_m <= 0:
            raise ToolError("radius must be larger than 0", Err.INPUT)
    names = model.sketch_names(doc)
    name = model.match_name(sketch, names)
    if name is None:
        raise ToolError(f"No sketch named '{sketch}'. Sketches: {', '.join(names) or 'none'}", Err.INPUT)
    users = model.sketch_users(doc, name)
    if users:
        raise ToolError(f"{name} is already used by {', '.join(users)}. Draw the bend line in a new sketch.",
                        Err.INPUT)
    sketch_feat = doc.FeatureByName(name)
    # what the sketch must hold (one line) before whether it is defined: defining a wrong sketch fixes nothing
    start, end, normal = _sketch_line_in_model(value(sketch_feat, "GetSpecificFeature2"))
    sk.require_defined(doc, name)
    mid = tuple((a + b) / 2.0 for a, b in zip(start, end))
    on_face = [f for dist, f in geometry.faces_at_point(doc, *mid) if dist <= geometry.FACE_POINT_TOLERANCE_M]
    if not on_face:
        raise ToolError(f"The line of {name} is not on a face of the part: draw it on a flat face of the sheet.",
                        Err.INPUT)
    direction = [b - a for a, b in zip(start, end)]
    across = (normal[1] * direction[2] - normal[2] * direction[1], normal[2] * direction[0] - normal[0] * direction[2],
              normal[0] * direction[1] - normal[1] * direction[0])
    length = math.sqrt(sum(c * c for c in across)) or 1.0
    across = tuple(c / length for c in across)

    def on_part(p):
        return any(dist <= geometry.FACE_POINT_TOLERANCE_M for dist, _f in geometry.faces_at_point(doc, *p))

    sides = []                                             # a point on the face either side of the line
    for sign in (1.0, -1.0):
        for step in (0.010, 0.003, 0.001):
            candidate = tuple(m + sign * step * a for m, a in zip(mid, across))
            if on_part(candidate):
                sides.append(candidate)
                break

    known = [row[0] for row in model.tree_features(doc)]
    box_before = [float(v) for v in value(geometry.solid_bodies(doc)[0], "GetBodyBox")]
    feat = None
    try:
        feat = sw.sheet_metal.sw_sketched_bend(doc, sketch_feat, math.radians(angle), BEND_POSITIONS[pos], radius_m,
                                               reverse)
    except Exception as e:
        logger.debug(f"Sketched bend: {e}")
    model.rebuild(doc)
    problem = None if feat is not None else "SolidWorks made no sketched bend along that line"
    problem = problem or _feature_errors(doc, known)
    if problem is None:
        box_after = [float(v) for v in value(geometry.solid_bodies(doc)[0], "GetBodyBox")]
        if max(abs(a - b) for a, b in zip(box_before, box_after)) < 1e-7:
            problem = "the part did not bend (does the line cross the face from edge to edge?)"
    if problem:
        model.remove_new_features(doc, known)
        model.rebuild(doc)
        raise ToolError(f"Not created: {problem}.", Err.FEATURE)
    data_def = value(feat, "GetDefinition")
    stayed = [p for p in sides if on_part(p)]               # measured: the side whose face did not move
    fixed = stayed[0] if len(stayed) == 1 else None
    flat = _measure_flat(doc)
    data = {"feature": str(feat.Name), "sketch": name, "angle": round(math.degrees(float(data_def.BendAngle)), 4),
            "radius": round(ctx.from_m(float(data_def.BendRadius), unit), 6), "position": pos,
            "reverse": bool(data_def.ReverseDirection), "fixed_point": [round(ctx.from_m(c, unit), 4) for c in fixed] if fixed else None,
            "unit": units.label(unit)}
    if flat and flat["size_mm"]:
        data["flat_size"] = [_conv(ctx, v / 1000.0, unit) for v in flat["size_mm"]]
    stayed_text = f"; the side at {data['fixed_point']} stayed" if fixed else ""
    return ok(f"{feat.Name}: {data['angle']} deg along {name}, radius {data['radius']}{data['unit']}, {pos}{stayed_text}"
              + (f"; flat size {data['flat_size']}{data['unit']}" if "flat_size" in data else ""), data)


FLANGE_POSITIONS = {"inside": 1, "outside": 2, "bend_outside": 3}   # material inside / material outside / bend outside


@tool(
    group="sheetmetal",
    description="Add an edge flange (a wall bent up) on the sheet metal edge through a point, with its length measured to the outer virtual sharp. position: inside (outer face on the edge), outside (inner face on the edge) or bend_outside (bend outside the edge). Returns the volume added, the flat size and what defining its profile sketch added (added_by_solidworks, relations_added: SolidWorks', not from your spec). Refused while flattened.",
    params={"edge": dict(_POINT, description="A point on the edge (within 0.5 mm)"),
            "length": {"type": "number", "description": "Flange length to the outer virtual sharp"},
            "angle": {"type": "number", "default": 90, "description": "Bend angle in degrees (1-179)"},
            "position": {"type": "string", "enum": list(FLANGE_POSITIONS), "default": "inside",
                         "description": "Where the flange sits on the edge"},
            "radius": {"type": "number", "description": "Bend radius (default: the part's)"},
            "unit": {"type": "string", "description": "mm, cm, m, inch or ft"}},
    required=["edge", "length"],
    needs="part",
    clears_selection=True,
)
def edge_flange(ctx, edge, length, angle=90, position="inside", radius=None, unit=None):
    doc = ctx.doc
    names = _sm_features(doc)
    if names["sheet_metal"] is None:
        raise ToolError("This part is not sheet metal. Start it with sheet_metal_base_flange or "
                        "convert_to_sheet_metal.", Err.INPUT)
    if doc.SketchManager.ActiveSketch is not None:
        raise ToolError("A sketch is open. Close it first (close_sketch).", Err.SKETCH)
    flat_feat = _flat_pattern_feature(doc)
    if flat_feat is not None and not bool(value(flat_feat, "IsSuppressed")):
        raise ToolError("The part is flattened (its flat pattern is shown). Fold it first, then add the flange.",
                        Err.INPUT)
    _number(length, "length")
    _number(angle, "angle")
    length_m = ctx.to_m(length, unit)
    if length_m <= 0:
        raise ToolError("length must be larger than 0", Err.INPUT)
    units.check_size(length_m, "length")
    if not 1 <= angle <= 179:
        raise ToolError("angle must be from 1 to 179 degrees", Err.INPUT)
    pos = str(position or "inside").strip().lower()
    if pos not in FLANGE_POSITIONS:
        raise ToolError(f"position must be one of: {', '.join(FLANGE_POSITIONS)}", Err.INPUT)
    settings = _sm_settings(doc)
    radius_m = None
    if radius is not None:
        _number(radius, "radius")
        radius_m = ctx.to_m(radius, unit)
        if radius_m <= 0:
            raise ToolError("radius must be larger than 0", Err.INPUT)
    thickness = settings["thickness_m"] or 0.0
    bend_r = radius_m if radius_m is not None else (settings["bend_radius_m"] or 0.0)
    if length_m <= bend_r + thickness:
        raise ToolError(f"length must be more than the bend radius plus the thickness "
                        f"({ctx.from_m(bend_r + thickness, unit):g}{units.label(unit)}): it is measured to the "
                        f"outer virtual sharp", Err.INPUT)
    picked = _pick_edges(doc, [edge], unit, "edge")[0]
    known = [row[0] for row in model.tree_features(doc)]
    volume_before = model.volume_mm3(doc)
    feat = None
    try:
        feat = sw.sheet_metal.sw_edge_flange(doc, picked, math.radians(angle), radius_m, FLANGE_POSITIONS[pos],
                                             length_m)
    except Exception as e:
        logger.debug(f"Edge flange: {e}")
    model.rebuild(doc)
    problem = None if feat is not None else "SolidWorks made no edge flange on that edge"
    problem = problem or _feature_errors(doc, known)
    volume_after = model.volume_mm3(doc)
    if problem is None and volume_before is not None and volume_after is not None and \
            abs(volume_after - volume_before) < 1e-6:
        problem = "the flange added no material"
    if problem:
        model.remove_new_features(doc, known)
        model.rebuild(doc)
        raise ToolError(f"Not created: {problem}. Is it an outer edge of the sheet (not a bend or a side "
                        f"edge of the thickness)?", Err.FEATURE)
    # a part's sketches are fully defined: the flange's own profile sketch too
    sketches, added, relations = sk.define_and_report(doc, feat, lambda v: ctx.from_m(v, unit), units.label(unit))
    data_def = value(feat, "GetDefinition")
    flat = _measure_flat(doc)
    data = {"feature": str(feat.Name), "length": round(ctx.from_m(float(data_def.OffsetDistance), unit), 6),
            "angle": round(math.degrees(float(data_def.BendAngle)), 4), "position": pos,
            "radius": round(ctx.from_m(float(data_def.BendRadius), unit), 6), "unit": units.label(unit),
            "volume_added_mm3": round(volume_after - volume_before, 3) if volume_before is not None else None}
    if flat and flat["size_mm"]:
        data["flat_size"] = [_conv(ctx, v / 1000.0, unit) for v in flat["size_mm"]]
    data["sketches"] = sketches
    data["added_by_solidworks"] = added            # what defining the profile sketch added
    if relations:
        data["relations_added"] = relations
    loose = [f"{n} is {state.replace('_', '-')}" for n, state in sketches.items() if state != "fully_defined"]
    return ok(f"{feat.Name}: {data['length']}{data['unit']} at {data['angle']} deg, radius {data['radius']}"
              f"{data['unit']}, {pos}" + (f"; flat size {data['flat_size']}{data['unit']}" if "flat_size" in data else "")
              + _added_text(added, relations)
              + (f". WARNING: {', '.join(loose)}" if loose else ""), data)


def _added_text(added: list, relations: dict) -> str:
    """What defining the flange's profile sketch added: SolidWorks' choice, not from the spec (a dimension may equal
    the flange length without measuring it: it is still SolidWorks')."""
    if not added and not relations:
        return ""
    return ". Added by SolidWorks to define its profile sketch, not from your spec: " + ", ".join(
        [f"{d['name']} = {d['value']}{d['unit']}" for d in added] + [f"{k} x{n}" for k, n in relations.items()])


@tool(
    group="sheetmetal",
    description="Make the first feature of a sheet metal part from a sketch. A CLOSED sketch (a plate; holes allowed) becomes a flat plate of the given thickness. An OPEN sketch (one chain of lines and arcs) becomes a bent profile extruded to `depth`, its corners turning into bends. The sketch is the OUTSIDE of the part: the thickness is built inward. Refused when the part already has a base flange (a tab on a face comes later) or a solid body. Returns the thickness, bend radius, K-factor and number of bends read back from the part.",
    params={'thickness': {'type': 'number', 'description': 'Sheet thickness'},
     'bend_radius': {'type': 'number', 'description': 'Inside bend radius (default: the thickness)'},
     'k_factor': {'type': 'number', 'default': 0.5, 'description': 'K-factor from 0 to 1'},
     'depth': {'type': 'number',
               'description': 'Length of an open profile (required for an open sketch, refused for a '
                              'closed one)'},
     'sketch': {'type': 'string', 'description': 'Name of the sketch (default: the last sketch)'},
     'flip': {'type': 'boolean',
              'default': False,
              'description': 'Closed sketch: build the plate on the other side of the sketch plane. '
                             'Open sketch: put the thickness on the other side of the profile'},
     'unit': {'type': 'string',
              'description': 'Unit for thickness, bend_radius and depth (mm, cm, m, inch or ft)'}},
    required=['thickness'],
    needs='part',
    clears_selection=True,
)
def sheet_metal_base_flange(ctx, thickness, bend_radius=None, k_factor=0.5, depth=None, sketch=None,
                            flip=False, unit=None):
    doc = ctx.doc
    unit_str = units.label(unit)

    if thickness is None:
        raise ToolError("thickness is required", Err.INPUT)
    _number(thickness, "thickness")
    thickness_m = ctx.to_m(thickness, unit)
    units.check_size(thickness_m, "thickness")
    if bend_radius is None:
        radius_m, bend_radius = thickness_m, thickness
    else:
        _number(bend_radius, "bend_radius")
        radius_m = ctx.to_m(bend_radius, unit)
        units.check_size(radius_m, "bend_radius")
    _check_k_factor(k_factor)
    depth_m = 0.0
    if depth is not None:
        _number(depth, "depth")
        depth_m = ctx.to_m(depth, unit)
        units.check_size(depth_m, "depth")
    flip = bool(flip)

    # the part must be empty
    names = _sm_features(doc)
    if names["base_flange"] is not None or names["sheet_metal"] is not None:
        raise ToolError(f"This part already has a base flange ({names['base_flange'] or names['sheet_metal']}). "
                        f"Adding a tab on a face of the sheet metal part comes later (tab, 6.10); "
                        f"to make another sheet metal part, start a new part.", Err.FEATURE,
                        {"base_flange": names["base_flange"]})
    bodies = model.solid_body_count(doc)
    if bodies > 0:
        raise ToolError(f"This part already has {bodies} solid body(ies). A base flange must be the first "
                        f"feature: start a new part. To turn an existing shell or solid into sheet metal, "
                        f"use convert_to_sheet_metal.", Err.FEATURE)

    # the sketch
    model.close_open_sketch(doc)
    sketches = model.sketch_names(doc)
    if not sketches:
        raise ToolError("There is no sketch to make the flange from. Draw a closed sketch (a plate) or one "
                        "open chain of lines (a bent profile) first.", Err.SKETCH)
    if sketch:
        sketch_name = model.match_name(sketch, sketches)
        if sketch_name is None:
            raise ToolError(f"No sketch named '{sketch}'. Sketches: {', '.join(sketches)}", Err.INPUT)
    else:
        sketch_name = sketches[-1]
        model.refuse_used_sketches(doc, [sketch_name])
    defined_note = sk.require_defined(doc, sketch_name)

    kind, detail = _profile_kind(doc, sketch_name)
    if kind == "error":
        raise ToolError(str(detail), Err.SKETCH, {"sketch": sketch_name})
    if kind == "open" and depth is None:
        raise ToolError(f"{sketch_name} is an open profile (a bent profile): depth is required, the length it "
                        f"is extruded to.", Err.INPUT, {"sketch": sketch_name})
    if kind == "closed" and depth is not None:
        raise ToolError(f"{sketch_name} is a closed sketch (a plate): its length is the sketch itself, so depth "
                        f"is not used. Leave depth out, or draw an open profile.", Err.INPUT, {"sketch": sketch_name})

    before = [row[0] for row in model.tree_features(doc)]
    sw.common.sw_clear_selection(doc)
    if not sw.common.sw_select_named(doc, sketch_name, "SKETCH", 0, append=False):
        raise ToolError(f"Could not select {sketch_name}", Err.SELECTION)

    allowance = value(doc.FeatureManager, "CreateCustomBendAllowance")
    allowance.Type = K_FACTOR_TYPE
    allowance.KFactor = float(k_factor)

    # closed: the plate side is the extrusion direction; open: the thickness side
    feat = sw.sheet_metal.sw_base_flange(doc, allowance, thickness_m, flip if kind == "open" else False, radius_m,
                                         depth_m if kind == "open" else 0.0, flip if kind == "closed" else False)
    sw.common.sw_clear_selection(doc)

    if feat is None:
        model.remove_new_features(doc, before)
        raise ToolError(f"Base flange from {sketch_name} failed. For a bent profile the bend radius must fit the "
                        f"legs (the legs must be longer than radius + thickness at each bend); a plate must be "
                        f"one closed loop, plus holes.", Err.FEATURE, {"sketch": sketch_name})

    problem = None
    try:
        code = value(feat, "GetErrorCode")
        if int(code) != 0:
            problem = f"it fails to rebuild (SolidWorks error code {int(code)})"
    except Exception:
        pass
    volume_after = model.volume_mm3(doc)
    if problem is None and (volume_after is None or volume_after <= 0):
        problem = "it made no solid"
    if problem:
        model.remove_new_features(doc, before)
        raise ToolError(f"Not created: the base flange from {sketch_name} is not possible - {problem}.",
                        Err.FEATURE, {"sketch": sketch_name})

    settings, bends = _sm_settings(doc), _sm_bend_count(doc)
    data = {
        "feature_name": feat.Name,
        "sheet_metal_feature": _sm_features(doc)["sheet_metal"],
        "sketch": sketch_name,
        "profile": "closed (plate)" if kind == "closed" else "open (bent profile)",
        "thickness": _conv(ctx, settings["thickness_m"], unit),
        "bend_radius": _conv(ctx, settings["bend_radius_m"], unit),
        "k_factor": round(settings["k_factor"], 6) if settings["k_factor"] is not None else None,
        "bends": bends, "flip": flip, "unit": unit_str,
        "solid_bodies": model.solid_body_count(doc), "volume_mm3": volume_after,
    }
    if kind == "open":
        data["depth"] = _conv(ctx, depth_m, unit)
    return ok(f"{feat.Name}: sheet metal {'plate' if kind == 'closed' else 'bent profile'} from {sketch_name}, "
              f"{data['thickness']}{unit_str} thick, bend radius {data['bend_radius']}{unit_str}, "
              f"K-factor {data['k_factor']}, {bends} bend(s)" + defined_note, data)


@tool(
    group="sheetmetal",
    description="Turn the solid body of a part into sheet metal with SolidWorks' Convert to Sheet Metal: give fixed_face (a point on the face that stays fixed), bend_edges (a point on each edge that becomes a bend) and thickness; SolidWorks adds the rips itself. The result is checked by measuring the flat pattern (one piece), and what was made is removed again when that fails. Returns thickness, bend radius, K-factor and flat pattern size. Insert Bends on a shelled part is not offered.",
    params={'method': {'type': 'string',
                'enum': ['convert'],
                'default': 'convert',
                'description': 'convert = Convert to Sheet Metal on a solid (the only method)'},
     'fixed_face': {'type': 'object',
                    'properties': {'x': {'type': 'number'},
                                   'y': {'type': 'number'},
                                   'z': {'type': 'number'}},
                    'required': ['x', 'y', 'z']},
     'bend_edges': {'type': 'array',
                    'items': {'type': 'object',
                              'properties': {'x': {'type': 'number'},
                                             'y': {'type': 'number'},
                                             'z': {'type': 'number'}},
                              'required': ['x', 'y', 'z']},
                    'description': 'A point on each edge that becomes a bend'},
     'thickness': {'type': 'number', 'description': 'Sheet thickness (required)'},
     'bend_radius': {'type': 'number', 'description': 'Inside bend radius (default: the thickness)'},
     'k_factor': {'type': 'number', 'default': 0.5, 'description': 'K-factor from 0 to 1'},
     'flip': {'type': 'boolean',
              'default': False,
              'description': 'Put the thickness on the other side'},
     'unit': {'type': 'string',
              'description': 'Unit for the coordinates, thickness and bend_radius (mm, cm, m, inch or ft)'}},
    required=['fixed_face'],
    needs='part',
    clears_selection=True,
)
def convert_to_sheet_metal(ctx, fixed_face, method="convert", bend_edges=None, thickness=None,
                           bend_radius=None, k_factor=0.5, flip=False, unit=None):
    doc = ctx.doc
    unit_str = units.label(unit)

    method = method or "convert"
    method = method.strip().lower() if isinstance(method, str) else method
    if method == "insert_bends":
        raise ToolError("method insert_bends is not offered (put off to a later tier); "
                        "use method convert on a solid", Err.INPUT)
    if method != "convert":
        raise ToolError("method must be convert", Err.INPUT)
    _check_k_factor(k_factor)
    radius_m = None
    if bend_radius is not None:
        _number(bend_radius, "bend_radius")
        radius_m = ctx.to_m(bend_radius, unit)
        units.check_size(radius_m, "bend_radius")
    thickness_m = None
    if thickness is not None:
        _number(thickness, "thickness")
        thickness_m = ctx.to_m(thickness, unit)
        units.check_size(thickness_m, "thickness")
    if thickness_m is None:
        raise ToolError("thickness is required", Err.INPUT)
    if fixed_face is None:
        raise ToolError("fixed_face is required: give a point on the face that stays fixed", Err.INPUT)

    names = _sm_features(doc)
    if names["sheet_metal"] is not None:
        raise ToolError(f"This part is already sheet metal ({names['sheet_metal']}).", Err.FEATURE)
    bodies = model.solid_body_count(doc)
    if bodies != 1:
        raise ToolError(f"The part must have exactly one solid body (it has {bodies}).", Err.FEATURE)

    # pick the entities
    try:
        face_pts = geometry.points_to_m(fixed_face, unit)
    except (ValueError, TypeError) as e:
        raise ToolError(f"fixed_face: {e}", Err.INPUT)
    if len(face_pts) != 1:
        raise ToolError("fixed_face takes one point", Err.INPUT)
    face, reason = geometry.face_at_point(geometry.all_faces(doc), face_pts[0], geometry.PICK_TOLERANCE_M)
    if face is None:
        raise ToolError(f"fixed_face {geometry.point_in_unit(face_pts[0], unit)}: {reason}", Err.SELECTION)
    edges = _pick_edges(doc, bend_edges, unit, "bend_edges")
    if radius_m is None:
        radius_m = thickness_m

    # Refuse before SolidWorks is called: a thickness that is a large part of the body makes Convert to
    # Sheet Metal fail, leave a stray cut list folder, or hang SolidWorks (measured: 10 mm on a 20 mm block).
    size_before = _body_extents_mm(doc)[1]
    if size_before:
        smallest = min(size_before)
        if thickness_m * 1000.0 > smallest / 4.0 + 1e-9:
            raise ToolError(f"thickness {thickness_m * 1000.0:g} mm is more than a quarter of the body's smallest "
                            f"size ({smallest:g} mm). Convert to Sheet Metal is only offered for thin walls "
                            f"(thickness up to {smallest / 4.0:g} mm here).", Err.INPUT)

    before = [row[0] for row in model.tree_features(doc)]
    volume_before = model.volume_mm3(doc)

    if not sw.sheet_metal.sw_select_face_and_edges(doc, face, edges):
        sw.common.sw_clear_selection(doc)
        raise ToolError("Could not select the face and edges", Err.SELECTION)

    problem = None
    try:
        sw.sheet_metal.sw_convert(doc, thickness_m, bool(flip), radius_m)
    except Exception as e:
        problem = f"SolidWorks refused it ({e})"
    sw.common.sw_clear_selection(doc)

    # the return value is not reliable: judge by the tree
    if problem is None and _sm_features(doc)["sheet_metal"] is None:
        problem = "SolidWorks made no sheet metal feature"
    if problem is None:
        problem = _feature_errors(doc, before)
    flat = None
    if problem is None:
        if not _apply_k_factor(doc, k_factor):
            problem = "the K-factor could not be set"
        else:
            problem = _feature_errors(doc, before)
    if problem is None:
        flat = _measure_flat(doc)
        if flat is None or flat["pieces"] != 1:
            problem = "the result has no single flat pattern"
    if problem:
        model.remove_new_features(doc, before)
        after = model.volume_mm3(doc)
        note = ""
        if volume_before and after and abs(after - volume_before) > 0.01 * volume_before:
            note = " The model volume changed and could not be restored; reload the part."
        raise ToolError(f"Not converted: {problem}.{note}", Err.FEATURE, {"method": method})

    settings = _sm_settings(doc)
    data = {
        "method": method,
        "sheet_metal_feature": _sm_features(doc)["sheet_metal"],
        "thickness": _conv(ctx, settings["thickness_m"], unit),
        "bend_radius": _conv(ctx, settings["bend_radius_m"], unit),
        "k_factor": round(settings["k_factor"], 6) if settings["k_factor"] is not None else None,
        "edges_given": len(edges), "flat_pieces": flat["pieces"], "flat_size_mm": flat["size_mm"],
        "solid_bodies": model.solid_body_count(doc), "volume_mm3": model.volume_mm3(doc), "unit": unit_str,
    }
    return ok(f"{data['sheet_metal_feature']}: converted to sheet metal ({method}), "
              f"{data['thickness']}{unit_str} thick, bend radius {data['bend_radius']}{unit_str}, "
              f"K-factor {data['k_factor']}; flat pattern {flat['size_mm']} mm in {flat['pieces']} piece", data)


# ---------------------------------------------------------------- get_sheet_metal_info

def _straight_edge_length(face, axis):
    """Length (m) of the longest straight edge of a cylindrical face that runs along its axis."""
    best = None
    for edge in list(value(face, "GetEdges") or []):
        try:
            if not bool(value(value(edge, "GetCurve"), "IsLine")):
                continue
            p1 = [float(v) for v in value(value(edge, "GetStartVertex"), "GetPoint")]
            p2 = [float(v) for v in value(value(edge, "GetEndVertex"), "GetPoint")]
        except Exception:
            continue
        d = [p2[i] - p1[i] for i in range(3)]
        length = math.sqrt(sum(c * c for c in d))
        if length > 1e-9 and abs(sum(d[i] * axis[i] for i in range(3))) / length > 0.9999:
            best = length if best is None else max(best, length)
    return best


def _bend_list(doc, thickness_m: float) -> list:
    """The bends of a folded sheet metal part, read from its faces. Every bend is two coaxial
    cylinder faces: the inside (radius R) and the outside (R + thickness), with the same width and
    angle. Returns [{"radius_m", "angle_deg", "width_m"}], inside radius."""
    dot = lambda a, b: sum(a[i] * b[i] for i in range(3))
    sub = lambda a, b: [a[i] - b[i] for i in range(3)]
    cyl = []
    for face in geometry.all_faces(doc):
        try:
            surf = value(face, "GetSurface")
            if not bool(value(surf, "IsCylinder")):
                continue
            p = [float(v) for v in value(surf, "CylinderParams")]
            area = float(value(face, "GetArea"))
        except Exception:
            continue
        origin, axis, radius = p[0:3], p[3:6], p[6]
        width = _straight_edge_length(face, axis)
        if width is None or radius <= 0:
            continue
        cyl.append({"o": origin, "a": axis, "r": radius, "area": area, "w": width,
                    "angle": area / (radius * width), "used": False})

    def coaxial(c1, c2) -> bool:
        if abs(dot(c1["a"], c2["a"])) < 0.9999:
            return False
        d = sub(c2["o"], c1["o"])
        along = dot(d, c1["a"])
        off = [d[i] - along * c1["a"][i] for i in range(3)]
        return math.sqrt(dot(off, off)) < 1e-6

    bends = []
    for inner in sorted(cyl, key=lambda c: c["r"]):
        if inner["used"]:
            continue
        for outer in cyl:
            if outer is inner or outer["used"]:
                continue
            if abs(outer["r"] - inner["r"] - thickness_m) > 1e-6:
                continue
            if abs(outer["w"] - inner["w"]) > 1e-6 or abs(outer["angle"] - inner["angle"]) > 1e-3:
                continue
            if not coaxial(inner, outer):
                continue
            inner["used"] = outer["used"] = True
            bends.append({"radius_m": inner["r"], "angle_deg": math.degrees(inner["angle"]), "width_m": inner["w"]})
            break
    return bends


def _read_in_place(doc, thickness_m: float) -> dict:
    """Read the bends (folded state) and the flat pattern size (flat state), and put the flat
    pattern suppression back exactly as it was."""
    feat = _flat_pattern_feature(doc)
    out = {"bends": [], "flat": None, "was_flat": None}
    if feat is None:
        out["bends"] = _bend_list(doc, thickness_m)
        return out
    was_suppressed = bool(value(feat, "IsSuppressed"))
    out["was_flat"] = not was_suppressed
    try:
        if was_suppressed:
            out["bends"] = _bend_list(doc, thickness_m)
            sw.sheet_metal.sw_show_flat_pattern(feat, True)     # show the flat pattern
            pieces, size = _body_extents_mm(doc)
        else:
            pieces, size = _body_extents_mm(doc)
            sw.sheet_metal.sw_show_flat_pattern(feat, False)    # fold it for a moment
            out["bends"] = _bend_list(doc, thickness_m)
        out["flat"] = {"pieces": pieces, "size_mm": size}
    finally:
        if bool(value(feat, "IsSuppressed")) != was_suppressed:
            sw.sheet_metal.sw_show_flat_pattern(feat, not was_suppressed)
    return out


def _read_on_copy(app, doc, thickness_m: float):
    """_read_in_place on a temporary copy of the part. None when the copy could not be made or opened."""
    folder = tempfile.mkdtemp(prefix="swmcp_info_")
    path = os.path.join(folder, "sheet_metal_info_copy.SLDPRT")
    copy = None
    try:
        if not sw.sheet_metal.sw_save_copy(doc, path) or not os.path.exists(path):
            return None
        copy = sw.sheet_metal.sw_open_copy(app, path)
        if copy is None:
            return None
        out = _read_in_place(copy, thickness_m)
        out["measured_on"] = "copy"
        return out
    except Exception as e:
        logger.debug(f"Measuring on a copy failed: {e}")
        return None
    finally:
        if copy is not None:
            try:
                sw.documents.sw_close(app, doc_path(copy) or path)
            except Exception as e:
                logger.debug(f"Could not close the copy: {e}")
        sw.documents.sw_activate(app, doc_title(doc))
        shutil.rmtree(folder, ignore_errors=True)


def _read_folded_and_flat(app, doc, thickness_m: float, use_copy: bool) -> dict:
    """Showing the flat pattern marks a saved document as modified (nothing clears that flag again,
    not even undo). So when the document has no unsaved changes the part is saved as a copy in the
    temp folder, the copy is opened hidden and read-only, measured and closed; the document itself is
    not touched. If that fails, or the document is modified already, the flat pattern is shown and
    hidden again in the document. Returns {"bends", "flat", "was_flat", "measured_on"}."""
    if use_copy:
        out = _read_on_copy(app, doc, thickness_m)
        if out is not None:
            return out
    out = _read_in_place(doc, thickness_m)
    out["measured_on"] = "document"
    return out


@tool(
    group="sheetmetal",
    description="Read the active part's sheet metal settings (read only): thickness, default bend radius, K-factor, the bends (inside radius, angle in degrees and width of each), flat pattern size (bounding box, largest first), number of flat pieces, and whether the part is flattened now. A part with no unsaved changes is measured on a temporary copy, so the document is untouched and stays unmodified (measured_on = copy); an already modified part is measured in place, the flat pattern shown (or hidden) for a moment and then put back exactly as it was (measured_on = document). Refused on a part that is not sheet metal, and on assemblies and drawings.",
    params={'unit': {'type': 'string', 'description': 'Unit for the sizes in the result (mm, cm, m, inch or ft)'}},
    needs='part',
)
def get_sheet_metal_info(ctx, unit=None):
    doc = ctx.doc
    unit_str = units.label(unit)
    names = _sm_features(doc)
    if names["sheet_metal"] is None:
        raise ToolError("This part is not sheet metal (no Sheet-Metal feature). "
                        "Make it with sheet_metal_base_flange or convert_to_sheet_metal.", Err.FEATURE)
    settings = _sm_settings(doc)
    if settings["thickness_m"] is None:
        raise ToolError("Could not read the sheet metal settings", Err.FEATURE)

    def dirty():
        try:
            return bool(value(doc, "GetSaveFlag"))
        except Exception:
            return None

    dirty_before = dirty()
    title = doc_title(doc)
    read = _read_folded_and_flat(ctx.app, doc, settings["thickness_m"], use_copy=(dirty_before is False))
    sw.common.sw_clear_selection(doc)
    dirty_after = dirty()
    try:
        active = ctx.app.ActiveDoc
        if active is None or doc_title(active) != title:
            sw.documents.sw_activate(ctx.app, title)
    except Exception as e:
        logger.debug(f"Could not check the active document: {e}")

    bends = [{"radius": _conv(ctx, b["radius_m"], unit), "angle_deg": round(b["angle_deg"], 4),
              "width": _conv(ctx, b["width_m"], unit)} for b in read["bends"]]
    bends.sort(key=lambda b: (b["angle_deg"], b["radius"]))
    flat = read["flat"]
    flat_size = [_conv(ctx, v / 1000.0, unit) for v in flat["size_mm"]] if flat and flat["size_mm"] else None
    data = {
        "sheet_metal_feature": names["sheet_metal"], "base_feature": names["base_flange"],
        "flat_pattern_feature": names["flat_pattern"],
        "thickness": _conv(ctx, settings["thickness_m"], unit),
        "bend_radius": _conv(ctx, settings["bend_radius_m"], unit),
        "k_factor": round(settings["k_factor"], 6) if settings["k_factor"] is not None else None,
        "bend_count": len(bends), "bends": bends, "flat_size": flat_size,
        "flat_pieces": flat["pieces"] if flat else None, "flattened_now": read["was_flat"],
        "measured_on": read.get("measured_on"), "document_modified_before": dirty_before,
        "document_modified_after": dirty_after, "unit": unit_str,
    }
    message = (f"{names['sheet_metal']}: {data['thickness']}{unit_str} thick, bend radius "
               f"{data['bend_radius']}{unit_str}, K-factor {data['k_factor']}, {len(bends)} bend(s)")
    if flat_size:
        message += f"; flat size {flat_size}{unit_str}"
    if read["was_flat"] is not None:
        message += "; flattened now" if read["was_flat"] else "; folded now"
    if dirty_before is False and dirty_after:
        message += ". NOTE: the document is marked modified because the flat pattern was shown for a moment"
    return ok(message, data)
