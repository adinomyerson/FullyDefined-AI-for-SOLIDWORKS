---
title: What it can do
---


121 tools, in tool groups. You never call them yourself: you ask in plain language and the assistant picks the tools. Every group except the always-on ones can be switched off in your settings (see [Help](help.md#settings)).

## Documents, measuring and selection (21)

Always on.

| Tool | What it does |
|---|---|
| `capture_view` | Capture a PNG of the active document's graphics view, to inspect the model visually. |
| `clear_selection` | Empty the current selection. |
| `close_document` | Close the current document. |
| `connect_solidworks` | Attach to the running SolidWorks, or start it first when none is running. |
| `copy_file` | Copy a file to a new path, keeping the extension. |
| `create_new_assembly` | Create an empty assembly from the default template and make it the active document; the result names the template's units and standard. |
| `create_new_part` | Create an empty part from the default template and make it the active document; the result names the template's units and standard. |
| `get_document_info` | Report the active document's title, file path ('Not saved' when it has none) and type. |
| `get_solidworks_info` | Report the SolidWorks installation, the server's version, the settings and log file it uses, and any tool modules that failed to load. |
| `list_custom_properties` | List the custom properties of the active document. |
| `list_edges` | List the edges of the solid body. |
| `list_faces` | List the faces of the solid body. |
| `list_open_documents` | List every document that is open in SolidWorks. |
| `measure_distance` | Measure between two model points, or between the two faces or edges at those points. |
| `measure_part` | Measure the part: volume, mass, surface area, centre of mass and size. |
| `open_document` | Open a SolidWorks file and make it the active document. |
| `save_document` | Save the current document. |
| `select_edges` | Select body edges by geometry (the view does not matter), ready for fillet_edges / chamfer_edges. |
| `select_faces` | Select body faces by geometry (the view does not matter). |
| `set_custom_properties` | Set or delete custom properties of the active document (part, assembly or drawing). |
| `set_units` | Set the default length unit, mm, inch, cm or m, used when a tool's unit input is left out. |

## Sketches and reference planes (20)

Part sketches are fully defined from your own dimensions and references.

| Tool | What it does |
|---|---|
| `add_sketch_dimension` | Add a driving dimension in the open sketch and optionally set its value. |
| `add_sketch_relation` | Add a geometric relation in the open sketch. |
| `close_sketch` | Exit the open sketch, rebuilding the features made from it. |
| `create_offset_plane` | Create a reference plane parallel to a plane or to a flat face of the body, at a given distance. |
| `create_sketch` | Start a sketch on one of the model's planes (Front, Top, Right or a plane by name). |
| `create_sketch_on_face` | Start a sketch on the flat body face that contains a given 3D model point (found by geometry, so the current view does not matter). |
| `draw_arc` | Add an arc to the open sketch from its centre, radius and start / end angles (degrees), counter-clockwise from start to end. |
| `draw_centerline` | Add a centerline (a construction line) to the open sketch. |
| `draw_circle` | Add a circle to the open sketch, given its centre (x, y) and radius. |
| `draw_line` | Add a line to the open sketch at exactly the coordinates given. |
| `draw_point` | Add a sketch point at x, y in the open sketch. |
| `draw_polygon` | Add a regular polygon whose corners lie on a circle of the given radius; the first corner is at angle 0. |
| `draw_polyline` | Add a chain of lines through the points given, in one call. |
| `draw_rectangle` | Add a rectangle in the open sketch between opposite corners (x1, y1) and (x2, y2). |
| `draw_slot` | Add a straight slot to the open sketch. |
| `edit_sketch` | Reopen an existing sketch (for example 'Sketch1') for editing. |
| `fit_spline` | In the open sketch, replace a chain of lines and arcs with ONE spline that follows them within a tolerance (default 0.001 mm). |
| `fully_define_sketch` | Last resort, after your own dimensions: let SOLIDWORKS add what is still missing to define the open sketch (reported as added by SOLIDWORKS). |
| `get_sketch_status` | Report the open sketch's state: fully defined, under defined or over defined. |
| `list_sketch_entities` | List the open sketch's entities (ids, coordinates, sizes), dimensions, relation count and definition status (under_defined / fully_defined / over_defined). |

## Part features, materials and weldments (26)

| Tool | What it does |
|---|---|
| `chamfer_edges` | Add an angle-distance chamfer to the selected edges. |
| `circular_pattern` | Pattern one or more features around a model axis through the origin (x, y or z), or around the axis of a cylindrical face or circular edge (axis_point). |
| `cut_extrude` | Cut the most recent sketch through the body to remove material. |
| `delete_equation` | Delete an equation or global variable by name or index (see list_equations) and rebuild. |
| `delete_feature` | Delete a feature, sketch or plane by name; in an assembly also a mate by name. |
| `extrude_sketch` | Extrude the most recent sketch as a boss (Boss-Extrude). |
| `extrude_surface` | Extrude the most recent sketch into a surface (Surface-Extrude). |
| `fillet_edges` | Add a constant-radius fillet to the selected edges (or to all edges of the selected faces). |
| `get_color` | Read the colour (rgb 0 to 255) and transparency of the active part, or of components of the active assembly (all parts when none are named). |
| `get_cut_list` | Read the cut list of the active weldment part. |
| `hole_wizard` | Make a standard ISO Hole Wizard hole at a model point on a flat face. |
| `linear_pattern` | Pattern one or more features along the model axes, in one direction or two (a grid). |
| `list_dimensions` | List the model's dimensions by feature, with the names set_dimension uses. |
| `list_equations` | List the equations and global variables. |
| `list_features` | List the features of the active model as the feature tree shows them. |
| `list_materials` | List materials from the SolidWorks material databases. |
| `loft` | Loft a solid through two or more closed profile sketches on different planes, in the order given. |
| `mirror_feature` | Mirror one or more features about a plane (Front, Top, Right or any plane by name) or about a flat body face. |
| `revolve_sketch` | Revolve the most recent sketch about a centerline drawn in that sketch (draw_centerline). |
| `set_color` | Set the colour of the active part, or of components of the active assembly. |
| `set_dimension` | Change a dimension by name and rebuild the model. |
| `set_equation` | Set an equation and rebuild. |
| `set_material` | Assign a material to the active part by its name from list_materials. |
| `shell` | Hollow out the body, leaving walls of the given thickness. |
| `structural_member` | Make weldment structural members along the lines and arcs of a sketch, from the installed profile library. |
| `sweep` | Sweep a solid along a path sketch. |

## Sheet metal (12)

| Tool | What it does |
|---|---|
| `convert_to_sheet_metal` | Turn the solid body of a part into sheet metal with SolidWorks' Convert to Sheet Metal. |
| `edge_flange` | Add an edge flange (a wall bent up) on the sheet metal edge through a point, with its length measured to the outer virtual sharp. |
| `export_flat_pattern_dxf` | Export the active sheet metal part's flat pattern as a DXF. |
| `flatten` | Show the flat pattern of the active sheet metal part (unsuppress it). |
| `forming_tool` | Press a forming tool from the installed design library into a sheet metal part. |
| `get_sheet_metal_info` | Read the active part's sheet metal settings (read only). |
| `hem` | Fold a sheet metal edge back on itself (a hem), inside the old edge. |
| `miter_flange` | Add a miter flange. |
| `sheet_metal_base_flange` | Make the first feature of a sheet metal part from a sketch. |
| `sketched_bend` | Bend sheet metal along the single line of a sketch on its flat face, toward the sketch's side (reverse bends the other way). |
| `tab` | Add a tab to a sheet metal part. |
| `unflatten` | Fold the active sheet metal part again (suppress its flat pattern). |

## Assemblies (20)

| Tool | What it does |
|---|---|
| `add_component` | Insert a saved part or sub-assembly file into the active assembly, with the centre of its bounding box at x, y, z. |
| `add_mate` | Add a mate in the active assembly between two references. |
| `check_interference` | Run SolidWorks' interference check on the whole active assembly (parts inside sub-assemblies are checked one by one). |
| `create_exploded_view` | Make a new exploded view of the active assembly. |
| `delete_component` | Delete a top-level component from the active assembly. |
| `delete_mate` | Delete a mate by name (delete_feature also finds mates). |
| `edit_mate` | Change an existing mate. |
| `list_component_faces` | List the faces of one component of the active assembly. |
| `list_component_sketches` | List the sketches of one component of the active assembly. |
| `list_components` | List the top-level components of the active assembly. |
| `list_mates` | List every mate of the active assembly. |
| `move_component` | Move a floating top-level component to a position and orientation in the assembly. |
| `replace_component` | Replace a top-level component of the active assembly by another part or assembly file. |
| `set_component_fixed` | Fix a top-level component of the active assembly where it is, or release it (fixed = false). |
| `set_component_visibility` | Hide or show components of the active assembly (visible = false hides). |
| `set_subassembly_solving` | Make a sub-assembly component rigid or flexible in the active assembly. |
| `show_exploded` | Show the active assembly's exploded view exploded (exploded true) or collapsed (false). |
| `step_mate` | Step a distance or angle mate through a list of values, optionally saving a picture and counting interferences at each step. |
| `suppress_mate` | Suppress a mate. |
| `unsuppress_mate` | Turn a suppressed mate back on and rebuild. |

## Drawings (18)

| Tool | What it does |
|---|---|
| `activate_sheet` | Make another sheet of the active drawing the active one. |
| `add_balloons` | Add item balloons to a view of an assembly in the active drawing. |
| `add_bom_column` | Add a column to the drawing's bill of materials (add_bom_table) that shows a custom property of each item. |
| `add_bom_table` | Add a bill of materials table to a drawing of an assembly. |
| `add_center_marks` | Add centre marks to a drawing view. |
| `add_centerlines` | Add centrelines to a drawing view. |
| `add_detail_view` | Add a detail view to the active drawing. |
| `add_drawing_dimension` | Add a dimension to a drawing view between two of its edges or points, chosen by the ids that list_drawing_views(view=...) reports (E3 = an edge, P2 = a point). |
| `add_section_view` | Add a section view to the active drawing by cutting an existing view along a straight line. |
| `add_sheet` | Add a sheet to the active drawing, after the last one, and make it active. |
| `arrange_dimensions` | Spread the dimensions of a drawing view (or of every view) so they stop overlapping; use it after create_drawing(dimensions=true) or after adding dimensions. |
| `create_drawing` | Create a drawing of a saved part or assembly. |
| `delete_drawing_dimension` | Delete a dimension from a drawing view by its name. |
| `list_drawing_views` | List the views of the active drawing. |
| `move_drawing_view` | Move a drawing view so that its outline centre (see list_drawing_views) lands at x_sheet, y_sheet (mm from the lower left corner). |
| `set_dimension_precision` | Set how many decimals drawing dimensions show. |
| `set_dimension_style` | Change how a drawing dimension looks. |
| `set_title_block` | Put a title block in the bottom right corner of the active sheet, or update the one already there (title, drawn by, date, scale, material, sheet size). |

## Export (3)

| Tool | What it does |
|---|---|
| `export_pdf` | Export the active drawing as a PDF. |
| `export_step` | Export the active part or assembly as a STEP file (.step / .stp) for other CAD programs. |
| `export_stl` | Export the active part or assembly as an STL file (.stl) for 3D printing or meshing. |

## The guide (1)

Always on: the assistant reads recipes and pitfalls from it.

| Tool | What it does |
|---|---|
| `solidworks_guide` | Read how to operate SOLIDWORKS through these tools: recipes, pitfalls and limits. |

## What it cannot do

- Path, slider, lock-to-sketch and magnetic mates, and routing: SOLIDWORKS does not offer them to add-on programs.
- Drawing views at an oblique angle (save a named view in the model instead).
- Hole Wizard holes other than ISO; linear patterns along anything but the model axes.
- Weldments: trim / extend, end caps, gussets, rotating a profile or moving its pierce point. Forming tools only in
  their library sizes.
- A flat-pattern view or a cut-list table on a drawing (the assistant reads the cut list instead).
- Work while SOLIDWORKS shows a dialog: answer it in SOLIDWORKS, then try again.
