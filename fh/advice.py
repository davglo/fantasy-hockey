"""Advice from engine numbers: lineup problems, needs, free agents, streaming, trades, buy/sell posture."""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from fh import config, engine
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


# ---- shared: roster value + safe drops ------------------------------------

HEALTHY = ("ACTIVE", "DAY_TO_DAY")


class Valuer:
    """Exact roster value = ROS regular-season points + PLAYOFF_WEIGHT x playoff-week points,
    both from day-by-day optimal lineups. Memoized by roster membership."""

    def __init__(self, league: League, cal: Calendar, ros_periods, po_periods):
        self.league, self.cal, self.ros, self.po = league, cal, list(ros_periods), list(po_periods)
        self.memo = {}

    def __call__(self, roster: list) -> float:
        key = frozenset(p.id for p in roster)
        if key not in self.memo:
            self.memo[key] = (engine.projected_points(roster, self.league, self.cal, self.ros, include_ir=True)
                              + config.PLAYOFF_WEIGHT * engine.projected_points(roster, self.league, self.cal, self.po,
                                                                                include_ir=True))
        return self.memo[key]

    def quick(self, p) -> float:
        """Per-player ROS + weighted playoff points (for sorting/screening)."""
        return p.ros(self.cal, self.ros) + config.PLAYOFF_WEIGHT * p.ros(self.cal, self.po)


def healthy_goalies(roster: list) -> int:
    return sum(1 for p in roster if p.group == "G" and p.slot != IR_SLOT and p.injury in HEALTHY)


def droppable(mine: list, value: Valuer, k: int = 6, protect_top: int = 0) -> list:
    """Low-value players I could cut: never IR or injury/suspension-flagged (status isn't proof of a long
    absence), never below 2 healthy goalies, never my top `protect_top`."""
    ranked = sorted((p for p in mine if p.slot != IR_SLOT and p.injury in HEALTHY), key=value.quick)
    top = {p.id for p in ranked[::-1][:protect_top]}
    out = []
    for p in ranked:
        if p.id in top:
            continue
        if p.group == "G" and p.injury in HEALTHY and healthy_goalies(mine) <= 2:
            continue
        out.append(p)
    return out[:k]


def drop_ranking(mine: list, value: "Valuer") -> list:
    """Every droppable player (same protections as `droppable`) with the ROS + playoff points I'd lose."""
    base = value(mine)
    cands = droppable(mine, value, k=len(mine))
    return sorted(((p, base - value([q for q in mine if q.id != p.id])) for p in cands), key=lambda t: t[1])


def underperformers(mine: list, min_gp: int = 10, ratio: float = 0.75) -> list:
    """Players running well below their preseason per-game projection on a real sample."""
    out = []
    for p in mine:
        pre = p.base.fp / p.base.gp if p.base.gp else 0
        if p.act_gp >= min_gp and pre > 0 and p.act_fp / p.act_gp < ratio * pre:
            out.append((p, p.act_fp / p.act_gp, pre))
    return out


def group_points(roster: list, league: League, cal: Calendar, periods, include_ir: bool = True) -> dict:
    """Projected points by position group from optimal daily lineups (UTIL counted with the player's group)."""
    out = {"F": 0.0, "D": 0.0, "G": 0.0}
    pool = list(roster) if include_ir else engine.active(roster)
    for d in periods:
        f = lambda p, d=d: p.exp_game() if d in cal.team_games.get(p.team, ()) else 0.0
        for p in engine.best_lineup(pool, league.slots, f)[1]:
            out[p.group] += f(p)
    return out


def where_you_rank(rosters: dict, me: int, league: League, cal: Calendar, ros_periods) -> dict:
    """Per group, two yardsticks (rank 1 = best):
    quality = per-game points of the top-N players at the group (N = starting slots; IR included, schedule-free),
              which is how most roster rankers (e.g. Lineup Experts) judge a team;
    ros     = rest-of-season starter points from optimal daily lineups (schedule-adjusted, UTIL counted by group)."""
    n = {g: league.slots.get(g, 0) for g in ("F", "D", "G")}
    quality = {t: {g: sum(sorted((p.exp_game() for p in ps if p.group == g), reverse=True)[:n[g]]) for g in n}
               for t, ps in rosters.items()}
    ros = {t: group_points(ps, league, cal, ros_periods) for t, ps in rosters.items()}

    def rank(vals, t):
        return sorted(vals.values(), reverse=True).index(vals[t]) + 1
    out = {}
    for g in ("F", "D", "G"):
        q = {t: quality[t][g] for t in rosters}
        r = {t: ros[t][g] for t in rosters}
        out[g] = {"quality": q[me], "quality_rank": rank(q, me), "ros": r[me], "ros_rank": rank(r, me),
                  "ros_best": max(r.values())}
    tq = {t: sum(quality[t].values()) for t in rosters}
    tr = {t: sum(ros[t].values()) for t in rosters}
    out["Total"] = {"quality": tq[me], "quality_rank": rank(tq, me), "ros": tr[me], "ros_rank": rank(tr, me),
                    "ros_best": max(tr.values())}
    return out


