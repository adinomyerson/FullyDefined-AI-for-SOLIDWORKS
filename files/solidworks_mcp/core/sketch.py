"""
What every sketch tool shares: the open sketch, exact input, entity ids, status, input checks.

Entity ids are positions in the order the entities were drawn. SolidWorks returns the
segments grouped by type (arcs and circles first, then lines), so the order is recorded
here while the sketch is open: an entity keeps its id when more geometry is added.
"""

import logging
import math
from contextlib import contextmanager
from typing import Dict, List, Optional

import pythoncom
import win32com.client

from . import com, units
from .com import byref_int, doc_title, value
from .results import Err, ToolError

logger = logging.getLogger(__name__)

# survives a reload of the module, so entity ids do not change while a sketch is open
KEEP_ON_RELOAD = ("_order",)
_order = {"key": None, "order": []}

# A SolidWorks model must fit in a 1000 m cube centred on the origin
MODEL_LIMIT_M = 500.0
# Sizes below this count as zero
MIN_SIZE_M = 1e-8
# Endpoints closer than this count as the same place
JOIN_TOLERANCE_M = 1e-8

STATUS_NAMES = {1: "unknown", 2: "under_defined", 3: "fully_defined", 4: "over_defined",
                5: "no_solution", 6: "invalid_solution", 7: "autosolve_off"}
BROKEN = ("over_defined", "no_solution", "invalid_solution")

# swSketchFullyDefineRelationType_e: horizontal 2, vertical 4, tangent 8, coincident 512. Never equal, parallel,
# perpendicular, collinear, concentric or midpoint: those carry design intent the user did not give
NEUTRAL_RELATIONS = 2 | 4 | 8 | 512
BASELINE = 1                           # swAutodimSchemeBaseline: every position measured from the origin


def open_sketch(doc):
    """The open sketch, or None."""
    try:
        return doc.SketchManager.ActiveSketch
    except Exception:
        return None


def active_sketch(doc):
    """The open sketch. Without one SolidWorks would silently start a sketch on a plane of
    its own choosing, so the tools refuse instead."""
    sketch = open_sketch(doc)
    if sketch is None:
        raise ToolError("No sketch is open. Open one with create_sketch / create_sketch_on_face first.",
                        Err.SKETCH)
    return sketch


def check_input(points=(), sizes=()) -> None:
    """Refuse geometry that SolidWorks accepts but that breaks the sketch: zero or negative
    sizes, and points outside the modelling space (the sketch could no longer be extruded).
    points: (x, y) in metres. sizes: (label, metres) that must be above zero."""
    for label, size in sizes:
        if not math.isfinite(size) or size < MIN_SIZE_M:
            raise ToolError(f"{label} must be greater than 0", Err.INPUT)
    for x, y in points:
        if not (math.isfinite(x) and math.isfinite(y)) or max(abs(x), abs(y)) > MODEL_LIMIT_M:
            raise ToolError("That is outside SolidWorks' modelling space (at most 500 m from the "
                            "origin in any direction). Check the values and the unit.", Err.INPUT)


@contextmanager
def exact_input(doc):
    """Draw at exactly the coordinates given. Otherwise SolidWorks snaps new points to nearby
    geometry (the snap distance is in screen pixels, so it depends on the zoom) and adds
    automatic relations, which can move a corner. Endpoints at identical coordinates still merge."""
    manager = doc.SketchManager
    try:
        previous = bool(manager.AddToDB)
    except Exception:
        previous = False
    manager.AddToDB = True
    try:
        yield
    finally:
        try:
            manager.AddToDB = previous
        except Exception:
            pass


@contextmanager
def silent_dimensions(app):
    """Stop SolidWorks opening dialogs while a dimension is added (a dialog blocks every call).
    10 swInputDimValOnCreate off; 100 swSketchOverdefiningDimsPromptToSetState off;
    101 swSketchOverdefiningDimsSetDrivenByDefault on (an over-defining dimension becomes driven)."""
    previous = {}
    for toggle, wanted in {10: False, 100: False, 101: True}.items():
        try:
            previous[toggle] = bool(app.GetUserPreferenceToggle(toggle))
            app.SetUserPreferenceToggle(toggle, wanted)
        except Exception as e:
            logger.debug(f"Could not set preference {toggle}: {e}")
    try:
        yield
    finally:
        for toggle, was in previous.items():
            try:
                app.SetUserPreferenceToggle(toggle, was)
            except Exception:
                pass


# Each segment object's key, read once per tool call (a key never changes for the object; the object is kept with it,
# so its id is not reused)
_keys_read = {"call": None, "keys": {}}


def segment_key(seg) -> tuple:
    """What identifies a segment inside its sketch: its type and SolidWorks' per-type id."""
    if _keys_read["call"] != com.call_token():
        _keys_read.update(call=com.call_token(), keys={})
    hit = _keys_read["keys"].get(id(seg))
    if hit is not None and hit[0] is seg:
        return hit[1]
    key = (int(value(seg, "GetType")),) + tuple(int(v) for v in value(seg, "GetID"))
    _keys_read["keys"][id(seg)] = (seg, key)
    return key


# The sketch's segments with their keys, in SolidWorks' order: read once per tool call, again when the count changes
_keyed_read = {"call": None, "name": None, "rows": []}


def _keyed(sketch) -> list:
    """[(segment_key, segment)] of the sketch, in SolidWorks' order."""
    found = list(value(sketch, "GetSketchSegments") or [])
    try:
        name = str(sketch.Name)
    except Exception:
        name = None
    read = _keyed_read
    if read["call"] == com.call_token() and name is not None and read["name"] == name \
            and len(read["rows"]) == len(found):
        return read["rows"]
    rows = []
    for seg in found:
        try:
            rows.append((segment_key(seg), seg))
        except Exception:
            continue
    if len(rows) == len(found):
        read.update(call=com.call_token(), name=name, rows=rows)
    return rows


