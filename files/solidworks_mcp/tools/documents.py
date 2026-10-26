"""New, open, save and close documents; what is open."""

import logging
import os
import shutil
import tempfile

from ..core.api import (
    doc_path, doc_title, doc_type, DOC_TYPES, document, Err, ok, open_documents,
    files, same_path, sw, tool, ToolError, value)

logger = logging.getLogger(__name__)

_OPEN_TYPES = document.OPEN_TYPES
_SILENT, _SAVE_REFERENCED = document.SILENT, document.SAVE_REFERENCED
template = document.template                # shared with create_drawing (core/document.py)
save_in_place = document.save_in_place


def _new_document(ctx, kind: str, label: str):
    path = template(ctx.app, kind, ctx.setting("exe_path"))
    doc = sw.documents.sw_new_document(ctx.app, path)
    if doc is None:
        raise ToolError(f"Failed to create {label}", Err.LOAD)
    title = doc_title(doc)
    info = document.template_info(doc, path)
    return ok(f"Created {kind}: {title} (template {info['file']}: {info['unit_system']}, {info['length_unit']}, "
              f"{info['standard']})", {"name": title, "type": kind.capitalize(), "template": info})


@tool(group="core", description="Create an empty part from the default template and make it the active document; the result names the template's units and standard.")
def create_new_part(ctx):
    return _new_document(ctx, "part", "part document")


@tool(group="core", description="Create an empty assembly from the default template and make it the active document; the result names the template's units and standard.")
def create_new_assembly(ctx):
    return _new_document(ctx, "assembly", "assembly")


@tool(
    group="core",
    description="Open a SolidWorks file and make it the active document. A file that is already loaded (in the background, or only as a component of an open assembly) is brought to the front instead. The result names the active document; it is an error if the file could not be made active.",
    params={"filepath": {"type": "string", "description": "Full path of the file to open"}},
    required=["filepath"],
    timeout=300,                    # a big assembly can take minutes to open
)
def open_document(ctx, filepath):
    if not isinstance(filepath, str) or not os.path.exists(filepath):
        raise ToolError(f"File not found: {filepath}", Err.NOT_FOUND)
    app = ctx.app
    path = os.path.abspath(filepath)
    try:
        already_open = app.GetOpenDocumentByName(path) is not None
    except Exception:
        already_open = False

    kind = _OPEN_TYPES.get(os.path.splitext(path)[1].lower(), 1)
    doc, error_code = sw.documents.sw_open(app, path, kind)
    if doc is None or error_code != 0:
        raise ToolError(f"Failed to open (error {error_code})", Err.LOAD)
    title = doc_title(doc)

    # A file that was already loaded (in the background, or as a component) stays
    # behind the active document: bring it to the front by path, then by title (a
    # part and an assembly can share a title), and check.
    def active_path():
        active = app.ActiveDoc
        return doc_path(active) if active is not None else ""

    for target in (path, title):
        if same_path(active_path(), path):
            break
        sw.documents.sw_activate(app, target)
    if not same_path(active_path(), path):
        now = active_path() or "another document"
        raise ToolError(f"{title} is loaded but could not be made the active document "
                        f"(the active document is still {os.path.basename(now)}). Nothing was changed.",
                        Err.LOAD, {"name": title, "path": path, "active_document": now})

    message = f"Opened: {title}" + (" (it was already loaded; now the active document)" if already_open else "")
    return ok(message, {"name": title, "path": path, "active_document": title, "was_already_open": already_open})


def _unsaved_references(app, doc) -> list:
    """Files a drawing or assembly references (at any level) that are open with unsaved
    changes. Saving the drawing or assembly writes these too."""
    if doc_type(doc) not in (2, 3):
        return []
    deps = sw.documents.sw_dependencies(doc)
    out = []
    for path in deps[1::2]:                                     # [name, path, name, path, ...]
        path = str(path or "")
        if not path or path.lower() in (p.lower() for p in out):
            continue
        try:
            ref = app.GetOpenDocumentByName(path)
            if ref is not None and bool(value(ref, "GetSaveFlag")):
                out.append(path)
        except Exception as e:
            logger.debug(f"Save flag of {path}: {e}")
    return out


