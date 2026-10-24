"""
The one door to the frame. Tools import from here and from nowhere else in solidworks_mcp.

THE CONTRACT. Everything listed in __all__ is public, and a change to it is deliberate: a new need is a new
function or an optional input, never a removed or renamed name, a reordered parameter or a changed default.

What is here:
  tool, ok, ToolError, Err      declare a tool, return a result, refuse, error codes
  Context                       the first argument of every tool (ctx.app, ctx.doc, ctx.to_m ...)
  units, model, geometry,       namespaces of helpers: lengths and sizes; bodies, volume and the feature
  sketch, selecting, images,    tree; faces and edges; the open sketch; picking faces; BMP to PNG;
  install, settings             where SolidWorks is installed; the settings
  sw                            the SolidWorks calls that change a model, by area (sw.drawing.sw_finish ...)
  connection                    the one SolidWorks connection (connect, alive, app)
  load_errors()                 tool modules that failed to load, {module: error}
  value, byref_int, ...         COM helpers for the late-bound API
"""

from ..version import __version__ as SERVER_VERSION
from . import assembly, capture, document, drawing, files, geometry, groups, images, install, knowhow, model, selecting, settings, sketch, sw, units
from .com import (DOC_TYPES, byref_int, doc_path, doc_title, doc_type, null_object, open_documents,
                  plane_feature_name, same_path, value)
from .connection import connection
from .images import bmp_size, bmp_to_png
from .log import log_path
from .registry import TOOLS as registered_tools
from .registry import load_errors, tool
from .results import Err, ToolError, ok
from .runner import Context

API_VERSION = 1

__all__ = [
    "API_VERSION", "SERVER_VERSION",
    "tool", "ok", "ToolError", "Err", "Context", "registered_tools", "load_errors",
    "units", "model", "geometry", "sketch", "selecting", "images", "install", "settings", "connection", "files", "assembly", "capture", "document", "drawing", "groups", "knowhow", "sw",
    "value", "byref_int", "null_object", "doc_title", "doc_type", "doc_path", "same_path",
    "open_documents", "plane_feature_name", "DOC_TYPES", "bmp_size", "bmp_to_png", "log_path",
]
