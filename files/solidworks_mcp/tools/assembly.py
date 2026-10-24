"""Assembly components: list them, their faces and sketches; insert, fix, move, hide, make flexible.

Reading lives in core/assembly.py. The SolidWorks calls that change the model are the sw_* helpers below.
"""

import logging
import math
import os


from ..core.api import assembly, doc_title, Err, ok, tool, ToolError, units, value
from ..core.api import sw

logger = logging.getLogger(__name__)

_SOLVING = {0: "rigid", 1: "flexible"}


# ---------------------------------------------------------------- SolidWorks calls


# ---------------------------------------------------------------- tools

_UNIT = {"type": "string", "description": "mm, cm, m, inch or ft"}
_COMPONENT_OR_PATH = {"type": "string", "description": "Component name from list_components, or a path into a sub-assembly"}
_AXIS = {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}},
         "required": ["x", "y", "z"], "description": "Instead of rx / ry / rz: the direction to turn about"}


@tool(
    group="assembly",
    description="List the top-level components of the active assembly: name, file, fixed or floating, rigid or flexible for a sub-assembly, the position of the origin (and axes, if rotated) in the assembly, bounding box, and mates. A part inside a sub-assembly is addressed as 'Sub-1/Part-2'.",
    params={"unit": _UNIT},
    needs="assembly",
)
def list_components(ctx, unit=None):
    asm = ctx.doc
    rows = [assembly.component_row(c, unit) for c in assembly.components(asm)]
    mates = assembly.mate_rows(asm)
    return ok(f"{len(rows)} components, {len(mates)} mates",
              {"unit": units.label(unit), "components": rows, "mates": mates})


@tool(
    group="assembly",
    description="List the faces of one component of the active assembly: id, type (plane / cylinder / other), area, a point on the face, and the normal or the axis and radius, all in ASSEMBLY coordinates. The id is the face_id that add_mate and measure_distance take, for when picking a face by point is ambiguous. Works on a part inside a sub-assembly (component path). Read-only.",
    params={"component": _COMPONENT_OR_PATH,
            "face_type": {"type": "string", "enum": ["plane", "cylinder", "other"],
                          "description": "List only faces of this type"},
            "include": {"type": "array", "items": {"type": "string", "enum": ["edges", "vertices"]},
                        "description": "Also list the edges and / or vertices, with ids for edge_id / vertex_id"},
            "unit": _UNIT},
    required=["component"],
    needs="assembly",
)
def list_component_faces(ctx, component, face_type=None, include=None, unit=None):
    asm = ctx.doc
    extra = []
    if include is not None:
        extra = [str(v).strip().lower() for v in (include if isinstance(include, list) else [include])]
        if any(v not in ("edges", "vertices") for v in extra):
            raise ToolError("include takes 'edges' and / or 'vertices'", Err.INPUT)
    wanted = None
    if face_type is not None:
        wanted = str(face_type).strip().lower()
        if wanted not in ("plane", "cylinder", "other"):
            raise ToolError("face_type must be plane, cylinder or other", Err.INPUT)
    comp = assembly.component(asm, component)
    faces = assembly.faces(comp)
    if not faces:
        inside = [str(c.Name2) for c in assembly.all_components(asm)
                  if str(c.Name2).lower().startswith(str(comp.Name2).lower() + "/")]
        hint = f" It is a sub-assembly: name one of its parts ({', '.join(inside[:20])})." if inside else ""
        raise ToolError(f"{comp.Name2} has no solid or surface body.{hint}", Err.SELECTION)
    rows = []
    for index, face in enumerate(faces, start=1):
        info = assembly.face_info(comp, face, index, unit)
        if wanted and info["type"] != wanted:
            continue
        rows.append({"id": index, **{k: v for k, v in info.items() if k not in ("kind", "component", "face_id")}})
    message = f"{comp.Name2}: {len(faces)} faces" + (f", {len(rows)} of type {wanted}" if wanted else "")
    data = {"component": str(comp.Name2), "unit": units.label(unit), "coordinates": "assembly",
            "count": len(rows), "faces": rows}
    if "edges" in extra:
        data["edges"] = [{"id": i, **{k: v for k, v in assembly.edge_info(comp, e, i, unit).items()
                                      if k not in ("kind", "component", "edge_id")}}
                         for i, e in enumerate(assembly.edges(comp), start=1)]
        message += f", {len(data['edges'])} edges"
    if "vertices" in extra:
        data["vertices"] = [{"id": i, "at": assembly.vertex_info(comp, v, i, unit)["at"]}
                            for i, v in enumerate(assembly.vertices(comp), start=1)]
        message += f", {len(data['vertices'])} vertices"
    return ok(message, data)


