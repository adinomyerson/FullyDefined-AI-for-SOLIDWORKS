"""
Assemblies, read only: components by name or path, their placement, their faces and sketch points in
ASSEMBLY coordinates, and what a mate or measure reference points at. Shared by the component, mate and
measuring tools. Reading, plus the few changes that more than one tool needs (select_reference, delete_mate,
interferences); every other change is in core/sw.

A component transform maps part coordinates to the assembly: asm = part * R + t (R's rows are where the
part's x, y, z axes point).
"""

import logging
import math
from typing import Dict, List, Optional, Tuple

import pythoncom
import win32com.client

from . import units
from .com import null_object, plane_feature_name, value
from .geometry import PICK_TOLERANCE_M, edge_geometry, face_geometry, point_in_unit, points_to_m
from .results import Err, ToolError
from .selecting import vec

logger = logging.getLogger(__name__)

SKETCH_TYPES = ("ProfileFeature", "3DProfileFeature")
SEGMENT_TYPES = {0: "line", 1: "arc", 2: "ellipse", 3: "spline", 4: "text", 5: "parabola"}
REFERENCE_KEYS = ("component", "point", "face_id", "edge_id", "vertex_id", "sketch", "point_id", "segment_id", "plane",
                  "axis", "origin")
REFERENCE_HELP = (
    "A reference is one of: {point} a face at a point; {component, point} a face of that "
    "component at a point; {component, face_id} a face from list_component_faces; {component, edge_id} / "
    "{component, vertex_id} an edge / vertex from list_component_faces(include=[\"edges\", \"vertices\"]); "
    "{component, sketch, point_id} or {component, sketch, segment_id} a sketch point or "
    "segment from list_component_sketches; {component, plane} / {component, axis} / "
    "{component, origin: true} the part's own plane, axis or origin; {plane} / {axis} / "
    "{origin: true} the assembly's own.")


# ---------------------------------------------------------------- components

def components(asm) -> list:
    """The top-level components."""
    try:
        return list(asm.GetComponents(True) or [])
    except Exception as e:
        logger.debug(f"GetComponents failed: {e}")
        return []


def all_components(asm) -> list:
    """Every component at every level; a nested one is named 'Sub-1/Part-1'."""
    try:
        return list(asm.GetComponents(False) or [])
    except Exception as e:
        logger.debug(f"GetComponents failed: {e}")
        return []


def find_component(asm, name) -> Tuple[Optional[object], str]:
    """A component by name ('Plate-1') or path ('Door-1/Slat-3'), any case; the last part of a path is
    enough when only one component ends with it. (component, "") or (None, reason)."""
    wanted = str(name or "").strip().replace("\\", "/").strip("/").lower()
    if not wanted:
        return None, "the component name is empty"
    comps = all_components(asm)
    names = [str(c.Name2) for c in comps]
    for comp, comp_name in zip(comps, names):
        if comp_name.lower() == wanted:
            return comp, ""
    tails = [c for c, n in zip(comps, names) if n.lower().endswith("/" + wanted)]
    if len(tails) == 1:
        return tails[0], ""
    if len(tails) > 1:
        return None, (f"'{name}' fits {len(tails)} components "
                      f"({', '.join(str(c.Name2) for c in tails[:8])}) - give the full path")
    shown = ", ".join(names[:40]) + (" ..." if len(names) > 40 else "")
    return None, f"no component named '{name}'. Components: {shown or 'none'}"


def component(asm, name):
    """find_component, or a refusal."""
    comp, reason = find_component(asm, name)
    if comp is None:
        raise ToolError(reason[0].upper() + reason[1:], Err.SELECTION)
    return comp


def top_level_component(asm, name, action: str):
    """A top-level component, or a refusal naming the sub-assembly it sits in."""
    comp = component(asm, name)
    full = str(comp.Name2)
    if "/" in full:
        parent = full.split("/")[0]
        raise ToolError(f"{full} sits inside the sub-assembly {parent}. Only a top-level component can be "
                        f"{action} here: use {parent} itself, or open the sub-assembly and do it there.", Err.INPUT)
    return comp


def is_hidden(comp) -> bool:
    try:
        return int(comp.Visible) == 0
    except Exception:
        return False


# ---------------------------------------------------------------- placement

def transform(comp) -> Tuple[tuple, tuple, float]:
    """(rotation rows, translation, scale) of a component: asm = part * R + t."""
    a = [float(v) for v in value(comp.Transform2, "ArrayData")]
    return (tuple(a[0:3]), tuple(a[3:6]), tuple(a[6:9])), tuple(a[9:12]), (a[12] or 1.0)


