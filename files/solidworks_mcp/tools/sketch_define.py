"""Defining a sketch: list its entities, add relations and dimensions, fit a spline."""

import logging
import math

from ..core.api import Err, geometry, ok, sketch as sk, sw, tool, ToolError, units, value as _value

logger = logging.getLogger(__name__)

# relation name -> (constraint for segments / mixed selections, constraint when only points are selected)
RELATIONS = {
    "horizontal": ("sgHORIZONTAL2D", "sgHORIZONTALPOINTS2D"),
    "vertical": ("sgVERTICAL2D", "sgVERTICALPOINTS2D"),
    "coincident": ("sgCOINCIDENT", "sgCOINCIDENT"),
    "merge": ("sgMERGEPOINTS", "sgMERGEPOINTS"),
    "midpoint": ("sgATMIDDLE", "sgATMIDDLE"),
    "parallel": ("sgPARALLEL", None),
    "perpendicular": ("sgPERPENDICULAR", None),
    "collinear": ("sgCOLINEAR", None),
    "equal": ("sgSAMELENGTH", None),
    "tangent": ("sgTANGENT", None),
    "concentric": ("sgCONCENTRIC", "sgCONCENTRIC"),
    "coradial": ("sgCORADIAL", None),
    "symmetric": ("sgSYMMETRIC", "sgSYMMETRIC"),
    "fix": ("sgFIXED", "sgFIXED"),
}

_POINT_GETTERS = {"start": "GetStartPoint2", "end": "GetEndPoint2", "center": "GetCenterPoint2"}
_DEFAULT_FIT_TOLERANCE_M = 1e-6         # 0.001 mm


# ---------------------------------------------------------------- describing a sketch

def _round_xy(ctx, point, unit):
    return [round(ctx.from_m(point.X, unit), 4) + 0.0, round(ctx.from_m(point.Y, unit), 4) + 0.0]


def _segment_info(ctx, seg, index, unit) -> dict:
    """One sketch segment in sketch coordinates, in the user's unit."""
    kind = int(_value(seg, "GetType"))
    info = {"id": index}
    try:
        info["name"] = _value(seg, "GetName")
    except Exception:
        pass
    if kind == 0:
        info["type"] = "line"
        info["start"] = _round_xy(ctx, _value(seg, "GetStartPoint2"), unit)
        info["end"] = _round_xy(ctx, _value(seg, "GetEndPoint2"), unit)
    elif kind == 1:
        is_circle = False
        try:
            is_circle = int(_value(seg, "IsCircle")) == 1
        except Exception:
            pass
        info["type"] = "circle" if is_circle else "arc"
        info["center"] = _round_xy(ctx, _value(seg, "GetCenterPoint2"), unit)
        info["radius"] = round(ctx.from_m(_value(seg, "GetRadius"), unit), 4)
        if not is_circle:
            info["start"] = _round_xy(ctx, _value(seg, "GetStartPoint2"), unit)
            info["end"] = _round_xy(ctx, _value(seg, "GetEndPoint2"), unit)
    else:
        info["type"] = {2: "ellipse", 3: "spline", 4: "text", 5: "parabola"}.get(kind, f"type_{kind}")
    try:
        info["length"] = round(ctx.from_m(_value(seg, "GetLength"), unit), 4)
    except Exception:
        pass
    try:
        if bool(_value(seg, "ConstructionGeometry")):
            info["construction"] = True
    except Exception:
        pass
    try:
        code = int(_value(seg, "Status"))
        info["status"] = sk.STATUS_NAMES.get(code, f"status_{code}")
    except Exception:
        pass
    return info


def _sketch_dimensions(ctx, sketch, unit) -> list:
    dims = []
    try:
        # the walk can also return dimensions of the feature the sketch sits on
        # (an offset plane's distance): keep only this sketch's own
        own = f"@{sketch.Name}@"
        display = _value(sketch, "GetFirstDisplayDimension")
        seen = 0
        while display is not None and seen < 500:
            seen += 1
            dim = sw.editing.sw_display_dimension(display)
            if own in dim.FullName:
                raw = float(dim.SystemValue)
                angular = int(_value(dim, "GetType")) == 1
                dims.append({"name": dim.FullName,
                             "value": round(math.degrees(raw), 4) if angular else round(ctx.from_m(raw, unit), 4),
                             "kind": "angle_deg" if angular else "length",
                             "driven": int(dim.DrivenState) == 1})
            display = sketch.GetNextDisplayDimension(display)
    except Exception as e:
        logger.debug(f"Could not list the dimensions: {e}")
    return dims


