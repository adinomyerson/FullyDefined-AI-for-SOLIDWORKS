"""Equations and global variables (IEquationMgr): list, set, delete. A change that breaks the model is rolled back."""

import logging
import re

from ..core.api import Err, model, ok, sketch as sk, sw, tool, ToolError, value

logger = logging.getLogger(__name__)

# swLengthUnit_e -> label; equation values come back in the document's units
DOC_UNITS = {0: "mm", 1: "cm", 2: "m", 3: "inch", 4: "ft"}
EQUATION = re.compile(r'^\s*"([^"]+)"\s*=\s*(.*)$')


def _doc_unit(doc) -> str:
    try:
        return DOC_UNITS.get(int(list(value(doc, "GetUnits"))[0]), "unknown")
    except Exception:
        return "unknown"


def _split(text: str):
    """'"Wall" = 3mm 'note' -> ('Wall', '3mm', 'note'). The name is None for a line of another form."""
    m = EQUATION.match(text or "")
    if not m:
        return None, (text or "").strip(), ""
    rest, comment = m.group(2), ""
    if "'" in rest:
        rest, comment = rest.split("'", 1)
    return m.group(1), rest.strip(), comment.strip()


def _rows(mgr) -> list:
    rows = []
    for i in range(int(value(mgr, "GetCount"))):
        text = str(mgr.Equation(i))
        name, expression, comment = _split(text)
        row = {"index": i, "text": text, "name": name, "expression": expression, "comment": comment}
        try:
            row["value"] = round(float(mgr.Value(i)), 6)
        except Exception:
            row["value"] = None
        try:
            row["global"] = bool(mgr.GlobalVariable(i))
        except Exception:
            row["global"] = name is not None and "@" not in name
        rows.append(row)
    return rows


def _no_sketch(doc):
    if sk.open_sketch(doc) is not None:
        raise ToolError("A sketch is open. Close it first (close_sketch): the rebuild would close it.", Err.SKETCH)


def _find(rows, name=None, index=None):
    """The row for a name (ignoring case and quotes) or an index, or a refusal naming what exists."""
    if (name is None or str(name).strip() == "") == (index is None):
        raise ToolError("Give either name or index (see list_equations).", Err.INPUT)
    known = ", ".join(r["name"] or r["text"] for r in rows) or "none"
    if index is not None:
        try:
            i = int(index)
        except (TypeError, ValueError):
            raise ToolError(f"Bad index '{index}'", Err.INPUT)
        if not 0 <= i < len(rows) or i != index:
            raise ToolError(f"No equation {index}: there are {len(rows)} (0 to {len(rows) - 1}). Equations: {known}",
                            Err.INPUT)
        return rows[i]
    wanted = str(name).strip().strip('"').lower()
    for r in rows:
        if r["name"] is not None and r["name"].lower() == wanted:
            return r
    return None


def _error_keys(doc) -> set:
    return {(p["feature"], p["error_code"]) for p in model.rebuild_errors(doc)}


def _new_problems(doc, before: set) -> list:
    """Rebuild errors that were not there before the change."""
    return [p for p in model.rebuild_errors(doc) if (p["feature"], p["error_code"]) not in before]


def _apply(doc, mgr, index, new_text, old_text, dimension=None):
    """Put new_text at index (replacing old_text there, or adding at the end when old_text is None), rebuild,
    and roll back when SolidWorks refuses it or the rebuild breaks a feature that was whole before. A NEW
    equation on a dimension also puts the dimension's value back. Returns the index used."""
    before = _error_keys(doc)
    held = None
    if old_text is None and dimension is not None:
        try:
            held = float(dimension.SystemValue)
        except Exception:
            held = None
    if old_text is None:
        index = sw.equations.sw_add_equation(mgr, -1, new_text)
        taken = index >= 0
    else:
        taken = sw.equations.sw_set_equation(mgr, index, new_text)
    if not taken:
        if old_text is not None and str(mgr.Equation(index)) != old_text:
            if not sw.equations.sw_set_equation(mgr, index, old_text):
                raise ToolError(f"SolidWorks refused {new_text}, and equation {index} could not be put back to "
                                f"{old_text}: set it again with set_equation.", Err.FEATURE)
        raise ToolError(f"SolidWorks refused {new_text}: bad syntax, an unknown name, a name already used or a "
                        f"circular reference. Nothing was changed.", Err.INPUT)

    def undo() -> bool:
        if old_text is None:
            done = sw.equations.sw_delete_equation(mgr, index)
            if held is not None:
                done = sw.editing.sw_set_dimension_value(dimension, held) == 0 and done
        else:
            done = sw.equations.sw_set_equation(mgr, index, old_text)
        model.rebuild(doc)
        return done

    model.rebuild(doc)
    problems = _new_problems(doc, before)
    if problems:
        broken = ", ".join(p["feature"] for p in problems)
        if not undo():
            raise ToolError(f"{new_text} breaks {broken} on rebuild, and putting the model back failed: check "
                            f"list_equations and the dimension.", Err.FEATURE, {"rebuild_errors": problems})
        raise ToolError(f"Not changed: {new_text} breaks {broken} on rebuild; the equations were put back.",
                        Err.FEATURE, {"rebuild_errors": problems})
    return index


@tool(
    group="features",
    description="List the equations and global variables: index, text, name, expression, value (in the document's units) and comment.",
    needs="part_or_assembly",
)
def list_equations(ctx):
    doc = ctx.doc
    rows = _rows(sw.equations.sw_equation_manager(doc))
    variables = [{"name": r["name"], "value": r["value"]} for r in rows if r["global"]]
    data = {"count": len(rows), "equations": rows, "global_variables": variables, "unit": _doc_unit(doc)}
    return ok(f"{len(rows)} equations, {len(variables)} global variables", data)


