"""Revolve, sweep and loft."""

import logging
import math

from ..core.api import Err, model, ok, selecting, sketch as sk, sw, tool, ToolError, units, value

logger = logging.getLogger(__name__)


def _volume_change(doc, volume_before, data) -> None:
    after = model.volume_mm3(doc)
    if volume_before is not None and after is not None:
        data["volume_change_mm3"] = round(after - volume_before, 3)


@tool(
    group="features",
    description="Revolve the most recent sketch about a centerline drawn in that sketch (draw_centerline). The profile must be closed and lie on one side of the centerline. cut=true makes a revolved cut.",
    params={'angle': {'type': 'number',
               'default': 360,
               'description': 'Angle in degrees (360 = full revolution)'},
     'cut': {'type': 'boolean',
             'default': False,
             'description': 'Remove material instead of adding it'},
     'flip': {'type': 'boolean',
              'default': False,
              'description': 'Revolve in the opposite direction (matters below 360)'},
     'axis': {'type': 'string',
              'description': "Name of the centerline to use (e.g. 'Line5') when the sketch has more "
                             'than one'},
     'merge': {'type': 'boolean',
               'default': True,
               'description': 'Merge new material into the body it touches'}},
    needs='part',
    clears_selection=True,
)
def revolve_sketch(ctx, angle=360, cut=False, flip=False, axis=None, merge=True):
    doc = ctx.doc
    try:
        angle = float(angle)
    except (TypeError, ValueError):
        raise ToolError(f"Bad angle '{angle}'", Err.INPUT)
    if not (0 < angle <= 360):
        raise ToolError("angle must be more than 0 and at most 360 degrees", Err.INPUT)

    selected, sketch_name, error = model.close_and_select_sketch(doc)
    if not selected:
        raise ToolError(f"Revolve failed: {error}", Err.FEATURE)
    defined_note = sk.require_defined(doc, sketch_name)

    centerlines = []
    try:
        sketch = value(doc.FeatureByName(sketch_name), "GetSpecificFeature2")
        for seg in value(sketch, "GetSketchSegments") or []:
            if int(value(seg, "GetType")) == 0 and bool(value(seg, "ConstructionGeometry")):
                centerlines.append(value(seg, "GetName"))
    except Exception as e:
        logger.debug(f"Could not read the sketch centerlines: {e}")
    if not centerlines:
        sw.common.sw_clear_selection(doc)
        raise ToolError(f"{sketch_name} has no centerline to revolve about. The axis must be a centerline "
                        f"(draw_centerline) drawn in the same sketch as the profile: delete {sketch_name} "
                        f"and draw it again with one.", Err.SKETCH, {"sketch_name": sketch_name})

    if axis:
        axis_name = model.match_name(axis, centerlines)
        if axis_name is None:
            sw.common.sw_clear_selection(doc)
            raise ToolError(f"'{axis}' is not a centerline of {sketch_name}. Centerlines: {', '.join(centerlines)}",
                            Err.INPUT)
    elif len(centerlines) == 1:
        axis_name = centerlines[0]
    else:
        sw.common.sw_clear_selection(doc)
        raise ToolError(f"{sketch_name} has {len(centerlines)} centerlines ({', '.join(centerlines)}). "
                        f"Say which one is the axis with axis='...'.", Err.INPUT, {"centerlines": centerlines})

    # the axis shapes the part, so it is defined like the profile
    try:
        axis_seg = next(seg for seg in value(sketch, "GetSketchSegments") or []
                        if value(seg, "GetName") == axis_name)
        axis_free = int(value(axis_seg, "Status")) == 2
    except Exception:
        axis_free = False
    if axis_free:
        sw.common.sw_clear_selection(doc)
        raise ToolError(f"Not built: the axis {axis_name} of {sketch_name} is not fully defined. edit_sketch('"
                        f"{sketch_name}'), then dimension or relate the centerline from the user's references (on the "
                        f"origin, vertical or horizontal, or a distance from an edge); fully_define_sketch only as a "
                        f"last resort (tell the user what it added); close_sketch, and try again.", Err.SKETCH,
                        {"sketch_name": sketch_name, "axis": axis_name})

    # select the axis (mark 16), then the sketch
    sw.common.sw_clear_selection(doc)
    if not sw.common.sw_select_named(doc, f"{axis_name}@{sketch_name}", "EXTSKETCHSEGMENT", 16, append=False):
        raise ToolError(f"Could not select centerline {axis_name}", Err.SELECTION)
    if not sw.common.sw_select_named(doc, sketch_name, "SKETCH", 0):
        sw.common.sw_clear_selection(doc)
        raise ToolError(f"Could not select {sketch_name}", Err.SELECTION)

    bodies_before, volume_before = model.solid_body_count(doc), model.volume_mm3(doc)
    cut, flip, merge = bool(cut), bool(flip), bool(merge)
    feat = sw.shapes.sw_revolve(doc, math.radians(angle), cut, flip, merge)
    sw.common.sw_clear_selection(doc)

    kind = "Revolved cut" if cut else "Revolve"
    if feat is None:
        raise ToolError(f"{kind} failed on {sketch_name} about {axis_name}. The profile must be closed, lie "
                        f"on one side of the centerline and not cross it"
                        + ("; a cut must also overlap the body." if cut else "."),
                        Err.FEATURE, {"sketch_name": sketch_name, "axis": axis_name})
    problem = model.feature_problem(doc, feat, volume_before)     # a revolve that changes nothing is removed
    if problem:
        sw.common.sw_clear_selection(doc)
        raise ToolError(f"Not created: the {kind.lower()} of {sketch_name} about {axis_name} is not possible - "
                        f"{problem}.", Err.FEATURE, {"sketch_name": sketch_name, "axis": axis_name})

    bodies_after = model.solid_body_count(doc)
    data = {"feature_name": feat.Name, "sketch_name": sketch_name, "axis": axis_name, "angle": angle,
            "cut": cut, "flip": flip, "solid_bodies": bodies_after}
    _volume_change(doc, volume_before, data)
    if cut:
        message = f"{feat.Name}: revolved cut of {sketch_name}, {angle:g} deg about {axis_name}"
    else:
        message = f"{feat.Name}: {sketch_name} revolved {angle:g} deg about {axis_name}"
    if not cut and merge and bodies_before >= 1 and bodies_after > bodies_before:
        data["separate_body"] = True
        message += (f". WARNING: this made a separate body ({bodies_after} solid bodies now) - "
                    f"the revolve does not touch the existing body.")
    return ok(message.rstrip(".") + defined_note, data)