def _free_points(ctx, sketch, segs, unit) -> list:
    """Sketch points that are not an end or the centre of an entity (drawn with draw_point)."""
    taken = []
    for seg in segs:
        for getter in _POINT_GETTERS.values():
            try:
                point = _value(seg, getter)
                if point is not None:
                    taken.append((float(point.X), float(point.Y), float(point.Z)))
            except Exception:
                continue
    free = []
    for point in list(_value(sketch, "GetSketchPoints2") or []):
        here = (float(point.X), float(point.Y), float(point.Z))
        if not any(math.dist(here, other) <= sk.JOIN_TOLERANCE_M for other in taken):
            free.append({"x": round(ctx.from_m(here[0], unit), 4) + 0.0,
                         "y": round(ctx.from_m(here[1], unit), 4) + 0.0})
    return free


def _endpoint_joins(segs, entities) -> list:
    """Adds "start_joins" / "end_joins" to every entity with ends: the ends of other entities at the
    same place, as "id:start" / "id:end". Returns the ends that touch nothing (construction geometry
    is not counted as a partner and not listed)."""
    ends = []           # (entity index, "start" | "end", x, y, z, construction)
    for index, seg in enumerate(segs):
        info = entities[index]
        if info.get("type") == "circle":
            continue
        for which, getter in (("start", "GetStartPoint2"), ("end", "GetEndPoint2")):
            try:
                point = _value(seg, getter)
                if point is not None:
                    ends.append((index, which, float(point.X), float(point.Y), float(point.Z),
                                 bool(info.get("construction"))))
            except Exception:
                continue
    open_ends = []
    for index, which, x, y, z, construction in ends:
        close = [(o, ow, oc) for o, ow, ox, oy, oz, oc in ends
                 if (o, ow) != (index, which) and math.dist((x, y, z), (ox, oy, oz)) <= sk.JOIN_TOLERANCE_M]
        entities[index][f"{which}_joins"] = [f"{o + 1}:{ow}" for o, ow, _ in close]
        if not construction and not any(not oc for _, _, oc in close):
            open_ends.append(f"{index + 1}:{which}")
    return open_ends


@tool(
    group="sketch",
    description="List the open sketch's entities (ids, coordinates, sizes), dimensions, relation count and definition status (under_defined / fully_defined / over_defined). The ids are what add_sketch_relation and add_sketch_dimension take. They follow drawing order (1 = the first entity drawn; a rectangle is 4 entities) and stay the same when more geometry is added. Every line, arc and spline also gets start_joins / end_joins: the ends of other entities at the same place, as 'id:start' / 'id:end'. open_ends lists the ends that touch nothing (construction geometry does not count), so an empty open_ends means every contour is closed.",
    params={'unit': {'type': 'string', 'description': 'mm, cm, m, inch or ft'}},
    needs='any',
)
def list_sketch_entities(ctx, unit=None):
    doc = ctx.doc
    sketch = sk.active_sketch(doc)
    segs = sk.segments(doc, sketch)
    entities = [_segment_info(ctx, seg, i + 1, unit) for i, seg in enumerate(segs)]
    open_ends = _endpoint_joins(segs, entities)
    try:
        points = _free_points(ctx, sketch, segs, unit)
    except Exception as e:
        logger.debug(f"Could not list the sketch points: {e}")
        points = []
    dims = _sketch_dimensions(ctx, sketch, unit)
    status = sk.status(sketch)
    try:
        name = sketch.Name
    except Exception:
        name = "<sketch>"
    relations = sk.relation_count(sketch)
    return ok(f"{name}: {len(entities)} entities, {len(dims)} dimensions, {relations} relations, status {status}",
              {"sketch": name, "status": status, "unit": units.label(unit), "relations": relations,
               "open_ends": open_ends, "points": points, "entities": entities, "dimensions": dims})


# ---------------------------------------------------------------- selecting by reference

def _model_edge(ctx, doc, text: str, unit):
    """The model edge through the point of an 'edge:x,y,z' reference (model coordinates, the call's unit)."""
    try:
        p = tuple(ctx.to_m(float(v), unit) for v in text.split(":", 1)[1].split(","))
        if len(p) != 3:
            raise ValueError
    except (ValueError, IndexError):
        raise ToolError(f"Bad edge reference '{text}': use 'edge:x,y,z', a point on the model edge in model "
                        f"coordinates, e.g. 'edge:80,40,5'", Err.SELECTION)
    # the sketch plane's normal in model space: [sketch] = [model] * R + T, so it is R's third column
    normal = None
    try:
        a = [float(v) for v in _value(sk.open_sketch(doc).ModelToSketchTransform, "ArrayData")]
        normal = (a[2], a[5], a[8])
    except Exception:
        pass
    return geometry.edge_at(doc, p, text[5:], normal, face=sk.reference_face(sk.open_sketch(doc)), unit=unit)