def to_part(comp, p):
    """An assembly-space point in the component's own coordinates."""
    R, t, scale = transform(comp)
    v = [p[i] - t[i] for i in range(3)]
    return tuple(sum(v[i] * R[j][i] for i in range(3)) / scale for j in range(3))


def to_asm(comp, p):
    """A point in the component's own coordinates in assembly space."""
    R, t, scale = transform(comp)
    return tuple(sum(p[j] * R[j][i] for j in range(3)) * scale + t[i] for i in range(3))


def dir_to_asm(comp, v):
    """A direction in the component's own coordinates in assembly space."""
    R, _, _ = transform(comp)
    return tuple(sum(v[j] * R[j][i] for j in range(3)) for i in range(3))


def rotation_rows(rx=None, ry=None, rz=None, axis=None, angle=None):
    """The rows of a component transform for a rotation, or a refusal. Degrees: rx, ry, rz about the
    assembly X, then Y, then Z axis; or one turn of `angle` about `axis`."""
    def number(v, label):
        if v is None:
            return 0.0
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ToolError(f"{label} must be a number of degrees", Err.INPUT)
        return float(v)

    euler = [v for v in (rx, ry, rz) if v is not None]
    if (axis is not None or angle is not None) and euler:
        raise ToolError("Give rx / ry / rz or axis + angle, not both", Err.INPUT)
    if (axis is None) != (angle is None):
        raise ToolError("axis and angle go together", Err.INPUT)
    if axis is not None:
        try:
            a = ((float(axis["x"]), float(axis["y"]), float(axis["z"])) if isinstance(axis, dict)
                 else tuple(float(c) for c in axis))
            if len(a) != 3:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ToolError("axis must be a vector {x, y, z}", Err.INPUT)
        length = math.sqrt(sum(c * c for c in a))
        if not math.isfinite(length) or length < 1e-9:
            raise ToolError("axis must not be a zero vector", Err.INPUT)
        a = tuple(c / length for c in a)
        turn = number(angle, "angle")
        c, s_ = math.cos(math.radians(turn)), math.sin(math.radians(turn))
        x, y, z = a
        q = ((c + x * x * (1 - c), x * y * (1 - c) - z * s_, x * z * (1 - c) + y * s_),
             (y * x * (1 - c) + z * s_, c + y * y * (1 - c), y * z * (1 - c) - x * s_),
             (z * x * (1 - c) - y * s_, z * y * (1 - c) + x * s_, c + z * z * (1 - c)))
    else:
        angles = [math.radians(number(v, label)) for v, label in ((rx, "rx"), (ry, "ry"), (rz, "rz"))]
        cx, sx = math.cos(angles[0]), math.sin(angles[0])
        cy, sy = math.cos(angles[1]), math.sin(angles[1])
        cz, sz = math.cos(angles[2]), math.sin(angles[2])
        q = ((cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx),     # q = Rz * Ry * Rx (X first)
             (sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx),
             (-sy, cy * sx, cy * cx))
    return tuple(tuple(q[j][i] for j in range(3)) for i in range(3))      # rows of a transform: columns of q


def apply_rows(rows, v):
    """Rotate a vector: component coordinates -> assembly directions."""
    return tuple(sum(v[j] * rows[j][i] for j in range(3)) for i in range(3))


def positions(asm) -> Dict[str, tuple]:
    """name -> (rotation, translation) of every component at every level."""
    out = {}
    for comp in all_components(asm):
        try:
            R, t, _ = transform(comp)
            out[str(comp.Name2)] = (R, t)
        except Exception:
            pass
    return out


def moved_since(asm, before: Dict[str, tuple], unit) -> List[Dict]:
    """The components whose position or orientation changed since `before` (positions())."""
    moved = []
    for name, (R, t) in positions(asm).items():
        old = before.get(name)
        if old is None:
            continue
        turned = max(abs(a - b) for row_a, row_b in zip(R, old[0]) for a, b in zip(row_a, row_b)) > 1e-9
        if math.dist(t, old[1]) > 1e-9 or turned:
            row = {"name": name, "origin_in_assembly": point_in_unit(t, unit)}
            if turned:
                row["turned"] = True
            moved.append(row)
    return moved


