"""Assembly components: list them, their faces and sketches; insert, fix, move, hide, make flexible.

Reading lives in core/assembly.py. The SolidWorks calls that change the model are the sw_* helpers below.
"""

import logging
import math
import os


from ..core.api import assembly, doc_title, Err, geometry, ok, open_documents, tool, ToolError, units, value
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


def _mate_states(asm) -> dict:
    """name -> (type, error code, only a warning) of every mate, also mates in folders: one walk of the tree."""
    return {name: (str(value(feat, "GetTypeName2")),) + tuple(assembly.mate_error(feat))
            for name, feat in assembly.mate_features(asm)}


def _top_level_target(asm, component, action: str):
    """A top-level component that is not an instance of a component pattern, or a refusal."""
    comp = assembly.top_level_component(asm, component, action)
    try:
        pattern = bool(value(comp, "IsPatternInstance"))
    except Exception:
        pattern = False
    if pattern:
        raise ToolError(f"{comp.Name2} is an instance of a component pattern: change the pattern instead.", Err.INPUT)
    return comp


@tool(
    group="assembly",
    description="Delete a top-level component from the active assembly. SolidWorks deletes the mates that use it with it; the result lists them (name and type), the components left, and any other mate that now has an error. A component inside a sub-assembly is refused (it belongs to the sub-assembly's file), as is an instance of a component pattern.",
    params={"component": {"type": "string", "description": "Top-level component name from list_components, e.g. Bracket-2"}},
    required=["component"],
    needs="assembly",
)
def delete_component(ctx, component):
    asm = ctx.doc
    comp = _top_level_target(asm, component, "deleted")
    name = str(comp.Name2)
    mates_before = _mate_states(asm)
    sw.assembly.sw_delete_component(asm, comp)
    left = [str(c.Name2) for c in assembly.components(asm)]
    if name in left:
        raise ToolError(f"SolidWorks did not delete {name}", Err.FEATURE, {"component": name})
    sw.common.sw_rebuild(asm)
    mates_after = _mate_states(asm)
    gone = [{"name": n, "type": t} for n, (t, _, _) in mates_before.items() if n not in mates_after]
    data = {"deleted": name, "mates_deleted": gone, "components_left": left}
    message = (f"Deleted {name} and {len(gone)} mate(s) that used it"
               + (f" ({', '.join(m['name'] for m in gone)})" if gone else "") + f"; {len(left)} components left")
    broken = sorted(n for n, (_, code, _) in mates_after.items() if code and not mates_before.get(n, ("", 0))[1])
    if broken:
        data["mate_errors"] = broken
        message += f". WARNING: these mates now have errors: {', '.join(broken)}"
    return ok(message, data)


