"""Standard (ISO) holes from the Hole Wizard."""

import logging

from ..core.api import Err, geometry, model, ok, sketch as sk, sw, tool, ToolError, units, value

logger = logging.getLogger(__name__)

# Value1..12 of HoleWizard5 to try first per hole type: the one SolidWorks 2026 accepts (the other stays as the
# fallback). A wrong first try costs a failed feature and its removal.
KNOWN_GOOD = {"simple": (0, -1)}

# hole type -> (swWzdGeneralHoleTypes_e, swWzdHoleStandardFastenerTypes_e, what the size means)
HOLE_TYPES = {
    "simple":      (2, 143, "drill"),    # swWzdHole, ISO drill sizes
    "counterbore": (0, 139, "thread"),   # swWzdCounterBore, ISO socket head cap screw
    "countersink": (1, 140, "thread"),   # swWzdCounterSink, ISO socket countersunk head
    "tapped":      (4, 147, "thread"),   # swWzdTap, ISO tapped hole
}


@tool(
    group="features",
    description="Make a standard ISO Hole Wizard hole at a model point on a flat face. hole_type: 'simple' (drilled; size = standard ISO drill diameter in mm; with a unit other than mm write it '6.5mm'), 'counterbore' (socket head cap screw; size like 'M6'), 'countersink' (countersunk socket screw; size like 'M6') or 'tapped' (size like 'M6'; with depth, depth is the tap drill depth and the thread is 1 x the thread diameter deep). Leave depth out for a through hole. Several holes of the same type and size on one flat face: give points instead of x, y, z; they become one Hole Wizard feature (one call, one feature). The positions are dimensioned from the edges named in dimension_from (the user's references; one call per group of holes sharing their edges); what no named edge covers is dimensioned from the origin and listed in dimensions_from_origin (not from your spec); an edge dimension that could not be made is listed in edge_dimensions_missing with a warning.",
    params={'hole_type': {'type': 'string',
                   'enum': ['simple', 'counterbore', 'countersink', 'tapped'],
                   'default': 'simple',
                   'description': 'Kind of hole'},
     'size': {'type': 'string',
              'description': "Drill diameter in mm for 'simple' (e.g. '6.5'), or thread size for the "
                             "others (e.g. 'M6')"},
     'x': {'type': 'number', 'description': 'X of the hole position on the face'},
     'y': {'type': 'number', 'description': 'Y of the hole position on the face'},
     'z': {'type': 'number', 'description': 'Z of the hole position on the face'},
     'dimension_from': {'type': 'array', 'minItems': 1, 'maxItems': 2,
                        'items': {'type': 'object', 'properties': {'x': {'type': 'number'}, 'y': {'type': 'number'},
                                                                  'z': {'type': 'number'}},
                                  'required': ['x', 'y', 'z']},
                        'description': 'The edges the user measures the holes from, each by a point on it (model '
                                       'coordinates), e.g. [{"x": 0, "y": 6, "z": 0}] for "20 mm from the outside '
                                       'face"; one or two (one per direction). Without it, the positions are '
                                       'dimensioned from the origin'},
     'points': {'type': 'array', 'minItems': 1,
                'items': {'type': 'object', 'properties': {'x': {'type': 'number'}, 'y': {'type': 'number'},
                                                          'z': {'type': 'number'}},
                          'required': ['x', 'y', 'z']},
                'description': 'Several hole positions on the same flat face, e.g. [{"x": 20, "y": 6, "z": 15}, '
                               '{"x": 70, "y": 6, "z": 15}]; replaces x, y, z'},
     'depth': {'type': 'number', 'description': 'Hole depth; omit for a through hole'},
     'unit': {'type': 'string', 'description': 'Unit for x, y, z and depth (mm, cm, m, inch or ft)'}},
    required=['size'],
    needs='part',
    clears_selection=True,
)
def hole_wizard(ctx, size, hole_type="simple", x=None, y=None, z=None, depth=None, unit=None, points=None, dimension_from=None):
    doc = ctx.doc
    try:
        if doc.SketchManager.ActiveSketch is not None:
            raise ToolError("A sketch is open. Close it first (close_sketch).", Err.SKETCH)
    except ToolError:
        raise
    except Exception:
        pass

    kind = str(hole_type or "simple").strip().lower()
    if kind not in HOLE_TYPES:
        raise ToolError(f"Unknown hole_type '{hole_type}'. Use one of: {', '.join(HOLE_TYPES)}", Err.INPUT)
    generic_type, fastener_type, size_kind = HOLE_TYPES[kind]

    # the size as the Hole Wizard tables spell it
    if size is None or str(size).strip() == "":
        raise ToolError("Give a size: a drill diameter in mm for 'simple' (e.g. 6.5), "
                        "or a thread size like 'M6' for the other types", Err.INPUT)
    text = str(size).strip().upper().replace("Ø", "")
    if size_kind == "drill":
        # drill sizes are ISO millimetres whatever `unit` says: with another unit, a bare number is ambiguous
        # (0.25 meant as inch would make a 0.25 mm hole), so it must say mm
        if (units.parse(unit) or units.default()) != "mm" and not text.endswith("MM"):
            raise ToolError(f"size '{size}' has no unit, and unit is {units.label(unit)}: a 'simple' hole's size is "
                            f"an ISO drill diameter in millimetres. Give it with mm, e.g. '6.5mm' (inch drill sizes "
                            f"are not in the ISO table)", Err.INPUT)
        try:
            diameter_mm = float(text.replace("MM", "").replace("M", ""))
        except ValueError:
            raise ToolError(f"Bad drill diameter '{size}' (give millimetres, e.g. 6.5)", Err.INPUT)
        # size 0 or a negative size made SolidWorks fall back to a 10 mm hole, reported as 0 mm
        if not (0 < diameter_mm <= 1000):
            raise ToolError(f"Bad drill diameter '{size}': give a diameter in millimetres greater than 0 "
                            f"(e.g. 6.5)", Err.INPUT)
        size_text = f"Ø{diameter_mm:.1f}" if abs(diameter_mm * 10 - round(diameter_mm * 10)) < 1e-9 \
            else f"Ø{diameter_mm:g}"
        diameter_m = diameter_mm / 1000.0
    else:
        if not text.startswith("M"):
            text = "M" + text
        try:
            thread = float(text[1:].split("X")[0])
        except ValueError:
            raise ToolError(f"Bad thread size '{size}' (use e.g. M6)", Err.INPUT)
        if not (0 < thread <= 300):
            raise ToolError(f"Bad thread size '{size}' (use e.g. M6)", Err.INPUT)
        size_text = text
        diameter_m = -1.0                  # let the standard decide

    unit_str = units.label(unit)
    if points is not None:
        try:
            spots = [(float(p["x"]), float(p["y"]), float(p["z"])) for p in points]
        except (TypeError, KeyError, ValueError):
            raise ToolError("points must be a list of {x, y, z} numbers", Err.INPUT)
        if not spots:
            raise ToolError("points is empty: give at least one {x, y, z}", Err.INPUT)
        if len(set(spots)) != len(spots):
            raise ToolError("Two points are the same: each hole needs its own position", Err.INPUT)
    elif x is None or y is None or z is None:
        raise ToolError("Give the hole's position: x, y and z (a model point on the face), or points for several "
                        "holes (there is no default position).", Err.INPUT)
    else:
        spots = [(x, y, z)]
    x, y, z = spots[0]
    x_m, y_m, z_m = (ctx.to_m(v, unit) for v in (x, y, z))
    where = f"({x}, {y}, {z}) {unit_str}"
    through = depth is None
    depth_m = 0.01 if through else ctx.to_m(depth, unit)
    if not through and depth_m <= 0:
        raise ToolError("depth must be positive (omit it for a through hole)", Err.INPUT)

    # the edges the positions are measured from (checked against the face once it is found)
    refs = []
    if dimension_from is not None:
        try:
            refs = [tuple(ctx.to_m(float(p[k]), unit) for k in ("x", "y", "z")) for p in dimension_from]
        except (TypeError, KeyError, ValueError):
            raise ToolError("dimension_from must be a list of {x, y, z} points, one on each edge", Err.INPUT)
        if not 1 <= len(refs) <= 2:
            raise ToolError("dimension_from takes one or two edges (one per direction)", Err.INPUT)

    # the face, found by geometry
    candidates = geometry.faces_at_point(doc, x_m, y_m, z_m)
    on_point = [(d, f) for d, f in candidates if d <= geometry.FACE_POINT_TOLERANCE_M]
    if not on_point:
        extra = f" The nearest face is {ctx.from_m(candidates[0][0], unit):.3f}{unit_str} away." if candidates else ""
        raise ToolError(f"No face at {where}.{extra}", Err.SELECTION)
    if len(on_point) > 1:
        raise ToolError(f"The point {where} is on an edge shared by {len(on_point)} faces. "
                        f"Give a point inside the face.", Err.SELECTION)
    face = on_point[0][1]
    if not value(value(face, "GetSurface"), "IsPlane"):
        raise ToolError(f"The face at {where} is not flat", Err.SELECTION)
    try:
        face_normal = tuple(float(v) for v in value(face, "Normal"))
    except Exception:
        face_normal = None
    # every named edge parallel to the face: a dimension to one that runs into it would be a radial distance
    from_edges = [geometry.edge_at(doc, p, normal=face_normal, face=face, unit=unit) for p in refs]
    others = [tuple(ctx.to_m(v, unit) for v in spot) for spot in spots[1:]]
    off_face = [spots[i + 1] for i, p in enumerate(others) if geometry.distance_to_face(face, p)
                > geometry.FACE_POINT_TOLERANCE_M]
    if off_face:
        raise ToolError(f"Not on the face at {where}: {', '.join(str(tuple(s)) for s in off_face)} {unit_str}. All "
                        f"points of one call must be on one flat face; make another call for the others.",
                        Err.SELECTION, {"off_face": [list(s) for s in off_face]})

    last_before = model.last_feature_name(doc)
    volume_before = model.volume_mm3(doc)

    def remove_new():
        for name in model.feature_names_after(doc, last_before)[::-1]:
            sw.holes.sw_delete_with_sketches(doc, name)
        sw.common.sw_clear_selection(doc)

    end_type = 1 if through else 0         # swEndCondThroughAll / swEndCondBlind

    # Value1..Value12 of HoleWizard5: -1 means "take it from the standard", 0 "not used". Which one
    # SolidWorks 2026 accepts depends on the hole type and end condition, so both are tried; an attempt
    # that fails or does not rebuild is removed before the next one. A blind tapped hole needs Value1 = the
    # thread diameter (-1 there gives a 1 inch hole that fails); the rest come from the standard:
    # the tap drill to `depth`, the thread 1 x D deep.
    attempts = KNOWN_GOOD.get(kind, (-1, 0))
    if kind == "tapped":
        attempts = (0, -1) if through else ([thread / 1000.0] + [-1] * 11,)
    feat, had_rebuild_error = None, False
    for v in attempts:
        if not sw.holes.sw_select_face_at(doc, face, x_m, y_m, z_m):
            raise ToolError(f"Could not select the face at {where}", Err.SELECTION)
        candidate = sw.holes.sw_hole_wizard(doc, generic_type, fastener_type, size_text, end_type, diameter_m,
                                            depth_m, v)

        if candidate is None:
            remove_new()
            continue
        if model.feature_error(candidate):
            had_rebuild_error = True
            remove_new()
            # the face object may be stale after the delete: find it again
            refreshed = [f for dist, f in geometry.faces_at_point(doc, x_m, y_m, z_m)
                         if dist <= geometry.FACE_POINT_TOLERANCE_M]
            if len(refreshed) == 1:
                face = refreshed[0]
            continue
        feat = candidate
        break

    if feat is None:
        if had_rebuild_error and kind == "tapped" and not through:
            raise ToolError(f"Not created: the blind tapped hole {size_text}, {depth}{unit_str} deep, fails to "
                            f"rebuild at {where}. The tap drill depth must be more than the thread depth "
                            f"(1 x the thread diameter) and less than the part is thick.", Err.FEATURE)
        if had_rebuild_error:
            raise ToolError(f"Not created: the {kind} hole {size_text} fails to rebuild at {where} "
                            f"(is the part thick enough for it?)", Err.FEATURE)
        hint = ("Standard drill sizes go in 0.1 mm steps up to 10 mm and coarser above; "
                "for another diameter sketch a circle and use cut_extrude."
                if size_kind == "drill" else
                "Use a standard ISO thread size (M3, M4, M5, M6, M8, M10, M12...).")
        raise ToolError(f"SolidWorks could not make a {kind} hole of size {size_text} at {where}. {hint}",
                        Err.FEATURE)

    # the other points join the same feature: they go into its placement sketch, which rebuilds the hole at each
    if others:
        placed = _add_points(doc, feat, others)
        if placed != len(spots) or model.feature_error(feat):
            remove_new()
            raise ToolError(f"Not created: the Hole Wizard placed {placed} of the {len(spots)} holes (or failed to "
                            f"rebuild with them). Check the points are inside the face.", Err.FEATURE)

    feat_name = feat.Name
    data = {"feature_name": feat_name, "hole_type": kind, "size": size_text,
            "position": {"x": x, "y": y, "z": z}, "unit": unit_str, "through": through,
            "solid_bodies": model.solid_body_count(doc)}
    if points is not None:
        data.update(holes=len(spots), positions=[{"x": a, "y": b, "z": c} for a, b, c in spots])
    if not through:
        data["depth"] = depth
    volume_after = model.volume_mm3(doc)
    if volume_before is not None and volume_after is not None:
        data["volume_removed_mm3"] = round(volume_before - volume_after, 3)
        if data["volume_removed_mm3"] < 1e-6:
            remove_new()
            raise ToolError(f"Not created: the hole at {where} removed nothing (it points away from the body)",
                            Err.FEATURE)
    end_text = "through" if through else f"{depth}{unit_str} deep"
    at = where if len(spots) == 1 else f"{len(spots)} points ({', '.join(str(s) for s in spots)}) {unit_str}"
    message = f"{feat_name}: {kind} hole {size_text}, {end_text}, at {at}"
    # a part's sketches are fully defined: the positions from the edges the user named, the rest from the origin
    from_named, missing = (_dimension_placement(ctx, doc, feat, refs, face_normal, unit) if refs else ([], []))
    # what the origin fallback added: listed, as not from the spec
    sketches, rows, relations = sk.define_and_report(doc, feat, lambda v: ctx.from_m(v, unit), unit_str)
    data["sketches"] = sketches
    from_origin = [{"name": d["name"], "value": d["value"]} for d in rows]
    if from_named:
        data["dimensions_from_edges"] = from_named
        message += ". Positions dimensioned from the named edges: " + ", ".join(
            f"{d['name']} = {d['value']}{unit_str}" for d in from_named)
    if missing:
        data["edge_dimensions_missing"] = missing
        message += (f". WARNING: {len(missing)} of {len(from_named) + len(missing)} dimensions from the named edges "
                    f"could not be made ({'; '.join(missing)})")
    if from_origin:
        data["dimensions_from_origin"] = from_origin
        message += (". From the origin, not from your spec: " + ", ".join(
            f"{d['name']} = {d['value']}{unit_str}" for d in from_origin)
            + ("" if refs else " (name the edges they are measured from with dimension_from)"))
    elif not refs:
        message += ". The positions are from the origin, not from your spec (name the edges with dimension_from)"
    if relations:
        data["relations_added"] = relations
        message += (". Relations added by SolidWorks, not from your spec: "
                    + ", ".join(f"{k} x{n}" for k, n in relations.items()))
    loose = [f"{n} is {state.replace('_', '-')}" for n, state in sketches.items() if state != "fully_defined"]
    if loose:
        message += f". WARNING: {', '.join(loose)}"
    return ok(message, data)


