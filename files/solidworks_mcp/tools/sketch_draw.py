"""Drawing geometry in the open sketch, at exactly the coordinates given."""

import logging
import math

from ..core.api import doc_type, Err, ok, sketch as sk, sw, tool, ToolError, units, value

logger = logging.getLogger(__name__)


def _draw(ctx):
    """The document and open sketch, and the number of entities before drawing."""
    doc = ctx.doc
    sketch = sk.active_sketch(doc)
    return doc, sketch


TEXT_GAP_M = 0.005                      # a size dimension's text sits this far outside the shape
ORIGIN_NOTE = "; tied to the origin"


def _size_later(ctx, doc, sketch, items, data) -> str:
    """In a part, the shape's own sizes become dimensions when the sketch is closed or fully defined, where they are
    still free (the user's own dimensions and relations win): items are (entity, text x, text y) in metres. Returns
    the note for the message."""
    if doc_type(doc) != 1:
        return ""
    for entity, x_m, y_m in items:
        sk.pend_size(doc, sketch, ctx.app, entity, x_m, y_m)
    data["size_on_close"] = True
    return "; its size is dimensioned when the sketch is closed, unless set first"


@tool(
    group="sketch",
    description="Add a line to the open sketch at exactly the coordinates given: no snapping and no automatic relations. Lines that share an endpoint are joined. In a part, a line that touches nothing when the sketch is closed gets its length as a dimension (a line in a chain does not). Returns the id of the new entity.",
    params={'x1': {'type': 'number', 'description': "X of the start point"},
     'y1': {'type': 'number', 'description': "Y of the start point"},
     'x2': {'type': 'number', 'description': "X of the end point"},
     'y2': {'type': 'number', 'description': "Y of the end point"},
     'unit': {'type': 'string', 'description': 'mm, cm, m, inch or ft'}},
    required=['x1', 'y1', 'x2', 'y2'],
    needs='any',
)
def draw_line(ctx, x1, y1, x2, y2, unit=None):
    doc, sketch = _draw(ctx)
    x1_m, y1_m, x2_m, y2_m = (ctx.to_m(v, unit) for v in (x1, y1, x2, y2))
    length_m = math.hypot(x2_m - x1_m, y2_m - y1_m)
    sk.check_input(points=[(x1_m, y1_m), (x2_m, y2_m)], sizes=[("The length of the line", length_m)])
    before = sk.count(doc, sketch)
    with sk.exact_input(doc):
        line = sw.sketch_draw.sw_line(doc, x1_m, y1_m, x2_m, y2_m)
    if line is None:
        raise ToolError("SolidWorks could not draw that line", Err.SKETCH)
    length = ctx.from_m(length_m, unit)
    unit_str = units.label(unit)
    data = {"length": length, "unit": unit_str}
    note = sk.new_entity_ids(doc, sketch, before, data, created=[line])
    if sk.tie_to_origin(doc, sketch, [line], drawn=[(x1_m, y1_m), (x2_m, y2_m)]):
        data["tied_to_origin"] = True
        note += ORIGIN_NOTE
    # a lone line (a path, a slot's centre line) gets its length on closing; one in a chain does not
    _lone_length_later(ctx, doc, sketch, line, (x1_m, y1_m), (x2_m, y2_m))
    return ok(f"Line: {length:.2f}{unit_str}{note}", data)


def _lone_length_later(ctx, doc, sketch, line, a, b) -> None:
    """In a part, a line's length is dimensioned on closing if it still touches nothing then. Its text sits off the
    line's middle along the normal: a text off to the side of a slanted line along x and y (on its extension at 45
    degrees) could make a horizontal or vertical distance instead of the length."""
    if doc_type(doc) != 1:
        return
    length = math.dist(a, b)
    nx, ny = (-(b[1] - a[1]) / length, (b[0] - a[0]) / length) if length else (0.0, 1.0)
    sk.pend_size(doc, sketch, ctx.app, line, (a[0] + b[0]) / 2 + nx * TEXT_GAP_M, (a[1] + b[1]) / 2 + ny * TEXT_GAP_M,
                 lone=True)


