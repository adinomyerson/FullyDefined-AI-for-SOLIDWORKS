"""
Dimension values and configurations.

A part or assembly with more than one configuration can hold a different value of one dimension in each. SolidWorks
writes a value to the active configuration only unless told otherwise, so a change made "in the part" was silently
right in one configuration and left the others as they were. The rule:

- one configuration: everything exactly as before (the active one is the only one);
- more than one, and the caller named none: refused before any change, listing them, so the AI asks the user;
  except one configuration of the model's own with one derived from it (a weldment's "As Welded"): the derived one
  follows, so nothing is asked;
- configurations given (a list of names, or ["all"]): the value goes to exactly those.

The tools read where() once at the start of a call (before any change), then pass its answer to set_value(),
values() and restore(). Cost on a single-configuration document: one read (the configuration count), and every
write and read after it is the call made before this module existed.

    owner(app, doc, name) -> model               the document whose configurations a dimension has (no read on a part)
    where(model, configurations=None) -> Where   the plan, or a refusal listing the configurations
    set_value(dim, value_m, where=ACTIVE) -> int SolidWorks' code (0: taken)
    values(dim, where) -> {name: value}          the value in each configuration it covers ("" = the active one), one
                                                 read per configuration
    restore(dim, saved, where) -> bool           each configuration's own value back (values() taken before)
    value_in(dim, configuration=None)            one configuration's value, or None (None asked: SystemValue)

For the tools that use them:
- where() goes before anything changes, so its refusal changes nothing. A tool that adds a dimension and may set a
  value (add_sketch_dimension) asks it before the first dimension is added, and only when a value is given: a
  dimension added at its drawn size is the same in every configuration.
- A component's dimension in an assembly ("D1@Sketch1@bracket.Part") has the part's configurations, not the
  assembly's: pass owner(app, doc, name) to where(), not the assembly.
- With a plan that is not ACTIVE, everything that reads the model sees the ACTIVE configuration only: SystemValue,
  the volume, the rebuild errors, the sketch status. Read the value back with values(dim, plan), never SystemValue
  (a value set in another configuration would look as if something put the old one back). A rebuild error in a
  configuration that is not active is not seen unless that configuration is activated and rebuilt: say which
  configuration was checked.
"""

import logging
from typing import Dict, NamedTuple, Optional, Tuple

import pythoncom
import win32com.client

from .com import doc_title, doc_type, open_documents, value
from .results import Err, ToolError

logger = logging.getLogger(__name__)

# swSetValueInConfiguration_e (IDimension.SetSystemValue3)
THIS, ALL, SPECIFIC = 1, 2, 3
# swInConfigurationOpts_e.swSpecifyConfiguration (IDimension.GetSystemValue3)
READ_SPECIFIC = 3

ALL_WORDS = ("all", "*")


class Where(NamedTuple):
    """Where a dimension value goes: option THIS (the active configuration: the write the tools always made), ALL
    or SPECIFIC; names are the configurations it covers (ALL: every one, when known), () for THIS."""
    option: int
    names: Tuple[str, ...] = ()

    def text(self) -> str:
        if self.option == THIS:
            return "the active configuration"
        if self.option == ALL:
            return "every configuration" + (f" ({', '.join(self.names)})" if self.names else "")
        return ("configuration " if len(self.names) == 1 else "configurations ") + ", ".join(self.names)


ACTIVE = Where(THIS)         # a document with one configuration: the write the tools always made
EVERY = Where(ALL)           # every configuration (core's own put-backs of sizes it measured from the drawing)


def _names_arg(names):
    """Configuration names as SolidWorks takes them: an array of strings."""
    return win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_BSTR, [str(n) for n in names])


def configuration_names(doc) -> list:
    return [str(n) for n in (value(doc, "GetConfigurationNames") or [])]


def active_name(doc) -> str:
    try:
        return str(value(doc, "GetActiveConfiguration").Name)
    except Exception as e:
        logger.debug(f"Active configuration: {e}")
        return ""


def owner(app, doc, name: str):
    """The document that holds a dimension named as SolidWorks names it: "D1@Sketch1" is the document's own (no
    read); "D1@Sketch1@bracket.Part" (or "@bracket-1.Part", ".Assembly") in an assembly is that open component
    document's. One not found among the open documents (a lightweight component): the document itself, logged."""
    parts = str(name or "").split("@")
    if len(parts) < 3:
        return doc
    stem = parts[-1]
    for suffix in (".part", ".assembly", ".sldprt", ".sldasm"):
        if stem.lower().endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    wanted = {stem.lower()}
    base, _, number = stem.rpartition("-")
    if base and number.isdigit():
        wanted.add(base.lower())                     # an instance name: bracket-1
    found = []
    try:
        for other in open_documents(app):
            title = str(doc_title(other) or "")
            if title.rsplit(".", 1)[0].lower() in wanted or title.lower() in wanted:
                found.append(other)
    except Exception as e:
        logger.debug(f"Open documents for {name}: {e}")
    if len(found) == 1:
        return found[0]
    logger.debug(f"The owner of {name}: {len(found)} open documents match; the document itself")
    return doc


