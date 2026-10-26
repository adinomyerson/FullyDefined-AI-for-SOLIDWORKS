---
title: FullyDefined – AI for SOLIDWORKS
---

Describe a part in plain language. Your AI app builds it in SOLIDWORKS and checks every result by measuring the
model.

FullyDefined is a free [MCP](https://modelcontextprotocol.io/) server for Windows. It connects AI apps such as
Claude Desktop, Claude Code and Antigravity to SOLIDWORKS on your own computer.

**[Install it](install.md)** · [Examples](examples.md) · [What it can do](tools.md) · [Help](help.md) ·
[Download](https://github.com/adinomyerson/FullyDefined-AI-for-SOLIDWORKS/releases)

## In three steps

1. **Install:** download `FullyDefined-Setup-<version>.exe` from the
   [Releases page](https://github.com/adinomyerson/FullyDefined-AI-for-SOLIDWORKS/releases) and run it.
2. **Open your AI app** (Claude Desktop is recommended) and start SOLIDWORKS.
3. **Ask:** *"Create a new part, sketch a circle of 40 mm diameter on the Front plane, extrude it 10 mm, and show
   me the result."*

## What makes it different

- **Fully defined sketches.** Part sketches are dimensioned and related from the sizes and references you give, so
  every one stays editable by its dimensions.
- **Checked, not assumed.** Volume, mass, distances, faces and sketch status are measured from the model and
  reported against what you asked for.
- **You stay the designer.** The assistant asks once at the start for anything missing (sizes, material,
  tolerances), then builds the whole part and reports the measured numbers.
- **Your settings stay yours.** New documents use your own templates, units and drafting standard.

## What it can do

- **Parts:** sketches, extrude, cut, revolve, sweep, loft, fillet, chamfer, shell, mirror, patterns, Hole Wizard
  holes, reference planes, surfaces, materials, colours and equations.
- **Sheet metal and weldments:** flanges, tabs, hems, bends, forming tools, flat patterns and DXF; structural
  members and their cut list.
- **Assemblies:** components, mates, interference checks, exploded views.
- **Drawings:** standard, section and detail views, dimensions, balloons, a bill of materials, title block and
  sheets.
- **Files:** STEP, STL and PDF export; custom properties.

The full list, by area: [What it can do](tools.md).

## Requirements

- Windows 10 or 11.
- SOLIDWORKS 2026, installed, licensed and set to English.
- An AI app that runs local MCP servers: Claude Desktop, Claude Code, Antigravity and Gemini CLI are connected by
  the installer; [others](install.md#other-ai-apps) can be added by hand.

Tested on SOLIDWORKS 2026 SP4.1 (English) and Windows 11, with Claude Desktop and Claude Code using the Claude Opus
and Claude Sonnet models.

---

MIT License. SOLIDWORKS is a registered trademark of Dassault Systèmes SolidWorks Corporation. This project is not
affiliated with or endorsed by Dassault Systèmes or Anthropic.