def _add_points(doc, feat, others) -> int:
    """Put further hole positions (model points, metres) into the hole's placement sketch, so the one feature cuts a
    hole at each. Returns how many points the placement sketch holds afterwards."""
    sub = value(feat, "GetFirstSubFeature")
    while sub is not None:
        if value(sub, "GetTypeName2") == "ProfileFeature":
            sketch = value(sub, "GetSpecificFeature2")
            if not list(value(sketch, "GetSketchSegments") or []):          # the placement sketch: points only
                name = str(sub.Name)
                if not sw.sketches.sw_edit_sketch(doc, name):
                    return 0
                try:
                    open_sk = sk.open_sketch(doc) or sketch
                    # SolidWorks: [sketch] = [model] * R + T (row vectors, R = a[0:9], T = a[9:12])
                    a = [float(v) for v in value(open_sk.ModelToSketchTransform, "ArrayData")]
                    R, T = (a[0:3], a[3:6], a[6:9]), a[9:12]
                    with sk.exact_input(doc):
                        for p in others:
                            u = sum(p[i] * R[i][0] for i in range(3)) + T[0]
                            v = sum(p[i] * R[i][1] for i in range(3)) + T[1]
                            sw.sketch_draw.sw_point(doc, u, v)
                finally:
                    sw.sketches.sw_close_sketch(doc)
                placement = value(doc.FeatureByName(name), "GetSpecificFeature2")
                return len(list(value(placement, "GetSketchPoints2") or []))
        sub = value(sub, "GetNextSubFeature")
    return 0