@tool(
    group="sketch",
    description="Add a chain of lines through the points given, in one call (instead of one draw_line per side): each point joins the next, and closed: true also joins the last to the first, so an outline such as an L profile is one call. Exact coordinates, no snapping; lines along x or y get horizontal / vertical relations. In a part, a two-point polyline (one line) that touches nothing when the sketch is closed gets its length as a dimension, as with draw_line. Returns the new entity ids in order.",
    params={'points': {'type': 'array', 'minItems': 2,
                       'items': {'type': 'object', 'properties': {'x': {'type': 'number'}, 'y': {'type': 'number'}},
                                 'required': ['x', 'y']},
                       'description': "The corners in order, e.g. [{'x': 0, 'y': 0}, {'x': 80, 'y': 0}, {'x': 80, 'y': 6}]"},
            'closed': {'type': 'boolean', 'default': False,
                       'description': "true joins the last point back to the first (a closed outline)"},
            'unit': {'type': 'string', 'description': 'mm, cm, m, inch or ft'}},
    required=['points'],
    needs='any',
)
def draw_polyline(ctx, points, closed=False, unit=None):
    doc, sketch = _draw(ctx)
    if not isinstance(closed, bool):
        raise ToolError("closed must be true or false", Err.INPUT)
    try:
        xy = [(ctx.to_m(float(p["x"]), unit), ctx.to_m(float(p["y"]), unit)) for p in points]
    except (TypeError, KeyError, ValueError):
        raise ToolError("points must be a list of {x, y} numbers", Err.INPUT)
    if closed and len(xy) > 2 and xy[0] == xy[-1]:
        xy = xy[:-1]                                    # the first point repeated at the end: closed already says so
    if len(xy) < (3 if closed else 2):
        raise ToolError(f"A {'closed outline' if closed else 'polyline'} needs at least {3 if closed else 2} points",
                        Err.INPUT)
    pairs = list(zip(xy, xy[1:] + (xy[:1] if closed else [])))
    sk.check_input(points=xy, sizes=[(f"Line {i} (from point {i} to {i % len(xy) + 1})", math.dist(a, b))
                                     for i, (a, b) in enumerate(pairs, 1)])
    before = sk.count(doc, sketch)
    lines = []
    with sk.exact_input(doc):
        for a, b in pairs:
            line = sw.sketch_draw.sw_line(doc, a[0], a[1], b[0], b[1])
            if line is None:
                break
            lines.append(line)
    if len(lines) != len(pairs):
        sw.sketch_draw.sw_delete_entities(doc, lines)
        sw.common.sw_clear_selection(doc)
        sk.segments(doc, sketch)
        raise ToolError("SolidWorks could not draw that polyline; nothing was added", Err.SKETCH)
    try:
        relations = sk.add_horizontal_vertical(doc, lines)
    except Exception as e:
        relations = 0
        logger.debug(f"Polyline relations failed: {e}")
    unit_str = units.label(unit)
    length = sum(ctx.from_m(math.dist(a, b), unit) for a, b in pairs)
    data = {"lines": len(lines), "closed": closed, "length": round(length, 6), "unit": unit_str,
            "relations_added": relations}
    note = sk.new_entity_ids(doc, sketch, before, data, created=lines)
    if sk.tie_to_origin(doc, sketch, lines, drawn=xy):
        data["tied_to_origin"] = True
        note += ORIGIN_NOTE
    if len(lines) == 1:                                 # two points: one line, as draw_line makes it
        _lone_length_later(ctx, doc, sketch, lines[0], *pairs[0])
    return ok(f"{'Closed outline' if closed else 'Polyline'}: {len(lines)} lines, {length:.2f}{unit_str} long{note}",
              data)


