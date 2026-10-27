"""Weldments: structural members from the installed profile library, and the cut list."""

import logging
import math
import os
import re

from ..core.api import Err, geometry, model, ok, sketch as sk, sw, tool, ToolError, units, value

logger = logging.getLogger(__name__)

CORNERS = {"miter": 1, "butt1": 2, "butt2": 3, "none": 0}         # swSolidworksWeldmentEndCondOptions_e


def _library_years(app=None) -> list:
    """The SOLIDWORKS <year> data folders to look in: the running version's first (RevisionNumber 34.x = 2026), then
    every other installed one, newest first. Older versions keep their libraries in their own year's folder."""
    base = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "SOLIDWORKS")
    try:
        years = sorted((n[11:] for n in os.listdir(base) if n.startswith("SOLIDWORKS 20") and n[11:].isdigit()),
                       reverse=True)
    except OSError:
        years = []
    try:
        running = str(1992 + int(str(value(app, "RevisionNumber")).split(".")[0])) if app is not None else None
    except Exception:
        running = None
    if running in years:
        years.remove(running)
        years.insert(0, running)
    return [os.path.join(base, f"SOLIDWORKS {y}") for y in years]


def _profile_root(app=None):
    """The installed weldment profile folder, or None."""
    for folder in _library_years(app):
        folder = os.path.join(folder, "weldment profiles")
        if os.path.isdir(folder):
            return folder
    return None


def _choices(folder, ext=None) -> list:
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    if ext:
        return [os.path.splitext(n)[0] for n in names if n.lower().endswith(ext)]
    return [n for n in names if os.path.isdir(os.path.join(folder, n))]


def _match(wanted, names):
    return {n.lower(): n for n in names}.get(str(wanted or "").strip().lower())


