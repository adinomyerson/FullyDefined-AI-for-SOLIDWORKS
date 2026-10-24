"""Mates: add, list, edit, suppress, delete, and step a distance or angle mate through values.

What a reference points at is read in core/assembly.py. The SolidWorks calls that change the model are the
sw_* helpers below.
"""

import logging
import math
import os
import re
import time


from ..core.api import assembly, capture, Err, ok, tool, ToolError, units
from ..core.api import value as com_value
from ..core.api import sw

logger = logging.getLogger(__name__)

# mate type -> swMateType_e, for add_mate
MATE_TYPES = {"coincident": 0, "concentric": 1, "perpendicular": 2, "parallel": 3, "tangent": 4, "distance": 5,
              "angle": 6}
MATE_ALIGN = {"aligned": 0, "anti_aligned": 1, "closest": 2}                 # swMateAlign_e
ADD_MATE_ERRORS = {0: "unknown error", 2: "this mate type does not fit these references",       # swAddMateError_e
                   3: "wrong alignment", 4: "these references cannot be mated this way",
                   5: "the mate would over-define the assembly", 6: "bad gear ratio"}
TYPE_NAMES = {0: "coincident", 1: "concentric", 2: "perpendicular", 3: "parallel", 4: "tangent", 5: "distance",
              6: "angle", 8: "symmetric", 9: "cam_follower", 10: "gear", 11: "width", 12: "lock_to_sketch",
              13: "rack_pinion", 15: "path", 16: "lock", 17: "screw", 18: "linear_coupler", 19: "universal_joint",
              20: "coordinate", 21: "slot", 22: "hinge", 23: "slider", 24: "profile_center", 25: "magnetic"}
ALIGN_NAMES = {0: "aligned", 1: "anti_aligned", 2: "closest"}
ALIGN_VALUES = {"aligned": 0, "anti_aligned": 1}
ENTITY_TYPES = {0: "point", 1: "line", 2: "circle", 3: "plane", 4: "cylinder", 5: "sphere", 6: "set", 7: "cone",
                8: "swept surface", 9: "surfaces", 10: "surface", 11: "ellipse", 12: "curve"}
FEATURE_ERRORS = {1: "unknown error", 38: "invalid edge", 39: "invalid face", 40: "could not create the surface",
                  41: "invalid entity", 42: "unknown tangent", 43: "a reference is missing (dangling)",
                  44: "entity is not linear", 45: "entity failed", 46: "over-defined", 47: "cannot be solved",
                  48: "broken"}
MAX_STEP_VALUES = 200


# ---------------------------------------------------------------- SolidWorks calls


# ---------------------------------------------------------------- reading mates

def _suppressed(feat) -> bool:
    try:
        return bool(com_value(feat, "IsSuppressed"))
    except Exception:
        return False


def _largest_turn_deg(asm, before) -> float:
    """The largest angle (degrees) any component has turned since `before` (assembly.positions())."""
    biggest = 0.0
    for name, (R, _t) in assembly.positions(asm).items():
        old = before.get(name)
        if old is None:
            continue
        trace = sum(R[i][k] * old[0][i][k] for i in range(3) for k in range(3))     # trace(R . R0^T)
        biggest = max(biggest, math.degrees(math.acos(max(-1.0, min(1.0, (trace - 1.0) / 2.0)))))
    return biggest


def _mate_row(name: str, feat, unit) -> dict:
    row = {"name": name}
    mate = None
    try:
        mate = sw.mates.sw_mate_data(feat)
    except Exception as e:
        logger.debug(f"GetSpecificFeature2 failed for {name}: {e}")
    mate_type = None
    try:
        mate_type = int(mate.Type)
    except Exception:
        pass
    row["type"] = TYPE_NAMES.get(mate_type, str(com_value(feat, "GetTypeName2")))
    suppressed = _suppressed(feat)
    if mate_type in (5, 6) and not suppressed:
        try:
            data = com_value(feat, "GetDefinition")
            if mate_type == 5:
                row["value"] = round(units.from_m(float(data.Distance), unit), 4) + 0.0
                row["unit"] = units.label(unit)
                if "Limit" in str(com_value(feat, "GetTypeName2")):
                    row["type"] = "limit_distance"
                    row["minimum"] = round(units.from_m(float(data.MinimumDistance), unit), 4) + 0.0
                    row["maximum"] = round(units.from_m(float(data.MaximumDistance), unit), 4) + 0.0
            else:
                row["value"] = round(math.degrees(float(data.Angle)), 4) + 0.0
                row["unit"] = "deg"
            row["dimension_flipped"] = bool(data.FlipDimension)
        except Exception as e:
            logger.debug(f"Could not read the value of {name}: {e}")
    between = []
    try:
        for entity in sw.mates.sw_mate_entities(mate):
            comp = None
            try:
                comp = entity.ReferenceComponent
            except Exception:
                pass
            between.append({"component": str(comp.Name2) if comp is not None else "(assembly)",
                            "entity": ENTITY_TYPES.get(int(entity.ReferenceType), "entity")})
    except Exception as e:
        logger.debug(f"Could not read the entities of {name}: {e}")
    row["between"] = between
    try:
        row["alignment"] = ALIGN_NAMES.get(int(mate.Alignment), str(mate.Alignment))
    except Exception:
        pass
    row["suppressed"] = suppressed
    code, warning = assembly.mate_error(feat)
    row["error_code"] = code
    if code:
        row["error"] = FEATURE_ERRORS.get(code, f"error {code}") + (" (warning)" if warning else "")
    return row


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
_MATE_NAME = {"type": "string", "description": "Mate name from list_mates"}