def component_row(comp, unit) -> Dict:
    """What list_components says about one component."""
    row = {"name": comp.Name2}
    try:
        row["file"] = value(comp, "GetPathName")
    except Exception:
        pass
    try:
        row["fixed"] = bool(value(comp, "IsFixed"))
    except Exception:
        pass
    try:
        if str(row.get("file", "")).lower().endswith(".sldasm"):
            row["solving"] = "flexible" if int(comp.Solving) == 1 else "rigid"
    except Exception:
        pass
    if is_hidden(comp):
        row["hidden"] = True
    try:
        R, t, _ = transform(comp)
        row["origin_in_assembly"] = point_in_unit(t, unit)
        identity = all(abs(R[i][j] - (1.0 if i == j else 0.0)) < 1e-9 for i in range(3) for j in range(3))
        if not identity:
            row["x_axis_in_assembly"] = [round(v, 4) + 0.0 for v in R[0]]
            row["y_axis_in_assembly"] = [round(v, 4) + 0.0 for v in R[1]]
            row["z_axis_in_assembly"] = [round(v, 4) + 0.0 for v in R[2]]
    except Exception:
        pass
    try:
        box = [float(v) for v in comp.GetBox(False, False)]
        row["box"] = point_in_unit(box, unit)
    except Exception:
        pass
    return row


def mate_rows(asm) -> List[Dict]:
    """name and type of every mate."""
    mates = []
    try:
        feat = value(asm, "FirstFeature")
        while feat is not None and len(mates) < 2000:
            if value(feat, "GetTypeName2") == "MateGroup":
                sub = value(feat, "GetFirstSubFeature")
                while sub is not None:
                    mates.append({"name": sub.Name, "type": value(sub, "GetTypeName2")})
                    sub = value(sub, "GetNextSubFeature")
            feat = value(feat, "GetNextFeature")
    except Exception as e:
        logger.debug(f"Could not list mates: {e}")
    return mates


def mate_error_codes(asm) -> Dict[str, int]:
    """Error code of every mate (0 = fine)."""
    codes = {}
    try:
        feat = value(asm, "FirstFeature")
        while feat is not None and len(codes) < 5000:
            if value(feat, "GetTypeName2") == "MateGroup":
                sub = value(feat, "GetFirstSubFeature")
                while sub is not None:
                    try:
                        codes[sub.Name] = int(value(sub, "GetErrorCode"))
                    except Exception:
                        codes[sub.Name] = 0
                    sub = value(sub, "GetNextSubFeature")
            feat = value(feat, "GetNextFeature")
    except Exception as e:
        logger.debug(f"Could not read mate errors: {e}")
    return codes


def new_mate_errors(asm, before: Dict[str, int]) -> List[str]:
    """Mates that have an error now and had none before."""
    return sorted(n for n, code in mate_error_codes(asm).items() if code != 0 and before.get(n, 0) == 0)


# ---------------------------------------------------------------- faces

def _bodies(comp, kind: int) -> list:
    try:
        info = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_VARIANT, None)
        return list(comp.GetBodies3(kind, info) or [])          # 0 = solid, 1 = sheet
    except Exception as e:
        logger.debug(f"GetBodies3({kind}) failed: {e}")
        return []


def solid_bodies(comp) -> list:
    bodies = _bodies(comp, 0)
    if bodies:
        return bodies
    try:
        body = value(comp, "GetBody")
        return [body] if body is not None else []
    except Exception:
        return []


def faces(comp) -> list:
    """The faces of a component; a face id is its position in this list, from 1. Faces of surface bodies
    follow those of the solid bodies."""
    out = []
    for body in solid_bodies(comp):
        out.extend(list(value(body, "GetFaces") or []))
    for body in _bodies(comp, 1):
        out.extend(list(value(body, "GetFaces") or []))
    return out


def edges(comp) -> list:
    """The edges of a component, in the same body order as faces(); an edge id is its position here, from 1."""
    out = []
    for body in solid_bodies(comp) + _bodies(comp, 1):
        out.extend(list(value(body, "GetEdges") or []))
    return out


def vertices(comp) -> list:
    """The vertices of a component, in the same body order as faces(); a vertex id is its position here, from 1."""
    out = []
    for body in solid_bodies(comp) + _bodies(comp, 1):
        out.extend(list(value(body, "GetVertices") or []))
    return out