@tool(
    group="assembly",
    description="Replace a top-level component of the active assembly by another part or assembly file. SolidWorks keeps the component's place and re-attaches the mates it can; nothing is fixed silently: the result lists every mate as kept, broken (with its error) or gone, and the new component's name and position. all_instances (default false) replaces every instance of the same file; configuration is the new file's configuration (default its active one; the result says which). A file named like one already in use (another folder) is refused: SolidWorks cannot load both, so copy_file it to a new name first.",
    params={"component": {"type": "string", "description": "Top-level component name from list_components, e.g. Bracket-2"},
            "filepath": {"type": "string", "description": "The part (.sldprt) or assembly (.sldasm) file to put in its place"},
            "all_instances": {"type": "boolean", "default": False,
                              "description": "Replace every instance of the component's file, not only this one"},
            "configuration": {"type": "string", "description": "Configuration of the new file (default its active one)"}},
    required=["component", "filepath"],
    needs="assembly",
)
def replace_component(ctx, component, filepath, all_instances=None, configuration=None):
    asm = ctx.doc
    if all_instances is not None and not isinstance(all_instances, bool):
        raise ToolError("all_instances must be true or false", Err.INPUT)
    every = bool(all_instances)
    comp = _top_level_target(asm, component, "replaced")
    name = str(comp.Name2)
    if not isinstance(filepath, str) or not filepath.strip():
        raise ToolError("Give the filepath of the part or assembly to put in its place", Err.INPUT)
    path = os.path.abspath(filepath.strip())
    if os.path.splitext(path)[1].lower() not in (".sldprt", ".sldasm"):
        raise ToolError("filepath must be a part (.sldprt) or an assembly (.sldasm)", Err.INPUT)
    if not os.path.isfile(path):
        raise ToolError(f"No such file: {path}", Err.INPUT)
    old_path = str(value(comp, "GetPathName") or "")
    if os.path.normcase(old_path) == os.path.normcase(path):
        raise ToolError(f"{name} already is {path}", Err.INPUT)
    if os.path.normcase(str(value(asm, "GetPathName") or "")) == os.path.normcase(path):
        raise ToolError("An assembly cannot be put inside itself", Err.INPUT)
    # SolidWorks holds one document per file name: another file of the same name cannot be loaded beside it
    same_name = [p for p in [old_path] + [str(value(d, "GetPathName") or "") for d in open_documents(ctx.app)]
                 if p and os.path.basename(p).lower() == os.path.basename(path).lower()
                 and os.path.normcase(p) != os.path.normcase(path)]
    if same_name:
        raise ToolError(f"{same_name[0]} has the same file name as {path} and is in use: SolidWorks cannot load "
                        f"both. Rename one (copy_file) first.", Err.INPUT)
    configs, active = sw.assembly.sw_configurations(ctx.app, path)
    if configuration is not None:
        chosen = next((c for c in configs if c.lower() == str(configuration).strip().lower()), None)
        if chosen is None:
            raise ToolError(f"{os.path.basename(path)} has no configuration '{configuration}'. "
                            f"Configurations: {', '.join(configs) or 'none read'}", Err.INPUT)
        config_from = "given"
    else:
        chosen, config_from = active, "the file's active configuration"
        if not chosen:
            raise ToolError(f"Could not read the configurations of {path}", Err.INPUT)

    before = {(str(c.Name2), os.path.normcase(str(value(c, "GetPathName") or ""))) for c in assembly.components(asm)}
    mates_before = _mate_states(asm)
    targets = [n for n, p in before if p == os.path.normcase(old_path)] if every else [name]
    done = sw.assembly.sw_replace_component(asm, comp, path, chosen, every)
    sw.common.sw_rebuild(asm)
    sw.common.sw_clear_selection(asm)

    after = {(str(c.Name2), os.path.normcase(str(value(c, "GetPathName") or ""))): c for c in assembly.components(asm)}
    new = [c for key, c in after.items() if key not in before and key[1] == os.path.normcase(path)]
    replaced = [n for n in targets if (n, os.path.normcase(old_path)) not in after]
    left = [n for n in targets if n not in replaced]
    if not done or not new or left:
        changed = sorted(set(after) ^ before)
        raise ToolError(f"SolidWorks did not replace {', '.join(left or targets)} by {os.path.basename(path)}"
                        + (f". The components did change: {', '.join(n for n, _ in changed)} - check list_components"
                           if changed else "; the assembly is as it was"), Err.FEATURE,
                        {"replaced": replaced, "not_replaced": left or targets,
                         "new_components": [str(c.Name2) for c in new]})
    unit_str = units.label(None)
    rows = []
    for c in new:
        try:
            rows.append({"name": str(c.Name2), "origin_in_assembly": geometry.point_in_unit(assembly.transform(c)[1])})
        except Exception:
            rows.append({"name": str(c.Name2)})
    mates_after = _mate_states(asm)
    broken = [dict({"name": n, "type": t, "error_code": code},
                   **({"warning_only": True} if warning else {}),
                   **({"had_error_before": True} if mates_before.get(n, ("", 0))[1] else {}))
              for n, (t, code, warning) in mates_after.items() if code]
    gone = [{"name": n, "type": t} for n, (t, _, _) in mates_before.items() if n not in mates_after]
    kept = [n for n, (_, code, _) in mates_after.items() if n in mates_before and not code]
    data = {"replaced": replaced, "new_components": rows, "file": path, "configuration": chosen,
            "configuration_from": config_from, "all_instances": every, "mates_kept": kept, "mates_broken": broken,
            "mates_gone": gone, "unit": unit_str}
    message = (f"Replaced {', '.join(replaced)} by {os.path.basename(path)} ({chosen}, {config_from}): "
               f"{', '.join(r['name'] + ' at ' + str(r.get('origin_in_assembly')) for r in rows)} {unit_str}; "
               f"mates kept {len(kept)}, broken {len(broken)}, gone {len(gone)}")
    if broken:
        message += f". Broken (not fixed): {', '.join(b['name'] for b in broken)}"
    if gone:
        message += f". Gone: {', '.join(g['name'] for g in gone)}"
    return ok(message, data)


