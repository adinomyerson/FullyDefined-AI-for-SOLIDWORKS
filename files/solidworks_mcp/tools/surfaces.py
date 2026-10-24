"""Surfaces: extrude an open sketch into a surface."""

import logging

from ..core.api import Err, model, ok, sketch as sk, sw, tool, ToolError, units, value

logger = logging.getLogger(__name__)


@tool(
    group="features",
    description="Extrude the most recent sketch into a surface (Surface-Extrude). The sketch may be an open profile; construction geometry is ignored. Each sketch entity gives one face, so a sketch holding a single fit spline gives a single face. In an assembly the surface's faces are listed by list_component_faces (marked body = surface) and work in add_mate and measure_distance like any face.",
    params={'depth': {'type': 'number', 'description': 'Depth (total depth for mid_plane)'},
     'end_condition': {'type': 'string',
                       'enum': ['blind', 'mid_plane'],
                       'default': 'blind',
                       'description': 'blind = one side of the sketch plane; mid_plane = half the '
                                      'depth on each side'},
     'flip': {'type': 'boolean', 'default': False, 'description': 'Extrude in the opposite direction'},
     'unit': {'type': 'string', 'description': 'Unit of depth (mm, cm, m, inch or ft)'}},
    needs='part',
    clears_selection=True,
)
def extrude_surface(ctx, depth=None, end_condition="blind", flip=False, unit=None):
    doc = ctx.doc
    condition = str(end_condition or "blind").strip().lower()
    if condition not in ("blind", "mid_plane"):
        raise ToolError("end_condition must be blind or mid_plane", Err.INPUT)
    if not isinstance(flip, bool):
        raise ToolError("flip must be true or false", Err.INPUT)
    if depth is None:
        raise ToolError("Give depth: the user's surface depth (there is no default).", Err.INPUT)
    depth_m = units.size_m(depth, unit, "depth")
    unit_str = units.label(unit)

    selected, sketch_name, problem = model.close_and_select_sketch(doc)
    if not selected:
        sw.common.sw_clear_selection(doc)
        raise ToolError(f"No surface made: {problem}.", Err.FEATURE)
    defined_note = sk.require_defined(doc, sketch_name)

    before = sw.surfaces.sw_sheet_bodies(doc)
    names_before = [row[0] for row in model.tree_features(doc)]
    sw.surfaces.sw_extrude_surface(doc, depth_m, condition == "mid_plane", flip)
    sw.common.sw_clear_selection(doc)

    # SolidWorks returns nothing from this call even when it worked: look at the tree
    new = [row for row in model.tree_features(doc) if row[0] not in names_before and row[1] == "ExtruRefSurface"]
    after = sw.surfaces.sw_sheet_bodies(doc)
    if not new or len(after) <= len(before):
        for row in new:
            model.delete_feature(doc, row[2])
        raise ToolError(f"No surface made from '{sketch_name}'. The sketch needs at least one line, arc or "
                        f"spline that is not construction geometry.", Err.FEATURE, {"sketch_name": sketch_name})
    feature_name = new[-1][0]
    faces = 0
    try:
        faces = sum(len(list(value(body, "GetFaces") or [])) for body in after) \
            - sum(len(list(value(body, "GetFaces") or [])) for body in before)
    except Exception:
        pass
    data = {"feature_name": feature_name, "sketch_name": sketch_name, "depth": depth, "unit": unit_str,
            "end_condition": condition, "flip": flip, "faces": faces, "surface_bodies": len(after)}
    message = (f"{feature_name}: surface from '{sketch_name}', {depth}{unit_str} "
               f"({condition.replace('_', ' ')}), {faces} face(s)")
    if faces > 1:
        message += (". NOTE: one face per sketch entity. For a single face to mate along, "
                    "use fit_spline in the sketch first.")
    return ok(message.rstrip(".") + defined_note, data)
