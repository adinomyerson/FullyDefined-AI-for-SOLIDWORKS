"""Read-only measurements of a part."""

import logging

from ..core.api import Err, geometry, ok, tool, ToolError, units, value

logger = logging.getLogger(__name__)


@tool(
    group="core",
    description="Measure the part: bounding box (min and max corners along the model axes), overall size, volume, surface area, mass, density, centre of mass and number of solid bodies. Read-only.",
    params={'unit': {'type': 'string', 'description': 'Unit for lengths (mm, cm, m, inch or ft)'}},
    needs='part',
)
def measure_part(ctx, unit=None):
    doc = ctx.doc
    unit_str = units.label(unit)
    scale = ctx.from_m(1.0, unit)                      # metres -> the user's unit
    bodies = geometry.solid_bodies(doc)
    if not bodies:
        raise ToolError("The model has no solid body to measure", Err.NO_DOCUMENT)

    lo, hi = [float("inf")] * 3, [float("-inf")] * 3   # bounding box: union of the body boxes
    for body in bodies:
        box = [float(v) for v in value(body, "GetBodyBox")]
        for i in range(3):
            lo[i] = min(lo[i], box[i], box[i + 3])
            hi[i] = max(hi[i], box[i], box[i + 3])

    def r(v, digits=3):
        return round(v, digits) + 0.0

    data = {"unit": unit_str,
            "bounding_box": {"min": [r(v * scale) for v in lo], "max": [r(v * scale) for v in hi]},
            "size": {"x": r((hi[0] - lo[0]) * scale), "y": r((hi[1] - lo[1]) * scale), "z": r((hi[2] - lo[2]) * scale)},
            "solid_bodies": len(bodies)}

    try:
        props = doc.Extension.CreateMassProperty
        # Read order matters: if Volume is read before Density / Mass, SolidWorks 2026 reports a
        # density of 1 and a mass equal to the volume. Read density and mass first, then cross-check.
        density = float(props.Density)
        mass_kg = float(props.Mass)
        volume_m3 = float(props.Volume)
        if volume_m3 > 0 and abs(mass_kg - density * volume_m3) > 1e-6 * max(mass_kg, 1e-12):
            logger.warning("Mass properties inconsistent; using density x volume")
            mass_kg = density * volume_m3
        data["volume"] = r(volume_m3 * scale ** 3)
        data["volume_unit"] = f"{unit_str}^3"
        data["surface_area"] = r(float(props.SurfaceArea) * scale ** 2)
        data["surface_area_unit"] = f"{unit_str}^2"
        data["mass_g"] = r(mass_kg * 1000.0)
        data["mass_kg"] = r(mass_kg, 6)
        data["density_kg_m3"] = r(density, 2)
        data["center_of_mass"] = [r(float(v) * scale) for v in props.CenterOfMass]
    except Exception as e:
        logger.warning(f"Mass properties failed: {e}")
        data["mass_properties_error"] = str(e)

    try:                                               # the material name as shown in the feature tree
        feat, guard = value(doc, "FirstFeature"), 0
        while feat is not None and guard < 60:
            guard += 1
            if value(feat, "GetTypeName2") == "MaterialFolder":
                name = feat.Name
                data["material"] = None if "not specified" in name.lower() else name
                break
            feat = value(feat, "GetNextFeature")
    except Exception:
        pass

    size = data["size"]
    message = f"Size {size['x']} x {size['y']} x {size['z']} {unit_str}"
    if "mass_g" in data:
        message += f", volume {data['volume']} {unit_str}^3, mass {data['mass_g']} g"
        if data.get("material") is None:
            message += (f" (no material assigned: SolidWorks' default density "
                        f"{data['density_kg_m3']} kg/m^3 was used)")
        else:
            message += f" ({data['material']})"
    return ok(message, data)
