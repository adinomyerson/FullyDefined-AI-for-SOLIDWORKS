"""Features: the calls that make boss and cut extrudes, fillets and chamfers.
The tools do the inputs, messages and measuring."""

import logging

logger = logging.getLogger(__name__)

# The API signatures are long and positional. Every extrude and cut variant opens with the same 13 values:
# one or two directions, which side a cut removes, reverse, the two end conditions, the two depths, then the
# draft (on, outward, for each direction, and two angles), which we never use.
_NO_DRAFT = [False, False, False, False, 0.0, 0.0]
# Newer variants end with the start condition: from the sketch plane, no offset, offset not flipped.
_FROM_SKETCH_PLANE = [0, 0.0, False]


def _shared_start(both_ways: bool, reverse: bool, end_1: int, end_2: int, depth_1: float, depth_2: float) -> list:
    keep_inside = False     # for a cut: remove the material inside the profile
    return [not both_ways, keep_inside, reverse, end_1, end_2, depth_1, depth_2, *_NO_DRAFT]


def _first_that_works(variants):
    """Call each (name, call) in turn until one returns a feature. Returns (feature or None, name or "")."""
    for name, call in variants:
        try:
            made = call()
        except Exception as e:
            logger.debug(f"{name} raised: {e}")
            continue
        if made:
            return made, name
        logger.debug(f"{name} returned nothing")
    return None, ""


def sw_extrude(doc, flip, end_cond, depth_m, merge):
    """Run the boss-extrude on the selected sketch, trying the API variants of different
    SolidWorks versions. Returns (feature or None, name of the API method that worked)."""
    fm = doc.FeatureManager
    start = _shared_start(False, flip, end_cond, 0, depth_m, depth_m)
    # after the draft: offset and translate flags for each direction (all off), merge, feature scope, auto-select
    current = start + [False] * 4 + [merge, True, True] + _FROM_SKETCH_PLANE
    older = start + [False] * 3 + [merge, True, True, True]
    v3 = _shared_start(False, flip, end_cond, 0, depth_m, 0) + [False] * 3 + [merge, True, True] + _FROM_SKETCH_PLANE
    return _first_that_works((
        ("FeatureExtrusion2_23p", lambda: fm.FeatureExtrusion2(*current)),
        ("FeatureExtrusion2_20p", lambda: fm.FeatureExtrusion2(*older)),
        ("FeatureExtrusion3", lambda: fm.FeatureExtrusion3(*v3)),
    ))


def sw_cut(doc, single: bool, direction_flip: bool, end_cond_1: int, end_cond_2: int, cut_depth: float):
    """Cut-Extrude of the selected sketch, trying the API variants. Returns (feature or None, API method name)."""
    fm = doc.FeatureManager
    start = _shared_start(not single, direction_flip, end_cond_1, end_cond_2, cut_depth, 0)
    # FeatureCut3 (26 values): offset / translate flags, then normal cut, scope and the assembly-scope flags, all off
    cut3 = start + [False] * 10 + _FROM_SKETCH_PLANE
    # FeatureCut4 (27 values) adds one more flag after the start condition (optimise geometry, off)
    cut4 = start + [False] * 10 + _FROM_SKETCH_PLANE + [False]
    return _first_that_works((
        ("FeatureCut3", lambda: fm.FeatureCut3(*cut3)),
        ("FeatureCut4", lambda: fm.FeatureCut4(*cut4)),
    ))


def sw_fillet(doc, radius_m: float):
    """Fillet the selected edges or faces. Returns (feature or None, API method name)."""
    fm = doc.FeatureManager
    # FeatureFillet3 wants exactly 14 values ("Invalid number of parameters" with 15): option bits 195, the
    # radius, then zeros for the second radius, conic and overflow settings and the seven optional arrays.
    # SimpleFillet is the fallback for versions where that call fails.
    return _first_that_works((
        ("FeatureFillet3", lambda: fm.FeatureFillet3(195, radius_m, *([0] * 12))),
        ("SimpleFillet", lambda: fm.SimpleFillet(radius_m, True, True, True)),
    ))


def sw_chamfer(doc, options: int, dist_m: float, angle_rad: float):
    """Angle-distance chamfer of the selected edges, or None."""
    angle_distance = 1
    # 8 values: options, type, distance, angle, then a second distance and three vertex distances (unused)
    made, _ = _first_that_works((
        ("InsertFeatureChamfer",
         lambda: doc.FeatureManager.InsertFeatureChamfer(options, angle_distance, dist_m, angle_rad, 0.0, 0.0, 0.0, 0.0)),
    ))
    return made
