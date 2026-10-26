"""Exporting a copy of the active document in another file format."""

import logging
import math
import os
import struct

from ..core.api import (document, doc_path, doc_title, doc_type, Err, geometry, ok, sw, tool, ToolError, units,
                        value)

logger = logging.getLogger(__name__)

@tool(
    group="export",
    description="Export the active part or assembly as a STEP file (.step / .stp) for other CAD programs. protocol AP214 (the default) keeps colours; AP203 does not. The document is not saved, renamed or changed. Reports the file size and how many solid bodies and components the file holds.",
    params={
        "filepath": {"type": "string", "description": "Where to write it, e.g. C:\\Work\\door.step"},
        "protocol": {"type": "string", "enum": ["AP214", "AP203"], "default": "AP214",
                     "description": "STEP application protocol"},
    },
    required=["filepath"],
    needs="any",
    writes=("filepath",),
)
def export_step(ctx, filepath, protocol="AP214"):
    doc = ctx.doc
    if doc_type(doc) not in (1, 2):
        raise ToolError("export_step works on a part or an assembly, but the active document is a drawing.",
                        Err.INPUT)
    key = str(protocol or "AP214").strip().upper().replace(" ", "")
    if key not in ("AP214", "AP203"):
        raise ToolError("protocol must be AP214 or AP203", Err.INPUT)
    if not isinstance(filepath, str) or not filepath.strip():
        raise ToolError("Give a filepath ending in .step", Err.INPUT)
    path = os.path.abspath(filepath.strip())
    if os.path.splitext(path)[1].lower() not in (".step", ".stp"):
        raise ToolError("filepath must end in .step or .stp", Err.INPUT)
    if os.path.isdir(path):
        raise ToolError(f"filepath is a folder: {path}", Err.INPUT)
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            raise ToolError(f"Cannot create the folder {folder} ({e.strerror or e})", Err.SAVE)

    title_before, path_before = doc_title(doc), doc_path(doc)
    stamp_before = os.path.getmtime(path) if os.path.exists(path) else None
    saved, errors = sw.export.sw_save_step_copy(ctx.app, doc, path, key == "AP214")

    written = saved and os.path.isfile(path) and os.path.getsize(path) > 0
    header = b""
    if written:
        with open(path, "rb") as f:
            header = f.read(400)
    if not written or not header.startswith(b"ISO-10303-21"):
        raise ToolError(f"SolidWorks did not write the STEP file (error code {errors.value}). "
                        f"An empty model cannot be exported.", Err.EXPORT)

    with open(path, "rb") as f:
        compact = f.read().decode("latin-1", "replace").replace(" ", "")
    schema = "AP214" if "AUTOMOTIVE_DESIGN" in compact else ("AP203" if "CONFIG_CONTROL_DESIGN" in compact else "?")
    data = {"path": path, "bytes": os.path.getsize(path), "protocol": schema,
            "solid_bodies": compact.count("MANIFOLD_SOLID_BREP("),
            "surface_bodies": compact.count("SHELL_BASED_SURFACE_MODEL("),
            "components": compact.count("NEXT_ASSEMBLY_USAGE_OCCURRENCE("),
            "colors": compact.count("COLOUR_RGB("),
            "document": title_before}
    message = (f"Exported {title_before} to {path} (STEP {schema}, {data['bytes']} bytes, "
               f"{data['solid_bodies']} solid bodies")
    if data["components"]:
        message += f", {data['components']} components"
    message += ")"
    if doc_title(doc) != title_before or doc_path(doc) != path_before:
        message += ". WARNING: the active document's name or path changed - check get_document_info."
    return ok(message, data)


STL_UNIT_CODES = {"mm": 0, "cm": 1, "m": 2, "inch": 3, "ft": 4}        # swLengthUnit_e
STL_RESOLUTIONS = ("coarse", "fine", "custom")


