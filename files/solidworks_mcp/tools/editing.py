"""Looking at and changing an existing model: the feature tree and dimensions by name."""

import logging
import math

from ..core.api import assembly, doc_type, Err, model, ok, sketch as sk, tool, ToolError, units, value as com_value
from ..core.api import sw

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- SolidWorks calls (delete_feature)



ANGLE, COUNT = 1, 2                     # Dimension.GetType as SolidWorks 2026 returns it (S3.3 probe: a count is 2)


def _driven_by(equations: list, name: str):
    """The equation whose left side is this dimension ('"D1@Sketch1" = ...'), or None."""
    want = f'"{name.lower()}"'
    for text in equations:
        left = text.split("=", 1)[0].strip().lower()
        if left == want or left.startswith(want[:-1] + "@"):           # also "D1@Sketch1@Part1"
            return text
    return None


def _feature_dimensions(ctx, feat, unit) -> list:
    """The dimensions that belong to one feature (not those of its sketch)."""
    dims = []
    try:
        own = f"@{feat.Name}@"
        display = com_value(feat, "GetFirstDisplayDimension")
        seen = 0
        while display is not None and seen < 500:
            seen += 1
            dim = sw.editing.sw_display_dimension(display)
            full = dim.FullName
            if own in full:
                raw = float(dim.SystemValue)
                kind = int(com_value(dim, "GetType"))           # 1 angle, 3 a count (S3.3), else a length
                dims.append({"name": "@".join(full.split("@")[:2]),         # e.g. D1@Sketch1
                             "value": round(math.degrees(raw), 4) if kind == ANGLE else int(round(raw))
                             if kind == COUNT else round(ctx.from_m(raw, unit), 4),
                             "kind": "angle_deg" if kind == ANGLE else "count" if kind == COUNT else "length",
                             "driven": int(dim.DrivenState) == 1})
            display = feat.GetNextDisplayDimension(display)
    except Exception as e:
        logger.debug(f"Could not read the dimensions of a feature: {e}")
    return dims


@tool(
    group="features",
    description="List the features of the active model as the feature tree shows them.",
    needs='any',
)
def list_features(ctx):
    doc = ctx.doc
    features = []
    feat = com_value(doc, "FirstFeature")
    while feat is not None and len(features) < 5000:
        try:
            name = feat.Name
        except Exception:
            name = "<unknown>"
        try:
            kind = com_value(feat, "GetTypeName2")
        except Exception:
            kind = "<unknown>"
        try:
            suppressed = bool(com_value(feat, "IsSuppressed"))
        except Exception:
            suppressed = False
        features.append({"name": name, "type": kind, "suppressed": suppressed})
        try:
            feat = com_value(feat, "GetNextFeature")
        except Exception as e:
            logger.warning(f"The feature tree walk stopped after '{name}': {e}")
            break
    return ok(f"{len(features)} features found", {"features": features, "count": len(features)})


@tool(
    group="features",
    description="List the model's dimensions by feature, with the names set_dimension uses (D1@Sketch1 for a sketch dimension, D1@Boss-Extrude1 for an extrude depth, for example). A dimension an equation drives shows that equation: change it with set_equation.",
    params={'feature': {'type': 'string', 'description': 'Only this feature or sketch (default: all)'},
     'unit': {'type': 'string', 'description': 'mm, cm, m, inch or ft'}},
    needs='any',
)
def list_dimensions(ctx, feature=None, unit=None):
    doc = ctx.doc
    rows = model.tree_features(doc)
    if feature:
        wanted = str(feature).strip().lower()
        rows = [r for r in rows if r[0].lower() == wanted]
        if not rows:
            names = ", ".join(r[0] for r in model.tree_features(doc))
            raise ToolError(f"No feature named '{feature}'. Features: {names}", Err.INPUT)
    dims = []
    equations = sw.equations.sw_equation_texts(doc)
    for name, _kind, feat in rows:
        for dim in _feature_dimensions(ctx, feat, unit):
            dim["feature"] = name
            driver = _driven_by(equations, dim["name"]) if equations else None
            if driver:
                dim["equation"] = driver                    # set_equation changes it, set_dimension cannot
            dims.append(dim)
    return ok(f"{len(dims)} dimensions", {"unit": units.label(unit), "count": len(dims), "dimensions": dims})


