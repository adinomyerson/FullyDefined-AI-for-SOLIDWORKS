"""Materials: browse the SolidWorks material databases and assign one to a part."""

import glob
import logging
import os
import re

from ..core.api import connection, Err, install, ok, sw, tool, ToolError, value

logger = logging.getLogger(__name__)

_cache = None           # every material of every database, read once

_TOKEN = re.compile(r'<classification name="([^"]*)"|<material name="([^"]*)"|<DENS\b[^>]*\bvalue="([^"]*)"')


def _database_files(exe_path: str) -> list:
    """The .sldmat files SolidWorks ships with, the main database first."""
    roots = []
    if connection.alive():
        try:
            exe = value(connection.app, "GetExecutablePath")
            roots.append(exe if os.path.isdir(exe) else os.path.dirname(exe))
        except Exception:
            pass
    exe = install.find_exe(exe_path)
    if exe:
        roots.append(os.path.dirname(exe))
    roots.append(r"C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS")
    files = []
    for root in roots:
        for path in glob.glob(os.path.join(root, "lang", "*", "sldmaterials", "*.sldmat")):
            if path.lower() not in (f.lower() for f in files):
                files.append(path)
    files.sort(key=lambda p: (0 if os.path.basename(p).lower() == "solidworks materials.sldmat" else 1,
                              os.path.basename(p).lower()))
    return files


def _load(exe_path: str = "auto") -> list:
    """Every material: name, category, density, database."""
    global _cache
    if _cache is not None:
        return _cache
    rows = []
    for path in _database_files(exe_path):
        try:
            with open(path, "rb") as f:
                raw = f.read()
            text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "replace")
        except Exception as e:
            logger.debug(f"Could not read {path}: {e}")
            continue
        database = os.path.splitext(os.path.basename(path))[0]
        category, current = "", None
        for m in _TOKEN.finditer(text):
            if m.group(1) is not None:
                category = m.group(1)
            elif m.group(2) is not None:
                current = {"name": m.group(2), "category": category, "database": database, "database_path": path}
                rows.append(current)
            elif current is not None and "density_kg_m3" not in current:
                try:
                    current["density_kg_m3"] = round(float(m.group(3)), 2)
                except ValueError:
                    pass
    _cache = rows
    return rows


def _current_material(doc):
    """The material name shown in the feature tree, or None when not specified."""
    try:
        feat, guard = value(doc, "FirstFeature"), 0
        while feat is not None and guard < 60:
            guard += 1
            if value(feat, "GetTypeName2") == "MaterialFolder":
                return None if "not specified" in feat.Name.lower() else feat.Name
            feat = value(feat, "GetNextFeature")
    except Exception:
        pass
    return None


@tool(
    group="features",
    description="List materials from the SolidWorks material databases. With no input it returns the categories; give search (part of a name, e.g. '6061', 'ABS') or category (e.g. 'Steel', 'Plastics') to get material names and densities.",
    params={
        "search": {"type": "string", "description": "Only materials whose name contains this text"},
        "category": {"type": "string", "description": "Only this category, e.g. 'Aluminium Alloys'"},
    },
)
def list_materials(ctx, search=None, category=None):
    rows = _load(ctx.setting("exe_path"))
    if not rows:
        raise ToolError("No SolidWorks material database was found", Err.INTERNAL)
    categories = {}
    for row in rows:
        categories[row["category"]] = categories.get(row["category"], 0) + 1

    found = rows
    if category:
        wanted = str(category).strip().lower()
        found = [r for r in found if r["category"].lower() == wanted]
        if not found:
            raise ToolError(f"No category '{category}'. Categories: {', '.join(sorted(categories))}", Err.INPUT)
    if search:
        needle = str(search).strip().lower()
        found = [r for r in found if needle in r["name"].lower()]

    data = {"count": len(found)}
    if not search and not category:
        # the full list is long: give the categories and let the caller narrow down
        data["categories"] = [{"category": k, "materials": v} for k, v in sorted(categories.items())]
        return ok(f"{len(rows)} materials in {len(categories)} categories. "
                  f"Give search or category to list materials.", data)

    limit = 80
    data["materials"] = [{k: r[k] for k in ("name", "category", "density_kg_m3", "database") if k in r}
                         for r in found[:limit]]
    if len(found) > limit:
        data["truncated"] = True
    return ok(f"{len(found)} materials" + (f" (first {limit} shown)" if len(found) > limit else ""), data)


@tool(
    group="features",
    description="Assign a material to the active part by its exact name from list_materials (e.g. '6061 Alloy', 'AISI 304', 'ABS'). Returns the resulting density and mass.",
    params={"name": {"type": "string", "description": "Material name exactly as listed"}},
    required=["name"],
    needs="part",
    clears_selection=True,
)
def set_material(ctx, name):
    wanted = str(name or "").strip()
    if not wanted:
        raise ToolError("Give a material name (see list_materials)", Err.INPUT)
    rows = _load(ctx.setting("exe_path"))
    exact = [r for r in rows if r["name"].lower() == wanted.lower()]
    if not exact:
        close = [r["name"] for r in rows if wanted.lower() in r["name"].lower()][:12]
        hint = f" Did you mean: {', '.join(close)}?" if close else " Use list_materials to search."
        raise ToolError(f"No material named '{wanted}'.{hint}", Err.INPUT, {"suggestions": close})

    row, doc = exact[0], ctx.doc
    before = _current_material(doc)
    sw.materials.sw_set_material(doc, row["database_path"], row["name"])
    after = _current_material(doc)
    if (after or "").lower() != row["name"].lower():
        raise ToolError(f"SolidWorks did not apply '{row['name']}' (the part still shows "
                        f"{after or 'no material'})", Err.FEATURE)

    data = {"material": row["name"], "category": row["category"], "database": row["database"],
            "previous_material": before}
    message = f"Material set to {row['name']} ({row['category']})"
    try:
        props = doc.Extension.CreateMassProperty       # read density and mass before volume
        data["density_kg_m3"] = round(float(props.Density), 2)
        data["mass_g"] = round(float(props.Mass) * 1000.0, 3)
        message += f": density {data['density_kg_m3']} kg/m^3, mass {data['mass_g']} g"
    except Exception as e:
        logger.debug(f"Mass after the material change failed: {e}")
    return ok(message, data)
