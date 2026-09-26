"""Draft math: who's left, who survives to my next pick, what I need, what to take."""
from __future__ import annotations

import math
from dataclasses import dataclass

from fh.espn import League

BENCH_WEIGHT = 0.35   # a skater who can only sit on the bench still scores on off-days (daily lineups)
G3_WEIGHT = 0.5       # 3rd/4th goalie = streaming starts on days G1/G2 don't play
DROPOFF_WEIGHT = 0.5  # how much "the position dries up before my next pick" matters


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _sd(adp: float) -> float:
    return max(2.5, 0.22 * adp)


def p_available(market: float, at_pick: int, now_pick: int = 1) -> float:
    """P(player still on the board at at_pick | still there at now_pick). Normal model around ADP."""
    if at_pick <= now_pick:
        return 1.0
    sd = _sd(market)
    s_at = 1 - _phi((at_pick - 0.5 - market) / sd)
    s_now = 1 - _phi((now_pick - 0.5 - market) / sd)
    return 0.0 if s_now <= 1e-9 else max(0.0, min(1.0, s_at / s_now))


def next_contested_pick(my_picks: list, current: int) -> int | None:
    """My first pick after `current` that has someone else's pick before it.
    With back-to-back picks (8 & 9) nothing can be taken between them, so the real
    decision horizon is the following turn (24)."""
    later = [p for p in my_picks if p >= current]
    if not later:
        return None
    if later[0] > current:
        return later[0]
    prev = later[0]
    for p in later[1:]:
        if p > prev + 1:
            return p
        prev = p
    return None


@dataclass
class Needs:
    counts: dict      # F/D/G rostered
    open_slots: dict  # F/D/G/UTIL/BE open

    def weight(self, group: str, limits: dict) -> float:
        if group == "G":
            if self.counts["G"] >= limits.get("G", 99):
                return 0.0
            return 1.0 if self.open_slots["G"] > 0 else G3_WEIGHT
        if self.open_slots[group] > 0 or self.open_slots["UTIL"] > 0:
            return 1.0
        return BENCH_WEIGHT


def needs(my_players: list, league: League) -> Needs:
    counts = {g: sum(1 for p in my_players if p.group == g) for g in ("F", "D", "G")}
    open_f = league.slots.get("F", 0) - counts["F"]
    open_d = league.slots.get("D", 0) - counts["D"]
    extra = max(0, -open_f) + max(0, -open_d)
    open_util = max(0, league.slots.get("UTIL", 0) - extra)
    starters = sum(min(counts[g], league.slots.get(g, 0)) for g in counts) + min(extra, league.slots.get("UTIL", 0))
    bench = league.slots.get("BE", 0) - (len(my_players) - starters)
    return Needs(counts=counts, open_slots={"F": max(0, open_f), "D": max(0, open_d),
                                            "G": max(0, league.slots.get("G", 0) - counts["G"]),
                                            "UTIL": open_util, "BE": max(0, bench)})


def expected_best_vor(pool: list, group: str, at_pick: int | None, now_pick: int) -> float:
    """Expected VOR of the best player at `group` still there at at_pick."""
    if at_pick is None:
        return 0.0
    cands = sorted((p for p in pool if p.group == group), key=lambda p: -p.vor)[:25]
    exp, none_left = 0.0, 1.0
    for p in cands:
        pa = p_available(p.market, at_pick, now_pick)
        exp += none_left * pa * p.vor
        none_left *= 1 - pa
    return exp


@dataclass
class Rec:
    player: object
    score: float
    dropoff: float
    p_next: float    # chance he's still there at my next contested pick
    need_w: float


def recommend(available: list, my_players: list, league: League, current_pick: int, n: int = 10) -> list:
    nd = needs(my_players, league)
    horizon = next_contested_pick(league.my_picks, current_pick)
    baseline = {g: expected_best_vor(available, g, horizon, current_pick) for g in ("F", "D", "G")}
    recs = []
    for p in available:
        w = nd.weight(p.group, league.pos_limits)
        if w <= 0:
            continue
        drop = max(0.0, p.vor - baseline[p.group])
        recs.append(Rec(player=p, score=w * (p.vor + DROPOFF_WEIGHT * drop), dropoff=drop,
                        p_next=p_available(p.market, horizon, current_pick) if horizon else 0.0, need_w=w))
    recs.sort(key=lambda r: -r.score)
    return recs[:n]


def goalie_plan(available: list, league: League, current_pick: int) -> list:
    """Plain-language goalie read, generated from the numbers."""
    gs = sorted((p for p in available if p.group == "G"), key=lambda p: -p.vor)
    sk = sorted((p for p in available if p.group != "G"), key=lambda p: -p.vor)
    if not gs or not sk:
        return []
    lines = ["Top goalie left: %s (VOR %+.0f, ADP %.0f) vs top skater %s (VOR %+.0f)."
             % (gs[0].name, gs[0].vor, gs[0].market, sk[0].name, sk[0].vor)]
    for pk in [p for p in league.my_picks if p >= current_pick][:4]:
        e = expected_best_vor(available, "G", pk, current_pick)
        lines.append("Expected best G VOR still there at pick %d: %+.0f" % (pk, e))
    lines.append("Replacement-level G still scores a lot here; G VOR is the gap over the ~%dth goalie."
                 % (league.size * league.slots.get("G", 0) + 1))
    return lines