# The last full listing of a sketch in this tool call: listed again only when the segment count changes (reading
# every segment's type and id is what costs; a tool may ask for the segments several times). Like the other
# once-per-call reads below, it is keyed by com.call_token() and emptied when the call ends (forget_call).
_listing = {"call": None, "key": None, "segs": []}


def segments(doc, sketch, key: tuple = None) -> list:
    """The segments of a sketch in the order they were drawn. Entities not drawn through
    this server are appended, in SolidWorks' order, when first seen. key: (document title, sketch name), when the
    caller has read it."""
    global _order
    found = list(value(sketch, "GetSketchSegments") or [])
    try:
        key = key or (doc_title(doc), sketch.Name)
        if _listing["call"] == com.call_token() and _listing["key"] == key and len(_listing["segs"]) == len(found):
            return list(_listing["segs"])
        by_key = {segment_key(seg): seg for seg in found}
    except Exception as e:
        logger.debug(f"Could not order the sketch segments: {e}")
        return found
    if len(by_key) != len(found):
        return found                    # ids are not unique: fall back to SolidWorks' order
    if _order.get("key") != key:
        _order = {"key": key, "order": []}
    order = [k for k in _order["order"] if k in by_key]            # entities that still exist
    order += [k for k in by_key if k not in order]                 # new ones go to the end
    _order["order"] = order
    ordered = [by_key[k] for k in order]
    _listing.update(call=com.call_token(), key=key, segs=ordered)
    return list(ordered)


@com.at_call_end
def forget_call() -> None:
    """Drop this call's reads (and the COM objects they hold): the next call reads the sketch again."""
    _listing.update(call=None, key=None, segs=[])
    _keyed_read.update(call=None, name=None, rows=[])
    _keys_read.update(call=None, keys={})


def count(doc, sketch) -> int:
    """How many segments the sketch has, before a draw: always from a listing in this call, never from what an
    earlier call saw (the user may have deleted one entity and drawn another in between: same count, other ids)."""
    return len(segments(doc, sketch))


def _register(doc, sketch, before: int, created: list, ordered: bool) -> int:
    """After a draw: the created entities take the next ids, without a full listing when the count is exactly what
    was drawn on top of a known order: in SolidWorks' order (by id: the draws that make several entities make them
    of one type) or, ordered, as given. Returns the count."""
    n = len(value(sketch, "GetSketchSegments") or [])
    if _order.get("key") == (doc_title(doc), sketch.Name) and len(_order["order"]) == before \
            and n == before + len(created):
        keys = [segment_key(c) for c in created]
        if not ordered:
            keys.sort()
        if len(set(keys)) == len(keys) and not set(keys) & set(_order["order"]):
            _order["order"] = _order["order"] + keys
            _listing["call"] = None
            return n
    n = len(segments(doc, sketch))
    if ordered:
        move_to_end([segment_key(c) for c in created])
    return n


def move_to_end(keys: list) -> None:
    """Put entities last in the id order (the polygon registers its sides, then its circle)."""
    if keys and all(k in _order["order"] for k in keys):
        _order["order"] = [k for k in _order["order"] if k not in keys] + keys


def new_entity_ids(doc, sketch, count_before: int, data: Dict, created: list = None, ordered: bool = False) -> str:
    """After a draw call: add the new entities' ids to `data`; returns text for the message. created: the entities
    the call drew (registered without listing the sketch when the count agrees); ordered: their ids follow the
    order given."""
    try:
        count_after = (_register(doc, sketch, count_before, created, ordered) if created
                       else len(segments(doc, sketch)))
    except Exception:
        return ""
    ids = list(range(count_before + 1, count_after + 1))
    if not ids:
        return ""
    if len(ids) == 1:
        data["id"] = ids[0]
        return f" (entity {ids[0]})"
    data["ids"] = ids
    return f" (entities {ids[0]}-{ids[-1]})"


def reference_face(sketch):
    """The model face the sketch lies on, or None (a plane, or no sketch)."""
    try:
        kind = byref_int(0)
        entity = sketch.GetReferenceEntity(kind)
        return entity if int(kind.value) == 2 else None                 # swSelFACES
    except Exception:
        return None


def status(sketch) -> str:
    try:
        code = int(value(sketch, "GetConstrainedStatus"))
        return STATUS_NAMES.get(code, f"status_{code}")
    except Exception:
        return "unknown"


def free_entities(doc, sketch, quick: bool = False) -> Dict:
    """What is not fully defined in a sketch (SolidWorks gives no degrees-of-freedom count, only each entity's own
    status): {"ids": free entity ids (list_sketch_entities' ids), "points": free points that are not an entity's
    end or centre (draw_point)}. Construction geometry does not count (fit_spline leaves its helper lines free, and
    the spline itself is defined), except a mirror line: that shapes the part. quick: stop at the first free entity
    (enough to know the sketch is not defined; the result then lists only that one)."""
    segs = segments(doc, sketch)
    ids, construction = [], []
    for index, seg in enumerate(segs, 1):
        try:
            if int(value(seg, "Status")) != 2:                             # swUnderConstrained
                continue
            if bool(value(seg, "ConstructionGeometry")):
                construction.append((index, seg))
            else:
                ids.append(index)
                if quick:
                    return {"ids": ids, "points": 0, "kinds": {index: _kind(seg)}, "partial": True}
        except Exception as e:
            logger.debug(f"Could not read entity {index}'s status: {e}")
    if construction:                                    # a free construction line counts only as a mirror line
        mirrors = mirror_lines(sketch)
        for index, seg in construction:
            try:
                if segment_key(seg) in mirrors:
                    ids.append(index)
            except Exception as e:
                logger.debug(f"Could not read entity {index}'s id: {e}")
    ids.sort()
    kinds = {index: _kind(segs[index - 1]) for index in ids}
    free_points = []
    for point in list(value(sketch, "GetSketchPoints2") or []):
        try:
            if int(value(point, "Status")) == 2:
                free_points.append((float(point.X), float(point.Y)))
        except Exception:
            continue
    points = 0
    if free_points:                                     # a free point that is not an entity's end or centre
        ends = []
        for seg in segs:
            for getter in ("GetStartPoint2", "GetEndPoint2", "GetCenterPoint2"):
                try:
                    point = value(seg, getter)
                    if point is not None:
                        ends.append((float(point.X), float(point.Y)))
                except Exception:
                    continue
        points = sum(1 for here in free_points
                     if not any(math.dist(here, other) <= JOIN_TOLERANCE_M for other in ends))
    return {"ids": ids, "points": points, "kinds": kinds}


