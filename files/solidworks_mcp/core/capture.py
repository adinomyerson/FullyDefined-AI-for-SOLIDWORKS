"""
A picture of the graphics view (capture_view, and step_mate's pictures): the view, the size, a section and
display options for the capture only. Shared so no tool calls another tool.
"""

import logging
import math
import os
import tempfile

import pythoncom
import win32com.client

from . import units
from .com import doc_title, doc_type, plane_feature_name, value
from .images import bmp_size, bmp_to_png
from .results import Err, ok, ToolError

logger = logging.getLogger(__name__)

MAX_IMAGE_SIZE = 1280           # longest side of a picture sent back
MIN_IMAGE_SIZE = 256
MAX_FILE_IMAGE = 4096           # longest side of a picture written to a file

VIEW_NAMES = ("current", "isometric", "trimetric", "dimetric",
              "front", "back", "left", "right", "top", "bottom")
# named view -> (SolidWorks view name, swStandardViews_e)
STANDARD_VIEWS = {
    "front": ("*Front", 1), "back": ("*Back", 2), "left": ("*Left", 3), "right": ("*Right", 4),
    "top": ("*Top", 5), "bottom": ("*Bottom", 6),
    "isometric": ("*Isometric", 7), "trimetric": ("*Trimetric", 8), "dimetric": ("*Dimetric", 9),
}

def _bad(message: str):
    return ToolError(message, Err.INPUT)


def _vector(v, label: str):
    """A unit vector (x, y, z) from {x, y, z} or a list."""
    try:
        t = (float(v["x"]), float(v["y"]), float(v["z"])) if isinstance(v, dict) else tuple(float(c) for c in v)
        if len(t) != 3:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise _bad(f"{label} must be a vector {{x, y, z}}")
    length = math.sqrt(sum(c * c for c in t))
    if not math.isfinite(length) or length < 1e-9:
        raise _bad(f"{label} must not be a zero vector")
    return tuple(c / length for c in t)


def _native_size(doc):
    """The size in pixels at which SolidWorks renders the graphics area. The picture must keep
    this shape: any other ratio crops the sides of the view and can cut the model off."""
    path = None
    try:
        handle, path = tempfile.mkstemp(prefix="sw_capture_", suffix=".bmp")
        os.close(handle)
        if doc.SaveBMP(path, 0, 0):                 # 0 x 0 renders at the native size
            with open(path, "rb") as f:
                size = bmp_size(f.read(26))
            if size:
                return size
    except Exception as e:
        logger.debug(f"Could not read the native view size: {e}")
    finally:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    try:
        view = doc.ActiveView
        if int(view.FrameWidth) > 0 and int(view.FrameHeight) > 0:
            return int(view.FrameWidth), int(view.FrameHeight)
    except Exception as e:
        logger.debug(f"Could not read the view frame size: {e}")
    return None


def _set_direction(doc, toward, up):
    """Turn the view so the camera sits in direction `toward` from the model and `up` points up.
    The orientation matrix has one row per model axis: its parts along screen-right, screen-up
    and towards the viewer. (CreateTransform fails late-bound, so the view's own transform is rewritten.)"""
    right = (up[1] * toward[2] - up[2] * toward[1],
             up[2] * toward[0] - up[0] * toward[2],
             up[0] * toward[1] - up[1] * toward[0])
    data = []
    for i in range(3):
        data += [right[i], up[i], toward[i]]
    data += [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
    view = doc.ActiveView
    transform = view.Orientation3
    transform.ArrayData = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, data)
    view.Orientation3 = transform
    doc.GraphicsRedraw2()


def view_state(doc):
    """What the on-screen view shows: orientation, scale and translation, to put back with restore_view."""
    view = doc.ActiveView
    return {"orientation": [float(v) for v in value(view.Orientation3, "ArrayData")],
            "scale": float(view.Scale2),
            "translation": [float(v) for v in value(view.Translation3, "ArrayData")]}


def restore_view(doc, state) -> None:
    """Put the on-screen view back as view_state read it."""
    view = doc.ActiveView
    transform = view.Orientation3
    transform.ArrayData = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, state["orientation"])
    view.Orientation3 = transform
    view.Scale2 = state["scale"]
    move = view.Translation3
    move.ArrayData = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, state["translation"])
    view.Translation3 = move
    doc.GraphicsRedraw2()


