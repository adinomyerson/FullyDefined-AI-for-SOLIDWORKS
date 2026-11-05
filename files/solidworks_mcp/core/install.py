"""Where SolidWorks and its templates are installed. Reads the disk and registry only."""

import glob
import logging
import os
import winreg

logger = logging.getLogger(__name__)

PROGRAMDATA = r"C:\ProgramData\SolidWorks"         # where SolidWorks keeps its default templates per version
TEMPLATE_FILES = {"part": "Part.prtdot", "assembly": "Assembly.asmdot", "drawing": "Drawing.drwdot"}


def _from_registry():
    for hive, key in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\SolidWorks\SOLIDWORKS"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\SolidWorks\SOLIDWORKS"),
                      (winreg.HKEY_CURRENT_USER, r"SOFTWARE\SolidWorks\SOLIDWORKS")):
        try:
            with winreg.OpenKey(hive, key) as root:
                versions, i = [], 0
                while True:
                    try:
                        versions.append(winreg.EnumKey(root, i))
                        i += 1
                    except OSError:
                        break
                for version in sorted(versions, reverse=True):     # newest first
                    try:
                        with winreg.OpenKey(root, version) as k:
                            exe = winreg.QueryValueEx(k, "SolidWorks Exe")[0]
                        if os.path.isfile(exe):
                            return exe
                    except OSError:
                        continue
        except OSError:
            continue
    return None


def _from_common_paths():
    candidates = []
    for drive in ("C", "D", "E"):
        candidates.append(rf"{drive}:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\SLDWORKS.exe")
        candidates += sorted(glob.glob(rf"{drive}:\Program Files\SOLIDWORKS Corp\SOLIDWORKS 20*\SLDWORKS.exe"),
                             reverse=True)
    return next((p for p in candidates if os.path.isfile(p)), None)


def find_exe(configured: str = "auto"):
    """Path of SLDWORKS.exe, or None."""
    if configured and configured != "auto":
        return configured if os.path.isfile(configured) else None
    return _from_registry() or _from_common_paths()


def exe_version(exe: str):
    try:
        import win32api
        info = win32api.GetFileVersionInfo(exe, "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception as e:
        logger.debug(f"No version for {exe}: {e}")
        return None


def find_template(kind: str, exe: str = None):
    """A template file on disk, for when SolidWorks does not name its own default. The English places and name first
    (as always); then the same name in another language's folder; then any template of that kind in those folders or
    SolidWorks' shared template folders (a localized install may name the file in its language; with several
    languages installed, the first by folder name). Files only, no SolidWorks call."""
    name = TEMPLATE_FILES[kind]
    folders = []
    if exe:
        base = os.path.dirname(exe)
        folders += [os.path.join(base, sub) for sub in ("data/templates", "lang/english/templates",
                                                        "lang/english/Tutorial")]
    shared = sorted(glob.glob(os.path.join(PROGRAMDATA, "SOLIDWORKS *", "templates")), reverse=True)
    shared.append(os.path.join(PROGRAMDATA, "SOLIDWORKS", "templates"))
    folders += shared
    found = next((os.path.join(f, name) for f in folders if os.path.isfile(os.path.join(f, name))), None)
    if found:
        return found
    other = []
    if exe:
        base = os.path.dirname(exe)
        other = [f for sub in ("templates", "Tutorial") for f in sorted(glob.glob(os.path.join(base, "lang", "*", sub)))
                 if os.path.basename(os.path.dirname(f)).lower() != "english"]
    found = next((os.path.join(f, name) for f in other if os.path.isfile(os.path.join(f, name))), None)
    if found:
        return found
    extension = os.path.splitext(name)[1].lower()
    for folder in other + shared:                       # a template of that kind named in the install's language
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        hit = next((n for n in names if n.lower().endswith(extension) and os.path.isfile(os.path.join(folder, n))),
                   None)
        if hit:
            return os.path.join(folder, hit)
    return None


def info(configured_exe: str = "auto") -> dict:
    exe = find_exe(configured_exe)
    return {
        "found": exe is not None,
        "exe_path": exe,
        "version": exe_version(exe) if exe else None,
        "install_dir": os.path.dirname(exe) if exe else None,
        "templates": {kind: (find_template(kind, exe) if exe else None) for kind in TEMPLATE_FILES},
    }
