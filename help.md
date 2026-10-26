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

Restart your AI app after a change.

With Claude Desktop from the Microsoft Store, Windows keeps the server's files under
`%LOCALAPPDATA%\Packages\Claude_...\LocalCache\Roaming\solidworks-mcp` instead; `solidworks-mcp doctor` shows
where.

## Good to know

- **You are the designer.** The assistant asks for sizes, materials and tolerances it does not have, once, at the
  start. It never invents a design size.
- **Your templates and units stay as they are.** New parts, assemblies and drawings use your default templates; the
  result says which units and drafting standard they have.
- **Face and edge ids last only until the geometry changes.** The assistant lists them again when needed.
- **Renaming a part that assemblies use:** copy it (`copy_file`) and replace the component instead.
- **SOLIDWORKS in English** is what it is tested with. Other languages may use different names for planes and
  views.

## Licence

MIT licence: see [LICENSE](https://github.com/adinomyerson/FullyDefined-AI-for-SOLIDWORKS/blob/main/LICENSE).

SOLIDWORKS is a registered trademark of Dassault Systèmes SolidWorks Corporation. This project is not affiliated
with or endorsed by Dassault Systèmes or Anthropic.