@tool(
    group="sketch",
    description="Add a circle to the open sketch, given its centre (x, y) and radius. In a part its diameter becomes a dimension when the sketch is closed, unless set first.",
    params={'x': {'type': 'number', 'description': "X of the centre"},
     'y': {'type': 'number', 'description': "Y of the centre"},
     'radius': {'type': 'number', 'description': "Circle radius"},
     'unit': {'type': 'string', 'description': 'mm, cm, m, inch or ft'}},
    required=['x', 'y', 'radius'],
    needs='any',
)
def draw_circle(ctx, x, y, radius, unit=None):
    doc, sketch = _draw(ctx)
    x_m, y_m, r_m = (ctx.to_m(v, unit) for v in (x, y, radius))
    sk.check_input(points=[(x_m - r_m, y_m - r_m), (x_m + r_m, y_m + r_m)], sizes=[("The radius", r_m)])
    before = sk.count(doc, sketch)
    with sk.exact_input(doc):
        circle = sw.sketch_draw.sw_circle(doc, x_m, y_m, r_m)
    if circle is None:
        raise ToolError("SolidWorks could not draw that circle", Err.SKETCH)
    unit_str = units.label(unit)
    data = {"radius": radius, "unit": unit_str, "center_x": x, "center_y": y}
    note = sk.new_entity_ids(doc, sketch, before, data, created=[circle])
    if sk.tie_to_origin(doc, sketch, [circle], drawn=sk.round_points(x_m, y_m, r_m)):
        data["tied_to_origin"] = True
        note += ORIGIN_NOTE
    note += _size_later(ctx, doc, sketch, [(circle, x_m + r_m + TEXT_GAP_M, y_m + r_m + TEXT_GAP_M)], data)
    return ok(f"Circle: r={radius}{unit_str}{note}", data)


@tool(
    group="sketch",
    description="Add a rectangle in the open sketch between opposite corners (x1, y1) and (x2, y2). Horizontal and vertical relations are normally added to its sides; list_sketch_entities shows them. In a part its width and height become dimensions when the sketch is closed, unless set first.",
    params={'x1': {'type': 'number', 'description': "X of the first corner"},
     'y1': {'type': 'number', 'description': "Y of the first corner"},
     'x2': {'type': 'number', 'description': "X of the opposite corner"},
     'y2': {'type': 'number', 'description': "Y of the opposite corner"},
     'unit': {'type': 'string', 'description': 'mm, cm, m, inch or ft'}},
    required=['x1', 'y1', 'x2', 'y2'],
    needs='any',
)
def draw_rectangle(ctx, x1, y1, x2, y2, unit=None):
    doc, sketch = _draw(ctx)
    x1_m, y1_m, x2_m, y2_m = (ctx.to_m(v, unit) for v in (x1, y1, x2, y2))
    sk.check_input(points=[(x1_m, y1_m), (x2_m, y2_m)],
                   sizes=[("The width of the rectangle", abs(x2_m - x1_m)),
                          ("The height of the rectangle", abs(y2_m - y1_m))])
    before = sk.count(doc, sketch)
    with sk.exact_input(doc):
        rect = sw.sketch_draw.sw_rectangle(doc, x1_m, y1_m, x2_m, y2_m)
    if rect is None:
        raise ToolError("SolidWorks could not draw that rectangle", Err.SKETCH)
    try:
        sk.add_horizontal_vertical(doc, rect)              # exact input adds no relations: keep the sides square
    except Exception as e:
        logger.debug(f"Rectangle relations failed: {e}")
    width, height = abs(x2 - x1), abs(y2 - y1)
    unit_str = units.label(unit)
    data = {"width": width, "height": height, "unit": unit_str}
    note = sk.new_entity_ids(doc, sketch, before, data, created=list(rect))
    sides = {}
    for side in list(rect):
        try:
            a, b = value(side, "GetStartPoint2"), value(side, "GetEndPoint2")
            sides.setdefault("width" if abs(a.Y - b.Y) < 1e-12 else "height", side)
        except Exception:
            continue
    top, right = max(y1_m, y2_m) + TEXT_GAP_M, max(x1_m, x2_m) + TEXT_GAP_M
    items = [(sides[k], x, y) for k, x, y in (("width", (x1_m + x2_m) / 2, top), ("height", right, (y1_m + y2_m) / 2))
             if k in sides]
    centred = abs(x1_m + x2_m) < 1e-12 and abs(y1_m + y2_m) < 1e-12
    tied = sk.tie_to_origin(doc, sketch, [], centre=((x1_m, y1_m), (x2_m, y2_m))) if centred else \
        sk.tie_to_origin(doc, sketch, list(rect), drawn=[(x1_m, y1_m), (x2_m, y2_m), (x1_m, y2_m), (x2_m, y1_m)])
    if tied:
        data["tied_to_origin"] = "centre" if centred else "corner"
        note += ORIGIN_NOTE + (" (its centre, by a construction diagonal)" if centred else " (a corner)")
    note += _size_later(ctx, doc, sketch, items, data)
    return ok(f"Rectangle: {width}x{height}{unit_str}{note}", data)


