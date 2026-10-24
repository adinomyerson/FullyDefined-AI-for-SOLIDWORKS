"""Colours of a part, or of components inside an assembly.

A colour set on a part is stored in the part, so every assembly that uses it shows it. A colour set on a
component is stored in that assembly only; the part file is not changed.
"""

import logging
from typing import List, Optional


from ..core.api import assembly, doc_type, Err, ok, tool, ToolError
from ..core.api import sw

logger = logging.getLogger(__name__)

COLOR_NAMES = {
    "red": (220, 40, 40), "green": (40, 160, 70), "blue": (40, 90, 200), "yellow": (240, 210, 40),
    "orange": (240, 140, 30), "purple": (130, 60, 170), "cyan": (40, 190, 210), "magenta": (210, 50, 160),
    "pink": (240, 150, 180), "brown": (130, 85, 50), "white": (255, 255, 255), "black": (20, 20, 20),
    "grey": (128, 128, 128), "gray": (128, 128, 128), "light_grey": (200, 200, 200),
    "light_gray": (200, 200, 200), "dark_grey": (70, 70, 70), "dark_gray": (70, 70, 70),
    "silver": (192, 192, 196), "gold": (212, 175, 55),
}
# R, G, B, ambient, diffuse, specular, shininess, transparency, emission


# ---------------------------------------------------------------- SolidWorks calls


# ---------------------------------------------------------------- helpers

def _own_properties(owner) -> Optional[List[float]]:
    """The 9 property values of a document or component, or None when it has none of its own."""
    try:
        values = owner.MaterialPropertyValues
    except Exception as e:
        logger.debug(f"MaterialPropertyValues failed: {e}")
        return None
    if values is None:
        return None
    values = [float(v) for v in values]
    return values if len(values) >= 9 else None


def _color_row(values) -> dict:
    return {"rgb": [int(round(max(0.0, min(1.0, v)) * 255)) for v in values[0:3]],
            "transparency": round(float(values[7]), 4) + 0.0}


def _parse_color(color, rgb, transparency):
    """((r, g, b) 0..255 or None, transparency or None), or a refusal."""
    if color is not None and rgb is not None:
        raise ToolError("Give color or rgb, not both", Err.INPUT)
    triple = None
    if color is not None:
        key = str(color).strip().lower().replace(" ", "_").replace("-", "_")
        if key.startswith("#") and len(key) == 7:
            try:
                triple = tuple(int(key[i:i + 2], 16) for i in (1, 3, 5))
            except ValueError:
                triple = None
        else:
            triple = COLOR_NAMES.get(key)
        if triple is None:
            names = sorted(n for n in COLOR_NAMES if "gray" not in n)
            raise ToolError(f"Unknown color '{color}'. Use one of: {', '.join(names)}, a hex value like '#3366cc', "
                            f"or rgb = [r, g, b]", Err.INPUT)
    elif rgb is not None:
        if isinstance(rgb, dict):
            rgb = [rgb.get("r"), rgb.get("g"), rgb.get("b")]
        if (not isinstance(rgb, (list, tuple)) or len(rgb) != 3
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in rgb)):
            raise ToolError("rgb must be three numbers [r, g, b], each 0 to 255", Err.INPUT)
        if any(v < 0 or v > 255 for v in rgb):
            raise ToolError(f"rgb values must be 0 to 255, got {list(rgb)}", Err.INPUT)
        triple = tuple(int(round(v)) for v in rgb)
    if transparency is not None:
        if isinstance(transparency, bool) or not isinstance(transparency, (int, float)) or not 0 <= transparency <= 1:
            raise ToolError("transparency must be a number from 0 (solid) to 1 (invisible)", Err.INPUT)
        transparency = float(transparency)
    if triple is None and transparency is None:
        raise ToolError("Give a color (name or '#rrggbb'), rgb = [r, g, b], or a transparency", Err.INPUT)
    return triple, transparency


def _component_list(component, components) -> list:
    names = []
    for given in (component, components):
        if given is None:
            continue
        if isinstance(given, str):
            names.append(given)
        elif isinstance(given, (list, tuple)) and all(isinstance(v, str) for v in given):
            names.extend(given)
        else:
            raise ToolError("components must be a list of component names", Err.INPUT)
    if any(not n.strip() for n in names):
        raise ToolError("a component name is empty", Err.INPUT)
    return names


def _matches(got, want) -> bool:
    # SolidWorks keeps a component colour in 8-bit steps and can store it one step off (a part colour of
    # 228/255 reads back as 227/255). A limit of 0.003 failed every first call that kept the part's own colour
    # (transparency only) and passed the second. One step is allowed.
    step = 1.5 / 255.0
    return got is not None and all(abs(a - b) < step for a, b in zip(got[0:3], want[0:3])) and abs(got[7] - want[7]) < step


# ---------------------------------------------------------------- tools