def edge_info(comp, edge, edge_id: int, unit) -> Dict:
    """What a component edge is, in assembly coordinates and the user's unit (as list_edges describes one)."""
    geo = edge_geometry(edge)
    info = {"kind": "edge", "component": str(comp.Name2), "edge_id": edge_id, "type": geo["type"],
            "length": round(units.from_m(geo["length"], unit), 3),
            "mid": point_in_unit(to_asm(comp, geo["mid"]), unit)}
    if geo["type"] != "circle":
        info["start"] = point_in_unit(to_asm(comp, geo["start"]), unit)
        info["end"] = point_in_unit(to_asm(comp, geo["end"]), unit)
    if "direction" in geo:
        info["direction"] = vec(dir_to_asm(comp, geo["direction"]))
    if "radius" in geo:
        info["radius"] = round(units.from_m(geo["radius"], unit), 3)
        info["center"] = point_in_unit(to_asm(comp, geo["center"]), unit)
        info["axis"] = vec(dir_to_asm(comp, geo["axis"]))
    return info


def vertex_info(comp, vertex, vertex_id: int, unit) -> Dict:
    at = tuple(float(v) for v in list(value(vertex, "GetPoint"))[0:3])
    return {"kind": "vertex", "component": str(comp.Name2), "vertex_id": vertex_id,
            "at": point_in_unit(to_asm(comp, at), unit)}


def face_info(comp, face, face_id: int, unit) -> Dict:
    """What a component face is, in assembly coordinates and the user's unit."""
    geo = face_geometry(face)
    scale = units.from_m(1.0, unit)
    info = {"kind": "face", "component": str(comp.Name2), "face_id": face_id, "type": geo["type"],
            "area": round(geo["area"] * scale * scale, 3),
            "point_on_face": point_in_unit(to_asm(comp, geo["point"]), unit)}
    if "normal" in geo:
        info["normal"] = vec(dir_to_asm(comp, geo["normal"]))
    if "radius" in geo:
        info["radius"] = round(units.from_m(geo["radius"], unit), 3)
        info["axis"] = vec(dir_to_asm(comp, geo["axis"]))
        info["axis_point"] = point_in_unit(to_asm(comp, geo["axis_point"]), unit)
    try:
        if int(value(value(face, "GetBody"), "GetType")) == 1:
            info["body"] = "surface"
    except Exception:
        pass
    return info


def face_text(info: Dict) -> str:
    text = (f"{'surface ' if info.get('body') == 'surface' else ''}"
            f"{info['type']} face {info['face_id']} of {info['component']}")
    if "normal" in info:
        text += f" (normal {info['normal']})"
    elif "radius" in info:
        text += f" (radius {info['radius']}, axis {info['axis']})"
    return text


def face_at(asm, p, tol: float, only=None):
    """The component face containing an assembly-space point: (face, component, face id), or
    (None, None, reason). Looks at `only`, else at every part at every level."""
    hits = []
    for comp in ([only] if only is not None else all_components(asm)):
        try:
            if bool(value(comp, "IsSuppressed")):
                continue
        except Exception:
            pass
        comp_faces = faces(comp)
        if not comp_faces:
            continue                                # a sub-assembly has no body: its parts are looked at
        local = to_part(comp, p)
        for index, face in enumerate(comp_faces, start=1):
            closest = face.GetClosestPointOn(local[0], local[1], local[2])
            hits.append((math.dist(local, tuple(closest[0:3])), face, comp, index))
    if not hits:
        if only is not None:
            return None, None, f"{only.Name2} has no solid body"
        return None, None, "the assembly has no components with solid bodies"
    hits.sort(key=lambda h: h[0])
    on_point = [h for h in hits if h[0] <= tol]
    if not on_point:
        return None, None, f"no component face there (nearest is {hits[0][0] * 1000:.3f} mm away)"
    if len(on_point) > 1 and on_point[1][0] - on_point[0][0] < 1e-7:
        names = sorted({str(h[2].Name2) for h in on_point})
        hint = ("give a point inside one face" if len(names) == 1 else
                "name the component as well, or use a face id from list_component_faces")
        return None, None, f"the point lies on {len(on_point)} faces at once ({', '.join(names)}) - {hint}"
    _, face, comp, index = on_point[0]
    return face, comp, index


# ---------------------------------------------------------------- sketches inside components

def sketches(comp) -> list:
    """[(name, feature)] for the sketches of a component, in tree order."""
    found = []
    try:
        feat = value(comp, "FirstFeature")
        while feat is not None and len(found) < 2000:
            if value(feat, "GetTypeName2") in SKETCH_TYPES:
                found.append((str(feat.Name), feat))
            feat = value(feat, "GetNextFeature")
    except Exception as e:
        logger.debug(f"Could not walk the features of {comp.Name2}: {e}")
    return found