def _kind(seg) -> str:
    """line, circle, arc, spline or entity: for messages."""
    try:
        kind = int(value(seg, "GetType"))
        if kind == 0:
            return "line"
        if kind == 1:
            a, b = value(seg, "GetStartPoint2"), value(seg, "GetEndPoint2")
            closed = a is None or b is None or math.dist((a.X, a.Y), (b.X, b.Y)) < JOIN_TOLERANCE_M
            return "circle" if closed else "arc"
        return {3: "spline", 2: "ellipse"}.get(kind, "entity")
    except Exception:
        return "entity"


def mirror_lines(sketch) -> set:
    """Keys (segment_key) of the lines a symmetric relation mirrors about: construction geometry that shapes the
    part, so it must be defined like the rest."""
    keys = set()
    try:
        for relation in list(sketch.RelationManager.GetRelations(0) or []):
            if int(value(relation, "GetRelationType")) != 11:            # swConstraintType_SYMMETRIC
                continue
            for entity in list(value(relation, "GetEntities") or []):
                try:
                    if bool(value(entity, "ConstructionGeometry")):
                        keys.add(segment_key(entity))
                except Exception:
                    continue                                            # a point, not a segment
    except Exception as e:
        logger.debug(f"Could not read the symmetric relations: {e}")
    return keys


def free_centerlines(sketch) -> int:
    """How many construction lines are still free: a revolve axis among them must be defined too (it shapes the part),
    so fully defining does not stop at "defined" while one is free. (A fit_spline helper line is defined with it:
    harmless.)"""
    n = 0
    for seg in list(value(sketch, "GetSketchSegments") or []):
        try:
            if bool(value(seg, "ConstructionGeometry")) and _kind(seg) == "line" and int(value(seg, "Status")) == 2:
                n += 1
        except Exception:
            continue
    return n


def defined(doc, sketch, quick: bool = False) -> tuple:
    """(is it defined, its free entities): fully defined, or under-defined only in construction geometry. quick: see
    free_entities (the answer is exact; the free entities may be only the first)."""
    state = status(sketch)
    if state == "fully_defined":
        return True, {"ids": [], "points": 0}
    if state != "under_defined":
        return False, {"ids": [], "points": 0}
    free = free_entities(doc, sketch, quick) if quick else free_entities(doc, sketch)
    return not free["ids"] and not free["points"], free


AUTODIM_PROBLEMS = {6: "the sketch is empty", 7: "the sketch is over-defined", 8: "it has no entities to define",
                    9: "its entities cannot be dimensioned", 17: "SolidWorks' algorithm failed",
                    18: "no solution was found"}
# swConstraintType_e: the relations fully defining adds, by name (25 / 26 are horizontal / vertical between points)
RELATION_NAMES = {4: "horizontal", 5: "vertical", 6: "tangent", 7: "parallel", 8: "perpendicular", 9: "coincident",
                  10: "concentric", 11: "symmetric", 12: "midpoint", 14: "equal", 25: "horizontal", 26: "vertical"}
DIMENSION_TYPES = {1, 2, 3, 15}         # distance, angle, radius, diameter: dimensions, not relations


def _relation_kinds(sketch) -> Dict[str, int]:
    kinds: Dict[str, int] = {}
    try:
        for relation in list(sketch.RelationManager.GetRelations(0) or []):
            kind = int(value(relation, "GetRelationType"))
            if kind not in DIMENSION_TYPES:
                name = RELATION_NAMES.get(kind, f"type {kind}")
                kinds[name] = kinds.get(name, 0) + 1
    except Exception as e:
        logger.debug(f"Could not read the relations: {e}")
    return kinds


def _dimension_rows(sketch) -> List[tuple]:
    """(full name, SolidWorks value: metres or radians, is it an angle) for the sketch's own dimensions."""
    from .sw import editing as sw_editing               # here: core.sw loads after this module
    rows, own = [], f"@{sketch.Name}@"
    try:
        display, seen = value(sketch, "GetFirstDisplayDimension"), 0
        while display is not None and seen < 500:
            seen += 1
            dim = sw_editing.sw_display_dimension(display)
            if own in dim.FullName:
                rows.append((str(dim.FullName), float(dim.SystemValue), int(value(dim, "GetType")) == 1))
            display = sketch.GetNextDisplayDimension(display)
    except Exception as e:
        logger.debug(f"Could not list the dimensions: {e}")
    return rows


# The sizes the draw tools leave to dimension when the sketch is closed or defined (a rectangle's width and height,
# a circle's diameter, an arc's radius, a polygon's circle), per sketch: dimensioned then, not when drawn, so the
# user can still set the size or relate the shape (equal, symmetric...) without over-defining it first.
_pending_sizes: Dict[tuple, list] = {}


