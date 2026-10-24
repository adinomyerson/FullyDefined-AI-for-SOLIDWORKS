"""
Selecting body faces and edges by geometry, and describing them for output. Shared by the selection
tools and by tools that need faces selected first (shell).
"""

import hashlib

import pythoncom
import win32com.client

from . import geometry, units
from .com import value
from .results import Err, ToolError, ok

AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
DIRECTIONS = {"+x": (1.0, 0.0, 0.0), "-x": (-1.0, 0.0, 0.0), "+y": (0.0, 1.0, 0.0),
              "-y": (0.0, -1.0, 0.0), "+z": (0.0, 0.0, 1.0), "-z": (0.0, 0.0, -1.0)}


# Face and edge ids are positions in the body's lists: they hold only until the geometry changes. The stamp of the
# geometry each listing saw, per document and kind ("faces" / "edges"), so ids from before a change are refused.
_listed = {}


def stamp_of(kind: str, geos: list) -> str:
    """A short fingerprint of the geometry a listing read (face_geometry / edge_geometry rows of every face or edge,
    in list order): faces by area and box, edges by length and midpoint. Any feature, dimension change or rebuild
    that moves, adds or removes one changes it. Made from what the listing reads anyway: no extra SolidWorks calls."""
    keys = ("area", "box") if kind == "faces" else ("length", "mid")
    parts = []
    for geo in geos:
        vals = []
        for k in keys:
            v = geo.get(k)
            vals += [float(x) for x in v] if isinstance(v, (list, tuple)) else ([float(v)] if v is not None else [])
        parts.append(",".join(f"{x:.7g}" for x in vals))
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:8]


def geometry_stamp(doc, kind: str) -> str:
    """The stamp of the document's faces or edges as they are now (read again: for checking ids)."""
    if kind == "faces":
        return stamp_of(kind, [geometry.face_geometry(f) for f in geometry.all_faces(doc)])
    return stamp_of(kind, [geometry.edge_geometry(e) for e in geometry.all_edges(doc)])


def _doc_key(doc) -> str:
    try:
        return str(value(doc, "GetPathName")) or str(value(doc, "GetTitle"))
    except Exception:
        return ""


def remember_listing(doc, kind: str, geos: list) -> str:
    """list_faces / list_edges: the geometry the ids just listed belong to (geos: what the listing read, every face
    or edge in list order). Returns its stamp."""
    stamp = stamp_of(kind, geos)
    _listed[(_doc_key(doc), kind)] = stamp
    return stamp


def check_ids(doc, kind: str) -> None:
    """Refuse ids (kind "faces" or "edges") unless this document's geometry is the one they were listed from."""
    tool = f"list_{kind}"
    listed = _listed.get((_doc_key(doc), kind))
    if listed is None:
        raise ToolError(f"{kind[:-1].capitalize()} ids come from {tool}, and it has not listed this part's {kind} "
                        f"since it was opened or saved under a new name: call {tool} first, or select by points.",
                        Err.SELECTION)
    if listed != geometry_stamp(doc, kind):
        raise ToolError(f"The geometry changed since {tool} listed these ids, so they may point at other {kind} "
                        f"now: call {tool} again, or select by points.", Err.SELECTION)


def _null():
    return win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)


def vec(v):
    return [round(c, 4) + 0.0 for c in v]


def edge_row(index, geo, unit=None):
    row = {"id": index, "type": geo["type"], "length": round(units.from_m(geo["length"], unit), 3),
           "mid": geometry.point_in_unit(geo["mid"], unit)}
    if geo["type"] != "circle":
        row["start"] = geometry.point_in_unit(geo["start"], unit)
        row["end"] = geometry.point_in_unit(geo["end"], unit)
    if "direction" in geo:
        row["direction"] = vec(geo["direction"])
    if "radius" in geo:
        row["radius"] = round(units.from_m(geo["radius"], unit), 3)
        row["center"] = geometry.point_in_unit(geo["center"], unit)
        row["axis"] = vec(geo["axis"])
    return row


