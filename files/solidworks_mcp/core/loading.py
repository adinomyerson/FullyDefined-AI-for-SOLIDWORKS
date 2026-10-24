"""
Loading the tool modules one by one. A broken module disables only its own tools: the error is kept in
registry.LOAD_ERRORS (shown by get_solidworks_info and doctor, and logged), and any
tool the module registered before it failed is taken out again.
"""

import importlib
import logging
import sys
import traceback

from . import registry

logger = logging.getLogger(__name__)


def load_modules(package: str, names, reload: bool = False) -> dict:
    """Import `package.<name>` for every name. Returns {name: error text} for the ones that failed."""
    errors = {}
    for name in names:
        full = f"{package}.{name}"
        before = set(registry.TOOLS)
        try:
            if reload and full in sys.modules:
                importlib.reload(sys.modules[full])
            else:
                importlib.import_module(full)
            registry.LOAD_ERRORS.pop(name, None)
        except Exception as e:
            for added in set(registry.TOOLS) - before:           # no half-registered module
                registry.TOOLS.pop(added, None)
            text = f"{type(e).__name__}: {e}"
            errors[name] = text
            registry.LOAD_ERRORS[name] = text
            logger.error(f"Tool module {name} failed to load, its tools are off: {text}\n{traceback.format_exc()}")
    return errors