@tool(
    group="assembly",
    description="Add a mate in the active assembly between two references. Simple form: point1 / point2 are points in ASSEMBLY coordinates that lie on the component faces to mate, or plane1 / plane2 name assembly planes. Full form: ref1 / ref2 can also pick a face by component and face id (list_component_faces), a sketch point or segment of a component (list_component_sketches), a part's own plane, axis or origin, and anything on a part inside a sub-assembly (component path like 'Door-1/Slat-3'). An edge or vertex: {component, edge_id} or {component, vertex_id}, ids from list_component_faces with include ['edges', 'vertices']. mate_type: coincident, concentric, parallel, perpendicular, tangent, distance (value = distance) or angle (value = degrees). A distance between a flat face and a cylinder is measured to the cylinder's AXIS, not its surface: for a gap of 0.5 to a disc of radius 20, give 20.5. A distance mate with minimum and maximum is a LIMIT mate: the distance may sit anywhere in that range, so another mate (the drive) can move the part only between the two ends; a value outside the range is refused. The result states what was picked on each side (component, face id, type, normal or axis), which components moved, and where to.",
    params={"mate_type": {"type": "string", "description": "Kind of mate",
                          "enum": ["coincident", "concentric", "parallel", "perpendicular", "tangent", "distance", "angle"]},
            "point1": _POINT, "point2": _POINT,
            "plane1": {"type": "string", "description": "Instead of point1: Front, Top, Right or an assembly plane name"},
            "plane2": {"type": "string", "description": "Instead of point2: Front, Top, Right or an assembly plane name"},
            "ref1": _REF, "ref2": _REF,
            "value": {"type": "number", "description": "Distance for a distance mate, degrees for an angle mate"},
            "minimum": {"type": "number", "description": "With mate_type distance: smallest distance allowed. Together with maximum this makes a LIMIT mate; value is where it starts."},
            "maximum": {"type": "number", "description": "With mate_type distance: largest distance allowed"},
            "alignment": {"type": "string", "enum": ["closest", "aligned", "anti_aligned"], "default": "closest",
                          "description": "Whether the two faces point the same way (aligned) or against each other (anti_aligned)"},
            "flip": {"type": "boolean", "default": False, "description": "Put the distance / angle on the other side"},
            "unit": {"type": "string", "description": "Unit for points and distance (mm, cm, m, inch or ft)"}},
    required=["mate_type"],
    needs="assembly",
)
def add_mate(ctx, mate_type, point1=None, point2=None, plane1=None, plane2=None, ref1=None, ref2=None, value=None,
             minimum=None, maximum=None, alignment="closest", flip=False, unit=None):
    asm = ctx.doc
    kind = str(mate_type or "").strip().lower()
    if kind not in MATE_TYPES:
        raise ToolError(f"Unknown mate_type '{mate_type}'. Use one of: {', '.join(MATE_TYPES)}", Err.INPUT)
    align_key = str(alignment or "closest").strip().lower()
    if align_key not in MATE_ALIGN:
        raise ToolError("alignment must be closest, aligned or anti_aligned", Err.INPUT)
    unit_str = units.label(unit)
    distance_m, angle_rad, upper_m, lower_m = 0.0, 0.0, None, None
    if (minimum is not None or maximum is not None) and kind != "distance":
        raise ToolError("minimum / maximum make a limit mate and need mate_type distance", Err.INPUT)
    if kind == "distance":
        if value is None:
            raise ToolError("A distance mate needs a value", Err.INPUT)
        distance_m = abs(ctx.to_m(float(value), unit))
        if minimum is not None or maximum is not None:
            if minimum is None or maximum is None:
                raise ToolError("A limit mate needs both minimum and maximum", Err.INPUT)
            for limit, label in ((minimum, "minimum"), (maximum, "maximum")):
                if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not math.isfinite(limit) or limit < 0:
                    raise ToolError(f"{label} must be a number, 0 or more", Err.INPUT)
            lower_m, upper_m = ctx.to_m(float(minimum), unit), ctx.to_m(float(maximum), unit)
            if upper_m - lower_m < 1e-9:
                raise ToolError("maximum must be larger than minimum", Err.INPUT)
            if upper_m > 500:
                raise ToolError("maximum is more than 500 m", Err.INPUT)
            if not lower_m - 1e-12 <= distance_m <= upper_m + 1e-12:
                raise ToolError(f"value ({value}) must lie between minimum ({minimum}) and maximum ({maximum}): "
                                f"it is where the mate starts", Err.INPUT)
    elif kind == "angle":
        if value is None:
            raise ToolError("An angle mate needs a value in degrees", Err.INPUT)
        angle_rad = math.radians(float(value))

    refs = []
    for index, (ref, point, plane) in enumerate(((ref1, point1, plane1), (ref2, point2, plane2)), start=1):
        given = [n for n, v in ((f"ref{index}", ref), (f"point{index}", point), (f"plane{index}", plane))
                 if v is not None and v != ""]
        if not given:
            raise ToolError(f"Reference {index} is missing: give point{index}, plane{index} or ref{index}", Err.INPUT)
        if len(given) > 1:
            raise ToolError(f"Reference {index} is given {len(given)} ways ({', '.join(given)}): use one", Err.INPUT)
        if ref is None:
            ref = {"plane": str(plane)} if plane else {"point": point}
        refs.append(assembly.resolve_reference(asm, ref, index, unit))

    comp_a, comp_b = refs[0]["component"], refs[1]["component"]
    if comp_a is not None and comp_b is not None and str(comp_a.Name2) == str(comp_b.Name2):
        raise ToolError(f"Both references are on {comp_a.Name2}. A mate needs two different components "
                        f"(or a component and something of the assembly).", Err.SELECTION)
    if comp_a is None and comp_b is None:
        raise ToolError("Both references belong to the assembly itself. One of them must be on a component.",
                        Err.SELECTION)

    positions_before = {c.Name2: assembly.transform(c)[1] for c in assembly.components(asm)}
    errors_before = assembly.mate_error_codes(asm)
    sw.common.sw_clear_selection(asm)
    for resolved in refs:
        if not assembly.select_reference(asm, resolved, 1):
            sw.common.sw_clear_selection(asm)
            raise ToolError(f"Could not select the {resolved['text']}", Err.SELECTION)
    mate, status = sw.mates.sw_add_mate(asm, MATE_TYPES[kind], MATE_ALIGN[align_key], flip, distance_m,
                               distance_m if upper_m is None else upper_m, distance_m if lower_m is None else lower_m,
                               angle_rad)
    sw.common.sw_clear_selection(asm)
    between = f"{refs[0]['text']} and {refs[1]['text']}"
    if mate is None:
        raise ToolError(f"Mate not added between {between}: {ADD_MATE_ERRORS.get(status, f'error code {status}')}",
                        Err.FEATURE, {"error_code": status})
    sw.common.sw_rebuild(asm)

    # a mate that conflicts with the existing ones is taken out again
    broken = assembly.new_mate_errors(asm, errors_before)
    if broken:
        mate_name = mate.Name
        try:
            assembly.delete_mate(asm, mate_name)
        except Exception as e:
            logger.warning(f"Could not remove conflicting mate {mate_name}: {e}")
        others = [n for n in broken if n != mate_name]
        detail = f" It conflicts with {', '.join(others)}." if others else ""
        raise ToolError(f"Mate not added between {between}: it over-defines the assembly or cannot be satisfied "
                        f"together with the existing mates.{detail}", Err.FEATURE, {"conflicts_with": others})

    moved = []
    for comp in assembly.components(asm):
        t = assembly.transform(comp)[1]
        old = positions_before.get(comp.Name2)
        if old is not None and math.dist(t, old) > 1e-9:
            moved.append({"name": comp.Name2, "origin_in_assembly": [round(units.from_m(v, unit), 3) + 0.0 for v in t]})
    data = {"mate": mate.Name, "mate_type": kind, "between": [refs[0]["text"], refs[1]["text"]],
            "references": [refs[0]["info"], refs[1]["info"]], "alignment": align_key, "unit": unit_str,
            "moved": moved}
    message = f"{mate.Name}: {kind} mate between {between}"
    if kind == "distance":
        data["distance"] = value
        message += f", {value}{unit_str}"
        if upper_m is not None:
            data["mate_type"] = "limit_distance"
            data["minimum"], data["maximum"] = minimum, maximum
            message += f" (limit mate: {minimum} to {maximum}{unit_str})"
    elif kind == "angle":
        data["angle_deg"] = value
        message += f", {value} deg"
    if moved:
        message += ". Moved: " + ", ".join(f"{m['name']} to {m['origin_in_assembly']}" for m in moved) + f" {unit_str}"
    return ok(message, data)