@tool(
    group="sketch",
    description="Add an arc to the open sketch from its centre, radius and start / end angles (degrees), counter-clockwise from start to end. Equal angles are refused: use draw_circle for a full circle. In a part its radius becomes a dimension when the sketch is closed, unless set first.",
    params={'cx': {'type': 'number', 'description': "X of the centre"},
     'cy': {'type': 'number', 'description': "Y of the centre"},
     'radius': {'type': 'number', 'description': "Arc radius"},
     'start_angle': {'type': 'number', 'description': "Angle where the arc starts, in degrees"},
     'end_angle': {'type': 'number', 'description': "Angle where the arc ends, in degrees"},
     'unit': {'type': 'string', 'description': "mm, cm, m, inch or ft"}},
    required=['cx', 'cy', 'radius', 'start_angle', 'end_angle'],
    needs='any',
)
def draw_arc(ctx, cx, cy, radius, start_angle, end_angle, unit=None):
    doc, sketch = _draw(ctx)
    cx_m, cy_m, r_m = (ctx.to_m(v, unit) for v in (cx, cy, radius))
    if abs(end_angle - start_angle) < 1e-9:
        raise ToolError("start_angle and end_angle are the same: the arc would have no length. "
                        "For a full circle use draw_circle.", Err.INPUT)
    # the arc runs counter-clockwise from start to end: its sweep is (end - start) modulo 360
    sweep = (end_angle - start_angle) % 360.0
    full_circle = sweep < 1e-9 or 360.0 - sweep < 1e-9
    a0, a1 = math.radians(start_angle), math.radians(end_angle)
    sk.check_input(points=[(cx_m - r_m, cy_m - r_m), (cx_m + r_m, cy_m + r_m)], sizes=[("The radius", r_m)])
    before = sk.count(doc, sketch)
    with sk.exact_input(doc):
        if full_circle:
            # a whole number of turns: start and end coincide, which an arc cannot draw
            arc = sw.sketch_draw.sw_circle(doc, cx_m, cy_m, r_m)
        else:
            arc = sw.sketch_draw.sw_arc(doc, cx_m, cy_m, r_m, a0, a1)
    if arc is None:
        raise ToolError("SolidWorks could not draw that arc", Err.SKETCH)
    unit_str = units.label(unit)
    sweep_angle = 360.0 if full_circle else round(sweep, 6)
    data = {"radius": radius, "start_angle": start_angle, "end_angle": end_angle,
            "sweep_angle": sweep_angle, "unit": unit_str}
    note = sk.new_entity_ids(doc, sketch, before, data, created=[arc])
    ends = [(cx_m + r_m * math.cos(a), cy_m + r_m * math.sin(a)) for a in (a0, a1)]
    if sk.tie_to_origin(doc, sketch, [arc], drawn=sk.round_points(cx_m, cy_m, r_m) + ends):
        data["tied_to_origin"] = True
        note += ORIGIN_NOTE
    note += _size_later(ctx, doc, sketch, [(arc, cx_m + r_m + TEXT_GAP_M, cy_m + r_m + TEXT_GAP_M)], data)
    if full_circle:
        return ok(f"Full circle drawn (r={radius}{unit_str}): {start_angle:g}° to {end_angle:g}° "
                  f"is a whole turn{note}", data)
    return ok(f"Arc: r={radius}{unit_str}, {sweep_angle:g}° counter-clockwise from "
              f"{start_angle:g}° to {end_angle:g}°{note}", data)