def _crop_fractions(region, zoom):
    """(x, y, width, height) as fractions of the picture from its top left, or None for the whole picture."""
    if region is not None and zoom is not None:
        raise _bad("Give region or zoom, not both")
    if zoom is not None:
        if isinstance(zoom, bool) or not isinstance(zoom, (int, float)) or not 1 <= zoom <= 20:
            raise _bad("zoom must be a number from 1 to 20")
        if zoom == 1:
            return None
        side = 1.0 / zoom
        return ((1 - side) / 2, (1 - side) / 2, side, side)
    if region is None:
        return None
    keys = ("x", "y", "width", "height")
    if not isinstance(region, dict) or any(k not in region for k in keys) or any(k not in keys for k in region):
        raise _bad("region must be {x, y, width, height}: fractions of the picture (0 to 1) from its top left")
    x, y, w, h = (region[k] for k in keys)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (x, y, w, h)):
        raise _bad("region x, y, width and height must be numbers from 0 to 1")
    if not (0 <= x < 1 and 0 <= y < 1 and 0.01 <= w <= 1 and 0.01 <= h <= 1 and x + w <= 1 + 1e-9
            and y + h <= 1 + 1e-9):
        raise _bad("region must lie inside the picture: x, y from 0, width and height from 0.01, x + width and "
                   "y + height at most 1")
    if w == 1 and h == 1:
        return None
    return (float(x), float(y), float(w), float(h))


