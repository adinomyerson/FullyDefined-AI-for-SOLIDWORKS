"""The default unit for lengths."""

from ..core.api import ok, tool, units


@tool(
    group="core",
    description="Set the default length unit, mm, inch, cm or m, used when a tool's unit input is left out.",
    params={"unit": {"type": "string", "enum": ["mm", "inch", "m", "cm"], "description": "New default length unit"}},
    required=["unit"],
)
def set_units(ctx, unit):
    units.set_default(unit)
    return ok(f"Default unit set to: {unit}", {"unit": unit})