@tool(
    group="sketch",
    description="Add a regular polygon whose corners lie on a circle of the given radius; the first corner is at angle 0. It is made of lines tied to a construction circle, so dimension that circle to resize the polygon; in a part that diameter becomes a dimension when the sketch is closed, unless set first. Returns line_ids (the sides, in order) and circle_id, the ids list_sketch_entities shows.",
    params={'cx': {'type': 'number', 'description': "X of the centre"},
     'cy': {'type': 'number', 'description': "Y of the centre"},
     'radius': {'type': 'number', 'description': "Radius of the circle the corners lie on"},
     'sides': {'type': 'integer', 'description': "How many sides, 3 to 100"},
     'unit': {'type': 'string', 'description': "mm, cm, m, inch or ft"}},
    required=['cx', 'cy', 'radius', 'sides'],
    needs='any',
)
def draw_polygon(ctx, cx, cy, radius, sides, unit=None):
    doc, sketch = _draw(ctx)
    try:
        whole = float(sides) == int(sides)
    except (TypeError, ValueError):
        whole = False
    if not whole or sides < 3 or sides > 100:
        raise ToolError("Sides must be a whole number between 3 and 100", Err.INPUT)
    sides = int(sides)
    cx_m, cy_m, r_m = (ctx.to_m(v, unit) for v in (cx, cy, radius))
    sk.check_input(points=[(cx_m - r_m, cy_m - r_m), (cx_m + r_m, cy_m + r_m)], sizes=[("The radius", r_m)])

    # SolidWorks' own polygon command came out distorted when the sketch held another polygon.
    # The polygon is drawn as plain lines at exact corners inside a construction circle, then tied
    # together (corners on the circle, sides equal), so dimensioning the circle resizes the whole.
    before = sk.count(doc, sketch)
    corners = [(cx_m + r_m * math.cos(2.0 * math.pi * i / sides),
                cy_m + r_m * math.sin(2.0 * math.pi * i / sides)) for i in range(sides)]
    lines, circle = [], None
    with sk.exact_input(doc):
        for i in range(sides):
            a, b = corners[i], corners[(i + 1) % sides]
            line = sw.sketch_draw.sw_line(doc, a[0], a[1], b[0], b[1])
            if line is None:
                break
            lines.append(line)
        if len(lines) == sides:
            circle = sw.sketch_draw.sw_circle(doc, cx_m, cy_m, r_m)
            if circle is not None:
                circle.ConstructionGeometry = True

    if len(lines) != sides or circle is None:
        sw.sketch_draw.sw_delete_entities(doc, lines + ([circle] if circle is not None else []))
        sw.common.sw_clear_selection(doc)
        sk.segments(doc, sketch)
        raise ToolError("SolidWorks could not draw that polygon", Err.SKETCH)

    relations = sw.sketch_draw.sw_tie_polygon(doc, lines, circle)
    sw.common.sw_clear_selection(doc)

    unit_str = units.label(unit)
    data = {"sides": sides, "radius": radius, "unit": unit_str, "relations_added": relations}
    # SolidWorks lists the new circle before the new lines; the sides get the first ids, the circle the last
    note = sk.new_entity_ids(doc, sketch, before, data, created=lines + [circle], ordered=True)
    if sk.tie_to_origin(doc, sketch, [circle], drawn=sk.round_points(cx_m, cy_m, r_m)):
        data["tied_to_origin"] = True
        note += ORIGIN_NOTE
    note += _size_later(ctx, doc, sketch, [(circle, cx_m + r_m + TEXT_GAP_M, cy_m + r_m + TEXT_GAP_M)], data)
    if "ids" in data:
        data["line_ids"] = data["ids"][:-1]
        data["circle_id"] = data["ids"][-1]
    return ok(f"{sides}-sided polygon: r={radius}{unit_str}{note}; the last entity is its "
              f"construction circle (dimension that to resize the polygon)", data)


def _slot_parts(doc, sketch, known, ends, width_m):
    """Among the entities that are not in known (segment keys before the draw): the slot's centreline, arcs and
    lines, by where they are, and every new entity: (centreline, [arcs], [lines], [new])."""
    near = lambda p, q: math.hypot(p[0] - q[0], p[1] - q[1]) <= 1e-9      # noqa: E731
    centreline, arcs, lines = None, [], []
    new = [seg for seg in sk.segments(doc, sketch) if sk.segment_key(seg) not in known]
    for seg in new:
        try:
            kind = int(value(seg, "GetType"))
            if kind == 1:
                c = value(seg, "GetCenterPoint2")
                if any(near((c.X, c.Y), e) for e in ends) and abs(float(value(seg, "GetRadius")) - width_m / 2) <= 1e-9:
                    arcs.append(seg)
                continue
            if kind != 0:
                continue
            a, b = value(seg, "GetStartPoint2"), value(seg, "GetEndPoint2")
            pair = ((a.X, a.Y), (b.X, b.Y))
            if bool(value(seg, "ConstructionGeometry")):
                if {near(pair[0], ends[0]) and near(pair[1], ends[1]), near(pair[0], ends[1]) and near(pair[1], ends[0])} & {True}:
                    centreline = seg
            elif abs(math.hypot(pair[1][0] - pair[0][0], pair[1][1] - pair[0][1])
                     - math.hypot(ends[1][0] - ends[0][0], ends[1][1] - ends[0][1])) <= 1e-9 \
                    and all(abs(min(math.hypot(p[0] - e[0], p[1] - e[1]) for e in ends) - width_m / 2) <= 1e-9
                            for p in pair):
                lines.append(seg)
        except Exception as e:
            logger.debug(f"Slot part not read: {e}")
    return centreline, arcs, lines, new