@tool(
    group="features",
    description="Sweep a solid along a path sketch. Give a closed profile sketch and a path sketch (the path must start on the profile's plane), or give a diameter to sweep a round bar along the path without a profile sketch. By default the profile is the last sketch and the path the one before it. With cut=true the swept volume is removed from the body instead (refused when the part has no solid body or the cut removes nothing).",
    params={'profile': {'type': 'string',
                 'description': 'Name of the profile sketch (default: the last sketch)'},
     'path': {'type': 'string',
              'description': 'Name of the path sketch (default: the sketch before the profile; the '
                             'last sketch when diameter is given)'},
     'diameter': {'type': 'number',
                  'description': 'Sweep a circle of this diameter instead of a profile sketch'},
     'merge': {'type': 'boolean',
               'default': True,
               'description': 'Merge new material into the body it touches (boss only)'},
     'cut': {'type': 'boolean',
             'default': False,
             'description': 'Remove material (a swept cut) instead of adding it'},
     'unit': {'type': 'string', 'description': 'Unit for diameter (mm, cm, m, inch or ft)'}},
    needs='part',
    clears_selection=True,
)
def sweep(ctx, profile=None, path=None, diameter=None, unit=None, merge=True, cut=False):
    doc = ctx.doc
    if not isinstance(cut, bool):
        raise ToolError("cut must be true or false", Err.INPUT)
    unit_str = units.label(unit)
    circular = diameter is not None
    if circular:                                          # inputs first: a refusal leaves the model untouched
        diameter_m = units.size_m(diameter, unit, "diameter")
    model.close_open_sketch(doc)
    sketches = model.sketch_names(doc)

    if circular:
        if not sketches:
            raise ToolError("There is no sketch to use as the path", Err.SKETCH)
        path_name = model.match_name(path, sketches) if path else sketches[-1]
        if path_name is None:
            raise ToolError(f"No sketch named '{path}'. Sketches: {', '.join(sketches)}", Err.INPUT)
        profile_name = None
    else:
        diameter_m = 0.0
        if len(sketches) < 2:
            raise ToolError("A sweep needs two sketches (a profile and a path), or a path plus a diameter",
                            Err.SKETCH)
        profile_name = model.match_name(profile, sketches) if profile else sketches[-1]
        if profile_name is None:
            raise ToolError(f"No sketch named '{profile}'. Sketches: {', '.join(sketches)}", Err.INPUT)
        if path:
            path_name = model.match_name(path, sketches)
            if path_name is None:
                raise ToolError(f"No sketch named '{path}'. Sketches: {', '.join(sketches)}", Err.INPUT)
        else:
            path_name = [name for name in sketches if name != profile_name][-1]
        if path_name == profile_name:
            raise ToolError("The profile and the path must be different sketches", Err.INPUT)

    model.refuse_used_sketches(doc, [None if profile else profile_name, None if path else path_name])
    defined_note = "".join(sk.require_defined(doc, n) for n in (profile_name, path_name) if n)

    bodies_before, volume_before = model.solid_body_count(doc), model.volume_mm3(doc)
    before = [row[0] for row in model.tree_features(doc)]
    merge = bool(merge)
    if cut and not bodies_before:
        raise ToolError("A swept cut needs a solid body to cut; the part has none", Err.FEATURE)

    # profile: mark 1, path: mark 4
    sw.common.sw_clear_selection(doc)
    if profile_name and not sw.common.sw_select_named(doc, profile_name, "SKETCH", 1, append=False):
        raise ToolError(f"Could not select {profile_name}", Err.SELECTION)
    if not sw.common.sw_select_named(doc, path_name, "SKETCH", 4):
        sw.common.sw_clear_selection(doc)
        raise ToolError(f"Could not select {path_name}", Err.SELECTION)

    feat = sw.shapes.sw_sweep(doc, cut, merge, circular, diameter_m)
    sw.common.sw_clear_selection(doc)

    what = f"a {diameter}{unit_str} circle" if circular else profile_name
    kind = "swept cut" if cut else "sweep"
    if feat is None:
        model.remove_new_features(doc, before)
        raise ToolError(f"{kind.capitalize()} of {what} along {path_name} failed. The profile must be closed, "
                        f"the path must start on (or pass through) the profile's plane, and the sweep must "
                        f"not cut through itself" + ("; a cut must also overlap the body." if cut else "."),
                        Err.FEATURE, {"profile": profile_name, "path": path_name})
    problem = model.feature_problem(doc, feat, volume_before)    # a failed sweep leaves helper features behind
    if problem:
        model.remove_new_features(doc, before)
        raise ToolError(f"Not created: the {kind} of {what} along {path_name} is not possible - {problem}.",
                        Err.FEATURE)

    bodies_after = model.solid_body_count(doc)
    data = {"feature_name": feat.Name, "profile": profile_name, "path": path_name,
            "solid_bodies": bodies_after, "cut": cut}
    if circular:
        data["diameter"], data["unit"] = diameter, unit_str
    _volume_change(doc, volume_before, data)
    if cut:
        message = f"{feat.Name}: swept cut of {what} along {path_name}"
    else:
        message = f"{feat.Name}: {what} swept along {path_name}"
    if not cut and merge and bodies_before >= 1 and bodies_after > bodies_before:
        data["separate_body"] = True
        message += (f". WARNING: this made a separate body ({bodies_after} solid bodies now) - "
                    f"the sweep does not touch the existing body.")
    return ok(message.rstrip(".") + defined_note, data)


