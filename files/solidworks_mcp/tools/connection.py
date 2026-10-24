"""Connecting to SolidWorks."""

from ..core.api import (connection, Err, install, load_errors, log_path, ok, SERVER_VERSION as __version__, settings, tool,
                        ToolError)


@tool(group="core", description="Attach to the running SolidWorks, or start it first when none is running.", timeout=300)
def connect_solidworks(ctx):
    data = connection.connect()
    data["server_version"] = __version__
    if data.get("launched"):
        return ok(f"Started SolidWorks {data['version']} and connected to it "
                  f"({data.get('startup_time', 0)}s)", data)
    return ok(f"Connected to SolidWorks {data['version']}", data)


@tool(group="core", description="Report the SolidWorks installation, the server's version, the settings and log file it uses, and any tool modules that failed to load.")
def get_solidworks_info(ctx):
    info = install.info(ctx.setting("exe_path"))
    info["server_version"] = __version__
    info["settings_file"] = str(settings.user_file())     # where the user changes settings
    info["log_file"] = log_path()                          # this server process's log
    info["load_errors"] = load_errors()
    broken = (f" Tool modules that failed to load (their tools are off): {', '.join(sorted(info['load_errors']))}."
              if info["load_errors"] else "")
    if not info["found"]:
        raise ToolError("SolidWorks not found." + broken, Err.NO_SOLIDWORKS, info)
    return ok("SolidWorks found." + broken, info)
