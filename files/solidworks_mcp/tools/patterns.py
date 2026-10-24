"""Patterns and mirrors of features."""

import logging
import math

from ..core.api import Err, geometry, model, ok, plane_feature_name, sw, tool, ToolError, units, value

logger = logging.getLogger(__name__)

# A linear pattern runs along the normal of a standard plane: Right = +X, Top = +Y, Front = +Z
DIRECTION_PLANES = {"x": "Right Plane", "y": "Top Plane", "z": "Front Plane"}
# A model axis through the origin is the intersection of two standard planes
AXIS_PLANES = {"x": ("Front Plane", "Top Plane"), "y": ("Front Plane", "Right Plane"),
               "z": ("Top Plane", "Right Plane")}
# Feature types that are not solid features, so they cannot be patterned or mirrored
NON_BODY_FEATURE_TYPES = {
    "ProfileFeature", "RefPlane", "RefAxis", "OriginProfileFeature", "3DProfileFeature",
    "CommentsFolder", "FavoriteFolder", "HistoryFolder", "SelectionSetFolder", "SensorFolder",
    "DocsFolder", "DetailCabinet", "SurfaceBodyFolder", "SolidBodyFolder", "EnvFolder",
    "InkMarkupFolder", "EqnFolder", "MaterialFolder", "CoordSys", "RefPoint",
}


def _no_open_sketch(doc) -> None:
    try:
        if doc.SketchManager.ActiveSketch is not None:
            raise ToolError("A sketch is open. Close it first (close_sketch).", Err.SKETCH)
    except ToolError:
        raise
    except Exception:
        pass


def _body_feature_names(doc) -> list:
    """Names of the solid features in tree order."""
    names = []
    feat = value(doc, "FirstFeature")
    while feat is not None and len(names) < 5000:
        try:
            if value(feat, "GetTypeName2") not in NON_BODY_FEATURE_TYPES:
                names.append(feat.Name)
        except Exception:
            pass
        try:
            feat = value(feat, "GetNextFeature")
        except Exception:
            break
    return names


def _resolve_features(doc, features) -> list:
    """The `features` argument as existing feature names. None or empty: the last solid feature."""
    available = _body_feature_names(doc)
    if not available:
        raise ToolError("The model has no solid features yet", Err.INPUT)
    if not features:
        return [available[-1]]
    if isinstance(features, str):
        features = [features]
    lookup = {name.lower(): name for name in available}
    resolved = []
    for raw in features:
        name = lookup.get(str(raw).strip().lower())
        if name is None:
            raise ToolError(f"No feature named '{raw}'. Features in this model: {', '.join(available)}", Err.INPUT)
        if name not in resolved:
            resolved.append(name)
    return resolved


def _parse_direction(direction):
    """'+x', '-y', 'z' -> (axis, flip)"""
    text = str(direction or "").strip().lower()
    axis = text.lstrip("+-")
    if axis not in DIRECTION_PLANES:
        raise ToolError(f"Bad direction '{direction}'. Use +x, -x, +y, -y, +z or -z", Err.INPUT)
    return axis, text.startswith("-")


def _select_features(doc, names, mark) -> None:
    for name in names:
        if not sw.patterns.sw_select_feature(doc, name, mark):
            sw.common.sw_clear_selection(doc)
            raise ToolError(f"Could not select feature '{name}'", Err.SELECTION)


def _dead_feature(doc, feat, volume_before, data) -> bool:
    """Record the volume change; a pattern that changed nothing is deleted. True = it was dead."""
    after = model.volume_mm3(doc)
    if volume_before is None or after is None:
        return False
    change = round(after - volume_before, 3)
    data["volume_change_mm3"] = change
    if abs(change) < 1e-6:
        model.delete_feature(doc, feat)
        return True
    return False


