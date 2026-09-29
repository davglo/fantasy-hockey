"""ESPN market value: how leaguemates see a player, for judging whether a trade would be accepted.

Market rank = ESPN preseason rank/ADP, shifting toward ESPN's season player-rater rank as games are played.
Rank is mapped onto the league's own points curve, then raised to a power so one star outweighs two
middling players (the consolidation premium every trade market charges)."""
from __future__ import annotations

STAR_POWER = 1.3
RATER_WEIGHT_MAX = 0.7    # never fully abandon preseason perception
RATER_FULL_GP = 25        # games played for the rater to reach full weight
REPLACEMENT_RANK = 160    # ~ players rostered as starters + key bench across the league


def rater_ranks(pool: list) -> dict:
    """playerId -> ESPN season player-rater total ranking (from the leaguedefaults pool)."""
    out = {}
    for e in pool:
        r = ((e.get("ratings") or {}).get("0") or {}).get("totalRanking")
        if r:
            out[e["player"]["id"]] = r
    return out


def market_rank(p, rater: dict) -> float:
    pre = p.base.market                      # ESPN ADP, else ESPN rank, else 400
    r = rater.get(p.id)
    if r is None or p.act_gp == 0:
        return pre
    w = RATER_WEIGHT_MAX * min(1.0, p.act_gp / RATER_FULL_GP)
    return (1 - w) * pre + w * r


def trade_values(players: list, rater: dict, points) -> dict:
    """playerId -> market trade value. points(p) = the league points scale used to price each rank slot."""
    curve = sorted((points(p) for p in players), reverse=True)
    repl = curve[min(len(curve) - 1, REPLACEMENT_RANK)]
    ordered = sorted(players, key=lambda p: market_rank(p, rater))
    return {p.id: max(0.0, curve[i] - repl) ** STAR_POWER for i, p in enumerate(ordered)}