@tool(
    group="features",
    description="Set the colour of the active part, or of components of the active assembly. On a part the colour is stored in the part, so every assembly using it shows it (save the part to keep it). On an assembly, give components (names, or paths into sub-assemblies such as 'Door-1/Slat-3'; a list does several at once): the colour is stored in the assembly and the part files stay unchanged. Colour as a name (red, green, blue, yellow, orange, purple, cyan, magenta, pink, brown, white, black, grey, light_grey, dark_grey, silver, gold), hex '#rrggbb', or rgb [r, g, b] with 0 to 255 each; transparency from 0 (solid) to 1 is optional. Reports the colour read back for each target.",
    params={"color": {"type": "string", "description": "Colour name or hex value '#rrggbb'"},
            "rgb": {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 255}, "minItems": 3,
                    "maxItems": 3, "description": "Instead of color: [r, g, b], each 0 to 255"},
            "transparency": {"type": "number", "minimum": 0, "maximum": 1,
                             "description": "0 = solid, 1 = invisible. Left as it is when not given."},
            "components": {"type": "array", "items": {"type": "string"}, "description": "In an assembly: the components to colour"},
            "component": {"type": "string", "description": "In an assembly: one component, instead of the list"}},
    needs="part_or_assembly",
)
def set_color(ctx, color=None, rgb=None, transparency=None, components=None, component=None):
    doc = ctx.doc
    triple, alpha = _parse_color(color, rgb, transparency)
    names = _component_list(component, components)

    def updated(values):
        values = list(values[0:9])
        if triple is not None:
            values[0:3] = [c / 255.0 for c in triple]
        if alpha is not None:
            values[7] = alpha
        return values

    if doc_type(doc) == 1:
        if names:
            raise ToolError("The active document is a part, which has no components. Leave components out to colour "
                            "the part itself.", Err.INPUT)
        want = updated(_own_properties(doc) or list(sw.appearance.DEFAULT_PROPERTIES))
        sw.appearance.sw_set_properties(doc, want)
        sw.common.sw_redraw(doc)
        got = _own_properties(doc)
        if not _matches(got, want):
            raise ToolError("SolidWorks did not take the colour for this part", Err.INTERNAL)
        row = _color_row(got)
        return ok(f"Part colour set to rgb {row['rgb']}, transparency {row['transparency']} (stored in the part: save "
                  f"the part to keep it)", {"target": "part", **row})

    if not names:
        raise ToolError("Name the components to colour (components = [...]). To colour a part everywhere it is used, "
                        "open the part and call set_color there.", Err.INPUT)
    found = []                                  # every component first: nothing is coloured unless all exist
    for name in names:
        comp, reason = assembly.find_component(doc, name)
        if comp is None:
            raise ToolError(reason[0].upper() + reason[1:] + ". Nothing was coloured.", Err.SELECTION)
        if str(comp.Name2) not in [str(c.Name2) for c in found]:
            found.append(comp)
    rows, failed = [], []
    for comp in found:
        want = updated(_own_properties(comp) or sw.appearance.sw_model_properties(comp))
        try:
            sw.appearance.sw_set_properties(comp, want)
        except Exception as e:
            logger.debug(f"Could not colour {comp.Name2}: {e}")
        got = _own_properties(comp)
        if _matches(got, want):
            rows.append({"component": str(comp.Name2), **_color_row(got)})
        else:
            failed.append(str(comp.Name2))
    sw.common.sw_redraw(doc)
    if failed:
        raise ToolError(f"SolidWorks did not take the colour for: {', '.join(failed)}"
                        + (f". Coloured: {', '.join(r['component'] for r in rows)}" if rows else ""), Err.INTERNAL,
                        {"colored": rows, "failed": failed})
    first = rows[0]
    return ok(f"{len(rows)} component(s) set to rgb {first['rgb']}, transparency {first['transparency']} (stored in "
              f"this assembly; the part files are unchanged)", {"target": "components", "colored": rows})


@tool(
    group="features",
    description="Read the colour (rgb 0 to 255) and transparency of the active part, or of components of the active assembly (all parts when none are named). For a component it also says whether the colour is set in the assembly or comes from the part file. Read-only.",
    params={"components": {"type": "array", "items": {"type": "string"}, "description": "In an assembly: the components to read (default: every part)"},
            "component": {"type": "string", "description": "In an assembly: one component, instead of the list"}},
    needs="part_or_assembly",
)
def get_color(ctx, components=None, component=None):
    doc = ctx.doc
    names = _component_list(component, components)
    if doc_type(doc) == 1:
        if names:
            raise ToolError("The active document is a part, which has no components.", Err.INPUT)
        row = _color_row(_own_properties(doc) or list(sw.appearance.DEFAULT_PROPERTIES))
        return ok(f"Part colour rgb {row['rgb']}, transparency {row['transparency']}", {"target": "part", **row})
    if names:
        comps = [assembly.component(doc, name) for name in names]
    else:
        comps = [c for c in assembly.all_components(doc) if assembly.solid_bodies(c)]
    rows = []
    for comp in comps:
        own = _own_properties(comp)
        row = {"component": str(comp.Name2)}
        row.update(_color_row(own if own is not None else sw.appearance.sw_model_properties(comp)))
        row["set_in"] = "assembly" if own is not None else "part"
        rows.append(row)
    return ok(f"Colours of {len(rows)} component(s)", {"target": "components", "colors": rows})