@tool(
    group="features",
    description="Pattern one or more features along the model axes, in one direction or two (a grid). count includes the original. Warns if new instances fall outside the body.",
    params={'features': {'type': 'array',
                  'items': {'type': 'string'},
                  'description': "Feature names from list_features, e.g. ['Cut-Extrude1']. Default: "
                                 'the last solid feature.'},
     'direction': {'type': 'string',
                   'enum': ['+x', '-x', '+y', '-y', '+z', '-z'],
                   'description': 'Pattern direction in model axes'},
     'count': {'type': 'integer',
               'description': 'Instances along direction, including the original'},
     'spacing': {'type': 'number',
                 'description': 'Distance between neighbouring instances'},
     'direction2': {'type': 'string',
                    'enum': ['+x', '-x', '+y', '-y', '+z', '-z'],
                    'description': 'Optional second direction'},
     'count2': {'type': 'integer',
                'default': 1,
                'description': 'Instances along direction2, including the original'},
     'spacing2': {'type': 'number', 'description': 'Spacing along direction2 (needed with count2 above 1)'},
     'unit': {'type': 'string', 'description': 'mm, cm, m, inch or ft'}},
    required=['count', 'spacing', 'direction'],
    needs='part',
    clears_selection=True,
)
def linear_pattern(ctx, count, spacing, direction, features=None, unit=None, direction2=None, count2=1, spacing2=None):
    doc = ctx.doc
    unit_str = units.label(unit)
    axis1, flip1 = _parse_direction(direction)
    try:
        count, count2 = int(count), int(count2 or 1)
    except (TypeError, ValueError):
        raise ToolError("count and count2 must be whole numbers", Err.INPUT)

    use_dir2 = bool(direction2) and count2 > 1
    axis2, flip2 = None, False
    if use_dir2:
        axis2, flip2 = _parse_direction(direction2)
        if axis2 == axis1:
            raise ToolError("direction2 must be along a different axis than direction", Err.INPUT)
    else:
        count2 = 1
    if count < 1 or count2 < 1 or count * count2 < 2:
        raise ToolError("A pattern needs at least 2 instances in total (count includes the original)", Err.INPUT)

    spacing_m = ctx.to_m(spacing, unit)
    if use_dir2 and spacing2 is None:
        raise ToolError("Give spacing2: the user's spacing along direction2 (there is no default).", Err.INPUT)
    spacing2_m = ctx.to_m(spacing2, unit) if use_dir2 else spacing_m
    if spacing_m <= 0 or spacing2_m <= 0:
        raise ToolError("Spacing must be positive (use -x etc. for the direction)", Err.INPUT)
    units.check_size(spacing_m, "spacing")
    units.check_size(spacing2_m, "spacing2")

    _no_open_sketch(doc)                                  # after the inputs: a refusal leaves the model untouched
    names = _resolve_features(doc, features)
    volume_before = model.volume_mm3(doc)

    # select: features (mark 4), direction 1 (mark 1), direction 2 (mark 2)
    sw.common.sw_clear_selection(doc)
    _select_features(doc, names, 4)
    if not sw.common.sw_select_named(doc, DIRECTION_PLANES[axis1], "PLANE", 1):
        sw.common.sw_clear_selection(doc)
        raise ToolError(f"Could not select {DIRECTION_PLANES[axis1]} as direction", Err.SELECTION)
    if use_dir2 and not sw.common.sw_select_named(doc, DIRECTION_PLANES[axis2], "PLANE", 2):
        sw.common.sw_clear_selection(doc)
        raise ToolError(f"Could not select {DIRECTION_PLANES[axis2]} as direction 2", Err.SELECTION)

    feat, method_used = sw.patterns.sw_linear_pattern(doc, count, spacing_m, count2, spacing2_m, flip1, flip2)
    sw.common.sw_clear_selection(doc)
    if feat is None:
        raise ToolError(f"SolidWorks could not pattern {', '.join(names)}. Check that the instances stay on "
                        f"the body.", Err.FEATURE, {"features": names})

    data = {"feature_name": feat.Name, "features": names, "direction": ("-" if flip1 else "+") + axis1,
            "count": count, "spacing": spacing, "unit": unit_str, "instances": count * count2,
            "solid_bodies": model.solid_body_count(doc), "api_method": method_used}
    layout = f"{count} x {spacing}{unit_str} along {data['direction']}"
    if use_dir2:
        data["direction2"] = ("-" if flip2 else "+") + axis2
        data["count2"], data["spacing2"] = count2, spacing2
        layout += f", {count2} x {spacing2}{unit_str} along {data['direction2']}"
    if _dead_feature(doc, feat, volume_before, data):
        raise ToolError(f"Not created: patterning {', '.join(names)} {layout} would not change the model - "
                        f"the new instances fall outside the body. Check the direction and spacing.",
                        Err.FEATURE, {"features": names, "direction": data["direction"]})
    return ok(f"{feat.Name}: {', '.join(names)} patterned {layout} ({count * count2} instances)", data)