def pend_size(doc, sketch, app, entity, x_m: float, y_m: float, lone: bool = False) -> None:
    """Remember a drawn shape's size to dimension later (its text at x, y in metres). lone: a line's length, kept
    only if the line still touches nothing when the sketch is closed (in a chain its length would be derived)."""
    try:
        key = (doc_title(doc), str(sketch.Name))
        _pending_sizes.setdefault(key, []).append({"key": segment_key(entity), "x": x_m, "y": y_m, "app": app,
                                                   "lone": lone})
    except Exception as e:
        logger.debug(f"Could not remember the shape's size: {e}")


def apply_sizes(doc, sketch) -> List[tuple]:
    """Dimension the remembered sizes that are still free; one that would over-define (the user set it, or a
    relation fixes it) comes out driven and is taken back. Returns (name, metres) per dimension kept."""
    from .sw import common as sw_common, editing as sw_editing, sketch_define as sw_define
    try:
        items = _pending_sizes.pop((doc_title(doc), str(sketch.Name)), [])
    except Exception:
        return []
    by_key = {segment_key(seg): seg for seg in segments(doc, sketch)} if items else {}
    kept, taken = [], None
    for item in items:
        entity = by_key.get(item["key"])
        if entity is None or (item.get("lone") and _joined(entity, by_key.values())):
            continue
        sw_common.sw_clear_selection(doc)
        if not sw_define.sw_select_entity(entity):
            continue
        with silent_dimensions(item["app"]):
            display = sw_define.sw_add_dimension(doc, "auto", item["x"], item["y"])
        sw_common.sw_clear_selection(doc)
        if display is None:
            continue
        dim = sw_editing.sw_display_dimension(display)
        name = str(dim.FullName).split("@")[0]
        if int(dim.DrivenState) == 1:                                   # swDimensionDriven: it over-defined
            sw_define.sw_delete_dimension(doc, display, name)
            continue
        if taken is None:                                               # read once: then ours are added
            taken = {r[0].split("@")[0] for r in _dimension_rows(sketch)}
        kept.append((_name_drawn(dim, sketch, taken), float(dim.SystemValue)))     # ours: DrawnN
    return kept


# The dimensions apply_sizes adds are named Drawn1, Drawn2... in the file: they are ours, not the user's, so a later
# dimension or relation that conflicts with one replaces it instead of being refused.
DRAWN = "Drawn"


def _name_drawn(dim, sketch, taken: set = None) -> str:
    """Rename a new size dimension DrawnN (the first free N in the sketch); returns its short name. taken: the
    sketch's dimension names, read once by the caller and kept up to date here."""
    if taken is None:
        taken = {r[0].split("@")[0] for r in _dimension_rows(sketch)}
    taken.add(str(dim.FullName).split("@")[0])
    n = 1
    while f"{DRAWN}{n}" in taken:
        n += 1
    try:
        dim.Name = f"{DRAWN}{n}"
    except Exception as e:
        logger.debug(f"Could not rename the size dimension: {e}")
    short = str(dim.FullName).split("@")[0]
    taken.add(short)
    return short


def claim_drawn(doc, dim) -> tuple:
    """A Drawn size the user has just set (set_dimension) is the user's from now on: renamed to the first free DN of
    its sketch, so a later relation or dimension that conflicts with it is refused instead of replacing it. dim is
    the IDimension; its own full name decides (the name typed may differ in case). Returns (was it ours, the new
    full name or None when the rename failed)."""
    full_name = str(dim.FullName)
    short, _, rest = full_name.partition("@")
    if not short.startswith(DRAWN):
        return False, None
    try:
        sketch = value(doc.FeatureByName(rest.split("@")[0]), "GetSpecificFeature2")
        taken = {r[0].split("@")[0] for r in _dimension_rows(sketch)}
        n = 1
        while f"D{n}" in taken:
            n += 1
        dim.Name = f"D{n}"
        renamed = str(dim.FullName)
        return True, (renamed if not renamed.startswith(DRAWN) else None)
    except Exception as e:
        logger.debug(f"Could not rename {full_name}: {e}")
        return True, None


def ref_keys(doc, sketch, refs) -> set:
    """segment_key of the entities a tool's references ('3', '3:start'; 'origin' has none) point at."""
    segs, keys = segments(doc, sketch), set()
    for ref in refs if isinstance(refs, (list, tuple)) else [refs]:
        head = str(ref).split(":")[0].strip()
        if head.isdigit() and 1 <= int(head) <= len(segs):
            keys.add(segment_key(segs[int(head) - 1]))
    return keys


def _touches(entity, keys: set, ends: list) -> bool:
    """Is this relation entity one of the keyed segments, or a point at one of their ends or centres? (A line's
    length dimension holds its two end points, not the line.)"""
    try:
        return segment_key(entity) in keys
    except Exception:
        pass
    try:
        here = (float(entity.X), float(entity.Y))
        return any(math.dist(here, p) <= JOIN_TOLERANCE_M for p in ends)
    except Exception:
        return False


def _key_points(sketch, keys: set) -> list:
    """(x, y) of the ends and centres of the keyed segments."""
    ends = []
    for key, seg in _keyed(sketch):
        if key not in keys:
            continue
        for getter in ("GetStartPoint2", "GetEndPoint2", "GetCenterPoint2"):
            try:
                p = value(seg, getter)
                if p is not None:
                    ends.append((float(p.X), float(p.Y)))
            except Exception:
                continue
    return ends


def dimensions_on(sketch, keys: set) -> List[tuple]:
    """(display dimension, short name, value in metres or radians, is it ours: Drawn) for the dimensions of the
    sketch that measure one of these entities."""
    out = []
    ends = _key_points(sketch, keys)
    try:
        for relation in list(sketch.RelationManager.GetRelations(0) or []):
            if int(value(relation, "GetRelationType")) not in DIMENSION_TYPES:
                continue
            touched = any(_touches(e, keys, ends) for e in list(value(relation, "GetEntities") or []))
            if not touched:
                continue
            display = value(relation, "GetDisplayDimension")
            dim = display.GetDimension2(0)
            short = str(dim.FullName).split("@")[0]
            out.append((display, short, float(dim.SystemValue), short.startswith(DRAWN)))
    except Exception as e:
        logger.debug(f"Could not read the dimensions on the entities: {e}")
    return out