def sketch(comp, name):
    """A sketch of a component by name, or a refusal listing its sketches."""
    found = sketches(comp)
    wanted = str(name or "").strip().lower()
    for sketch_name, feat in found:
        if sketch_name.lower() == wanted:
            sk = value(feat, "GetSpecificFeature2")
            if sk is None:
                break
            return sk
    listed = ", ".join(n for n, _ in found) or "none"
    raise ToolError(f"{comp.Name2} has no sketch named '{name}'. Its sketches: {listed}", Err.SELECTION)


def _sketch_to_part(sk, p):
    """A sketch-space point in the coordinates of the part that owns the sketch."""
    a = [float(v) for v in value(sk.ModelToSketchTransform, "ArrayData")]
    rel = [p[j] - a[9 + j] for j in range(3)]
    scale = a[12] or 1.0
    return tuple(sum(rel[j] * a[3 * i + j] for j in range(3)) / scale for i in range(3))


def _sketch_point_asm(comp, sk, point):
    return to_asm(comp, _sketch_to_part(sk, (float(point.X), float(point.Y), float(point.Z))))


def sketch_point_rows(comp, sk, unit) -> List[Tuple[Dict, object]]:
    """The points of a sketch: SolidWorks' own list first (ids as before), then any circle or arc centre that
    list leaves out. Each row says what the point is: "of": ["2:center"], ["1:end", "3:start"], or []."""
    found = [(point, (float(point.X), float(point.Y), float(point.Z)))
             for point in list(value(sk, "GetSketchPoints2") or [])]
    roles = []
    for seg_index, seg in enumerate(list(value(sk, "GetSketchSegments") or []), start=1):
        for role, getter in (("start", "GetStartPoint2"), ("end", "GetEndPoint2"), ("center", "GetCenterPoint2")):
            try:
                point = value(seg, getter)
            except Exception:
                point = None
            if point is None:
                continue
            here = (float(point.X), float(point.Y), float(point.Z))
            roles.append((here, f"{seg_index}:{role}"))
            if role == "center" and not any(math.dist(here, other) < 1e-9 for _, other in found):
                found.append((point, here))
    rows = []
    for index, (point, here) in enumerate(found, start=1):
        of = [label for where, label in roles if math.dist(here, where) < 1e-9]
        if len(of) > 1 and any(lb.endswith(":start") for lb in of) and any(lb.endswith(":end") for lb in of):
            of = [lb for lb in of if not (lb.endswith(":end") and lb[:-4] + ":start" in of)]   # a full circle
        rows.append(({"id": index, "at": point_in_unit(_sketch_point_asm(comp, sk, point), unit), "of": of}, point))
    return rows


def sketch_segment_rows(comp, sk, unit) -> List[Tuple[Dict, object]]:
    rows = []
    for index, seg in enumerate(list(value(sk, "GetSketchSegments") or []), start=1):
        row = {"id": index, "type": SEGMENT_TYPES.get(int(value(seg, "GetType")), "other")}
        try:
            if bool(seg.ConstructionGeometry):
                row["construction"] = True
        except Exception:
            pass
        for key, getter in (("start", "GetStartPoint2"), ("end", "GetEndPoint2"), ("center", "GetCenterPoint2")):
            if key == "center" and row["type"] != "arc":
                continue
            try:
                point = value(seg, getter)
                if point is not None:
                    row[key] = point_in_unit(_sketch_point_asm(comp, sk, point), unit)
            except Exception:
                pass
        if row["type"] == "arc":
            try:
                row["radius"] = round(units.from_m(float(value(seg, "GetRadius")), unit), 3)
            except Exception:
                pass
            if row.get("start") is not None and row.get("start") == row.get("end"):
                row["type"] = "circle"
        rows.append((row, seg))
    return rows


# ---------------------------------------------------------------- mate and measure references