def _select_refs(doc, sketch, refs, ctx=None, unit=None) -> list:
    """Select sketch entities by reference: an id (3 or "3") for the whole entity, "3:start" /
    "3:end" / "3:center" for one of its points, "origin", or "edge:x,y,z" for a model edge through that model
    point (the dimension or relation then ties the sketch to the part). Returns "segment" / "point" / "edge"
    per ref."""
    if isinstance(refs, (str, int)):
        refs = [refs]
    if not refs:
        raise ToolError("No entities given", Err.SELECTION)
    segs = sk.segments(doc, sketch)
    kinds = []

    def fail(message):
        sw.common.sw_clear_selection(doc)
        raise ToolError(message, Err.SELECTION)

    sw.common.sw_clear_selection(doc)
    for ref in refs:
        text = str(ref).strip().lower()
        if text.startswith("edge:"):
            if ctx is None:
                fail(f"'{ref}': a model edge cannot be used here")
            try:
                edge = _model_edge(ctx, doc, text, unit)
            except ToolError as e:
                fail(e.message)
            if not sw.sketch_define.sw_select_entity(edge):
                fail(f"Could not select the edge at {text[5:]}")
            kinds.append("edge")
            continue
        if text == "origin":
            if not sw.common.sw_select_named(doc, "Point1@Origin", "EXTSKETCHPOINT", 0):
                fail("Could not select the sketch origin")
            kinds.append("point")
            continue
        parts = text.split(":")
        try:
            index = int(parts[0])
        except ValueError:
            fail(f"Bad entity reference '{ref}'. Use an id like 3, a point like '3:start', "
                 f"'3:end', '3:center', 'origin', or 'edge:x,y,z' for a model edge")
        if index < 1 or index > len(segs):
            fail(f"Entity {index} does not exist (the sketch has {len(segs)} entities)")
        target, kind = segs[index - 1], "segment"
        if len(parts) > 1:
            getter = _POINT_GETTERS.get(parts[1])
            if getter is None:
                fail(f"Bad point '{parts[1]}' in '{ref}'. Use start, end or center")
            try:
                target = _value(target, getter)
            except Exception:
                target = None
            if target is None:
                fail(f"Entity {index} has no {parts[1]} point")
            kind = "point"
        if not sw.sketch_define.sw_select_entity(target):
            fail(f"Could not select '{ref}'")
        kinds.append(kind)
    return kinds


def _ref_point(doc, sketch, ref):
    """The sketch point a reference like '1:start' or '2:center' names, or None."""
    parts = str(ref).split(":")
    if len(parts) != 2 or not parts[0].strip().isdigit() or parts[1] not in _POINT_GETTERS:
        return None
    segs = sk.segments(doc, sketch)
    index = int(parts[0])
    if not 1 <= index <= len(segs):
        return None
    try:
        return _value(segs[index - 1], _POINT_GETTERS[parts[1]])
    except Exception:
        return None


def _on_origin(sketch, point) -> bool:
    """Is this sketch point fixed on the origin already (a coincident relation that holds it)? A point at (0, 0)
    whose own status is fully defined counts; a free one at (0, 0) does not."""
    try:
        return int(_value(point, "Status")) == 3
    except Exception:
        return False


def _ref_point_gap(doc, sketch, refs):
    """Distance in metres between two point references like '1:end', '2:start' (None if unknown)."""
    try:
        segs = sk.segments(doc, sketch)
        points = []
        for ref in refs:
            text = str(ref).strip().lower()
            if text == "origin":
                points.append((0.0, 0.0))
                continue
            index, name = text.split(":")
            point = _value(segs[int(index) - 1], _POINT_GETTERS[name])
            points.append((float(point.X), float(point.Y)))
        return math.dist(points[0], points[1]) if len(points) == 2 else None
    except Exception:
        return None


