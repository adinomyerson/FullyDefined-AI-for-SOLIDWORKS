"""
doctor: checks the setup and writes a report a tester attaches to a bug report.

    solidworks-mcp doctor              (or: python -m solidworks_mcp doctor)
    solidworks-mcp doctor --check      also a short real build in SolidWorks (2-3 minutes): PASS or FAIL

It checks Windows, 64-bit Python, pywin32 and mcp, the server (version, tool modules that failed to load,
groups), the settings, each AI app's entry for this server (does what it starts exist) and SolidWorks. SolidWorks
is only looked at when it is
already running: doctor never starts it. Then it reads SolidWorks' version and language and the plane names of a
new part from the default template (with the template's units and standard), which it closes unsaved. The report goes to the server's home folder
(doctor-<date>.txt) with personal paths, the user name and the computer name replaced; its path is printed.
It runs outside the MCP server, so it also works when the server does not start in the AI app.
"""

import datetime
import glob
import importlib
import importlib.metadata
import json
import math
import os
import platform
import struct
import sys
import traceback

LOG_LINES = 50
PLANES = ("Front Plane", "Top Plane", "Right Plane")
PATH_VARS = ("LOCALAPPDATA", "APPDATA", "TEMP", "TMP", "USERPROFILE")    # longest paths first, see redact()


def redact(text: str, env=None) -> str:
    """Personal paths become their %VARIABLE%, the user and computer names <user> and <computer>."""
    env = os.environ if env is None else env
    text = str(text)
    for var in sorted(PATH_VARS, key=lambda v: -len(env.get(v, ""))):
        value = env.get(var, "")
        if len(value) > 3:
            for form in {value, value.replace("\\", "\\\\"), value.replace("\\", "/")}:
                text = _replace_nocase(text, form, f"%{var}%")
    names = (("USERNAME", "<user>"), ("COMPUTERNAME", "<computer>"))
    for var, mark in sorted(names, key=lambda n: -len(env.get(n[0], ""))):     # "bob" must not eat "BOBPC"
        value = env.get(var, "")
        if len(value) > 2:
            text = _replace_nocase(text, value, mark)
    return text


def _replace_nocase(text, old, new):
    lower, low_old, out, i = text.lower(), old.lower(), [], 0
    while True:
        j = lower.find(low_old, i)
        if j < 0:
            return "".join(out) + text[i:]
        out += [text[i:j], new]
        i = j + len(old)


class Report:
    def __init__(self):
        self.lines = []
        self.problems = []          # (what, how to fix)

    def section(self, title):
        self.lines += ["", f"== {title}"]

    def row(self, label, value, problem=None, fix=""):
        mark = "PROBLEM " if problem else ""
        self.lines.append(f"{mark}{label}: {value}")
        if problem:
            self.problems.append((f"{label}: {problem}", fix))

    def text(self, now):
        from .version import __version__
        head = [f"SolidWorks MCP doctor report, server {__version__}, {now:%Y-%m-%d %H:%M:%S}", ""]
        if self.problems:
            head.append(f"{len(self.problems)} problem(s):")
            for what, fix in self.problems:
                head.append(f"  - {what}" + (f"\n    Fix: {fix}" if fix else ""))
        else:
            head.append("No problems found.")
        return redact("\n".join(head + self.lines)) + "\n"


# ---------------------------------------------------------------- checks

def check_system(r: Report):
    r.section("System")
    windows = platform.system() == "Windows"
    r.row("Windows", platform.platform(), None if windows else "not Windows",
          "The server drives SolidWorks through COM and runs only on Windows.")
    bits = struct.calcsize("P") * 8
    r.row("Python", f"{platform.python_version()} {bits}-bit, {sys.executable}",
          None if bits == 64 else "32-bit Python", "Install 64-bit Python 3.10 or newer (the installer does this).")
    if sys.version_info < (3, 10):
        r.row("Python version", platform.python_version(), "older than 3.10", "Install Python 3.10 or newer.")


def check_packages(r: Report):
    r.section("Packages")
    for dist, module, fix in (("pywin32", "win32com.client", "Run the installer again, or: pip install pywin32"),
                              ("mcp", "mcp.server", "Run the installer again, or: pip install \"mcp<2\"")):
        try:
            version = importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            version = "not installed"
        try:
            importlib.import_module(module)
            r.row(dist, version)
        except Exception as e:
            r.row(dist, f"{version}; import {module} failed: {e}", "cannot be imported", fix)