@tool(
    group="assembly",
    description="List every mate of the active assembly: name, type, value (distance or angle), the two components, what was picked on each (plane, cylinder, point, line), alignment, whether the dimension is flipped, whether it is suppressed, and its error state after the last rebuild. A limit mate has type limit_distance with its minimum and maximum. Read-only.",
    params={"unit": {"type": "string", "description": "Unit for distances (mm, cm, m, inch or ft)"}},
    needs="assembly",
)
def list_mates(ctx, unit=None):
    rows = [_mate_row(name, feat, unit) for name, feat in assembly.mate_features(ctx.doc)]
    broken = [r["name"] for r in rows if r["error_code"]]
    off = [r["name"] for r in rows if r["suppressed"]]
    message = f"{len(rows)} mates" + (f", {len(off)} suppressed" if off else "")
    message += f", {len(broken)} with errors" + (f" ({', '.join(broken[:20])})" if broken else "")
    return ok(message, {"count": len(rows), "length_unit": units.label(unit), "mates": rows})


@tool(
    group="assembly",
    description="Change an existing mate: value for a new distance or angle, flip_dimension to put the distance / angle on the other side, flip_alignment to swap aligned and anti-aligned (or alignment to set it directly). The assembly is rebuilt; a change the other mates do not allow is undone and refused. Reports the mate as read back and which components moved.",
    params={"name": {"type": "string", "description": "Mate name from list_mates, e.g. 'Distance1'"},
            "value": {"type": "number", "description": "New distance (in unit) for a distance mate, or degrees for an angle mate"},
            "flip_dimension": {"type": "boolean", "default": False, "description": "Put the distance / angle on the other side"},
            "flip_alignment": {"type": "boolean", "default": False, "description": "Switch between aligned and anti-aligned"},
            "alignment": {"type": "string", "enum": ["aligned", "anti_aligned"], "description": "Set the alignment outright instead of flipping it"},
            "minimum": {"type": "number", "description": "New smallest distance of a limit distance mate"},
            "maximum": {"type": "number", "description": "New largest distance of a limit distance mate"},
            "unit": {"type": "string", "description": "Unit of the distance (mm, cm, m, inch or ft)"}},
    required=["name"],
    needs="assembly",
)
def edit_mate(ctx, name, value=None, flip_dimension=False, flip_alignment=False, alignment=None, minimum=None,
              maximum=None, unit=None):
    return _edit_mate(ctx, ctx.doc, name, value, flip_dimension, flip_alignment, alignment, minimum, maximum, unit)


