"""
Start-up self-check: what the log should say first when something is odd (doctor reports the same).

  - at start-up: the server version, and every tool module that failed to load;
  - on the first connection to SolidWorks: its version and language, with a warning when it is not
    SolidWorks 2026 (revision 34) or not English, the only combination the tools are tested on.
"""

import logging
from typing import List

from ..version import __version__
from .com import value
from .registry import load_errors

logger = logging.getLogger(__name__)

TESTED_MAJOR = 34                 # RevisionNumber 34.x = SolidWorks 2026
TESTED_LANGUAGE = "english"

_checked_pid = None               # the SolidWorks process already checked


def startup() -> List[str]:
    """Log the server version and the tool modules that failed to load; returns the warnings."""
    logger.info(f"Starting the SolidWorks MCP server {__version__}")
    warnings = [f"Tool module {module} failed to load, its tools are off: {error.splitlines()[0][:200]}"
                for module, error in sorted(load_errors().items())]
    for w in warnings:
        logger.warning(w)
    return warnings


def solidworks(app, pid=None) -> List[str]:
    """Log SolidWorks' version and language once per SolidWorks process; returns the warnings."""
    global _checked_pid
    if pid is not None and pid == _checked_pid:
        return []
    _checked_pid = pid
    warnings = []
    try:
        revision = str(value(app, "RevisionNumber"))
    except Exception as e:
        revision = f"unknown ({e})"
    try:
        language = str(value(app, "GetCurrentLanguage"))
    except Exception as e:
        language = f"unknown ({e})"
    logger.info(f"SolidWorks revision {revision}, language {language}, process {pid}")
    try:
        major = int(revision.split(".")[0])
    except ValueError:
        major = None
    if major != TESTED_MAJOR:
        warnings.append(f"SolidWorks revision {revision} is not SolidWorks 2026 (revision {TESTED_MAJOR}.x), the "
                        f"version this server is tested on; some tools may behave differently.")
    if language.lower() != TESTED_LANGUAGE:
        warnings.append(f"SolidWorks runs in {language}, not English, the language this server is tested on; "
                        f"names such as 'Front Plane' or 'Sketch1' may differ.")
    for w in warnings:
        logger.warning(w)
    return warnings
