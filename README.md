# FullyDefined – AI for SOLIDWORKS

**A SolidWorks MCP server** that lets Claude, Gemini/Antigravity and other AI apps build fully defined SOLIDWORKS
parts, assemblies and drawings.

**To install: download `solidworks-mcp-<version>.zip` from the [Releases page](https://github.com/adinomyerson/FullyDefined-AI-for-SOLIDWORKS/releases), Extract All, then
double-click "Install SolidWorks MCP".**

Lets an AI assistant build and inspect SolidWorks models. You describe a part in plain language; the assistant uses
this server's tools to drive SolidWorks, and every result is checked by measuring the model. Works with AI apps
that run local [MCP](https://modelcontextprotocol.io/) servers.

**Tested with** Claude Desktop and Claude Code, using the Claude Opus and Claude Sonnet models; installed and used
on Antigravity. Other apps and models are your choice: they should work, but may work less well (please report).

## Requirements

- Windows 10 or 11.
- SolidWorks 2026, installed, licensed and **set to English**.
- An AI app that runs local MCP servers.

Tested on SolidWorks 2026 SP4.1 (English) and Windows 11.

## Install

1. Right-click the release zip (`solidworks-mcp-<version>.zip`) and choose **Extract All**.
2. In the extracted folder, double-click **Install SolidWorks MCP**.
3. If Windows asks: the release is not code-signed, so Windows warns about a file from the internet. On
   "Windows protected your PC" choose **More info > Run anyway**; on "Do you want to run this file?" choose **Run**.
4. Restart your AI app, as the installer's summary says.

The installer does the rest: it installs [uv](https://docs.astral.sh/uv/) and its own Python if needed (your other
Pythons are not touched), puts the server in `%LOCALAPPDATA%\solidworks-mcp`, connects it to every one of Claude
Desktop, Claude Code, Antigravity and Gemini CLI that is installed (also one never opened yet), and runs the
install check: a small test part built in SolidWorks, measured and closed unsaved, which must say **PASS** (if
SolidWorks is not running it asks once whether to start it). It ends with a summary of what it connected and what
to restart, and offers to restart Claude Desktop for you (only if you say yes).

The installer never replaces a `solidworks` server entry that points somewhere else. To replace it, open a Command
Prompt in the extracted folder and run:

```
"Install SolidWorks MCP.cmd" -Force
```

## First use

Start SolidWorks and ask: *"Connect to SolidWorks, create a new part, sketch a circle of 40 mm diameter on the Front
plane, extrude it 10 mm, and show me the result."*

The assistant works as your operator: you decide sizes, materials and tolerances. It asks once at the start
for anything missing, then builds the whole part and reports the measured numbers. It looks up recipes and pitfalls with the `solidworks_guide` tool (ask it to "show the SolidWorks guide
topics" to see them).

Lengths are in mm unless you ask for another unit. Your settings are in `%APPDATA%\solidworks-mcp\settings.json`
(the file holds only what you change), for example:

```json
{"groups": {"drawing": false}, "protected_paths": ["C:\\Vault"]}
```

`groups` switches tool groups off (`sketch` 19 tools, `features` 26, `sheetmetal` 12, `assembly` 16, `drawing` 14,
`export` 2; `core`, 19 tools, and the guide are always on); `protected_paths` lists folders the server never writes into. Restart your AI app after a change. With
Claude Desktop from the Microsoft Store, Windows keeps the server's files under
`%LOCALAPPDATA%\Packages\Claude_...\LocalCache\Roaming\solidworks-mcp` instead; `solidworks-mcp doctor` shows where.

## Google: the Antigravity app, Antigravity CLI or Gemini CLI

With Google AI Pro or Ultra use **Antigravity**, the app or its CLI (`agy`): since June 2026 Gemini CLI no longer
serves those plans. Gemini CLI still works with a paid Gemini API key. The app and the CLI share one MCP file,
`%USERPROFILE%\.gemini\config\mcp_config.json`, which the installer writes; Gemini CLI uses
`%USERPROFILE%\.gemini\settings.json`.

**Antigravity app:**
1. Install it from [antigravity.google/download](https://antigravity.google/download).
2. Run **Install SolidWorks MCP** (again, if you installed it before Antigravity). Its summary must say
   "Antigravity: registered". If it says "may be too old", update Antigravity first.
3. In Antigravity (sign in with your Google account if it asks): Settings (bottom left) > Customizations >
   Installed MCP Servers lists `solidworks` (click Refresh if it is not there yet).

**Antigravity CLI or Gemini CLI:** install it (Gemini CLI: `npm install -g @google/gemini-cli`, which needs
[Node.js](https://nodejs.org/)), run **Install SolidWorks MCP**, start it in a new terminal (`agy` or `gemini`;
sign in, Gemini CLI with your API key) and type `/mcp list` (`agy`) or `/mcp` (`gemini`): `solidworks` must show
as connected. Gemini CLI connects to servers only in a folder you trust: if it asks, trust it.

The server has about 110 tools. If the app reports too many tools or function declarations, switch groups off
(see First use) and restart the app.

## Other AI apps

Add the block the installer printed to the app's MCP config and restart the app. The command is always
`%LOCALAPPDATA%\solidworks-mcp\app\.venv\Scripts\solidworks-mcp.exe`, written as a full path:

```json
{ "mcpServers": { "solidworks": { "command": "C:\\Users\\<you>\\AppData\\Local\\solidworks-mcp\\app\\.venv\\Scripts\\solidworks-mcp.exe", "args": [] } } }
```

| App | Where the block goes |
|---|---|
| Cursor | `%USERPROFILE%\.cursor\mcp.json` (if it does not load, add `"type": "stdio"`). Cursor passes only a limited number of tools to the model: switch groups off (see First use) so the ones you need are among them |
| Windsurf | its Cascade MCP settings ("View raw config") |
| Cline | MCP Servers > Configure > "Configure MCP Servers" |
| LM Studio | Program tab > Install > "Edit mcp.json" |
| VS Code | `mcp.json` ("MCP: Open User Configuration"): the same entry under `"servers"`, with `"type": "stdio"` |
| Codex CLI | `%USERPROFILE%\.codex\config.toml`: `[mcp_servers.solidworks]` with `command = '<the path>'` |

The server lists about 110 tools; if an app works better with fewer, switch groups off (see First use). ChatGPT
cannot use it: it only connects to remote servers, and this server must run on the SolidWorks computer.

## What it cannot do

- Path, slider, lock-to-sketch and magnetic mates, and routing: SolidWorks does not offer them to add-on programs
  (routing also needs SolidWorks Premium).
- Drawing views at an oblique angle (save a named view in the model instead).
- Hole Wizard holes other than ISO; linear patterns along anything but the model axes.
- Weldments: trim / extend, end caps, gussets, rotating a profile or moving its pierce point (tested with the ISO
  tube profiles). Forming tools only in their library sizes.
- A flat-pattern view or a cut-list table on a drawing (the assistant reads the cut list instead).
- Rename a part so that closed assemblies follow it: copy it (`copy_file`) and replace the component instead.
- Work while SolidWorks shows a dialog: answer it in SolidWorks, then try again.
- Face and edge ids last only until the geometry changes: the assistant lists them again.

## Update

Extract the new release and run its **Install SolidWorks MCP**. Your settings and logs stay.

## Uninstall

Settings > Apps > Installed apps > **SolidWorks MCP** > Uninstall. (Or double-click **Uninstall SolidWorks MCP**
in `%LOCALAPPDATA%\solidworks-mcp`.) It removes the server from every app it was added to, its PATH entry and its
folder. Your settings and logs stay; to remove them too, run in a Command Prompt:

```
"%LOCALAPPDATA%\solidworks-mcp\Uninstall SolidWorks MCP.cmd" -Purge
```

uv stays.

## Support

Open an issue on GitHub ([Issues](https://github.com/adinomyerson/FullyDefined-AI-for-SOLIDWORKS/issues)), using the bug-report form; include the output of
`solidworks-mcp doctor`. Run `solidworks-mcp doctor` in a new terminal: it checks the setup (and SolidWorks, if it
is running; it never starts it) and writes a report file. Attach that report to the issue with what you asked the
AI, what happened and what you expected. [files/TESTING.md](files/TESTING.md) lists what helps most.

A tool that hangs usually means SolidWorks is showing a dialog: answer it there.

## Licence

MIT License, Copyright (c) 2026 Adino Myerson. See [LICENSE](LICENSE).

SOLIDWORKS is a registered trademark of Dassault Systèmes SolidWorks Corporation. This project is not affiliated
with or endorsed by Dassault Systèmes or Anthropic.
