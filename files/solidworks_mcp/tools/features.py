"""Boss and cut extrudes, fillets and chamfers."""

import logging
import math

from ..core.api import Err, geometry, model, ok, plane_feature_name, sketch as sk, sw, tool, ToolError, units, value

logger = logging.getLogger(__name__)

# end condition name -> swEndConditions_e
EXTRUDE_END_CONDITIONS = {"blind": 0, "through_all": 1, "mid_plane": 6, "up_to_next": 11}
CUT_END_CONDITIONS = {"blind": 0, "through_all": 1, "mid_plane": 6, "through_all_both": 9}


@tool(
    group="features",
    description="Extrude the most recent sketch as a boss (Boss-Extrude). New material merges into the body it touches; the result warns if it made a separate body instead, which usually means the wrong direction. A sketch that already belongs to a feature is not extruded again: draw a new sketch first.",
    params={'depth': {'type': 'number',
               'description': 'Extrusion depth (total depth for mid_plane; ignored for through_all / '
                              'up_to_next)'},
     'end_condition': {'type': 'string',
                       'enum': ['blind', 'through_all', 'mid_plane', 'up_to_next'],
                       'default': 'blind',
                       'description': 'blind = to depth; mid_plane = depth split equally both sides of '
                                      'the sketch; through_all / up_to_next need existing geometry in '
                                      'that direction'},
     'flip': {'type': 'boolean', 'default': False, 'description': 'Extrude in the opposite direction'},
     'merge': {'type': 'boolean',
               'default': True,
               'description': 'Merge into the existing body (false = keep as a separate body)'},
     'both_directions': {'type': 'boolean',
                         'default': False,
                         'description': "Legacy: same as end_condition='mid_plane'"},
     'unit': {'type': 'string', 'description': "mm, cm, m, inch or ft"}},
    needs='part',
    clears_selection=True,
)
def extrude_sketch(ctx, depth=None, both_directions=False, unit=None, flip=False, end_condition=None, merge=True):
    doc = ctx.doc
    unit_str = units.label(unit)

    # checked before anything is touched
    condition = (end_condition or ("mid_plane" if both_directions else "blind")).strip().lower()
    if condition not in EXTRUDE_END_CONDITIONS:
        raise ToolError(f"Unknown end_condition '{end_condition}'. Use one of: "
                        f"{', '.join(EXTRUDE_END_CONDITIONS)}", Err.INPUT)
    end_cond = EXTRUDE_END_CONDITIONS[condition]
    both_directions = condition == "mid_plane"
    flip, merge = bool(flip), bool(merge)
    uses_depth = condition in ("blind", "mid_plane")
    if uses_depth and depth is None:
        raise ToolError(f"Give depth: the user's extrusion depth (end_condition {condition} needs one; there is no "
                        f"default).", Err.INPUT)
    depth_m = units.size_m(depth, unit, "depth") if uses_depth else 0.01

    bodies_before = model.solid_body_count(doc)
    if not uses_depth and bodies_before < 1:             # "through all" with no body made a 1 m extrusion
        raise ToolError(f"end_condition '{condition}' needs an existing body to extrude up to, "
                        f"and this part has none. Use a blind depth.", Err.INPUT)

    selected, sketch_name, error = model.close_and_select_sketch(doc)
    if not selected:
        info = model.sketch_info(doc)
        raise ToolError(f"Extrusion failed: {error}. Sketches found: {info['sketch_count']} "
                        f"{info['sketch_names']}. Active sketch: {info['has_active_sketch']}. "
                        f"Try: create_sketch → draw geometry → extrude_sketch",
                        Err.FEATURE, {"diagnostics": info})
    defined_note = sk.require_defined(doc, sketch_name)

    volume_before = model.volume_mm3(doc)
    feat, method_used = sw.features.sw_extrude(doc, flip, end_cond, depth_m, merge)

    # A boss that does not touch the existing body cannot merge into it and becomes a separate
    # body. If so, try the other direction.
    auto_flipped = False
    if feat is not None and merge and condition != "mid_plane" \
            and bodies_before >= 1 and model.solid_body_count(doc) > bodies_before:
        original = feat.Name
        if model.delete_feature(doc, feat):
            reselected, _, _ = model.close_and_select_sketch(doc)
            retry, retry_method = sw.features.sw_extrude(doc, not flip, end_cond, depth_m, merge) if reselected else (None, "")
            if retry is not None and model.solid_body_count(doc) <= bodies_before:
                feat, method_used, flip, auto_flipped = retry, retry_method, not flip, True
            else:                                          # the other direction is no better: restore
                if retry is not None:
                    model.delete_feature(doc, retry)
                reselected, _, _ = model.close_and_select_sketch(doc)
                feat, method_used = sw.features.sw_extrude(doc, flip, end_cond, depth_m, merge) if reselected else (None, "")
                logger.info(f"Extrude {original}: separate body in both directions")

    if feat is None:
        sw.common.sw_clear_selection(doc)
        info = model.sketch_info(doc)
        hint = ("Ensure sketch has a closed profile (circle, rectangle, etc)." if uses_depth else
                f"Ensure the profile is closed and that there is a body to extrude "
                f"'{condition.replace('_', ' ')}' to in that direction (try flip={not flip}).")
        raise ToolError(f"Extrusion failed on sketch '{sketch_name}'. {hint} Sketches in model: "
                        f"{info['sketch_names']}", Err.FEATURE, {"sketch_name": sketch_name, "diagnostics": info})

    problem = model.feature_problem(doc, feat, volume_before)
    if problem is None and not uses_depth and merge and model.solid_body_count(doc) > bodies_before:
        model.delete_feature(doc, feat)           # "through all" / "up to next" that reaches nothing
        problem = "there is no body to extrude up to in either direction"
    if problem:
        sw.common.sw_clear_selection(doc)
        raise ToolError(f"Not created: extruding '{sketch_name}' ({condition.replace('_', ' ')}) is not "
                        f"possible - {problem}.", Err.FEATURE, {"sketch_name": sketch_name})

    direction = "both directions (mid-plane)" if condition == "mid_plane" else \
                ("flipped direction" if flip else "one direction")
    size = f"{depth}{unit_str}" if uses_depth else condition.replace("_", " ")
    bodies_after = model.solid_body_count(doc)
    data = {"depth": depth, "unit": unit_str, "both_directions": both_directions, "end_condition": condition,
            "flip": flip, "merge": merge, "feature_name": feat.Name, "sketch_name": sketch_name,
            "solid_bodies": bodies_after, "api_method": method_used}
    message = f"Extruded {size} ({direction}) [{method_used}]"
    if auto_flipped:
        data["auto_flipped"] = True
        message += (". NOTE: the direction was flipped automatically, because the direction asked for did "
                    "not reach the existing body and would have made a separate body (use merge=false "
                    "to keep it separate).")
    elif merge and bodies_before >= 1 and bodies_after > bodies_before:
        data["separate_body"] = True
        message += (f". WARNING: this made a separate body ({bodies_after} solid bodies now) - the extrude "
                    f"does not touch the existing body in either direction. Use a larger depth or "
                    f"end_condition='up_to_next'.")
    return ok(message.rstrip(".") + defined_note, data)