@tool(
    group="sketch",
    description="Add a geometric relation in the open sketch. Entities are given by id (drawing order; see list_sketch_entities): 3 for a whole entity, '3:start', '3:end' or '3:center' for one of its points, or 'origin'. A relation that contradicts a size the tools added from the drawing (named DrawnN) replaces it (the result says which); one that contradicts the user's own dimensions or relations is refused, naming them, and not left in the sketch. Under-defined geometry is free to move, so for 'symmetric' first define the centerline (dimensions or relations) if it must stay put. 'fix' is refused in a part sketch (dimension from the user's references instead) and allowed in an assembly sketch.",
    params={'relation': {'type': 'string',
                  'enum': ['horizontal',
                           'vertical',
                           'coincident',
                           'merge',
                           'midpoint',
                           'parallel',
                           'perpendicular',
                           'collinear',
                           'equal',
                           'tangent',
                           'concentric',
                           'coradial',
                           'symmetric',
                           'fix'],
                  'description': 'Relation type. horizontal/vertical take one line or two points; '
                                 'merge takes two points; symmetric takes two entities (or two points) '
                                 'plus a centerline.'},
     'entities': {'type': 'array',
                  'items': {'type': 'string'},
                  'description': "Entity references as strings, e.g. ['1'], ['2', '4'], ['origin', "
                                 "'1:start'], or a model edge by a point on it: ['1:center', 'edge:80,40,5']"},
     'unit': {'type': 'string', 'description': "Unit of the 'edge:x,y,z' points (mm, cm, m, inch or ft)"}},
    required=['relation', 'entities'],
    needs='any',
)
def add_sketch_relation(ctx, relation, entities, unit=None):
    doc = ctx.doc
    sketch = sk.active_sketch(doc)
    key = (relation or "").strip().lower()
    if key not in RELATIONS:
        raise ToolError(f"Unknown relation '{relation}'. Use one of: {', '.join(sorted(RELATIONS))}", Err.INPUT)
    # a point drawn on the origin is tied to it already (the draw tools do it): coincident again is not an error
    refs = [str(e).strip().lower() for e in (entities if isinstance(entities, (list, tuple)) else [entities])]
    if key == "coincident" and len(refs) == 2 and "origin" in refs:
        other = next(r for r in refs if r != "origin")
        point = _ref_point(doc, sketch, other)
        if point is not None and math.hypot(float(point.X), float(point.Y)) <= sk.JOIN_TOLERANCE_M and \
                _on_origin(sketch, point):
            return ok(f"{other} is already coincident with the origin (tied when it was drawn). Sketch status: "
                      f"{sk.status(sketch)}", {"relation": key, "entities": list(entities), "already": True,
                                               "status": sk.status(sketch)})
    # a part sketch is defined by the user's references; fixing pins geometry at an arbitrary place
    if key == "fix" and int(_value(doc, "GetType")) == 1:
        raise ToolError("'fix' is refused in a part sketch: it pins the geometry where it happens to be, not where the "
                        "user's design puts it. Dimension it from the user's references instead (add_sketch_dimension "
                        "to a model edge 'edge:x,y,z', a sketch line or 'origin') or relate it (coincident with "
                        "'origin', midpoint, symmetric). In an assembly sketch 'fix' is allowed.", Err.INPUT)
    kinds = _select_refs(doc, sketch, entities, ctx, unit)

    segment_constraint, point_constraint = RELATIONS[key]
    only_points = all(kind == "point" for kind in kinds)
    constraint = point_constraint if (only_points and point_constraint) else segment_constraint
    entity_list = list(entities) if isinstance(entities, (list, tuple)) else [entities]
    if key == "merge" and not (only_points and len(kinds) == 2):
        sw.common.sw_clear_selection(doc)
        raise ToolError("'merge' takes exactly two points, e.g. ['1:end', '2:start']", Err.INPUT)
    gap_before = _ref_point_gap(doc, sketch, entity_list) if key == "merge" else None

    before, status_before = sk.relation_count(sketch), sk.status(sketch)
    doc.SketchAddConstraints(constraint)
    sw.common.sw_clear_selection(doc)
    after, status = sk.relation_count(sketch), sk.status(sketch)
    data = {"relation": key, "entities": entity_list, "relations_before": before,
            "relations_after": after, "status": status}

    # a relation that contradicts the sketch (vertical on a line that is already horizontal)
    # is added anyway and leaves the sketch over-defined: take it out again
    gave_way = None
    if status in sk.BROKEN and status_before not in sk.BROKEN:
        removed = sw.sketch_define.sw_delete_relations_after(sketch, before)
        if not removed or sk.status(sketch) in sk.BROKEN:
            sw.sketch_define.sw_undo(doc)
            removed = sk.status(sketch) == status_before and sk.relation_count(sketch) <= before
        keys = sk.ref_keys(doc, sketch, entity_list)

        def again() -> bool:
            """Add the relation again (a Drawn size on its entities was taken out): True when it holds."""
            count = sk.relation_count(sketch)
            _select_refs(doc, sketch, entity_list, ctx)
            doc.SketchAddConstraints(constraint)
            sw.common.sw_clear_selection(doc)
            if sk.status(sketch) in sk.BROKEN:
                if not sw.sketch_define.sw_delete_relations_after(sketch, count) or sk.status(sketch) in sk.BROKEN:
                    sw.sketch_define.sw_undo(doc)
                return False
            return sk.relation_count(sketch) > count

        if removed:
            # one of our own sizes from the drawing (DrawnN) may be what it contradicts: that one gives way
            gave_way = sk.give_way(ctx.app, doc, sketch, keys, again, [])
        if gave_way is None:
            data["relations_after"] = sk.relation_count(sketch)
            data["status"] = sk.status(sketch)
            theirs = sk.conflicts_on(lambda v: ctx.from_m(v, None), units.label(None), sketch, keys)
            data["conflicts"] = theirs
            raise ToolError(f"Not added: '{key}' contradicts the relations or dimensions that are already "
                            f"in the sketch (it would become {status.replace('_', ' ')})."
                            + (f" It conflicts with {', '.join(theirs)}." if theirs else "")
                            + ("" if removed else " WARNING: the relation could not be removed again."),
                            Err.SKETCH, data)
        after, status = sk.relation_count(sketch), sk.status(sketch)
        data.update(relations_after=after, status=status,
                    replaced=f"{gave_way[0]} ({round(ctx.from_m(gave_way[1], None), 4)}{units.label(None)})")

    if key == "merge":
        # merging two points leaves no relation behind (they become one): the count cannot show it
        gap = _ref_point_gap(doc, sketch, entity_list)
        merged = (after > before) or (gap is not None and gap < 1e-9 and (gap_before is None or gap_before > 1e-9))
        if merged:
            return ok(f"Merged the two points. Sketch status: {status}", data)
        raise ToolError(f"SolidWorks did not merge the two points. Sketch status: {status}", Err.SKETCH, data)

    if before >= 0 and after <= before and gave_way is None:
        raise ToolError(f"SolidWorks did not add the '{key}' relation (it may already exist, "
                        f"or it does not apply to these entities). Sketch status: {status}", Err.SKETCH, data)
    return ok(f"Added '{key}' relation. Sketch status: {status}"
              + (f". Replaced the drawn size {data['replaced']} (added from the drawing, not by the user)"
                 if gave_way else ""), data)