def _edit_mate(ctx, asm, name, value=None, flip_dimension=False, flip_alignment=False, alignment=None, minimum=None,
               maximum=None, unit=None):
    """edit_mate's work, shared with step_mate: the Result, or a refusal."""
    for flag, label in ((flip_dimension, "flip_dimension"), (flip_alignment, "flip_alignment")):
        if not isinstance(flag, bool):
            raise ToolError(f"{label} must be true or false", Err.INPUT)
    align_key = None
    if alignment is not None:
        align_key = str(alignment).strip().lower()
        if align_key not in ALIGN_VALUES:
            raise ToolError("alignment must be aligned or anti_aligned", Err.INPUT)
        if flip_alignment:
            raise ToolError("Give alignment or flip_alignment, not both", Err.INPUT)
    for limit, label in ((minimum, "minimum"), (maximum, "maximum")):
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, (int, float))
                                  or not math.isfinite(limit) or limit < 0):
            raise ToolError(f"{label} must be a number, 0 or more", Err.INPUT)
    new_limits = minimum is not None or maximum is not None
    if value is None and not flip_dimension and not flip_alignment and align_key is None and not new_limits:
        raise ToolError("Nothing to change: give value, flip_dimension, flip_alignment, alignment or (for a limit "
                        "mate) minimum / maximum", Err.INPUT)
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
        raise ToolError("value must be a number", Err.INPUT)

    mate_name, feat = assembly.find_mate(asm, name)
    if _suppressed(feat):
        raise ToolError(f"{mate_name} is suppressed. Unsuppress it first (unsuppress_mate).", Err.FEATURE)
    mate = sw.mates.sw_mate_data(feat)
    mate_type = int(mate.Type)
    kind = TYPE_NAMES.get(mate_type, str(mate_type))
    if (value is not None or flip_dimension) and mate_type not in (5, 6):
        raise ToolError(f"{mate_name} is a {kind} mate: only distance and angle mates have a value or a dimension "
                        f"to flip", Err.INPUT)
    new_value = None
    if value is not None:
        if mate_type == 5:
            new_value = ctx.to_m(float(value), unit)
            if new_value < 0:
                raise ToolError("A distance cannot be negative. Use flip_dimension to put it on the other side.",
                                Err.INPUT)
            if new_value > 500:
                raise ToolError("The distance is more than 500 m", Err.INPUT)
        else:
            new_value = math.radians(float(value))

    data = None
    try:
        data = com_value(feat, "GetDefinition")
    except Exception:
        pass
    if data is None:
        raise ToolError(f"SolidWorks does not let {kind} mates be edited this way", Err.FEATURE)
    attr = "Distance" if mate_type == 5 else "Angle"
    old = {}
    try:
        if mate_type in (5, 6):
            old[attr] = float(getattr(data, attr))
            old["FlipDimension"] = bool(data.FlipDimension)
        old["MateAlignment"] = int(data.MateAlignment)
    except Exception as e:
        logger.debug(f"Could not read the definition of {mate_name}: {e}")
    if (flip_alignment or align_key is not None) and "MateAlignment" not in old:
        raise ToolError(f"A {kind} mate has no alignment to change", Err.INPUT)

    # limit distance mates: the value must stay inside the limits
    is_limit = mate_type == 5 and "Limit" in str(com_value(feat, "GetTypeName2"))
    if new_limits and not is_limit:
        raise ToolError(f"{mate_name} is not a limit mate, so it has no minimum / maximum. A limit mate is made with "
                        f"add_mate (mate_type distance, minimum, maximum).", Err.INPUT)
    low = high = None
    if is_limit:
        try:
            old["MinimumDistance"] = float(data.MinimumDistance)
            old["MaximumDistance"] = float(data.MaximumDistance)
        except Exception as e:
            logger.debug(f"Could not read the limits of {mate_name}: {e}")
        low = ctx.to_m(float(minimum), unit) if minimum is not None else old.get("MinimumDistance", 0.0)
        high = ctx.to_m(float(maximum), unit) if maximum is not None else old.get("MaximumDistance", 500.0)
        if high - low < 1e-9:
            raise ToolError("maximum must be larger than minimum", Err.INPUT)
        target = new_value if new_value is not None else old.get("Distance", low)
        if not low - 1e-9 <= target <= high + 1e-9:
            shown = [round(ctx.from_m(v, unit), 4) + 0.0 for v in (target, low, high)]
            raise ToolError(f"{mate_name} is a limit mate: {shown[0]} is outside its range {shown[1]} to {shown[2]}. "
                            + ("Change minimum / maximum as well, or the value." if new_limits else
                               "Give a value inside the range, or change minimum / maximum."), Err.INPUT)

    new = dict(old)
    if is_limit and new_limits:
        new["MinimumDistance"], new["MaximumDistance"] = low, high
    if new_value is not None:
        new[attr] = new_value
    if flip_dimension:
        new["FlipDimension"] = not old["FlipDimension"]
    current_align = int(mate.Alignment)
    if flip_alignment:
        new["MateAlignment"] = 1 if current_align == 0 else 0
    elif align_key is not None:
        new["MateAlignment"] = ALIGN_VALUES[align_key]
    else:
        new["MateAlignment"] = current_align                       # keep what the mate has now

    def apply(values) -> bool:
        return sw.mates.sw_modify_mate(asm, feat, values)

    def restore_old():
        try:
            apply(dict(old, MateAlignment=current_align, FlipDimension=False))
            sw.common.sw_clear_selection(asm)
        except Exception as e:
            logger.warning(f"Could not restore {mate_name}: {e}")

    positions = assembly.positions(asm)
    problems_before = assembly.mate_problems(asm)
    # Angle mates (measured on SolidWorks 2026 SP4.1): the flag does not say which side the part is on. Writing
    # FlipDimension = True has turned the part over every time; writing False sometimes keeps the side and
    # sometimes turns it over too (a new value 40 -> 25 did). So: a flip writes True, anything else writes
    # False, and then the side is checked by how far the part actually turned - |new - old| on the same side,
    # new + old on the other side. If it is on the wrong side, writing True once more turns it over.
    if mate_type == 6 and "FlipDimension" in new:
        new["FlipDimension"] = bool(flip_dimension)
    done = apply(new)
    sw.common.sw_clear_selection(asm)
    side_wrong = False
    if done and mate_type == 6 and "Angle" in old and "Angle" in new:
        a0, a1 = math.degrees(old["Angle"]), math.degrees(new["Angle"])
        same, other = abs(a1 - a0), a0 + a1
        if abs(other - same) > 0.5:                                   # otherwise the two sides look alike
            def on_other_side() -> bool:
                turn = _largest_turn_deg(asm, positions)
                return abs(turn - other) < abs(turn - same)
            if on_other_side() != bool(flip_dimension):
                done = apply(dict(new, FlipDimension=True))
                sw.common.sw_clear_selection(asm)
                side_wrong = done and on_other_side() != bool(flip_dimension)
    if side_wrong:
        restore_old()
        raise ToolError(f"{mate_name} not changed: SolidWorks put the part on the "
                        f"{'same' if flip_dimension else 'other'} side and would not turn it over. Check with "
                        f"list_components.", Err.FEATURE, {"mate": mate_name})
    if done and flip_dimension and mate_type == 6 and not assembly.moved_since(asm, positions, unit):
        restore_old()                                                 # nothing turned: the flip did not happen
        raise ToolError(f"{mate_name} not flipped: SolidWorks kept every component where it was. The other mates "
                        f"may hold the part on this side (a fully positioned chain cannot fold the other way).",
                        Err.FEATURE, {"mate": mate_name})
    broken = sorted(n for n in assembly.mate_problems(asm) if n not in problems_before)
    if not done or broken:
        restore = dict(old)
        restore["MateAlignment"] = current_align
        if mate_type == 6 and "FlipDimension" in restore:
            restore["FlipDimension"] = bool(flip_dimension)          # True turns it over: only to undo a flip
        try:
            apply(restore)
            sw.common.sw_clear_selection(asm)
        except Exception as e:
            logger.warning(f"Could not restore {mate_name}: {e}")
        still = sorted(n for n in assembly.mate_problems(asm) if n not in problems_before)
        detail = ""
        if broken:
            others = [n for n in broken if n != mate_name]
            detail = (f": it cannot be satisfied together with {', '.join(others)}" if others
                      else ": the assembly cannot be solved with it")
        message = f"{mate_name} not changed{detail or ': SolidWorks refused the change'}."
        if still:
            message += f" WARNING: {', '.join(still)} now show an error - check with list_mates."
        raise ToolError(message, Err.FEATURE, {"mate": mate_name, "conflicts_with": [n for n in broken if n != mate_name]})

    row = _mate_row(mate_name, feat, unit)
    moved = assembly.moved_since(asm, positions, unit)
    changed = []
    if new_value is not None:
        changed.append(f"value {row.get('value')} {row.get('unit', '')}".rstrip())
    if is_limit and new_limits:
        changed.append(f"limits {row.get('minimum')} to {row.get('maximum')} {row.get('unit', '')}".rstrip())
    if flip_dimension:
        changed.append("dimension flipped")
    if new["MateAlignment"] != current_align:
        changed.append(f"alignment {row.get('alignment')}")
    elif flip_alignment or align_key is not None:
        changed.append(f"alignment already {row.get('alignment')}")
    unit_str = units.label(unit)
    message = f"{mate_name}: " + ", ".join(changed)
    if moved:
        message += ". Moved: " + ", ".join(f"{m['name']} to {m['origin_in_assembly']}" for m in moved[:12]) + f" {unit_str}"
    else:
        message += ". No component moved"
    return ok(message, {"mate": row, "moved": moved, "length_unit": unit_str})


