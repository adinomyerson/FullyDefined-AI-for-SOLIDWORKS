"""The SolidWorks calls that create, select, delete, save, open or mate, by area. Tools reach them through
core.api.sw (sw.drawing.sw_finish ...)."""

from . import appearance, assembly, common, documents, drawing, editing, equations, export, features, holes, inspection, materials, mates, patterns, selection, shapes, sheet_metal, sketch_define, sketch_draw, sketches, surfaces, weldments

__all__ = ["appearance", "assembly", "common", "documents", "drawing", "editing", "equations", "export", "features", "holes", "inspection", "materials", "mates", "patterns", "selection", "shapes", "sheet_metal", "sketch_define", "sketch_draw", "sketches", "surfaces", "weldments"]
