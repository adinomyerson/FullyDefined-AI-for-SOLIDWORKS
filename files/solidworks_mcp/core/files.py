"""
File safety: no tool writes under a protected folder.

The setting protected_paths lists folders (a project, a vault) the server must never write into. The runner
checks every input a tool declares with @tool(writes=...); tools that write a path they work out themselves
(saving in place, referenced files) call check_writable.
"""

import os
from typing import Optional

from . import settings
from .results import Err, ToolError


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(os.path.expandvars(os.path.expanduser(str(path))))))


def protected_root(path: str) -> Optional[str]:
    """The protected folder that contains path, or None."""
    target = _norm(path)
    for root in settings.load().get("protected_paths") or []:
        if not root:
            continue
        base = _norm(root)
        try:
            if os.path.commonpath([target, base]) == base:
                return str(root)
        except ValueError:                  # another drive
            continue
    return None


def check_writable(path: str, what: str = "write") -> None:
    """Refuse a write under a protected folder."""
    root = protected_root(path)
    if root is not None:
        raise ToolError(f"Nothing was written: {path} is inside the protected folder {root} (setting "
                        f"protected_paths), and this server never writes there. Choose another location.",
                        Err.SAVE, {"path": str(path), "protected_by": root, "action": what})