def face_row(index, geo, unit=None):
    scale = units.from_m(1.0, unit)
    row = {"id": index, "type": geo["type"], "area": round(geo["area"] * scale * scale, 3),
           "point_on_face": geometry.point_in_unit(geo["point"], unit),
           "box": geometry.point_in_unit(geo["box"], unit)}
    if "normal" in geo:
        row["normal"] = vec(geo["normal"])
    if "radius" in geo:
        row["radius"] = round(units.from_m(geo["radius"], unit), 3)
        row["axis"] = vec(geo["axis"])
    return row


def check_edge_filters(edge_type, parallel_to) -> None:
    if edge_type and str(edge_type).lower() not in ("line", "circle", "arc", "other"):
        raise ToolError("edge_type must be line, circle, arc or other", Err.INPUT)
    if parallel_to and str(parallel_to).lower() not in AXES:
        raise ToolError("parallel_to must be x, y or z", Err.INPUT)


def edge_passes(geo, edge_type, parallel_to) -> bool:
    if edge_type and geo["type"] != str(edge_type).lower():
        return False
    if parallel_to:
        if "direction" not in geo or not geometry.parallel(geo["direction"], AXES[str(parallel_to).lower()]):
            return False
    return True


def pick_faces(doc, points=None, face_ids=None, normal=None, append=False, unit=None, tolerance=None):
    """Select faces by point, id or normal direction. Returns a result; raises ToolError."""
    unit_str = units.label(unit)
    tol = units.to_m(tolerance, unit) if tolerance else geometry.PICK_TOLERANCE_M
    pts = geometry.points_to_m(points, unit)
    faces = geometry.all_faces(doc)
    if not faces:
        raise ToolError("The model has no solid body faces", Err.SELECTION)

    chosen = []
    for p in pts:
        face, reason = geometry.face_at_point(faces, p, tol)
        if face is None:
            raise ToolError(f"Point {geometry.point_in_unit(p, unit)} {unit_str}: {reason}", Err.SELECTION)
        chosen.append(face)
    if face_ids:
        check_ids(doc, "faces")
    for raw in face_ids or []:
        try:
            index = int(raw)
        except (TypeError, ValueError):
            raise ToolError(f"Bad face id '{raw}'", Err.INPUT)
        if index < 1 or index > len(faces):
            raise ToolError(f"Face {index} does not exist (the model has {len(faces)} faces)", Err.SELECTION)
        chosen.append(faces[index - 1])
    if normal:
        key = str(normal).strip().lower()
        if key not in DIRECTIONS:
            raise ToolError("normal must be +x, -x, +y, -y, +z or -z", Err.INPUT)
        wanted = DIRECTIONS[key]
        for face in faces:
            geo = geometry.face_geometry(face)
            if geo["type"] == "plane" and sum(a * b for a, b in zip(geo["normal"], wanted)) > 0.9999:
                chosen.append(face)
    if not chosen:
        raise ToolError("No face matched. Give points, face_ids or normal.", Err.SELECTION)

    if not append:
        doc.ClearSelection2(True)
    rows, seen = [], []
    for face in chosen:
        geo = geometry.face_geometry(face)
        key = (geo["type"], round(geo["area"], 12), tuple(round(v, 7) for v in geo["point"]))
        if key in seen:
            continue
        seen.append(key)
        if face.Select4(True, _null()):
            row = face_row(len(rows) + 1, geo, unit)
            row.pop("id", None)
            rows.append(row)
    if not rows:
        raise ToolError("Could not select the face(s)", Err.SELECTION)
    total = doc.SelectionManager.GetSelectedObjectCount2(-1)
    return ok(f"Selected {len(rows)} face(s); {total} item(s) selected in total",
              {"selected": len(rows), "total_selected": total, "unit": unit_str, "faces": rows})