@tool(
    group="features",
    description="Make weldment structural members along the lines and arcs of a sketch, from the installed profile library: standard (e.g. iso, ansi, DIN), type (e.g. square tube) and size (e.g. 20 x 20 x 2). corner decides how members meet: miter, butt1, butt2 or none; butt1 / butt2 give SolidWorks' pinwheel: each member runs through at one end and stops at the other, so every member is exactly as long as its path (the butt joint as built, nothing to trim). An unknown standard, type or size is refused with the list of choices. Returns the feature, the bodies and their lengths.",
    params={"sketch": {"type": "string", "description": "Name of the sketch whose segments are the member paths"},
            "standard": {"type": "string", "description": "Profile standard folder, e.g. iso"},
            "type": {"type": "string", "description": "Profile type, e.g. square tube"},
            "size": {"type": "string", "description": "Profile size, e.g. 20 x 20 x 2"},
            "corner": {"type": "string", "enum": list(CORNERS), "default": "miter", "description": "Corner treatment"}},
    required=['sketch', 'type', 'size', 'standard'],
    needs="part",
    clears_selection=True,
    timeout=300,
)
def structural_member(ctx, sketch, type, size, standard, corner="miter"):
    doc = ctx.doc
    if doc.SketchManager.ActiveSketch is not None:
        raise ToolError("A sketch is open. Close it first (close_sketch).", Err.SKETCH)
    corner_key = str(corner or "miter").strip().lower()
    if corner_key not in CORNERS:
        raise ToolError(f"corner must be one of: {', '.join(CORNERS)}", Err.INPUT)
    root = _profile_root(ctx.app)
    if root is None:
        raise ToolError("No weldment profile library is installed (ProgramData\\SOLIDWORKS\\SOLIDWORKS 20xx\\"
                        "weldment profiles).", Err.NOT_FOUND)
    standards = _choices(root)
    std = _match(standard, standards)
    if std is None:
        raise ToolError(f"No profile standard '{standard}'. Standards: {', '.join(standards)}", Err.INPUT)
    types = _choices(os.path.join(root, std), ".sldlfp")
    kind = _match(type, types)
    if kind is None:
        raise ToolError(f"No profile type '{type}' in {std}. Types: {', '.join(types)}", Err.INPUT)
    path = os.path.join(root, std, kind + ".sldlfp")
    sizes = sw.weldments.sw_profile_sizes(ctx.app, path)
    chosen = _match(size, sizes) or _match(str(size).replace(" ", "").replace("x", " x "), sizes)
    if chosen is None:
        raise ToolError(f"No size '{size}' for {std} {kind}. Sizes: {', '.join(sizes[:60])}", Err.INPUT)
    names = model.sketch_names(doc)
    name = model.match_name(sketch, names)
    if name is None:
        raise ToolError(f"No sketch named '{sketch}'. Sketches: {', '.join(names) or 'none'}", Err.INPUT)
    sk.require_defined(doc, name)
    users = model.sketch_users(doc, name)
    if users:
        raise ToolError(f"{name} is already used by {', '.join(users)}. Draw the member paths in a new sketch.",
                        Err.INPUT)
    segments = [s for s in (value(value(doc.FeatureByName(name), "GetSpecificFeature2"), "GetSketchSegments") or [])
                if not bool(value(s, "ConstructionGeometry")) and int(value(s, "GetType")) in (0, 1)]
    if not segments:
        raise ToolError(f"{name} has no lines or arcs to put members on.", Err.INPUT)
    known = [row[0] for row in model.tree_features(doc)]
    bodies_before = model.solid_body_count(doc)
    feat = None
    was_open = ctx.app.GetOpenDocumentByName(path) is not None
    try:
        sw.weldments.sw_weldment_feature(doc)              # the Weldment feature makes the cut list
        feat = sw.weldments.sw_structural_member(doc, segments, path, chosen, CORNERS[corner_key])
    except Exception as e:
        logger.debug(f"Structural member: {e}")
    finally:
        sw.weldments.sw_close_if_opened(ctx.app, path, was_open)
    model.rebuild(doc)
    problem = None if feat is not None else "SolidWorks made no structural member"
    if problem is None:
        errors = [p for p in model.rebuild_errors(doc) if p["feature"] not in known]
        if errors:
            problem = f"{errors[0]['feature']} fails to rebuild"
    if problem is None and model.solid_body_count(doc) <= bodies_before:
        problem = "no member body was made"
    if problem:
        model.remove_new_features(doc, known)
        model.rebuild(doc)
        raise ToolError(f"Not created: {problem}.", Err.FEATURE)
    rows = []
    for body in geometry.solid_bodies(doc)[bodies_before:]:
        box = [float(v) * 1000 for v in value(body, "GetBodyBox")]
        rows.append({"name": str(value(body, "Name")), "box_mm": sorted([round(box[i + 3] - box[i], 3) for i in range(3)],
                                                                     reverse=True)})
    data = {"feature": str(feat.Name), "sketch": name, "standard": std, "type": kind, "size": chosen,
            "corner": corner_key, "members": len(rows), "bodies": rows, "volume_mm3": model.volume_mm3(doc)}
    pinwheel = ""
    if corner_key in ("butt1", "butt2"):
        data["corner_layout"] = "pinwheel"
        pinwheel = ("; butt corners are SolidWorks' pinwheel: each member runs through at one end and stops at the "
                    "other, so every member is exactly as long as its path (the butt joint as built, nothing to trim)")
    return ok(f"{feat.Name}: {len(rows)} {kind} {chosen} member(s) on {name}, corners {corner_key}{pinwheel}", data)


# swLengthUnit_e -> metres: the unit a cut list's LENGTH text is in
_DOC_UNIT_M = {0: 0.001, 1: 0.01, 2: 1.0, 3: 0.0254, 4: 0.3048}