def conflicts_on(ctx_from_m, unit_str: str, sketch, keys: set) -> List[str]:
    """The user's own dimensions ("D3 = 40 mm") and relations ("equal") on these entities, for a refusal."""
    out = [f"{nm} = {round(ctx_from_m(v), 4)}{unit_str}" for _, nm, v, ours in dimensions_on(sketch, keys) if not ours]
    ends = _key_points(sketch, keys)
    try:
        for relation in list(sketch.RelationManager.GetRelations(0) or []):
            kind = int(value(relation, "GetRelationType"))
            if kind in DIMENSION_TYPES or kind not in RELATION_NAMES:
                continue
            if any(_touches(e, keys, ends) for e in list(value(relation, "GetEntities") or [])) and f"the {RELATION_NAMES[kind]} relation" not in out:
                out.append(f"the {RELATION_NAMES[kind]} relation")
    except Exception as e:
        logger.debug(f"Could not read the relations on the entities: {e}")
    return out


def give_way(app, doc, sketch, keys: set, attempt, replaced: list) -> Optional[tuple]:
    """A new dimension or relation on these entities over-defined the sketch (and was taken out again). Remove our
    own size dimensions (Drawn) on them one at a time and try again with attempt() (True when it now fits); a
    removal that does not help is put back. Returns (name, metres) of the one removed, or None; what was removed is
    appended to replaced, for restore_drawn if the caller's call is refused later."""
    from .sw import sketch_define as sw_define
    near = [row for row in dimensions_on(sketch, keys) if row[3]]
    every = [row for row in dimensions_on(sketch, {k for k, _ in _keyed(sketch)})
             if row[3] and row[1] not in {r[1] for r in near}]
    for display, name, size, ours in near + every:
        # what it takes to put it back: the entity it measures and where its text sits
        # the segment it sizes: itself (a circle's diameter) or the one whose ends it holds (a line's length)
        try:
            measured = _relation_entities(sketch, name)
            all_keys = {k for k, _ in _keyed(sketch)}
            # the segment that holds all of it: a circle itself, or the line whose two ends it measures (a
            # corner point also touches the next line)
            entity_key = next(k for k in all_keys if measured
                              and all(_touches(e, {k}, _key_points(sketch, {k})) for e in measured))
            at = [float(v) for v in value(value(display, "GetAnnotation"), "GetPosition")]
        except Exception:
            continue
        item = {"key": entity_key, "at": at, "name": name, "size": size}
        sw_define.sw_delete_dimension(doc, display, name)
        try:
            if attempt():
                replaced.append(item)
                return name, size
        except ToolError:
            pass
        restore_drawn(app, doc, sketch, item)                   # it did not help: it goes back
    return None


def restore_drawn(app, doc, sketch, item: Dict) -> bool:
    """Put a removed Drawn size back on its entity, under its name, its text where it was."""
    from .sw import common as sw_common, sketch_define as sw_define
    entity = {segment_key(s): s for s in segments(doc, sketch)}.get(item["key"])
    if entity is None:
        return False
    sw_common.sw_clear_selection(doc)
    if not sw_define.sw_select_entity(entity):
        return False
    with silent_dimensions(app):
        back = sw_define.sw_add_dimension(doc, "auto", item["at"][0], item["at"][1])
    sw_common.sw_clear_selection(doc)
    if back is None:
        return False
    from .sw import editing as sw_editing
    try:
        dim = back.GetDimension2(0)
        dim.Name = item["name"]
        if item.get("size") is not None:
            sw_editing.sw_set_dimension_value(dim, item["size"])
    except Exception:
        pass
    return True


def _relation_entities(sketch, dimension_name: str) -> list:
    """The entities the dimension with this short name measures (from its sketch relation)."""
    for relation in list(sketch.RelationManager.GetRelations(0) or []):
        try:
            if int(value(relation, "GetRelationType")) not in DIMENSION_TYPES:
                continue
            display = value(relation, "GetDisplayDimension")
            if str(display.GetDimension2(0).FullName).split("@")[0] == dimension_name:
                return list(value(relation, "GetEntities") or [])
        except Exception:
            continue
    return []



def _joined(line, segs) -> bool:
    """Does either end of this line touch another entity's end or centre?"""
    try:
        ends = [(float(p.X), float(p.Y)) for p in (value(line, "GetStartPoint2"), value(line, "GetEndPoint2"))]
    except Exception:
        return True
    me = segment_key(line)
    for seg in segs:
        if segment_key(seg) == me:
            continue
        for getter in ("GetStartPoint2", "GetEndPoint2", "GetCenterPoint2"):
            try:
                p = value(seg, getter)
                if p is not None and any(math.dist(e, (float(p.X), float(p.Y))) <= JOIN_TOLERANCE_M for e in ends):
                    return True
            except Exception:
                continue
    return False


def sizes_pending(doc, sketch) -> bool:
    try:
        return bool(_pending_sizes.get((doc_title(doc), str(sketch.Name))))
    except Exception:
        return False


def forget_sizes(doc, sketch) -> None:
    try:
        _pending_sizes.pop((doc_title(doc), str(sketch.Name)), None)
    except Exception:
        pass


