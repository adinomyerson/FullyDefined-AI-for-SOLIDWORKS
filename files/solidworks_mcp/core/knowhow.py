"""
The operator know-how (know_how.md, shipped with the package): its "In short" summary, which the server sends as
its instructions, and its sections and recipes, which solidworks_guide returns one at a time. The setting
"know_how": false switches both off, for runs without the know-how.
"""

import logging
import os
import re
from typing import Dict, List, Optional

from . import settings

logger = logging.getLogger(__name__)

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "know_how.md")
SUMMARY = "In short"


def enabled() -> bool:
    return settings.load().get("know_how") is not False


def _text() -> str:
    try:
        with open(PATH, encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        logger.warning(f"The know-how is missing: {e}")
        return ""


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def topics() -> List[Dict]:
    """Every section, and inside Recipes every recipe: {key, title, text}, in file order."""
    out = []
    for part in re.split(r"^## ", _text(), flags=re.M)[1:]:
        title, _, body = part.partition("\n")
        title = title.strip()
        out.append({"key": _slug(title), "title": title, "text": f"## {title}\n{body}".strip()})
        if title == "Recipes":
            for m in re.finditer(r"^\*\*([A-Z])\. ([^*]+?)\*\*.*?(?=^\*\*[A-Z]\. |\Z)", body, re.S | re.M):
                out.append({"key": m.group(1).lower(), "title": f"{m.group(1)}. {m.group(2).strip()}",
                            "text": m.group(0).strip()})
    return out


def summary() -> str:
    """The 'In short' section, for the server's instructions; empty when switched off or missing."""
    if not enabled():
        return ""
    found = next((t for t in topics() if t["title"] == SUMMARY), None)
    return found["text"].split("\n", 1)[1].strip() if found else ""


def _words(text: str) -> List[str]:
    """A title's or topic's words, a recipe letter and a plural s left out ("sheet metal" = "Sheet-metal")."""
    words = [w for w in _slug(re.sub(r"^[A-Z]\. ", "", text.strip())).split("-") if w]
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words]


def matches(topic: str) -> List[Dict]:
    """The topics a topic names: by its key, its title or a recipe letter; else the start of a title; else every
    title holding all of its words (a title that is only those words first). Case does not matter."""
    wanted = _slug(topic or "")
    rows = topics()
    if not wanted:
        return []
    exact = [r for r in rows if wanted in (r["key"], _slug(r["title"]))]
    if exact:
        return exact[:1]
    starts = [r for r in rows if r["key"].startswith(wanted) or _slug(r["title"]).startswith(wanted)]
    if starts:
        return starts
    asked = _words(topic)
    found = [r for r in rows if asked and all(w in _words(r["title"]) for w in asked)]
    whole = [r for r in found if _words(r["title"]) == asked]
    return whole or found


def find(topic: str) -> Optional[Dict]:
    """The one topic a topic names, or None (none, or more than one)."""
    found = matches(topic)
    return found[0] if len(found) == 1 else None