def _set_suppression(ctx, name, suppress: bool):
    asm = ctx.doc
    mate_name, feat = assembly.find_mate(asm, name)
    word = "suppressed" if suppress else "unsuppressed"
    if _suppressed(feat) == suppress:
        return ok(f"{mate_name} is already {word}", {"mate": mate_name, "suppressed": suppress, "changed": False,
                                                     "moved": []})
    positions = assembly.positions(asm)
    problems_before = assembly.mate_problems(asm)
    sw.mates.sw_set_suppressed(asm, feat, suppress)
    if _suppressed(feat) != suppress:
        raise ToolError(f"SolidWorks did not {'suppress' if suppress else 'unsuppress'} {mate_name}", Err.FEATURE,
                        {"mate": mate_name})
    broken = sorted(n for n in assembly.mate_problems(asm) if n not in problems_before)
    moved = assembly.moved_since(asm, positions, None)
    data = {"mate": mate_name, "suppressed": suppress, "changed": True, "moved": moved}
    message = f"{mate_name} is now {word}" + (f"; {len(moved)} component(s) moved" if moved else "")
    if broken:
        data["mate_errors"] = broken
        message += f". WARNING: these mates now have errors: {', '.join(broken)}"
    return ok(message, data)


@tool(
    group="assembly",
    description="Suppress a mate: it stays listed but holds nothing, so the components it joined can move. Reports the state read back.",
    params={"name": _MATE_NAME},
    required=["name"],
    needs="assembly",
)
def suppress_mate(ctx, name):
    return _set_suppression(ctx, name, True)