@tool(
    group="features",
    description="Cut the most recent sketch through the body to remove material. Reports the volume removed. If the requested direction removes nothing (for example a sketch lying beside the body), the other direction is used and the result says so. A sketch that already belongs to a feature is not cut again: draw a new sketch first.",
    params={'depth': {'type': 'number',
               'description': 'Cut depth (total depth for mid_plane; ignored for through_all)'},
     'end_condition': {'type': 'string',
                       'enum': ['blind', 'through_all', 'mid_plane', 'through_all_both'],
                       'default': 'blind',
                       'description': 'blind = to depth; through_all = through the whole body in one '
                                      'direction; through_all_both = both directions; mid_plane = '
                                      'depth split equally both sides of the sketch'},
     'flip': {'type': 'boolean', 'default': False, 'description': 'Cut in the opposite direction'},
     'through_all': {'type': 'boolean',
                     'default': False,
                     'description': "Legacy: same as end_condition='through_all'"},
     'both_directions': {'type': 'boolean',
                         'default': False,
                         'description': 'Legacy: with through_all = through_all_both, otherwise '
                                        'mid_plane'},
     'unit': {'type': 'string', 'description': "mm, cm, m, inch or ft"}},
    needs='part',
    clears_selection=True,
)
def cut_extrude(ctx, depth=None, through_all=False, both_directions=False, unit=None, flip=False, end_condition=None):
    doc = ctx.doc
    unit_str = units.label(unit)

    # "through all - both" used end condition 2 once, which is "up to next"
    if end_condition:
        condition = end_condition.strip().lower()
    elif through_all:
        condition = "through_all_both" if both_directions else "through_all"
    else:
        condition = "mid_plane" if both_directions else "blind"
    if condition not in CUT_END_CONDITIONS:
        raise ToolError(f"Unknown end_condition '{end_condition}'. Use one of: "
                        f"{', '.join(CUT_END_CONDITIONS)}", Err.INPUT)
    end_cond = CUT_END_CONDITIONS[condition]
    through_all = condition in ("through_all", "through_all_both")
    if not through_all and depth is None:
        raise ToolError(f"Give depth: the user's cut depth (end_condition {condition} needs one; there is no "
                        f"default), or end_condition through_all.", Err.INPUT)
    depth_m = 0.0 if through_all else units.size_m(depth, unit, "depth")

    if model.solid_body_count(doc) < 1:
        raise ToolError("There is no solid body to cut. Make a body first (extrude_sketch).", Err.FEATURE)

    selected, sketch_name, error = model.close_and_select_sketch(doc)
    if not selected:
        info = model.sketch_info(doc)
        raise ToolError(f"Cut failed: {error}. Sketches: {info['sketch_names']}.", Err.FEATURE,
                        {"diagnostics": info})
    defined_note = sk.require_defined(doc, sketch_name)

    # "Through all - both" needs two directions, each through all
    # (end condition 9 with a single direction only cuts one side)
    single = condition != "through_all_both"
    end_cond_1, end_cond_2 = (end_cond, 0) if single else (1, 1)
    cut_depth = 0 if through_all else depth_m
    flip = bool(flip)
    volume_before = model.volume_mm3(doc)

    def try_cut(direction_flip):
        """One attempt in one direction. Returns (feature or None, API method name)."""
        made, method = sw.features.sw_cut(doc, single, direction_flip, end_cond_1, end_cond_2, cut_depth)
        # a cut that removes nothing or does not rebuild is taken out again
        if made is not None and model.feature_problem(doc, made, volume_before) is not None:
            made = None
        return made, method

    feat, method_used = try_cut(flip)

    # from a plane beside the body the first direction may point away from it: try the other one
    auto_flipped = False
    if feat is None and single and condition != "mid_plane":
        reselected, _, _ = model.close_and_select_sketch(doc)
        if reselected:
            feat, method_used = try_cut(not flip)
            if feat is not None:
                flip, auto_flipped = not flip, True

    if feat is None:
        sw.common.sw_clear_selection(doc)
        raise ToolError(f"Cut failed on sketch '{sketch_name}' ({condition.replace('_', ' ')}): it removes "
                        f"nothing in either direction. Check that the profile is closed, that it lies "
                        f"over the body, and that the depth reaches the body.", Err.FEATURE,
                        {"sketch_name": sketch_name, "end_condition": condition, "flip": flip})

    cut_type = condition.replace("_", " ") if condition != "blind" else f"{depth}{unit_str}"
    if condition == "mid_plane":
        cut_type = f"{depth}{unit_str} mid plane"
    if flip:
        cut_type += ", flipped"
    data = {"depth": depth, "through_all": through_all, "end_condition": condition, "flip": flip,
            "feature_name": feat.Name, "sketch_name": sketch_name,
            "solid_bodies": model.solid_body_count(doc), "api_method": method_used}
    volume_after = model.volume_mm3(doc)
    if volume_before is not None and volume_after is not None:
        data["volume_removed_mm3"] = round(volume_before - volume_after, 3)
    message = f"Cut extrude: {cut_type} [{method_used}]"
    if auto_flipped:
        data["auto_flipped"] = True
        message += (". NOTE: the direction was flipped automatically, because the direction asked for "
                    "points away from the body and removes nothing.")
    return ok(message.rstrip(".") + defined_note, data)


