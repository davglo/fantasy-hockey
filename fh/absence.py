"""Reported absences -> return dates. Parses the latest news note for a stated timeline ('approximately two weeks',
'at least one month', 'out for the season', 'next four games') and removes those games from projections.
Players who are OUT / on IR with no stated timeline stay in projections and are labeled 'return date unknown'."""
from __future__ import annotations

import re
from datetime import date, timedelta

from fh.config import ET

NUM = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
       "ten": 10, "eleven": 11, "twelve": 12}
_N = r"(\d+|%s)" % "|".join(NUM)
_MISS = r"(?:miss|sidelin|out |absen|recover|expected to (?:return|be (?:out|sidelined))|return in|back in|IR\b|injured reserve)"
SPAN = re.compile(r"(?:(at least|a minimum of|minimum of)\s+|(?:approximately|about|around|roughly)\s+)?%s(?:\s*(?:-|to)\s*%s)?[- ](day|week|month)s?" % (_N, _N), re.I)
GAMES = re.compile(r"(?:(?:next|following)\s+%s\s+games?|miss(?:ing)?\s+%s\s+(?:more\s+)?games?)" % (_N, _N), re.I)
SEASON = re.compile(r"(out for the (?:rest of the )?(?:season|year)|season-ending|miss (?:the )?(?:rest|remainder) of the (?:regular )?season)", re.I)
CLEAR = re.compile(r"(upgraded to day-to-day|activated|cleared to (?:play|return)|will return|returns? to (?:the )?lineup|"
                   r"back in the lineup|returned to (?:action|the lineup)|reinstated|practic\w* in full|expected to (?:play|be in the lineup))", re.I)
UNIT = {"day": 1, "week": 7, "month": 30}
UNKNOWN_STATUS = ("OUT", "INJURY_RESERVE", "SUSPENSION")
FAR = date(2100, 1, 1)


def _n(tok: str) -> int:
    return int(tok) if tok.isdigit() else NUM[tok.lower()]


def parse(summary: str) -> dict | None:
    """{'season': True} | {'days': n} | {'games': n} | {'clear': True} | None (no timeline stated)."""
    if SEASON.search(summary):
        return {"season": True}
    if CLEAR.search(summary):
        return {"clear": True}
    g = GAMES.search(summary)
    if g:
        return {"games": _n(g.group(1) or g.group(2))}
    if re.search(_MISS, summary, re.I):
        m = SPAN.search(summary)
        if m:
            return {"days": _n(m.group(2)) * UNIT[m.group(4).lower()]}   # lower bound of a range / "at least"
    return None


def return_date(p, items: list, cal, today: date, max_age_days: int = 30) -> tuple:
    """(first date he's available or None, note). Items newest first. Only the newest note that states a
    timeline (or clears him) counts."""
    for it in items:
        noted = it.published.astimezone(ET).date()
        if (today - noted).days > max_age_days:
            break
        a = parse(it.summary)
        if a is None or "clear" in a:
            if a is not None:
                return None, ""
            continue
        if "season" in a:
            return FAR, "out for the season (%s)" % noted.strftime("%b %-d")
        if "days" in a:
            back = noted + timedelta(days=a["days"])
            label = "~%s per note of %s" % (back.strftime("%b %-d"), noted.strftime("%b %-d"))
        else:
            games = sorted(d for d in cal.team_games.get(p.team, ()) if cal.date_of(d) >= noted)
            if len(games) <= a["games"]:
                return FAR, "out %d games per note of %s" % (a["games"], noted.strftime("%b %-d"))
            back = cal.date_of(games[a["games"]])
            label = "after %d games per note of %s" % (a["games"], noted.strftime("%b %-d"))
        if back <= today:
            return None, ""
        return back, "back %s" % label
    return None, ""


def apply(players: list, items_by_id: dict, cal, today: date) -> int:
    """Set out_until / out_note on every flagged player. Returns how many have a stated return date."""
    dated = 0
    for p in players:
        flagged = p.injury in UNKNOWN_STATUS or p.slot == 8
        if not flagged:
            continue
        back, note = return_date(p, items_by_id.get(p.id, []), cal, today)
        if back:
            p.out_until, p.out_note = back, note
            dated += 1
        elif p.injury in UNKNOWN_STATUS or p.slot == 8:
            p.out_until, p.out_note = None, "return date unknown"
    return dated