def _count(doc) -> int:
    try:
        return int(value(doc, "GetConfigurationCount"))
    except Exception as e:
        logger.debug(f"Configuration count: {e}")
    try:
        return len(configuration_names(doc))
    except Exception as e:
        logger.debug(f"Configuration names: {e}")
    return 1                                    # unreadable: one, so the tools write as they always did


def _listing(names, active) -> str:
    return ", ".join(f"{n} (active)" if n == active else n for n in names)


def _wanted(configurations) -> Optional[list]:
    """The input as a list of names, ["all"] for every one, or None when nothing was given. A refusal for a list
    that names nothing."""
    if configurations is None:
        return None
    if isinstance(configurations, str):
        items = [configurations]
    elif isinstance(configurations, (list, tuple)):
        items = list(configurations)
    else:
        raise ToolError(f"configurations must be a list of configuration names, or [\"all\"] (got {configurations!r}).",
                        Err.INPUT)
    items = [str(i).strip() for i in items if i is not None and str(i).strip()]
    if not items:
        raise ToolError("configurations is empty: give configuration names, or [\"all\"].", Err.INPUT)
    if any(i.lower() in ALL_WORDS for i in items):
        return ["all"]
    return list(dict.fromkeys(items))


def _match(asked: list, names: list, active: str) -> list:
    """Each asked name as the document spells it (exact first, then ignoring case when only one fits); a refusal
    naming the unknown ones and listing the document's configurations."""
    found, unknown = [], []
    for item in asked:
        hit = item if item in names else None
        if hit is None:
            folded = [n for n in names if n.lower() == item.lower()]
            hit = folded[0] if len(folded) == 1 else None
        if hit is None:
            unknown.append(item)
        elif hit not in found:
            found.append(hit)
    if unknown:
        raise ToolError(f"No configuration named {', '.join(repr(u) for u in unknown)}. Configurations: "
                        f"{_listing(names, active)}. Nothing was changed.", Err.INPUT,
                        {"configurations": names, "active": active})
    return found


def where(doc, configurations=None) -> Where:
    """Where a new dimension value goes in this document; call it before any change, so a refusal changes nothing.

    One configuration: ACTIVE, after one read (the count), whatever was asked ("all", or the one name, which is
    checked). More than one: nothing asked is refused, listing the configurations and the active one, so the AI
    asks the user (but a weldment part's pair, one derived from the other: _followed); "all" is ALL with every name;
    names are SPECIFIC (unknown names are refused)."""
    asked = _wanted(configurations)
    count = _count(doc)
    if count <= 1:
        if asked is not None:
            try:
                drawing = doc_type(doc) == 3
            except Exception:
                drawing = False
            if drawing:
                raise ToolError("configurations does not apply in a drawing: a drawing's views show the model's "
                                "configurations. Change the dimension in its part. Nothing was changed.", Err.INPUT)
        if asked and asked != ["all"]:
            try:
                names = configuration_names(doc)
            except Exception as e:
                logger.debug(f"Configuration names: {e}")
                names = []
            if names:
                _match(asked, names, "")
        return ACTIVE
    if asked is None and count == 2:
        plan = _followed(doc)
        if plan is not None:
            return plan
    try:
        names = configuration_names(doc)
    except Exception as e:
        logger.debug(f"Configuration names: {e}")
        raise ToolError(f"This model has {count} configurations, and SolidWorks did not list them. Nothing was "
                        f"changed.", Err.INPUT)
    if asked is None:
        active = active_name(doc)
        raise ToolError(f"This model has {len(names)} configurations: {_listing(names, active)}. A dimension can hold "
                        f"a different value in each, so say which to change: configurations as a list of names, or "
                        f"[\"all\"]. Ask the user which they mean. Nothing was changed.", Err.INPUT,
                        {"configurations": names, "active": active})
    if asked == ["all"]:
        return Where(ALL, tuple(names))
    return Where(SPECIFIC, tuple(_match(asked, names, active_name(doc))))


def _is_derived(doc, name: str) -> bool:
    """A derived configuration (IConfiguration.IsDerived): one that follows its parent's values unless given its own."""
    return bool(value(doc.GetConfigurationByName(name), "IsDerived"))


def _followed(doc) -> Optional[Where]:
    """With nothing named, in a model with two configurations: the plan when it is a weldment part
    (IPartDoc.IsWeldment) whose two configurations are one and the one derived from it, as SOLIDWORKS makes them
    ("As Welded" under "As Machined"), so the AI does not ask the user about a configuration they never made. The
    parent active (one child: with two configurations, the other one): ACTIVE, the write as before (the derived one
    follows it). The derived one active: ALL over both (a write to it alone would give it a value of its own; its
    parent is the only other configuration). None otherwise (refused, as before): not a weldment (a user's own
    derived pair is asked about), two of the user's own, or anything unreadable. Reads, cheapest first and only
    where a refusal was due (the count before this): IsWeldment; the active configuration; its GetChildrenCount
    (one: ACTIVE, done); then its IsDerived and the two names (derived active)."""
    try:
        if not bool(value(doc, "IsWeldment")):
            return None
        config = value(doc, "GetActiveConfiguration")
        if int(value(config, "GetChildrenCount")) == 1:
            return ACTIVE
        if not bool(value(config, "IsDerived")):
            return None
        names = configuration_names(doc)
        return Where(ALL, tuple(names)) if len(names) == 2 else None
    except Exception as e:
        logger.debug(f"Derived configurations: {e}")
        return None