def auto_define(doc, sketch) -> Dict:
    """Fully define the open sketch: first the drawn shapes' own sizes that are still free (the user's numbers),
    then the rest from the origin (neutral relations, baseline dimensions), and say exactly what each added:
    {"sizes": [(name, metres, False)], "code": swAutodimStatus_e (0 = done), "dimensions": [(name, metres or
    radians, angle?)] SolidWorks added, "relations": {kind: count}, "status", "defined", "free"}."""
    from .sw import sketches as sw_sketches
    sizes = [(n, v, False) for n, v in apply_sizes(doc, sketch)]
    before, kinds_before = {r[0] for r in _dimension_rows(sketch)}, _relation_kinds(sketch)
    code = 0
    if not defined(doc, sketch)[0] or free_centerlines(sketch):
        code = sw_sketches.sw_fully_define(doc, NEUTRAL_RELATIONS, BASELINE)
    sketch = open_sketch(doc) or sketch
    kinds_after = _relation_kinds(sketch)
    done, free = defined(doc, sketch)
    return {"sizes": sizes, "code": code,
            "dimensions": [(r[0].split("@")[0], r[1], r[2]) for r in _dimension_rows(sketch) if r[0] not in before],
            "relations": {k: n - kinds_before.get(k, 0) for k, n in sorted(kinds_after.items())
                          if n > kinds_before.get(k, 0)},
            "status": status(sketch), "defined": done, "free": free}


def added_dimensions(result: Dict, from_m, unit_str: str, key: str = "dimensions") -> List[dict]:
    """The result's new dimensions (key "dimensions": SolidWorks', "sizes": the drawn sizes) as {name, value, unit}:
    lengths through from_m (the caller's unit), angles in degrees."""
    return [{"name": n, "value": round(math.degrees(v), 4) if angle else round(from_m(v), 4),
             "unit": "deg" if angle else unit_str} for n, v, angle in result[key]]


def listed(dims: List[dict]) -> str:
    return ", ".join(f"{d['name']} = {d['value']} {d['unit']}" for d in dims)


def added_text(result: Dict, dims: List[dict], sizes: List[dict] = ()) -> str:
    """'its drawn sizes became D1 = 40 mm, D2 = 20 mm; SolidWorks added 2 dimensions (D3 = 10 mm, D4 = 5 mm) and
    3 relations (coincident x1, horizontal x2) from the origin'. dims / sizes as {name, value, unit}."""
    parts = []
    if sizes:
        parts.append(f"its drawn sizes became {listed(sizes)}")
    n, rel = len(dims), sum(result["relations"].values())
    if dims or rel:
        text = (f"added by SolidWorks, not from your spec: {n} dimension{'' if n == 1 else 's'} from the "
                f"origin" + (f" ({listed(dims)})" if dims else ""))
        text += f" and {rel} relation{'' if rel == 1 else 's'}"
        if rel:
            text += " (" + ", ".join(f"{k} x{v}" for k, v in result["relations"].items()) + ")"
        parts.append(text)
    return "; ".join(parts)


# What the last automatic define (a feature tool closing an open part sketch) added, so the feature's result can
# list it: {"sketch": name, "text": added_text}
_last_define: Dict = {}


def define_before_closing(doc, sketch) -> None:
    """A feature tool is about to close the open sketch of a part: its drawn sizes become dimensions where still
    free, as close_sketch does (no dimensions from the origin: what else is free, require_defined reports), and
    what was added is kept for require_defined to report."""
    _last_define.clear()
    try:
        if int(value(doc, "GetType")) != 1 or defined(doc, sketch)[0] or status(sketch) != "under_defined":
            forget_sizes(doc, sketch)
            return
        sizes = [{"name": n, "value": round(units.from_m(v), 4), "unit": units.label(None)}
                 for n, v in apply_sizes(doc, sketch)]
        if sizes:
            _last_define.update(sketch=str(sketch.Name), text=f"its drawn sizes became {listed(sizes)}")
    except Exception as e:
        logger.debug(f"Could not dimension the drawn sizes before closing: {e}")


def require_defined(doc, name: str) -> str:
    """Refuse to build a part feature on a sketch that is not fully defined (construction geometry aside), naming
    what is free. Returns a note for the feature's result when the sketch was defined automatically just before
    ("" otherwise). Assemblies are not checked: their layout sketches are the user's call."""
    if int(value(doc, "GetType")) != 1:
        return ""
    feat = doc.FeatureByName(name)
    sketch = value(feat, "GetSpecificFeature2") if feat is not None else None
    if sketch is None:
        return ""
    done, free = defined(doc, sketch)
    if not done:
        state = status(sketch)
        what = free_text(free) if state == "under_defined" else state.replace("_", "-")
        kinds = set(free.get("kinds", {}).values())
        how = []
        unsized = sizes_missing(doc, sketch, free)
        if unsized:
            how.append(f"no size yet: {', '.join(unsized)}: add_sketch_dimension on that entity with the user's "
                       f"size")
        if kinds & {"circle", "arc", "ellipse"}:
            how.append("a circle's or arc's centre: dimension it from an edge ('edge:x,y,z') or a sketch line, or "
                       "make it coincident with 'origin'")
        if "line" in kinds:
            how.append("a line: dimension its position from an edge or the origin, or relate it (coincident, "
                       "collinear)")
        if free["points"]:
            how.append("a point: dimension it from an edge or the origin")
        try:
            first = not list(doc.GetBodies2(0, False) or [])                # no solid yet: the part's first sketch
        except Exception:
            first = False
        if first:
            how.insert(0, "this is the part's first sketch: put one point on the origin (the centre if the part is "
                          "symmetric, else a corner), coincident with 'origin', or draw it there")
        fix = ("; ".join(how) + ". ") if how else ""
        raise ToolError(f"Not built: {name} is not fully defined ({what}). In a part every sketch is fully defined "
                        f"before its feature. To fix: edit_sketch('{name}'), then {fix}Use the references the user "
                        f"named (add_sketch_dimension, add_sketch_relation); fully_define_sketch only as a last "
                        f"resort (tell the user what it added); close_sketch, and try again.",
                        Err.SKETCH, {"sketch": name, "status": state, "free_entities": free["ids"],
                                     "free_points": free["points"]})
    if _last_define.get("sketch") == name:
        return f". {name}: {_last_define['text']}"
    return ""


