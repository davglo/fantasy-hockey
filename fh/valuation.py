"""Player values under the league's scoring: projection -> value over replacement (VOR)."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from fh import config
from fh.espn import League, fantasy_points
from fh.rankings import initial_key, norm_name, norm_team

log = logging.getLogger(__name__)


@dataclass
class Player:
    id: int
    name: str
    group: str               # F / D / G (league has no C/LW/RW slots)
    positions: str           # display, e.g. "C/LW"
    team: str
    injury: str
    fp: float                # projected fantasy points (workbook if matched, else ESPN)
    source: str              # "sheet" or "espn"
    espn_fp: float
    gp: float | None
    espn_rank: int | None
    espn_adp: float | None
    sheet_rank: int | None
    boost: str
    pct_owned: float
    vor: float = 0.0
    overall: int = 0         # rank by VOR

    @property
    def fp_per_gp(self) -> float | None:
        return self.fp / self.gp if self.gp else None

    @property
    def market(self) -> float:
        """Expected overall pick in this league: ESPN ADP (what leaguemates' draft room shows)."""
        if self.espn_adp and self.espn_adp > 0:
            return self.espn_adp
        if self.espn_rank:
            return float(self.espn_rank)
        return 400.0


def _group(slots: list, default_pos: int) -> str:
    if 5 in slots or default_pos == 5:
        return "G"
    if 4 in slots and not ({0, 1, 2, 3} & set(slots)):
        return "D"
    return "F"


HAT = 28  # hat tricks: neither the workbook nor ESPN projections include them


def p_hat_trick(goals: float, gp: float) -> float:
    """P(3+ goals in a game), goals per game ~ Poisson(goals/gp)."""
    lam = goals / gp
    return 1 - math.exp(-lam) * (1 + lam + lam * lam / 2)


def hat_trick_calibration(pool: list) -> float:
    """Actual / Poisson-predicted hat tricks over last season's skater actuals (computed each build)."""
    actual = pred = 0.0
    for entry in pool:
        pl = entry["player"]
        if pl.get("defaultPositionId") == 5:
            continue
        st = next((s["stats"] for s in pl.get("stats", []) if s.get("seasonId") == config.SEASON - 1
                   and s.get("statSourceId") == 0 and s.get("statSplitTypeId") == 0), None)
        gp = (st or {}).get("34") or 0
        if gp:
            actual += st.get(str(HAT), 0)
            pred += gp * p_hat_trick(st.get("13", 0), gp)
    k = actual / pred if pred > 20 else 1.0
    return min(max(k, 0.5), 2.0)


def _sheet_group(pos: str) -> str:
    return pos if pos in ("G", "D") else "F"


def rescore_delta(row: dict, league_scoring: dict, sheet_scoring: dict) -> float:
    """FP change for a workbook row when league points differ from the workbook's points."""
    return sum((league_scoring.get(sid, 0) - sheet_scoring.get(sid, 0)) * v
               for sid, v in row.get("stats", {}).items() if sid in sheet_scoring)


def build_players(pool: list, sheet: list, league: League, pro_teams: dict,
                  sheet_scoring: dict | None = None) -> list:
    by_key_team = {(r["key"], r["team"]): r for r in sheet}
    by_key = {}
    for r in sheet:
        by_key.setdefault(r["key"], []).append(r)
    by_initial = {}
    for r in sheet:
        by_initial.setdefault((initial_key(r["name"]), r["team"]), []).append(r)
    hat_pts = league.scoring.get(HAT, 0)
    hat_k = hat_trick_calibration(pool) if hat_pts else 0.0
    if hat_pts:
        log.info("hat tricks: +%g pts each, Poisson calibration k=%.2f vs last season", hat_pts, hat_k)
    players, matched = [], 0
    for entry in pool:
        pl = entry["player"]
        proj = next((s["stats"] for s in pl.get("stats", [])
                     if s.get("seasonId") == config.SEASON and s.get("statSourceId") == 1
                     and s.get("statSplitTypeId") == 0), None)
        espn_fp = fantasy_points(proj, league.scoring) if proj else 0.0
        team = norm_team(pro_teams.get(pl.get("proTeamId"), ""))
        key = norm_name(pl["fullName"])
        row = by_key_team.get((key, team))
        if row is None and len(by_key.get(key, [])) == 1:
            row = by_key[key][0]  # team changed since the sheet was made
        if row is None and len(by_initial.get((initial_key(pl["fullName"]), team), [])) == 1:
            row = by_initial[(initial_key(pl["fullName"]), team)][0]
        slots = pl.get("eligibleSlots", [])
        group = _group(slots, pl.get("defaultPositionId"))
        if row is not None and _sheet_group(row["pos"]) != group:
            row = None  # same name/initial, different player (e.g. Jordie vs Jamie Benn)
        matched += row is not None
        own = pl.get("ownership", {}) or {}
        rank = (pl.get("draftRanksByRankType", {}) or {}).get("STANDARD", {}).get("rank")
        gp = row["gp"] if row else (proj.get("34") or proj.get("30") if proj else None)
        goals = row["g"] if row else (proj or {}).get("13", 0)
        hat = hat_pts * hat_k * gp * p_hat_trick(goals, gp) if hat_pts and gp and goals else 0.0
        if proj and str(HAT) in proj:
            espn_fp -= hat_pts * proj[str(HAT)]  # avoid double counting if ESPN ever projects it
        pos = "/".join(config.SLOT_NAMES[s] for s in sorted(slots) if s in (0, 1, 2, 4, 5)) or group
        players.append(Player(
            id=pl["id"], name=pl["fullName"], group=group, positions=pos, team=team,
            injury=pl.get("injuryStatus") or "ACTIVE",
            fp=(row["fp"] + rescore_delta(row, league.scoring, sheet_scoring or {}) if row else espn_fp) + hat, source="sheet" if row else "espn", espn_fp=espn_fp + hat,
            gp=gp, espn_rank=rank, espn_adp=own.get("averageDraftPosition") or None,
            sheet_rank=row["sheet_rank"] if row else None, boost=row["boost"] if row else "",
            pct_owned=own.get("percentOwned", 0.0),
        ))
    log.info("workbook matched %d of %d ESPN players (%d workbook rows)", matched, len(players), len(sheet))
    return players


def starter_counts(league: League) -> dict:
    n = league.size
    return {"F": n * league.slots.get("F", 0), "D": n * league.slots.get("D", 0),
            "G": n * league.slots.get("G", 0), "UTIL": n * league.slots.get("UTIL", 0)}


def replacement_levels(players: list, league: League) -> dict:
    """FP of the best non-starter at each position once every team's lineup (incl. UTIL) is filled."""
    need = starter_counts(league)
    by = {g: sorted((p.fp for p in players if p.group == g), reverse=True) for g in ("F", "D", "G")}
    taken = {g: min(need[g], len(by[g])) for g in by}
    # UTIL takes the best leftover skaters, F or D.
    for _ in range(need["UTIL"]):
        nf = by["F"][taken["F"]] if taken["F"] < len(by["F"]) else float("-inf")
        nd = by["D"][taken["D"]] if taken["D"] < len(by["D"]) else float("-inf")
        taken["F" if nf >= nd else "D"] += 1
    return {g: (by[g][taken[g]] if taken[g] < len(by[g]) else 0.0) for g in by}


def value(players: list, league: League) -> dict:
    repl = replacement_levels(players, league)
    for p in players:
        p.vor = p.fp - repl[p.group]
    for i, p in enumerate(sorted(players, key=lambda p: -p.vor), 1):
        p.overall = i
    return repl