@tool(
    group="features",
    description="Add a constant-radius fillet to the selected edges (or to all edges of the selected faces). Select first with select_edges / select_faces. A radius too large for the edges is refused.",
    params={'radius': {'type': 'number', 'description': "Radius of the fillet"},
     'unit': {'type': 'string', 'description': "mm, cm, m, inch or ft"}},
    required=['radius'],
    needs='part',
    clears_selection=True,
)
def fillet_edges(ctx, radius, unit=None):
    doc = ctx.doc
    radius_m = units.size_m(radius, unit, "radius")
    unit_str = units.label(unit)
    # Edges (or faces) must be selected first. Any selection used to count, e.g. a feature still
    # selected from the previous call.
    selected = model.selected_edge_or_face_count(doc)
    if selected == 0:
        raise ToolError("Fillet failed - no edge or face is selected. Select the edges first (select_edges)",
                        Err.SELECTION)
    volume_before = model.volume_mm3(doc)
    feat, method_used = sw.features.sw_fillet(doc, radius_m)
    if feat is None:
        raise ToolError("Fillet failed - SolidWorks could not fillet the selected edges "
                        "(is the radius too large for them?)", Err.FEATURE)

    problem = model.feature_problem(doc, feat, volume_before)   # a dead feature is removed again
    if problem:
        sw.common.sw_clear_selection(doc)
        raise ToolError(f"Not created: a {radius}{unit_str} fillet on the selected edges is not possible - "
                        f"{problem}. Is the radius too large for them?", Err.FEATURE, {"selected_items": selected})
    data = {"radius": radius, "unit": unit_str, "feature_name": feat.Name, "selected_items": selected}
    message = f"Fillet: r={radius}{unit_str} [{method_used}]"
    message += model.big_change_warning(doc, volume_before, data, "radius")
    return ok(message, data)