@tool(
    group="core",
    description="Save the current document. With filepath it is saved under that name and the extension chooses the format: .SLDPRT / .SLDASM / .SLDDRW keep the document, while .pdf, .step, .stl, .dxf and the like export a copy; without filepath it saves in place. Saving a drawing or assembly that has open referenced files with unsaved changes is refused (naming them) unless save_referenced is true, and a Save As onto an existing file (other than the document's own) is refused unless overwrite is true. A refusal saves nothing.",
    params={
        "filepath": {"type": "string", "description": "Full path to save under; leave out to save in place"},
        "save_referenced": {"type": "boolean", "default": False,
                            "description": "Drawing or assembly: also write the referenced files that have unsaved changes (they are listed in the result)"},
        "overwrite": {"type": "boolean", "default": False,
                      "description": "Save As: replace a file that already exists at filepath"},
    },
    needs="any",
    writes=("filepath",),
)
def save_document(ctx, filepath=None, save_referenced=False, overwrite=False):
    if save_referenced is None:
        save_referenced = False
    if not isinstance(save_referenced, bool):
        raise ToolError("save_referenced must be true or false", Err.INPUT)
    if overwrite is None:
        overwrite = False
    if not isinstance(overwrite, bool):
        raise ToolError("overwrite must be true or false", Err.INPUT)
    doc = ctx.doc
    own = doc_path(doc)
    if filepath:
        target = os.path.abspath(filepath)
        if os.path.exists(target) and not (own and same_path(target, own)) and not overwrite:
            raise ToolError(f"Nothing was saved: {target} already exists. Choose another name, or call "
                            f"save_document again with overwrite: true to replace it.", Err.SAVE,
                            {"path": target, "exists": True})
    elif own:
        files.check_writable(own, "save")
    unsaved = _unsaved_references(ctx.app, doc)
    if unsaved and not save_referenced:
        kind = "drawing" if doc_type(doc) == 3 else "assembly"
        raise ToolError(f"Nothing was saved: saving this {kind} would also write {len(unsaved)} referenced "
                        f"file(s) that have unsaved changes: {', '.join(unsaved)}. Save or close them first, "
                        f"or call save_document again with save_referenced: true to write them too.",
                        Err.SAVE, {"would_also_write": unsaved})
    if unsaved:                                           # save_referenced writes these too
        for p in unsaved:
            files.check_writable(p, "save a referenced file")
    stamps = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in unsaved}

    if filepath:
        path = os.path.abspath(filepath)
        folder = os.path.dirname(path)
        if folder and not os.path.isdir(folder):
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError as e:
                raise ToolError(f"Cannot create the folder {folder} ({e.strerror or e})", Err.SAVE)
        method = sw.documents.sw_save_as(doc, path)
        if method is None:
            raise ToolError(f"SolidWorks would not save {path} (SaveAs, Extension.SaveAs and Extension.SaveAs2 "
                            f"all refused)", Err.SAVE)
        data, message = {"path": path, "method": method}, f"Saved: {path} [{method}]"
    else:
        path = doc_path(doc)
        if not path:
            raise ToolError("This document has never been saved. Give a filepath, "
                            "e.g. C:\\Work\\my_part.SLDPRT", Err.SAVE)
        saved, errors, warnings = save_in_place(doc, _SILENT | _SAVE_REFERENCED if unsaved else _SILENT)
        if not saved:
            raise ToolError(f"Save failed (error code {errors}, warning code {warnings})", Err.SAVE)
        data, message = {"path": path}, f"Saved: {path}"

    if unsaved:
        data["also_written"] = [p for p, s in stamps.items() if os.path.exists(p) and os.path.getmtime(p) != s]
        message += f". Also wrote (save_referenced): {', '.join(data['also_written']) or 'none'}"
    return ok(message, data)


@tool(
    group="core",
    description="Close the current document. save = true saves it first, but a document that was never saved is refused then (save_document first, or close with save = false).",
    params={"save": {"type": "boolean", "default": False, "description": "Save the document first"}},
)
def close_document(ctx, save=False):
    doc = ctx.active_doc()
    if doc is None:
        return ok("No document to close")
    title, path = doc_title(doc), doc_path(doc)
    if save and path:
        files.check_writable(path, "save")
    if save:
        if not path:
            raise ToolError(f"{title} has never been saved, so it was not closed. Save it "
                            f"with save_document(filepath=...) first, or close with save=false.", Err.SAVE)
        saved, errors, _ = save_in_place(doc)
        if not saved:
            raise ToolError(f"Save failed (error code {errors}), so {title} was not closed", Err.SAVE)
    # by path when there is one: a part and an assembly saved under one name share a title
    sw.documents.sw_close(ctx.app, path or title)
    return ok(f"Closed: {title}", {"document": title})


