"""
The standard planes and the origin by position and type, when the English name is not there: a part or assembly made
from a localized template names them differently (e.g. "Ebene vorne").

  - Every place first uses the English name exactly as before, with no extra read ("Front Plane", "Point1@Origin").
    Only when SolidWorks does not find it, and the input was Front / Top / Right or the origin, does that place ask
    here (fallback_name, fallback_origin_point, select_origin, feature) and try once more. English documents cost
    what they always did.
  - The lookup: one walk of the top of the feature tree: the first three reference planes above the origin feature
    (OriginProfileFeature: types are not translated) are the standard planes, each told apart by its normal (front:
    along Z, top: along Y, right: along X; from IRefPlane.Transform). Planes whose normal cannot be told keep the
    template order front, top, right.
  - Kept as plain names (no COM object) per SolidWorks process, document title and path, so the next call does not
    look again. Not kept when any of those cannot be read. A kept name other than the English one is checked again
    with one lookup on every use: a localized document may be closed and another opened under the same title. A
    walk that found no origin is not kept.
"""

import logging
from collections import OrderedDict
from typing import Optional

from .com import doc_path, doc_title, null_object, plane_feature_name, value

logger = logging.getLogger(__name__)

_WORDS = ("front", "top", "right")
_ENGLISH = {"front": "Front Plane", "top": "Top Plane", "right": "Right Plane"}
_ENGLISH_ORIGIN = "Origin"
_AXIS = {2: "front", 1: "top", 0: "right"}         # the plane's normal along Z / Y / X
_WALK_LIMIT = 40                                    # the standard planes sit in the first nodes of the tree
_KEEP = 64                                          # documents remembered

_known: "OrderedDict[tuple, dict]" = OrderedDict()  # key -> {item: name, "walked": True}


def word_of(text) -> Optional[str]:
    """'Front', 'front plane', ' TOP ', nothing -> 'front' / 'top' / 'front'; anything else -> None (a plane name used
    as given). The same reading as com.plane_feature_name always had (no input maps differently)."""
    t = (text or "Front").strip().lower().replace(" plane", "")
    return t if t in _WORDS else None


def _pid():
    try:
        from .connection import connection
        return connection.pid
    except Exception:
        return None


def _kind_of(item: str) -> tuple:
    return (_ENGLISH_ORIGIN, "OriginProfileFeature") if item == "origin" else (_ENGLISH[item], "RefPlane")


def typed(owner, name: str, kind: str):
    """The feature called name if it is of that type, else None."""
    try:
        feat = owner.FeatureByName(name)
        return feat if feat is not None and value(feat, "GetTypeName2") == kind else None
    except Exception:
        return None


def _normal_axis(feat) -> Optional[int]:
    """0 / 1 / 2 when the reference plane's normal lies along X / Y / Z; None when it cannot be told. The normal is
    the rotation's third row and third column alike for the standard planes (quarter turns about one axis); both are
    read and must agree."""
    try:
        data = [float(v) for v in value(value(value(feat, "GetSpecificFeature2"), "Transform"), "ArrayData")]
    except Exception as e:
        logger.debug(f"plane transform unreadable: {e}")
        return None
    if len(data) < 9:
        return None
    row, column = data[6:9], [data[2], data[5], data[8]]
    axes = []
    for v in (row, column):
        i = max(range(3), key=lambda k: abs(v[k]))
        if abs(abs(v[i]) - 1.0) > 1e-6:
            return None
        axes.append(i)
    return axes[0] if axes[0] == axes[1] else None