def _stl_triangles(path: str):
    """(format, triangles, zero-area triangles, [min xyz], [max xyz]) read from an STL file, in the file's own
    unit. SolidWorks writes a zero-area triangle between the bodies of an assembly in one file: it is counted
    apart and left out of the box."""
    with open(path, "rb") as f:
        raw = f.read()
    lo, hi = [float("inf")] * 3, [float("-inf")] * 3
    counts = [0, 0]

    def take(a, b, c):
        if a == b or b == c or a == c:
            counts[1] += 1
            return
        counts[0] += 1
        for point in (a, b, c):
            for i, v in enumerate(point):
                lo[i] = min(lo[i], v)
                hi[i] = max(hi[i], v)

    if len(raw) >= 84 and len(raw) == 84 + 50 * struct.unpack_from("<I", raw, 80)[0]:
        for row in struct.iter_unpack("<12fH", raw[84:]):
            take(row[3:6], row[6:9], row[9:12])
        return ("binary", *counts, lo, hi)
    corners = []
    for line in raw.decode("latin-1", "replace").splitlines():
        words = line.split()
        if words[:1] == ["vertex"] and len(words) == 4:
            corners.append(tuple(float(w) for w in words[1:]))
        elif words[:1] == ["endfacet"]:
            if len(corners) == 3:
                take(*corners)
            corners = []
    return ("ascii", *counts, lo, hi)


def _model_box_m(doc):
    """The model's bounding box in metres: the solid bodies' boxes for a part, SolidWorks' box for an assembly."""
    try:
        if doc_type(doc) == 1:
            boxes = [[float(v) for v in value(body, "GetBodyBox")] for body in geometry.solid_bodies(doc)]
        else:
            boxes = [[float(v) for v in doc.GetBox(0)]]
    except Exception as e:
        logger.debug(f"Cannot read the model box: {e}")
        return None
    if not boxes:
        return None
    return ([min(min(b[i], b[i + 3]) for b in boxes) for i in range(3)],
            [max(max(b[i], b[i + 3]) for b in boxes) for i in range(3)])


