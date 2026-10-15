"""Betting lines (DraftKings, via ESPN's free game summaries) -> per-team, per-day projection adjustments.

Lines exist only a day or two ahead, so this sharpens today/tomorrow: skaters scale with their team's
implied goals vs its usual scoring; goalies shift with implied win odds and the opponent's implied goals."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from fh import espn
from fh.rankings import norm_team

log = logging.getLogger(__name__)

SITE = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/"
# Share of a skater's fantasy points that moves with his team's goals (G, A, PPP/SHP under league scoring;
# SOG/HIT/BLK barely do). Typical F ~65%, D ~40%.
GOAL_SHARE = {"F": 0.65, "D": 0.40}
MULT_RANGE = (0.7, 1.4)


@dataclass
class Line:
    goals: float       # this team's implied goals
    opp_goals: float   # opponent's implied goals
    win: float         # implied win probability (incl. OT/SO), vig removed
    opp: str


@dataclass
class Adj:
    line: Line
    base_gf: float     # team's usual goals for / game
    sk_mult: dict      # group -> skater multiplier
    g_delta: float     # goalie points per start vs his usual rate


def _poisson(lam: float, k: int) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _p_over(lam: float, line: float) -> float:
    return 1 - sum(_poisson(lam, k) for k in range(int(math.floor(line)) + 1))


def _p_win(la: float, lb: float) -> float:
    """P(A wins) with independent Poisson goals; ties (OT/SO) split evenly."""
    pa = [_poisson(la, k) for k in range(20)]
    pb = [_poisson(lb, k) for k in range(20)]
    win = sum(pa[i] * pb[j] for i in range(20) for j in range(i))
    tie = sum(pa[i] * pb[i] for i in range(20))
    return win + 0.5 * tie


def _bisect(f, lo: float, hi: float, target: float) -> float:
    """f increasing on [lo, hi]; x with f(x) ~= target (clamped to the range)."""
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) < target else (lo, mid)
    return (lo + hi) / 2


def _implied(american: float) -> float:
    return 100 / (american + 100) if american > 0 else -american / (-american + 100)


def implied_goals(total: float, over_odds, under_odds, ml_home: float, ml_away: float) -> tuple:
    """(home goals, away goals, home win prob) from an over/under and moneylines."""
    p_home = _implied(ml_home) / (_implied(ml_home) + _implied(ml_away))
    mu = total
    if over_odds and under_odds:
        p_over = _implied(over_odds) / (_implied(over_odds) + _implied(under_odds))
        mu = _bisect(lambda lam: _p_over(lam, total), total - 1.5, total + 1.5, p_over)
    share = _bisect(lambda s: _p_win(s * mu, (1 - s) * mu), 0.2, 0.8, p_home)
    return share * mu, (1 - share) * mu, p_home


def _game_line(event_id: str) -> dict | None:
    data = espn.cached("odds_%s" % event_id, 2 * 3600, lambda: espn._curl(SITE + "summary?event=%s" % event_id))
    pc = next((p for p in data.get("pickcenter") or [] if p.get("overUnder") and p.get("homeTeamOdds", {}).get("moneyLine")
               and p.get("awayTeamOdds", {}).get("moneyLine")), None)
    if not pc:
        return None
    comp = (data.get("header", {}).get("competitions") or [{}])[0]
    teams = {c["homeAway"]: norm_team(c["team"]["abbreviation"]) for c in comp.get("competitors", [])}
    if set(teams) != {"home", "away"}:
        return None
    hg, ag, ph = implied_goals(pc["overUnder"], pc.get("overOdds"), pc.get("underOdds"),
                               pc["homeTeamOdds"]["moneyLine"], pc["awayTeamOdds"]["moneyLine"])
    return {teams["home"]: Line(hg, ag, ph, teams["away"]), teams["away"]: Line(ag, hg, 1 - ph, teams["home"])}


def fetch_lines(day) -> dict:
    """NHL team -> Line for games on `day` (ET date). Empty if lines aren't posted."""
    board = espn.cached("scoreboard_%s" % day.strftime("%Y%m%d"), 2 * 3600,
                        lambda: espn._curl(SITE + "scoreboard?dates=%s" % day.strftime("%Y%m%d")))
    out = {}
    for ev in board.get("events", []):
        try:
            out.update(_game_line(ev["id"]) or {})
        except (espn.FetchError, KeyError, ValueError) as e:
            log.info("no line for event %s (%s)", ev.get("id"), e)
    return out


def adjustments(lines: dict, rates: dict, scoring: dict) -> dict:
    """team -> Adj. rates: team -> {"gf", "ga", "win"} usual per-game rates. scoring: statId -> points."""
    w_pts, ga_pts = scoring.get(1, 0.0), scoring.get(4, 0.0)
    lines = {t: ln for t, ln in lines.items() if rates.get(t, {}).get("gf") and rates.get(ln.opp)}
    if not lines:
        return {}
    # Totals run above teams' usual scoring (OT goals, juice); keep the relative signal, not a slate-wide bump.
    k = sum(rates[t]["gf"] for t in lines) / sum(ln.goals for ln in lines.values())
    out = {}
    for team, ln in lines.items():
        r = rates[team]
        ln = Line(ln.goals * k, ln.opp_goals * k, ln.win, ln.opp)
        sk = {g: min(max(1 + s * (ln.goals / r["gf"] - 1), MULT_RANGE[0]), MULT_RANGE[1]) for g, s in GOAL_SHARE.items()}
        # Goalie: wins vs his team's usual win rate, goals against vs his team's usual goals against.
        g_delta = w_pts * (ln.win - r["win"]) + ga_pts * (ln.opp_goals - r["ga"])
        out[team] = Adj(line=ln, base_gf=r["gf"], sk_mult=sk, g_delta=g_delta)
    return out


def fetch(cal, periods, rates: dict, scoring: dict) -> dict:
    """(team, period) -> Adj for the given scoring periods; {} when lines are unavailable."""
    out = {}
    for d in periods:
        try:
            adj = adjustments(fetch_lines(cal.date_of(d)), rates, scoring)
        except espn.FetchError as e:
            log.info("betting lines unavailable for %s (%s)", cal.date_of(d), e)
            continue
        for team, a in adj.items():
            if d in cal.team_games.get(team, ()):
                out[(team, d)] = a
    log.info("betting lines: %d team-games adjusted", len(out))
    return out