def _origin_axis(doc, axis: str):
    """The reference axis along a model axis through the origin. It is created once per part
    (named "Axis X" / "Axis Y" / "Axis Z") and reused. Returns (feature or None, name)."""
    wanted = f"Axis {axis.upper()}"
    try:
        existing = doc.FeatureByName(wanted)
    except Exception:
        existing = None
    if existing is not None:
        return existing, wanted

    def ref_axis_names():
        names = []
        feat = value(doc, "FirstFeature")
        while feat is not None and len(names) < 5000:
            try:
                if value(feat, "GetTypeName2") == "RefAxis":
                    names.append(feat.Name)
                feat = value(feat, "GetNextFeature")
            except Exception:
                break
        return names

    before = ref_axis_names()
    plane_a, plane_b = AXIS_PLANES[axis]
    if not sw.patterns.sw_insert_axis(doc, plane_a, plane_b):
        return None, wanted
    new = [n for n in ref_axis_names() if n not in before]
    if not new:
        return None, wanted
    feat = doc.FeatureByName(new[-1])
    try:
        feat.Name = wanted
    except Exception as e:
        logger.debug(f"Could not rename the axis: {e}")
        wanted = new[-1]
    return feat, wanted


@tool(
    group="features",
    description="Pattern one or more features around a model axis through the origin (x, y or z), or around the axis of a cylindrical face or circular edge (axis_point). count includes the original. Instances are spread evenly over 360 degrees by default; angle_between sets a fixed step instead.",
    params={'features': {'type': 'array',
                  'items': {'type': 'string'},
                  'description': 'Feature names from list_features. Default: the last solid feature.'},
     'count': {'type': 'integer', 'description': 'Instances including the original'},
     'axis': {'type': 'string',
              'enum': ['x', 'y', 'z'],
              'description': 'Model axis through the origin'},
     'axis_point': {'type': 'object',
                    'properties': {'x': {'type': 'number'},
                                   'y': {'type': 'number'},
                                   'z': {'type': 'number'}},
                    'required': ['x', 'y', 'z']},
     'angle_between': {'type': 'number',
                       'description': 'Degrees between neighbouring instances (default: evenly over '
                                      '360)'},
     'flip': {'type': 'boolean', 'default': False, 'description': 'Go round the other way'},
     'unit': {'type': 'string', 'description': 'Unit for axis_point (mm, cm, m, inch or ft)'}},
    required=['count'],
    needs='part',
    clears_selection=True,
)
def circular_pattern(ctx, count, axis=None, features=None, angle_between=None, flip=False, unit=None, axis_point=None):
    doc = ctx.doc
    _no_open_sketch(doc)
    unit_str = units.label(unit)
    try:
        count = int(count)
    except (TypeError, ValueError):
        raise ToolError("count must be a whole number", Err.INPUT)
    if count < 2:
        raise ToolError("count must be at least 2 (it includes the original)", Err.INPUT)

    equal_spacing = angle_between is None
    if equal_spacing:
        spacing = 2.0 * math.pi
    else:
        try:
            step = float(angle_between)
        except (TypeError, ValueError):
            raise ToolError(f"Bad angle_between '{angle_between}'", Err.INPUT)
        if step <= 0 or step * (count - 1) > 360:
            raise ToolError("angle_between must be positive and the pattern must fit in 360 degrees", Err.INPUT)
        spacing = math.radians(step)

    names = _resolve_features(doc, features)

    # the axis
    axis_face = None
    if axis_point is not None:
        try:
            p = geometry.points_to_m(axis_point, unit)[0]
        except (IndexError, TypeError, ValueError):
            raise ToolError("axis_point must be a point {x, y, z}", Err.INPUT)
        best = None
        for face in geometry.all_faces(doc):
            dist = geometry.distance_to(face, p)
            if dist <= geometry.PICK_TOLERANCE_M and value(value(face, "GetSurface"), "IsCylinder"):
                if best is None or dist < best[0]:
                    best = (dist, face)
        if best is None:
            for edge in geometry.all_edges(doc):
                dist = geometry.distance_to(edge, p)
                if dist <= geometry.PICK_TOLERANCE_M and value(value(edge, "GetCurve"), "IsCircle"):
                    if best is None or dist < best[0]:
                        best = (dist, edge)
        if best is None:
            raise ToolError(f"No cylindrical face or circular edge at {geometry.point_in_unit(p, unit)} "
                            f"{unit_str} to take the axis from", Err.SELECTION)
        axis_face = best[1]
        axis_name = f"round geometry at {geometry.point_in_unit(p, unit)} {unit_str}"
    elif axis is None:
        raise ToolError("Give axis (x, y or z: the model axis through the origin to pattern about) or axis_point "
                        "(round geometry to pattern about); there is no default.", Err.INPUT)
    else:
        key = str(axis).strip().lower().lstrip("+-")
        if key not in AXIS_PLANES:
            raise ToolError("axis must be x, y or z", Err.INPUT)
        axis_feature, axis_name = _origin_axis(doc, key)
        if axis_feature is None:
            raise ToolError(f"Could not create the reference axis along {key}", Err.FEATURE)

    volume_before = model.volume_mm3(doc)

    # select: features (mark 4), axis (mark 1)
    sw.common.sw_clear_selection(doc)
    _select_features(doc, names, 4)
    if axis_face is not None:
        selected = sw.patterns.sw_select_face(doc, axis_face, 1, True)
    else:
        selected = sw.common.sw_select_named(doc, axis_name, "AXIS", 1)
    if not selected:
        sw.common.sw_clear_selection(doc)
        raise ToolError(f"Could not select the axis ({axis_name})", Err.SELECTION)

    feat, method_used = sw.patterns.sw_circular_pattern(doc, count, spacing, bool(flip), equal_spacing)
    sw.common.sw_clear_selection(doc)
    layout = (f"{count} instances evenly over 360 deg" if equal_spacing
              else f"{count} instances {float(angle_between):g} deg apart")
    if feat is None:
        raise ToolError(f"SolidWorks could not pattern {', '.join(names)} about {axis_name}. Check that the "
                        f"instances stay on the body.", Err.FEATURE, {"features": names, "axis": axis_name})

    data = {"feature_name": feat.Name, "features": names, "axis": axis_name, "count": count,
            "equal_spacing": equal_spacing, "flip": bool(flip),
            "solid_bodies": model.solid_body_count(doc), "api_method": method_used}
    if not equal_spacing:
        data["angle_between"] = float(angle_between)
    if _dead_feature(doc, feat, volume_before, data):
        raise ToolError(f"Not created: patterning {', '.join(names)} about {axis_name} would not change the "
                        f"model - the new instances coincide with the original or fall outside the body. "
                        f"Check the axis.", Err.FEATURE, {"features": names, "axis": axis_name})
    return ok(f"{feat.Name}: {', '.join(names)} patterned about {axis_name}, {layout}", data)


