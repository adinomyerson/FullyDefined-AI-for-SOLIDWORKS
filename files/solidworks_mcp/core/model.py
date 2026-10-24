"""
Looking at the model: bodies, volume, the feature tree, and checking that a new feature is real.

SolidWorks hands back a feature object even when the feature could not be built, and a
pattern or cut can "succeed" and change nothing. So after every feature a tool measures
the model and calls feature_problem(); a dead feature is deleted again.
"""

import logging
from typing import Dict, List, Optional

import pythoncom
import win32com.client

from .com import doc_type, value
from .results import Err, ToolError

logger = logging.getLogger(__name__)

# Folders and reference items at the top of the tree that are not user features
TREE_SYSTEM_TYPES = {
    "CommentsFolder", "FavoriteFolder", "HistoryFolder", "SelectionSetFolder", "SensorFolder",
    "DocsFolder", "DetailCabinet", "SurfaceBodyFolder", "SolidBodyFolder", "EnvFolder",
    "InkMarkupFolder", "EqnFolder", "MaterialFolder", "OriginProfileFeature",
}
STANDARD_PLANES = ("Front Plane", "Top Plane", "Right Plane")
# Children of a sketch that do not "use it up"
NOT_A_SKETCH_USER = ("ProfileFeature", "3DProfileFeature", "RefPlane", "RefAxis", "RefPoint", "CoordSys")


def _null():
    return win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)


def solid_body_count(doc) -> int:
    """Number of solid bodies in a part (-1 if it cannot be read)."""
    try:
        return len(doc.GetBodies2(0, False) or [])          # 0 = swSolidBody
    except Exception:
        return -1


def volume_mm3(doc) -> Optional[float]:
    """Total volume of the model in mm^3 (None if it cannot be read)."""
    try:
        return round(float(doc.Extension.CreateMassProperty.Volume) * 1e9, 3)
    except Exception:
        return None


def tree_features(doc) -> list:
    """(name, type, feature) for every user feature in tree order."""
    rows = []
    feat = value(doc, "FirstFeature")
    while feat is not None and len(rows) < 5000:
        try:
            name, kind = feat.Name, value(feat, "GetTypeName2")
            if kind not in TREE_SYSTEM_TYPES and name not in STANDARD_PLANES:
                rows.append((name, kind, feat))
        except Exception:
            pass
        try:
            feat = value(feat, "GetNextFeature")
        except Exception:
            break
    return rows


def last_feature_name(doc) -> Optional[str]:
    """The newest feature's name, read without walking the tree (a walk costs about 1 s on a small part)."""
    try:
        feat = doc.FeatureByPositionReverse(0)
        return str(feat.Name) if feat is not None else None
    except Exception:
        return None


def feature_names_after(doc, last_name: Optional[str]) -> list:
    """Names of the user features after last_name in tree order (every feature when it is None or gone): what a
    failed build left behind. Walks the tree, so only for cleaning up."""
    names = [row[0] for row in tree_features(doc)]
    return names[names.index(last_name) + 1:] if last_name in names else names


def feature_error(feat) -> bool:
    """Does this one feature report an error after its rebuild (instead of walking the whole tree)?"""
    try:
        return int(value(feat, "GetErrorCode")) != 0
    except Exception:
        return False


def rebuild_errors(doc) -> List[Dict]:
    """Features that report an error or warning after a rebuild."""
    problems = []
    for name, kind, feat in tree_features(doc):
        try:
            code = int(value(feat, "GetErrorCode"))
        except Exception:
            continue
        if code != 0:
            problems.append({"feature": name, "type": kind, "error_code": code})
    return problems


def error_keys(doc) -> set:
    """(feature, error code) of every feature that reports an error now: the "before" for new_rebuild_errors."""
    return {(p["feature"], p["error_code"]) for p in rebuild_errors(doc)}


def new_rebuild_errors(doc, before: set) -> List[Dict]:
    """The rebuild errors that were not there before a change (an old broken feature does not count against it)."""
    return [p for p in rebuild_errors(doc) if (p["feature"], p["error_code"]) not in before]