_DIRECTIONS = {"+x": ("x", 0, 1), "-x": ("x", 0, -1), "+y": ("y", 1, 1), "-y": ("y", 1, -1),
               "+z": ("z", 2, 1), "-z": ("z", 2, -1)}
EXPLODE_TOLERANCE_M = 1e-6


def _origins(comps) -> dict:
    return {str(c.Name2): assembly.transform(c)[1] for c in comps}


@tool(
    group="assembly",
    description="Make a new exploded view of the active assembly's configuration: steps move components along the assembly axes (direction +x, -x, +y, -y, +z or -z; distance required). Components are named as in list_components (a path such as Sub-1/Part-1 for a part in a sub-assembly). The view is left shown exploded (show_exploded collapses it); while it is shown, mates, measurements, check_interference and export_stl see the exploded positions, so collapse it first. Mates are not changed. Returns the view's name and each component's measured move per step.",
    params={"steps": {"type": "array", "minItems": 1,
                      "items": {"type": "object",
                                "properties": {"components": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                                               "direction": {"type": "string", "enum": list(_DIRECTIONS)},
                                               "distance": {"type": "number"}},
                                "required": ["components", "direction", "distance"]},
                      "description": "The steps in order: [{components: [...], direction: '+z', distance: 50}]"},
            "unit": {"type": "string", "description": "mm, cm, m, inch or ft"}},
    required=["steps"],
    needs="assembly",
)
def create_exploded_view(ctx, steps, unit=None):
    asm = ctx.doc
    if not isinstance(steps, list) or not steps:
        raise ToolError("Give at least one step: {components, direction, distance}", Err.INPUT)
    plan = []
    for n, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            raise ToolError(f"Step {n} must be {{components, direction, distance}}", Err.INPUT)
        names = step.get("components")
        if not isinstance(names, list) or not names:
            raise ToolError(f"Step {n}: give the components to move", Err.INPUT)
        key = str(step.get("direction") or "").strip().lower()
        if key not in _DIRECTIONS:
            raise ToolError(f"Step {n}: direction must be {', '.join(_DIRECTIONS)}", Err.INPUT)
        distance_m = units.size_m(step.get("distance"), unit, f"Step {n}: distance")
        comps = [assembly.component(asm, name) for name in names]
        if len({str(c.Name2) for c in comps}) != len(comps):
            raise ToolError(f"Step {n}: a component is listed twice", Err.INPUT)
        plan.append((n, comps, key, distance_m))
    views_before = sw.assembly.sw_exploded_views(asm)
    collapsed = [v for v in views_before if sw.assembly.sw_is_exploded(asm, v)]
    for v in collapsed:                     # moves are measured from the collapsed assembly
        sw.assembly.sw_show_exploded(asm, v, False)
    start = assembly.positions(asm)
    steps_before = {str(value(st, "Name")) for st in sw.assembly.sw_explode_steps(asm)}
    sw.assembly.sw_create_exploded_view(asm)
    new_views = [v for v in sw.assembly.sw_exploded_views(asm) if v not in views_before]
    if len(new_views) != 1:
        raise ToolError("SolidWorks did not make a new exploded view", Err.FEATURE)
    view = new_views[0]

    def undo(reason):
        sw.assembly.sw_delete_exploded_view(asm, view)
        gone = view not in sw.assembly.sw_exploded_views(asm)
        raise ToolError(f"{reason}. " + (f"The exploded view {view} was taken out again" if gone else
                                         f"WARNING: the exploded view {view} could not be taken out"), Err.FEATURE)

    # SolidWorks 2026 starts a new view with a step of its own (Chain1, every component; measured: it moved a
    # fixed part 43 mm): taken out, so the view holds only the steps asked for
    for own in [st for st in sw.assembly.sw_explode_steps(asm) if str(value(st, "Name")) not in steps_before]:
        sw.assembly.sw_delete_explode_step(asm, own)
    if any(str(value(st, "Name")) not in steps_before for st in sw.assembly.sw_explode_steps(asm)):
        undo("SolidWorks put a step of its own in the new view and it could not be taken out")

    rows, unit_str = [], units.label(unit)
    for n, comps, key, distance_m in plan:
        axis, index, sign = _DIRECTIONS[key]
        before = _origins(comps)
        moved, tries = None, []
        for reverse in (False, True):                 # which way the plane's normal points is measured
            step = sw.assembly.sw_add_explode_step(asm, comps, axis, distance_m, reverse)
            if step is None:
                undo(f"Step {n}: SolidWorks did not add it")
            sw.assembly.sw_show_exploded(asm, view, True)
            after = _origins(comps)
            moved = {name: [b - a for a, b in zip(before[name], after[name])] for name in before}
            tries.append({name: [round(v * 1000, 3) for v in d] for name, d in moved.items()})
            want = sign * distance_m
            if all(abs(d[index] - want) <= EXPLODE_TOLERANCE_M and
                   all(abs(d[i]) <= EXPLODE_TOLERANCE_M for i in range(3) if i != index) for d in moved.values()):
                break
            sw.assembly.sw_delete_explode_step(asm, step)
            moved = None
        if moved is None:
            undo(f"Step {n}: the components did not move {distance_m * 1000:g} mm along {key} (measured moves, mm: "
                 f"{'; then '.join(str(t) for t in tries)})")
        rows.append({"step": n, "direction": key, "distance": ctx.from_m(distance_m, unit),
                     "moved": {name: [round(ctx.from_m(v, unit), 4) + 0.0 for v in d] for name, d in moved.items()}})
    # every component, not only the ones asked for: nothing else may move (a part inside a moved one moves with it)
    named = {str(c.Name2) for _, comps, _, _ in plan for c in comps}
    now = assembly.positions(asm)
    strays = sorted(name for name, (_, t) in start.items() if name in now
                    and not any(name == m or name.startswith(m + "/") for m in named)
                    and max(abs(a - b) for a, b in zip(t, now[name][1])) > EXPLODE_TOLERANCE_M)
    if strays:
        undo(f"The view also moved {', '.join(strays)}, which no step names")
    data = {"view": view, "steps": rows, "unit": unit_str, "shown": "exploded", "others_unmoved": True}
    summary = "; ".join(f"{r['step']}: {', '.join(r['moved'])} {r['distance']:g} {unit_str} {r['direction']}"
                        for r in rows)
    note = ""
    if collapsed:
        data["collapsed_first"] = collapsed
        note = f" ({', '.join(collapsed)} collapsed first)"
    return ok(f"Exploded view {view} with {len(rows)} step(s), shown exploded{note}: {summary}", data)