# ---------------------------------------------------------------- dimensions

@tool(
    group="sketch",
    description="Add a driving dimension in the open sketch and optionally set its value. What gets measured follows from the entities: one line = length, circle = diameter, arc = radius, two points / a point and a line / two parallel lines = distance, two non-parallel lines = angle (degrees). The value must be greater than 0. A dimension that over-defines the sketch replaces a size the tools added from the drawing (named DrawnN; the result says which); one that conflicts with the user's own dimensions or relations, or that the sketch cannot satisfy, is rejected and not left in it, naming the conflicting dimensions. Several dimensions in one call: the others in more; if any is rejected, none of the call's stays. Returns the sketch's definition status.",
    params={'entities': {'type': 'array',
                  'items': {'type': 'string'},
                  'description': "Entity references as strings, e.g. ['1'], ['origin', '1:start'], "
                                 "['2', '3'], or a model edge by a point on it: ['1:center', 'edge:80,40,5']"},
     'value': {'type': 'number',
               'description': 'Value to set (length in unit, or degrees for an angle). Omit to keep '
                              'the size as drawn.'},
     'orientation': {'type': 'string',
                     'enum': ['auto', 'horizontal', 'vertical'],
                     'default': 'auto',
                     'description': 'For distances between points: measure horizontally, vertically, '
                                    'or aligned (auto)'},
     'more': {'type': 'array',
              'items': {'type': 'object',
                        'properties': {'entities': {'type': 'array', 'items': {'type': 'string'}},
                                       'value': {'type': 'number'},
                                       'orientation': {'type': 'string', 'enum': ['auto', 'horizontal', 'vertical']}},
                        'required': ['entities']},
              'description': "Further dimensions in this call, e.g. [{'entities': ['origin', '2:start'], 'value': 20, "
                             "'orientation': 'horizontal'}]; same unit"},
     'unit': {'type': 'string', 'description': 'mm, cm, m, inch or ft'}},
    required=['entities'],
    needs='any',
)
def add_sketch_dimension(ctx, entities, value=None, unit=None, orientation="auto", more=None):
    doc = ctx.doc
    sketch = sk.active_sketch(doc)
    asks = [{"entities": entities, "value": value, "orientation": orientation}]
    if more is not None:
        if not isinstance(more, list) or not all(isinstance(m, dict) and m.get("entities") for m in more):
            raise ToolError("more must be a list of {entities, value, orientation}, each with entities", Err.INPUT)
        asks += [{"entities": m["entities"], "value": m.get("value"), "orientation": m.get("orientation", "auto")}
                 for m in more]
    unit_str = units.label(unit)
    for n, ask in enumerate(asks):                        # every input checked before anything is added
        where = "" if n == 0 else f"more[{n - 1}]: "
        ask["orientation"] = (ask.get("orientation") or "auto").strip().lower()
        if ask["orientation"] not in ("auto", "horizontal", "vertical"):
            raise ToolError(f"{where}orientation must be auto, horizontal or vertical", Err.INPUT)
        v = ask.get("value")
        # value 0 or negative would add the dimension, make the sketch unsolvable, and say "success"
        if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v)
                              or v <= 0):
            raise ToolError(f"{where}value must be a number greater than 0", Err.INPUT)
    done, replaced = [], []                       # added (display, name, size as drawn); Drawn sizes taken out
    try:
        results = [_one_dimension(ctx, doc, sketch, ask, unit, unit_str, n, done, replaced)
                   for n, ask in enumerate(asks)]
    except ToolError:
        for display, name, drawn in reversed(done):       # a refused call changes nothing: sizes back, then out
            try:
                sw.editing.sw_set_dimension_value(sw.editing.sw_display_dimension(display), drawn)
            except Exception:
                pass
            sw.sketch_define.sw_delete_dimension(doc, display, name)
        for item in replaced:
            sk.restore_drawn(ctx.app, doc, sketch, item)
        raise
    status = sk.status(sketch)
    data = dict(results[0])
    data["status"] = status
    if len(results) > 1:
        data["dimensions"] = results
    texts = [f"{r['name']} = {r['value']}{r['unit']}" for r in results]
    message = f"Dimension{'s' if len(results) > 1 else ''} {', '.join(texts)}. Sketch status: {status}"
    gone = [r["replaced"] for r in results if r.get("replaced")]
    if gone:
        message += (". Replaced the drawn size" + ("s " if len(gone) > 1 else " ") + ", ".join(gone)
                    + " (added from the drawing, not by the user)")
    return ok(message, data)


