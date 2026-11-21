"""Shared test fixtures: a minimal ESPN league payload and player factory."""
from __future__ import annotations

from fh import espn
from fh.valuation import Player

SWID = "{AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA}"


def raw_league(order=(4, 3, 8, 7, 6, 1, 2, 5), made=(), in_progress=False, drafted=False, rounds=23):
    """Minimal ESPN league payload shaped like the real mSettings+mTeam+mDraftDetail response."""
    snake = espn.snake_order(list(order), rounds)
    picks = [{"overallPickNumber": i + 1, "teamId": t, "playerId": made[i] if i < len(made) else -1}
             for i, t in enumerate(snake)]
    return {
        "settings": {
            "name": "Test", "size": 8,
            "scoringSettings": {"scoringType": "H2H_POINTS", "scoringItems": [{"statId": 13, "points": 2}]},
            "rosterSettings": {"lineupSlotCounts": {"3": 9, "4": 5, "5": 2, "6": 2, "7": 5, "8": 2},
                               "positionLimits": {"5": 4}},
            "draftSettings": {"type": "SNAKE", "pickOrder": list(order), "timePerSelection": 60,
                              "date": 1790555400000, "auctionBudget": 260},
            "scheduleSettings": {"playoffTeamCount": 4},
        },
        "teams": [{"id": t, "name": "T%d" % t, "owners": [SWID] if t == 5 else ["{X}"]} for t in range(1, 9)],
        "draftDetail": {"picks": picks, "inProgress": in_progress, "drafted": drafted},
    }


def mk(i, group="F", fp=100.0, adp=50.0):
    return Player(id=i, name="P%d" % i, group=group, positions=group, team="X", injury="ACTIVE", fp=fp,
                  source="sheet", espn_fp=fp, gp=82, espn_rank=None, espn_adp=adp, sheet_rank=None,
                  boost="", pct_owned=50.0)
