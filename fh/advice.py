"""Advice from engine numbers: lineup problems, needs, free agents, streaming, trades, buy/sell posture."""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from fh import engine
from fh.espn import League
from fh.season import BENCH_SLOT, IR_SLOT, Calendar

SLOT_GROUP = {3: "F", 4: "D", 5: "G", 6: "UTIL", 0: "F", 1: "F", 2: "F"}


# ---- lineup helper ---------------------------------------------------------

@dataclass
class LineupIssue:
    severity: str    # "fix" or "check"
    text: str


def lineup_check(mine: list, league: League, cal: Calendar, period: int) -> tuple:
    """Compare my actual ESPN lineup for `period` with the best possible one. Advice only.
    A starter without a game only matters if a benched player with a game could take the slot."""
    plays = lambda p: period in cal.team_games.get(p.team, ())
    can_play = lambda p: plays(p) and p.injury != "OUT"
    starters = [p for p in mine if p.slot not in (BENCH_SLOT, IR_SLOT)]
    best_pts, best = engine.best_lineup(engine.active(mine), league.slots,
                                        lambda p: p.exp_game() if can_play(p) else 0.0)
    best_ids = {p.id for p in best}
    # Starters who won't score and aren't part of the best lineup = slots to hand to benched players.
    idle = sorted((p for p in starters if p.id not in best_ids), key=lambda p: p.exp_game() if can_play(p) else -1)
    issues = []
    for p in sorted((p for p in mine if p.slot == BENCH_SLOT and p.id in best_ids), key=lambda p: -p.exp_game()):
        swap = next((q for q in idle if q.group == p.group or (p.group != "G" and q.group != "G")), None)
        if swap is not None:
            idle.remove(swap)
        why = "no game" if swap is not None and not plays(swap) else ("listed %s" % swap.injury if swap is not None and not can_play(swap) else "lower projection")
        issues.append(LineupIssue("fix", "Start %s (%s vs %s)%s" % (
            p.name, p.team, cal.opponents.get((p.team, period), "?"),
            " for %s (%s)." % (swap.name, why) if swap is not None else " in an open slot.")))
    for p in starters:
        if plays(p) and p.injury == "OUT":
            issues.append(LineupIssue("fix", "%s is starting but listed OUT." % p.name))
        elif plays(p) and p.injury not in ("ACTIVE", "DAY_TO_DAY"):
            issues.append(LineupIssue("check", "%s is starting but listed %s - confirm he's eligible tonight." % (p.name, p.injury)))
        if p.group == "G" and can_play(p):
            issues.append(LineupIssue("check", "Confirm %s starts in net (no confirmed-starter feed)." % p.name))
    actual = sum(p.exp_game() for p in starters if can_play(p))
    return issues, best, max(0.0, best_pts - actual)


# ---- needs -------------------------------------------------------------------