def _walk(owner) -> dict:
    """{front, top, right, origin} -> names from the top of the feature tree, plus "found_origin"."""
    planes, origin_name_, node, seen = [], None, None, 0
    try:
        node = value(owner, "FirstFeature")
    except Exception as e:
        logger.debug(f"feature tree unreadable: {e}")
    while node is not None and seen < _WALK_LIMIT:
        seen += 1
        try:
            kind = value(node, "GetTypeName2")
            if kind == "OriginProfileFeature":
                origin_name_ = str(node.Name)
                break
            if kind == "RefPlane" and len(planes) < 3:
                planes.append((str(node.Name), _normal_axis(node)))
            node = value(node, "GetNextFeature")
        except Exception as e:
            logger.debug(f"feature tree walk stopped: {e}")
            break
    found = {}
    axes = [axis for _, axis in planes]
    if len(planes) == 3 and None not in axes and len(set(axes)) == 3:
        for name, axis in planes:
            found[_AXIS[axis]] = name
    else:
        for word, (name, _) in zip(_WORDS, planes):                    # the template's order: front, top, right
            found[word] = name
        if planes:
            logger.info(f"standard planes by tree order (normals {axes}): {[n for n, _ in planes]}")
    for word in _WORDS:
        found.setdefault(word, _ENGLISH[word])                          # none found: today's name, refused loudly
    found["origin"] = origin_name_ or _ENGLISH_ORIGIN
    if any(found[k] != v for k, v in dict(_ENGLISH, origin=_ENGLISH_ORIGIN).items()):
        logger.info(f"standard planes and origin of this document: {found}")
    found["found_origin"] = origin_name_ is not None
    return found


def _remember(key, known: dict) -> None:
    if key is None:
        return
    _known[key] = known
    _known.move_to_end(key)
    while len(_known) > _KEEP:
        _known.popitem(last=False)


def _lookup(owner, key, item: str, english_missing: bool = False) -> str:
    """One name (front / top / right / origin) of owner, kept under key (None: not kept). english_missing: the caller
    has just failed to find the English name, so it is not looked up again."""
    known = dict(_known.get(key, {})) if key is not None else {}
    english, kind = _kind_of(item)
    if item in known and known[item] != english and typed(owner, known[item], kind) is None:
        known = {}                                                      # another document under this key: look again
    if english_missing and known.get(item) == english and not known.get("walked"):
        known = {}                                                      # kept from a lookup by name, now gone: walk
    if item not in known:
        if not english_missing and not known.get("walked") and typed(owner, english, kind) is not None:
            known[item] = english
        else:
            walked = _walk(owner)
            if not walked.pop("found_origin"):
                return walked[item]                                     # no origin found: answer, keep nothing
            for k, v in walked.items():
                known.setdefault(k, v)
            known["walked"] = True
    _remember(key, known)
    return known[item]


def _doc_key(doc):
    pid, title = _pid(), doc_title(doc)
    if pid is None or title == "Unknown":
        return None
    return (pid, title, doc_path(doc))


def _component_key(comp):
    pid = _pid()
    try:
        path = str(value(comp, "GetPathName") or "")
    except Exception:
        path = ""
    return (pid, "component", path) if pid is not None and path else None


def _plane_name(text, owner=None, component: bool = False) -> str:
    """Front / Top / Right (any case, with or without "plane") -> that standard plane's name in owner (default: the
    active document); any other text is returned as given (Plane1, a user's own plane)."""
    word = word_of(text)
    if word is None:
        return (text or "Front").strip()
    try:
        if owner is None:
            from .connection import connection
            owner = connection.app.ActiveDoc if connection.app is not None else None
        if owner is None:
            return _ENGLISH[word]
        return _lookup(owner, _component_key(owner) if component else _doc_key(owner), word)
    except Exception as e:
        logger.debug(f"standard plane lookup failed, using the English name: {e}")
        return _ENGLISH[word]


def _origin_name(doc) -> str:
    """The name of this document's origin feature (by type)."""
    try:
        return _lookup(doc, _doc_key(doc), "origin")
    except Exception as e:
        logger.debug(f"origin lookup failed, using the English name: {e}")
        return _ENGLISH_ORIGIN


def _origin_point(doc) -> str:
    """The SelectByID2 name of the origin's point in this document: Point1@<its origin feature's name>."""
    return "Point1@" + _origin_name(doc)


def _component_origin_point(comp) -> str:
    """The origin's point name inside a component (before its @<component> suffix)."""
    try:
        return "Point1@" + _lookup(comp, _component_key(comp), "origin")
    except Exception:
        return "Point1@" + _ENGLISH_ORIGIN