@tool(
    group="assembly",
    description="Turn a suppressed mate back on and rebuild. Reports the state read back, which components moved, and any mates that now have errors.",
    params={"name": _MATE_NAME},
    required=["name"],
    needs="assembly",
)
def unsuppress_mate(ctx, name):
    return _set_suppression(ctx, name, False)


@tool(
    group="assembly",
    description="Delete a mate by name (delete_feature also finds mates). Reports the mates that remain.",
    params={"name": _MATE_NAME},
    required=["name"],
    needs="assembly",
)
def delete_mate(ctx, name):
    message, data = assembly.delete_mate(ctx.doc, name)
    return ok(message, data)


@tool(
    group="assembly",
    description="Step a distance or angle mate through a list of values, optionally saving a picture (named <prefix>_<number>.png) and counting interferences at each step: this is how a mechanism is moved and shown. For each value the mate is set, the assembly rebuilt and the mate errors read. The call stops by itself before max_seconds runs out and returns the values still to do, to pass on as first_index; it also stops at the first value the mates do not allow. A PUSHED chain folds if the driven point moves more than one link pitch along its track in one move: set max_change (about half the pitch) and the way between values is walked in smaller moves.",
    params={"name": {"type": "string", "description": "Mate name from list_mates, e.g. 'Distance1'"},
            "values": {"type": "array", "items": {"type": "number"}, "minItems": 1,
                       "description": "Values to step through, in order (distance in unit, or degrees)"},
            "unit": {"type": "string", "description": "Unit of the values for a distance mate (mm, cm, m, inch or ft)"},
            "output_folder": {"type": "string", "description": "Folder on the computer for the pictures. Without it no pictures are saved."},
            "prefix": {"type": "string", "description": "File name prefix (default: the mate name)"},
            "first_index": {"type": "integer", "default": 1, "description": "Number of the first picture; use it to continue a series"},
            "check_interference": {"type": "boolean", "default": False, "description": "Count interferences at every step"},
            "max_seconds": {"type": "number", "default": 40, "description": "Stop before the call has run this long (5-50)"},
            "return_image": {"type": "string", "enum": ["last", "none"], "default": "last", "description": "Send the last picture back, or none"},
            "view": {"type": "string", "enum": ["current", "isometric", "trimetric", "dimetric", "front", "back", "left", "right", "top", "bottom"],
                     "default": "current", "description": "View for the pictures"},
            "direction": dict(_POINT, description="Instead of view: where the camera is, seen from the model"),
            "up": dict(_POINT, description="With direction: the model direction that points up in the picture"),
            "width": {"type": "integer", "description": "Picture width in pixels (64-4096)"},
            "height": {"type": "integer", "description": "Picture height in pixels (64-4096)"},
            "hide_annotations": {"type": "boolean", "default": False, "description": "Hide dimensions, planes, axes, origins and sketches in the pictures"},
            "white_background": {"type": "boolean", "default": False, "description": "Plain white background"},
            "zoom_to_fit": {"type": "boolean", "default": True, "description": "Zoom to fit once before the first picture (all pictures then have the same scale)"},
            "max_change": {"type": "number", "description": "Never change the mate by more than this at once: the way between two values is walked in smaller equal moves, without pictures. Use about half the link pitch for a chain that is pushed."}},
    required=["name", "values"],
    needs="assembly",
    writes=("output_folder",),
)
def step_mate(ctx, name, values, unit=None, output_folder=None, prefix=None, first_index=1, check_interference=False,
              max_seconds=40, return_image="last", view="current", direction=None, up=None, width=None, height=None,
              hide_annotations=False, white_background=False, zoom_to_fit=True, max_change=None):
    # its pictures share one framing (the first capture's zoom to fit); the screen is put back at the end
    held = None
    try:
        held = capture.view_state(ctx.doc)
    except Exception as e:
        logger.debug(f"Could not read the view: {e}")
    try:
        return _step_mate(ctx, name, values, unit, output_folder, prefix, first_index, check_interference,
                          max_seconds, return_image, view, direction, up, width, height, hide_annotations,
                          white_background, zoom_to_fit, max_change)
    finally:
        if held is not None:
            try:
                capture.restore_view(ctx.doc, held)
            except Exception as e:
                logger.debug(f"Could not put the view back: {e}")


