"""
List and select body edges and faces by geometry (position, direction, type, which face they
border) - never by what happens to be visible on screen.

Edge and face ids are positions in the body's edge / face list. They are only valid until the
geometry changes: list again after adding a feature.
"""

import logging

from ..core.api import Err, geometry, ok, selecting, sw, tool, ToolError, units, value

logger = logging.getLogger(__name__)

_COMPACT = {"type": "boolean", "default": True,
            "description": "One short text line per row (same ids and values, fewer tokens); false gives an object "
                           "per row"}


def _xyz(p) -> str:
    return "(" + ",".join(f"{v:g}" for v in p) + ")"


def _face_line(row) -> str:
    """'3 plane at(20,10,30) area 1200 n(0,0,1) box(0,0,30,40,20,30)'"""
    line = f"{row['id']} {row['type']} at{_xyz(row['point_on_face'])} area {row['area']:g}"
    if "normal" in row:
        line += f" n{_xyz(row['normal'])}"
    if "radius" in row:
        line += f" r {row['radius']:g} axis{_xyz(row['axis'])}"
    return line + f" box{_xyz(row['box'])}"


def _edge_line(row) -> str:
    """'5 line L 40 (0,0,0)->(40,0,0) d(1,0,0)' or '9 circle L 31.416 c(20,10,30) r 5 axis(0,0,1)'"""
    line = f"{row['id']} {row['type']} L {row['length']:g}"
    if "start" in row:
        line += f" {_xyz(row['start'])}->{_xyz(row['end'])}"
    else:
        line += f" mid{_xyz(row['mid'])}"
    if "direction" in row:
        line += f" d{_xyz(row['direction'])}"
    if "radius" in row:
        line += f" c{_xyz(row['center'])} r {row['radius']:g} axis{_xyz(row['axis'])}"
    return line


@tool(
    group="core",
    description="List the faces of the solid body: id, type (plane / cylinder / other), area, outward normal, bounding box and a point on each face. Ids hold only until the geometry changes: select_faces refuses them after a change (list again). By default one short text line per item (fewer tokens); compact: false gives an object per item.",
    params={'unit': {'type': 'string', 'description': 'mm, cm, m, inch or ft'}, 'compact': _COMPACT},
    needs='part',
)
def list_faces(ctx, unit=None, compact=True):
    doc = ctx.doc
    if not isinstance(compact, bool):
        raise ToolError("compact must be true or false", Err.INPUT)
    geos = [geometry.face_geometry(f) for f in geometry.all_faces(doc)]
    rows = [selecting.face_row(i + 1, geo, unit) for i, geo in enumerate(geos)]
    if compact:
        rows = [_face_line(row) for row in rows]
    stamp = selecting.remember_listing(doc, "faces", geos)
    return ok(f"{len(rows)} faces", {"unit": units.label(unit), "count": len(rows), "faces": rows, "stamp": stamp})


@tool(
    group="core",
    description="List the edges of the solid body: id, type (line / circle / arc), length, end points, midpoint, and radius / centre for round edges. Filters are optional. Ids hold only until the geometry changes: select_edges refuses them after a change (list again). By default one short text line per item (fewer tokens); compact: false gives an object per item.",
    params={'edge_type': {'type': 'string',
                   'enum': ['line', 'circle', 'arc', 'other'],
                   'description': 'Only edges of this type'},
     'parallel_to': {'type': 'string',
                     'enum': ['x', 'y', 'z'],
                     'description': 'Only straight edges parallel to this model axis'},
     'unit': {'type': 'string', 'description': 'mm, cm, m, inch or ft'},
     'compact': _COMPACT},
    needs='part',
)
def list_edges(ctx, unit=None, edge_type=None, parallel_to=None, compact=True):
    doc = ctx.doc
    selecting.check_edge_filters(edge_type, parallel_to)
    if not isinstance(compact, bool):
        raise ToolError("compact must be true or false", Err.INPUT)
    rows, geos = [], [geometry.edge_geometry(edge) for edge in geometry.all_edges(doc)]
    for i, geo in enumerate(geos):
        if selecting.edge_passes(geo, edge_type, parallel_to):
            rows.append(selecting.edge_row(i + 1, geo, unit))
    if compact:
        rows = [_edge_line(row) for row in rows]
    stamp = selecting.remember_listing(doc, "edges", geos)
    return ok(f"{len(rows)} edges", {"unit": units.label(unit), "count": len(rows), "edges": rows, "stamp": stamp})