@tool(
    group="features",
    description="Add an angle-distance chamfer to the selected edges. Select first with select_edges. A distance too large for the edges is refused.",
    params={'distance': {'type': 'number', 'description': "Distance of the chamfer"},
     'angle': {'type': 'number', 'default': 45, 'description': "Angle of the chamfer, in degrees"},
     'flip': {'type': 'boolean',
              'default': False,
              'description': 'For angles other than 45: measure the distance on the other face'},
     'unit': {'type': 'string', 'description': "mm, cm, m, inch or ft"}},
    required=['distance'],
    needs='part',
    clears_selection=True,
)
def chamfer_edges(ctx, distance, angle=45, unit=None, flip=False):
    doc = ctx.doc
    unit_str = units.label(unit)
    dist_m = units.size_m(distance, unit, "distance")
    if not isinstance(angle, (int, float)) or not (0 < angle < 90):
        raise ToolError("angle must be more than 0 and less than 90 degrees", Err.INPUT)
    selected = model.selected_edge_or_face_count(doc)
    if selected == 0:
        raise ToolError("Chamfer failed - no edge or face is selected. Select the edges first (select_edges)",
                        Err.SELECTION)
    volume_before = model.volume_mm3(doc)
    feat = sw.features.sw_chamfer(doc, 4 | (1 if flip else 0), dist_m, math.radians(angle))   # 4 tangent propagation, 1 flip
    if feat is None:
        raise ToolError("Chamfer failed - SolidWorks could not chamfer the selected edges "
                        "(is the distance too large for them?)", Err.FEATURE, {"selected_items": selected})
    problem = model.feature_problem(doc, feat, volume_before)
    if problem:
        sw.common.sw_clear_selection(doc)
        raise ToolError(f"Not created: a {distance}{unit_str} chamfer on the selected edges is not possible - "
                        f"{problem}. Is the distance too large for them?", Err.FEATURE, {"selected_items": selected})
    data = {"distance": distance, "angle": angle, "unit": unit_str, "flip": bool(flip),
            "feature_name": feat.Name, "selected_items": selected}
    message = f"Chamfer: {distance}{unit_str} x {angle}°"
    message += model.big_change_warning(doc, volume_before, data, "distance")
    return ok(message, data)