def _step_mate(ctx, name, values, unit, output_folder, prefix, first_index, check_interference, max_seconds,
               return_image, view, direction, up, width, height, hide_annotations, white_background, zoom_to_fit,
               max_change):
    asm = ctx.doc

    def bad(message):
        return ToolError(message, Err.INPUT)

    if not isinstance(values, (list, tuple)) or not values:
        raise bad("values must be a list of numbers, e.g. [10, 20, 30]")
    if len(values) > MAX_STEP_VALUES:
        raise bad(f"At most {MAX_STEP_VALUES} values per call")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise bad("values must all be numbers")
    if isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float)) or not 5 <= max_seconds <= 50:
        raise bad("max_seconds must be from 5 to 50 (a call is cut off after 60 s)")
    if isinstance(first_index, bool) or not isinstance(first_index, int) or first_index < 0:
        raise bad("first_index must be a whole number, 0 or more")
    if return_image not in ("last", "none"):
        raise bad("return_image must be last or none")
    for label, flag in (("check_interference", check_interference), ("zoom_to_fit", zoom_to_fit)):
        if not isinstance(flag, bool):
            raise bad(f"{label} must be true or false")

    mate_name, feat = assembly.find_mate(asm, name)
    mate_type = int(sw.mates.sw_mate_data(feat).Type)
    if mate_type not in (5, 6):
        raise bad(f"{mate_name} is a {TYPE_NAMES.get(mate_type, mate_type)} mate: only a distance or angle mate can "
                  f"be stepped")
    if _suppressed(feat):
        raise ToolError(f"{mate_name} is suppressed. Unsuppress it first (unsuppress_mate).", Err.FEATURE)
    if mate_type == 5 and any(v < 0 for v in values):
        raise bad("A distance cannot be negative")
    if max_change is not None and (isinstance(max_change, bool) or not isinstance(max_change, (int, float))
                                   or not math.isfinite(max_change) or max_change <= 0):
        raise bad("max_change must be a number above 0")
    current = _mate_row(mate_name, feat, unit).get("value")
    if max_change is not None and current is None:
        raise ToolError(f"Could not read the current value of {mate_name}", Err.FEATURE)
    if max_change is not None:
        moves = sum(max(1, math.ceil(abs(b - a) / max_change - 1e-9)) for a, b in zip([current] + list(values[:-1]), values))
        if moves > 2000:
            raise bad(f"max_change is too small: that would be {moves} moves")

    folder = None
    if output_folder is not None:
        if not isinstance(output_folder, str) or not output_folder.strip():
            raise bad("output_folder must be a folder path")
        folder = os.path.abspath(output_folder.strip())
        if os.path.isfile(folder):
            raise bad(f"output_folder is a file: {folder}")
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            raise ToolError(f"Cannot create the folder {folder} ({e.strerror or e})", Err.SAVE)
    stem = re.sub(r'[^A-Za-z0-9_.-]+', "_", str(prefix if prefix else mate_name)).strip("_") or "step"

    # the picture settings are checked once, on a capture that changes nothing on disk
    wants_picture = folder is not None or return_image == "last"
    picture = dict(direction=direction, up=up, width=width, height=height, hide_annotations=hide_annotations,
                   white_background=white_background)
    if wants_picture:
        capture.capture(ctx.app, asm, view=view, zoom_to_fit=zoom_to_fit, max_size=256, return_image=False,
                        keep_view=True, **{k: v for k, v in picture.items() if k not in ("width", "height")})
        for label, size in (("width", width), ("height", height)):
            if size is not None and (isinstance(size, bool) or not isinstance(size, int) or not 64 <= size <= 4096):
                raise bad(f"{label} must be a whole number of pixels from 64 to 4096")
        if (width or height) and max(width or 0, height or 0) > 1280 and folder is None:
            raise bad("A picture larger than 1280 px is only written to a file: give output_folder")

    started = time.time()
    steps, files = [], []
    last_image, longest, longest_move, on_the_way, stopped, done = None, 0.0, 0.0, None, None, 0
    for index, target_value in enumerate(values):
        if index > 0 and time.time() - started + longest * 1.3 > max_seconds:
            stopped = "time"
            break
        step_start = time.time()
        row = {"value": target_value}
        targets = [target_value]                                   # the way there, in moves of at most max_change
        if max_change is not None and current is not None:
            parts = max(1, math.ceil(abs(target_value - current) / max_change - 1e-9))
            targets = [current + (target_value - current) * k / parts for k in range(1, parts)] + [target_value]
            if parts > 1:
                row["moves"] = parts
        result, out_of_time = None, False
        for target in targets:
            if result is not None and time.time() - started + longest_move * 1.5 > max_seconds:
                out_of_time = True
                break
            move_start = time.time()
            try:
                result = _edit_mate(ctx, asm, mate_name, target, unit=unit)
            except ToolError as e:
                result = e.result()
            longest_move = max(longest_move, time.time() - move_start)
            if not result.success:
                break
            current = target
        if out_of_time:                                            # on the way: it stays on the list
            stopped, on_the_way = "time", round(current, 6)
            break
        row["ok"] = bool(result.success)
        if not result.success:
            row["message"] = result.message
            if len(targets) > 1 and current is not None:
                row["reached"] = round(current, 6)
            steps.append(row)
            stopped = "refused"
            break
        if len(targets) == 1:
            row["moved"] = len(result.data.get("moved", []))
        row["mate_errors"] = sorted(assembly.mate_problems(asm))
        if check_interference:
            try:
                row["interferences"] = assembly.interferences(asm, False, None)[1]["count"]
            except ToolError:
                row["interferences"] = None
        if wants_picture:
            path = os.path.join(folder, f"{stem}_{first_index + index:03d}.png") if folder is not None else None
            try:
                shot = capture.capture(ctx.app, asm, view=view, zoom_to_fit=False, max_size=1280, output_path=path,
                                       return_image=(return_image == "last"), keep_view=True, **picture)
            except ToolError as e:
                shot = e.result()
            if shot.success:
                if path:
                    row["file"] = path
                    files.append(path)
                if shot.image:
                    last_image = shot.image
            else:
                row["picture_error"] = shot.message
        row["seconds"] = round(time.time() - step_start, 1)
        longest = max(longest, time.time() - step_start)
        steps.append(row)
        done += 1

    remaining = list(values[done + (1 if stopped == "refused" else 0):])
    unit_str = "deg" if mate_type == 6 else units.label(unit)
    data = {"mate": mate_name, "unit": unit_str, "steps_done": done, "steps_asked": len(values), "steps": steps,
            "files": files, "remaining_values": remaining, "seconds": round(time.time() - started, 1)}
    with_errors = [s["value"] for s in steps if s.get("mate_errors")]
    clashes = [s["value"] for s in steps if s.get("interferences")]
    message = f"{mate_name}: {done} of {len(values)} steps done in {data['seconds']} s"
    if stopped == "refused" and steps[-1].get("reached") is not None:
        message += f", now at {steps[-1]['reached']} {unit_str}"
    elif done:
        message += f", now at {steps[done - 1]['value']} {unit_str}"
    if files:
        message += f"; {len(files)} picture(s) in {folder}"
    if stopped == "refused":
        data["stopped_at"] = steps[-1]["value"]
        message += f". STOPPED at {steps[-1]['value']}: {steps[-1]['message']}"
    elif stopped == "time":
        if on_the_way is not None:
            data["now_at"] = on_the_way
            message += f". On the way to {remaining[0]} the mate is now at {on_the_way} {unit_str}"
        message += (f". Stopped for time: call again with values = {remaining[:8]}{'...' if len(remaining) > 8 else ''} "
                    f"and first_index = {first_index + done}")
    if with_errors:
        message += f". Mate errors at: {with_errors}"
    if clashes:
        message += f". Interference at: {clashes}"
    if done == 0 and on_the_way is None:
        raise ToolError(message, Err.FEATURE, data)
    return ok(message, data, image=last_image if done > 0 else None)
