---
title: Install
---

Windows 10 or 11, SOLIDWORKS 2026 in English, and an AI app. No administrator rights needed.

## 1. Download

**[Download for Windows](https://github.com/adinomyerson/FullyDefined-AI-for-SOLIDWORKS/releases/latest)**: the file
`FullyDefined-Setup-<version>.exe`.

## 2. Run it

Double-click it. Windows warns because the installer is not code-signed: click **More info**, then **Run anyway**.

![Windows protected your PC: More info, then Run anyway](images/smartscreen.jpg)

## 3. Tick your AI apps and install

The apps found on your PC are already ticked. Keep "Start SOLIDWORKS for this check" ticked: the installer builds a
small test part and tells you it works.

## 4. Restart your AI app and ask

> Create a new part and extrude a 40 mm diameter disc 10 mm thick.

More to try: [Examples](examples.html).

## Works with

<ul class="apps">
  <li><span class="tag tested">Tested</span><span>Claude Desktop and Claude Code, with the Claude Opus and Claude Sonnet models.</span></li>
  <li><span class="tag">Installed and used</span><span>Antigravity.</span></li>
  <li><span class="tag">Should work</span><span>Gemini CLI and other apps that run local MCP servers. Please report how it goes.</span></li>
  <li><span class="tag">Note</span><span>Cursor gives the model only about 40 tools, fewer than building a part needs.</span></li>
</ul>

## Update or uninstall

To update, run the new installer: your settings stay. To uninstall: Settings > Apps > Installed apps >
**SolidWorks MCP** > Uninstall.

Other setups (the zip, Antigravity, other apps, where files go): [Help](help.html#more-setup).