@tool(
    group="core",
    description="Empty the current selection.",
    needs='any',
)
def clear_selection(ctx):
    sw.common.sw_clear_selection(ctx.doc)
    return ok("Selection cleared")


@tool(
    group="core",
    description="Select body edges by geometry (the view does not matter), ready for fillet_edges / chamfer_edges. Choose by points (the edge nearest each point), face_points (one point = every edge bordering that face; two points = the edge the two faces share) or edge_ids from list_edges (refused if the geometry changed since it listed them; points are safer). With none of these, a filter alone selects from all edges. The filters edge_type, parallel_to and loop narrow the choice.",
    params={'points': {'type': 'array',
                'items': {'type': 'object',
                          'properties': {'x': {'type': 'number'},
                                         'y': {'type': 'number'},
                                         'z': {'type': 'number'}},
                          'required': ['x', 'y', 'z']},
                'description': 'Model points on or near the edges to select (one edge per point)'},
     'face_points': {'type': 'array',
                     'items': {'type': 'object',
                               'properties': {'x': {'type': 'number'},
                                              'y': {'type': 'number'},
                                              'z': {'type': 'number'}},
                               'required': ['x', 'y', 'z']},
                     'description': 'One point inside a face = all its edges; two points on two faces '
                                    '= their shared edge'},
     'edge_ids': {'type': 'array',
                  'items': {'type': 'integer'},
                  'description': 'Edge ids from list_edges'},
     'edge_type': {'type': 'string',
                   'enum': ['line', 'circle', 'arc'],
                   'description': 'Keep only edges of this type'},
     'parallel_to': {'type': 'string',
                     'enum': ['x', 'y', 'z'],
                     'description': 'Keep only straight edges parallel to this model axis'},
     'loop': {'type': 'string',
              'enum': ['all', 'outer', 'inner'],
              'default': 'all',
              'description': 'With one face point: the face outline (outer), its hole edges (inner) or '
                             'both'},
     'append': {'type': 'boolean',
                'default': False,
                'description': 'Add to the current selection instead of replacing it'},
     'tolerance': {'type': 'number',
                   'description': 'How far a point may be from its edge (default 0.5 mm)'},
     'unit': {'type': 'string', 'description': 'mm, cm, m, inch or ft'}},
    needs='part',
)
def select_edges(ctx, points=None, face_points=None, edge_ids=None, edge_type=None, parallel_to=None,
                 loop="all", append=False, unit=None, tolerance=None):
    doc = ctx.doc
    unit_str = units.label(unit)
    selecting.check_edge_filters(edge_type, parallel_to)
    loop = (loop or "all").strip().lower()
    if loop not in ("all", "outer", "inner"):
        raise ToolError("loop must be all, outer or inner", Err.INPUT)

    tol = ctx.to_m(tolerance, unit) if tolerance else geometry.PICK_TOLERANCE_M
    pts = geometry.points_to_m(points, unit)
    face_pts = geometry.points_to_m(face_points, unit)
    if len(face_pts) > 2:
        raise ToolError("face_points takes one or two points", Err.INPUT)
    all_edges = geometry.all_edges(doc)
    if not all_edges:
        raise ToolError("The model has no solid body edges", Err.SELECTION)

    chosen = []
    for p in pts:                                      # the edge nearest to each point
        dist, index = min((geometry.distance_to(e, p), i) for i, e in enumerate(all_edges))
        if dist > tol:
            raise ToolError(f"No edge within {ctx.from_m(tol, unit):.3f}{unit_str} of "
                            f"{geometry.point_in_unit(p, unit)} (nearest is {ctx.from_m(dist, unit):.3f}"
                            f"{unit_str} away)", Err.SELECTION)
        chosen.append(all_edges[index])

    if face_pts:
        faces = geometry.all_faces(doc)
        picked = []
        for p in face_pts:
            face, reason = geometry.face_at_point(faces, p, tol)
            if face is None:
                raise ToolError(f"Face point {geometry.point_in_unit(p, unit)} {unit_str}: {reason}", Err.SELECTION)
            picked.append(face)
        if loop == "all":
            face_edges = list(value(picked[0], "GetEdges") or [])
        else:
            face_edges = []
            for lp in value(picked[0], "GetLoops") or []:
                if bool(value(lp, "IsOuter")) == (loop == "outer"):
                    face_edges.extend(list(value(lp, "GetEdges") or []))
        if len(picked) == 2:                           # keep the edges that also lie on the second face
            shared = [e for e in face_edges
                      if geometry.distance_to(picked[1], geometry.edge_geometry(e)["mid"]) <= 1e-6]
            if not shared:
                raise ToolError("Those two faces do not share an edge", Err.SELECTION)
            face_edges = shared
        chosen.extend(face_edges)

    if edge_ids:
        selecting.check_ids(doc, "edges")
    for raw in edge_ids or []:
        try:
            index = int(raw)
        except (TypeError, ValueError):
            raise ToolError(f"Bad edge id '{raw}'", Err.INPUT)
        if index < 1 or index > len(all_edges):
            raise ToolError(f"Edge {index} does not exist (the model has {len(all_edges)} edges)", Err.SELECTION)
        chosen.append(all_edges[index - 1])

    if not pts and not face_pts and not edge_ids:
        if not edge_type and not parallel_to:
            raise ToolError("Say which edges: give points, face_points, edge_ids, or a filter "
                            "(edge_type / parallel_to) to select from all edges", Err.INPUT)
        chosen = list(all_edges)

    if not append:
        sw.common.sw_clear_selection(doc)
    rows, seen = [], []
    for edge in chosen:
        geo = geometry.edge_geometry(edge)
        if not selecting.edge_passes(geo, edge_type, parallel_to):
            continue
        key = (geo["type"], tuple(round(v, 7) for v in geo["mid"]), round(geo["length"], 7))
        if key in seen:
            continue
        seen.append(key)
        if sw.selection.sw_select_edge(edge):
            rows.append(selecting.edge_row(len(rows) + 1, geo, unit))
    if not rows:
        raise ToolError("No edge matched", Err.SELECTION)
    for row in rows:
        row.pop("id", None)                            # these are not list_edges ids
    total = sw.selection.sw_selected_count(doc)
    return ok(f"Selected {len(rows)} edge(s); {total} item(s) selected in total",
              {"selected": len(rows), "total_selected": total, "unit": unit_str, "edges": rows})