def check_server(r: Report):
    """Loads the server's tools (as the AI app would) and reports what is on."""
    r.section("Server")
    from .version import __version__
    r.row("Version", __version__)
    r.row("Program folder", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from . import main
        from .core import groups
        from .core.registry import load_errors
    except Exception as e:
        r.row("Start", f"the server cannot load: {e}", "the server does not start",
              "Send this report. Running the installer again often fixes a broken install.")
        r.lines.append(traceback.format_exc().rstrip())
        return
    errors = load_errors()
    for module, error in sorted(errors.items()):
        r.row(f"Tool module {module}", error.splitlines()[0][:300], "failed to load, its tools are off",
              "Send this report.")
    names = [t.name for t in main.tool_list()]
    on = [g for g in groups.GROUPS if groups.is_on(g)]
    r.row("Tools", f"{len(names)} registered, {sum(groups.is_on(main.group_of(n)) for n in names)} listed")
    r.row("Groups on", ", ".join(on))
    for group, text in groups.reports():           # groups with a switch of their own
        r.row(f"Group {group}", text)


def packaged_homes(env=None) -> list:
    r"""The server's home folder as packaged apps see it. Windows redirects what a packaged (MSIX) app such as
    the Microsoft Store Claude Desktop, and the servers it starts, writes under %APPDATA% to
    %LOCALAPPDATA%\Packages\<app>\LocalCache\Roaming: their logs are there, and a settings.json there
    overrides the real one for them."""
    env = os.environ if env is None else env
    local = env.get("LOCALAPPDATA", "")
    if not local:
        return []
    return sorted(glob.glob(os.path.join(local, "Packages", "*", "LocalCache", "Roaming", "solidworks-mcp")))


def check_settings(r: Report, packaged=None):
    r.section("Settings")
    from .core import settings
    r.row("Home folder", settings.home())
    path = settings.user_file()
    real = _read_json(path)
    for home in packaged_homes() if packaged is None else packaged:
        copy = os.path.join(home, settings.FILE_NAME)
        if os.path.exists(copy) and os.path.normcase(copy) != os.path.normcase(str(path)):
            theirs = _read_json(copy)
            app = home.split(os.sep + "Packages" + os.sep)[-1].split(os.sep)[0]
            r.row(f"settings.json of {app}", f"{copy}: {theirs}",
                  "differs from your settings.json: servers that app starts use this copy" if theirs != real else None,
                  f"Make your changes in {copy} too (or delete it).")
    if not path.exists():
        r.row("settings.json", "none (all defaults)")
        return
    try:
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        r.row("settings.json", json.dumps(user, ensure_ascii=False))
        unknown = sorted(set(user) - set(settings.DEFAULTS)) if isinstance(user, dict) else []
        if unknown:
            r.row("Unknown settings", ", ".join(unknown), "ignored by the server",
                  f"Check the spelling in {path}; known: {', '.join(settings.DEFAULTS)}")
    except Exception as e:
        r.row("settings.json", f"cannot be read: {e}", "the file is ignored", f"Fix or delete {path}.")


def entry_problem(item: dict, which=None, find_module=None):
    """What is wrong with one app's server entry, or None: a command that is not there, a script argument that is
    not there, or a `-m` module its Python cannot import (with the entry's PYTHONPATH and folder)."""
    import shutil
    import subprocess
    which = shutil.which if which is None else which
    command = str(item.get("command", ""))
    args = [str(a) for a in item.get("args") or []]
    cwd = item.get("cwd") or None
    env = item.get("env") if isinstance(item.get("env"), dict) else {}
    if not command:
        return "no command"
    if os.path.isabs(command) and not os.path.isfile(command):
        return f"the command {command} does not exist"
    if not os.path.isabs(command) and not which(command):
        return f"the command {command} is not found on PATH"
    for a in args:
        if a.lower().endswith(".py") and not os.path.isfile(a if os.path.isabs(a) else os.path.join(cwd or "", a)):
            return f"the script {a} does not exist"
    if "-m" in args[:-1] and os.path.basename(command).lower().startswith(("python", "py.")):
        module = args[args.index("-m") + 1]
        if find_module is None:
            run_env = dict(os.environ, **{k: str(v) for k, v in env.items()})
            probe = f"import importlib.util, sys; sys.exit(0 if importlib.util.find_spec({module!r}) else 1)"
            try:
                found = subprocess.run([command, "-c", probe], env=run_env, cwd=cwd, stdin=subprocess.DEVNULL,
                                       capture_output=True, timeout=30).returncode == 0
            except (OSError, subprocess.SubprocessError):
                found = False
        else:
            found = find_module(command, module, env.get("PYTHONPATH", ""), cwd)
        if not found:
            where = f" (PYTHONPATH {env['PYTHONPATH']})" if env.get("PYTHONPATH") else ""
            return f"its Python cannot import the module {module}{where}"
    return None


def check_app_configs(r: Report, env=None, problem=entry_problem):
    """Every AI app config's entry for this server: does what it starts exist? (A config left pointing at a
    deleted script or a moved folder makes the server fail in that app with no message the user sees.)"""
    from . import apps
    r.section("AI apps")
    for app, path in apps.app_configs(env):
        if not os.path.isfile(path):
            continue
        data = _read_json(path)
        if not isinstance(data, dict):
            r.row(f"{app} config", f"{path} cannot be read as JSON")
            continue
        entries = apps.server_entries(data)
        if not entries:
            r.row(app, f"no SolidWorks server in {path}")
        for where, name, item in entries:
            started = " ".join([str(item.get("command", ""))] + [str(a) for a in item.get("args") or []])
            wrong = problem(item)
            r.row(f"{app} '{name}'", f"{where}{started}", wrong,
                  f"Run the installer again (it rewrites the entry), or fix '{name}' in {path} by hand.")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def registered_solidworks() -> str:
    """The SolidWorks COM class registered on this machine with its program, e.g.
    'SldWorks 2026 Application (SldWorks.Application.34), C:\\...\\SLDWORKS.exe', or ''. SolidWorks 2026 writes
    no CurVer key, so the versioned class is the one with the same CLSID."""
    import winreg

    def read(path):
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, path) as key:
            return str(winreg.QueryValueEx(key, "")[0])

    try:
        clsid = read(r"SldWorks.Application\CLSID")
    except OSError:
        return ""
    found = "SldWorks.Application"
    for major in range(45, 19, -1):
        try:
            if read(rf"SldWorks.Application.{major}\CLSID").lower() == clsid.lower():
                found = f"{read(f'SldWorks.Application.{major}')} (SldWorks.Application.{major})"
                break
        except OSError:
            continue
    try:
        found += ", " + read(rf"CLSID\{clsid}\LocalServer32").strip('"')
    except OSError:
        pass
    return found