def resolve_reference(asm, ref, index: int, unit) -> Dict:
    """What a mate or measure reference points at, or a refusal. The result has kind, component (or None),
    text, info, and what selecting it needs (entity, feature or select_name)."""
    unit_str = units.label(unit)

    def bad(message, code=Err.INPUT):
        return ToolError(f"Reference {index}: {message}", code)

    if not isinstance(ref, dict) or not ref:
        raise bad(f"must be an object. {REFERENCE_HELP}")
    unknown = [k for k in ref if k not in REFERENCE_KEYS]
    if unknown:
        raise bad(f"unknown field(s) {', '.join(map(str, unknown))}. {REFERENCE_HELP}")

    comp = None
    if ref.get("component") is not None:
        comp, reason = find_component(asm, ref["component"])
        if comp is None:
            raise bad(reason, Err.SELECTION)
        try:
            if bool(value(comp, "IsSuppressed")):
                raise bad(f"{comp.Name2} is suppressed", Err.SELECTION)
        except ToolError:
            raise
        except Exception:
            pass

    origin = ref.get("origin")
    if origin is not None and not isinstance(origin, bool):
        raise bad("origin must be true")
    whats = [k for k in ("point", "face_id", "edge_id", "vertex_id", "sketch", "plane", "axis") if ref.get(k) is not None]
    if origin:
        whats.append("origin")
    if len(whats) != 1:
        raise bad(("it names nothing to pick. " if not whats else
                   f"it names {' and '.join(whats)} - give one of them. ") + REFERENCE_HELP)
    what = whats[0]
    if what != "sketch" and (ref.get("point_id") is not None or ref.get("segment_id") is not None):
        raise bad("point_id / segment_id need the sketch name as well")
    if comp is None and what in ("face_id", "edge_id", "vertex_id", "sketch"):
        raise bad(f"{what} needs the component name as well")
    where = str(comp.Name2) if comp is not None else "the assembly"

    if what == "point":
        try:
            p = points_to_m(ref["point"], unit)[0]
        except (IndexError, TypeError, ValueError):
            raise bad("point must be a point {x, y, z}")
        if any(abs(v) > 500 for v in p):
            raise bad("the point is more than 500 m from the origin")
        face, owner, face_id = face_at(asm, p, PICK_TOLERANCE_M, only=comp)
        if face is None:
            raise bad(f"at {point_in_unit(p, unit)} {unit_str}: {face_id}", Err.SELECTION)
        info = face_info(owner, face, face_id, unit)
        return {"kind": "face", "component": owner, "entity": face, "info": info, "text": face_text(info)}

    if what == "face_id":
        face_id = ref["face_id"]
        if isinstance(face_id, bool) or not isinstance(face_id, int):
            raise bad("face_id must be a whole number from list_component_faces")
        comp_faces = faces(comp)
        if not comp_faces:
            raise bad(f"{comp.Name2} has no solid body (for a sub-assembly, name the part inside it, e.g. "
                      f"'{comp.Name2}/Part-1')", Err.SELECTION)
        if face_id < 1 or face_id > len(comp_faces):
            raise bad(f"{comp.Name2} has faces 1 to {len(comp_faces)}, not {face_id}", Err.SELECTION)
        info = face_info(comp, comp_faces[face_id - 1], face_id, unit)
        return {"kind": "face", "component": comp, "entity": comp_faces[face_id - 1], "info": info,
                "text": face_text(info)}

    if what in ("edge_id", "vertex_id"):
        noun = what[:-3]
        nouns = "edges" if noun == "edge" else "vertices"
        wanted = ref[what]
        if isinstance(wanted, bool) or not isinstance(wanted, int):
            raise bad(f"{what} must be a whole number from list_component_faces(include=[\"{nouns}\"])")
        found = edges(comp) if noun == "edge" else vertices(comp)
        if not found:
            raise bad(f"{comp.Name2} has no {nouns} (for a sub-assembly, name the part inside it, e.g. "
                      f"'{comp.Name2}/Part-1')", Err.SELECTION)
        if wanted < 1 or wanted > len(found):
            raise bad(f"{comp.Name2} has {nouns} 1 to {len(found)}, not {wanted}", Err.SELECTION)
        entity = found[wanted - 1]
        if noun == "edge":
            info = edge_info(comp, entity, wanted, unit)
            text = f"{info['type']} edge {wanted} of {comp.Name2}"
        else:
            info = vertex_info(comp, entity, wanted, unit)
            text = f"vertex {wanted} of {comp.Name2} (at {info['at']})"
        found = {"kind": noun, "component": comp, "entity": entity, "info": info, "text": text}
        if noun == "vertex":                               # unrounded, for measuring
            found["point_m"] = to_asm(comp, tuple(float(v) for v in list(value(entity, "GetPoint"))[0:3]))
        return found

    if what == "sketch":
        point_id, segment_id = ref.get("point_id"), ref.get("segment_id")
        if (point_id is None) == (segment_id is None):
            raise bad("a sketch reference needs point_id or segment_id (one of them) from list_component_sketches")
        wanted = point_id if point_id is not None else segment_id
        if isinstance(wanted, bool) or not isinstance(wanted, int):
            raise bad("point_id / segment_id must be a whole number")
        try:
            sk = sketch(comp, ref["sketch"])
        except ToolError as e:
            raise bad(e.message, Err.SELECTION)
        sketch_name = str(sk.Name)
        rows = sketch_point_rows(comp, sk, unit) if point_id is not None else sketch_segment_rows(comp, sk, unit)
        noun = "point" if point_id is not None else "segment"
        if wanted < 1 or wanted > len(rows):
            raise bad(f"sketch {sketch_name} of {comp.Name2} has {len(rows)} {noun}s, so there is no {noun} "
                      f"{wanted}", Err.SELECTION)
        row, entity = rows[wanted - 1]
        info = {"kind": f"sketch {noun}", "component": str(comp.Name2), "sketch": sketch_name}
        info.update(row)
        text = (f"sketch point {wanted} of {sketch_name} in {comp.Name2} (at {row['at']})" if noun == "point"
                else f"sketch {row['type']} {wanted} of {sketch_name} in {comp.Name2}")
        found = {"kind": f"sketch_{noun}", "component": comp, "entity": entity, "info": info, "text": text}
        if noun == "point":                                # unrounded, for measuring
            found["point_m"] = _sketch_point_asm(comp, sk, entity)
        return found

    if what in ("plane", "axis"):
        name = str(ref[what]).strip()
        if what == "plane":
            name = plane_feature_name(name)
        wanted_type = "RefPlane" if what == "plane" else "RefAxis"
        owner = comp if comp is not None else asm
        feat = None
        try:
            feat = owner.FeatureByName(name)
        except Exception as e:
            logger.debug(f"FeatureByName({name}) failed: {e}")
        if feat is None or value(feat, "GetTypeName2") != wanted_type:
            listed = []
            try:
                walk = value(owner, "FirstFeature")
                while walk is not None and len(listed) < 30:
                    if value(walk, "GetTypeName2") == wanted_type:
                        listed.append(str(walk.Name))
                    walk = value(walk, "GetNextFeature")
            except Exception:
                pass
            raise bad(f"{where} has no {what} named '{name}'. Its {what}s: {', '.join(listed) or 'none'}",
                      Err.SELECTION)
        info = {"kind": what, "name": str(feat.Name)}
        if comp is not None:
            info["component"] = str(comp.Name2)
        text = f"{feat.Name} of {comp.Name2}" if comp is not None else f"assembly {feat.Name}"
        return {"kind": what, "component": comp, "feature": feat, "info": info, "text": text}

    select_name, at = "Point1@Origin", (0.0, 0.0, 0.0)                       # origin
    if comp is not None:
        try:
            select_name += "@" + str(value(comp, "GetSelectByIDString"))
        except Exception as e:
            logger.debug(f"GetSelectByIDString failed: {e}")
            raise bad(f"could not address the origin of {comp.Name2}", Err.SELECTION)
        at = to_asm(comp, at)
    info = {"kind": "origin", "at": point_in_unit(at, unit)}
    if comp is not None:
        info["component"] = str(comp.Name2)
    text = f"origin of {comp.Name2}" if comp is not None else "assembly origin"
    return {"kind": "origin", "component": comp, "select_name": select_name, "info": info, "text": text,
            "point_m": tuple(at)}