def usable_games(roster: list, league: League, cal: Calendar, periods) -> dict:
    """Day by day: starts I can fill, games wasted on the bench (more players playing than slots) and slots left
    empty. Plus, per NHL team, how many of its games land on days I have an open skater / goalie slot."""
    days, open_sk, open_g = [], set(), set()
    for d in periods:
        playing = [p for p in engine.active(roster) if d in cal.team_games.get(p.team, ())]
        _, st = engine.best_lineup(playing, league.slots, lambda p: p.exp_game() + 1e-9)
        used_g = sum(1 for p in st if p.group == "G")
        used_sk = len(st) - used_g
        sk_slots = sum(league.slots.get(g, 0) for g in ("F", "D", "UTIL"))
        empty_sk, empty_g = sk_slots - used_sk, league.slots.get("G", 0) - used_g
        if empty_sk > 0:
            open_sk.add(d)
        if empty_g > 0:
            open_g.add(d)
        days.append({"period": d, "date": cal.date_of(d).strftime("%a %b %-d"), "playing": len(playing),
                     "used": len(st), "wasted": len(playing) - len(st), "empty_sk": empty_sk, "empty_g": empty_g})
    teams = {t: {"games": cal.games(t, periods), "sk_fit": len(open_sk & g), "g_fit": len(open_g & g)}
             for t, g in cal.team_games.items()}
    return {"days": days, "teams": teams}


def goalies_today(fas: list, cal: Calendar, period: int, starting: dict, opp_gf: dict) -> list:
    """FA goalies whose team plays today. starting: playerId -> 'confirmed' / 'expected' / 'backup' / ''.
    Expected points: full per-start rate if confirmed/expected, 0 if backup, else rate x share of starts."""
    out = []
    for p in fas:
        if p.group != "G" or period not in cal.team_games.get(p.team, ()) or p.injury not in HEALTHY:
            continue    # IR / out goalies aren't starting (e.g. Demko)
        opp = cal.opponents.get((p.team, period), "")
        st = starting.get(p.id, "")
        exp = p.rate if st in ("confirmed", "expected") else 0.0 if st == "backup" else p.exp_game()
        out.append({"p": p, "opp": opp, "confirmed": st, "opp_gf": opp_gf.get(opp), "exp": exp})
    order = {"confirmed": 0, "expected": 1, "": 2, "backup": 3}
    return sorted(out, key=lambda r: (order[r["confirmed"]], -r["exp"]))


# ---- free agents (rest of season) --------------------------------------------

@dataclass
class FARec:
    p: object
    gain: float          # ROS + weighted playoff points gained with the best drop
    week_gain: float     # this week + next (info)
    po_games: int
    drop: object
    score: float
    games_this: int
    games_next: int
    luck: str
    alt_drops: list = None    # next-best drops: [(player, gain)]


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


def free_agents(mine: list, fas: list, value: Valuer, this_week, next_week, n=25) -> list:
    """Rest-of-season adds: exact lineup gain (ROS + playoff weeks x weight), best drop chosen per player."""
    cal, lg = value.cal, value.league
    base = value(mine)
    drops = droppable(mine, value)
    short = list(this_week) + list(next_week)
    cands = sorted(fas, key=lambda p: -value.quick(p))[:40]
    recs = []
    for fa in cands:
        opts = []
        for d in drops:
            if fa.group != "G" and d.group == "G" and healthy_goalies([p for p in mine if p.id != d.id]) < 2:
                continue
            opts.append((value([p for p in mine if p.id != d.id] + [fa]) - base, d))
        if not opts:
            continue
        opts.sort(key=lambda o: -o[0])
        g, d = opts[0]
        roster = [p for p in mine if p.id != d.id] + [fa]
        w = engine.projected_points(roster, lg, cal, short) - engine.projected_points(mine, lg, cal, short)
        trend = max(-5.0, min(5.0, fa.pct_change)) * 0.2
        recs.append(FARec(p=fa, gain=g, week_gain=w, po_games=cal.games(fa.team, value.po), drop=d,
                          score=g + trend, games_this=cal.games(fa.team, this_week),
                          games_next=cal.games(fa.team, next_week), luck=luck_flag(fa),
                          alt_drops=[(x, gx) for gx, x in opts[1:3]]))
    recs.sort(key=lambda r: -r.score)
    return recs[:n]


