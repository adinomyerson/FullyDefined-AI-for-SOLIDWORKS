"""Read-only checks: the distance between two things (part or assembly) and interference in an assembly."""

import logging
import math

from ..core.api import assembly, doc_type, Err, geometry, ok, selecting, tool, ToolError, units
from ..core.api import sw

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- SolidWorks calls


# ---------------------------------------------------------------- helpers

def _read(measure, attr):
    try:
        return float(getattr(measure, attr))
    except Exception:
        return None


def _unit_vector(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v) if n > 1e-12 else None


def _length(value_m, unit):
    return round(units.from_m(value_m, unit), 4) + 0.0


def _in_assembly(asm, point1, point2, ref1, ref2, between, unit):
    """Measure between two references (core/assembly.resolve_reference)."""
    unit_str = units.label(unit)
    if between == "edges":
        raise ToolError("between = edges is not available in an assembly yet. Measure between faces, sketch "
                        "entities, planes or origins with ref1 / ref2.", Err.INPUT)
    resolved = []
    for index, (ref, point) in enumerate(((ref1, point1), (ref2, point2)), start=1):
        if ref is not None and point is not None:
            raise ToolError(f"Reference {index} is given 2 ways (ref{index}, point{index}): use one", Err.INPUT)
        if ref is None and point is None:
            raise ToolError(f"Reference {index} is missing: give ref{index} (or point{index} for the face at a "
                            f"point)", Err.INPUT)
        resolved.append(assembly.resolve_reference(asm, ref if ref is not None else {"point": point}, index, unit))
    first, second = resolved[0]["info"], resolved[1]["info"]
    if first == second:
        raise ToolError(f"Both references are the same {resolved[0]['text']}. Give two different things.",
                        Err.SELECTION)

    def select(doc) -> bool:
        return all(assembly.select_reference(doc, item, 0) for item in resolved)

    measure = sw.inspection.sw_measure(asm, select)
    if measure is None:
        raise ToolError(f"SolidWorks could not measure between the {resolved[0]['text']} and the "
                        f"{resolved[1]['text']}", Err.INTERNAL)

    data = {"unit": unit_str, "first": first, "second": second}
    parts = []
    distance = _read(measure, "Distance")
    intersect = False
    try:
        intersect = bool(measure.IsIntersect)
    except Exception:
        pass
    if distance is not None and distance >= 0:
        data["distance"] = _length(distance, unit)
        parts.append(f"minimum distance {data['distance']} {unit_str}")
    elif intersect:
        data["distance"] = 0.0
        parts.append("they touch or intersect (distance 0)")
    deltas = [_read(measure, "DeltaX"), _read(measure, "DeltaY"), _read(measure, "DeltaZ")]
    if all(v is not None and v >= 0 for v in deltas):
        data["delta"] = {"x": _length(deltas[0], unit), "y": _length(deltas[1], unit), "z": _length(deltas[2], unit)}

    def direction(item):
        """(unit direction, point in metres, 'plane' | 'axis') of a flat or round face, else None."""
        if item["kind"] != "face":
            return None
        geo = geometry.face_geometry(item["entity"])
        comp = item["component"]
        if geo["type"] == "plane":
            return _unit_vector(assembly.dir_to_asm(comp, geo["normal"])), assembly.to_asm(comp, geo["point"]), "plane"
        if geo["type"] == "cylinder":
            return (_unit_vector(assembly.dir_to_asm(comp, geo["axis"])), assembly.to_asm(comp, geo["axis_point"]),
                    "axis")
        return None

    a, b = direction(resolved[0]), direction(resolved[1])         # angles and axis distances from the geometry
    if a and b and a[0] and b[0]:
        dot = max(-1.0, min(1.0, sum(x * y for x, y in zip(a[0], b[0]))))
        gap = [q - p for p, q in zip(a[1], b[1])]
        if a[2] == b[2]:
            angle = math.degrees(math.acos(abs(dot)))
            parallel = abs(dot) > 1.0 - 1e-10
            data["parallel"] = parallel
            if a[2] == "plane":
                data["angle_deg"] = round(angle, 4) + 0.0
                data["facing"] = "same way" if dot > 0 else "opposite ways"
                if parallel:
                    data["normal_distance"] = _length(abs(sum(g * n for g, n in zip(gap, a[0]))), unit)
                    parts.append(f"parallel planes {data['normal_distance']} {unit_str} apart")
                else:
                    parts.append(f"angle between the planes {data['angle_deg']} deg")
            else:
                data["axis_angle_deg"] = round(angle, 4) + 0.0
                if parallel:
                    along = sum(g * n for g, n in zip(gap, a[0]))
                    across = [g - along * n for g, n in zip(gap, a[0])]
                    axis_distance = math.sqrt(sum(c * c for c in across))
                else:
                    normal = _unit_vector((a[0][1] * b[0][2] - a[0][2] * b[0][1], a[0][2] * b[0][0] - a[0][0] * b[0][2],
                                           a[0][0] * b[0][1] - a[0][1] * b[0][0]))
                    axis_distance = abs(sum(g * n for g, n in zip(gap, normal)))
                data["axis_distance"] = _length(axis_distance, unit)
                parts.append(f"axis to axis {data['axis_distance']} {unit_str}"
                             + ("" if parallel else f", axes at {data['axis_angle_deg']} deg"))
        else:                                                     # a plane and an axis
            data["axis_to_plane_angle_deg"] = round(math.degrees(math.asin(abs(dot))), 4) + 0.0
            if abs(dot) < 1e-10:
                plane, axis = (a, b) if a[2] == "plane" else (b, a)
                data["axis_to_plane_distance"] = _length(abs(sum((q - p) * n for p, q, n in zip(plane[1], axis[1], plane[0]))), unit)
                parts.append(f"axis {data['axis_to_plane_distance']} {unit_str} from the plane")
    elif (a and a[2] == "plane") != (b and b[2] == "plane") and             {resolved[0]["kind"], resolved[1]["kind"]} & {"vertex", "sketch_point", "origin"}:
        # a point and a flat face: the minimum can be to the face's edge, so also the distance to its plane (3.5)
        plane, other = (a, resolved[1]) if a and a[2] == "plane" else (b, resolved[0])
        if other["kind"] in ("vertex", "sketch_point", "origin"):
            p = other.get("point_m") or [units.to_m(v, unit) for v in other["info"]["at"]]
            data["normal_distance"] = _length(abs(sum((q - o) * n for o, q, n in zip(plane[1], p, plane[0]))), unit)
            parts.append(f"{data['normal_distance']} {unit_str} from the face's plane")
    else:                                                         # planes, sketch lines: SolidWorks' own reading
        try:
            if bool(measure.IsParallel):
                data["parallel"] = True
                normal = _read(measure, "NormalDistance")
                if normal is not None and normal >= 0:
                    data["normal_distance"] = _length(normal, unit)
                    parts.append(f"parallel, {data['normal_distance']} {unit_str} apart")
            else:
                angle = _read(measure, "Angle")
                if angle is not None and angle >= 0:
                    data["parallel"] = False
                    data["angle_deg"] = round(math.degrees(angle), 4) + 0.0
                    parts.append(f"angle {data['angle_deg']} deg")
        except Exception as e:
            logger.debug(f"Could not read parallel / angle: {e}")
    return ok(f"Between the {resolved[0]['text']} and the {resolved[1]['text']}: "
              + (", ".join(parts) or "no distance available"), data)