def rebuild(doc) -> bool:
    """Rebuild the model. False if SolidWorks reports rebuild errors. In an assembly or drawing
    only the top level is rebuilt, or every part is marked modified and re-saved with it."""
    try:
        return bool(doc.ForceRebuild3(doc_type(doc) != 1))
    except Exception as e:
        logger.debug(f"ForceRebuild3 failed: {e}")
        try:
            return bool(value(doc, "EditRebuild3"))
        except Exception:
            return False


def delete_feature(doc, feat) -> bool:
    """Delete one feature (its sketch is kept)."""
    try:
        doc.ClearSelection2(True)
        if feat.Select2(False, 0):
            doc.EditDelete()
            doc.ClearSelection2(True)
            return True
    except Exception as e:
        logger.debug(f"Could not delete the feature: {e}")
    return False


def feature_problem(doc, feat, volume_before, must_change: bool = True) -> Optional[str]:
    """Check a feature that was just created. With a rebuild error, or (must_change) a volume
    exactly as before, the feature is deleted and the reason returned. None = the feature is fine."""
    reason = None
    try:
        code = value(feat, "GetErrorCode")
        if int(code) != 0:
            reason = f"it fails to rebuild (SolidWorks error code {int(code)})"
    except Exception:
        pass
    if reason is None and must_change and volume_before is not None:
        after = volume_mm3(doc)
        if after is not None and abs(after - volume_before) < 1e-6:
            reason = "it would not change the model"
    if reason is not None:
        delete_feature(doc, feat)
    return reason


def big_change_warning(doc, volume_before, data: Dict, what: str) -> str:
    """Record the volume change of a fillet / chamfer in `data`; return a warning when it is large.
    SolidWorks builds a fillet far bigger than the faces next to the edge (a 500 mm fillet on a
    40 mm block leaves a tenth of the part): almost always a typo or a wrong unit."""
    after = volume_mm3(doc)
    if volume_before is None or after is None:
        return ""
    change = after - volume_before
    data["volume_change_mm3"] = round(change, 3)
    if volume_before > 0 and abs(change) > 0.25 * volume_before:
        data["large_change"] = True
        return (f". WARNING: this changed the volume of the part by {round(100.0 * abs(change) / volume_before)}% "
                f"- check the {what} and the unit (delete_feature removes it again)")
    return ""


def selected_edge_or_face_count(doc) -> int:
    """How many selected items are edges, faces or loops - what a fillet or chamfer works on.
    -1 when the selection cannot be read."""
    try:
        manager = doc.SelectionManager
        usable = 0
        for index in range(1, int(manager.GetSelectedObjectCount2(-1)) + 1):
            if int(manager.GetSelectedObjectType3(index, -1)) in (1, 2, 12):    # edge, face, loop
                usable += 1
        return usable
    except Exception as e:
        logger.debug(f"Could not read the selection: {e}")
        return -1


# ---------------------------------------------------------------- sketches as feature input

def _every_feature(doc):
    """Every node of the feature tree in order, folders and planes included. Stops quietly where the tree
    cannot be read further."""
    try:
        node = value(doc, "FirstFeature")
    except Exception as e:
        logger.debug(f"feature tree unreadable: {e}")
        return
    while node is not None:
        yield node
        try:
            node = value(node, "GetNextFeature")
        except Exception:
            return


def _read(node, member):
    try:
        return value(node, member)
    except Exception:
        return None


def tree_nodes(doc) -> list:
    """(name, type) of every node of the feature tree, folders and planes included; None for a member
    SolidWorks would not give."""
    return [(_read(node, "Name"), _read(node, "GetTypeName2")) for node in _every_feature(doc)]


def find_last_sketch(doc) -> Optional[str]:
    """The newest sketch (lowest in the tree), or None."""
    names = [name for name, kind in tree_nodes(doc) if kind == "ProfileFeature" and name]
    return names[-1] if names else None


