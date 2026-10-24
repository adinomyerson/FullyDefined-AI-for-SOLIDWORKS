"""Finding body faces by geometry, independent of the view."""

import math
from typing import List, Tuple

from . import units
from .com import value
from .results import Err, ToolError

# Largest distance between a point and a face for the point to count as "on" the face
FACE_POINT_TOLERANCE_M = 0.0001            # 0.1 mm


def faces_at_point(doc, x_m: float, y_m: float, z_m: float) -> List[Tuple[float, object]]:
    """(distance in metres, face) for every face of every solid body, closest first.
    Selecting by screen position fails for a face seen edge-on; asking each face for the
    closest point on it to a 3D point does not depend on the view."""
    found = []
    for body in doc.GetBodies2(0, False) or []:                    # 0 = swSolidBody
        for face in value(body, "GetFaces") or []:
            closest = face.GetClosestPointOn(x_m, y_m, z_m)
            found.append((math.dist((x_m, y_m, z_m), tuple(closest[0:3])), face))
    found.sort(key=lambda item: item[0])
    return found


def parallel_to_plane(edge, normal) -> bool:
    """Does the edge lie parallel to a plane with this normal (a straight edge across it, or an arc around it)? A
    reference edge must, or a sketch dimension to it measures to its projected point (a radial distance)."""
    try:
        geo = edge_geometry(edge)
    except Exception:
        return False
    n = normal
    if geo.get("type") == "line" and "direction" in geo:
        return abs(sum(a * b for a, b in zip(geo["direction"], n))) < 1e-6
    if geo.get("type") in ("circle", "arc") and "axis" in geo:
        return abs(abs(sum(a * b for a, b in zip(geo["axis"], n))) - 1) < 1e-6
    return False


def edge_at(doc, p, label: str = None, normal=None, face=None, unit=None):
    """The one model edge through the point p (metres): refused when there is none, or when the point is on two
    (a corner), naming the point as label (the user's words). normal: the sketch face's; the edge must be parallel
    to it (an edge of that face, or one parallel to it). face: the face the point is likely on an edge of (the
    hole's or sketch's face): its edges are tried first, every edge of the body only when the point is on none of
    them, on more than one, or near an end. A refusal for no edge, or one not parallel, names the nearest edges
    that would do, with a point on each in unit."""
    where = label or f"({p[0] * 1000:g}, {p[1] * 1000:g}, {p[2] * 1000:g}) mm"
    near = _near_face_edge(face, p) if face is not None else None
    if near is None:
        near = [(d, e) for d, e in ((distance_to(e, p), e) for e in all_edges(doc)) if d <= PICK_TOLERANCE_M]
    if not near:
        raise ToolError(f"No model edge at {where}: give a point on the edge (list_edges shows them)"
                        f"{nearest_edges(doc, p, normal, unit)}", Err.SELECTION)
    if len(near) > 1:
        raise ToolError(f"{where} is on {len(near)} edges (a corner?): give a point along one edge, away from its "
                        f"ends", Err.SELECTION)
    if normal is not None and not parallel_to_plane(near[0][1], normal):
        raise ToolError(f"The edge at {where} is not parallel to the sketch face (it runs into it, so a dimension to "
                        f"it would measure to a point): use an edge of that face, or one parallel to it"
                        f"{nearest_edges(doc, p, normal, unit)}", Err.SELECTION)
    return near[0][1]


# How many edges a refusal names, and how many of the nearest are looked at for that
NEAREST_NAMED = 3
NEAREST_LOOKED_AT = 12


def nearest_edges(doc, p, normal=None, unit=None) -> str:
    """'; nearest edges (parallel to the face), a point on each: (x, y, z), ... mm' for a refusal: the middles of
    the edges closest to p (with normal, only those parallel to that face); "" when there is none."""
    try:
        ranked = sorted(((distance_to(e, p), e) for e in all_edges(doc)), key=lambda item: item[0])
    except Exception:
        return ""
    points = []
    for _d, edge in ranked[:NEAREST_LOOKED_AT]:
        if normal is not None and not parallel_to_plane(edge, normal):
            continue
        try:
            mid = edge_geometry(edge)["mid"]
        except Exception:
            continue
        points.append("(" + ", ".join(f"{round(units.from_m(v, unit), 3) + 0.0:g}" for v in mid) + ")")
        if len(points) == NEAREST_NAMED:
            break
    if not points:
        return ""
    which = " parallel to the face" if normal is not None else ""
    return f"; nearest edges{which}, a point on each: {', '.join(points)} {units.label(unit)}"


