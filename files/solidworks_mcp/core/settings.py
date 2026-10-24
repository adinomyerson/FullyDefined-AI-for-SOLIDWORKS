"""
Settings: the shipped defaults below, plus the user's settings.json.

The user's settings and the log live in the server's home folder, outside the program folder, so an update
cannot overwrite or lock them: %APPDATA%\\solidworks-mcp, or the folder in SWMCP_HOME; if that cannot be
written, the temp folder. settings.json holds only what the user changes, e.g.
    {"groups": {"drawing": false}, "protected_paths": ["C:\\\\Vault"]}
Tools read settings through ctx.setting(name).
"""

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULTS = {
    "exe_path": "auto",             # SLDWORKS.exe, or "auto" to find it
    "startup_timeout": 120,         # seconds to wait for SolidWorks to start
    "connection_retry_interval": 5,
    "default_unit": "mm",
    "log_level": "INFO",
    "log_file": "solidworks_mcp.log",
    "log_max_bytes": 5000000,       # the log rotates at this size
    "log_backups": 3,               # rotated copies kept (solidworks_mcp.log.1 ...)
    "call_timeout_s": 120,          # a tool call that runs longer is given up on
    "dialog_check_after_s": 5,      # after this long a running call is checked for a SolidWorks dialog
    "dialog_poll_s": 1,             # how often the server looks at a running call
    "groups": {},                   # switch tool groups on or off, e.g. {"assembly": false}
    "protected_paths": [],          # folders no tool ever writes into (a project, a vault)
    "lock_wait_s": 10,              # a call waits this long for another chat's call before it is refused
    "max_result_chars": 40000,      # a longer tool result is cut (about 10k tokens); 0 = no limit
    "know_how": True,               # the know-how: summary sent at start-up and solidworks_guide (false: a run without it)
}

FILE_NAME = "settings.json"

_overrides = {}                     # set by tests only
_home = None
_warned = set()


def _writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def home() -> Path:
    """The folder for the user's settings and the log: SWMCP_HOME, else %APPDATA%\\solidworks-mcp; the temp
    folder if that cannot be written."""
    global _home
    if _home is None:
        wanted = os.environ.get("SWMCP_HOME") or os.path.join(
            os.environ.get("APPDATA") or os.path.expanduser("~"), "solidworks-mcp")
        folder = Path(wanted)
        if not _writable(folder):
            fallback = Path(tempfile.gettempdir()) / "solidworks-mcp"
            logger.warning(f"Cannot write {folder}; using {fallback} for settings and the log")
            fallback.mkdir(parents=True, exist_ok=True)
            folder = fallback
        _home = folder
    return _home


def user_file() -> Path:
    return home() / FILE_NAME


def set_override(**values) -> None:
    """Override settings in memory (tests). set_override() with no values clears them."""
    _overrides.clear()
    _overrides.update(values)


def load() -> dict:
    out = dict(DEFAULTS)
    path = user_file()
    try:
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        if not isinstance(user, dict):
            raise ValueError("settings.json must be a JSON object")
        unknown = sorted(set(user) - set(DEFAULTS))
        if unknown and tuple(unknown) not in _warned:
            _warned.add(tuple(unknown))
            logger.warning(f"{path}: unknown settings {unknown} are ignored (known: {', '.join(DEFAULTS)})")
        out.update({k: v for k, v in user.items() if k in DEFAULTS})
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.error(f"Ignoring {path}: {e}")
    out.update(_overrides)
    return out
