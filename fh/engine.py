"""In-season engine: per-game rates, optimal daily lineups, matchup + playoff projections."""
from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass

from fh import config
from fh.espn import League, fantasy_points
from fh.season import BENCH_SLOT, IR_SLOT, Calendar, LeagueState

log = logging.getLogger(__name__)

PRIOR_GAMES = 15        # preseason projection counts as this many games when blending with actuals
TEAM_WEEK_SD = 0.15     # sd of a team's weekly score as a share of its projection
SIMS = 4000
BENCH_WEIGHT = 0.35     # season-level: a bench skater fills some off-day slots
NHL_GAMES = 82


@dataclass
class P:
    """A player in-season. `base` is the preseason valuation.Player."""
    base: object
    rate: float          # expected fantasy points per game he plays
    share: float         # share of his team's games he plays (goalies ~0.65)
    act_gp: int
    act_fp: float
    act_g: float
    act_sog: float
    pct_change: float
    owner: int | None = None
    slot: int | None = None
    status: str = ""     # FREEAGENT / WAIVERS for free agents

    @property
    def id(self): return self.base.id
    @property
    def name(self): return self.base.name
    @property
    def group(self): return self.base.group
    @property
    def team(self): return self.base.team
    @property
    def injury(self): return self.base.injury

    def exp_game(self) -> float:
        """Expected points on a day his NHL team plays."""
        return self.rate * self.share

    def ros(self, cal: Calendar, periods) -> float:
        return self.exp_game() * cal.games(self.team, periods)


def _season_actuals(pl: dict) -> dict:
    return next((s["stats"] for s in pl.get("stats", []) if s.get("seasonId") == config.SEASON
                 and s.get("statSourceId") == 0 and s.get("statSplitTypeId") == 0), {}) or {}


def make_players(entries: list, base_players: list, league: League, owners: dict | None = None) -> list:
    """entries: playerPoolEntry dicts; base_players: valuation.Player built from the same entries."""
    by_id = {b.id: b for b in base_players}
    out = []
    for e in entries:
        pl = e["player"]
        b = by_id.get(pl["id"])
        if b is None:
            continue
        act = _season_actuals(pl)
        gp = int(act.get("30") or act.get("34") or 0)
        afp = fantasy_points(act, league.scoring) if act else 0.0
        pre_gp = b.gp or (60 if b.group == "G" else 78)
        pre_rate = b.fp / pre_gp if pre_gp else 0.0
        rate = (pre_rate * PRIOR_GAMES + afp) / (PRIOR_GAMES + gp)
        own = pl.get("ownership", {}) or {}
        o = (owners or {}).get(pl["id"], (None, None))
        out.append(P(base=b, rate=rate, share=min(1.0, pre_gp / NHL_GAMES), act_gp=gp, act_fp=afp,
                     act_g=act.get("13", 0.0), act_sog=act.get("29", 0.0), pct_change=own.get("percentChange", 0.0),
                     owner=o[0], slot=o[1], status=e.get("status", "")))
    return out


# ---- lineups -------------------------------------------------------------

def best_lineup(players: list, slots: dict, value) -> tuple:
    """Greedy optimal lineup for one day. value(p) -> points (0 if no game). Returns (points, starters)."""
    pool = sorted((p for p in players if value(p) > 0), key=lambda p: -value(p))
    used, total, starters = set(), 0.0, []
    for grp, n in (("G", slots.get("G", 0)), ("F", slots.get("F", 0)), ("D", slots.get("D", 0))):
        for p in [p for p in pool if p.group == grp][:n]:
            used.add(p.id); total += value(p); starters.append(p)
    for p in [p for p in pool if p.id not in used and p.group in ("F", "D")][:slots.get("UTIL", 0)]:
        used.add(p.id); total += value(p); starters.append(p)
    return total, starters


def active(players: list) -> list:
    return [p for p in players if p.slot != IR_SLOT]


def projected_points(players: list, league: League, cal: Calendar, periods, include_ir: bool = False) -> float:
    """Sum of optimal daily lineups over the given scoring periods (manager sets the best lineup).
    include_ir: count IR players (rest-of-season: they return at an unknown date, so no discount)."""
    act = list(players) if include_ir else active(players)
    total = 0.0
    for d in periods:
        total += best_lineup(act, league.slots,
                             lambda p: p.exp_game() if d in cal.team_games.get(p.team, ()) else 0.0)[0]
    return total