@tool(
    group="assembly",
    description="List the sketches of one component of the active assembly. Given a sketch name, list that sketch's points and segments (line / arc / circle with start, end, centre, radius) in ASSEMBLY coordinates. The ids are the point_id / segment_id that add_mate and measure_distance take. They follow SolidWorks' order for a finished sketch, which is NOT the ids list_sketch_entities gave while drawing. Each point has an 'of' field saying what it is: ['2:center'] is the centre of circle or arc 2, ['1:end', '3:start'] a corner shared by two segments, [] a point made with draw_point. Read-only.",
    params={"component": _COMPONENT_OR_PATH,
            "sketch": {"type": "string", "description": "Sketch name. Leave out to get the list of sketch names."},
            "unit": _UNIT},
    required=["component"],
    needs="assembly",
)
def list_component_sketches(ctx, component, sketch=None, unit=None):
    asm = ctx.doc
    comp = assembly.component(asm, component)
    base = {"component": str(comp.Name2), "unit": units.label(unit), "coordinates": "assembly"}
    if sketch is None or not str(sketch).strip():
        names = [n for n, _ in assembly.sketches(comp)]
        base["sketches"] = names
        return ok(f"{comp.Name2}: {len(names)} sketches" + (f" ({', '.join(names[:30])})" if names else ""), base)
    sk = assembly.sketch(comp, sketch)
    points = [row for row, _ in assembly.sketch_point_rows(comp, sk, unit)]
    segments = [row for row, _ in assembly.sketch_segment_rows(comp, sk, unit)]
    base.update({"sketch": str(sk.Name), "points": points, "segments": segments})
    return ok(f"{sk.Name} of {comp.Name2}: {len(points)} points, {len(segments)} segments", base)


@tool(
    group="assembly",
    description="Insert a saved part or sub-assembly file into the active assembly, with the centre of its bounding box at x, y, z. It can be rotated about that centre: rx, ry, rz in degrees (applied about the assembly X, then Y, then Z axis), or one turn given as axis plus angle. The first component inserted is fixed; later ones float until mated (add_mate). set_component_fixed changes that, and move_component repositions a floating one.",
    params={"filepath": {"type": "string", "description": "Path of the .SLDPRT / .SLDASM file"},
            "x": {"type": "number", "description": "X of where the component's center goes"},
            "y": {"type": "number", "description": "Y of where the component's center goes"},
            "z": {"type": "number", "description": "Z of where the component's center goes"},
            "rx": {"type": "number", "description": "Turn it by this many degrees about the assembly X axis"},
            "ry": {"type": "number", "description": "... then about the assembly Y axis"},
            "rz": {"type": "number", "description": "... then about the assembly Z axis"},
            "axis": _AXIS,
            "angle": {"type": "number", "description": "With axis: degrees to turn"},
            "unit": _UNIT},
    required=['filepath', 'x', 'y', 'z'],
    timeout=300,                    # loading a big sub-assembly can take minutes
    needs="assembly",
)
def add_component(ctx, filepath, x, y, z, rx=None, ry=None, rz=None, axis=None, angle=None, unit=None):
    asm = ctx.doc
    path = os.path.abspath(str(filepath or ""))
    if not os.path.isfile(path):
        raise ToolError(f"File not found: {path}", Err.NOT_FOUND)
    if os.path.splitext(path)[1].lower() not in (".sldprt", ".sldasm"):
        raise ToolError("The component must be a .SLDPRT or .SLDASM file", Err.INPUT)
    x_m, y_m, z_m = (ctx.to_m(v, unit) for v in (x, y, z))
    turn = None
    if any(v is not None for v in (rx, ry, rz, axis, angle)):
        turn = assembly.rotation_rows(rx, ry, rz, axis, angle)

    # the file must be loaded before it can be inserted
    app, asm_title = ctx.app, doc_title(asm)
    loaded = None
    try:
        loaded = app.GetOpenDocumentByName(path)
    except Exception:
        pass
    if loaded is None:
        if sw.assembly.sw_open(app, path) is None:
            raise ToolError(f"SolidWorks could not load {os.path.basename(path)}", Err.LOAD)
        sw.assembly.sw_activate(app, asm_title)
        asm = ctx.active_doc()
        if asm is None or doc_title(asm) != asm_title:
            raise ToolError(f"{os.path.basename(path)} was loaded, but the assembly {asm_title} could not be made "
                            f"active again. Nothing was inserted.", Err.LOAD)

    first = not assembly.components(asm)
    comp = sw.assembly.sw_add_component(asm, path, x_m, y_m, z_m)
    if comp is None:
        raise ToolError(f"SolidWorks could not insert {os.path.basename(path)}", Err.FEATURE)

    if turn is not None:
        # turn it about the point it was placed by, so that point stays where it was asked
        center = (x_m, y_m, z_m)
        rows_now, origin_now, _ = assembly.transform(comp)
        arm = tuple(origin_now[i] - center[i] for i in range(3))
        new_origin = tuple(center[i] + c for i, c in enumerate(assembly.apply_rows(turn, arm)))
        new_rows = tuple(assembly.apply_rows(turn, r) for r in rows_now)
        was_fixed = bool(value(comp, "IsFixed"))
        if was_fixed:
            sw.assembly.sw_set_fixed(asm, comp, False)
        sw.assembly.sw_write_transform(comp, new_rows, new_origin)
        if was_fixed:
            sw.assembly.sw_set_fixed(asm, comp, True)
        sw.common.sw_rebuild(asm)
        sw.common.sw_clear_selection(asm)

    row = assembly.component_row(comp, unit)
    row["unit"] = units.label(unit)
    message = f"Inserted {comp.Name2}"
    if row.get("fixed"):
        message += " (fixed in place" + (", as the first component)" if first else ")")
    else:
        message += " (floating until mated)"
    if "origin_in_assembly" in row:
        message += f"; its origin is at {row['origin_in_assembly']} {row['unit']}"
    return ok(message, row)