def set_value(dim, value_m: float, where: Where = ACTIVE) -> int:
    """Write a dimension's system value (m, rad or a count) to where it goes; 0 means SolidWorks took it. ACTIVE is
    exactly the write the tools always made. A 0 into named configurations is refused before any write: a
    configuration's own 0 would read as "no value of its own" (value_in), so the server never makes one."""
    if where.option == SPECIFIC and value_m == 0:
        raise ToolError(f"A dimension can't be set to 0 in {', '.join(where.names)} only: SolidWorks can't tell a 0 "
                        f"there from \"no value of its own\", so the value would be read back wrong. Nothing was "
                        f"changed. Use a value above 0, or set 0 in every configuration (configurations [\"all\"]).",
                        Err.INPUT)
    return _write(dim, value_m, where)


def _write(dim, value_m: float, where: Where) -> int:
    """set_value's write, with no check (restore puts back exactly what values() read)."""
    if where.option == THIS:
        return int(dim.SetSystemValue3(value_m, THIS, None))
    if where.option == ALL:
        return int(dim.SetSystemValue3(value_m, ALL, None))
    return int(dim.SetSystemValue3(value_m, SPECIFIC, _names_arg(where.names)))


def values(dim, where: Where = ACTIVE) -> Dict[str, float]:
    """The dimension's system value in each configuration where() covers: {"": value} for ACTIVE (one read, the
    SystemValue the tools always read), else {name: value}, one read per configuration (value_in: a configuration
    with no value of its own reads the active one's). A refusal if SolidWorks gives no value for one."""
    if where.option == THIS:
        return {"": float(dim.SystemValue)}
    if not where.names:
        raise ToolError("values() needs the configurations' names: use where()'s answer, not EVERY.", Err.INTERNAL)
    out, active = {}, None
    for name in where.names:
        got = _own(dim, name)
        if got is None:
            raise ToolError(f"SolidWorks did not give the dimension's value in configuration {name}.", Err.FEATURE)
        if got == 0.0:                                  # no value of its own: the active one's (read once)
            active = float(dim.SystemValue) if active is None else active
            got = active
        out[name] = got
    return out


def restore(dim, saved: Dict[str, float], where: Optional[Where] = None) -> bool:
    """Put back the values values() read before a change made with where, each configuration its own. A change to
    ALL whose configurations all held one value goes back with one ALL write (the value stays one for all, not one
    per configuration); otherwise configurations that held the same value share one write. True when SolidWorks
    took every write."""
    if set(saved) == {""}:
        return _write(dim, saved[""], ACTIVE) == 0
    if where is not None and where.option == ALL and len(set(saved.values())) == 1:
        return _write(dim, next(iter(saved.values())), EVERY) == 0
    by_value: Dict[float, list] = {}
    for name, v in saved.items():
        by_value.setdefault(v, []).append(name)
    done = True
    for v, names in by_value.items():
        try:
            done = _write(dim, v, Where(SPECIFIC, tuple(names))) == 0 and done
        except Exception as e:
            logger.debug(f"Restore {v} in {names}: {e}")
            done = False
    return done


def value_in(dim, configuration: Optional[str] = None) -> Optional[float]:
    """One configuration's value of a dimension. None asked: the active one (the SystemValue the tools always read).
    A configuration with no value of its own (never given one, or given one by an ALL write since) reads 0.0, and
    the model there is at the active configuration's value (measured live): that value is returned (one more read).
    A configuration's own value of 0 reads the same 0.0 and cannot be told apart by any read: it is ruled out
    instead. The tools refuse a length <= 0 and a count < 1, set_value refuses a 0 into named configurations, and
    SOLIDWORKS does not hold a 0 angle in a configuration (live core.configurations.zero_angle). A configuration
    SolidWorks gives no value for: None (never another configuration's value)."""
    if not configuration:
        return float(dim.SystemValue)
    got = _own(dim, configuration)
    return float(dim.SystemValue) if got == 0.0 else got


def _own(dim, configuration: str) -> Optional[float]:
    """GetSystemValue3 for one configuration, as SolidWorks gives it (0.0: no value of its own), or None."""
    try:
        got = dim.GetSystemValue3(READ_SPECIFIC, _names_arg([configuration]))
        if got:
            return float(got[0])
    except Exception as e:
        logger.debug(f"GetSystemValue3({configuration}): {e}")
    return None
