"""Opening and closing sketches, and the planes they sit on."""

import logging

from ..core.api import Err, geometry, doc_type, model, ok, plane_feature_name, sketch as sk, sw, tool, ToolError, units, value

logger = logging.getLogger(__name__)


@tool(
    group="sketch",
    description="Start a sketch on one of the model's planes: Front, Top, Right, or another plane by name (for example 'Plane1' made by create_offset_plane). Refused while another sketch is open.",
    params={'plane': {'type': 'string',
               'default': 'Front',
               'description': 'Front, Top, Right, or the exact name of a plane in the feature tree'}},
    needs='any',
)
def create_sketch(ctx, plane="Front"):
    doc = ctx.doc
    # With a sketch open, InsertSketch2 would CLOSE it and the call would still say "created"
    active = sk.open_sketch(doc)
    if active is not None:
        try:
            name = active.Name
        except Exception:
            name = "a sketch"
        raise ToolError(f"{name} is still open. Close it first (close_sketch), or finish it "
                        f"with a feature (extrude_sketch, cut_extrude, ...).", Err.SKETCH)
    plane_name = plane_feature_name(plane)         # an unknown name is not replaced by the Front Plane
    sw.common.sw_clear_selection(doc)
    if not sw.common.sw_select_named(doc, plane_name, "PLANE", 0, append=False):
        raise ToolError(f"Could not select a plane named '{plane_name}'. Use Front, Top, Right "
                        f"or the exact name of a plane in the feature tree.", Err.SELECTION)
    sw.sketches.sw_insert_sketch(doc)
    return ok(f"Sketch created on {plane_name}", {"plane": plane_name})


@tool(
    group="sketch",
    description="Start a sketch on the flat body face that contains a given 3D model point (found by geometry, so the current view does not matter). The point must lie inside the face, not on an edge. The result gives the sketch origin and axis directions in model space and the point in sketch coordinates, which you need to place geometry.",
    params={'x': {'type': 'number', 'description': "X of a point inside the face"},
     'y': {'type': 'number', 'description': "Y of a point inside the face"},
     'z': {'type': 'number', 'description': "Z of a point inside the face"},
     'unit': {'type': 'string', 'description': 'mm, cm, m, inch or ft'}},
    required=['x', 'y', 'z'],
    needs='part',
)
def create_sketch_on_face(ctx, x, y, z, unit=None):
    doc = ctx.doc
    x_m, y_m, z_m = (ctx.to_m(v, unit) for v in (x, y, z))
    unit_str = units.label(unit)
    where = f"({x}, {y}, {z}) {unit_str}"
    if sk.open_sketch(doc) is not None:
        raise ToolError("A sketch is already open. Close it first (close_sketch).", Err.SKETCH)

    try:
        candidates = geometry.faces_at_point(doc, x_m, y_m, z_m)
    except Exception as e:
        logger.exception("Face search failed")
        raise ToolError(f"Could not read the body faces of this document: {e}", Err.SELECTION)
    if not candidates:
        raise ToolError("The document has no solid body faces.", Err.SELECTION)
    on_point = [(d, f) for d, f in candidates if d <= geometry.FACE_POINT_TOLERANCE_M]
    if not on_point:
        nearest = ctx.from_m(candidates[0][0], unit)
        raise ToolError(f"No face at {where}. The nearest face is {nearest:.3f}{unit_str} away. "
                        f"Coordinates must be on an existing body face.", Err.SELECTION,
                        {"nearest_face_distance": round(nearest, 4), "unit": unit_str})
    if len(on_point) > 1:
        raise ToolError(f"The point {where} lies on an edge or corner shared by "
                        f"{len(on_point)} faces. Give a point inside the face, away from its edges.",
                        Err.SELECTION, {"faces_at_point": len(on_point)})

    face = on_point[0][1]
    if not value(value(face, "GetSurface"), "IsPlane"):
        raise ToolError(f"The face at {where} is not flat. A sketch needs a planar face "
                        f"(or use an offset plane).", Err.SELECTION)
    normal = [round(float(v), 6) + 0.0 for v in value(face, "Normal")]

    sw.common.sw_clear_selection(doc)
    if not sw.sketches.sw_select_face(face):
        raise ToolError(f"Could not select the face at {where}", Err.SELECTION)
    sw.sketches.sw_insert_sketch_on_selection(doc)
    sketch = sk.open_sketch(doc)
    if sketch is None:
        raise ToolError(f"Could not open a sketch on the face at {where}", Err.SKETCH)

    data = {"face_point": {"x": x, "y": y, "z": z}, "unit": unit_str, "face_normal": normal}
    # The sketch's coordinate system in model space. SolidWorks: [sketch] = [model] * R + T
    # (row vectors, R = a[0:9], T = a[9:12])
    try:
        a = [float(v) for v in value(sketch.ModelToSketchTransform, "ArrayData")]
        R, T = (a[0:3], a[3:6], a[6:9]), a[9:12]

        def rnd(v):
            return round(v, 6) + 0.0

        origin_m = [-(T[0] * R[i][0] + T[1] * R[i][1] + T[2] * R[i][2]) for i in range(3)]
        p = (x_m, y_m, z_m)
        u_m = sum(p[i] * R[i][0] for i in range(3)) + T[0]
        v_m = sum(p[i] * R[i][1] for i in range(3)) + T[1]
        data["sketch_origin_in_model"] = [rnd(ctx.from_m(c, unit)) for c in origin_m]
        data["sketch_x_axis_in_model"] = [rnd(R[i][0]) for i in range(3)]
        data["sketch_y_axis_in_model"] = [rnd(R[i][1]) for i in range(3)]
        data["point_in_sketch"] = {"x": rnd(ctx.from_m(u_m, unit)), "y": rnd(ctx.from_m(v_m, unit))}
    except Exception as e:
        logger.warning(f"Could not read the sketch transform: {e}")

    message = f"Sketch created on face at {where}, face normal {normal}"
    if "point_in_sketch" in data:
        ps = data["point_in_sketch"]
        message += f". In sketch coordinates that point is ({ps['x']}, {ps['y']}) {unit_str}"
    return ok(message, data)