def _near_face_edge(face, p):
    """[(distance, edge)] for the one edge of the face through p, away from its ends; None when that is not so
    (no edge or two, or a corner near): the caller then searches every edge, which also finds the corner."""
    try:
        hits = [(d, e) for d, e in ((distance_to(e, p), e) for e in list(value(face, "GetEdges") or []))
                if d <= PICK_TOLERANCE_M]
        if len(hits) != 1:
            return None
        for getter in ("GetStartVertex", "GetEndVertex"):
            vertex = value(hits[0][1], getter)
            if vertex is not None and math.dist(p, tuple(float(v) for v in value(vertex, "GetPoint"))) \
                    <= PICK_TOLERANCE_M:
                return None
        return hits
    except Exception:
        return None


def distance_to_face(face, p) -> float:
    """How far the point (metres) is from one face: one closest-point query, not a search of every face."""
    try:
        closest = face.GetClosestPointOn(p[0], p[1], p[2])
        return math.dist(tuple(p), tuple(closest[0:3]))
    except Exception:
        return float("inf")


# Default search distance when picking an edge or face by a point
PICK_TOLERANCE_M = 0.0005                   # 0.5 mm


def solid_bodies(doc) -> list:
    try:
        return list(doc.GetBodies2(0, False) or [])            # 0 = swSolidBody
    except Exception:
        return []


def all_faces(doc) -> list:
    faces = []
    for body in solid_bodies(doc):
        faces.extend(list(value(body, "GetFaces") or []))
    return faces


def all_edges(doc) -> list:
    edges = []
    for body in solid_bodies(doc):
        edges.extend(list(value(body, "GetEdges") or []))
    return edges


def distance_to(entity, p: Tuple[float, float, float]) -> float:
    """Distance in metres from a point to the closest point on a face or edge."""
    closest = entity.GetClosestPointOn(p[0], p[1], p[2])
    return math.dist(p, tuple(closest[0:3]))


def points_to_m(points, unit=None) -> List[Tuple[float, float, float]]:
    """[{x,y,z}, ...], [[x,y,z], ...] or a single point, as metres."""
    if points is None:
        return []
    if isinstance(points, dict) or (isinstance(points, (list, tuple)) and len(points) == 3
                                    and all(isinstance(v, (int, float)) for v in points)):
        points = [points]
    out = []
    for p in points:
        if isinstance(p, dict):
            xyz = (p.get("x", 0), p.get("y", 0), p.get("z", 0))
        else:
            xyz = tuple(p)
            if len(xyz) != 3:
                raise ValueError(f"A point needs 3 coordinates, got {p}")
        out.append(tuple(units.to_m(float(v), unit) for v in xyz))
    return out


def point_in_unit(p, unit=None) -> List[float]:
    """A point in metres as [x, y, z] in the user's unit, rounded for output."""
    return [round(units.from_m(v, unit), 3) + 0.0 for v in p]


def face_at_point(faces: list, p, tol: float = PICK_TOLERANCE_M):
    """The single face containing point p (metres): (face, "") or (None, reason)."""
    hits = sorted(((distance_to(f, p), i) for i, f in enumerate(faces)), key=lambda t: t[0])
    if not hits:
        return None, "the model has no solid body"
    on_point = [h for h in hits if h[0] <= tol]
    if not on_point:
        return None, f"no face there (nearest is {hits[0][0] * 1000:.3f} mm away)"
    if len(on_point) > 1 and on_point[1][0] - on_point[0][0] < 1e-7:
        return None, (f"the point lies on an edge shared by {len(on_point)} faces - "
                      f"give a point inside the face")
    return faces[on_point[0][1]], ""


