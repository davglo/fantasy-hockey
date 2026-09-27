"""ESPN fantasy API: auth from .env, fetches via curl, disk caches, settings parsing."""
from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fh import config

log = logging.getLogger(__name__)


class FetchError(RuntimeError):
    pass


def load_env(path: Path = config.ENV_FILE) -> dict:
    if not path.exists():
        raise FetchError(".env missing at %s" % path)
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for key in ("ESPN_S2", "ESPN_SWID"):
        if not env.get(key):
            raise FetchError("%s missing from .env" % key)
    return env


def _curl(url: str, headers: dict | None = None, auth: bool = False, timeout: int = 20) -> dict:
    # Headers go through a curl config on stdin so cookies never appear in argv / ps output.
    lines = ['url = "%s"' % url, "silent", "show-error", "compressed", "max-time = %d" % timeout,
             'write-out = "\\n%{http_code}"']
    hdrs = dict(headers or {})
    if auth:
        env = load_env()
        hdrs["Cookie"] = "espn_s2=%s; SWID=%s" % (env["ESPN_S2"], env["ESPN_SWID"])
    for k, v in hdrs.items():
        lines.append('header = "%s: %s"' % (k, v.replace("\\", "\\\\").replace('"', '\\"')))
    try:
        r = subprocess.run(["curl", "-K", "-"], input="\n".join(lines), capture_output=True,
                           text=True, timeout=timeout + 5)
    except subprocess.TimeoutExpired:
        raise FetchError("timeout fetching %s" % url.split("?")[0])
    if r.returncode != 0 or "\n" not in r.stdout:
        raise FetchError("curl failed (%s) for %s" % (r.returncode, url.split("?")[0]))
    body, code = r.stdout.rsplit("\n", 1)
    if code == "401":
        raise FetchError("401 from ESPN: cookies in .env rejected or expired")
    if code != "200":
        raise FetchError("HTTP %s for %s" % (code, url.split("?")[0]))
    return json.loads(body)


def cached(name: str, ttl: float, fn):
    """Return fn() cached on disk for ttl seconds; serve stale cache if fn fails."""
    path = config.STATE / ("%s_cache.json" % name)
    if path.exists() and time.time() - path.stat().st_mtime < ttl:
        return json.loads(path.read_text())
    try:
        data = fn()
    except FetchError as e:
        if path.exists():
            log.warning("%s fetch failed (%s); using stale cache", name, e)
            return json.loads(path.read_text())
        raise
    config.STATE.mkdir(exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def fetch_league(views: tuple = ("mSettings", "mTeam", "mDraftDetail", "mStatus"), ttl: float = 600) -> dict:
    q = "&".join("view=" + v for v in views)
    return cached("league", ttl, lambda: _curl(config.LEAGUE_URL + "?" + q, auth=True))


def fetch_draft_live() -> dict:
    """Uncached: draft picks + settings (pick order is re-randomized before the draft)."""
    return _curl(config.LEAGUE_URL + "?view=mDraftDetail&view=mSettings&view=mTeam", auth=True, timeout=8)


def fetch_pool(limit: int = 1200, ttl: float = 6 * 3600) -> list:
    # Sorted by % owned: the draft-rank sort leaves out many rosterable D (Slavin, Chiarot...).
    flt = {"players": {"limit": limit, "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
    data = cached("pool", ttl, lambda: _curl(config.POOL_URL + "?view=kona_player_info",
                                             headers={"X-Fantasy-Filter": json.dumps(flt)}))
    return data["players"]


def fetch_players_by_id(ids: list) -> list:
    flt = {"players": {"filterIds": {"value": list(ids)}, "limit": len(ids)}}
    return _curl(config.POOL_URL + "?view=kona_player_info",
                 headers={"X-Fantasy-Filter": json.dumps(flt)})["players"]


def fetch_pro_teams(ttl: float = 7 * 86400) -> dict:
    data = cached("proteams", ttl, lambda: _curl(config.API + "?view=proTeamSchedules_wl"))
    return {t["id"]: t["abbrev"] for t in data["settings"]["proTeams"]}


@dataclass
class League:
    name: str
    size: int
    scoring_type: str
    scoring: dict            # statId -> points
    slots: dict              # slot name -> count (non-zero only)
    pos_limits: dict         # slot name -> max rostered (-1/0 = none)
    draft_type: str
    draft_time: datetime | None
    time_per_pick: int
    teams: dict              # teamId -> team name
    my_team_id: int | None
    pick_teams: list         # teamId for overall pick 1..N (from draftDetail, else snake of pickOrder)
    rounds: int
    auction_budget: int
    playoff_teams: int
    in_progress: bool = False
    drafted: bool = False
    picks: list = field(default_factory=list)  # [(overall, teamId, playerId)] made so far

    @property
    def my_picks(self) -> list:
        return [i + 1 for i, t in enumerate(self.pick_teams) if t == self.my_team_id]

    @property
    def my_slot(self) -> int | None:
        return self.my_picks[0] if self.my_picks else None


def snake_order(order: list, rounds: int, snake: bool = True) -> list:
    out = []
    for r in range(rounds):
        out += list(reversed(order)) if snake and r % 2 else list(order)
    return out


def parse_league(raw: dict, swid: str | None = None) -> League:
    s = raw["settings"]
    sc = s["scoringSettings"]
    rs = s["rosterSettings"]
    ds = s["draftSettings"]
    slots = {config.SLOT_NAMES.get(int(k), k): v for k, v in rs["lineupSlotCounts"].items() if v}
    rounds = sum(v for k, v in slots.items() if k != "IR")
    my_team = None
    if swid:
        for t in raw.get("teams", []):
            if swid.upper() in [o.upper() for o in t.get("owners", [])]:
                my_team = t["id"]
    dd = raw.get("draftDetail", {}) or {}
    picks_raw = sorted(dd.get("picks", []), key=lambda p: p["overallPickNumber"])
    if picks_raw:
        pick_teams = [p["teamId"] for p in picks_raw]
    else:
        pick_teams = snake_order(ds["pickOrder"], rounds, ds["type"] == "SNAKE")
    made = [(p["overallPickNumber"], p["teamId"], p["playerId"]) for p in picks_raw if p.get("playerId", -1) > 0]
    date_ms = ds.get("date")
    return League(
        name=s["name"], size=s["size"], scoring_type=sc["scoringType"],
        scoring={**{i["statId"]: i["points"] for i in sc["scoringItems"]}, **config.SCORING_OVERRIDES},
        slots=slots,
        pos_limits={config.SLOT_NAMES.get(int(k), k): v for k, v in rs.get("positionLimits", {}).items() if v > 0},
        draft_type=ds["type"],
        draft_time=datetime.fromtimestamp(date_ms / 1000, tz=timezone.utc) if date_ms else None,
        time_per_pick=ds.get("timePerSelection", 60),
        teams={t["id"]: t.get("name") or t.get("abbrev") for t in raw.get("teams", [])},
        my_team_id=my_team, pick_teams=pick_teams, rounds=rounds,
        auction_budget=ds.get("auctionBudget", 0),
        playoff_teams=s["scheduleSettings"].get("playoffTeamCount", 0),
        in_progress=bool(dd.get("inProgress")), drafted=bool(dd.get("drafted")), picks=made,
    )


def fantasy_points(stats: dict, scoring: dict) -> float:
    return sum(scoring.get(int(k), 0) * v for k, v in stats.items())