def _member_length_m(body, hint_m=None):
    """A member's length along itself, tip to tip: its vertices projected on its axis. The axis is one of the
    directions its straight edges and cylindrical faces run in: the one whose length comes closest to SolidWorks'
    own LENGTH for the item (hint_m; rounded, so only used to choose), else the direction with the most straight
    edge length (a short wide tube's profile edges can be longer than the tube). A bounding box is right only for a
    member along a model axis; it is the last resort here."""
    groups = []                                             # [direction, total straight edge length]

    def add(d, weight):
        n = math.sqrt(sum(c * c for c in d)) or 1.0
        d = [c / n for c in d]
        for g in groups:
            if abs(sum(a * b for a, b in zip(g[0], d))) > 0.9999:
                g[1] += weight
                return
        groups.append([d, weight])

    ends = []                                               # every edge's two ends: the body's vertices
    for edge in list(value(body, "GetEdges") or []):
        # one read per edge: its ends and parameter range. A straight edge's parameter is its length; an arc's is
        # its angle in radians, which equals its chord only for a radius of 1 m or more (no profile has one)
        try:
            params = [float(v) for v in value(edge, "GetCurveParams2")]
        except Exception:
            continue
        a, b = params[0:3], params[3:6]
        ends += [a, b]
        chord = math.dist(a, b)
        if chord > 0 and abs(abs(params[7] - params[6]) - chord) <= 1e-9 * max(1.0, chord):
            add([b[i] - a[i] for i in range(3)], chord)
    if not groups:                                          # no straight edge: a pipe's axis
        for face in list(value(body, "GetFaces") or []):
            try:
                surface = value(face, "GetSurface")
                if value(surface, "IsCylinder"):
                    add([float(v) for v in list(value(surface, "CylinderParams"))[3:6]], 0.0)
            except Exception:
                continue
    if not groups:
        box = [float(v) for v in value(body, "GetBodyBox")]
        return max(box[i + 3] - box[i] for i in range(3))

    def extent(d):
        # the body's extreme points both ways (exact, also for a pipe, whose round edges have no vertices); its
        # vertices when SolidWorks gives no extreme point
        far, near = sw.weldments.sw_extreme_point(body, d), sw.weldments.sw_extreme_point(body, [-c for c in d])
        if far is not None and near is not None:
            return sum((far[i] - near[i]) * d[i] for i in range(3))
        points = [[float(v) for v in value(vertex, "GetPoint")] for vertex in list(value(body, "GetVertices") or [])]
        along = [sum(p[i] * d[i] for i in range(3)) for p in points] or [0.0]
        return max(along) - min(along)

    def spread(d):
        along = [sum(p[i] * d[i] for i in range(3)) for p in ends] or [0.0]
        return max(along) - min(along)
    if hint_m and len(groups) > 1:
        # the direction whose length comes closest to SolidWorks' own, chosen by the edge ends (a straight-edged
        # body's extent is its vertices'); measured exactly in that direction only
        return extent(min((d for d, _w in groups), key=lambda d: abs(spread(d) - hint_m)))
    return extent(max(groups, key=lambda g: g[1])[0])


def _number_in(text: str):
    m = re.search(r"-?\d+(?:[.,]\d+)?", text or "")
    return float(m.group(0).replace(",", ".")) if m else None


@tool(
    group="features",
    description="Read the cut list of the active weldment part: for each item its name, quantity, length (measured along each member, tip to tip), description (profile), material and end angles.. After butt corners (structural_member butt1 / butt2: SolidWorks' pinwheel) every member is exactly as long as its path, with square ends. Read-only.",
    params={"unit": {"type": "string", "description": "Unit for the lengths (mm, cm, m, inch or ft)"}},
    needs="part",
)
def get_cut_list(ctx, unit=None):
    doc = ctx.doc
    sw.weldments.sw_update_cut_list(doc)
    folders = sw.weldments.sw_cut_list_folders(doc)
    if not folders:
        raise ToolError("This part has no cut list: it is not a weldment (make members with structural_member).",
                        Err.INPUT)
    items = []
    for folder in folders:
        bodies = list(value(value(folder, "GetSpecificFeature2"), "GetBodies") or [])
        length_text = sw.weldments.sw_property(folder, "LENGTH")
        hint = _number_in(length_text)
        if hint is not None:
            hint *= _DOC_UNIT_M.get(int(doc.Extension.GetUserPreferenceInteger(47, 0)), 0.001)   # swUnitsLinear
        lengths = [_member_length_m(body, hint) for body in bodies]
        item = {"name": str(folder.Name), "quantity": len(bodies),
                "length": round(ctx.from_m(max(lengths), unit), 4) if lengths else None,
                "length_text": length_text,
                "description": sw.weldments.sw_property(folder, "DESCRIPTION"),
                "material": sw.weldments.sw_property(folder, "MATERIAL"),
                "angles": [_number_in(sw.weldments.sw_property(folder, k)) for k in ("ANGLE1", "ANGLE2")]}
        items.append(item)
    data = {"items": items, "count": len(items), "bodies": sum(i["quantity"] for i in items), "unit": units.label(unit)}
    lines = "; ".join(f"{i['quantity']} x {i['description'] or i['name']} {i['length']}" for i in items)
    return ok(f"Cut list: {len(items)} item(s), {data['bodies']} bodies: {lines} {data['unit']}", data)