def select_reference(asm, resolved: Dict, mark: int = 1) -> bool:
    """Add a resolved reference (resolve_reference) to the selection."""
    try:
        if "entity" in resolved:
            select_data = value(asm.SelectionManager, "CreateSelectData")
            select_data.Mark = mark
            return bool(resolved["entity"].Select4(True, select_data))
        if "feature" in resolved:
            return bool(resolved["feature"].Select2(True, mark))
        return bool(asm.Extension.SelectByID2(resolved["select_name"], "EXTSKETCHPOINT", 0, 0, 0, True, mark,
                                              null_object(), 0))
    except Exception as e:
        logger.debug(f"Could not select the {resolved.get('text')}: {e}")
        return False



# ---------------------------------------------------------------- mates

def mate_features(asm) -> list:
    """[(name, feature)] for every mate, in tree order (also mates sorted into folders)."""
    found = []

    def collect(parent, depth):
        sub = value(parent, "GetFirstSubFeature")
        while sub is not None and len(found) < 5000:
            type_name = str(value(sub, "GetTypeName2"))
            if type_name == "FtrFolder" and depth < 5:
                collect(sub, depth + 1)
            elif type_name.startswith("Mate"):
                found.append((str(sub.Name), sub))
            sub = value(sub, "GetNextSubFeature")

    try:
        feat = value(asm, "FirstFeature")
        while feat is not None and len(found) < 5000:
            if value(feat, "GetTypeName2") == "MateGroup":
                collect(feat, 0)
            feat = value(feat, "GetNextFeature")
    except Exception as e:
        logger.debug(f"Could not walk the mates: {e}")
    return found