@tool(
    group="sketch",
    description="Create a reference plane parallel to a plane or to a flat face of the body, at a given distance. Returns the new plane's name, to use with create_sketch.",
    params={'reference': {'type': 'string',
                   'default': 'Front',
                   'description': "Front, Top, Right, a plane name (e.g. 'Plane1'), or 'face' to "
                                  'offset from the flat face at face_x/face_y/face_z'},
     'distance': {'type': 'number',
                  'description': 'Offset distance (negative flips the direction)'},
     'flip': {'type': 'boolean', 'default': False, 'description': 'Offset in the opposite direction'},
     'face_x': {'type': 'number',
                'description': "X of a point on the face (reference='face' only)"},
     'face_y': {'type': 'number',
                'description': "Y of a point on the face (reference='face' only)"},
     'face_z': {'type': 'number',
                'description': "Z of a point on the face (reference='face' only)"},
     'unit': {'type': 'string', 'description': 'mm, cm, m, inch or ft'}},
    required=['distance'],
    needs='any',
    clears_selection=True,
)
def create_offset_plane(ctx, distance, reference="Front", flip=False, unit=None, face_x=None, face_y=None, face_z=None):
    doc = ctx.doc
    if sk.open_sketch(doc) is not None:
        raise ToolError("A sketch is open. Close it first (close_sketch).", Err.SKETCH)
    unit_str = units.label(unit)
    distance_m = ctx.to_m(distance, unit)
    if distance_m < 0:
        distance_m, flip = -distance_m, not flip
    if distance_m == 0:
        raise ToolError("Distance must not be 0", Err.INPUT)

    sw.common.sw_clear_selection(doc)
    ref_text = (reference or "Front").strip()
    if ref_text.lower() == "face" and None in (face_x, face_y, face_z):
        raise ToolError("reference 'face' needs face_x, face_y and face_z: a point on the flat face.", Err.INPUT)
    if ref_text.lower() == "face":
        x_m, y_m, z_m = (ctx.to_m(v, unit) for v in (face_x, face_y, face_z))
        on_point = [(d, f) for d, f in geometry.faces_at_point(doc, x_m, y_m, z_m) if d <= geometry.FACE_POINT_TOLERANCE_M]
        if len(on_point) != 1:
            raise ToolError(f"Need a point inside exactly one face; found {len(on_point)} faces at "
                            f"({face_x}, {face_y}, {face_z}) {unit_str}", Err.SELECTION)
        face = on_point[0][1]
        if not value(value(face, "GetSurface"), "IsPlane"):
            raise ToolError("The face is not flat", Err.SELECTION)
        if not sw.sketches.sw_select_face(face):
            raise ToolError("Could not select the face", Err.SELECTION)
        ref_name = f"face at ({face_x}, {face_y}, {face_z}) {unit_str}"
    else:
        ref_name = plane_feature_name(ref_text)
        if not sw.common.sw_select_named(doc, ref_name, "PLANE", 0, append=False):
            raise ToolError(f"Could not select plane '{ref_name}'", Err.SELECTION)

    flags = 8 | (256 if flip else 0)               # distance constraint | flip
    plane = sw.sketches.sw_insert_offset_plane(doc, flags, distance_m)
    sw.common.sw_clear_selection(doc)
    if plane is None:
        raise ToolError(f"SolidWorks could not create a plane offset from {ref_name}", Err.FEATURE)

    data = {"plane": plane.Name, "reference": ref_name, "distance": ctx.from_m(distance_m, unit),
            "flip": bool(flip), "unit": unit_str}
    try:
        a = [float(v) for v in value(value(plane, "GetSpecificFeature2").Transform, "ArrayData")]
        data["normal"] = [round(v, 6) + 0.0 for v in a[6:9]]
        data["origin_in_model"] = [round(ctx.from_m(v, unit), 4) + 0.0 for v in a[9:12]]
    except Exception as e:
        logger.debug(f"Could not read the plane position: {e}")
    where = f" at {data['origin_in_model']} {unit_str}" if "origin_in_model" in data else ""
    return ok(f"Created {plane.Name}: {data['distance']}{unit_str} from {ref_name}{where}. "
              f"Sketch on it with create_sketch(plane=\"{plane.Name}\")", data)


