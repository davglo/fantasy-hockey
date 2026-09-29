"""In-season league state: calendar (scoring period = one day), matchups, rosters, free agents."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from fh import config, espn
from fh.board import ET

log = logging.getLogger(__name__)

IR_SLOT, BENCH_SLOT = 8, 7


@dataclass
class Calendar:
    start: date                    # date of scoring period 1
    final_period: int
    team_games: dict               # NHL team abbrev -> set of scoring periods with a game
    opponents: dict                # (abbrev, period) -> opponent abbrev
    matchups: dict                 # matchup period -> [scoring periods]

    def period_of(self, d: date) -> int:
        return (d - self.start).days + 1

    def date_of(self, period: int) -> date:
        return self.start + timedelta(days=period - 1)

    def matchup_of(self, period: int) -> int | None:
        return next((m for m, ps in self.matchups.items() if period in ps), None)

    def games(self, team: str, periods) -> int:
        g = self.team_games.get(team, set())
        return sum(1 for p in periods if p in g)


def build_calendar(pro_raw: dict, n_matchups: int, final_period: int) -> Calendar:
    abbrev = {t["id"]: t["abbrev"] for t in pro_raw["settings"]["proTeams"]}
    team_games, opponents, start = {}, {}, None
    for t in pro_raw["settings"]["proTeams"]:
        for sp, games in (t.get("proGamesByScoringPeriod") or {}).items():
            for g in games:
                p = int(sp)
                team_games.setdefault(t["abbrev"], set()).add(p)
                opp = g["awayProTeamId"] if g["homeProTeamId"] == t["id"] else g["homeProTeamId"]
                opponents[(t["abbrev"], p)] = abbrev.get(opp, "?")
                d = datetime.fromtimestamp(g["date"] / 1000, tz=ET).date() - timedelta(days=p - 1)
                start = start or d
    # Matchup weeks: the last one holds the final period and starts on that week's Monday; earlier
    # ones step back 7 days; matchup 1 absorbs the opening partial week (ESPN: Sep 29-Oct 11).
    cal = Calendar(start=start, final_period=final_period, team_games=team_games, opponents=opponents, matchups={})
    last_monday = cal.period_of(cal.date_of(final_period) - timedelta(days=cal.date_of(final_period).weekday()))
    starts = {m: last_monday - 7 * (n_matchups - m) for m in range(1, n_matchups + 1)}
    starts[1] = 1
    for m in range(1, n_matchups + 1):
        end = starts[m + 1] - 1 if m < n_matchups else final_period
        cal.matchups[m] = list(range(starts[m], end + 1))
    return cal


@dataclass
class Team:
    id: int
    name: str
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float
    roster: list = field(default_factory=list)   # [(playerPoolEntry dict, lineupSlotId)]
    adds_by_matchup: dict = field(default_factory=dict)  # matchup period -> acquisitions made


@dataclass
class Matchup:
    period: int
    home: int
    away: int
    home_pts: float
    away_pts: float
    winner: str          # UNDECIDED / HOME / AWAY / TIE
    playoff: bool


@dataclass
class LeagueState:
    league: espn.League
    today_period: int
    current_matchup: int
    regular_matchups: int
    teams: dict
    schedule: list
    free_agents: list    # playerPoolEntry-like dicts


def fetch_state(swid: str) -> LeagueState:
    views = ("mSettings", "mTeam", "mRoster", "mMatchupScore", "mSchedule", "mStatus", "mDraftDetail")
    raw = espn.cached("inseason", 900, lambda: espn._curl(
        config.LEAGUE_URL + "?" + "&".join("view=" + v for v in views), auth=True))
    lg = espn.parse_league(raw, swid)
    teams = {}
    for t in raw["teams"]:
        rec = t.get("record", {}).get("overall", {})
        teams[t["id"]] = Team(
            id=t["id"], name=t.get("name") or t.get("abbrev"), wins=rec.get("wins", 0), losses=rec.get("losses", 0),
            ties=rec.get("ties", 0), points_for=rec.get("pointsFor", 0.0), points_against=rec.get("pointsAgainst", 0.0),
            roster=[(e["playerPoolEntry"], e["lineupSlotId"]) for e in t.get("roster", {}).get("entries", [])],
            adds_by_matchup={int(k): v for k, v in
                             ((t.get("transactionCounter") or {}).get("matchupAcquisitionTotals") or {}).items()})
    sched = [Matchup(period=m["matchupPeriodId"], home=m["home"]["teamId"], away=m.get("away", {}).get("teamId"),
                     home_pts=m["home"].get("totalPoints", 0.0), away_pts=m.get("away", {}).get("totalPoints", 0.0),
                     winner=m.get("winner", "UNDECIDED"), playoff=m.get("playoffTierType", "NONE") != "NONE")
             for m in raw.get("schedule", []) if m.get("away")]
    flt = {"players": {"filterStatus": {"value": ["FREEAGENT", "WAIVERS"]}, "limit": 400,
                       "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
    fa = espn.cached("free_agents", 900, lambda: espn._curl(
        config.LEAGUE_URL + "?view=kona_player_info", headers={"X-Fantasy-Filter": json.dumps(flt)}, auth=True))
    return LeagueState(
        league=lg, today_period=max(1, raw.get("scoringPeriodId", 1)),
        current_matchup=raw["status"].get("currentMatchupPeriod", 1),
        regular_matchups=raw["settings"]["scheduleSettings"]["matchupPeriodCount"],
        teams=teams, schedule=sched, free_agents=fa["players"])


def fetch_calendar(state: LeagueState) -> Calendar:
    pro = espn.cached("proteams", 86400, lambda: espn._curl(config.API + "?view=proTeamSchedules_wl"))
    rounds = 0
    n = state.league.playoff_teams
    while n > 1:
        rounds, n = rounds + 1, n // 2
    final = max(int(p) for t in pro["settings"]["proTeams"] for p in (t.get("proGamesByScoringPeriod") or {}))
    return build_calendar(pro, state.regular_matchups + rounds, final)
