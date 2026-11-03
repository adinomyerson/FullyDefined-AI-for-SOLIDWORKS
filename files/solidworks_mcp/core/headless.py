"""Starting without Windows: the server answers initialize and tools/list on any computer, and refuses every tool call
on one that is not Windows.

The Windows-only modules (pywin32, winreg) are imported at the top of many modules, but none of them uses one while
it loads: only inside functions, during a tool call. So on a computer without them, install() registers stand-ins
under their names before the server imports anything. Every module then loads, every tool registers with its usual
schema, and the tool list is the one Windows shows. A tool call never reaches a stand-in: main.call_tool refuses it
first (refusal()). Using a stand-in anyway raises NotOnWindows with the same plain words.

On Windows install() returns at once and changes nothing: the real modules load as before, at the same moment.
"""

import importlib.util
import sys
import types

from .results import Err, ToolError

MESSAGE = "FullyDefined needs Windows with SOLIDWORKS installed."

# Every Windows-only module the package imports (a test checks the list stays complete).
NAMES = ("pythoncom", "pywintypes", "win32api", "win32con", "win32event", "win32gui", "win32process", "win32ts",
         "winreg", "win32com", "win32com.client")

_standing_in = []           # the names given a stand-in in this process


class NotOnWindows(AttributeError):
    """A stand-in was used: the call needs Windows. An AttributeError, so hasattr() and getattr(m, n, default) on a
    stand-in answer 'absent' instead of failing."""


class _StandIn(types.ModuleType):
    def __getattr__(self, attr):
        raise NotOnWindows(f"{MESSAGE} ({self.__name__}.{attr} is not available here)")


def _missing(name: str) -> bool:
    top = name.split(".")[0]
    if top in _standing_in:
        return True
    try:
        return importlib.util.find_spec(name) is None
    except (ImportError, ValueError):
        return True


def install(force: bool = False) -> list:
    """Stand-ins for the Windows-only modules this computer does not have. On Windows nothing is done. force (the
    offline test, on any computer): a stand-in for each one not loaded yet. Returns the names stood in for."""
    if sys.platform == "win32" and not force:
        return []
    for name in NAMES:
        if name in sys.modules or (not force and not _missing(name)):
            continue                               # loaded already (on Windows, mcp loads a few itself) or present
        module = _StandIn(name)
        module.__path__ = []                       # a package, so "import win32com.client" finds its child
        sys.modules[name] = module
        parent, _, child = name.rpartition(".")
        if parent and parent in sys.modules:
            setattr(sys.modules[parent], child, module)
        _standing_in.append(name)
    return list(_standing_in)


def active() -> bool:
    """True when this process runs on stand-ins: every tool call is refused."""
    return bool(_standing_in)


def refusal() -> ToolError:
    return ToolError(MESSAGE, Err.NO_SOLIDWORKS, {"platform": sys.platform, "missing": list(_standing_in)})