def find_mate(asm, name):
    """(real name, feature), or a refusal listing the mates."""
    mates = mate_features(asm)
    wanted = str(name or "").strip().lower()
    for mate_name, feat in mates:
        if mate_name.lower() == wanted:
            return mate_name, feat
    listed = ", ".join(n for n, _ in mates[:60]) or "none"
    raise ToolError(f"No mate named '{name}'. Mates: {listed}", Err.INPUT)


def mate_error(feat):
    """(error code, is only a warning) of a mate."""
    try:
        warning = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BOOL, False)
        code = int(feat.GetErrorCode2(warning))
        return code, bool(warning.value)
    except Exception:
        try:
            return int(value(feat, "GetErrorCode")), False
        except Exception:
            return 0, False


def mate_problems(asm) -> Dict[str, int]:
    """name -> error code, for the mates that have one."""
    return {name: code for name, code in ((n, mate_error(f)[0]) for n, f in mate_features(asm)) if code}


def delete_mate(asm, name):
    """Delete a mate by name: (message, data), or a refusal. Used by delete_mate and delete_feature."""
    mate_name, feat = find_mate(asm, name)
    problems_before = mate_problems(asm)
    asm.ClearSelection2(True)
    if not feat.Select2(False, 0):
        asm.ClearSelection2(True)
        raise ToolError(f"Could not select {mate_name}", Err.SELECTION)
    asm.Extension.DeleteSelection2(0)
    asm.ClearSelection2(True)
    remaining = [n for n, _ in mate_features(asm)]
    if mate_name in remaining:
        raise ToolError(f"SolidWorks did not delete {mate_name}", Err.FEATURE, {"mate": mate_name})
    try:
        asm.ForceRebuild3(True)                             # top level only
    except Exception as e:
        logger.debug(f"ForceRebuild3 failed: {e}")
    asm.ClearSelection2(True)
    broken = sorted(n for n in mate_problems(asm) if n not in problems_before)
    data = {"deleted": [mate_name], "remaining_mates": remaining}
    message = f"Deleted {mate_name}; {len(remaining)} mates left"
    if broken:
        data["mate_errors"] = broken
        message += f". WARNING: these mates now have errors: {', '.join(broken)}"
    return message, data


# ---------------------------------------------------------------- interference

def interferences(asm, coincident: bool, unit):
    """SolidWorks' interference detection on the whole assembly (parts inside sub-assemblies one by one):
    (message, data). Used by check_interference and step_mate."""
    asm.ClearSelection2(True)
    manager = asm.InterferenceDetectionManager
    if manager is None:
        raise ToolError("SolidWorks gave no interference detection for this assembly", Err.INTERNAL)
    found = []
    try:
        manager.TreatCoincidenceAsInterference = coincident
        manager.TreatSubAssembliesAsComponents = False
        for attr, setting in (("MakeInterferingPartsTransparent", False), ("ShowIgnoredInterferences", False),
                              ("IncludeMultibodyPartInterferences", False), ("UseTransform", False)):
            try:
                setattr(manager, attr, setting)
            except Exception as e:
                logger.debug(f"Could not set {attr}: {e}")
        for item in list(value(manager, "GetInterferences") or []):
            names = []
            try:
                names = [str(c.Name2) for c in (item.Components or [])]
            except Exception as e:
                logger.debug(f"Could not read the interfering components: {e}")
            volume = 0.0
            try:
                volume = float(item.Volume)
            except Exception:
                pass
            found.append((names, volume))
    finally:
        try:
            manager.Done()
        except Exception as e:
            logger.debug(f"InterferenceDetectionManager.Done failed: {e}")
    unit_str, scale = units.label(unit), units.from_m(1.0, unit)
    rows = sorted(({"components": names, "volume": round(volume * scale ** 3, 4) + 0.0} for names, volume in found),
                  key=lambda r: -r["volume"])
    checked = len([c for c in all_components(asm) if solid_bodies(c)])
    message = (f"{len(rows)} interference(s) between the {checked} parts"
               + ("" if coincident else " (touching faces are not counted)"))
    if rows:
        message += ": " + "; ".join(f"{' / '.join(r['components'])} {r['volume']} {unit_str}^3" for r in rows[:10])
    return message, {"count": len(rows), "parts_checked": checked, "volume_unit": f"{unit_str}^3",
                     "treat_coincident_as_interference": coincident, "interferences": rows}
