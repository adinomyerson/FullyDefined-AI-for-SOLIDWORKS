"""One module per area. Importing this package registers every tool. Each module loads on its own: a broken
one turns off only its own tools and is reported (core/loading.py)."""

from pathlib import Path

from ..core.loading import load_modules

MODULES = ("connection", "documents", "units", "materials", "views", "export",
           "sketches", "sketch_draw", "sketch_define", "selection", "features", "shapes", "patterns",
           "holes", "surfaces", "sheet_metal", "weldments", "editing", "equations", "measuring", "assembly", "mates",
           "appearance", "inspection", "drawings", "annotations", "guide")
# development modules (dev_*.py): loaded when present; a release does not contain them
DEV_MODULES = tuple(sorted(p.stem for p in Path(__file__).parent.glob("dev_*.py")))

load_modules(__name__, MODULES + DEV_MODULES)