# ---------------------------------------------------------------- tools

_POINT = {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}},
          "required": ["x", "y", "z"]}
_REF = {
    "type": "object",
    "description": "What to pick. One of: {point} the face at an assembly point; {component, point} the face of that component at the point; {component, face_id} a face from list_component_faces; {component, edge_id or vertex_id} from list_component_faces(include); {component, sketch, point_id} or {component, sketch, segment_id} a sketch point or segment from list_component_sketches; {component, plane} / {component, axis} / {component, origin: true} the part's own plane, axis or origin; {plane} / {axis} / {origin: true} the assembly's own. A component inside a sub-assembly is named by its path, e.g. 'Door-1/Slat-3'.",
    "properties": {
        "component": {"type": "string", "description": "Component name from list_components, or a path into a sub-assembly such as 'Door-1/Slat-3'"},
        "point": _POINT,
        "face_id": {"type": "integer", "description": "Face id from list_component_faces"},
        "edge_id": {"type": "integer", "description": "Edge id"},
        "vertex_id": {"type": "integer", "description": "Vertex id"},
        "sketch": {"type": "string", "description": "Name of a sketch in the component"},
        "point_id": {"type": "integer", "description": "Sketch point id from list_component_sketches"},
        "segment_id": {"type": "integer", "description": "Sketch segment id from list_component_sketches"},
        "plane": {"type": "string", "description": "Front, Top, Right or a plane name"},
        "axis": {"type": "string", "description": "Name of a reference axis"},
        "origin": {"type": "boolean", "description": "true = the origin point"}},
    "additionalProperties": False,
}


