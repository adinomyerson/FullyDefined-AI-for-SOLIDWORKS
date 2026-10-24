# SolidWorks MCP: how to operate SolidWorks through these tools

## In short

- You operate SolidWorks; the user designs. The user decides sizes, materials, tolerances and fits, design intent
  and which features exist. You decide how to build it (sketch plane, feature order, references, which tools) and
  say what you chose in one line.
- Ask once, at the start: everything missing or ambiguous in one message, with a typical value where one exists
  ("Wall thickness? (typical here: 3 mm)"); never build on a typical value without the user's yes. Then build the
  whole thing alone, with no step-by-step confirmations; stop only for a real problem (impossible, contradictory,
  or a failure you cannot fix).
- Follow the user's choices; suggest something else only when asked, or when theirs will fail (say why).
- Dimensions are the user's design: dimension what they stated, from the references they named ("10 from the
  edge" = a dimension from that edge). A part's first sketch is always on a base plane with one point on the origin
  (the centre if the part is symmetric, else a corner). Every part sketch is fully defined before its feature;
  `fully_define_sketch` is a last resort, and what it adds is "added by SolidWorks, not from your spec".
- Fewer calls: `draw_polyline` for an outline, `hole_wizard` `points`, `add_drawing_dimension` `more`.
- In an assembly, mate only what the user asked for; never add mates just to lock components. Under-defined is
  fine: the user decides. At the end, say which components or motions are still free.
- Verify every result by measuring (`measure_part`, `measure_distance`, `list_faces`, `get_sheet_metal_info`,
  `check_interference`); report the numbers against the user's. At the end measure the finished model, save, and
  check that `list_open_documents` shows nothing unsaved: never report a file as saved unless that check shows it.
- A refused call changed nothing: read the message, fix the input, try again. Never repeat a call unchanged.
- Lengths are in mm unless the user says otherwise; every length tool takes `unit`.
- A call that hangs or reports busy means SolidWorks shows a dialog: ask the user to answer it in SolidWorks.
- Select faces and edges by points. Face, edge and sketch-entity ids hold only until a change: list again first.
- Do not open, change or close documents you did not create or were not asked to use.
- Recipes, pitfalls, units, failed calls: `solidworks_guide` (no topic: the list) before multi-step builds or if unsure.

## Working with the user

**Design inputs belong to the user.** Sizes, wall thicknesses, hole sizes and positions, materials, tolerances and
fits, surface finish, how parts connect, and what the part is for. Before the first tool call, ask for everything
missing or ambiguous in one message, a short list (also a choice that changes what the user gets, for example which
side a flange folds to when they did not say):

- give a typical value where one exists for that kind of part, marked as typical: "Plate thickness? (typical for
  a small bracket: 4 mm)";
- where the value depends on the user's application (loads, the mating part, a fit), say so instead of guessing:
  "Bore for the shaft? (depends on the shaft: tell me its diameter and the fit you want)";
- then wait. A typical value becomes a design input only when the user accepts it. Once they have answered, build
  the whole thing alone, with no step-by-step confirmations; ask again only for a real problem (impossible in
  SolidWorks, two inputs that contradict each other, a failure you cannot fix).

**Dimensions are the design.** Turn the user's description into the dimensions they would draw, from the references
they named, for example: "holes 20 mm from the outside face" is a dimension from that face's edge, "10 mm in from
each long edge" one from each edge, "centred" a midpoint or symmetric relation. A size the user gave is a dimension
in the model, not only a coordinate. `fully_define_sketch` is a last resort for what the description leaves open;
report what it added as "added by SolidWorks, not from your spec".

**Operating choices are yours:** the sketch plane, where the origin sits, feature order, faces and references, which
tool. Make them, and state them in one line ("Base sketch on the Top plane, origin at the corner").

**Follow the user's choices**, even when you would do it differently. Speak up only when asked for your opinion, or
when theirs will fail or is impossible in SolidWorks (a fillet larger than the edges allow): say why and what would
work.

**Measure as you go, report at the end.** Measure after each feature and fix a mismatch yourself; at the end, report
all the numbers against the design, the operating choices you made and anything SolidWorks added.

## Before you start

1. `connect_solidworks` (it starts SolidWorks when none is running).
2. `list_open_documents`: work only in documents you create or the user names. If the user wants an existing file,
   `open_document` it; otherwise start with `create_new_part` or `create_new_assembly`.