def running_solidworks():
    """The running SolidWorks, or None. Never starts it (GetActiveObject, not Dispatch)."""
    try:
        import win32com.client
        return win32com.client.GetActiveObject("SldWorks.Application")
    except Exception:
        return None


def new_part_facts(app) -> dict:
    """A new part from SolidWorks' default part template: its reference planes and what the template sets
    (units, standard). The part is closed unsaved."""
    from .core.com import value
    from .core.document import template, template_info
    from .core.sw.documents import sw_close, sw_new_document
    path = template(app, "part")
    if not path:
        raise RuntimeError("SolidWorks has no default part template (Options > Default Templates)")
    before = {value(d, "GetTitle") for d in (value(app, "GetDocuments") or [])}
    doc = sw_new_document(app, path)
    if doc is None:
        raise RuntimeError(f"SolidWorks made no part from {path}")
    try:
        names, feat = [], value(doc, "FirstFeature")
        while feat is not None:
            if value(feat, "GetTypeName2") == "RefPlane":
                names.append(value(feat, "Name"))
            feat = value(feat, "GetNextFeature")
        return {"planes": names, "template": template_info(doc, path)}
    finally:            # close the part doctor made, and only that one, even if reading it failed
        for title in {value(d, "GetTitle") for d in (value(app, "GetDocuments") or [])} - before:
            sw_close(app, title)


