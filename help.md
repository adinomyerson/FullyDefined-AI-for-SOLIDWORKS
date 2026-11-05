---
title: Help
---


## Something does not work

1. **A tool hangs or the assistant waits:** SOLIDWORKS is probably showing a dialog. Answer it in SOLIDWORKS, then
   ask again.
2. **The assistant does not see the SOLIDWORKS tools:** restart the AI app (fully: close it from the system tray
   too) and open a new chat.
3. **Check the setup:** open a new Command Prompt and run

   ```
   solidworks-mcp doctor
   ```

   It checks the installation, the connection to each AI app and, if SOLIDWORKS is running, SOLIDWORKS itself (it
   never starts it). It writes a report file and says where.

## Report a problem or ask for something

Open an issue on [GitHub](https://github.com/adinomyerson/FullyDefined-AI-for-SOLIDWORKS/issues) with the bug-report
form. Attach the doctor report, and say what you asked the AI, what happened and what you expected.

Ideas and requests are welcome the same way.

## Settings

Most people never need settings. If you do, they are in `%APPDATA%\solidworks-mcp\settings.json` (the file holds
only what you change). For example:

```json
{"groups": {"drawing": false}, "protected_paths": ["C:\\Vault"]}
```

- `groups` switches tool groups off: `sketch` (20 tools), `features` (26), `sheetmetal` (12), `assembly` (20),
  `drawing` (18), `export` (3). `core` (21 tools) and the guide are always on. This helps with apps that limit the
  number of tools.
- `protected_paths` lists folders the server never writes into.
- `auto_save` (true by default) saves a part the AI created and saved again as it works. Your own files, assemblies
  and drawings are never auto-saved. `auto_save_minutes` (5) is the time between saves, 0 for every change.
  `auto_save_versions` (false) also keeps each saved version next to the file: `part_v1.SLDPRT`, `part_v2.SLDPRT`.

Restart your AI app after a change.

With Claude Desktop from the Microsoft Store, Windows keeps the server's files under
`%LOCALAPPDATA%\Packages\Claude_...\LocalCache\Roaming\solidworks-mcp` instead; `solidworks-mcp doctor` shows
where.

## More setup

### Where things go

- The program: `%LOCALAPPDATA%\solidworks-mcp` (your own user folder).
- Your settings: `%APPDATA%\solidworks-mcp\settings.json` (it holds only what you change).
- The installer's log: `%LOCALAPPDATA%\solidworks-mcp-install.log`.

The installer replaces a `solidworks` server entry that another program added only when you tick that option.

### Uninstall everything

Settings > Apps > Installed apps > **SolidWorks MCP** > Uninstall (or double-click **Uninstall SolidWorks MCP** in
`%LOCALAPPDATA%\solidworks-mcp`) removes the server from every app it was added to, its PATH entry and its folder.
Your settings and logs stay, and so does uv. To remove the settings and logs too, run this in a Command Prompt:

```
"%LOCALAPPDATA%\solidworks-mcp\Uninstall SolidWorks MCP.cmd" -Purge
```

### Without the installer: the zip

Each release also has `solidworks-mcp-<version>.zip`:

1. Right-click it and choose **Extract All**.
2. In the extracted folder, double-click **Install SolidWorks MCP**.
3. If Windows warns: **More info > Run anyway** (or **Run**).

It does the same as the `.exe`, in a console window.

### Antigravity and Gemini CLI

- With Google AI Pro or Ultra, use **Antigravity**: the app or its CLI (`agy`). Gemini CLI works with a paid
  Gemini API key.
- The app and its CLI share one MCP file, `%USERPROFILE%\.gemini\config\mcp_config.json`, which the installer
  writes. Gemini CLI uses `%USERPROFILE%\.gemini\settings.json`.
- **Antigravity app:** install it from [antigravity.google/download](https://antigravity.google/download), then run
  the installer (again, if you installed it before Antigravity). Its summary must say "Antigravity: registered"; if it
  says "may be too old", update Antigravity first. Then Settings (bottom left) > Customizations > Installed MCP Servers
  lists `solidworks`. Click Refresh if it is not there yet.
- **CLIs:** install one (Gemini CLI: `npm install -g @google/gemini-cli`, which needs [Node.js](https://nodejs.org/)),
  run the installer, start a new terminal and type `/mcp list` (`agy`) or `/mcp` (`gemini`): `solidworks` must show as
  connected. Gemini CLI connects only in a folder you trust.

### Other AI apps

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
| VS Code (GitHub Copilot) | The installer does this when you tick VS Code. By hand: `mcp.json` ("MCP: Open User Configuration"): the same entry under `"servers"`, with `"type": "stdio"` |
| Codex CLI | `%USERPROFILE%\.codex\config.toml`: `[mcp_servers.solidworks]` with `command = '<the path>'` |

The server lists about 120 tools. Cursor gives the model only about 40 tools (its own limit), fewer than building
a part needs, so it may not be able to build parts with this server: Claude Desktop, Claude Code and Antigravity
have no such limit. Other apps with a limit: switch tool groups you do not use off (see [Settings](#settings)). ChatGPT cannot use it: it connects only to remote
servers, and this server must run on the SOLIDWORKS computer.

## Good to know

- **You are the designer.** The assistant asks for sizes, materials and tolerances it does not have, once, at the
  start, and is guided not to invent a design size.
- **Your templates and units stay as they are.** New parts, assemblies and drawings use your default templates; the
  result says which units and drafting standard they have.
- **SOLIDWORKS in English** is what it is tested with. Parts and assemblies from templates in other languages work
  too.
- **Drawings of a part with configurations** read each dimension's value in the part's active configuration, not the
  configuration a view shows.

### What it cannot do

- Path, slider, lock-to-sketch and magnetic mates, and routing: SOLIDWORKS does not offer them to add-on programs
  (routing also needs SOLIDWORKS Premium).
- Drawing views at an oblique angle (save a named view in the model instead).
- Hole Wizard holes other than ISO; linear patterns along anything but the model axes.
- Weldments: trim / extend, end caps, gussets, rotating a profile or moving its pierce point (tested with the ISO
  tube profiles). Forming tools only in their library sizes.
- A flat-pattern view or a cut-list table on a drawing (the assistant reads the cut list instead).
- Rename a part so that closed assemblies follow it: copy it (`copy_file`) and replace the component instead.
- Work while SOLIDWORKS shows a dialog: answer it in SOLIDWORKS, then try again.
- Undo: the assistant reverses a step with the matching tool (deleting the feature, or setting a size back); a
  deleted feature does not come back.
- Strength checks (no simulation).
- Face and edge ids last only until the geometry changes: the assistant lists them again.

## Licence

MIT licence: see [LICENSE](https://github.com/adinomyerson/FullyDefined-AI-for-SOLIDWORKS/blob/main/LICENSE).

SOLIDWORKS is a registered trademark of Dassault Systèmes SolidWorks Corporation. This project is not affiliated
with or endorsed by Dassault Systèmes or Anthropic.