@tool(
    group="sketch",
    description="Exit the open sketch, rebuilding the features made from it. Call it before an extrude if the sketch has not been closed yet. In a part, the drawn sizes of rectangles, circles, arcs and polygons become dimensions where still free (listed in the result), and anything still free is named by id: dimension it from the references the user named before the feature, which refuses a sketch that is not fully defined.",
    needs='any',
    clears_selection=True,
)
def close_sketch(ctx):
    doc = ctx.doc
    sketch = sk.open_sketch(doc)
    if sketch is None:
        return ok("No active sketch to close", {"had_active_sketch": False, "action": "none"})
    data = {"had_active_sketch": True, "action": "closed"}
    # in a part every sketch is fully defined before its feature (assembly layout sketches are the user's call)
    note = ""
    if doc_type(doc) == 1:
        name, state = str(sketch.Name), sk.status(sketch)
        data.update(sketch=name, status=state)
        pending = state == "under_defined" and sk.sizes_pending(doc, sketch)
        done, free = sk.defined(doc, sketch, quick=pending)     # a full reading follows when sizes are pending
        if state == "under_defined" and not done:
            # the drawn sizes become dimensions where still free (the user's numbers); nothing from the origin:
            # what is still free is reported, for the AI to dimension from the references the user named
            sizes = [{"name": n, "value": round(ctx.from_m(v, None), 4), "unit": units.label(None)}
                     for n, v in sk.apply_sizes(doc, sketch)]
            if sizes:
                data["size_dimensions"] = sizes
                note = f". Its drawn sizes became {sk.listed(sizes)}"
                sketch = sk.open_sketch(doc) or sketch
                done, free = sk.defined(doc, sketch)       # read again only when sizes were added
                data["status"] = sk.status(sketch)
            elif pending:
                done, free = sk.defined(doc, sketch)       # the first reading stopped at the first free entity
            if not done:
                data.update(free_entities=free["ids"], free_points=free["points"])
                note += (f". {name} is under-defined: {sk.free_text(free)}. Dimension them from the references the "
                         f"user named (edit_sketch, then add_sketch_dimension / add_sketch_relation); "
                         f"fully_define_sketch only as a last resort. A feature refuses it until then")
        else:
            sk.forget_sizes(doc, sketch)
        if state in sk.BROKEN:
            note = f". WARNING: {name} is {state.replace('_', '-')}: fix its dimensions and relations"
    sw.sketches.sw_close_sketch(doc)
    return ok("Sketch closed successfully" + note, data)