@tool(
    group="assembly",
    description="Make a sub-assembly component rigid or flexible in the active assembly. Rigid (SolidWorks' default) moves as one piece; flexible lets the sub-assembly's own parts move, within its own mates, when they are mated or dragged in this assembly. The state read back is reported; list_components shows it as 'solving'.",
    params={"component": {"type": "string", "description": "Name of the sub-assembly component from list_components, e.g. 'Chain-1'"},
            "solving": {"type": "string", "enum": ["flexible", "rigid"], "default": "flexible",
                        "description": "flexible or rigid"}},
    required=["component"],
    needs="assembly",
)
def set_subassembly_solving(ctx, component, solving="flexible"):
    asm = ctx.doc
    key = str(solving or "").strip().lower()
    if key not in ("flexible", "rigid"):
        raise ToolError("solving must be flexible or rigid", Err.INPUT)
    comp = assembly.component(asm, component)
    name = str(comp.Name2)
    path = ""
    try:
        path = str(value(comp, "GetPathName") or "")
    except Exception:
        pass
    if not path.lower().endswith(".sldasm"):
        raise ToolError(f"{name} is a part, not a sub-assembly. Only a sub-assembly can be flexible or rigid.",
                        Err.INPUT)
    if "/" in name:
        raise ToolError(f"{name} sits inside another sub-assembly. Its solving is set in that sub-assembly: "
                        f"open {name.split('/')[-2].rsplit('-', 1)[0]} and set it there.", Err.INPUT)
    wanted = 1 if key == "flexible" else 0
    if int(comp.Solving) == wanted:
        return ok(f"{name} is already {key}", {"component": name, "solving": key, "changed": False})
    suppression = sw.assembly.sw_suppression(comp)
    if suppression not in (2, 3):
        raise ToolError(f"{name} is suppressed or lightweight. Resolve it first.", Err.FEATURE)

    problems_before = assembly.mate_error_codes(asm)
    if not sw.assembly.sw_set_solving(asm, comp, suppression, wanted):
        raise ToolError(f"Could not select {name}", Err.SELECTION)
    sw.common.sw_rebuild(asm)
    comp, _ = assembly.find_component(asm, name)
    now = int(comp.Solving) if comp is not None else -1
    if now != wanted:
        raise ToolError(f"SolidWorks left {name} {_SOLVING.get(now, 'as it was')}", Err.FEATURE,
                        {"component": name, "solving": _SOLVING.get(now)})
    data = {"component": name, "solving": key, "changed": True}
    message = f"{name} is now {key}"
    broken = assembly.new_mate_errors(asm, problems_before)
    if broken:
        data["mate_errors"] = broken
        message += f". WARNING: these mates now have errors: {', '.join(broken)}"
    return ok(message, data)


