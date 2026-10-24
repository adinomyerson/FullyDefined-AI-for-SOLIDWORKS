"""Exporting a copy of the active document in another file format."""

import logging
import os

from ..core.api import doc_path, doc_title, doc_type, Err, ok, sw, tool, ToolError

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
