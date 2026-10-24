"""
Lengths. The API works in metres; tools take the default unit (mm unless set_units
changed it) or a unit given on the call.

Holds the default unit: state that must outlive a reload of the other modules.
"""

from . import settings
from .results import Err, ToolError

TO_METRES = {"mm": 0.001, "cm": 0.01, "m": 1.0, "inch": 0.0254, "ft": 0.3048}
# how the accepted units read in a message or an input description: "mm, cm, m, inch or ft"
NAMES = ", ".join(list(TO_METRES)[:-1]) + " or " + list(TO_METRES)[-1]

ALIASES = {
    "mm": "mm", "millimeter": "mm", "millimeters": "mm",
    "cm": "cm", "centimeter": "cm", "centimeters": "cm",
    "m": "m", "meter": "m", "meters": "m",
    "inch": "inch", "in": "inch", "inches": "inch", '"': "inch",
    "ft": "ft", "foot": "ft", "feet": "ft", "'": "ft",
}

_default = ALIASES.get(str(settings.load().get("default_unit", "mm")).lower(), "mm")


def parse(unit) -> str:
    """The unit's canonical name, or None if it is unknown."""
    if not isinstance(unit, str):
        return None
    return ALIASES.get(unit.strip().lower())


def check(unit) -> None:
    """Refuse an unknown unit given on a call (None means the default)."""
    if unit is not None and parse(unit) is None:
        raise ToolError(f"Unknown unit '{unit}'. Use {NAMES}.", Err.INPUT)


def label(unit=None) -> str:
    """The unit as shown in a result: the one given on the call, else the default."""
    return unit if unit is not None else _default


def default() -> str:
    return _default


def set_default(unit) -> str:
    global _default
    name = parse(unit)
    if name is None:
        raise ToolError(f"Unknown unit '{unit}'. Use {NAMES}.", Err.INPUT)
    _default = name
    return name


def to_m(value: float, unit=None) -> float:
    return float(value) * TO_METRES[parse(unit) if unit is not None else _default]


def from_m(metres: float, unit=None) -> float:
    return float(metres) / TO_METRES[parse(unit) if unit is not None else _default]


MAX_SIZE_M = 500.0          # a SolidWorks model fits in a 1000 m cube


def check_size(value_m, label: str) -> None:
    """Refuse a size that is not a positive number inside the modelling space."""
    import math
    try:
        size = float(value_m)
    except (TypeError, ValueError):
        raise ToolError(f"{label} must be a number", Err.INPUT)
    if not math.isfinite(size) or size <= 0:
        raise ToolError(f"{label} must be greater than 0", Err.INPUT)
    if size > MAX_SIZE_M:
        raise ToolError(f"{label} is larger than SolidWorks' modelling space (500 m). "
                        f"Check the value and the unit.", Err.INPUT)


def size_m(size, unit, label: str) -> float:
    """A size given by the caller, in metres; refused unless it is a positive number inside the modelling space."""
    numeric = isinstance(size, (int, float)) and not isinstance(size, bool)
    check_size(to_m(size, unit) if numeric else size, label)
    return to_m(size, unit)