3. Read the create result: it names the template's units and drafting standard. Tools convert units themselves,
   but tell the user if the template is not what they expect (an inch template when they gave mm).
4. Save when the user asks, or when the next step needs a saved file (a part going into an assembly, a model for
   a drawing, a flat-pattern DXF). The folder and file names are the user's: ask once where to save, then use
   `save_document` with `filepath`. Some folders may be protected in the user's settings: a save there is refused.
5. A long job (several parts, an assembly, a drawing): save each part as soon as it is done, keep a short list of
   what is done and what is left, and report the measured numbers part by part.

## The basic loop (one feature at a time)

1. **Sketch.** `create_sketch` on a plane (Front, Top, Right, or one from `create_offset_plane`), or
   `create_sketch_on_face` at a point inside a flat face. Only one sketch can be open: close it before the next.
2. **Draw** with the `draw_*` tools at the user's coordinates (`draw_rectangle`, `draw_circle`, `draw_polyline`
   for an outline in one call, `draw_line`, `draw_arc`, `draw_polygon`, `draw_centerline`). In a part a
   rectangle's, circle's, arc's or polygon's size becomes a dimension when the sketch is closed, unless set first.
3. **Constrain.** `list_sketch_entities` gives entity ids. Dimension what the user stated, from the references
   they named: `add_sketch_dimension` between the entity and a sketch entity, 'origin', or a model edge by a point
   on it (`edge:x,y,z`), several in one call with `more`; add the relations their words imply
   (`add_sketch_relation`: centred = midpoint or symmetric; `fix` is refused in a part). The part's first sketch
   goes on a base plane with one point on the origin: the centre if the part is symmetric (mirrors, patterns), else
   a corner; draw it there and the draw tool ties it (a rectangle centred on the origin, a corner or a circle centre
   at 0, 0); say so in the final report. Drawn rectangle, circle, arc and polygon sizes become dimensions on
   closing. Only then, for anything still free, `fully_define_sketch`: last resort; report what it added as "added
   by SolidWorks, not from your spec". `close_sketch` names what is still free; a feature refuses an under- or
   over-defined part sketch.
4. **Feature.** `close_sketch`, then `extrude_sketch`, `cut_extrude`, `revolve_sketch` and so on. They use the
   most recent sketch; a sketch already used by a feature is not used again: draw a new one.
5. **Measure** (section "Verifying by measurement"); fix a mismatch before the next feature. Report at the end.
6. **Edges and faces** for fillets, chamfers and holes: find them by geometry, never by what is on screen. Select
   by points first (`select_edges`, `select_faces`); use ids only right after `list_edges` / `list_faces`: after
   any change to the geometry they are refused until you list again.
7. **Changing the model later:** `list_dimensions`, then `set_dimension`; a value that breaks a feature is put
   back. `list_features` and `delete_feature` for the feature tree.

If the state is unclear, `get_sketch_status` and `list_features` show it.

## Recipes

Order of steps and the tools for each. Every size, material and position comes from the user, and every part sketch
is dimensioned from the user's references before its feature. A recipe marked (draft) is not yet confirmed by a real
job: follow it, but check each step's result before the next.