@tool(
    group="features",
    description="Set an equation and rebuild. name is a global variable ('Wall'; created when new) or a dimension ('D1@Sketch1'); alternatively give index from list_equations. expression is the right-hand side, e.g. '3mm' or '\"Wall\" * 2'; a number without a unit is in the document's unit (20 is 20 inch in an inch document). A change that breaks the model is rolled back.",
    params={"name": {"type": "string", "description": "Global variable (e.g. 'Wall') or dimension (e.g. 'D1@Sketch1')"},
            "index": {"type": "integer", "description": "Index of an existing equation (instead of name)"},
            "expression": {"type": "string", "description": "Right-hand side, e.g. '3mm', '\"Wall\" * 2'"}},
    required=["expression"],
    needs="part_or_assembly",
)
def set_equation(ctx, expression, name=None, index=None):
    doc = ctx.doc
    _no_sketch(doc)
    expr = str(expression).strip()
    if not expr or "\n" in expr or "'" in expr:
        raise ToolError("expression must be one line with no ' (e.g. '3mm' or '\"Wall\" * 2')", Err.INPUT)
    mgr = sw.equations.sw_equation_manager(doc)
    rows = _rows(mgr)
    row = _find(rows, name, index)
    volume_before = model.volume_mm3(doc)
    if row is None:                                        # a new equation for this name
        target = str(name).strip().strip('"')
        if not target or '"' in target or "=" in target:
            raise ToolError(f"Bad name '{name}'", Err.INPUT)
        if "@" in target:
            try:
                found = doc.Parameter(target) is not None
            except Exception:
                found = False
            if not found:
                raise ToolError(f"No dimension named '{target}' in this model (see list_dimensions).", Err.INPUT)
        new_text, old_text, at = f'"{target}" = {expr}', None, -1
        own = target
        dimension = doc.Parameter(target) if "@" in target else None
    else:
        if row["name"] is None:
            raise ToolError(f"Equation {row['index']} ({row['text']}) is not of the form \"name\" = ...", Err.INPUT)
        comment = f" '{row['comment']}" if row["comment"] else ""
        new_text, old_text, at = f'"{row["name"]}" = {expr}{comment}', row["text"], row["index"]
        own = row["name"]
        dimension = None
    if f'"{own}"'.lower() in expr.lower():
        raise ToolError(f"{own} cannot refer to itself (circular reference).", Err.INPUT)
    used = _apply(doc, mgr, at, new_text, old_text, dimension)
    result = _rows(mgr)[used]
    data = {"equation": result, "added": old_text is None, "old_text": old_text, "unit": _doc_unit(doc)}
    volume_after = model.volume_mm3(doc)
    if volume_before is not None and volume_after is not None:
        data["volume_change_mm3"] = round(volume_after - volume_before, 3)
    verb = "Added" if old_text is None else "Changed"
    return ok(f"{verb} equation {used}: {result['text']} (value {result['value']} {data['unit']}), model rebuilt",
              data)


@tool(
    group="features",
    description="Delete an equation or global variable by name or index (see list_equations) and rebuild. A global variable that other equations use is refused.",
    params={"name": {"type": "string", "description": "Global variable or dimension name on the left of the equation"},
            "index": {"type": "integer", "description": "Index from list_equations (instead of name)"}},
    needs="part_or_assembly",
)
def delete_equation(ctx, name=None, index=None):
    doc = ctx.doc
    _no_sketch(doc)
    mgr = sw.equations.sw_equation_manager(doc)
    rows = _rows(mgr)
    row = _find(rows, name, index)
    if row is None:
        known = ", ".join(r["name"] or r["text"] for r in rows) or "none"
        raise ToolError(f"No equation for '{name}'. Equations: {known}", Err.INPUT)
    if row["global"] and row["name"]:
        quoted = f'"{row["name"]}"'.lower()
        users = [r["text"] for r in rows if r is not row and quoted in r["expression"].lower()]
        if users:
            raise ToolError(f"{row['name']} is used by: {'; '.join(users)}. Change or delete those first.",
                            Err.INPUT, {"used_by": users})
    volume_before = model.volume_mm3(doc)
    before = _error_keys(doc)
    if not sw.equations.sw_delete_equation(mgr, row["index"]):
        raise ToolError(f"SolidWorks could not delete equation {row['index']}", Err.FEATURE)
    model.rebuild(doc)
    problems = _new_problems(doc, before)
    if problems:
        broken = ", ".join(p["feature"] for p in problems)
        if sw.equations.sw_add_equation(mgr, row["index"], row["text"]) < 0:
            model.rebuild(doc)
            raise ToolError(f"Removing {row['text']} breaks {broken}, and SolidWorks could not put it back: it is "
                            f"deleted. Add it again with set_equation.", Err.FEATURE,
                            {"rebuild_errors": problems, "deleted": row["text"]})
        model.rebuild(doc)
        raise ToolError(f"Not deleted: removing {row['text']} breaks {broken}; it was put back.", Err.FEATURE,
                        {"rebuild_errors": problems})
    data = {"deleted": row["text"], "count": int(value(mgr, "GetCount"))}
    volume_after = model.volume_mm3(doc)
    if volume_before is not None and volume_after is not None:
        data["volume_change_mm3"] = round(volume_after - volume_before, 3)
    return ok(f"Deleted {row['text']}; {data['count']} equations left, model rebuilt", data)