@tool(
    group="sketch",
    description="Add a straight slot to the open sketch: two half circles joined by two lines, around a construction centreline. x, y is the slot's centre, centre_distance the distance between the two arc centres, width the slot's width (twice the end radius), angle its direction from +X (0 = horizontal, the default; the result says so). At 0 or 90 degrees it is horizontal or vertical by a relation; centred on the origin it is tied there. In a part its centre distance and end radius (width / 2) become dimensions when the sketch is closed, unless set first: a slot on the origin at 0 or 90 degrees is then fully defined. At any other angle the angle stays free: dimension it from a reference line. Returns the ids of its entities.",
    params={'x': {'type': 'number', 'description': "X of the slot's centre"},
     'y': {'type': 'number', 'description': "Y of the slot's centre"},
     'centre_distance': {'type': 'number', 'description': "Distance between the centres of the two end arcs"},
     'width': {'type': 'number', 'description': "Width of the slot"},
     'angle': {'type': 'number', 'description': "Direction of the slot, degrees from +X (default 0)"},
     'unit': {'type': 'string', 'description': 'mm, cm, m, inch or ft'}},
    required=['x', 'y', 'centre_distance', 'width'],
    needs='any',
)
def draw_slot(ctx, x, y, centre_distance, width, angle=None, unit=None):
    doc, sketch = _draw(ctx)
    try:
        degrees = 0.0 if angle is None else float(angle)
    except (TypeError, ValueError):
        raise ToolError("angle must be a number of degrees", Err.INPUT)
    if not math.isfinite(degrees):
        raise ToolError("angle must be a number of degrees", Err.INPUT)
    cx_m, cy_m = ctx.to_m(x, unit), ctx.to_m(y, unit)
    length_m = units.size_m(centre_distance, unit, "centre_distance")
    width_m = units.size_m(width, unit, "width")
    turn = math.radians(degrees)
    dx, dy = math.cos(turn) * length_m / 2, math.sin(turn) * length_m / 2
    ends = [(cx_m - dx, cy_m - dy), (cx_m + dx, cy_m + dy)]
    reach = length_m / 2 + width_m / 2
    sk.check_input(points=[(cx_m - reach, cy_m - reach), (cx_m + reach, cy_m + reach)],
                   sizes=[("centre_distance", length_m), ("width", width_m)])
    known = {sk.segment_key(seg) for seg in sk.segments(doc, sketch)}
    before = len(known)
    with sk.exact_input(doc):
        slot = sw.sketch_draw.sw_slot(doc, cx_m, cy_m, ends[1][0], ends[1][1], width_m)
    centreline, arcs, lines, new = _slot_parts(doc, sketch, known, ends, width_m)
    if slot is None or centreline is None or len(arcs) != 2 or len(lines) != 2 or len(new) != 5:
        sw.sketch_draw.sw_delete_entities(doc, new)         # a refusal leaves the sketch as it was
        sw.common.sw_clear_selection(doc)
        sk.segments(doc, sketch)
        raise ToolError("SolidWorks could not draw that slot", Err.SKETCH)

    unit_str = units.label(unit)
    data = {"centre_distance": centre_distance, "width": width, "angle": degrees + 0.0, "unit": unit_str,
            "angle_from": "given" if angle is not None else "default (0)"}
    note = sk.new_entity_ids(doc, sketch, before, data, created=[centreline] + arcs + lines)
    square = abs(math.remainder(degrees, 90.0)) < 1e-9
    if not square:
        data["angle_free"] = True
        note += "; its angle is free: dimension it from a reference line"
    if cx_m == 0 and cy_m == 0 and doc_type(doc) == 1:
        data["tied_to_origin"] = "centre"           # SolidWorks ties a slot's centre point drawn at 0, 0 to the origin
        note += ORIGIN_NOTE + " (its centre)"
    elif sk.tie_to_origin(doc, sketch, arcs, drawn=ends):
        data["tied_to_origin"] = "arc centre"
        note += ORIGIN_NOTE + " (an arc centre)"
    gap_x, gap_y = -math.sin(turn) * (width_m / 2 + TEXT_GAP_M), math.cos(turn) * (width_m / 2 + TEXT_GAP_M)
    arc_text = (ends[1][0] + math.cos(turn) * (width_m / 2 + TEXT_GAP_M),
                ends[1][1] + math.sin(turn) * (width_m / 2 + TEXT_GAP_M))
    end_arc = next(a for a in arcs if math.hypot(value(a, "GetCenterPoint2").X - ends[1][0],
                                                 value(a, "GetCenterPoint2").Y - ends[1][1]) <= 1e-9)
    note += _size_later(ctx, doc, sketch, [(centreline, cx_m + gap_x, cy_m + gap_y), (end_arc, *arc_text)], data)
    angle_text = f" at {degrees:g} deg" + (" (default)" if angle is None else "")
    return ok(f"Slot: centre distance {centre_distance}{unit_str}, width {width}{unit_str}{angle_text}{note}", data)