def _select_origin_point(doc, append: bool = True, mark: int = 0) -> bool:
    """Select the origin's point as an object, for when the select by name (origin_point) fails: a SolidWorks whose
    "Point1" is translated. The origin feature's own sketch holds that one point. The mark is kept (a caller such as
    fully_define_sketch's datum needs it). False when it cannot be read."""
    try:
        feat = doc.FeatureByName(_origin_name(doc))
        points = list(value(value(feat, "GetSpecificFeature2"), "GetSketchPoints2") or [])
        if len(points) != 1:
            return False
        data = null_object()
        if mark:
            data = value(value(doc, "SelectionManager"), "CreateSelectData")
            data.Mark = mark
        return bool(points[0].Select4(append, data))
    except Exception as e:
        logger.debug(f"the origin's point could not be selected as an object: {e}")
        return False


def _key(owner, component: bool):
    return _component_key(owner) if component else _doc_key(owner)


def fallback_name(owner, text, component: bool = False) -> Optional[str]:
    """After the English name of a standard plane was not found in owner: the name this document uses for it, or None
    (not Front / Top / Right, or the document's name is the English one after all, or nothing found)."""
    word = word_of(text)
    if word is None:
        return None
    try:
        name = _lookup(owner, _key(owner, component), word, english_missing=True)
    except Exception as e:
        logger.debug(f"standard plane lookup failed: {e}")
        return None
    return name if name != _ENGLISH[word] else None


def fallback_origin_point(owner, component: bool = False) -> Optional[str]:
    """After "Point1@Origin" could not be selected in owner: Point1@<this document's origin name>, or None when its
    origin is called Origin after all (then nothing else is tried: an English document behaves exactly as before)."""
    try:
        name = _lookup(owner, _key(owner, component), "origin", english_missing=True)
    except Exception as e:
        logger.debug(f"origin lookup failed: {e}")
        return None
    return "Point1@" + name if name != _ENGLISH_ORIGIN else None


def select_origin(doc, append: bool = True, mark: int = 0, after_english: bool = False) -> bool:
    """Select the origin's point: "Point1@Origin" first (skipped when the caller has just tried it; it may raise, as
    before); only when that fails: the name this document gives its origin (when it is not "Origin"), then the
    origin's point as an object (a translated "Point1", whatever the origin's name). The mark is kept on every path.
    When the English select succeeds the result is exactly as before; when it fails, the origin may now be selected
    where before nothing was."""
    def by_name(name):
        try:
            return bool(doc.Extension.SelectByID2(name, "EXTSKETCHPOINT", 0, 0, 0, append, mark, null_object(), 0))
        except Exception:
            return False
    if not after_english and doc.Extension.SelectByID2("Point1@" + _ENGLISH_ORIGIN, "EXTSKETCHPOINT", 0, 0, 0, append,
                                                       mark, null_object(), 0):
        return True
    localized = fallback_origin_point(doc)
    if localized is not None and by_name(localized):
        return True
    return _select_origin_point(doc, append, mark)


def select(doc, text, mark: int = 0, append: bool = True) -> Optional[str]:
    """Select a plane by the word or name a tool was given ("Front", "Top Plane", "Plane1"): the English name first,
    one select exactly as before; only when that fails, the name this document gives that standard plane. Returns
    the name selected, or None. For tools: one line in place of plane_feature_name + sw_select_named. When the English
    select succeeds the result is exactly as before; when it fails, the document's own standard plane may now be
    selected where before nothing was (a localized template, or a renamed standard plane). A user plane that carries
    the English name ("Front Plane") is taken first, as by name."""
    name = plane_feature_name(text)                     # None and "" mean Front, as before
    if doc.Extension.SelectByID2(name, "PLANE", 0, 0, 0, append, mark, null_object(), 0):   # may raise, as before
        return name
    localized = fallback_name(doc, text)
    try:
        if localized is not None and doc.Extension.SelectByID2(localized, "PLANE", 0, 0, 0, append, mark,
                                                               null_object(), 0):
            return localized
    except Exception as e:
        logger.debug(f"the localized plane {localized!r} could not be selected: {e}")
    return None


def feature(owner, text, component: bool = False):
    """The reference plane text names in owner: by its name first (Front / Top / Right: the English name, one lookup,
    as before); if it is not there and text is a standard word, by the document's own name. None when not found."""
    from .com import plane_feature_name
    feat = typed(owner, plane_feature_name(text), "RefPlane")
    if feat is not None:
        return feat
    localized = fallback_name(owner, text, component)
    return typed(owner, localized, "RefPlane") if localized else None


def _forget() -> None:
    """Tests: drop what was read."""
    _known.clear()