def check_solidworks(r: Report, attach=running_solidworks, new_part=new_part_facts, lock_factory=None):
    r.section("SolidWorks")
    from .core import selfcheck
    from .core.com import value
    registered = registered_solidworks()
    r.row("Registered", registered or "not found", None if registered else "SolidWorks is not registered",
          "Install SolidWorks 2026, or repair the installation.")
    app = attach()
    if app is None:
        r.row("Running", "no (start SolidWorks and run doctor again to check its version, language and planes)")
        return
    r.row("Running", "yes")
    try:
        r.row("Revision", value(app, "RevisionNumber"))
        r.row("Language", value(app, "GetCurrentLanguage"))
    except Exception as e:
        r.row("Version", f"cannot be read: {e}", "SolidWorks does not answer", "Close any open SolidWorks dialog.")
        return
    for warning in selfcheck.solidworks(app):
        r.row("Tested combination", warning, "untested", "The server is tested on SolidWorks 2026 in English.")
    if lock_factory is None:
        from .core.lock import CallLock as lock_factory
    lock = lock_factory()
    try:
        lock.acquire("doctor", 0)
    except Exception as e:
        r.row("Planes", f"not checked: {e}")
        return
    try:
        facts = new_part(app)
        info = facts.get("template") or {}
        r.row("Part template", f"{info.get('file')}: {info.get('unit_system')}, {info.get('length_unit')}, "
                               f"{info.get('standard')} (kept as is: the tools convert units themselves)")
        names = facts["planes"]
        missing = [p for p in PLANES if p not in names]
        r.row("Planes", ", ".join(names) or "none",
              f"missing {', '.join(missing)}" if missing else None,
              "The tools look for the English plane names. Use an English part template, or SolidWorks in English.")
    except Exception as e:
        r.row("Planes", f"not checked: {e}", "a new part could not be made", "Close any open SolidWorks dialog.")
    finally:
        lock.release()


def check_log(r: Report, folder=None, own=None, packaged=None):
    """The last lines of the most recent server log (not doctor's own), also from packaged apps' folders."""
    from .core import log, settings
    folders = [str(folder or settings.home())] + (packaged_homes() if packaged is None else list(packaged))
    own = os.path.normcase(str(own or log.log_path()))
    logs = [p for f in folders for p in glob.glob(os.path.join(f, "solidworks_mcp*.log*"))
            if os.path.normcase(p) != own]
    r.section("Recent log")
    if not logs:
        r.row("Log", "none yet (the server has not run under this user)")
        return
    newest = max(logs, key=os.path.getmtime)
    r.row("Log", newest)
    with open(newest, encoding="utf-8", errors="replace") as f:
        r.lines += [line.rstrip("\n") for line in f.readlines()[-LOG_LINES:]]


# ---------------------------------------------------------------- the command

def build(**solidworks_options) -> Report:
    r = Report()
    for check in (check_system, check_packages, check_server, check_settings, check_app_configs):
        try:
            check(r)
        except Exception as e:
            r.row(check.__name__, f"the check itself failed: {e}", "doctor error", "Send this report.")
    try:
        check_solidworks(r, **solidworks_options)
    except Exception as e:
        r.row("SolidWorks check", f"failed: {e}", "doctor error", "Send this report.")
    try:
        check_log(r)
    except Exception as e:
        r.row("Log", f"cannot be read: {e}")
    return r


# ---------------------------------------------------------------- the install check (doctor --check)

CHECK_BLOCK_MM = (40.0, 20.0, 10.0)         # a block on the Front plane ...
CHECK_HOLE_MM = 6.0                         # ... with a simple Hole Wizard hole through it
CHECK_TOLERANCE_MM3 = 0.01


def check_volume_mm3() -> float:
    w, hgt, d = CHECK_BLOCK_MM
    return w * hgt * d - math.pi * (CHECK_HOLE_MM / 2) ** 2 * d


def _run_tool(name: str, args: dict):
    """A tool through the server's own runner, as the AI app's calls go (lengths in mm whatever the settings)."""
    from . import main  # noqa: F401  (registers the tools)
    from .core import runner
    return runner.run(name, dict(args, unit="mm"))


