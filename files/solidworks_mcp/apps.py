"""
Registering the installed server with AI apps. setup.ps1 calls this with the
installed Python, so the config editing is tested offline instead of living in PowerShell.

    python -m solidworks_mcp.apps register   --install-dir DIR [--force] [--app claude-desktop|gemini|antigravity]
    python -m solidworks_mcp.apps unregister --install-dir DIR [--force] [--app claude-desktop|gemini|antigravity]
    python -m solidworks_mcp.apps snippet    --install-dir DIR

Only the server entry named "solidworks" is touched, and only when it is ours: its command lies under the install
folder. An entry that points elsewhere (a developer's own setup) is left alone unless --force. Every other server
and setting in the file is kept, and the file is written whole (a temporary file, then a rename).
Claude Desktop from the Microsoft Store reads its config from its own redirected folder
(%LOCALAPPDATA%\\Packages\\Claude_*\\LocalCache\\Roaming\\Claude) when a copy is there, and newer versions from
%LOCALAPPDATA%\\Claude-Data\\claude_*; every copy found is updated.
Gemini CLI reads <home>\\.gemini\\settings.json, home being GEMINI_CLI_HOME or the user's profile, with the same
mcpServers block. That file may hold comments, which a rewrite would lose: such a file is left alone, with the
block to add by hand. Antigravity, the app and its CLI (agy, which replaced Gemini CLI for Google AI Pro), read
<profile>\\.gemini\\config\\mcp_config.json, the same block; the same rules apply.
"""

import argparse
import glob
import json
import os
import shutil
import sys
from typing import Dict, List, Optional

NAME = "solidworks"
CONFIG = "claude_desktop_config.json"


def server_command(install_dir: str) -> str:
    """The installed server: the solidworks-mcp command in the install's virtual environment."""
    return os.path.join(install_dir, "app", ".venv", "Scripts", "solidworks-mcp.exe")


def entry(install_dir: str) -> Dict:
    return {"command": server_command(install_dir), "args": []}


def is_ours(existing: Optional[Dict], install_dir: str) -> bool:
    command = str((existing or {}).get("command", ""))
    root = os.path.normcase(os.path.abspath(install_dir)).rstrip("\\/") + os.sep
    return os.path.normcase(os.path.abspath(command)).startswith(root) if command else False


def desktop_configs(env=None) -> List[str]:
    """Claude Desktop config files to update: every existing one (the usual place and the Store app's
    redirected copy). With none yet: the Store app's folder when it is installed, else the usual place."""
    env = os.environ if env is None else env
    usual = os.path.join(env.get("APPDATA", ""), "Claude", CONFIG)
    store = sorted(glob.glob(os.path.join(env.get("LOCALAPPDATA", ""), "Packages", "Claude_*", "LocalCache",
                                          "Roaming", "Claude")))
    # newer Store versions keep their data, and the config they read, in %LOCALAPPDATA%\Claude-Data\claude_<id>
    data = sorted(glob.glob(os.path.join(env.get("LOCALAPPDATA", ""), "Claude-Data", "claude_*")))
    found = [p for p in [usual] + [os.path.join(s, CONFIG) for s in store + data] if os.path.isfile(p)]
    if found:
        return found
    if data:
        return [os.path.join(data[0], CONFIG)]
    if store:
        return [os.path.join(store[0], CONFIG)]
    packages = sorted(glob.glob(os.path.join(env.get("LOCALAPPDATA", ""), "Packages", "Claude_*")))
    if packages:
        return [os.path.join(packages[0], "LocalCache", "Roaming", "Claude", CONFIG)]
    return [usual]


def gemini_settings(env=None) -> str:
    """Gemini CLI's user settings file (its homedir(): GEMINI_CLI_HOME, else the user's profile)."""
    env = os.environ if env is None else env
    home = env.get("GEMINI_CLI_HOME") or env.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(home, ".gemini", "settings.json")


