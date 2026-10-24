"""Logging to the server's home folder (settings.home(); stdout belongs to MCP).

One log file per server process, solidworks_mcp-<pid>.log: each chat starts its own server, and on
Windows a log another process holds open cannot be rotated, so a shared log grew past its limit. Each file
rotates at a size limit. At start-up, older processes' logs are pruned: the newest log_backups are kept,
any older than PRUNE_DAYS go."""

import logging
import os
import re
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import settings

PRUNE_DAYS = 7


def file_for(pid: int) -> Path:
    stem, suffix = os.path.splitext(settings.load()["log_file"])
    return settings.home() / f"{stem}-{pid}{suffix or '.log'}"


FILE = file_for(os.getpid())


def log_path() -> str:
    """This server process's log file."""
    return str(FILE)


class SafeRotatingFileHandler(RotatingFileHandler):
    """Rotates at a size limit. Should another process hold the file open, Windows refuses to rename it:
    skip that rotation and keep logging instead of failing."""

    def doRollover(self):
        try:
            super().doRollover()
        except OSError:
            if self.stream is None:
                self.stream = self._open()


def make_handler(path=FILE, max_bytes=None, backups=None) -> logging.Handler:
    config = settings.load()
    return SafeRotatingFileHandler(
        path, maxBytes=int(config["log_max_bytes"] if max_bytes is None else max_bytes),
        backupCount=int(config["log_backups"] if backups is None else backups), encoding="utf-8")


def prune(folder: Path, keep: int, max_age_days: float = PRUNE_DAYS, current: Path = None) -> list:
    """Delete the logs of older server processes: all but the newest `keep` processes, and any older than
    max_age_days. The current process's log, and a file still open elsewhere, are left. Returns what went."""
    stem = os.path.splitext(settings.load()["log_file"])[0]
    pattern = re.compile(rf"^{re.escape(stem)}-(\d+)\.log(\.\d+)?$", re.I)
    runs = {}
    for path in Path(folder).iterdir():
        m = pattern.match(path.name)
        if m and (current is None or not path.name.startswith(Path(current).name)):
            runs.setdefault(m.group(1), []).append(path)
    newest = {pid: max(p.stat().st_mtime for p in paths) for pid, paths in runs.items()}
    order = sorted(runs, key=lambda pid: -newest[pid])
    cutoff = time.time() - max_age_days * 86400
    removed = []
    for i, pid in enumerate(order):
        if i < keep and newest[pid] >= cutoff:
            continue
        for path in runs[pid]:
            try:
                path.unlink()
                removed.append(path.name)
            except OSError:
                pass                                    # still open in a running server
    return removed


def setup() -> None:
    if logging.getLogger().handlers:
        return
    config = settings.load()
    removed = []
    try:
        removed = prune(settings.home(), int(config["log_backups"]), current=FILE)
    except OSError:
        pass
    logging.basicConfig(
        level=getattr(logging, str(config["log_level"]).upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[make_handler()])
    if removed:
        logging.getLogger(__name__).info(f"Pruned {len(removed)} old log files")