def sizes_missing(doc, sketch, free: Dict) -> List[str]:
    """The free circles, arcs and lines with no size: a circle or arc without a radius or diameter; a line when no
    line of its chain (lines joined end to end) has a length (a distance on the line alone or between its two ends)
    and no distance between two lines touches the chain (a rectangle's sides take their lengths from its two sizes
    through its relations; SolidWorks gives no degrees of freedom per entity to tell them apart). An equal relation
    passes a size on only from a member that has one. A distance to anything else (an edge, a point, the origin) is
    a position, not a size. ["circle 2's diameter", "line 3's length"], for a refusal's hint: when unsure, a shape
    counts as sized (the hint must never ask for a size the user already gave)."""
    segs = segments(doc, sketch)
    sized, equal_groups = set(), []
    try:
        for relation in list(sketch.RelationManager.GetRelations(0) or []):
            kind = int(value(relation, "GetRelationType"))
            if kind not in (1, 3, 14, 15):                          # distance, radius, equal, diameter
                continue
            keys, points, others = set(), [], 0
            for e in list(value(relation, "GetEntities") or []):
                try:
                    keys.add(segment_key(e))
                except Exception:
                    try:
                        points.append((float(e.X), float(e.Y)))
                    except Exception:
                        others += 1                                 # a model edge or face: a position
            if kind in (3, 15):
                sized |= keys
            elif kind == 14:
                equal_groups.append(keys)
            elif others or (keys and points):
                continue
            elif keys:                                              # one line's length, or between two lines
                sized |= keys
            elif len(points) == 2:                                  # between two points: a line's two ends
                for seg in (s for s in segs if _kind(s) == "line"):
                    ends = _key_points(sketch, {segment_key(seg)})
                    if len(ends) >= 2 and all(any(math.dist(p, q) <= JOIN_TOLERANCE_M for q in ends[:2])
                                              for p in points):
                        sized.add(segment_key(seg))
    except Exception as e:
        logger.debug(f"Could not read the sizes: {e}")
        return []
    # a chain of lines shares its sizes, and an equal relation passes one on
    lines = [s for s in segs if _kind(s) == "line"]
    ends = {segment_key(s): _key_points(sketch, {segment_key(s)})[:2] for s in lines}
    grown = True
    while grown:
        grown = False
        for group in equal_groups:
            if group & sized and not group <= sized:
                sized |= group
                grown = True
        for k, pts in ends.items():
            if k in sized:
                continue
            if any(j in sized and any(math.dist(p, q) <= JOIN_TOLERANCE_M for p in pts for q in other)
                   for j, other in ends.items() if j != k):
                sized.add(k)
                grown = True
    out = []
    for i in free.get("ids", []):
        what = free.get("kinds", {}).get(i)
        if what not in ("circle", "arc", "line") or not 1 <= i <= len(segs) or segment_key(segs[i - 1]) in sized:
            continue
        out.append(f"{what} {i}'s " + {"circle": "diameter", "arc": "radius", "line": "length"}[what])
    return out


def feature_sketch_state(feat) -> Dict:
    """What a feature's own sketches hold (a hole's placement, a flange's profile): {"dims": {"D1@Sketch3": (metres
    or radians, is it an angle)}, "relations": {kind: count}}. Two readings, one before and one after
    define_feature_sketches, say what defining added (added_rows)."""
    dims, kinds = {}, {}
    sub = value(feat, "GetFirstSubFeature")
    while sub is not None:
        if value(sub, "GetTypeName2") == "ProfileFeature":
            sketch = value(sub, "GetSpecificFeature2")
            for name, v, angle in _dimension_rows(sketch):
                dims[name.split("@")[0] + "@" + str(sub.Name)] = (v, angle)
            for kind, n in _relation_kinds(sketch).items():
                kinds[kind] = kinds.get(kind, 0) + n
        sub = value(sub, "GetNextSubFeature")
    return {"dims": dims, "relations": kinds}


def added_rows(before: Dict, after: Dict, from_m, unit_str: str) -> tuple:
    """What defining a feature's sketches added, from two feature_sketch_state readings: ([{name, sketch, value,
    unit}] with lengths through from_m and angles in degrees, {relation kind: count})."""
    rows = [{"name": n.split("@")[0], "sketch": n.split("@")[1],
             "value": round(math.degrees(v), 4) if angle else round(from_m(v), 4),
             "unit": "deg" if angle else unit_str}
            for n, (v, angle) in after["dims"].items() if n not in before["dims"]]
    relations = {k: n - before["relations"].get(k, 0) for k, n in sorted(after["relations"].items())
                 if n > before["relations"].get(k, 0)}
    return rows, relations


def define_feature_sketches(doc, feat, states: List[tuple] = None) -> List[tuple]:
    """Fully define, from the origin, every sketch a new feature made for itself and left under-defined (a Hole
    Wizard placement point, an edge flange's profile): each one is opened, defined and closed, which rebuilds the
    feature. Returns (sketch, status after) per sketch of the feature. states: (sketch, status) per sketch of the
    feature, when the caller has read them already."""
    from .sw import sketches as sw_sketches            # here: core.sw loads after this module
    if states is None:
        states = []
        sub = value(feat, "GetFirstSubFeature")
        while sub is not None:
            if value(sub, "GetTypeName2") == "ProfileFeature":
                states.append((str(sub.Name), status(value(sub, "GetSpecificFeature2"))))
            sub = value(sub, "GetNextSubFeature")
    out = []
    for name, state in states:
        if state == "under_defined" and sw_sketches.sw_edit_sketch(doc, name):
            try:
                sw_sketches.sw_fully_define(doc, NEUTRAL_RELATIONS, BASELINE)
            except ToolError as e:
                logger.debug(f"Could not define {name}: {e.message}")
            finally:
                sw_sketches.sw_close_sketch(doc)
        out.append((name, status(value(doc.FeatureByName(name), "GetSpecificFeature2"))))
    return out