def sketch_info(doc) -> Dict:
    """Sketch counts and names, for error messages."""
    try:
        active = doc.SketchManager.ActiveSketch is not None
    except Exception:
        active = False
    nodes = tree_nodes(doc)
    names = [name for name, kind in nodes if kind == "ProfileFeature" and name]
    return {"sketch_count": len(names), "sketch_names": names, "has_active_sketch": active,
            "feature_count": len(nodes)}


def sketch_users(doc, sketch_name: str) -> list:
    """Names of the features already built from a sketch."""
    users = []
    try:
        for child in value(doc.FeatureByName(sketch_name), "GetChildren") or []:
            try:
                if value(child, "GetTypeName2") not in NOT_A_SKETCH_USER:
                    users.append(child.Name)
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"Could not read the users of {sketch_name}: {e}")
    return users


def close_and_select_sketch(doc) -> tuple:
    """Close the open sketch, then select the last sketch. Returns (ok, sketch name, error text)."""
    from . import sketch as sk
    try:
        try:
            active = doc.SketchManager.ActiveSketch
            if active is not None:
                sk.define_before_closing(doc, active)       # a part's sketch is fully defined, as close_sketch does
                doc.SketchManager.InsertSketch(True)
        except Exception:
            try:
                doc.InsertSketch2(True)
            except Exception:
                pass
        doc.ClearSelection2(True)
        name = find_last_sketch(doc)
        if not name:
            return False, "", "the document has no sketch to build from (draw a profile first)"
        # the last sketch may already belong to a feature: a second call would build another one
        # from it (a cut through the boss just made from it)
        users = sketch_users(doc, name)
        if users:
            return False, name, (f"the most recent sketch ({name}) is already used by {', '.join(users)}. "
                                 f"Open a new sketch (create_sketch / create_sketch_on_face) and draw "
                                 f"the profile first")
        # the empty callout argument: some pywin32 builds take a null VARIANT, others only pythoncom.Nothing
        for no_callout in (_null(), pythoncom.Nothing):
            if doc.Extension.SelectByID2(name, "SKETCH", 0, 0, 0, False, 0, no_callout, 0):
                return True, name, ""
        return False, name, f"Could not select sketch '{name}'"
    except Exception as e:
        return False, "", f"could not pick the sketch to use ({e})"


def sketch_names(doc) -> list:
    """Names of the sketches in tree order."""
    return [row[0] for row in tree_features(doc) if row[1] == "ProfileFeature"]


def match_name(wanted, names):
    """The name in `names` that equals `wanted` ignoring case, else None."""
    return {name.lower(): name for name in names}.get(str(wanted).strip().lower())


def close_open_sketch(doc) -> None:
    from . import sketch as sk
    try:
        active = doc.SketchManager.ActiveSketch
        if active is not None:
            sk.define_before_closing(doc, active)           # a part's sketch is fully defined, as close_sketch does
            doc.SketchManager.InsertSketch(True)
    except Exception:
        pass
    doc.ClearSelection2(True)


def remove_new_features(doc, known_names) -> None:
    """Delete every feature not in known_names: what a failed sweep, loft or flange leaves behind."""
    for name in [row[0] for row in tree_features(doc) if row[0] not in known_names][::-1]:
        try:
            leftover = doc.FeatureByName(name)
            if leftover is not None:
                doc.ClearSelection2(True)
                if leftover.Select2(False, 0):
                    doc.Extension.DeleteSelection2(2)           # with absorbed sketches
        except Exception as e:
            logger.debug(f"Could not remove {name}: {e}")
    doc.ClearSelection2(True)


def refuse_used_sketches(doc, names) -> None:
    """Refuse sketches picked BY DEFAULT that already belong to a feature (a repeated call would
    build the same sweep / loft again). Sketches the caller names may be reused."""
    for name in names:
        if not name:
            continue
        users = sketch_users(doc, name)
        if users:
            raise ToolError(f"{name} is already used by {', '.join(users)}. Draw new sketches first, "
                            f"or name the sketches to use.", Err.SKETCH, {"sketch": name, "used_by": users})
