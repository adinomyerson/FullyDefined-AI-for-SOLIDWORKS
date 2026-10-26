---
title: Examples
---

[Home](index.md) · [Install](install.md) · **Examples** · [What it can do](tools.md) · [Help](help.md)

Start SOLIDWORKS, open a new chat in your AI app and ask in your own words. The assistant asks once at the start
for anything missing (a size, a material), then builds everything and reports what it measured. Lengths are in mm
unless you say otherwise.

## A first part

> Connect to SolidWorks, create a new part, sketch a circle of 40 mm diameter on the Front plane, extrude it 10 mm,
> and show me the result.

You get the part, a picture of it, and the measured volume.

## A part with holes, fillets and a material

> Make a 100 x 60 x 10 mm plate with its corner on the origin. Add four M6 tapped holes 12 mm deep, 10 mm in from
> each edge at the corners. Fillet the four vertical edges with 5 mm. Make it 6061 aluminium and tell me its mass.

Every sketch is fully defined from the sizes you gave, and the holes are Hole Wizard holes.

## Change a design

> List the dimensions of this part, then change the plate thickness to 12 mm and tell me the new mass.

## Sheet metal

> Make a 2 mm sheet metal bracket: a 120 x 40 mm base flange with a 30 mm edge flange up on one long edge.
> Then export the flat pattern as a DXF to my Desktop and tell me its flat size.

## An assembly

> Make a new assembly with the base plate and the bracket I just saved. Put the bracket's bottom face on the top of
> the plate and line up its back face with the plate's back face. Check for interference and tell me what is still free to move.

Assemblies stay as free as you want them: the assistant adds only the mates you ask for, then reports which
motions are still free.

## A drawing

> Make an A3 drawing of the plate with the standard three views and an isometric view, add the model dimensions,
> arrange them so they don't overlap, fill in the title block, and export it as PDF.

## Files

> Export the plate as STEP and STL, and set its custom properties: part number BP-100, description "Base plate".

## Ask about the model

> Measure the distance between the two hole centres, and list the faces of the part.

## Tips

- **Say sizes and references as you would to a colleague:** "10 mm from the left edge", "centred on the
  origin". The assistant turns them into the dimensions you would draw.
- **Ask for a picture** at any point: "show me the part from the front".
- **The assistant can read its own guide:** ask "show the SolidWorks guide topics".
