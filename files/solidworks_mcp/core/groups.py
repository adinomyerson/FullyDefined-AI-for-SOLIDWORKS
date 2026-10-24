"""
Tool groups. Every tool belongs to one group; the user can switch groups off in the settings
("groups": {"assembly": false}). A switched-off tool is not listed and is refused with the setting to change.
core is always on: without connecting and documents nothing else works. experimental is off until the user turns
it on. A tool module may bring a group of its own (add_group), with its own switch in place of the setting.
"""

import logging

from . import settings
from .results import Err, ToolError

GROUPS = ["core", "sketch", "features", "sheetmetal", "assembly", "drawing", "export", "experimental"]
ALWAYS_ON = {"core"}
OFF_BY_DEFAULT = {"experimental"}

logger = logging.getLogger(__name__)

all_on = False          # every group on, for a check that must see every tool
_own_switch = {}        # group -> (is_on(), refusal(tool) -> ToolError, report() -> text for doctor)
_warned = set()         # groups whose ignored setting was logged (once per process)


def add_group(name: str, off_by_default: bool = True, switch=None, refusal=None, report=None) -> None:
    """A group a tool module brings with it. switch(), when given, alone decides whether the group is on (the
    groups setting is then ignored); refusal(tool) words its refusal; report() is a line for doctor."""
    if name not in GROUPS:
        GROUPS.append(name)
    if off_by_default:
        OFF_BY_DEFAULT.add(name)
    if switch is not None:
        _own_switch[name] = (switch, refusal, report)


def reports() -> list:
    """(group, text) for the groups that report their switch to doctor."""
    return [(name, report()) for name, (_, _, report) in sorted(_own_switch.items()) if report]


def is_on(group: str) -> bool:
    if all_on or group in ALWAYS_ON:
        return True
    chosen = settings.load().get("groups") or {}
    if group in _own_switch:
        if group in chosen and group not in _warned:
            _warned.add(group)
            logger.warning(f'The "{group}" groups setting is ignored: this group has its own switch')
        return bool(_own_switch[group][0]())
    if group in chosen:
        return bool(chosen[group])
    return group not in OFF_BY_DEFAULT


def refusal(tool: str, group: str) -> ToolError:
    own = _own_switch.get(group)
    if own and own[1]:
        return own[1](tool)
    return ToolError(f"{tool} is in the tool group '{group}', which is switched off. To use it, set "
                     f"\"groups\": {{\"{group}\": true}} in the settings, then restart the AI app so it lists "
                     f"the tool again.", Err.INPUT, {"group": group, "setting": "groups"})