def antigravity_config(env=None) -> str:
    """The Antigravity app's and Antigravity CLI's (agy) shared user MCP file: <profile>\\.gemini\\config\\
    mcp_config.json (Google's Antigravity docs and codelab: the app, IDE and CLI share it)."""
    env = os.environ if env is None else env
    home = env.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(home, ".gemini", "config", "mcp_config.json")


def claude_code_config(env=None) -> str:
    """Claude Code's user file (CLAUDE_CONFIG_DIR, else the user's profile): user-scope servers in its mcpServers,
    each folder's local-scope ones under projects."""
    env = os.environ if env is None else env
    folder = env.get("CLAUDE_CONFIG_DIR") or env.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(folder, ".claude.json")


def app_configs(env=None) -> List[tuple]:
    """(app, config file) for every AI app config this program registers with, whether or not it exists."""
    rows = [("Claude Desktop", p) for p in desktop_configs(env)]
    rows += [("Claude Code", claude_code_config(env)), ("Gemini CLI", gemini_settings(env)),
             ("Antigravity", antigravity_config(env))]
    return rows


def server_entries(data: Dict) -> List[tuple]:
    """(where, name, entry) for each server in a config that is this program: named solidworks, or started from
    it (solidworks_mcp / solidworks-mcp in its command or arguments). Claude Code's per-folder servers count too."""
    blocks = [("", data.get("mcpServers"))]
    for folder, project in sorted((data.get("projects") or {}).items()):
        if isinstance(project, dict):
            blocks.append((f"folder {folder}: ", project.get("mcpServers")))
    out = []
    for where, servers in blocks:
        for name, item in sorted((servers or {}).items()) if isinstance(servers, dict) else []:
            if not isinstance(item, dict):
                continue
            words = " ".join([str(item.get("command", ""))] + [str(a) for a in item.get("args") or []]).lower()
            if name == NAME or "solidworks_mcp" in words or "solidworks-mcp" in words:
                out.append((where, name, item))
    return out


def _load(path: str) -> Dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not hold a JSON object")
    return data