@tool(
    group="assembly",
    description="Hide or show components of the active assembly (visible = false hides). Hiding only stops the drawing: the component's mates still hold, it still counts in check_interference and it can still be mated to. A part inside a sub-assembly is addressed by path ('Door-1/Slat-3'), and a list changes several components in one call. list_components marks hidden ones with 'hidden': true.",
    params={"visible": {"type": "boolean", "default": False, "description": "false = hide, true = show"},
            "components": {"type": "array", "items": {"type": "string"}, "description": "Component names or paths"},
            "component": {"type": "string", "description": "One component, instead of the list"}},
    needs="assembly",
)
def set_component_visibility(ctx, visible=False, components=None, component=None):
    asm = ctx.doc
    if not isinstance(visible, bool):
        raise ToolError("visible must be true or false", Err.INPUT)
    names = []
    for given in (component, components):
        if given is None:
            continue
        if isinstance(given, str):
            names.append(given)
        elif isinstance(given, (list, tuple)) and all(isinstance(v, str) for v in given):
            names.extend(given)
        else:
            raise ToolError("components must be a list of component names", Err.INPUT)
    if not names:
        raise ToolError("Name the components to hide or show (components = [...])", Err.INPUT)
    found = []
    for name in names:
        comp, reason = assembly.find_component(asm, name)
        if comp is None:
            raise ToolError(reason[0].upper() + reason[1:] + ". Nothing was changed.", Err.SELECTION)
        if str(comp.Name2) not in [str(c.Name2) for c in found]:
            found.append(comp)
    rows, failed = [], []
    for comp in found:
        was_hidden = assembly.is_hidden(comp)
        sw.assembly.sw_set_visible(comp, visible)
        now_hidden = assembly.is_hidden(comp)
        if now_hidden == (not visible):
            rows.append({"component": str(comp.Name2), "visible": not now_hidden, "changed": was_hidden != now_hidden})
        else:
            failed.append(str(comp.Name2))
    sw.common.sw_redraw(asm)
    word = "visible" if visible else "hidden"
    if failed:
        raise ToolError(f"SolidWorks did not make these {word}: {', '.join(failed)}", Err.INTERNAL,
                        {"components": rows, "failed": failed})
    hidden_now = [str(c.Name2) for c in assembly.all_components(asm) if assembly.is_hidden(c)]
    return ok(f"{len(rows)} component(s) now {word}"
              + (f"; hidden in this assembly: {', '.join(hidden_now[:30])}" if hidden_now else "; nothing is hidden"),
              {"components": rows, "hidden": hidden_now})


@tool(
    group="assembly",
    description="Fix a top-level component of the active assembly where it is, or release it (fixed = false). Mates and move_component cannot move a fixed component. The state read back is reported; list_components shows it as 'fixed'.",
    params={"component": {"type": "string", "description": "Component name from list_components"},
            "fixed": {"type": "boolean", "default": True, "description": "true = fix, false = float"}},
    required=["component"],
    needs="assembly",
)
def set_component_fixed(ctx, component, fixed=True):
    asm = ctx.doc
    if not isinstance(fixed, bool):
        raise ToolError("fixed must be true or false", Err.INPUT)
    comp = assembly.top_level_component(asm, component, "fixed or floated")
    name = str(comp.Name2)
    word = "fixed" if fixed else "floating"
    if bool(value(comp, "IsFixed")) == fixed:
        return ok(f"{name} is already {word}", {"component": name, "fixed": fixed, "changed": False})
    problems_before = assembly.mate_error_codes(asm)
    if not sw.assembly.sw_set_fixed(asm, comp, fixed):
        raise ToolError(f"SolidWorks did not make {name} {word}", Err.FEATURE,
                        {"component": name, "fixed": bool(value(comp, "IsFixed"))})
    sw.common.sw_rebuild(asm)
    sw.common.sw_clear_selection(asm)
    data = {"component": name, "fixed": fixed, "changed": True}
    message = f"{name} is now {word}"
    broken = assembly.new_mate_errors(asm, problems_before)
    if broken:
        data["mate_errors"] = broken
        message += f". WARNING: these mates now have errors: {', '.join(broken)}"
    return ok(message, data)