@tool(
    group="sketch",
    description="Add a sketch point at x, y in the open sketch. A point is reference geometry: an extrude ignores it. In an assembly it can be mated to or measured from; list_component_sketches gives its id. list_sketch_entities shows such points under 'points'.",
    params={'x': {'type': 'number', 'description': 'X in sketch coordinates'},
     'y': {'type': 'number', 'description': 'Y in sketch coordinates'},
     'unit': {'type': 'string', 'description': 'mm, cm, m, inch or ft'}},
    required=['x', 'y'],
    needs='any',
)
def draw_point(ctx, x, y, unit=None):
    doc, sketch = _draw(ctx)
    for v, label in ((x, "x"), (y, "y")):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ToolError(f"{label} must be a number", Err.INPUT)
    x_m, y_m = ctx.to_m(x, unit), ctx.to_m(y, unit)
    sk.check_input(points=[(x_m, y_m)])
    before = len(list(value(sketch, "GetSketchPoints2") or []))
    with sk.exact_input(doc):
        point = sw.sketch_draw.sw_point(doc, x_m, y_m)
    sw.common.sw_clear_selection(doc)
    after = len(list(value(sketch, "GetSketchPoints2") or []))
    if point is None:
        raise ToolError("SolidWorks could not draw the point", Err.SKETCH)
    at = [round(ctx.from_m(float(point.X), unit), 4) + 0.0, round(ctx.from_m(float(point.Y), unit), 4) + 0.0]
    note = "" if after > before else " (an existing point at that place was reused)"
    unit_str = units.label(unit)
    return ok(f"Point at ({at[0]}, {at[1]}) {unit_str}{note}",
              {"x": at[0], "y": at[1], "unit": unit_str, "points_in_sketch": after})


@tool(
    group="sketch",
    description="Add a centerline (a construction line) to the open sketch. It is the axis for revolve_sketch and the mirror line of a 'symmetric' relation.",
    params={'x1': {'type': 'number', 'description': 'X of one end of the axis'},
     'y1': {'type': 'number', 'description': 'Y of one end of the axis'},
     'x2': {'type': 'number', 'description': 'X of the other end'},
     'y2': {'type': 'number', 'description': 'Y of the other end'},
     'unit': {'type': 'string', 'description': 'mm, cm, m, inch or ft'}},
    required=['x1', 'y1', 'x2', 'y2'],
    needs='any',
)
def draw_centerline(ctx, x1, y1, x2, y2, unit=None):
    doc, sketch = _draw(ctx)
    x1_m, y1_m, x2_m, y2_m = (ctx.to_m(v, unit) for v in (x1, y1, x2, y2))
    sk.check_input(points=[(x1_m, y1_m), (x2_m, y2_m)],
                   sizes=[("The length of the centerline", math.hypot(x2_m - x1_m, y2_m - y1_m))])
    before = sk.count(doc, sketch)
    with sk.exact_input(doc):
        line = sw.sketch_draw.sw_centerline(doc, x1_m, y1_m, x2_m, y2_m)
    if line is None:
        raise ToolError("SolidWorks could not draw that centerline", Err.SKETCH)
    data = {}
    note = sk.new_entity_ids(doc, sketch, before, data, created=[line])
    try:
        data["name"] = value(line, "GetName")
    except Exception:
        pass
    return ok(f"Centerline created{note}", data)