def install_check(r: Report, call=_run_tool, attach=running_solidworks, lock_factory=None, close=None) -> bool:
    """One short real build through the tools: a new part, a sketched block extruded, a Hole Wizard hole through
    it, its volume measured against the calculation; the part is closed unsaved. PASS or FAIL in the report."""
    r.section("Install check")
    app = attach()
    if app is None:
        r.row("Install check", "FAIL: SolidWorks is not running", "the check cannot run",
              "Start SolidWorks, wait until it has opened, and run: solidworks-mcp doctor --check")
        return False
    if lock_factory is None:
        from .core.lock import CallLock as lock_factory
    lock = lock_factory()
    try:
        lock.acquire("doctor --check", 0)
    except Exception as e:
        r.row("Install check", f"FAIL: not run: {e}", "SolidWorks is busy", "Wait until the other chat is done.")
        return False
    if close is None:
        from .core.sw.documents import sw_close

        def close(title):
            sw_close(app, title)
    w, hgt, d = CHECK_BLOCK_MM
    title, failed = None, None
    try:
        for name, args in (("connect_solidworks", {}), ("create_new_part", {}),
                           ("create_sketch", {"plane": "Front"}),
                           ("draw_rectangle", {"x1": 0, "y1": 0, "x2": w, "y2": hgt}),
                           ("extrude_sketch", {"depth": d}), ("measure_part", {})):
            result = call(name, args)
            r.lines.append(f"{name}: {'ok' if result.success else 'ERROR'} {result.message[:200]}")
            if not result.success:
                failed = f"{name} failed: {result.message[:300]}"
                break
            if name == "create_new_part":
                title = result.data.get("name")
        if failed is None:
            box = result.data.get("bounding_box", {})
            low, high = box.get("min", [0, 0, 0]), box.get("max", [0, 0, 0])
            top = high[2] if abs(high[2]) >= abs(low[2]) else low[2]           # the face the extrude ended on
            hole = call("hole_wizard", {"hole_type": "simple", "size": f"{CHECK_HOLE_MM:g}",
                                        "x": w / 2, "y": hgt / 2, "z": top})
            r.lines.append(f"hole_wizard: {'ok' if hole.success else 'ERROR'} {hole.message[:200]}")
            measured = call("measure_part", {}) if hole.success else hole
            if not hole.success:
                failed = f"hole_wizard failed: {hole.message[:300]}"
            elif not measured.success:
                failed = f"measure_part failed: {measured.message[:300]}"
            else:
                got, want = float(measured.data.get("volume", -1)), check_volume_mm3()
                r.lines.append(f"volume: {got:.3f} mm3, calculated {want:.3f} mm3")
                if abs(got - want) > CHECK_TOLERANCE_MM3:
                    failed = f"the part measures {got:.3f} mm3, not the calculated {want:.3f} mm3"
    except Exception as e:
        failed = f"the check itself failed: {type(e).__name__}: {e}"
    finally:
        if title:
            try:
                close(title)
            except Exception as e:
                r.lines.append(f"could not close {title}: {e}")
        lock.release()
    if failed:
        r.row("Install check", f"FAIL: {failed}", f"the test build did not work: {failed}", "Send this report.")
        return False
    r.row("Install check", f"PASS: sketch, extrude, hole and measurement work ({check_volume_mm3():.2f} mm3 as "
                           f"calculated); the test part was closed unsaved")
    return True


def write(r: Report, folder=None, now=None) -> str:
    now = now or datetime.datetime.now()
    if folder is None:
        from .core import settings
        folder = settings.home()
    path = os.path.join(str(folder), f"doctor-{now:%Y%m%d-%H%M%S}.txt")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(r.text(now).replace("\n", "\r\n"))
    return path


def run(check: bool = False) -> int:
    """The doctor command: print the problems and where the report is. Exit code 1 when there are problems.
    With check (doctor --check), also the install check: a short real build in SolidWorks, PASS or FAIL."""
    r = build()
    passed = install_check(r) if check else None
    path = write(r)
    if r.problems:
        print(f"doctor found {len(r.problems)} problem(s):")
        for what, fix in r.problems:
            print(redact(f"  - {what}" + (f"\n    Fix: {fix}" if fix else "")))
    else:
        print("doctor found no problems.")
    if passed is not None:
        print("")
        print("INSTALL CHECK: PASS" if passed else "INSTALL CHECK: FAIL")
        line = next((x for x in r.lines if x.startswith(("Install check:", "PROBLEM Install check:"))), "")
        print(redact(line.replace("PROBLEM ", "")))
    print(f"Report: {path}")
    print("Attach this file when you report a bug.")
    return 1 if r.problems else 0