def _text_place(ctx, doc, sketch, orientation):
    """Where a new dimension's text goes: just outside the sketch geometry, staggered by the dimensions there."""
    xs, ys = [], []
    for seg in sk.segments(doc, sketch):
        for getter in _POINT_GETTERS.values():
            try:
                point = _value(seg, getter)
                if point is not None:
                    xs.append(float(point.X))
                    ys.append(float(point.Y))
            except Exception:
                pass
        try:
            if int(_value(seg, "GetType")) == 1:
                c, r = _value(seg, "GetCenterPoint2"), float(_value(seg, "GetRadius"))
                xs += [c.X - r, c.X + r]
                ys += [c.Y - r, c.Y + r]
        except Exception:
            pass
    if not xs:
        xs, ys = [0.0], [0.0]
    size = max(max(xs) - min(xs), max(ys) - min(ys), 0.005)
    gap = size * (0.15 + 0.08 * len(_sketch_dimensions(ctx, sketch, None)))   # stagger the texts
    return (max(xs) + gap, (min(ys) + max(ys)) / 2) if orientation == "vertical" else \
        ((min(xs) + max(xs)) / 2, min(ys) - gap)


def _one_dimension(ctx, doc, sketch, ask, unit, unit_str, n, done, replaced) -> dict:
    """Add one dimension; on a conflict, first let a Drawn size on the same entities give way. Appends what it
    added to done and what it took out to replaced (for the caller to undo a refused call)."""
    where = "" if n == 0 else f"more[{n - 1}]: "
    entities, value, orientation = ask["entities"], ask.get("value"), ask["orientation"]
    status_before = sk.status(sketch)
    keys = sk.ref_keys(doc, sketch, entities)
    attempt = {}

    def add_once() -> bool:
        """Add it; True when it holds. A dimension that does not hold is taken out again (attempt says why)."""
        text = _text_place(ctx, doc, sketch, orientation)
        _select_refs(doc, sketch, entities, ctx, unit)
        with sk.silent_dimensions(ctx.app):
            display = sw.sketch_define.sw_add_dimension(doc, orientation, text[0], text[1])
        sw.common.sw_clear_selection(doc)
        if display is None:
            raise ToolError(f"{where}SolidWorks could not create a dimension from these entities", Err.SKETCH,
                            {"status": sk.status(sketch)})
        dim = sw.editing.sw_display_dimension(display)
        name = dim.FullName
        angular = int(_value(dim, "GetType")) == 1
        attempt.update(display=display, dim=dim, name=name, angular=angular)

        def read_value():
            raw = float(dim.SystemValue)
            return round(math.degrees(raw), 4) if angular else round(ctx.from_m(raw, unit), 4)

        attempt["read"] = read_value
        if int(dim.DrivenState) == 1:                    # it over-defines the sketch: comes in driven
            attempt["why"] = ("over", read_value())
            sw.sketch_define.sw_delete_dimension(doc, display, name)
            return False
        drawn = float(dim.SystemValue)
        attempt["as_drawn"] = read_value()
        attempt["drawn_m"] = drawn
        if value is not None:
            if angular:
                if value >= 360:
                    sw.sketch_define.sw_delete_dimension(doc, display, name)
                    raise ToolError(f"{where}Not added: an angle must be less than 360 degrees.", Err.SKETCH)
                system_value = math.radians(float(value))
            else:
                system_value = ctx.to_m(value, unit)
                if system_value > sk.MODEL_LIMIT_M:
                    sw.sketch_define.sw_delete_dimension(doc, display, name)
                    raise ToolError(f"{where}Not added: the value is larger than SolidWorks' modelling space "
                                    f"(500 m). Check the value and the unit.", Err.SKETCH)
            code = sw.editing.sw_set_dimension_value(dim, system_value)
            if code != 0:
                sw.sketch_define.sw_delete_dimension(doc, display, name)
                raise ToolError(f"{where}Not added: SolidWorks refused the value {value} (code {code}).", Err.SKETCH)
            sw.common.sw_redraw(doc)
        status = sk.status(sketch)
        if status in sk.BROKEN and status_before not in sk.BROKEN:
            attempt["why"] = ("unsolvable", status)
            try:
                sw.editing.sw_set_dimension_value(dim, drawn)
            except Exception:
                pass
            sw.sketch_define.sw_delete_dimension(doc, display, name)
            return False
        return True

    gave_way = None
    if not add_once():
        gave_way = sk.give_way(ctx.app, doc, sketch, keys, add_once, replaced)
        if gave_way is None:
            why = attempt.get("why", ("over", None))
            theirs = sk.conflicts_on(lambda v: ctx.from_m(v, unit), unit_str, sketch, keys)
            named = f" It conflicts with {', '.join(theirs)}." if theirs else ""
            if why[0] == "over":
                raise ToolError(f"{where}Not added: this dimension would over-define the sketch (the size is "
                                f"already fixed at {why[1]}{'deg' if attempt.get('angular') else unit_str}).{named}",
                                Err.SKETCH, {"measured": why[1], "status": sk.status(sketch), "conflicts": theirs})
            shown = f"{value}{'deg' if attempt.get('angular') else unit_str}" if value is not None else "this dimension"
            raise ToolError(f"{where}Not added: the sketch cannot be solved with {shown} here (it would become "
                            f"{why[1].replace('_', ' ')}).{named}", Err.SKETCH,
                            {"status": sk.status(sketch), "conflicts": theirs})
    done.append((attempt["display"], attempt["name"], attempt.get("drawn_m")))
    row = {"name": attempt["name"], "kind": "angle_deg" if attempt["angular"] else "length",
           "value_as_drawn": attempt.get("as_drawn"), "value": attempt["read"](),
           "unit": "deg" if attempt["angular"] else unit_str}
    if gave_way:
        row["replaced"] = f"{gave_way[0]} ({round(ctx.from_m(gave_way[1], unit), 4)}{unit_str})"
    return row