@tool(
    group="export",
    description="Export the active part or assembly as an STL file (.stl) for 3D printing or meshing: one file, in the model's own coordinates. resolution: fine (the default), coarse, or custom with deviation and angle. file_unit is the unit the file's numbers are in (STL files carry none): the document's length unit by default, or mm if that is not mm, cm, m, inch or ft. The document is not saved, renamed or changed, and SolidWorks' STL settings are put back afterwards. Reports the triangles and the file's bounding box in mm next to the model's. An STL file carries no unit: tell the user the file_unit the result names.",
    params={
        "filepath": {"type": "string", "description": "Where to write it, e.g. C:\\Work\\bracket.stl"},
        "resolution": {"type": "string", "enum": list(STL_RESOLUTIONS), "default": "fine",
                       "description": "How finely curved faces are cut into triangles"},
        "deviation": {"type": "number", "description": "With resolution custom: the largest gap between the triangles and the true surface (in unit)"},
        "angle": {"type": "number", "description": "With resolution custom: the largest angle between neighbouring triangles, degrees (0-90]"},
        "file_unit": {"type": "string", "enum": list(STL_UNIT_CODES),
                      "description": "Unit of the numbers in the file (default the document's length unit)"},
        "format": {"type": "string", "enum": ["binary", "ascii"], "default": "binary", "description": "File format"},
        "unit": {"type": "string", "description": "Unit of deviation: mm, cm, m, inch or ft (default: the session unit, mm unless set_units changed it)"},
    },
    required=["filepath"],
    needs="any",
    writes=("filepath",),
)
def export_stl(ctx, filepath, resolution=None, deviation=None, angle=None, file_unit=None, format=None, unit=None):
    doc = ctx.doc
    if doc_type(doc) not in (1, 2):
        raise ToolError("export_stl works on a part or an assembly, but the active document is a drawing.", Err.INPUT)
    quality = str(resolution or "fine").strip().lower()
    if quality not in STL_RESOLUTIONS:
        raise ToolError("resolution must be coarse, fine or custom", Err.INPUT)
    kind = str(format or "binary").strip().lower()
    if kind not in ("binary", "ascii"):
        raise ToolError("format must be binary or ascii", Err.INPUT)
    deviation_m = angle_rad = None
    if quality == "custom":
        if deviation is None or angle is None:
            raise ToolError("resolution custom needs both deviation and angle", Err.INPUT)
        deviation_m = units.size_m(deviation, unit, "deviation")
        if not 0 < float(angle) <= 90:
            raise ToolError("angle must be more than 0 and at most 90 degrees", Err.INPUT)
        angle_rad = math.radians(float(angle))
    elif deviation is not None or angle is not None:
        raise ToolError("deviation and angle are used only with resolution custom", Err.INPUT)
    if file_unit is not None:
        out_unit = units.parse(file_unit)
        if out_unit is None:
            raise ToolError(f"file_unit must be {units.NAMES}", Err.INPUT)
        unit_source = "given"
    else:
        try:
            code = int(doc.Extension.GetUserPreferenceInteger(document.LENGTH_UNIT, 0))
        except Exception:
            code = -1
        out_unit = {v: k for k, v in STL_UNIT_CODES.items()}.get(code)
        unit_source = "the document's unit" if out_unit else "mm, since the document's unit is not one STL takes here"
        out_unit = out_unit or "mm"
    if not isinstance(filepath, str) or not filepath.strip():
        raise ToolError("Give a filepath ending in .stl", Err.INPUT)
    path = os.path.abspath(filepath.strip())
    if os.path.splitext(path)[1].lower() != ".stl":
        raise ToolError("filepath must end in .stl", Err.INPUT)
    if os.path.isdir(path):
        raise ToolError(f"filepath is a folder: {path}", Err.INPUT)
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            raise ToolError(f"Cannot create the folder {folder} ({e.strerror or e})", Err.SAVE)

    title_before, path_before = doc_title(doc), doc_path(doc)
    stamp_before = os.path.getmtime(path) if os.path.exists(path) else None
    saved, errors, restored = sw.export.sw_save_stl_copy(ctx.app, doc, path, quality, kind == "binary",
                                                         STL_UNIT_CODES[out_unit], deviation_m, angle_rad)
    written = saved and os.path.isfile(path) and os.path.getsize(path) > 0 \
        and os.path.getmtime(path) != stamp_before      # an old file must not pass for the new one
    found = _stl_triangles(path) if written else None
    if not found or not found[1]:
        if found:
            try:
                os.remove(path)                         # the file this call wrote holds nothing: not left behind
            except OSError as e:
                logger.debug(f"Could not remove the empty STL file: {e}")
        raise ToolError(f"SolidWorks did not write the STL file (error code {errors.value}). "
                        f"An empty model cannot be exported.", Err.EXPORT)

    in_mm = units.TO_METRES[out_unit] * 1000.0
    file_format, triangles, empty, lo, hi = found
    data = {"path": path, "bytes": os.path.getsize(path), "format": file_format, "triangles": triangles,
            "zero_area_triangles": empty,
            "file_unit": out_unit, "file_unit_from": unit_source, "resolution": quality,
            "file_box_mm": {"min": [round(v * in_mm, 4) for v in lo], "max": [round(v * in_mm, 4) for v in hi]},
            "document": title_before, "settings_restored": restored}
    if quality == "custom":
        data["deviation"] = deviation
        data["deviation_unit"] = units.label(unit)
        data["angle"] = angle
    model_box = _model_box_m(doc)
    if model_box:
        data["model_box_mm"] = {"min": [round(v * 1000, 4) for v in model_box[0]],
                                "max": [round(v * 1000, 4) for v in model_box[1]]}
    message = (f"Exported {title_before} to {path} (STL {file_format}, {quality}, {triangles} triangles, "
               f"numbers in {out_unit} ({unit_source}), {data['bytes']} bytes)")
    if not restored:
        message += ". WARNING: SolidWorks' STL settings could not all be put back"
    if doc_title(doc) != title_before or doc_path(doc) != path_before:
        message += ". WARNING: the active document's name or path changed - check get_document_info."
    return ok(message, data)