# ---- streaming (matchup to matchup) ------------------------------------------

def open_slots(roster: list, league: League, cal: Calendar, periods) -> list:
    """Per day: how many lineup slots my roster can't fill (by position) = where a streamer adds points."""
    out = []
    for d in periods:
        _, st = engine.best_lineup(engine.active(roster), league.slots,
                                   lambda p: p.exp_game() if d in cal.team_games.get(p.team, ()) and p.injury != "OUT" else 0.0)
        cnt = {g: sum(1 for p in st if p.group == g) for g in ("F", "D", "G")}
        util = sum(league.slots.get(g, 0) for g in ("F", "D", "UTIL")) - cnt["F"] - cnt["D"]
        out.append({"period": d, "date": cal.date_of(d).strftime("%a %b %-d"), "G": league.slots.get("G", 0) - cnt["G"],
                    "skater": max(0, util), "starts": len(st)})
    return out


@dataclass
class StreamMove:
    add: object
    drop: object
    gain: float
    days: list


def stream_plan(mine: list, fas: list, value: Valuer, periods, adds_left: int, core: int = 14, steps: int = 4) -> list:
    """Greedy adds for one matchup: each step picks the (free agent, drop) pair that adds the most projected
    points over `periods`. My top `core` players (ROS + playoffs) are never dropped. Waiver players need a claim."""
    lg, cal = value.league, value.cal
    pool = sorted((p for p in fas if p.injury in HEALTHY), key=lambda p: -(p.exp_game() * cal.games(p.team, periods)))[:40]
    roster, moves, used = list(mine), [], set()
    for _ in range(min(adds_left, steps)):
        base = engine.projected_points(roster, lg, cal, periods)
        drops = droppable(roster, value, k=5, protect_top=core)
        best = None
        for fa in pool:
            if fa.id in used or not cal.games(fa.team, periods):
                continue
            for d in drops:
                if fa.group != "G" and d.group == "G" and healthy_goalies([p for p in roster if p.id != d.id]) < 2:
                    continue
                trial = [p for p in roster if p.id != d.id] + [fa]
                g = engine.projected_points(trial, lg, cal, periods) - base
                if best is None or g > best.gain:
                    best = StreamMove(add=fa, drop=d, gain=g,
                                      days=[cal.date_of(x).strftime("%a %-d") for x in periods if x in cal.team_games.get(fa.team, ())])
        if best is None or best.gain < 1.0:
            break
        moves.append(best)
        used.add(best.add.id)
        roster = [p for p in roster if p.id != best.drop.id] + [best.add]
    return moves


def streaming(fas: list, cal: Calendar, this_week, next_week, n=8) -> dict:
    teams = sorted(cal.team_games)
    games = {t: (cal.games(t, this_week), cal.games(t, next_week)) for t in teams}
    goalies = sorted((p for p in fas if p.group == "G" and p.injury in HEALTHY),
                     key=lambda p: -(p.exp_game() * cal.games(p.team, this_week)))[:n]
    g_rows = [{"p": p, "exp": p.exp_game() * cal.games(p.team, this_week), "games": cal.games(p.team, this_week),
               "opps": [cal.opponents.get((p.team, d), "") for d in this_week if d in cal.team_games.get(p.team, ())]}
              for p in goalies]
    return {"games": games, "goalies": g_rows}


# ---- playoffs ---------------------------------------------------------------

def playoff_schedule(rosters: dict, me: int, league: League, cal: Calendar, po_weeks: dict) -> dict:
    """Games per NHL team in each playoff week + each fantasy team's projected playoff-week points."""
    teams = sorted(cal.team_games)
    nhl = [{"team": t, "weeks": [cal.games(t, ps) for ps in po_weeks.values()],
            "total": sum(cal.games(t, ps) for ps in po_weeks.values())} for t in teams]
    nhl.sort(key=lambda r: -r["total"])
    avg = sum(r["total"] for r in nhl) / len(nhl) if nhl else 0
    proj = {tid: [engine.projected_points(ps, league, cal, per) for per in po_weeks.values()] for tid, ps in rosters.items()}
    mine = [{"p": p, "weeks": [cal.games(p.team, per) for per in po_weeks.values()]} for p in rosters[me]]
    return {"nhl": nhl, "avg_games": avg, "proj": proj, "mine": mine}


# ---- trades -----------------------------------------------------------------

MAX_PARTNER_LOSS = 10.0   # ROS+playoff points a partner can lose (by projection) and still plausibly accept

