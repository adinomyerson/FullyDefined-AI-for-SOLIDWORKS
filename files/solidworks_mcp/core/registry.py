"""
The tool list. A tool declares itself with @tool next to its code:

    @tool(description="...", params={...}, required=[...], needs="part", clears_selection=True)
    def shell(ctx, thickness, faces=None, unit=None):
        ...
        return ok("...", data={...})

The function name is the tool name. Arguments arrive as keywords.
"""

import inspect
from dataclasses import dataclass, field
from typing import Callable, Dict, List

# What a tool needs before it runs (the runner checks it):
#   none      nothing; ctx.app connects if the tool uses it
#   any       an open document of any type
#   part / assembly / drawing / part_or_assembly   an open document of that type
NEEDS = ("none", "any", "part", "assembly", "drawing", "part_or_assembly")


@dataclass
class ToolDef:
    name: str
    description: str
    schema: dict
    func: Callable
    needs: str = "none"
    clears_selection: bool = False
    changes_tool_list: bool = False
    timeout: float = None                           # seconds; None = the call_timeout_s setting
    accepted: set = field(default_factory=set)      # argument names the function takes
    required_args: List[str] = field(default_factory=list)
    writes: tuple = ()                              # inputs that are paths the tool writes (core/files.py)
    group: str = "core"                             # core/groups.py


TOOLS: Dict[str, ToolDef] = {}
LOAD_ERRORS: Dict[str, str] = {}                    # tool module -> why it failed to load (core/loading.py)


def load_errors() -> Dict[str, str]:
    """Tool modules that failed to load, with the error; their tools are off."""
    return dict(LOAD_ERRORS)


def tool(description: str, params: dict = None, required: list = None, needs: str = "none",
         clears_selection: bool = False, changes_tool_list: bool = False, timeout: float = None,
         writes: tuple = (), group: str = None):
    if needs not in NEEDS:
        raise ValueError(f"needs must be one of {NEEDS}")
    from .groups import GROUPS
    if group not in GROUPS:
        raise ValueError(f"group must be one of {GROUPS}, not {group!r}")

    def register(func):
        name = func.__name__
        if name in TOOLS and TOOLS[name].func.__module__ != func.__module__:
            raise ValueError(f"tool {name} is defined twice")
        sig = inspect.signature(func)
        args = [p for p in list(sig.parameters.values())[1:]]          # after ctx
        schema = {"type": "object", "properties": dict(params or {}), "required": list(required or [])}
        missing = set(schema["properties"]) - {p.name for p in args}
        if missing:
            raise ValueError(f"{name}: schema inputs {sorted(missing)} are not function arguments")
        # the schema and the function agree on what is required
        required_set = set(schema["required"])
        no_default = [p.name for p in args if p.default is inspect.Parameter.empty]
        has_default = [p.name for p in args if p.default is not inspect.Parameter.empty]
        if set(no_default) - required_set:
            raise ValueError(f"{name}: inputs {sorted(set(no_default) - required_set)} have no default but are not "
                             f"in required")
        if required_set & set(has_default):
            raise ValueError(f"{name}: required inputs {sorted(required_set & set(has_default))} have a default")
        if required_set - {p.name for p in args}:
            raise ValueError(f"{name}: required {sorted(required_set - {p.name for p in args})} are not "
                             f"function arguments")
        if set(writes) - {p.name for p in args}:
            raise ValueError(f"{name}: writes {sorted(set(writes) - {p.name for p in args})} are not inputs")
        TOOLS[name] = ToolDef(
            name=name, description=description, schema=schema, func=func, needs=needs,
            clears_selection=clears_selection, changes_tool_list=changes_tool_list, timeout=timeout,
            writes=tuple(writes), group=group,
            accepted={p.name for p in args},
            required_args=list(dict.fromkeys(list(schema['required'])
                                             + [p.name for p in args if p.default is inspect.Parameter.empty])))
        return func

    return register