@tool(group="core", description="Report the active document's title, file path ('Not saved' when it has none) and type.", needs="any")
def get_document_info(ctx):
    doc = ctx.doc
    kind = doc_type(doc)
    info = {"title": doc_title(doc), "path": doc_path(doc) or "Not saved",
            "type": DOC_TYPES.get(kind, "None" if kind == 0 else "Unknown"), "type_code": kind}
    return ok(f"{info['title']} ({info['type']})", info)


@tool(
    group="core",
    description="Copy a file to a new path, keeping the extension. Refused when the target exists (unless overwrite), when the source is open with unsaved changes, when the target is open, and for protected folders. Files that reference the source keep pointing at the source.",
    params={"source": {"type": "string", "description": "Full path of the file to copy"},
            "target": {"type": "string", "description": "Full path of the copy (same extension; a missing folder is created)"},
            "overwrite": {"type": "boolean", "default": False, "description": "Replace an existing target"}},
    required=["source", "target"],
    writes=("target",),
)
def copy_file(ctx, source, target, overwrite=False):
    if not isinstance(overwrite, bool):
        raise ToolError("overwrite must be true or false", Err.INPUT)
    for label, value_ in (("source", source), ("target", target)):
        if not isinstance(value_, str) or not value_.strip():
            raise ToolError(f"{label} must be a full file path", Err.INPUT)
    src, dst = os.path.abspath(source.strip()), os.path.abspath(target.strip())
    root = files.protected_root(src)
    if root is not None:
        raise ToolError(f"Nothing was copied: {src} is inside the protected folder {root} (setting protected_paths).",
                        Err.SAVE, {"path": src, "protected_by": root})
    files.check_writable(dst, "copy")
    if not os.path.isfile(src):
        raise ToolError(f"No file at {src}", Err.INPUT)
    if os.path.isdir(dst):
        raise ToolError(f"target is a folder: give the full path of the copy, e.g. {os.path.join(dst, os.path.basename(src))}",
                        Err.INPUT)
    if os.path.splitext(src)[1].lower() != os.path.splitext(dst)[1].lower():
        raise ToolError(f"The copy must keep the extension {os.path.splitext(src)[1] or '(none)'}: a different "
                        f"extension does not convert the file (use export_step / export_pdf for that)", Err.INPUT)
    if same_path(src, dst):
        raise ToolError("source and target are the same file", Err.INPUT)
    exists = os.path.exists(dst)
    if exists and not overwrite:
        raise ToolError(f"Nothing was copied: {dst} already exists. Choose another name, or call copy_file again "
                        f"with overwrite: true to replace it.", Err.SAVE, {"path": dst})
    for doc in open_documents(ctx.app):
        path = doc_path(doc)
        if same_path(path, src) and bool(value(doc, "GetSaveFlag")):
            raise ToolError(f"{doc_title(doc)} is open with unsaved changes: the copy would miss them. Save it "
                            f"first (save_document), or close it without saving.", Err.SAVE)
        if same_path(path, dst):
            raise ToolError(f"The target {dst} is open in SolidWorks ({doc_title(doc)}): close it first.", Err.SAVE)
    folder = os.path.dirname(dst)
    try:
        os.makedirs(folder, exist_ok=True)
        handle, partial = tempfile.mkstemp(prefix="~copy_", suffix=os.path.splitext(dst)[1], dir=folder)
        os.close(handle)
        try:
            shutil.copy2(src, partial)
            os.replace(partial, dst)
        finally:
            if os.path.exists(partial):
                os.remove(partial)
    except OSError as e:
        raise ToolError(f"Could not copy to {dst} ({e.strerror or e})", Err.SAVE)
    size = os.path.getsize(dst)
    data = {"source": src, "path": dst, "bytes": size, "replaced": exists}
    return ok(f"Copied {os.path.basename(src)} to {dst} ({size} bytes)" + (", replacing the old file" if exists else ""),
              data)