@tool(
    group="features",
    description="Mirror one or more features about a plane (Front, Top, Right or any plane by name) or about a flat body face. Warns if the mirrored copy has no effect.",
    params={'features': {'type': 'array',
                  'items': {'type': 'string'},
                  'description': 'Feature names from list_features. Default: the last solid feature.'},
     'plane': {'type': 'string',
               'description': "Front, Top, Right, a plane name (e.g. 'Plane1'), or 'face' to mirror "
                              'about the flat face at face_x/face_y/face_z'},
     'face_x': {'type': 'number',
                'description': "X of a point on the mirror face (plane='face' only)"},
     'face_y': {'type': 'number',
                'description': "Y of a point on the mirror face (plane='face' only)"},
     'face_z': {'type': 'number',
                'description': "Z of a point on the mirror face (plane='face' only)"},
     'unit': {'type': 'string', 'description': 'mm, cm, m, inch or ft'}},
    required=['plane'],
    needs='part',
    clears_selection=True,
)
def mirror_feature(ctx, plane, features=None, unit=None, face_x=None, face_y=None, face_z=None):
    doc = ctx.doc
    _no_open_sketch(doc)
    unit_str = units.label(unit)
    names = _resolve_features(doc, features)
    volume_before = model.volume_mm3(doc)
    sw.common.sw_clear_selection(doc)

    # the mirror plane (mark 2)
    plane_text = (plane or "").strip()
    if not plane_text:
        raise ToolError("Give plane: what to mirror about (the user's symmetry: Front, Top, Right, a plane name, or "
                        "'face' with face_x, face_y, face_z); there is no default.", Err.INPUT)
    if plane_text.lower() == "face" and None in (face_x, face_y, face_z):
        raise ToolError("plane 'face' needs face_x, face_y and face_z: a point on the flat mirror face.", Err.INPUT)
    if plane_text.lower() == "face":
        x_m, y_m, z_m = (ctx.to_m(v, unit) for v in (face_x, face_y, face_z))
        on_point = [(d, f) for d, f in geometry.faces_at_point(doc, x_m, y_m, z_m)
                    if d <= geometry.FACE_POINT_TOLERANCE_M]
        if len(on_point) != 1:
            raise ToolError(f"Need a point inside exactly one face; found {len(on_point)} faces at "
                            f"({face_x}, {face_y}, {face_z}) {unit_str}", Err.SELECTION)
        face = on_point[0][1]
        if not value(value(face, "GetSurface"), "IsPlane"):
            raise ToolError("The mirror face is not flat", Err.SELECTION)
        if not sw.patterns.sw_select_face(doc, face, 2, False):
            raise ToolError("Could not select the mirror face", Err.SELECTION)
        plane_name = f"face at ({face_x}, {face_y}, {face_z}) {unit_str}"
    else:
        plane_name = plane_feature_name(plane_text)
        if not sw.common.sw_select_named(doc, plane_name, "PLANE", 2, append=False):
            raise ToolError(f"Could not select a plane named '{plane_name}'. Use Front, Top, Right, "
                            f"a plane name from the feature tree, or 'face'.", Err.SELECTION)

    _select_features(doc, names, 1)               # the features to mirror (mark 1)
    feat, method_used = sw.patterns.sw_mirror(doc)
    sw.common.sw_clear_selection(doc)
    if feat is None:
        raise ToolError(f"SolidWorks could not mirror {', '.join(names)} about {plane_name}. Check that the "
                        f"mirrored copy lands on the body.", Err.FEATURE, {"features": names, "plane": plane_name})

    data = {"feature_name": feat.Name, "features": names, "plane": plane_name,
            "solid_bodies": model.solid_body_count(doc), "api_method": method_used}
    if _dead_feature(doc, feat, volume_before, data):
        raise ToolError(f"Not created: mirroring {', '.join(names)} about {plane_name} would not change the "
                        f"model - the copy coincides with the original or falls outside the body.",
                        Err.FEATURE, {"features": names, "plane": plane_name})
    return ok(f"{feat.Name}: {', '.join(names)} mirrored about {plane_name}", data)
