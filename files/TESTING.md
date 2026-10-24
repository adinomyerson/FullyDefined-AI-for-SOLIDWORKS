# Testing a release: what to send back

Thank you for trying it. Send these to the person who gave you this release.

**Before you start**, note what is already on your PC: Windows version, SolidWorks version and language, and
whether you already had Python, uv or the AI app.

1. **Your AI app and its version**: for Antigravity, say whether you used the app or the CLI (app: Help > About,
   or the version in Settings; CLI: `agy --version`). Otherwise Gemini CLI (`gemini --version`), Claude Desktop or
   Claude Code.
2. **The installer window**: before closing the "Install SolidWorks MCP" window, click in it, press Ctrl+A, then Ctrl+C, and
   paste it into a text file.
3. **Any Windows warning** when you started "Install SolidWorks MCP" ("Windows protected your PC", "Do you want to run this
   file?", red text): what it said and what you clicked.
4. **The install check**: the last lines of `solidworks-mcp doctor --check` (PASS or FAIL).
5. **The doctor report**: the file whose path `solidworks-mcp doctor` prints (`doctor-<date>.txt`). Attach it.
6. **The server in your app**: in the Antigravity app, a screenshot of Settings > Customizations > Installed MCP
   Servers; in Antigravity CLI the output of `/mcp list`, in Gemini CLI of `/mcp` (in Claude Desktop, a screenshot
   of its tools list).
7. **One line per step**: unzip, install, install check, start the AI app, the first question from the README.
   For each: OK, or what went wrong.
8. **Anything you had to guess**, or do, that the README did not say, even small things.
9. **If you uninstall**: was SolidWorks MCP in Settings > Apps > Installed apps, and did its Uninstall finish
   without red text?
10. **Finding your way**: after extracting the zip, was it clear what to double-click?