@tool(
    group="features",
    description="Loft a solid through two or more closed profile sketches on different planes, in the order given. By default the last two sketches are used. With cut=true the lofted volume is removed from the body instead (refused when the part has no solid body or the cut removes nothing).",
    params={'profiles': {'type': 'array',
                  'items': {'type': 'string'},
                  'description': "Sketch names in loft order, e.g. ['Sketch2', 'Sketch3']"},
     'merge': {'type': 'boolean',
               'default': True,
               'description': 'Merge new material into the body it touches (boss only)'},
     'cut': {'type': 'boolean',
             'default': False,
             'description': 'Remove material (a lofted cut) instead of adding it'}},
    needs='part',
    clears_selection=True,
)
def loft(ctx, profiles=None, merge=True, cut=False):
    doc = ctx.doc
    if not isinstance(cut, bool):
        raise ToolError("cut must be true or false", Err.INPUT)
    if isinstance(profiles, str):
        profiles = [profiles]
    if profiles:                                          # inputs first: a refusal leaves the model untouched
        seen = set()
        for raw in profiles:
            key = str(raw).strip().lower()
            if key in seen:
                raise ToolError(f"{raw} is listed twice", Err.INPUT)
            seen.add(key)
        if len(profiles) < 2:
            raise ToolError("A loft needs at least two profile sketches", Err.SKETCH)
    model.close_open_sketch(doc)
    sketches = model.sketch_names(doc)

    if profiles:
        names = []
        for raw in profiles:
            name = model.match_name(raw, sketches)
            if name is None:
                raise ToolError(f"No sketch named '{raw}'. Sketches: {', '.join(sketches)}", Err.INPUT)
            if name in names:
                raise ToolError(f"{name} is listed twice", Err.INPUT)
            names.append(name)
    else:
        names = sketches[-2:]
    if len(names) < 2:
        raise ToolError("A loft needs at least two profile sketches", Err.SKETCH)
    if not profiles:
        model.refuse_used_sketches(doc, names)
    defined_note = "".join(sk.require_defined(doc, n) for n in names)
    before = [row[0] for row in model.tree_features(doc)]

    bodies_before, volume_before = model.solid_body_count(doc), model.volume_mm3(doc)
    merge = bool(merge)
    if cut and not bodies_before:
        raise ToolError("A lofted cut needs a solid body to cut; the part has none", Err.FEATURE)

    sw.common.sw_clear_selection(doc)
    for name in names:                                    # profiles: mark 1, in order
        if not sw.common.sw_select_named(doc, name, "SKETCH", 1):
            sw.common.sw_clear_selection(doc)
            raise ToolError(f"Could not select {name}", Err.SELECTION)

    feat = sw.shapes.sw_loft(doc, cut, merge)
    sw.common.sw_clear_selection(doc)

    kind = "lofted cut" if cut else "loft"
    if feat is None:
        model.remove_new_features(doc, before)
        raise ToolError(f"{kind.capitalize()} through {', '.join(names)} failed. Each sketch must hold one "
                        f"closed profile, on a different plane from the others"
                        + ("; a cut must also overlap the body." if cut else "."),
                        Err.FEATURE, {"profiles": names})
    problem = model.feature_problem(doc, feat, volume_before)    # two sketches on one plane change nothing
    if problem:
        model.remove_new_features(doc, before)
        raise ToolError(f"Not created: the {kind} through {', '.join(names)} is not possible - {problem}. "
                        f"Each sketch must hold one closed profile, on a different plane from the others.",
                        Err.FEATURE)

    bodies_after = model.solid_body_count(doc)
    data = {"feature_name": feat.Name, "profiles": names, "solid_bodies": bodies_after, "cut": cut}
    _volume_change(doc, volume_before, data)
    if cut:
        message = f"{feat.Name}: lofted cut through {', '.join(names)}"
    else:
        message = f"{feat.Name}: loft through {', '.join(names)}"
    if not cut and merge and bodies_before >= 1 and bodies_after > bodies_before:
        data["separate_body"] = True
        message += (f". WARNING: this made a separate body ({bodies_after} solid bodies now) - "
                    f"the loft does not touch the existing body.")
    return ok(message.rstrip(".") + defined_note, data)