def capture(app, doc, view="current", zoom_to_fit=True, max_size=MAX_IMAGE_SIZE, direction=None, up=None,
            output_path=None, width=None, height=None, hide_annotations=False, white_background=False,
            section=None, unit=None, return_image=True, region=None, zoom=None, keep_view=False):
    """A PNG of the document's graphics view, as capture_view describes: a Result with the image, or a
    refusal. Everything is checked before the view is touched; display changes are undone afterwards.
    region / zoom crop a larger render (zoom = the centred 1/zoom of the picture). The on-screen view is put back
    afterwards (orientation, scale, translation) unless keep_view (step_mate keeps its framing over its pictures and
    restores the view itself)."""
    view = (view or "current").strip().lower()
    if view not in VIEW_NAMES:
        raise _bad(f"Unknown view '{view}'. Use one of: {', '.join(VIEW_NAMES)}")
    try:
        max_size = int(max_size)
    except (TypeError, ValueError):
        max_size = MAX_IMAGE_SIZE
    max_size = max(MIN_IMAGE_SIZE, min(MAX_IMAGE_SIZE, max_size))

    # ---- everything is checked before the view is touched
    toward = up_vec = None
    if up is not None and direction is None:
        raise _bad("up needs a direction as well")
    if direction is not None:
        toward = _vector(direction, "direction")
        if up is None:
            up = (0.0, 1.0, 0.0) if abs(toward[1]) < 0.999 else (0.0, 0.0, 1.0)
        up_vec = _vector(up, "up")
        along = sum(a * b for a, b in zip(up_vec, toward))
        up_vec = tuple(u - along * t for u, t in zip(up_vec, toward))
        length = math.sqrt(sum(c * c for c in up_vec))
        if length < 1e-6:
            raise _bad("up must not point along the camera direction")
        up_vec = tuple(c / length for c in up_vec)

    for label, v in (("width", width), ("height", height)):
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or not 64 <= v <= MAX_FILE_IMAGE):
            raise _bad(f"{label} must be a whole number of pixels from 64 to {MAX_FILE_IMAGE}")
    for label, v in (("hide_annotations", hide_annotations), ("white_background", white_background),
                     ("return_image", return_image)):
        if not isinstance(v, bool):
            raise _bad(f"{label} must be true or false")

    crop = _crop_fractions(region, zoom)

    if output_path is not None:
        if not isinstance(output_path, str) or not output_path.strip():
            raise _bad("output_path must be a file path ending in .png")
        output_path = os.path.abspath(output_path.strip())
        if not output_path.lower().endswith(".png"):
            raise _bad("output_path must end in .png")
        if os.path.isdir(output_path):
            raise _bad(f"output_path is a folder: {output_path}")
        folder = os.path.dirname(output_path)
        if folder and not os.path.isdir(folder):
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError as e:
                raise ToolError(f"Cannot create the folder {folder} ({e.strerror or e})", Err.SAVE)
    if (width or height) and max(width or 0, height or 0) > MAX_IMAGE_SIZE and not output_path:
        raise _bad(f"A picture larger than {MAX_IMAGE_SIZE} px is only written to a file: give output_path")

    section_plane, section_offset, section_reverse = None, 0.0, False
    if section is not None:
        if not isinstance(section, dict) or not section.get("plane"):
            raise _bad('section must be {"plane": "Front" | "Top" | "Right" | plane name, "offset": ..., "reverse": ...}')
        unknown = [k for k in section if k not in ("plane", "offset", "reverse")]
        if unknown:
            raise _bad(f"section has unknown field(s) {', '.join(map(str, unknown))}")
        if doc_type(doc) not in (1, 2):
            raise _bad("A section needs a part or an assembly")
        offset = section.get("offset", 0)
        if isinstance(offset, bool) or not isinstance(offset, (int, float)) or not math.isfinite(offset):
            raise _bad("section offset must be a number")
        section_offset = units.to_m(float(offset), unit)
        if abs(section_offset) > 500:
            raise _bad("section offset is more than 500 m")
        section_reverse = section.get("reverse", False)
        if not isinstance(section_reverse, bool):
            raise _bad("section reverse must be true or false")
        plane_name = plane_feature_name(str(section["plane"]))
        try:
            section_plane = doc.FeatureByName(plane_name)
        except Exception:
            section_plane = None
        if section_plane is None or value(section_plane, "GetTypeName2") != "RefPlane":
            raise ToolError(f"No plane named '{plane_name}' for the section", Err.SELECTION)

    restore, bmp_path = [], None
    held = None
    if not keep_view:
        try:
            held = view_state(doc)
        except Exception as e:
            logger.debug(f"Could not read the view to put it back: {e}")
    try:
        # ---- temporary display changes, undone in `finally`
        if hide_annotations:
            try:
                ext = doc.Extension
                if not bool(ext.GetUserPreferenceToggle(198, 0)):          # swViewDisplayHideAllTypes
                    ext.SetUserPreferenceToggle(198, 0, True)
                    restore.append(lambda: ext.SetUserPreferenceToggle(198, 0, False))
            except Exception as e:
                logger.debug(f"Could not hide the annotations: {e}")
        if white_background:
            try:
                appearance = int(app.GetUserPreferenceIntegerValue(305))   # swColorsBackgroundAppearance
                color = int(app.GetUserPreferenceIntegerValue(99))         # swSystemColorsViewportBackground
                restore.append(lambda: (app.SetUserPreferenceIntegerValue(305, appearance),
                                        app.SetUserPreferenceIntegerValue(99, color)))
                app.SetUserPreferenceIntegerValue(305, 0)                  # plain
                app.SetUserPreferenceIntegerValue(99, 0xFFFFFF)
            except Exception as e:
                logger.debug(f"Could not set a white background: {e}")
        if section_plane is not None:
            manager = doc.ModelViewManager
            data = value(manager, "CreateSectionViewData")
            data.FirstPlane = section_plane
            data.FirstOffset = section_offset
            data.FirstReverseDirection = section_reverse
            if not manager.CreateSectionView(data):
                raise ToolError("SolidWorks could not make that section (the plane may not cut the model)",
                                Err.INTERNAL)
            restore.append(lambda: value(manager, "RemoveSectionView"))

        # ---- orientation and zoom (both change what is shown on screen)
        if toward is not None:
            _set_direction(doc, toward, up_vec)
        elif view != "current":
            name, view_id = STANDARD_VIEWS[view]
            doc.ShowNamedView2(name, view_id)
        if zoom_to_fit:
            try:
                doc.ViewZoomtofit2()
            except Exception as e:
                logger.debug(f"Zoom to fit failed: {e}")
        if restore:
            try:
                doc.GraphicsRedraw2()
            except Exception:
                pass
        seen = None
        try:                                               # the orientation the picture is taken in
            a = [float(v) for v in value(doc.ActiveView.Orientation3, "ArrayData")]
            seen = [[round(c, 4) + 0.0 for c in a[i * 3:i * 3 + 3]] for i in range(3)]
        except Exception as e:
            logger.debug(f"Could not read the view orientation: {e}")

        # ---- picture size: the shape of the graphics area, then padded to the size asked for
        native = _native_size(doc) or (4, 3)
        cx, cy, cw, ch = crop or (0.0, 0.0, 1.0, 1.0)
        frame = (native[0] * cw, native[1] * ch)        # the part of the view that ends up in the picture
        pad_to = None
        if width or height:
            out_w = width or max(64, round(height * frame[0] / frame[1]))
            out_h = height or max(64, round(width * frame[1] / frame[0]))
            out_w, out_h = min(out_w, MAX_FILE_IMAGE), min(out_h, MAX_FILE_IMAGE)
            scale = min(out_w / frame[0], out_h / frame[1])
            pad_to = (out_w, out_h)
        else:
            scale = max_size / max(frame)
        scale = min(scale, MAX_FILE_IMAGE / max(native))  # the whole view is rendered, then cropped
        render_w, render_h = max(1, round(native[0] * scale)), max(1, round(native[1] * scale))
        crop_px = None
        if crop:
            crop_px = (round(cx * render_w), round(cy * render_h), max(1, round(cw * render_w)),
                       max(1, round(ch * render_h)))
        elif pad_to:
            render_w, render_h = min(render_w, pad_to[0]), min(render_h, pad_to[1])

        handle, bmp_path = tempfile.mkstemp(prefix="sw_capture_", suffix=".bmp")
        os.close(handle)
        if not doc.SaveBMP(bmp_path, render_w, render_h) or not os.path.exists(bmp_path) \
                or os.path.getsize(bmp_path) == 0:
            raise ToolError("SolidWorks could not render the view (SaveBMP failed)", Err.INTERNAL)
        with open(bmp_path, "rb") as f:
            png, out_width, out_height = bmp_to_png(f.read(), pad_to, crop_px)

        title = doc_title(doc)
        shown = "custom direction" if toward is not None else view
        data = {"document": title, "view": shown, "zoom_to_fit": bool(zoom_to_fit),
                "width": out_width, "height": out_height, "png_bytes": len(png)}
        if seen is not None:
            data["view_orientation"] = seen                # rows: model x, y, z as (right, up, toward the viewer)
        if crop:
            data["region"] = {"x": round(cx, 4), "y": round(cy, 4), "width": round(cw, 4), "height": round(ch, 4)}
            if zoom is not None:
                data["zoom"] = float(zoom)
            wanted = max(pad_to) if pad_to else max_size
            if max(out_width, out_height) < wanted - 1:
                data["smaller_than_asked"] = {"asked": wanted, "got": max(out_width, out_height)}
        if toward is not None:
            data["direction"] = [round(c, 4) + 0.0 for c in toward]
            data["up"] = [round(c, 4) + 0.0 for c in up_vec]
        if section_plane is not None:
            data["section"] = {"plane": str(section_plane.Name),
                               "offset": round(units.from_m(section_offset, unit), 4) + 0.0,
                               "reverse": section_reverse}
        message = f"Captured {shown} view of {title} ({out_width}x{out_height} px)"
        if "smaller_than_asked" in data:
            message += (f" - smaller than the {data['smaller_than_asked']['asked']} px asked: that crop of the view "
                        f"renders at most this size")
        if output_path:
            try:
                with open(output_path, "wb") as f:
                    f.write(png)
            except OSError as e:
                raise ToolError(f"Could not write {output_path} ({e.strerror or e})", Err.SAVE)
            data["file"] = output_path
            message += f", saved to {output_path}"
        inline = return_image and max(out_width, out_height) <= MAX_IMAGE_SIZE
        data["image_returned"] = bool(inline)
        if return_image and not inline:
            message += f" (larger than {MAX_IMAGE_SIZE} px: in the file only)"
        return ok(message, data, image=png if inline else None)

    finally:
        if held is not None:
            try:
                restore_view(doc, held)
            except Exception as e:
                logger.debug(f"Could not put the view back: {e}")
        for undo in reversed(restore):
            try:
                undo()
            except Exception as e:
                logger.debug(f"Could not restore the display: {e}")
        if restore:
            try:
                doc.GraphicsRedraw2()
            except Exception:
                pass
        if bmp_path and os.path.exists(bmp_path):
            try:
                os.remove(bmp_path)
            except OSError:
                pass
