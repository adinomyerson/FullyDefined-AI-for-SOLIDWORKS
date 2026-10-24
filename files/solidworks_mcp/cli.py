"""The solidworks-mcp command: with no argument it runs the MCP server (what AI apps start); `doctor` checks
the setup and writes a report. The server's modules are imported only when needed, so doctor still
runs when a package the server needs is missing."""

import sys

USAGE = "usage: solidworks-mcp [doctor [--check]]"


def run(argv=None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if not args:
        from .main import run as serve
        serve()
        return 0
    if args in (["doctor"], ["doctor", "--check"]):
        from . import doctor
        sys.exit(doctor.run(check=args == ["doctor", "--check"]))
    print(USAGE, file=sys.stderr)
    sys.exit(2)