@tool(
    group="assembly",
    description="Move a floating top-level component to a position and orientation in the assembly. x, y, z are where its ORIGIN goes, in assembly coordinates (an omitted one stays). Orientation: rx, ry, rz in degrees applied about the assembly X, then Y, then Z axis starting from axes parallel to the assembly's, or one turn of angle degrees about axis. With relative = true, both are a shift from the current pose, about the component's own origin. Mates still apply: the result says where the component ended up and whether that is where it was put. A fixed component is refused.",
    params={"component": {"type": "string", "description": "Component name from list_components"},
            "x": {"type": "number", "description": "X of the component's origin (or the shift along X with relative)"},
            "y": {"type": "number", "description": "Y of the component's origin (or the shift along Y)"},
            "z": {"type": "number", "description": "Z of the component's origin (or the shift along Z)"},
            "rx": {"type": "number", "description": "Degrees about the assembly X axis"},
            "ry": {"type": "number", "description": "Degrees about the assembly Y axis (applied after rx)"},
            "rz": {"type": "number", "description": "Degrees about the assembly Z axis (applied last)"},
            "axis": _AXIS,
            "angle": {"type": "number", "description": "With axis: degrees to turn"},
            "relative": {"type": "boolean", "default": False, "description": "true = shift and turn from where it is now"},
            "unit": {"type": "string", "description": "Unit of x, y, z (mm, cm, m, inch or ft)"}},
    required=["component"],
    needs="assembly",
)
def move_component(ctx, component, x=None, y=None, z=None, rx=None, ry=None, rz=None, axis=None, angle=None,
                   relative=False, unit=None):
    asm = ctx.doc
    if not isinstance(relative, bool):
        raise ToolError("relative must be true or false", Err.INPUT)
    shift = []
    for given, label in ((x, "x"), (y, "y"), (z, "z")):
        if given is None:
            shift.append(None)
            continue
        if isinstance(given, bool) or not isinstance(given, (int, float)) or not math.isfinite(given):
            raise ToolError(f"{label} must be a number", Err.INPUT)
        metres = ctx.to_m(float(given), unit)
        if abs(metres) > 500:
            raise ToolError(f"{label} is more than 500 m from the origin", Err.INPUT)
        shift.append(metres)
    has_rotation = any(v is not None for v in (rx, ry, rz, axis, angle))
    if not has_rotation and all(v is None for v in shift):
        raise ToolError("Nothing to do: give x / y / z and / or a rotation (rx, ry, rz in degrees, or axis + angle)",
                        Err.INPUT)
    turn = assembly.rotation_rows(rx, ry, rz, axis, angle)

    comp = assembly.top_level_component(asm, component, "moved")
    name = str(comp.Name2)
    if bool(value(comp, "IsFixed")):
        raise ToolError(f"{name} is fixed and cannot be moved. Float it first with set_component_fixed "
                        f"(fixed = false), move it, and fix it again if needed.", Err.FEATURE)

    rows_now, origin_now, _ = assembly.transform(comp)
    if relative:
        origin = tuple(origin_now[i] + (shift[i] or 0.0) for i in range(3))
        rows = tuple(assembly.apply_rows(turn, row) for row in rows_now) if has_rotation else rows_now
    else:
        origin = tuple(origin_now[i] if shift[i] is None else shift[i] for i in range(3))
        rows = turn if has_rotation else rows_now
    if any(abs(c) > 500 for c in origin):
        raise ToolError("That would put the component more than 500 m from the origin", Err.INPUT)

    before = assembly.positions(asm)
    sw.assembly.sw_write_transform(comp, rows, origin)
    sw.common.sw_rebuild(asm)
    sw.common.sw_clear_selection(asm)

    comp, _ = assembly.find_component(asm, name)
    rows_after, origin_after, _ = assembly.transform(comp)
    off = math.dist(origin_after, origin)
    turned_off = max(abs(a - b) for ra, rb in zip(rows_after, rows) for a, b in zip(ra, rb))
    unit_str = units.label(unit)
    row = assembly.component_row(comp, unit)
    row["unit"] = unit_str
    row["as_asked"] = off < 1e-7 and turned_off < 1e-7
    row["moved"] = assembly.moved_since(asm, before, unit)
    if row["as_asked"]:
        message = f"{name}: origin at {row.get('origin_in_assembly')} {unit_str}"
        if has_rotation:
            message += ", turned as asked"
        if math.dist(origin_after, origin_now) < 1e-9 and not has_rotation:
            message += " (it was already there)"
    else:
        message = (f"{name} did NOT stay where it was put: its mates moved it to {row.get('origin_in_assembly')} "
                   f"{unit_str}" + (" and turned it" if turned_off >= 1e-7 else "")
                   + ". Suppress or delete the mates that hold it if it must go there.")
    return ok(message, row)
