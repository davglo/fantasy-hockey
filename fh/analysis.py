"""Written takes (state/ai_manual.json) and the build's Analysis check against live numbers."""
from __future__ import annotations

import json
import logging
import re
from datetime import date

from fh import config

log = logging.getLogger(__name__)

MANUAL = config.STATE / "ai_manual.json"


def load_manual() -> dict:
    try:
        return json.loads(MANUAL.read_text())
    except FileNotFoundError:
        return {}
    except ValueError as e:
        log.warning("ai_manual.json unreadable (%s); dashboard shows numbers only", e)
        return {}


def check(ctx: dict, manual: dict) -> list:
    """Flag written takes that the current numbers contradict: age, players who changed teams, stale urgency."""
    notes = []
    if not manual:
        return ["no written takes yet"]
    age = (date.fromisoformat(ctx["period"]["date"]) - date.fromisoformat(manual["date"])).days
    notes.append("takes dated %s (%d days old)" % (manual["date"], age))
    mine = {p["name"] for p in ctx["roster"]}
    text = " ".join((manual.get("takes") or {}).values())
    for name in sorted(manual.get("my_players", [])):
        last = name.split()[-1]
        if (name in text or re.search(r"\b%s\b" % re.escape(last), text)) and name not in mine:
            notes.append("take mentions %s as mine but he's no longer on my roster" % name)
    if manual.get("playoff_odds") is not None and abs(manual["playoff_odds"] - ctx["me"]["playoff_odds"]) > 0.15:
        notes.append("playoff odds moved %.0f%% -> %.0f%% since the takes were written"
                     % (manual["playoff_odds"] * 100, ctx["me"]["playoff_odds"] * 100))
    return notes