def needs(mine: list, league: League, cal: Calendar, periods, pool: list) -> dict:
    """Per position: my weakest starter's ROS points vs a typical starter at that position league-wide."""
    out = {}
    for g in ("F", "D", "G"):
        mine_g = sorted((p.ros(cal, periods) for p in engine.active(mine) if p.group == g), reverse=True)
        n = league.slots.get(g, 0)
        league_g = sorted((p.ros(cal, periods) for p in pool if p.group == g and p.owner), reverse=True)
        typical = league_g[min(len(league_g) - 1, n * league.size // 2)] if league_g else 0
        weakest = mine_g[n - 1] if len(mine_g) >= n else 0.0
        out[g] = {"weakest": weakest, "typical": typical, "gap": typical - weakest, "count": len(mine_g)}
    return out


# ---- free agents -------------------------------------------------------------

@dataclass
class FARec:
    p: object
    ros_gain: float      # change in my season lineup value if added (dropping my least valuable player)
    week_gain: float     # change in projected points this week + next
    drop: object
    score: float
    games_this: int
    games_next: int
    luck: str


def luck_flag(p) -> str:
    """Shooting-% luck vs his projection-implied rate, once there's a real sample."""
    if p.group == "G" or p.act_sog < 40:
        return ""
    sh = p.act_g / p.act_sog if p.act_sog else 0
    exp_sh = 0.10
    if sh > exp_sh * 1.6:
        return "hot (sh%% %.0f) - sell-high risk" % (sh * 100)
    if sh < exp_sh * 0.5:
        return "cold (sh%% %.0f) - buy-low" % (sh * 100)
    return ""


def free_agents(mine: list, fas: list, league: League, cal: Calendar, ros_periods, this_week, next_week, n=25) -> list:
    """Score FAs by exact day-by-day projected points gained (ROS and next two weeks), dropping my least valuable player."""
    ros = {p.id: p.ros(cal, ros_periods) for p in list(mine) + list(fas)}
    base = engine.projected_points(mine, league, cal, ros_periods)
    short = list(this_week) + list(next_week)
    base_short = engine.projected_points(mine, league, cal, short)
    # Drop candidates: my 3 lowest-ROS players, judged by actual lineup impact.
    drop_pool = sorted(engine.active(mine), key=lambda p: ros[p.id])[:4]
    drop_cost = {d.id: base - engine.projected_points([p for p in mine if p.id != d.id], league, cal, ros_periods)
                 for d in drop_pool}
    drop = min(drop_pool, key=lambda d: drop_cost[d.id])
    cands = sorted(fas, key=lambda p: -ros[p.id])[:50]
    recs = []
    for fa in cands:
        roster = [p for p in mine if p.id != drop.id] + [fa]
        g = engine.projected_points(roster, league, cal, ros_periods) - base
        w = engine.projected_points(roster, league, cal, short) - base_short
        trend = max(-5.0, min(5.0, fa.pct_change)) * 0.5
        recs.append(FARec(p=fa, ros_gain=g, week_gain=w, drop=drop, score=g + 0.5 * w + trend,
                          games_this=cal.games(fa.team, this_week), games_next=cal.games(fa.team, next_week),
                          luck=luck_flag(fa)))
    recs.sort(key=lambda r: -r.score)
    return recs[:n]


def streaming(fas: list, cal: Calendar, this_week, next_week, n=8) -> dict:
    teams = sorted(cal.team_games)
    games = {t: (cal.games(t, this_week), cal.games(t, next_week)) for t in teams}
    goalies = sorted((p for p in fas if p.group == "G"),
                     key=lambda p: -(p.exp_game() * cal.games(p.team, this_week)))[:n]
    g_rows = [{"p": p, "exp": p.exp_game() * cal.games(p.team, this_week), "games": cal.games(p.team, this_week),
               "opps": [cal.opponents.get((p.team, d), "") for d in this_week if d in cal.team_games.get(p.team, ())]}
              for p in goalies]
    return {"games": games, "goalies": g_rows}


# ---- trades ------------------------------------------------------------------

@dataclass
class Trade:
    partner: int
    give: list
    get: list
    my_gain: float
    their_gain: float
    partner_odds: float


def trades(rosters: dict, me: int, league: League, cal: Calendar, periods, odds: dict, n=12) -> list:
    ros = {p.id: p.ros(cal, periods) for ps in rosters.values() for p in ps}
    base = {t: engine.season_value(ps, league, cal, periods, ros) for t, ps in rosters.items()}
    mine = rosters[me]
    my_top = sorted(mine, key=lambda p: -ros[p.id])[:16]
    out = []
    for t, theirs in rosters.items():
        if t == me:
            continue
        their_top = sorted(theirs, key=lambda p: -ros[p.id])[:14]
        gives = [[a] for a in my_top] + [list(c) for c in combinations(my_top[:12], 2)]
        for give in gives:
            give_ids = {p.id for p in give}
            for b in their_top:
                if len(give) == 2 and ros[b.id] < max(ros[p.id] for p in give):
                    continue  # 2-for-1 only as a consolidation up
                new_mine = [p for p in mine if p.id not in give_ids] + [b]
                new_theirs = [p for p in theirs if p.id != b.id] + give
                mg = engine.season_value(new_mine, league, cal, periods, ros) - base[me]
                tg = engine.season_value(new_theirs, league, cal, periods, ros) - base[t]
                if mg > 3 and tg > -2:   # I gain; they don't lose meaningfully (fair enough to propose)
                    out.append(Trade(partner=t, give=give, get=[b], my_gain=mg, their_gain=tg,
                                     partner_odds=odds.get(t, 0.5)))
    # Screened with the fast season shortcut; re-score the best 30 with exact daily projections.
    out.sort(key=lambda x: -(x.my_gain + min(x.their_gain, 5)))
    exact = {t: engine.projected_points(ps, league, cal, periods) for t, ps in rosters.items()}
    confirmed = []
    for x in out[:30]:
        give_ids = {p.id for p in x.give}
        new_mine = [p for p in mine if p.id not in give_ids] + x.get
        new_theirs = [p for p in rosters[x.partner] if p.id != x.get[0].id] + x.give
        x.my_gain = engine.projected_points(new_mine, league, cal, periods) - exact[me]
        x.their_gain = engine.projected_points(new_theirs, league, cal, periods) - exact[x.partner]
        if x.my_gain > 3 and x.their_gain > -2:
            confirmed.append(x)
    out = confirmed
    # Prefer partners whose season is slipping (sellers), then my gain.
    out.sort(key=lambda x: -(x.my_gain * (1.3 - x.partner_odds) + min(x.their_gain, 5)))
    seen, best = set(), []
    for x in out:
        key = (x.partner, tuple(sorted(p.id for p in x.give)))
        if key in seen:
            continue
        seen.add(key)
        best.append(x)
        if len(best) >= n:
            break
    return best


def posture(odds: float) -> str:
    if odds >= 0.65:
        return "Contender: hold producing starters; buy at your biggest need."
    if odds >= 0.35:
        return "Bubble: make value-positive moves only; target short-term upgrades at your weakest spot."
    return "Chasing: take upside swings and sell anything a contender will overpay for."