@tool(
    group="features",
    description="Hollow out the body, leaving walls of the given thickness. The openings are the faces given by open_faces (points on them) and / or open_normal; with neither, the faces currently selected are removed, and with nothing selected the body becomes a closed hollow.",
    params={'thickness': {'type': 'number', 'description': 'Wall thickness'},
     'open_faces': {'type': 'array',
                    'items': {'type': 'object',
                              'properties': {'x': {'type': 'number'},
                                             'y': {'type': 'number'},
                                             'z': {'type': 'number'}},
                              'required': ['x', 'y', 'z']},
                    'description': 'Model points on the faces to remove'},
     'open_normal': {'type': 'string',
                     'enum': ['+x', '-x', '+y', '-y', '+z', '-z'],
                     'description': 'Remove every flat face with this outward normal'},
     'outward': {'type': 'boolean',
                 'default': False,
                 'description': 'Add the wall outside the body instead of inside'},
     'unit': {'type': 'string', 'description': 'mm, cm, m, inch or ft'}},
    required=['thickness'],
    needs='part',
    clears_selection=True,
)
def shell(ctx, thickness, unit=None, open_faces=None, open_normal=None, outward=False):
    doc = ctx.doc
    try:
        if doc.SketchManager.ActiveSketch is not None:
            raise ToolError("A sketch is open. Close it first (close_sketch).", Err.SKETCH)
    except ToolError:
        raise
    except Exception:
        pass

    unit_str = units.label(unit)
    thickness_m = units.size_m(thickness, unit, "thickness")
    if model.solid_body_count(doc) < 1:
        raise ToolError("The model has no solid body to shell", Err.FEATURE)

    if open_faces or open_normal:
        selecting.pick_faces(doc, open_faces, None, open_normal, False, unit)

    # Only FACES count as openings. Anything else still selected (the feature made by the previous
    # call) was once reported as "1 face(s) removed" on a shell that was in fact closed.
    opened = sw.shapes.sw_keep_only_faces(doc)

    before = [row[0] for row in model.tree_features(doc)]
    volume_before = model.volume_mm3(doc)
    try:
        sw.shapes.sw_shell(doc, thickness_m, bool(outward))
    except Exception as e:
        sw.common.sw_clear_selection(doc)
        raise ToolError(f"Shell failed: {e}", Err.FEATURE)
    sw.common.sw_clear_selection(doc)

    new = [row for row in model.tree_features(doc) if row[0] not in before]
    if not new:
        raise ToolError(f"SolidWorks could not shell the body to {thickness}{unit_str}. "
                        f"The wall may be thicker than the part allows.", Err.FEATURE)
    feat_name, _, feat = new[-1]
    problems = [p for p in model.rebuild_errors(doc) if p["feature"] == feat_name]
    if problems:
        model.delete_feature(doc, feat)
        raise ToolError(f"Not created: a {thickness}{unit_str} shell fails to rebuild "
                        f"(error code {problems[0]['error_code']}). Try a thinner wall.", Err.FEATURE)

    data = {"feature_name": feat_name, "thickness": thickness, "unit": unit_str, "open_faces": opened,
            "outward": bool(outward), "solid_bodies": model.solid_body_count(doc)}
    _volume_change(doc, volume_before, data)
    if opened:
        message = f"{feat_name}: {thickness}{unit_str} wall, {opened} face(s) removed"
    else:
        message = (f"{feat_name}: {thickness}{unit_str} wall, no face removed - the body is now a closed "
                   f"hollow shell (give open_faces or open_normal for an opening)")
    return ok(message, data)