# ---------------------------------------------------------------- fully define

@tool(
    group="sketch",
    description="Last resort, after the user's own dimensions: fully define the open sketch of a part, adding the dimensions and relations it still lacks, measured from the origin (baseline dimensions), with neutral relations only (horizontal, vertical, coincident, tangent; never equal, parallel, symmetric or midpoint). Add the user's own sizes first with add_sketch_dimension; this fills in the rest, mostly positions. Nothing moves. Returns the dimensions added (names and values; report them to the user as \"added by SolidWorks, not from your spec\"), how many relations were added, the sketch status and anything still free (construction geometry, such as fit_spline's helper lines, does not count, except a mirror line). Refused with no sketch open and on an over-defined sketch.",
    needs='part',
    clears_selection=True,
)
def fully_define_sketch(ctx):
    doc = ctx.doc
    sketch = sk.active_sketch(doc)
    name, state, unit_str = str(sketch.Name), sk.status(sketch), units.label(None)
    if state in sk.BROKEN:
        raise ToolError(f"{name} is {state.replace('_', '-')}: fix its dimensions and relations first "
                        f"(list_sketch_entities shows them)", Err.SKETCH, {"status": state})
    data = {"sketch": name, "added_dimensions": [], "added_relations": 0, "status": state, "free_entities": [],
            "free_points": 0}
    if sk.defined(doc, sketch)[0] and not sk.free_centerlines(sketch):
        return ok(f"{name} is already fully defined: nothing added", data)
    result = sk.auto_define(doc, sketch)
    if result["code"] != 0:
        raise ToolError(f"SolidWorks could not fully define {name}: "
                        f"{sk.AUTODIM_PROBLEMS.get(result['code'], 'status ' + str(result['code']))}",
                        Err.SKETCH, {"autodim_status": result["code"]})
    added = sk.added_dimensions(result, lambda v: ctx.from_m(v, None), unit_str)
    sizes = sk.added_dimensions(result, lambda v: ctx.from_m(v, None), unit_str, "sizes")
    data.update(size_dimensions=sizes, added_dimensions=added, added_relations=sum(result["relations"].values()),
                relation_kinds=result["relations"], status=result["status"])
    state, what = result["status"], sk.added_text(result, added, sizes) or "nothing added"
    done, free = result["defined"], result["free"]
    if done:
        return ok(f"{name} is fully defined: {what}"
                  + ("" if state == "fully_defined" else " (only construction geometry is free)"), data)
    data.update(free_entities=free["ids"], free_points=free["points"])
    return ok(f"{what}; {name} is still {state.replace('_', '-')}: {sk.free_text(free)}. Add dimensions or "
              f"relations to those (add_sketch_dimension / add_sketch_relation)", data)


