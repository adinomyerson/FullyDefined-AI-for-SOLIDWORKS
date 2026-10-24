"""The operator know-how, one section or recipe at a time (core/knowhow.py reads know_how.md)."""

from ..core.api import Err, groups, knowhow, ok, tool, ToolError


def _off(name: str) -> ToolError:
    return ToolError(f"{name} is switched off by the setting \"know_how\": false.", Err.INPUT,
                     {"group": "guide", "setting": "know_how"})


# its own group: always on for users (the groups setting cannot switch it off); only "know_how": false does
groups.add_group("guide", off_by_default=False, switch=knowhow.enabled, refusal=_off,
                 report=lambda: "know-how on (summary and solidworks_guide)" if knowhow.enabled()
                 else "know-how off (setting know_how)")


@tool(
    group="guide",
    description="Read how to operate SolidWorks through these tools: recipes (the order of steps and the tools for a plate with holes, a revolved part, sheet metal and its flat DXF, an assembly with mates, a finished drawing, looking inside an assembly, equations, a weldment frame), verifying by measurement, units and coordinates, pitfalls and limits, and what to do when a call fails. Call it before a multi-step build, or when unsure how to do something. Without topic it lists the topics. Every answer starts with the short rules (In short). Read-only.",
    params={"topic": {"type": "string",
                      "description": "A topic from the list: a section name (e.g. 'pitfalls'), a recipe letter (e.g. 'D') or a recipe's title words (e.g. 'sheet metal')"}},
)
def solidworks_guide(ctx, topic=None):
    rows = knowhow.topics()
    if not rows:
        raise ToolError("The know-how file is missing from the installation; reinstall the server.", Err.INTERNAL)
    listing = [{"topic": r["key"], "title": r["title"]} for r in rows]
    # every answer starts with the "In short" rules: some apps do not pass the server's instructions to the model
    short = next((r for r in rows if r["title"] == knowhow.SUMMARY), None)
    head = (short["text"] + "\n\n") if short else ""
    if topic is None or not str(topic).strip():
        lines = [f"- {r['key']}: {r['title']}" for r in rows]
        return ok(head + "Topics (call solidworks_guide with one):\n" + "\n".join(lines), {"topics": listing})
    found = knowhow.find(str(topic))
    several = knowhow.matches(str(topic)) if found is None else []
    if len(several) > 1:
        raise ToolError(f"'{topic}' fits more than one topic: "
                        f"{', '.join(r['key'] + ' (' + r['title'] + ')' for r in several)}. Call again with one.",
                        Err.INPUT, {"topics": [{"topic": r["key"], "title": r["title"]} for r in several]})
    if found is None:
        raise ToolError(f"No topic '{topic}'. Topics: {', '.join(r['key'] for r in rows)}", Err.INPUT,
                        {"topics": listing})
    text = found["text"] if short is None or found["key"] == short["key"] else head + found["text"]
    return ok(text, {"topic": found["key"], "title": found["title"]})
