---
title: Install
---


You need Windows 10 or 11, SOLIDWORKS 2026 (installed, licensed and set to English) and an AI app that runs
local MCP servers. Nothing else: the installer brings its own Python, installs into your user folder and adds FullyDefined to the AI
apps you tick. No administrator rights are needed.


## Works with

<ul class="apps">
  <li><span class="tag tested">Tested</span><span>Claude Desktop and Claude Code, with the Claude Opus and Claude Sonnet models.</span></li>
  <li><span class="tag">Installed and used</span><span>Antigravity.</span></li>
  <li><span class="tag">Should work</span><span>Gemini CLI and other apps that run local MCP servers. Please report how it goes.</span></li>
  <li><span class="tag">Note</span><span>Cursor gives the model only about 40 tools, fewer than building a part needs.</span></li>
</ul>

## 1. Download

On the [Releases page](https://github.com/adinomyerson/FullyDefined-AI-for-SOLIDWORKS/releases), download
**`FullyDefined-Setup-<version>.exe`** from the latest release.

## 2. Run it

Double-click the downloaded file.

**If Windows shows "Windows protected your PC":** the installer is not code-signed, so Windows SmartScreen warns
about a new file from the internet. Click **More info**, then **Run anyway**:

![Windows protected your PC: More info, then Run anyway](images/smartscreen.jpg)

If your browser asks whether to keep the file, choose **Keep**.

## 3. Follow the installer

1. **Welcome:** click Next.
2. **Your AI apps:** the apps found on this PC are already ticked: Claude Desktop (recommended), Claude Code,
   Antigravity (app and CLI) and Gemini CLI. Tick or untick what you want. Claude Desktop can be ticked before it is
   installed (its settings are made ready); the others are connected only when they are installed: run the
   installer again after installing one.
3. **Check and finish:**
   - *Start SOLIDWORKS for this check if it is not running* (recommended). The installer builds a small test part
     in SOLIDWORKS, measures it and closes it without saving.
   - *Restart Claude Desktop at the end*: optional. It closes Claude Desktop's windows, so leave it unticked if
     you are in the middle of something.
   - *Replace a "solidworks" server another program added*: only if you no longer use that other program's
     server. Unticked, the installer leaves it as it is and the summary says so.
4. **Install:** it takes a minute or two (longer the first time). No window opens.
5. **Finished:** the last page shows what was connected, the result of the check, and what to restart.

## 4. Restart your AI app

Restart the AI apps the summary names, so they see FullyDefined. Then open a new chat, start SOLIDWORKS and ask,
for example:

> Create a new part and extrude a 40 mm diameter disc 10 mm thick.

More to try: [Examples](examples.md).

## Where things go

- The program: `%LOCALAPPDATA%\solidworks-mcp` (your own user folder).
- Your settings: `%APPDATA%\solidworks-mcp\settings.json` (it holds only what you change).
- The installer's log: `%LOCALAPPDATA%\solidworks-mcp-install.log`.

The installer replaces a `solidworks` server entry that another program added only when you tick that option.

## Update

Download the new `FullyDefined-Setup-<version>.exe` and run it. Your settings and logs stay. Restart your AI app
afterwards.

## Uninstall

Settings > Apps > Installed apps > **SolidWorks MCP** > Uninstall. It removes the server from every app it was
added to, and its folder. Your settings and logs stay. To remove them too, run this in a Command Prompt:

```
"%LOCALAPPDATA%\solidworks-mcp\Uninstall SolidWorks MCP.cmd" -Purge
```

## Without the installer: the zip

Each release also has `solidworks-mcp-<version>.zip`:

1. Right-click it and choose **Extract All**.
2. In the extracted folder, double-click **Install SolidWorks MCP**.
3. If Windows warns: **More info > Run anyway** (or **Run**).

It does the same as the `.exe`, in a console window.

## Antigravity and Gemini CLI

- With Google AI Pro or Ultra, use **Antigravity**: the app or its CLI (`agy`). Gemini CLI works with a paid
  Gemini API key.
- The app and its CLI share one MCP file, `%USERPROFILE%\.gemini\config\mcp_config.json`, which the installer
  writes. Gemini CLI uses `%USERPROFILE%\.gemini\settings.json`.
- **Antigravity app:** Settings (bottom left) > Customizations > Installed MCP Servers lists `solidworks`. Click
  Refresh if it is not there yet.
- **CLIs:** start a new terminal and type `/mcp list` (`agy`) or `/mcp` (`gemini`): `solidworks` must show as
  connected. Gemini CLI connects only in a folder you trust.

## Other AI apps

Add this block to the app's MCP configuration (replace `<you>` with your Windows user name), then restart the app:

```json
{ "mcpServers": { "solidworks": { "command": "C:\\Users\\<you>\\AppData\\Local\\solidworks-mcp\\app\\.venv\\Scripts\\solidworks-mcp.exe", "args": [] } } }
```

| App | Where the block goes |
|---|---|
| Cursor | `%USERPROFILE%\.cursor\mcp.json` (add `"type": "stdio"` if it does not load) |
| Windsurf | its Cascade MCP settings ("View raw config") |
| Cline | MCP Servers > Configure > "Configure MCP Servers" |
| LM Studio | Program tab > Install > "Edit mcp.json" |
| VS Code | `mcp.json` ("MCP: Open User Configuration"): the same entry under `"servers"`, with `"type": "stdio"` |
| Codex CLI | `%USERPROFILE%\.codex\config.toml`: `[mcp_servers.solidworks]` with `command = '<the path>'` |

The server lists about 120 tools. Cursor gives the model only about 40 tools (its own limit), fewer than building
a part needs, so it may not be able to build parts with this server: Claude Desktop, Claude Code and Antigravity
have no such limit. Other apps with a limit: switch tool groups you do not use off (see [Help](help.md#settings)). ChatGPT cannot use it: it connects only to remote
servers, and this server must run on the SOLIDWORKS computer.