@tool(
    group="features",
    description="Change a dimension by name and rebuild the model. Works on sketch dimensions and feature dimensions (depths, pattern spacing, fillet radius, ...). If the new value breaks a feature on rebuild, the old value is restored and the call fails. A size the tools dimensioned from the drawing (DrawnN) becomes the user's when set: it is renamed (the result says to what). A dimension an equation drives is refused, naming the equation (change it with set_equation).",
    params={'name': {'type': 'string',
              'description': "Dimension name from list_dimensions, e.g. 'D1@Sketch1'"},
     'value': {'type': 'number', 'description': 'New value (length in unit, degrees for an angle, or a whole number for a pattern count)'},
     'unit': {'type': 'string', 'description': 'mm, cm, m, inch or ft'}},
    required=['name', 'value'],
    needs='any',
    clears_selection=True,
)
def set_dimension(ctx, name, value, unit=None):
    doc = ctx.doc
    try:
        if doc.SketchManager.ActiveSketch is not None:
            raise ToolError("A sketch is open. Close it first (close_sketch); inside an open sketch use "
                            "add_sketch_dimension.", Err.SKETCH)
    except ToolError:
        raise
    except Exception:
        pass

    text = str(name or "").strip()
    if "@" not in text:
        raise ToolError(f"Bad dimension name '{name}'. Use the form D1@Sketch1 (see list_dimensions).", Err.INPUT)
    dim = None
    try:
        dim = doc.Parameter(text)
    except Exception as e:
        logger.debug(f"Parameter lookup failed: {e}")
    if dim is None:
        raise ToolError(f"No dimension named '{text}' in this model (see list_dimensions).", Err.INPUT)

    unit_str = units.label(unit)
    kind = int(com_value(dim, "GetType"))
    angular, count = kind == ANGLE, kind == COUNT

    def read():
        raw = float(dim.SystemValue)
        return round(math.degrees(raw), 4) if angular else int(round(raw)) if count else round(ctx.from_m(raw, unit), 4)

    if int(dim.DrivenState) == 1:
        driver = _driven_by(sw.equations.sw_equation_texts(doc), text)      # S3.1: an equation makes it driven
        if driver:
            raise ToolError(f"Not changed: {text} is driven by the equation {driver}: change that with set_equation. "
                            f"Its value is {read()}{'deg' if angular else '' if count else unit_str}.", Err.INPUT)
        raise ToolError(f"{text} is a driven (reference) dimension and cannot be set. "
                        f"Its value is {read()}{'deg' if angular else '' if count else unit_str}.", Err.INPUT)

    old_value, old_system = read(), float(dim.SystemValue)
    volume_before = model.volume_mm3(doc)
    errors_before = model.error_keys(doc)                           # an old broken feature is not this change's
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ToolError(f"Bad value '{value}'", Err.INPUT)
    if count:                                       # a pattern's instance count: a whole number, no unit (S3.3)
        if number != int(number) or number < 1:
            raise ToolError(f"{text} is a count (a pattern's number of instances): give a whole number of 1 or more, "
                            f"without a unit", Err.INPUT)
        new_system = float(int(number))
    else:
        new_system = math.radians(number) if angular else ctx.to_m(number, unit)
    if not angular and new_system <= 0:
        raise ToolError("A length must be positive", Err.INPUT)

    code = sw.editing.sw_set_dimension_value(dim, new_system)
    if code != 0:
        raise ToolError(f"SolidWorks refused the value for {text} (code {code})", Err.FEATURE,
                        {"name": text, "value": old_value})

    rebuilt_ok = model.rebuild(doc)
    problems = model.new_rebuild_errors(doc, errors_before)
    shown_unit = "deg" if angular else "" if count else unit_str
    data = {"name": text, "old_value": old_value, "new_value": read(), "unit": shown_unit,
            "solid_bodies": model.solid_body_count(doc)}
    if errors_before:
        data["old_rebuild_errors"] = sorted(f for f, _ in errors_before)

    # a value that breaks the model is rolled back (a failed rebuild counts only when the model was clean before)
    if problems or (not rebuilt_ok and not errors_before):
        sw.editing.sw_set_dimension_value(dim, old_system)
        model.rebuild(doc)
        data["rebuild_errors"] = problems
        data["new_value"] = read()
        broken = ", ".join(p["feature"] for p in problems) or "the model"
        raise ToolError(f"Not changed: {text} = {number}{shown_unit} breaks {broken} on rebuild. "
                        f"The value was put back to {old_value}{shown_unit}.", Err.FEATURE, data)

    # an equation drives it: SolidWorks puts the equation's value back on rebuild and reports no error (S3.1)
    if abs(data["new_value"] - number) > max(1e-4, 1e-6 * abs(number)):
        driver = _driven_by(sw.equations.sw_equation_texts(doc), text)
        if abs(data["new_value"] - old_value) > max(1e-4, 1e-6 * abs(old_value)):
            sw.editing.sw_set_dimension_value(dim, old_system)
            model.rebuild(doc)
        data["new_value"] = read()
        why = (f"the equation {driver} drives it: change that with set_equation" if driver else
               "SolidWorks put another value back on rebuild (something else drives it)")
        raise ToolError(f"Not changed: {text} stays {data['new_value']}{shown_unit}: {why}.", Err.INPUT, data)

    volume_after = model.volume_mm3(doc)
    if volume_before is not None and volume_after is not None:
        data["volume_change_mm3"] = round(volume_after - volume_before, 3)
    # a size the tools dimensioned from the drawing (DrawnN) that the user sets is the user's now
    ours, claimed = sk.claim_drawn(doc, dim)
    note = ""
    if claimed:
        data["renamed"] = claimed.split("@")[0] + "@" + claimed.split("@")[1]
        note = f"; it is your dimension now, renamed {data['renamed']}"
    elif ours:
        data["rename_failed"] = True
        note = (f". WARNING: {text} could not be renamed: it still gives way to a conflicting relation or dimension "
                f"added later")
    return ok(f"{text}: {old_value}{shown_unit} -> {data['new_value']}{shown_unit}, model rebuilt{note}", data)