def define_and_report(doc, feat, from_m, unit_str: str) -> tuple:
    """define_feature_sketches with what it added: (sketch -> status after, added_rows' rows, relations). The
    sketches' dimensions and relations are read before and after only when one of them is under-defined (otherwise
    nothing is added)."""
    states = []
    sub = value(feat, "GetFirstSubFeature")
    while sub is not None:
        if value(sub, "GetTypeName2") == "ProfileFeature":
            states.append((str(sub.Name), status(value(sub, "GetSpecificFeature2"))))
        sub = value(sub, "GetNextSubFeature")
    if all(state != "under_defined" for _, state in states):
        return dict(states), [], {}                     # nothing to define: as define_feature_sketches would report
    before = feature_sketch_state(feat)
    sketches = dict(define_feature_sketches(doc, feat, states))
    rows, relations = added_rows(before, feature_sketch_state(feat), from_m, unit_str)
    return sketches, rows, relations


def free_text(free: Dict) -> str:
    """'2 free entities (ids 3, 5: line 3, circle 5) and 1 free point', for messages."""
    parts = []
    if free["ids"]:
        n = len(free["ids"])
        named = ", ".join(f"{free.get('kinds', {}).get(i, 'entity')} {i}" for i in free["ids"])
        parts.append(f"{n} free {'entity' if n == 1 else 'entities'} (id{'s' if n > 1 else ''} "
                     f"{', '.join(map(str, free['ids']))}: {named})")
    if free["points"]:
        parts.append(f"{free['points']} free point{'s' if free['points'] > 1 else ''}")
    return " and ".join(parts) or "free geometry"


def relation_count(sketch) -> int:
    try:
        return int(sketch.RelationManager.GetRelationsCount(0))     # 0 = all
    except Exception:
        return -1


# A drawn point this close to (0, 0) may be on the origin: the entity's own points are read then
ORIGIN_NEAR_M = 1e-6


def round_points(cx: float, cy: float, r: float) -> list:
    """The points to pass tie_to_origin as drawn for a circle or arc: its centre, and the origin itself when the
    origin is on its curve (SolidWorks puts a circle's start point somewhere on it)."""
    on_curve = abs(math.hypot(cx, cy) - r) <= ORIGIN_NEAR_M
    return [(cx, cy)] + ([(0.0, 0.0)] if on_curve else [])


def tie_to_origin(doc, sketch, entities, centre=None, drawn=None) -> bool:
    """In a part, a shape drawn on the sketch origin is tied to it (a first sketch has one point on
    the origin): a point of these entities exactly at (0, 0) gets coincident with the origin; centre=(a, b), two
    opposite corners of a rectangle whose centre is (0, 0), gets a construction diagonal with its midpoint on the
    origin. A tie that would over-define the sketch is taken back. True when tied. drawn: the points (metres) the
    call drew, its ends and centres; when none is near the origin nothing is read."""
    from .sw import common as sw_common
    if centre is None and drawn is not None and not any(math.hypot(x, y) <= ORIGIN_NEAR_M for x, y in drawn):
        return False
    try:
        if int(value(doc, "GetType")) != 1:
            return False
    except Exception:
        return False
    null = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
    state = status(sketch)

    def relate(target, constraint) -> bool:
        doc.ClearSelection2(True)
        if not (target.Select4(False, null) and sw_common.sw_select_named(doc, "Point1@Origin", "EXTSKETCHPOINT", 0)):
            doc.ClearSelection2(True)
            return False
        doc.SketchAddConstraints(constraint)
        doc.ClearSelection2(True)
        if status(sketch) in BROKEN and state not in BROKEN:
            doc.EditUndo2(1)                                        # already tied there: keep the sketch as it was
            return False
        return True

    if centre is not None:
        a, b = centre
        with exact_input(doc):
            diagonal = doc.SketchManager.CreateLine(a[0], a[1], 0, b[0], b[1], 0)
        if diagonal is None:
            return False
        diagonal.ConstructionGeometry = True
        return relate(diagonal, "sgATMIDDLE")
    for entity in entities or []:
        for getter in ("GetStartPoint2", "GetEndPoint2", "GetCenterPoint2"):
            try:
                p = value(entity, getter)
            except Exception:
                continue
            if p is not None and math.hypot(float(p.X), float(p.Y)) <= JOIN_TOLERANCE_M:
                return relate(p, "sgCOINCIDENT")
    return False


def add_horizontal_vertical(doc, segs) -> int:
    """Exact input adds no automatic relations: make axis-aligned lines horizontal / vertical."""
    added = 0
    null = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
    for seg in segs or []:
        try:
            if value(seg, "GetType") != 0:                          # 0 = line
                continue
            a, b = value(seg, "GetStartPoint2"), value(seg, "GetEndPoint2")
            dx, dy = abs(b.X - a.X), abs(b.Y - a.Y)
            if dy < 1e-9 and dx > 1e-9:
                constraint = "sgHORIZONTAL2D"
            elif dx < 1e-9 and dy > 1e-9:
                constraint = "sgVERTICAL2D"
            else:
                continue
            doc.ClearSelection2(True)
            if seg.Select4(False, null):
                doc.SketchAddConstraints(constraint)
                added += 1
        except Exception as e:
            logger.debug(f"Could not add a horizontal / vertical relation: {e}")
    doc.ClearSelection2(True)
    return added