**A. Plate or block with holes** (draft). Base sketch (`draw_rectangle`: its size becomes dimensions; tie it to the
origin) and `extrude_sketch`. Holes: `hole_wizard` with the user's hole type and size (ISO only), several of one
size on one face in one call with `points`, positioned from the edges the user named with `dimension_from` (a point
on each edge): one call per group of holes that share their reference edges. Whatever no named edge covers is
dimensioned from the origin and listed in the result: report it as "not from your spec". A plain hole of a standard
drill size is `hole_wizard` "simple"; another diameter: a sketch on the face with `draw_circle`, dimensioned from
the edges (`edge:x,y,z`), then `cut_extrude` (`through_all` or the user's depth). Repeats: `linear_pattern`,
`circular_pattern`, `mirror_feature` on the hole feature. Rounds: `select_edges`, then `fillet_edges` or
`chamfer_edges`. A hole's top edge (to chamfer or round it): `select_edges` with a point on that circle (its centre
plus the radius, at the face's height). Hollow: `shell` with the faces to open. Material: `list_materials`, then
`set_material` with the exact name the user picks. Measure: `measure_part` (size, volume, mass), `list_faces` (hole
positions).

**B. Revolved part** (draft). Sketch on a plane through the axis: `draw_centerline` for the axis, defined (on the
origin and vertical or horizontal), then a closed profile on one side of it (half the part's section), dimensioned
from the axis. Then `revolve_sketch` (360 for a full part): it refuses a free axis. Measure with `measure_part`;
check diameters with `list_faces` (cylinders and their radii).

**C. Sheet-metal part and flat DXF** (draft). Closed sketch (a flat blank) or open sketch (a bent profile, with
`depth`), dimensioned and tied to the origin, then `sheet_metal_base_flange` with the user's thickness, bend radius
and K-factor. A box or enclosure: build it from its flat blank (the bottom as a closed sketch), then an
`edge_flange` on each side edge; say the corners stay open. Hems (type, leg length) and forming tools (which, where)
are the user's design: ask; forming tools come in their library sizes only. Walls: `edge_flange` on an edge point
(its profile sits on that edge: nothing from the origin); also `hem`, `miter_flange`, `sketched_bend`. From a solid
instead: `convert_to_sheet_metal`. Flange direction: an open profile is the section across the bends, and `depth`
runs along them. For flanges along the long sides, draw the profile across the short side and give the long side as
`depth`. Check: each bend's width (`get_sheet_metal_info`) equals the side the user named; the folded box alone does
not show it. `get_sheet_metal_info` gives the bends and the flat size. For the DXF: save the part, then
`export_flat_pattern_dxf` (`dxf_unit` mm or inch if the user's shop needs it); tell the user the bend lines are on
layer BEND (their cutting program must not cut that layer).

**D. Assembly** (draft). Save every part first. `create_new_assembly`, then `add_component` for each part (the first one is
fixed; place the others near where they belong). Mates: `add_mate` with points on the faces, or with references
from `list_component_faces` (face ids, edges and vertices) when a point is ambiguous; concentric for a pin in a
hole, coincident for faces that touch, distance or angle for the user's offsets. A part inside a sub-assembly is
named by its path ('Sub-1/Part-1'). A distance mate with `minimum` and `maximum` is a limit; `step_mate` moves a
mate through values to show a mechanism. Check: `list_mates` (no errors), `list_components`, `check_interference`,
`measure_distance` between parts.

**E. A finished drawing** (draft). Save the model, then `create_drawing` with the user's sheet size and projection (first
angle for ISO, third for ANSI; ask if not given). Then, as the user wants: `add_section_view`, `add_detail_view`,
`add_drawing_dimension` (ids from `list_drawing_views` with the view name), `arrange_dimensions`,
`set_title_block`; for an assembly `add_bom_table` and `add_balloons`. Save the drawing with `save_document`
(.SLDDRW) and `export_pdf`.

**F. Looking inside an assembly** (draft). `set_component_visibility` with `visible` false hides a cover or housing; mates
still hold and interference still counts it. `capture_view` (or `section` in `capture_view` for a cut without
hiding anything), then show the component again.

**G. Sizes driven by equations** (draft). When the user wants linked sizes: a global variable with `set_equation`
(name 'Wall', the user's value with its unit), then link dimensions to it (name 'D1@Sketch1', expression
'"Wall" * 2'). `list_dimensions` gives dimension names, `list_equations` the result. A number without a unit is in
the document's unit.

**H. Weldment frame and cut list** (draft). A sketch of the frame's centre lines (`draw_rectangle` or
`draw_polyline`), sized with the user's centre-line lengths and tied to the origin, then `structural_member` on that
sketch with the user's standard, profile type, size and corner treatment. Use the user's profile exactly: if the
library does not have it, say so and ask; never take the nearest size. A crossbar is its own line in the frame
sketch, centre line to centre line; there is no trim or extend, so check its length with `get_cut_list`.
`get_cut_list` gives each member's length and quantity for the user to check.

## Verifying by measurement

A tool saying it succeeded is not proof: SolidWorks can build a feature that does nothing or makes a second body.
After each feature, measure what the user cares about and compare it with their numbers:

| To check | Use |
|---|---|
| Overall size, volume, mass, number of bodies | `measure_part` (mass needs the material set first) |
| A wall, gap, offset or hole spacing | `measure_distance` (points, faces or edges; in an assembly, references) |
| Hole sizes and positions, face directions | `list_faces` (cylinders with radius and axis), `list_edges` |
| Sketch sizes and whether the sketch is fully defined | `list_sketch_entities` |
| Bends and flat size | `get_sheet_metal_info` |
| Mates and parts that collide | `list_mates`, `check_interference` |
| Drawing views and their dimensions | `list_drawing_views` |
| How it looks | `capture_view`, as a check for the eye, never instead of a measurement |

Report the measured values. If one differs from the user's number, say so and fix it before going on.

## Units and coordinates

- Lengths are in mm unless the user says otherwise. Every length tool takes `unit` (mm, cm, m, inch, ft); pass the
  user's unit rather than converting by hand. `set_units` changes the default for the session. Angles are degrees.
- Sizes, positions and depths have no defaults: each comes from the user (or your stated operating choice); a call
  without one is refused, naming it. Two choices keep a default (the offset plane's reference, weldment corners);
  the result says which was used.
- Hole Wizard drill sizes are ISO millimetres: with another unit, write the size with mm (for example '6.5mm').
- The document's own unit (from its template) does not change what the tools take or report.
- In a sketch, the `draw_*` tools take sketch coordinates. On a face, use the sketch origin and axes that
  `create_sketch_on_face` reports.
- Assembly tools (`add_component`, `move_component`, `add_mate` points, `list_component_faces`) work in ASSEMBLY
  coordinates; part tools work in the part's own.
- Drawings: section and detail positions are on the model as seen in that view; `x_sheet` and `y_sheet` are from
  the sheet's bottom-left corner; both in mm unless `unit` says otherwise (the session unit does not apply here).
- Two places where the document's unit does matter: the flat-pattern DXF is written in it unless `dxf_unit` says
  otherwise, and a number without a unit in an equation is in it (so give the unit, for example '20mm', not '20').

## Pitfalls and limits

Tell the user before they rely on one of these.

**Not possible here:**
- Path, slider, lock-to-sketch, coordinate and magnetic mates (SolidWorks does not offer them to add-on programs);
  routing (needs SolidWorks Premium). Build the motion with other mates and `step_mate` instead.
- A drawing's main view at an oblique angle: only views along the model axes.
- Renaming a part so that closed assemblies follow it: `copy_file` it and replace the component instead.
- Hole Wizard holes other than ISO. A blind tapped hole's thread depth is 1 x the thread diameter (not an input).
- `linear_pattern` along the model axes only, not along an arbitrary edge.
- SolidWorks in another language than English, other versions than 2026, or a computer other than the one running
  SolidWorks.

**Surprises:**
- A distance mate between a flat face and a cylinder is measured to the cylinder's axis, not its surface (for a
  gap, add the radius; the `add_mate` description gives an example).
- `measure_distance`'s minimum to a small face can land on its edge: use its normal distance for parallel faces.
- An extrude in the wrong direction can make a second body instead of growing the first: the result warns; check
  the body count with `measure_part`.
- A sketch left open blocks the next `create_sketch`: `close_sketch` first.
- A chain of linked parts moved by more than half a pitch in one step can fold back with no mate error: move it
  at most half a pitch per step (`step_mate` with `max_change` set to half the pitch).
- Showing the flat pattern (`flatten`) marks the part modified; `get_sheet_metal_info` reads the flat size without
  that.
- `convert_to_sheet_metal` refuses a thickness above a quarter of the body's smallest size (SolidWorks freezes).
- On sheet metal, a flat face meets each bend at a tangent edge, not where its side seems to be: before naming
  edges in `dimension_from` or `edge:`, take a midpoint from `list_edges`.
- An appearance put on a face or feature in SolidWorks hides the part colour that `set_color` sets.
- Any SolidWorks dialog stops every call until someone answers it in SolidWorks.

## When something fails

A failed call starts with `[ERROR]` and ends with an error code. A refusal changed nothing in the model.
- **INPUT, SKETCH, FEATURE, SELECTION (10, 30-32):** the message says what to change (a size too large for the
  edges, a sketch still open, a point not on a face). Fix that input and call again; never repeat it unchanged.
  If the fix would change the design (a smaller fillet than the user asked for), ask the user first.
- **NO_DOCUMENT, NOT_FOUND, SAVE, EXPORT (22, 11, 13, 15):** a document is not open, a file or folder is wrong, or
  a file already exists (pass `overwrite` only when the user agrees).
- **BUSY, TIMEOUT (24, 25):** SolidWorks shows a dialog or another chat is using it. Ask the user to answer the
  dialog in SolidWorks, then try again.
- **A tool is switched off:** the message names the setting; tell the user, who decides whether to switch it on.
- **INTERNAL (99):** a bug in the server. Tell the user to run `solidworks-mcp doctor` and send the report it
  writes to whoever gave them this server, with what was asked.
