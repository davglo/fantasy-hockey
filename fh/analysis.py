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


def sanity(ctx: dict) -> list:
    """Invariants every build must satisfy; failures are logged as warnings (empty list = healthy)."""
    errs = []

    def chk(cond, msg):
        if not cond:
            errs.append(msg)
    slots = sum(v for k, v in ctx["league"]["slots"].items() if k not in ("BE", "IR"))
    odds = sum(s["odds"] for s in ctx["standings"])
    chk(abs(odds - ctx["league"]["playoff_teams"]) < 0.05, "playoff odds sum to %.2f" % odds)
    m = ctx.get("matchup")
    if m:
        chk(0 <= m["win_prob"] <= 1, "win prob out of range")
        chk(m["proj_me"] >= m["actual_me"] - 0.01 and m["proj_opp"] >= m["actual_opp"] - 0.01, "projection below banked points")
    mine = {r["name"] for r in ctx["roster"]}
    rostered = {n for names in ctx["rosters"].values() for n in names}
    chk(not ({f["name"] for f in ctx["free_agents"]} & rostered), "free-agent list contains rostered players")
    chk(not any(d["injury"] for d in ctx["drop_ranking"]), "flagged player in drop ranking")
    chk(not any(g["injury"] for g in ctx["goalies_today"] + ctx["streaming"]["goalies"]), "injured goalie suggested")
    for t in ctx["trades"]:
        chk(all(n in mine for n in t["give"]) and not any(n in mine for n in t["get"]), "trade sides wrong: %s" % t["give"])
        chk(t["market_give"] >= t["market_get"] - 0.5, "trade not market-fair: %s" % t["give"])
    for o in ctx["opportunities"]:
        chk(all(b["name"] not in rostered for b in o["beneficiaries"]), "opportunity names a rostered player")
    for d in ctx["playoff_usable"]["days"]:
        chk(d["used"] + d["empty_sk"] + d["empty_g"] == slots, "usable-games slots don't add up on %s" % d["date"])
    for k in ("this", "next"):
        chk(all(mv["drop"] in mine for mv in ctx["streaming"][k]["moves"]), "streaming drop isn't on my roster")
    chk(not ctx.get("calendar_warning"), "calendar: %s" % ctx.get("calendar_warning"))
    return errs
