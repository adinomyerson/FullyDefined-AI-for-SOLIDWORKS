"""
The MCP server. Lists the tools from the registry and runs every call through
core.runner.
"""

import base64
import logging

from .core import log

log.setup()

from mcp.server import Server
from mcp.server.lowlevel import NotificationOptions
from mcp.server.stdio import stdio_server
from mcp.types import ImageContent, TextContent, Tool

from .version import __version__
from . import tools                      # registers every tool  # noqa: F401
from .core import runner
from .core.registry import TOOLS
from .core import groups, knowhow, lock, selfcheck, settings
from .core.results import Err, Result, ToolError, cap_text
from .core.worker import WorkerError, worker

logger = logging.getLogger(__name__)

def instructions() -> str:
    """The know-how's 'In short' section. Every client gets it at start-up as the server's instructions, so apps
    without the know-how itself still know how to work with the user. The setting "know_how": false sends none
    (a run without the know-how)."""
    summary = knowhow.summary()
    return "How to work with the SolidWorks tools:\n" + summary if summary else ""


server = Server("solidworks-mcp-server", version=__version__, instructions=instructions() or None)


def _mcp_tool(t) -> Tool:
    return Tool(name=t.name, description=t.description, inputSchema=t.schema)


def tool_list() -> list:
    """Every tool in the registry, in the order the tool modules register them."""
    return [_mcp_tool(t) for t in TOOLS.values()]


def group_of(name: str) -> str:
    return TOOLS[name].group if name in TOOLS else "core"


@server.list_tools()
async def list_tools() -> list:
    """The tools of the groups that are switched on (core/groups.py)."""
    return [t for t in tool_list() if groups.is_on(group_of(t.name))]


def _capped(name: str, content: list) -> list:
    """Every text result is cut at max_result_chars: one huge result must not fill the context."""
    limit = int(settings.load()["max_result_chars"] or 0)
    for item in content:
        text = getattr(item, "text", None)
        if isinstance(text, str) and 0 < limit < len(text):
            logger.warning(f"{name}: result of {len(text)} characters cut at {limit}")
            item.text = cap_text(text, limit)
    return content


def _content(result) -> list:
    content = [TextContent(type="text", text=result.text(int(settings.load()["max_result_chars"] or 0)))]
    if result.image:
        content.append(ImageContent(type="image", data=base64.b64encode(result.image).decode("ascii"),
                                    mimeType="image/png"))
    return content


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list:
    """Every call runs on the one SolidWorks worker thread, with a time limit (core/worker.py), holding the
    lock that keeps other server processes (other chats) out meanwhile (core/lock.py)."""
    if name not in TOOLS:
        return _capped(name, _content(Result(False, f"Unknown tool: {name}", Err.INPUT)))
    group = group_of(name)
    if not groups.is_on(group):
        return _content(groups.refusal(name, group).result())
    call = lambda: runner.run(name, arguments)                  # noqa: E731
    timeout = TOOLS[name].timeout
    wait_s = float(settings.load()["lock_wait_s"])

    def job():
        try:
            return lock.locked(name, call, wait_s)
        except ToolError as e:                                  # another process holds SolidWorks
            return e.result()
    try:
        outcome = await worker.run(name, job, timeout)
    except WorkerError as e:
        return _content(Result(False, e.message, Err(e.code), e.data))
    if outcome.success and TOOLS[name].changes_tool_list:
        try:
            await server.request_context.session.send_tool_list_changed()
            outcome.data["client_notified"] = True
        except Exception as e:           # no client session, e.g. a direct call
            logger.warning(f"Could not send tools/list_changed: {e}")
            outcome.data["client_notified"] = False
    return _capped(name, _content(outcome))


async def main():
    selfcheck.startup()                 # server version, tool modules that failed to load
    options = server.create_initialization_options(notification_options=NotificationOptions(tools_changed=True))
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, options)


def run():
    import asyncio
    asyncio.run(main())