@tool(group="core", description="List every document that is open in SolidWorks: title, type, path (empty when never saved) and unsaved_changes (true when it was never saved or has changed since its last save; the result names those).")
def list_open_documents(ctx):
    docs = []
    for doc in open_documents(ctx.app):
        try:
            path = doc_path(doc)
            changed = bool(value(doc, "GetSaveFlag"))
            docs.append({"title": doc_title(doc), "type": DOC_TYPES.get(doc_type(doc), "Unknown"), "path": path,
                         "unsaved_changes": changed or not path})
        except Exception as e:
            logger.debug(f"Skipping a document: {e}")
            docs.append({"title": "<unreadable>", "type": "Unknown"})
    unsaved = [f"{d['title']} ({'never saved' if not d['path'] else 'changed since saved'})"
               for d in docs if d.get("unsaved_changes")]
    note = f"; unsaved changes: {', '.join(unsaved)}" if unsaved else ("; all saved" if docs else "")
    return ok(f"{len(docs)} document(s) open{note}", {"documents": docs})


# ---------------------------------------------------------------- custom properties

def _property_target(doc, configuration):
    """(property manager, configuration name or "", how it was chosen), or a refusal."""
    if configuration is None:
        return sw.documents.sw_property_manager(doc, ""), "", "the file's own properties (default)"
    if doc_type(doc) == 3:
        raise ToolError("A drawing has no configurations: leave configuration out", Err.INPUT)
    names = [str(n) for n in (value(doc, "GetConfigurationNames") or [])]
    chosen = next((n for n in names if n.lower() == str(configuration).strip().lower()), None)
    if chosen is None:
        raise ToolError(f"No configuration '{configuration}'. Configurations: {', '.join(names) or 'none'}", Err.INPUT)
    return sw.documents.sw_property_manager(doc, chosen), chosen, "given"


def _property_rows(manager) -> list:
    return [{"name": n, "value": v, "resolved": r, "type": sw.documents.PROPERTY_TYPES.get(k, str(k))}
            for n, v, r, k in sw.documents.sw_properties(manager)]