@tool(
    group="core",
    description="Select body faces by geometry (the view does not matter): by points (the face containing each point), face_ids from list_faces (refused if the geometry changed since it listed them), or normal (every flat face facing +x, -x, +y, -y, +z or -z).",
    params={'points': {'type': 'array',
                'items': {'type': 'object',
                          'properties': {'x': {'type': 'number'},
                                         'y': {'type': 'number'},
                                         'z': {'type': 'number'}},
                          'required': ['x', 'y', 'z']},
                'description': 'Model points inside the faces to select'},
     'face_ids': {'type': 'array',
                  'items': {'type': 'integer'},
                  'description': 'Face ids from list_faces'},
     'normal': {'type': 'string',
                'enum': ['+x', '-x', '+y', '-y', '+z', '-z'],
                'description': 'Select every flat face with this outward normal'},
     'append': {'type': 'boolean',
                'default': False,
                'description': 'Add to the current selection instead of replacing it'},
     'tolerance': {'type': 'number',
                   'description': 'How far a point may be from its face (default 0.5 mm)'},
     'unit': {'type': 'string', 'description': 'mm, cm, m, inch or ft'}},
    needs='part',
)
def select_faces(ctx, points=None, face_ids=None, normal=None, append=False, unit=None, tolerance=None):
    return selecting.pick_faces(ctx.doc, points, face_ids, normal, append, unit, tolerance)