def _save(path: str, data: Dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".swmcp-new"
    with open(temp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(temp, path)


def register(path: str, install_dir: str, force: bool = False) -> str:
    """Add or update our entry in one config file; the outcome in words."""
    try:
        data = _load(path)
    except ValueError as e:
        return (f"skipped {path}: it is not plain JSON ({e}; comments count), so it was left as it is. Add this "
                f"inside its \"mcpServers\" by hand: \"{NAME}\": {json.dumps(entry(install_dir))}")
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        return f"skipped {path}: mcpServers is not an object"
    existing = servers.get(NAME)
    if existing is not None and not is_ours(existing, install_dir) and not force:
        return (f"kept {path}: its '{NAME}' server runs {existing.get('command')!r}, not this install "
                f"(run the installer with -Force to replace it)")
    wanted = entry(install_dir)
    if existing == wanted:
        return f"unchanged {path}"
    servers[NAME] = wanted
    _save(path, data)
    return f"{'updated' if existing is not None else 'added'} {path}"


def unregister(path: str, install_dir: str, force: bool = False) -> str:
    """Remove our entry from one config file; the outcome in words."""
    if not os.path.exists(path):
        return f"nothing in {path} (no file)"
    try:
        data = _load(path)
    except ValueError as e:
        return f"skipped {path}: it is not valid JSON ({e})"
    servers = data.get("mcpServers")
    existing = servers.get(NAME) if isinstance(servers, dict) else None
    if existing is None:
        return f"nothing in {path}"
    if not is_ours(existing, install_dir) and not force:
        return f"kept {path}: its '{NAME}' server is not this install (-Force removes it anyway)"
    del servers[NAME]
    _save(path, data)
    return f"removed from {path}"


def snippet(install_dir: str) -> str:
    """The config block for other apps that run local MCP servers."""
    return json.dumps({"mcpServers": {NAME: entry(install_dir)}}, indent=2)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m solidworks_mcp.apps")
    parser.add_argument("action", choices=("register", "unregister", "snippet"))
    parser.add_argument("--install-dir", required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--app", choices=("claude-desktop", "gemini", "antigravity"), default="claude-desktop")
    args = parser.parse_args(argv)
    if args.action == "snippet":
        print(snippet(args.install_dir))
        return 0
    act = register if args.action == "register" else unregister
    if args.app == "gemini":
        print(f"Gemini CLI: {gemini(act, args.install_dir, args.force)}")
        return 0
    if args.app == "antigravity":
        print(f"Antigravity (app and CLI): {antigravity(act, args.install_dir, args.force)}")
        return 0
    for path in desktop_configs():
        print(f"Claude Desktop: {act(path, args.install_dir, args.force)}")
    return 0


def _on_path(name: str, env) -> Optional[str]:
    path = env.get("PATH") or env.get("Path")
    return shutil.which(name, path=path) if path else None


def gemini_installed(env=None) -> Optional[str]:
    """Where Gemini CLI is, or None: its .gemini folder (it has run) or the gemini command (never run yet)."""
    env = os.environ if env is None else env
    folder = os.path.dirname(gemini_settings(env))
    return folder if os.path.isdir(folder) else _on_path("gemini", env)


def antigravity_installed(env=None) -> Optional[str]:
    """Where Antigravity is, or None: the app (its user installer puts it in %LOCALAPPDATA%\\Programs\\
    Antigravity), the agy command, or the user's .gemini folder (one of them has run)."""
    env = os.environ if env is None else env
    app = os.path.join(env.get("LOCALAPPDATA", ""), "Programs", "Antigravity", "Antigravity.exe")
    agy = os.path.join(env.get("LOCALAPPDATA", ""), "agy", "bin", "agy.exe")
    folder = os.path.dirname(os.path.dirname(antigravity_config(env)))
    for found in (app, agy):
        if env.get("LOCALAPPDATA") and os.path.isfile(found):
            return found
    return _on_path("agy", env) or (folder if os.path.isdir(folder) else None)


# Where Antigravity kept its MCP file before the app, IDE and CLI shared .gemini\config\mcp_config.json (the CLI
# moved in its 1.0.3-1.0.4 releases). Such a file without the shared one means an Antigravity too old to read ours.
ANTIGRAVITY_LEGACY = (("antigravity", "mcp_config.json"), ("antigravity-cli", "mcp_config.json"))


def gemini(act, install_dir: str, force: bool = False, env=None) -> str:
    """Register with (or unregister from) Gemini CLI. Installed but never run: its settings.json is made now (it
    reads it on its first start). Not installed: nothing is written."""
    path = gemini_settings(env)
    if act is register and not gemini_installed(env):
        return "not installed: skipped"
    return act(path, install_dir, force)


def antigravity(act, install_dir: str, force: bool = False, env=None) -> str:
    """Register with (or unregister from) Antigravity, app and CLI. Installed but never opened: the shared file
    is made now (the app reads it on its first start). Not installed: nothing is written. A legacy MCP file
    without the shared one: a warning to update Antigravity."""
    path = antigravity_config(env)
    gemini_dir = os.path.dirname(os.path.dirname(path))
    if act is register and not antigravity_installed(env):
        return "not installed: skipped"
    legacy = [os.path.join(gemini_dir, *p) for p in ANTIGRAVITY_LEGACY]
    old = act is register and not os.path.exists(path) and [p for p in legacy if os.path.isfile(p)]
    outcome = act(path, install_dir, force)
    if old:
        outcome += (f". WARNING: this Antigravity may be too old to read it (it has {old[0]}, not the shared file): "
                    f"update Antigravity if 'solidworks' does not appear in its MCP servers")
    if act is unregister and outcome.startswith("removed"):
        # the app and the CLI cache each server's tools per flavour and keep showing a removed server until its
        # cache folder goes; only our server's folders are deleted
        for flavour in ("antigravity", "antigravity-ide", "antigravity-cli"):
            cache = os.path.join(gemini_dir, flavour, "mcp", NAME)
            if os.path.isdir(cache):
                shutil.rmtree(cache, ignore_errors=True)
                outcome += f"; cache {cache} deleted"
    return outcome


if __name__ == "__main__":
    sys.exit(main())