@tool(
    group="core",
    description="Set or delete custom properties of the active document (part, assembly or drawing): properties gives name -> text (a number is written as its text; the result says so), delete lists names to remove. They go on the file itself unless configuration names one of its configurations. A value can link to another property, e.g. '\"SW-Mass@Bracket.SLDPRT\"'. Every property touched is read back: value as typed and resolved value. Nothing changes if any input is refused.",
    params={"properties": {"type": "object", "additionalProperties": {"type": ["string", "number"]},
                           "description": "Name -> value to set, e.g. {\"PartNo\": \"BR-104\", \"Description\": \"Bracket\"}"},
            "delete": {"type": "array", "items": {"type": "string"}, "description": "Names of properties to remove"},
            "configuration": {"type": "string", "description": "A configuration of the part or assembly (default: the file's own properties)"}},
    needs="any",
)
def set_custom_properties(ctx, properties=None, delete=None, configuration=None):
    doc = ctx.doc
    if not properties and not delete:
        raise ToolError("Give properties to set, names to delete, or both", Err.INPUT)
    if properties is not None and not isinstance(properties, dict):
        raise ToolError("properties must be an object of name -> text", Err.INPUT)
    if delete is not None and not isinstance(delete, list):
        raise ToolError("delete must be a list of names", Err.INPUT)
    wanted, as_text = {}, []
    for name, text in (properties or {}).items():
        if not str(name).strip():
            raise ToolError("A property name is empty", Err.INPUT)
        if isinstance(text, bool) or not isinstance(text, (str, int, float)):
            raise ToolError(f"The value of {name} must be text or a number", Err.INPUT)
        if not isinstance(text, str):
            as_text.append(str(name).strip())
        if str(name).strip().lower() in {n.lower() for n in wanted}:
            raise ToolError(f"{str(name).strip()} is given twice (property names ignore case)", Err.INPUT)
        wanted[str(name).strip()] = text if isinstance(text, str) else str(text)
    gone = [str(n).strip() for n in (delete or [])]
    if len({n.lower() for n in gone}) != len(gone):
        raise ToolError("A name to delete is given twice (property names ignore case)", Err.INPUT)
    if any(not n for n in gone):
        raise ToolError("A name to delete is empty", Err.INPUT)
    both = sorted({n.lower() for n in wanted} & {n.lower() for n in gone})
    if both:
        raise ToolError(f"Set and delete in one call: {', '.join(both)}", Err.INPUT)
    manager, chosen, chosen_from = _property_target(doc, configuration)
    existing = {n.lower(): (n, k) for n, _, _, k in sw.documents.sw_properties(manager)}
    missing = [n for n in gone if n.lower() not in existing]
    if missing:
        raise ToolError(f"No property {', '.join(missing)} to delete. Properties: "
                        f"{', '.join(n for n, _ in existing.values()) or 'none'}", Err.INPUT)
    other_type = [existing[n.lower()][0] for n in wanted
                  if n.lower() in existing and existing[n.lower()][1] not in (0, sw.documents.PROPERTY_TEXT)]
    if other_type:
        raise ToolError(f"{', '.join(other_type)} hold a value of another type than text: delete them first",
                        Err.INPUT)

    snapshot = {n.lower(): (n, typed, kind) for n, typed, _, kind in sw.documents.sw_properties(manager)}
    failed = []
    for name, text in wanted.items():
        code = sw.documents.sw_set_property(manager, name, text)
        if code:
            failed.append(f"{name}: {sw.documents.ADD_RESULTS.get(code, code)}")
    for name in gone:
        code = sw.documents.sw_delete_property(manager, existing[name.lower()][0])
        if code:
            failed.append(f"{name}: {sw.documents.DELETE_RESULTS.get(code, code)}")
    rows = _property_rows(manager)
    now = {r["name"].lower(): r for r in rows}
    failed += [f"{n}: reads back '{now[n.lower()]['value']}'" if n.lower() in now else f"{n}: missing after setting"
               for n, text in wanted.items() if now.get(n.lower(), {}).get("value") != text]
    failed += [f"{n}: still there after deleting" for n in gone if n.lower() in now]
    if failed:
        # put every touched property back as it was: nothing of a refused call stays
        for key in [n.lower() for n in wanted] + [n.lower() for n in gone]:
            if key in snapshot:
                old_name, typed, kind = snapshot[key]
                sw.documents.sw_set_property(manager, old_name, typed, kind)
            elif key in now:
                sw.documents.sw_delete_property(manager, now[key]["name"])
        restored = {n.lower(): (n, typed, kind) for n, typed, _, kind in sw.documents.sw_properties(manager)} == snapshot
        raise ToolError(f"SolidWorks did not take: {'; '.join(failed)}. "
                        + ("Every property was put back as it was" if restored
                           else "WARNING: the properties could not all be put back - check list_custom_properties"),
                        Err.FEATURE)
    data = {"configuration": chosen, "configuration_from": chosen_from,
            "set": [now[n.lower()] for n in wanted], "deleted": gone, "count": len(rows)}
    if as_text:
        data["written_as_text"] = as_text
    where = f"configuration {chosen}" if chosen else "the file"
    parts = [f"{r['name']} = '{r.get('value')}'" + (f" (resolves to '{r['resolved']}')"
             if r.get("resolved") != r.get("value") else "") for r in data["set"]]
    message = f"On {where}: " + "; ".join(parts + ([f"deleted {', '.join(data['deleted'])}"] if gone else []))
    if as_text:
        message += f". Written as text: {', '.join(as_text)}"
    return ok(message, data)


@tool(
    group="core",
    description="List the custom properties of the active document: name, value as typed, resolved value (a linked value such as '\"SW-Mass@Bracket.SLDPRT\"' shows its result) and type. The file's own properties unless configuration names one of its configurations. Read-only.",
    params={"configuration": {"type": "string", "description": "A configuration of the part or assembly (default: the file's own properties)"}},
    needs="any",
)
def list_custom_properties(ctx, configuration=None):
    manager, chosen, chosen_from = _property_target(ctx.doc, configuration)
    rows = _property_rows(manager)
    where = f"configuration {chosen}" if chosen else "the file"
    listed = "; ".join(f"{r['name']} = '{r['resolved']}'" for r in rows)
    return ok(f"{len(rows)} custom properties on {where}" + (f": {listed}" if rows else ""),
              {"configuration": chosen, "configuration_from": chosen_from, "properties": rows, "count": len(rows)})