def rotate(v, axis, angle):
    """Rotate vector v about a unit axis by angle (Rodrigues' formula)."""
    c, s = math.cos(angle), math.sin(angle)
    dot = sum(a * b for a, b in zip(axis, v))
    cross = (axis[1] * v[2] - axis[2] * v[1], axis[2] * v[0] - axis[0] * v[2], axis[0] * v[1] - axis[1] * v[0])
    return tuple(v[i] * c + cross[i] * s + axis[i] * dot * (1 - c) for i in range(3))


def parallel(direction, axis) -> bool:
    return abs(sum(a * b for a, b in zip(direction, axis))) > 0.9999


def edge_geometry(edge) -> dict:
    """Geometry of one edge in metres: type (line / circle / arc / other), start, end, length, mid,
    and direction (line) or center, axis, radius (circle, arc). The midpoint is worked out from the end
    points, not from the curve parameters: an edge can run against the direction of its curve, in which
    case the curve at the mean parameter is not the middle of the edge."""
    params = value(edge, "GetCurveParams2")
    start = tuple(float(v) for v in params[0:3])
    end = tuple(float(v) for v in params[3:6])
    t0, t1 = float(params[6]), float(params[7])
    curve = value(edge, "GetCurve")
    info = {"start": start, "end": end}
    try:
        info["length"] = abs(float(curve.GetLength3(t0, t1)))
    except Exception:
        info["length"] = math.dist(start, end)
    chord_mid = tuple((a + b) / 2.0 for a, b in zip(start, end))

    if value(curve, "IsLine"):
        info["type"] = "line"
        info["mid"] = chord_mid
        length = math.dist(start, end)
        if length > 0:
            info["direction"] = tuple((b - a) / length for a, b in zip(start, end))
    elif value(curve, "IsCircle"):
        cp = [float(v) for v in value(curve, "CircleParams")]
        center, axis, radius = tuple(cp[0:3]), tuple(cp[3:6]), cp[6]
        info["center"], info["axis"], info["radius"] = center, axis, radius
        closed = math.dist(start, end) < 1e-9
        info["type"] = "circle" if closed else "arc"
        if closed or radius <= 0:
            info["mid"] = start                       # any point on the circle
        else:
            sweep = info["length"] / radius
            v = tuple(a - b for a, b in zip(start, center))
            sign = 1.0
            landed = tuple(c + r for c, r in zip(center, rotate(v, axis, sweep)))
            if math.dist(landed, end) > 1e-6 * max(1.0, radius * 1000):
                sign = -1.0
            info["mid"] = tuple(c + r for c, r in zip(center, rotate(v, axis, sign * sweep / 2.0)))
    else:
        info["type"] = "other"
        mid = chord_mid
        for t in ((t0 + t1) / 2.0, -(t0 + t1) / 2.0):
            try:
                candidate = tuple(float(x) for x in curve.Evaluate2(t, 0)[0:3])
                if distance_to(edge, candidate) < 1e-7:
                    mid = candidate
                    break
            except Exception:
                pass
        else:
            try:
                mid = tuple(float(x) for x in edge.GetClosestPointOn(*chord_mid)[0:3])
            except Exception:
                pass
        info["mid"] = mid
    return info


def face_geometry(face) -> dict:
    """Geometry of one face in metres: type (plane / cylinder / other), area, box, a point on it,
    and normal (plane) or axis_point, axis, radius (cylinder)."""
    surface = value(face, "GetSurface")
    info = {"area": float(value(face, "GetArea"))}
    box = [float(v) for v in value(face, "GetBox")]
    info["box"] = box
    center = tuple((box[i] + box[i + 3]) / 2.0 for i in range(3))
    try:
        info["point"] = tuple(float(v) for v in face.GetClosestPointOn(*center)[0:3])
    except Exception:
        info["point"] = center
    if value(surface, "IsPlane"):
        info["type"] = "plane"
        info["normal"] = tuple(float(v) for v in value(face, "Normal"))
    elif value(surface, "IsCylinder"):
        info["type"] = "cylinder"
        cp = [float(v) for v in value(surface, "CylinderParams")]
        info["axis_point"], info["axis"], info["radius"] = tuple(cp[0:3]), tuple(cp[3:6]), cp[6]
    else:
        info["type"] = "other"
    return info