def _dimension_placement(ctx, doc, feat, refs, normal, unit) -> tuple:
    """In the hole's placement sketch, a dimension from every hole point to each named edge (refs: a point on each,
    re-found now: the hole changed the geometry). A redundant one comes in driven and is taken out. Returns
    ([{name, value}] kept, [what could not be made])."""
    edges = []
    for p in refs:
        try:
            edges.append(geometry.edge_at(doc, p, normal=normal))
        except ToolError:                               # the hole cut it away or split it there: reported below
            edges.append(None)
    sub = value(feat, "GetFirstSubFeature")
    while sub is not None:
        if value(sub, "GetTypeName2") == "ProfileFeature":
            sketch = value(sub, "GetSpecificFeature2")
            if not list(value(sketch, "GetSketchSegments") or []):          # the placement sketch: points only
                break
        sub = value(sub, "GetNextSubFeature")
    else:
        return [], ["no placement sketch found"]
    name = str(sub.Name)
    if not sw.sketches.sw_edit_sketch(doc, name):
        return [], [f"could not open {name}"]
    kept, missing = [], []
    try:
        open_sk = sk.open_sketch(doc) or sketch
        for point in list(value(open_sk, "GetSketchPoints2") or []):
            for k, edge in enumerate(edges, 1):
                at = f"hole at ({ctx.from_m(float(point.X), unit):g}, {ctx.from_m(float(point.Y), unit):g}) to edge {k}"
                if edge is None:
                    missing.append(f"{at}: the edge is gone after the hole (nothing at its dimension_from point)")
                    continue
                sw.common.sw_clear_selection(doc)
                if not (sw.sketch_define.sw_select_entity(point) and sw.sketch_define.sw_select_entity(edge)):
                    missing.append(f"{at}: not selectable")
                    continue
                with sk.silent_dimensions(ctx.app):
                    display = sw.sketch_define.sw_add_dimension(doc, "auto", float(point.X) + 0.004,
                                                                float(point.Y) + 0.004)
                sw.common.sw_clear_selection(doc)
                if display is None:
                    missing.append(f"{at}: SolidWorks made no dimension")
                    continue
                dim = sw.editing.sw_display_dimension(display)
                short = str(dim.FullName).split("@")[0]
                if int(dim.DrivenState) == 1:                    # redundant (another point already fixes it)
                    sw.sketch_define.sw_delete_dimension(doc, display, short)
                    continue
                kept.append({"name": short, "value": round(ctx.from_m(float(dim.SystemValue), unit), 4)})
    finally:
        sw.sketches.sw_close_sketch(doc)
    return kept, missing