@tool(
    group="assembly",
    description="Show the active assembly's exploded view exploded (exploded true) or collapsed (false). name picks the view when the configuration has several (default: its only one). Returns the state read back and which components moved.",
    params={"exploded": {"type": "boolean", "description": "true = exploded, false = collapsed"},
            "name": {"type": "string", "description": "Exploded view name, e.g. ExplView1 (default: the only one)"}},
    required=["exploded"],
    needs="assembly",
)
def show_exploded(ctx, exploded, name=None):
    asm = ctx.doc
    if not isinstance(exploded, bool):
        raise ToolError("exploded must be true or false", Err.INPUT)
    views = sw.assembly.sw_exploded_views(asm)
    if not views:
        raise ToolError("The configuration has no exploded view: create_exploded_view first", Err.INPUT)
    if name is None:
        if len(views) > 1:
            raise ToolError(f"The configuration has {len(views)} exploded views: give name ({', '.join(views)})",
                            Err.INPUT)
        view, view_from = views[0], "its only exploded view"
    else:
        view = next((v for v in views if v.lower() == str(name).strip().lower()), None)
        if view is None:
            raise ToolError(f"No exploded view '{name}'. Exploded views: {', '.join(views)}", Err.INPUT)
        view_from = "given"
    before = assembly.positions(asm)
    was = sw.assembly.sw_is_exploded(asm, view)
    sw.assembly.sw_show_exploded(asm, view, exploded)
    state = sw.assembly.sw_is_exploded(asm, view)
    if state is not None and state != exploded:
        if was is not None:
            sw.assembly.sw_show_exploded(asm, view, was)            # a refusal leaves the display as it was
        raise ToolError(f"SolidWorks shows {view} {'exploded' if state else 'collapsed'}, not as asked; "
                        f"{'it was put back as it was' if sw.assembly.sw_is_exploded(asm, view) == was else 'WARNING: it could not be put back'}",
                        Err.FEATURE)
    moved = assembly.moved_since(asm, before, None)
    word = "exploded" if exploded else "collapsed"
    data = {"view": view, "view_from": view_from, "shown": word, "moved": moved, "state_read_back": state is not None}
    note = "" if state is not None else " (SolidWorks did not report the state; judge by the moves)"
    return ok(f"{view} is {word}; {len(moved)} component(s) moved{note}", data)