def season_value(players: list, league: League, cal: Calendar, periods, ros: dict | None = None) -> float:
    """Fast roster strength for trade/FA comparisons: best season lineup by ROS points + bench credit."""
    ros = ros if ros is not None else {p.id: p.ros(cal, periods) for p in players}
    total, starters = best_lineup(players, league.slots, lambda p: ros[p.id] + 1e-9)
    starter_ids = {p.id for p in starters}
    bench = sorted((ros[p.id] for p in players if p.id not in starter_ids and p.group != "G"), reverse=True)
    return total + BENCH_WEIGHT * sum(bench[:league.slots.get("BE", 0)])


def games_started(players: list, league: League, cal: Calendar, periods) -> int:
    """Player-games that would fill lineup slots (the 'games played edge')."""
    act = active(players)
    n = 0
    for d in periods:
        n += len(best_lineup(act, league.slots,
                             lambda p: p.exp_game() if d in cal.team_games.get(p.team, ()) else 0.0)[1])
    return n


# ---- matchups & playoff odds --------------------------------------------

def win_prob(mu_a: float, mu_b: float) -> float:
    sd = math.sqrt((TEAM_WEEK_SD * mu_a) ** 2 + (TEAM_WEEK_SD * mu_b) ** 2) or 1.0
    return 0.5 * (1 + math.erf((mu_a - mu_b) / (sd * math.sqrt(2))))


@dataclass
class Projection:
    week_mu: dict        # (teamId, matchup) -> projected points for that matchup (incl. actual so far)
    playoff_odds: dict   # teamId -> probability
    exp_wins: dict       # teamId -> projected final wins
    strength: dict       # teamId -> avg projected points per full week, rest of season


def project(state: LeagueState, cal: Calendar, rosters: dict) -> Projection:
    lg = state.league
    today = state.today_period
    week_mu = {}
    for m in range(state.current_matchup, state.regular_matchups + 1):
        periods = [d for d in cal.matchups[m] if d >= today]
        for tid, ps in rosters.items():
            week_mu[(tid, m)] = projected_points(ps, lg, cal, periods, include_ir=m > state.current_matchup)
    for mt in state.schedule:   # add points already banked in the current matchup
        if mt.period == state.current_matchup:
            week_mu[(mt.home, mt.period)] = week_mu.get((mt.home, mt.period), 0) + mt.home_pts
            week_mu[(mt.away, mt.period)] = week_mu.get((mt.away, mt.period), 0) + mt.away_pts
    full = [m for m in range(state.current_matchup + 1, state.regular_matchups + 1)] or [state.current_matchup]
    strength = {tid: sum(week_mu[(tid, m)] * 7 / len(cal.matchups[m]) for m in full) / len(full) for tid in rosters}

    remaining = [mt for mt in state.schedule if not mt.playoff and mt.winner == "UNDECIDED"
                 and mt.period >= state.current_matchup]
    rng = random.Random(42)
    made = {tid: 0 for tid in rosters}
    wins_tot = {tid: 0.0 for tid in rosters}
    n_playoff = lg.playoff_teams
    for _ in range(SIMS):
        w = {tid: t.wins + 0.5 * t.ties for tid, t in state.teams.items()}
        pf = {tid: t.points_for for tid, t in state.teams.items()}
        for mt in remaining:
            a = rng.gauss(week_mu[(mt.home, mt.period)], TEAM_WEEK_SD * week_mu[(mt.home, mt.period)])
            b = rng.gauss(week_mu[(mt.away, mt.period)], TEAM_WEEK_SD * week_mu[(mt.away, mt.period)])
            pf[mt.home] += a
            pf[mt.away] += b
            w[mt.home if a > b else mt.away] += 1
        for tid in sorted(w, key=lambda t: (-w[t], -pf[t]))[:n_playoff]:
            made[tid] += 1
        for tid in w:
            wins_tot[tid] += w[tid]
    return Projection(week_mu=week_mu, playoff_odds={t: made[t] / SIMS for t in made},
                      exp_wins={t: wins_tot[t] / SIMS for t in wins_tot}, strength=strength)