@tool(
    group="sketch",
    description="Reopen an existing sketch (for example 'Sketch1') for editing. The draw tools, add_sketch_relation and add_sketch_dimension then act on it, and close_sketch rebuilds the features made from it. Refused while another sketch is open.",
    params={'sketch': {'type': 'string', 'description': "Name of the sketch in the feature tree, e.g. 'Sketch1'"}},
    required=['sketch'],
    needs='part_or_assembly',
)
def edit_sketch(ctx, sketch):
    doc = ctx.doc
    active = sk.open_sketch(doc)
    if active is not None:
        try:
            name = active.Name
        except Exception:
            name = "A sketch"
        raise ToolError(f"{name} is still open. Close it first (close_sketch).", Err.SKETCH)
    names = model.sketch_names(doc)
    name = model.match_name(sketch, names)
    if name is None:
        raise ToolError(f"No sketch named '{sketch}'. Sketches: {', '.join(names) or 'none'}", Err.INPUT,
                        {"sketch_names": names})
    if not sw.sketches.sw_edit_sketch(doc, name):
        raise ToolError(f"Could not select {name}", Err.SELECTION)
    opened = sk.open_sketch(doc)
    if opened is None or str(opened.Name) != name:
        model.close_open_sketch(doc)
        raise ToolError(f"SolidWorks did not open {name} for editing", Err.SKETCH)
    users = model.sketch_users(doc, name)
    data = {"sketch": name, "entities": len(sk.segments(doc, opened)), "status": sk.status(opened),
            "used_by": users}
    used = f"; close_sketch rebuilds {', '.join(users)}" if users else ""
    return ok(f"{name} is open for editing ({data['entities']} entities, {data['status']}){used}", data)


@tool(
    group="sketch",
    description="Report the sketch state: whether a sketch is open, how many sketches there are and their names in the feature tree.",
    needs='any',
)
def get_sketch_status(ctx):
    doc = ctx.doc
    open_now = sk.open_sketch(doc)
    open_name = None
    if open_now is not None:
        try:
            open_name = open_now.Name
        except Exception:
            open_name = "<unknown>"
    nodes = model.tree_nodes(doc)
    kinds = [kind for _, kind in nodes if kind is not None]
    sketches = [name for name, kind in nodes if kind == "ProfileFeature" and name]
    data = {"has_active_sketch": open_now is not None, "active_sketch_name": open_name,
            "sketch_count": len(sketches), "sketch_names": sketches,
            "feature_count": len(kinds), "extrusion_count": kinds.count("Extrusion")}
    where = f"{open_name} is open" if open_now is not None else "no sketch is open"
    return ok(f"{where}; {len(sketches)} sketch(es) in the tree {sketches}, {data['extrusion_count']} "
              f"extrusion(s), {len(kinds)} tree items", data)
