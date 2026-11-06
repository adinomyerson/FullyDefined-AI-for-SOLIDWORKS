"""
Finding a dialog that SolidWorks is waiting on. A SolidWorks pop-up (a message box, "SOLIDWORKS CAM Error")
blocks every API call until someone answers it; the worker uses this to say so instead of freezing.

Windows only. Reads window titles and texts; never clicks anything.
"""

import logging

import win32con
import win32gui
import win32process

logger = logging.getLogger(__name__)

# Windows that are visible top-level windows of the process but are not dialogs
_IGNORED_CLASSES = ("tooltips_class32", "#32768", "IME", "MSCTFIME UI", "Default IME")

# A progress bar's window class (Windows common controls). A modal window holding one is SolidWorks at work (the STEP
# import's "Open Progress"), not a question: it closes by itself when the work ends.
_PROGRESS_BAR = "msctls_progress32"

# The titles of SolidWorks' own progress windows seen live, for one whose bar is not a Windows progress bar
# (SolidWorks may draw its own). Matched at the start, ignoring case ("Open Progress..." as shown).
_PROGRESS_TITLES = ("open progress",)


def _texts(hwnd) -> str:
    """The text of the static labels in a dialog (the message)."""
    out = []

    def on_child(child, _):
        try:
            if win32gui.GetClassName(child) in ("Static", "Edit", "RichEdit20W", "RICHEDIT50W"):
                text = win32gui.GetWindowText(child).strip()
                if text:
                    out.append(text)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumChildWindows(hwnd, on_child, None)
    except Exception:
        pass
    return " ".join(out)[:300]


def _is_progress(hwnd) -> bool:
    """The window holds a progress bar: SolidWorks showing its work, not asking anything."""
    found = []

    def on_child(child, _):
        try:
            if win32gui.GetClassName(child) == _PROGRESS_BAR:
                found.append(child)
                return False                            # stop: one is enough
        except Exception:
            pass
        return True

    try:
        win32gui.EnumChildWindows(hwnd, on_child, None)
    except Exception:
        pass                                            # EnumChildWindows also reports a callback's early stop
    return bool(found)


def find_dialogs(pid) -> list:
    """Modal dialogs of the process: visible, enabled windows whose owner window is disabled. Only a modal
    dialog blocks SolidWorks. Non-modal windows are not counted: the assembly-open progress box (a #32770
    with "Open Components / Update Assembly"), notification pop-ups such as the low-memory warning; a slow
    call while one of those is up is just slow. Nor is a modal progress window (one holding a progress bar, such
    as the STEP import's "Open Progress", or titled as one of SolidWorks' progress windows): SolidWorks is
    working, not waiting on anyone, so the call keeps its time limit instead of being given up on as blocked.
    Returns [{"title", "text", "hwnd"}]; an empty list when there is none or pid is unknown."""
    if not pid:
        return []
    found = []

    def on_window(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            if win32process.GetWindowThreadProcessId(hwnd)[1] != pid:
                return True
            cls = win32gui.GetClassName(hwnd)
            if cls in _IGNORED_CLASSES:
                return True
            owner = win32gui.GetWindow(hwnd, win32con.GW_OWNER)
            if owner and not win32gui.IsWindowEnabled(owner) and win32gui.IsWindowEnabled(hwnd):
                title = win32gui.GetWindowText(hwnd).strip()
                if title.lower().startswith(_PROGRESS_TITLES) or _is_progress(hwnd):
                    return True                         # SolidWorks at work: not a question
                text = _texts(hwnd)
                if title or text:
                    found.append({"title": title, "text": text, "hwnd": hwnd})
        except Exception as e:
            logger.debug(f"Skipping a window: {e}")
        return True

    try:
        win32gui.EnumWindows(on_window, None)
    except Exception as e:
        logger.debug(f"EnumWindows failed: {e}")
    return found


def describe(dialogs) -> str:
    """One line for an error message."""
    parts = []
    for d in dialogs[:3]:
        parts.append(f'"{d["title"]}"' + (f' says: {d["text"]}' if d["text"] else ""))
    return "; ".join(parts)
