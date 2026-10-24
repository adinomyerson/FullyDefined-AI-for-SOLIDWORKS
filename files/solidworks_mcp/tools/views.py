"""Seeing the model: a picture of the graphics view (the work is in core/capture.py)."""

from ..core.api import capture, tool

VIEW_NAMES = capture.VIEW_NAMES
MAX_IMAGE_SIZE = capture.MAX_IMAGE_SIZE

_POINT = {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}},
          "required": ["x", "y", "z"]}


@tool(
    group="core",
    description="Capture a PNG of the active document's graphics view, to inspect the model visually: a standard view, or a camera direction with an up direction (the default up is +Y; for a Z-up model give up = {x:0, y:0, z:1}). A picture wider or taller than 1280 pixels is written to output_path only, not sent back. A section cut by a plane can be added for the capture only. To see detail, return only part of the picture: region (fractions from the top left) or zoom (1-20, on the centre). The screen is left as it was: the on-screen view is restored afterwards, whatever view or zoom_to_fit was used.",
    params={
        "view": {"type": "string", "enum": list(VIEW_NAMES), "default": "current",
                 "description": "View orientation ('current' keeps what is on screen)"},
        "direction": dict(_POINT, description="Instead of view: where the camera is, seen from the model, in model axes. {0,0,1} = front view, {1,1,1} = an isometric view."),
        "up": dict(_POINT, description="With direction: the model direction that points up in the picture (default +Y)"),
        "zoom_to_fit": {"type": "boolean", "default": True, "description": "Zoom to fit before capturing"},
        "max_size": {"type": "integer", "default": 1280,
                     "description": "Longest side in pixels (256-1280), when width / height are not given"},
        "width": {"type": "integer", "description": "Exact picture width in pixels (64-4096)"},
        "height": {"type": "integer", "description": "Exact picture height in pixels (64-4096)"},
        "output_path": {"type": "string",
                        "description": "Also write the PNG to this file on the computer, e.g. C:\\Work\\door.png"},
        "hide_annotations": {"type": "boolean", "default": False,
                             "description": "Hide dimensions, notes, planes, axes, origins and sketches for the capture"},
        "white_background": {"type": "boolean", "default": False, "description": "Plain white background for the capture"},
        "section": {"type": "object",
                    "properties": {"plane": {"type": "string", "description": "Front, Top, Right or a plane name"},
                                   "offset": {"type": "number", "description": "Distance of the cut from the plane"},
                                   "reverse": {"type": "boolean", "description": "Keep the other side"}},
                    "required": ["plane"], "description": "Cut the model with a plane for this capture only"},
        "unit": {"type": "string", "description": "Unit of the section offset (mm, cm, m, inch or ft)"},
        "return_image": {"type": "boolean", "default": True,
                         "description": "false = do not send the picture back, only write the file"},
        "region": {"type": "object",
                   "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                                  "width": {"type": "number"}, "height": {"type": "number"}},
                   "required": ["x", "y", "width", "height"],
                   "description": "Only this part of the picture, in fractions (0-1) from the top left, at full resolution"},
        "zoom": {"type": "number", "description": "Instead of region: enlarge the centre of the picture by this factor (1-20)"},
    },
    needs="any",
    writes=("output_path",),
)
def capture_view(ctx, view="current", zoom_to_fit=True, max_size=MAX_IMAGE_SIZE, direction=None, up=None,
                 output_path=None, width=None, height=None, hide_annotations=False, white_background=False,
                 section=None, unit=None, return_image=True, region=None, zoom=None):
    doc = ctx.doc
    return capture.capture(ctx.app, doc, view=view, zoom_to_fit=zoom_to_fit, max_size=max_size, direction=direction,
                           up=up, output_path=output_path, width=width, height=height,
                           hide_annotations=hide_annotations, white_background=white_background, section=section,
                           unit=unit, return_image=return_image, region=region, zoom=zoom)