# ---------------------------------------------------------------- fit spline

@tool(
    group="sketch",
    description="In the open sketch, replace a chain of lines and arcs with ONE spline that follows them within a tolerance (default 0.001 mm). The entities must form one connected chain; they stay as construction geometry. Use it before extrude_surface when a track has to be a single face that points can be mated to along its whole length.",
    params={'tolerance': {'type': 'number',
                   'description': 'How far the spline may be from the entities (default 0.001 mm)'},
     'unit': {'type': 'string', 'description': 'Unit of the tolerance (mm, cm, m, inch or ft)'}},
    needs='any',
    clears_selection=True,
)
def fit_spline(ctx, tolerance=None, unit=None):
    doc = ctx.doc
    sketch = sk.active_sketch(doc)
    if tolerance is None:
        tol_m = _DEFAULT_FIT_TOLERANCE_M
    else:
        numeric = isinstance(tolerance, (int, float)) and not isinstance(tolerance, bool)
        units.check_size(ctx.to_m(tolerance, unit) if numeric else tolerance, "tolerance")
        tol_m = ctx.to_m(tolerance, unit)
        if tol_m > 0.001:
            raise ToolError("tolerance is more than 1 mm. Use a small value such as 0.001 mm.", Err.INPUT)

    def state():
        rows = []
        for seg in list(_value(sketch, "GetSketchSegments") or []):
            try:
                construction = bool(seg.ConstructionGeometry)
            except Exception:
                construction = False
            rows.append((int(_value(seg, "GetType")), construction, seg))
        return rows

    before = state()
    chain = [row[2] for row in before if not row[1] and row[0] in (0, 1, 3)]
    if not chain:
        raise ToolError("The open sketch has no lines, arcs or splines to fit (construction geometry "
                        "is not used).", Err.SKETCH)
    splines_before = sum(1 for row in before if row[0] == 3)

    sw.common.sw_clear_selection(doc)
    for seg in chain:
        if not sw.sketch_define.sw_select_entity(seg):
            sw.common.sw_clear_selection(doc)
            raise ToolError("Could not select the sketch entities", Err.SELECTION)
    made = sw.sketch_define.sw_fit_spline(doc, tol_m)
    sw.common.sw_clear_selection(doc)

    after = state()
    new_splines = sum(1 for row in after if row[0] == 3) - splines_before
    if not made or new_splines != 1:
        changed = len(after) != len(before)
        if changed:
            sw.sketch_define.sw_undo(doc)
            changed = len(state()) != len(before)
        reason = (f"the entities do not form one connected chain (SolidWorks made {new_splines} splines)"
                  if new_splines > 1 else
                  "SolidWorks could not fit a spline to these entities - they must form one "
                  "connected chain of lines and arcs")
        message = f"No fit spline made: {reason}."
        if changed:
            message += " WARNING: the sketch was changed - check it with list_sketch_entities."
        raise ToolError(message, Err.SKETCH)

    ordered = sk.segments(doc, sketch)
    spline_id = None
    for index, seg in enumerate(ordered, start=1):
        if int(_value(seg, "GetType")) == 3:
            spline_id = index
    unit_str = units.label(unit)
    data = {"spline_id": spline_id, "fitted_entities": len(chain),
            "tolerance": round(ctx.from_m(tol_m, unit), 6), "unit": unit_str, "entities_in_sketch": len(ordered)}
    return ok(f"One spline (entity {spline_id}) now follows the {len(chain)} entities within "
              f"{data['tolerance']} {unit_str}; they are kept as construction geometry. "
              f"extrude_surface turns it into a surface with a single face.", data)