@dataclass
class Trade:
    partner: int
    give: list
    get: list
    my_gain: float
    their_gain: float
    market_give: float   # trade value (ESPN market) I send
    market_get: float    # trade value I receive
    partner_odds: float
    backfill: object = None   # FA I add (2-for-1) or player I drop (1-for-2)


def trades(rosters: dict, me: int, fas: list, value: Valuer, tv: dict, odds: dict, n=15,
           fairness: float = 1.0) -> list:
    """Packages the partner should accept on ESPN market value (they receive >= what they send) that raise my
    ROS + playoff projection. tv: playerId -> market trade value (superlinear, so stars cost more)."""
    mine = rosters[me]
    quick = {p.id: value.quick(p) for ps in rosters.values() for p in ps}
    for p in fas:
        quick[p.id] = value.quick(p)
    best_fa = max((p for p in fas if p.group != "G" and p.injury in HEALTHY), key=lambda p: quick[p.id], default=None)
    lg, cal = value.league, value.cal

    def screen(roster):
        return engine.season_value(roster, lg, cal, value.ros, quick)

    base_screen = {t: screen(ps) for t, ps in rosters.items()}
    my_top = sorted(mine, key=lambda p: -quick[p.id])[:16]
    cands = []
    for t, theirs in rosters.items():
        if t == me:
            continue
        their_top = sorted(theirs, key=lambda p: -quick[p.id])[:14]
        packages = [([a], [b]) for a in my_top for b in their_top]
        packages += [(list(g), [b]) for g in combinations(my_top[:12], 2) for b in their_top]
        packages += [([a], list(g)) for a in my_top for g in combinations(their_top[:10], 2)]
        for give, get in packages:
            tv_give, tv_get = sum(tv.get(p.id, 0) for p in give), sum(tv.get(p.id, 0) for p in get)
            if tv_give < fairness * tv_get or tv_get <= 0:
                continue    # partner loses market value: they won't take it
            gi, ge = {p.id for p in give}, {p.id for p in get}
            new_mine = [p for p in mine if p.id not in gi] + get
            back = None
            if len(new_mine) > len(mine):          # 1-for-2: cut my weakest
                back = droppable(new_mine, value, k=1)[0]
                new_mine = [p for p in new_mine if p.id != back.id]
            elif len(new_mine) < len(mine) and best_fa:   # 2-for-1: backfill from free agency
                back = best_fa
                new_mine = new_mine + [best_fa]
            if healthy_goalies(new_mine) < min(2, healthy_goalies(mine)):
                continue    # same goalie floor as drops
            if screen(new_mine) - base_screen[me] <= 0:
                continue
            cands.append((screen(new_mine) - base_screen[me], t, give, get, back, tv_give, tv_get, new_mine))
    cands.sort(key=lambda c: -c[0])
    base_me = value(mine)
    base_them = {}
    out = []
    for _, t, give, get, back, tv_give, tv_get, new_mine in cands[:250]:
        mg = value(new_mine) - base_me
        if mg <= 3:
            continue
        theirs = rosters[t]
        if t not in base_them:
            base_them[t] = value(theirs)
        ge = {p.id for p in get}
        new_theirs = [p for p in theirs if p.id not in ge] + give
        if len(new_theirs) < len(theirs) and best_fa:   # they got 1 for 2: they refill from free agency too
            new_theirs.append(best_fa)
        elif len(new_theirs) > len(theirs):              # they got 2 for 1: they cut their weakest
            cut = min((p for p in new_theirs if p.slot != IR_SLOT), key=value.quick)
            new_theirs = [p for p in new_theirs if p.id != cut.id]
        tg = value(new_theirs) - base_them[t]
        if tg < -MAX_PARTNER_LOSS:
            continue    # market-fair but a clear lineup downgrade for them: they'd see it and pass
        out.append(Trade(partner=t, give=give, get=get, my_gain=mg, their_gain=tg, market_give=tv_give,
                         market_get=tv_get, partner_odds=odds.get(t, 0.5), backfill=back))
    # Most likely to be accepted first: partner also gains points or is out of the race, then my gain.
    out.sort(key=lambda x: -(x.my_gain + max(-30.0, min(x.their_gain, 15.0)) + 10 * (0.5 - x.partner_odds)))
    per_partner, per_player, best = {}, {}, []
    for x in out:   # variety: at most 2 ideas per partner and per player I'd send
        if per_partner.get(x.partner, 0) >= 2 or any(per_player.get(p.id, 0) >= 2 for p in x.give):
            continue
        per_partner[x.partner] = per_partner.get(x.partner, 0) + 1
        for p in x.give:
            per_player[p.id] = per_player.get(p.id, 0) + 1
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