@tool(
    group="core",
    description="Measure between two model points, or between the two faces or edges at those points. For faces and edges it returns the minimum distance, the normal distance when they are parallel, and the angle when they are not. In an assembly, measure between two references ref1 / ref2, picked as in add_mate, including edges and vertices by edge_id / vertex_id from list_component_faces with include (also on parts inside sub-assemblies): the result has the minimum distance, for two flat faces the angle between them and the gap when parallel, and for two cylinders the axis-to-axis distance. distance is always the MINIMUM (to a small face this can be to its edge); normal_distance is the PERPENDICULAR distance to the plane (parallel faces, or a point and a flat face). Read-only.",
    params={"point1": _POINT, "point2": _POINT,
            "between": {"type": "string", "enum": ["points", "faces", "edges"], "default": "points",
                        "description": "points = straight distance between the two points; faces = between the faces containing the points; edges = between the edges nearest the points"},
            "tolerance": {"type": "number", "description": "How far a point may be from its face / edge (default 0.5 mm)"},
            "ref1": _REF, "ref2": _REF,
            "unit": {"type": "string", "description": "mm, cm, m, inch or ft"}},
    needs="part_or_assembly",
)
def measure_distance(ctx, point1=None, point2=None, between="points", tolerance=None, ref1=None, ref2=None, unit=None):
    doc = ctx.doc
    unit_str = units.label(unit)
    between = (between or "points").strip().lower()
    if between not in ("points", "faces", "edges"):
        raise ToolError("between must be points, faces or edges", Err.INPUT)
    if tolerance is not None and (isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or tolerance < 0):
        raise ToolError("tolerance must be a number, 0 or more", Err.INPUT)
    if doc_type(doc) == 2 and (ref1 is not None or ref2 is not None or between != "points"):
        return _in_assembly(doc, point1, point2, ref1, ref2, between, unit)
    if ref1 is not None or ref2 is not None:
        raise ToolError("ref1 / ref2 are for assemblies. In a part give point1 and point2 (with between = points, "
                        "faces or edges).", Err.INPUT)
    if point1 is None or point2 is None:
        raise ToolError("Give point1 and point2", Err.INPUT)
    try:
        p1, p2 = geometry.points_to_m(point1, unit)[0], geometry.points_to_m(point2, unit)[0]
    except (IndexError, TypeError, ValueError) as e:
        raise ToolError(f"point1 and point2 must each be a point {{x, y, z}} ({e})", Err.INPUT)

    if between == "points":
        delta = [b - a for a, b in zip(p1, p2)]
        data = {"between": "points", "unit": unit_str, "distance": _length(math.sqrt(sum(c * c for c in delta)), unit),
                "delta": {"x": _length(delta[0], unit), "y": _length(delta[1], unit), "z": _length(delta[2], unit)}}
        return ok(f"Distance {data['distance']} {unit_str}", data)

    tol = ctx.to_m(tolerance, unit) if tolerance else geometry.PICK_TOLERANCE_M
    picked, described = [], []
    if between == "faces":
        faces = geometry.all_faces(doc)
        for p in (p1, p2):
            face, reason = geometry.face_at_point(faces, p, tol)
            if face is None:
                raise ToolError(f"Point {geometry.point_in_unit(p, unit)} {unit_str}: {reason}", Err.SELECTION)
            geo = geometry.face_geometry(face)
            picked.append(face)
            row = {"type": geo["type"], "point_on_face": geometry.point_in_unit(geo["point"], unit)}
            if "normal" in geo:
                row["normal"] = selecting.vec(geo["normal"])
            if "radius" in geo:
                row["radius"] = _length(geo["radius"], unit)
            described.append((row, geo))
    else:
        edges = geometry.all_edges(doc)
        if not edges:
            raise ToolError("The model has no solid body edges", Err.SELECTION)
        for p in (p1, p2):
            dist, index = min((geometry.distance_to(e, p), i) for i, e in enumerate(edges))
            if dist > tol:
                raise ToolError(f"No edge within {_length(tol, unit)} {unit_str} of {geometry.point_in_unit(p, unit)} "
                                f"(nearest is {_length(dist, unit)} {unit_str} away)", Err.SELECTION)
            geo = geometry.edge_geometry(edges[index])
            picked.append(edges[index])
            described.append(({"type": geo["type"], "length": _length(geo["length"], unit),
                               "mid": geometry.point_in_unit(geo["mid"], unit)}, geo))
    same = (math.dist(described[0][1].get("point", described[0][1].get("mid")),
                      described[1][1].get("point", described[1][1].get("mid"))) < 1e-9
            and described[0][0] == described[1][0])
    if same:
        raise ToolError(f"Both points are on the same {between[:-1]}. Give points on two different {between}.",
                        Err.SELECTION)

    measure = sw.inspection.sw_measure(doc, sw.inspection.sw_select_entities(picked[0], picked[1]))
    if measure is None:
        raise ToolError(f"SolidWorks could not measure between those {between}", Err.INTERNAL)
    data = {"between": between, "unit": unit_str, "first": described[0][0], "second": described[1][0]}
    parallel = False
    try:
        parallel = bool(measure.IsParallel)
        data["parallel"] = parallel
        data["perpendicular"] = bool(measure.IsPerpendicular)
        data["intersecting"] = bool(measure.IsIntersect)
    except Exception:
        pass
    distance, normal, angle = _read(measure, "Distance"), _read(measure, "NormalDistance"), _read(measure, "Angle")
    parts = []
    if distance is not None and distance >= 0:
        data["distance"] = _length(distance, unit)
        parts.append(f"minimum distance {data['distance']} {unit_str}")
    elif data.get("intersecting"):
        data["distance"] = 0.0
        parts.append("they touch or intersect (distance 0)")
    if parallel and normal is not None and normal >= 0:
        data["normal_distance"] = _length(normal, unit)
        parts.append(f"normal distance {data['normal_distance']} {unit_str}")
    if angle is not None and angle >= 0 and not parallel:
        data["angle_deg"] = round(math.degrees(angle), 4)
        parts.append(f"angle {data['angle_deg']} deg")
    if all(row[0]["type"] in ("circle", "arc", "cylinder") for row in described):
        center = _read(measure, "CenterDistance")
        if center is not None and center >= 0:
            data["center_distance"] = _length(center, unit)
            parts.append(f"center to center {data['center_distance']} {unit_str}")
    deltas = [_read(measure, "DeltaX"), _read(measure, "DeltaY"), _read(measure, "DeltaZ")]
    if all(v is not None and v >= 0 for v in deltas):
        data["delta"] = {"x": _length(deltas[0], unit), "y": _length(deltas[1], unit), "z": _length(deltas[2], unit)}
    return ok(f"Between the two {between}: " + (", ".join(parts) or "no distance available"), data)


@tool(
    group="assembly",
    description="Run SolidWorks' interference check on the whole active assembly (parts inside sub-assemblies are checked one by one). Returns the number of interferences and, for each, the two components and the overlapping volume. Faces that merely touch are not counted unless treat_coincident_as_interference is true. Read-only.",
    params={"treat_coincident_as_interference": {"type": "boolean", "default": False,
                                                 "description": "Also count faces that merely touch"},
            "unit": {"type": "string", "description": "Unit for the volumes, cubed (mm, cm, m, inch or ft)"}},
    needs="assembly",
)
def check_interference(ctx, treat_coincident_as_interference=False, unit=None):
    asm = ctx.doc
    if not isinstance(treat_coincident_as_interference, bool):
        raise ToolError("treat_coincident_as_interference must be true or false", Err.INPUT)
    message, data = assembly.interferences(asm, treat_coincident_as_interference, unit)
    return ok(message, data)