@tool(
    group="features",
    description="Delete a feature, sketch or plane by name; in an assembly also a mate by name. Refused when other features depend on it unless delete_children is true. Reports everything that was removed.",
    params={"name": {"type": "string", "description": "Feature name from list_features, e.g. 'Cut-Extrude1'"},
            "delete_sketch": {"type": "boolean", "default": False, "description": "Also delete the sketch the feature was made from"},
            "delete_children": {"type": "boolean", "default": False, "description": "Also delete the features that depend on this one"}},
    required=["name"],
    needs="any",
)
def delete_feature(ctx, name, delete_sketch=False, delete_children=False):
    doc = ctx.doc
    try:
        if doc.SketchManager.ActiveSketch is not None:
            raise ToolError("A sketch is open. Close it first (close_sketch).", Err.SKETCH)
    except ToolError:
        raise
    except Exception:
        pass
    before = model.tree_features(doc)
    lookup = {row[0].lower(): row for row in before}
    row = lookup.get(str(name or "").strip().lower())
    if row is None:
        mate_names = []                                   # mates are sub-features of the Mates folder
        if doc_type(doc) == 2:
            mate_names = [n for n, _ in assembly.mate_features(doc)]
            if str(name or "").strip().lower() in [n.lower() for n in mate_names]:
                message, data = assembly.delete_mate(doc, name)
                return ok(message, data)
        listed = f"Features: {', '.join(r[0] for r in before)}"
        if mate_names:
            listed += f". Mates: {', '.join(mate_names[:60])}"
        raise ToolError(f"No feature named '{name}'. {listed}", Err.INPUT)

    real_name, _, feat = row
    before_names = [r[0] for r in before]
    errors_before = model.error_keys(doc)                           # an old broken feature is not this delete's
    by_name = {r[0]: r[2] for r in before}
    children, queue = [], [feat]                          # dependents, directly or through another feature
    while queue and len(children) < 2000:
        current = queue.pop(0)
        try:
            kids = com_value(current, "GetChildren") or []
        except Exception as e:
            logger.debug(f"GetChildren failed: {e}")
            kids = []
        for child in kids:
            try:
                child_name = child.Name
            except Exception:
                continue
            if child_name in by_name and child_name != real_name and child_name not in children:
                children.append(child_name)
                queue.append(by_name[child_name])
    if children and not delete_children:
        raise ToolError(f"Not deleted: {', '.join(children)} depend(s) on {real_name}. Delete those first, or use "
                        f"delete_children=true to delete them too.", Err.FEATURE,
                        {"feature": real_name, "children": children})

    # the dependents first, last in the tree first (SolidWorks will not delete a sketch a feature still uses)
    for child_name in sorted(children, key=before_names.index, reverse=True):
        sw.editing.sw_delete_selected(doc, doc.FeatureByName(child_name))      # None: already gone with its parent
    if not sw.editing.sw_delete_selected(doc, doc.FeatureByName(real_name), absorbed=delete_sketch):
        raise ToolError(f"Could not select '{real_name}'", Err.SELECTION)
    after_names = [r[0] for r in model.tree_features(doc)]
    removed = [n for n in before_names if n not in after_names]
    if real_name not in removed:
        raise ToolError(f"SolidWorks did not delete '{real_name}'", Err.FEATURE, {"feature": real_name})
    model.rebuild(doc)
    problems = model.new_rebuild_errors(doc, errors_before)
    data = {"deleted": removed, "features_left": len(after_names), "solid_bodies": model.solid_body_count(doc)}
    message = f"Deleted {', '.join(removed)}"
    if problems:
        data["rebuild_errors"] = problems
        message += ". WARNING: these features now have rebuild errors: " + ", ".join(p["feature"] for p in problems)
    return ok(message, data)
