"""Projection accuracy tracker: which source predicts this league's scoring best?

Every build snapshots each player's per-game prediction from every source plus his cumulative actual
fantasy points and games (one file per day, local only - it contains workbook-derived numbers). A snapshot is
graded once a later snapshot >= WINDOW days on exists: realized pts/GP over that window vs each prediction."""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from fh import config

log = logging.getLogger(__name__)

LOG_DIR = config.STATE / "projection_log"
WINDOW = 7          # days a prediction is graded over
MIN_WINDOW_GP = 2   # games a player must play in the window to be graded
SOURCES = ("model", "sheet", "espn", "pace", "l15", "consensus")


def log_snapshot(players: list, day: date) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    snap = {}
    for p in players:
        src = dict(p.sources or {})
        src["consensus"] = p.consensus
        snap[str(p.id)] = {"name": p.name, "group": p.group, "cum_fp": p.act_fp, "cum_gp": p.act_gp,
                           **{k: (round(v, 4) if v is not None else None) for k, v in src.items()}}
    (LOG_DIR / ("%s.json" % day.isoformat())).write_text(json.dumps(snap))


def _load(day: date) -> dict | None:
    try:
        return json.loads((LOG_DIR / ("%s.json" % day.isoformat())).read_text())
    except (FileNotFoundError, ValueError):
        return None


def evaluate(today: date) -> dict:
    """Mean absolute error (pts per game, weighted by games played) per source, overall and by F/D/G."""
    days = sorted(date.fromisoformat(f.stem) for f in LOG_DIR.glob("*.json")) if LOG_DIR.exists() else []
    err = {s: {"all": [0.0, 0], "F": [0.0, 0], "D": [0.0, 0], "G": [0.0, 0]} for s in SOURCES}
    windows = 0
    for d in days:
        later = [x for x in days if x >= d + timedelta(days=WINDOW)]
        if not later:
            continue
        a, b = _load(d), _load(later[0])
        if not a or not b:
            continue
        windows += 1
        for pid, r in a.items():
            r2 = b.get(pid)
            if not r2:
                continue
            gp = r2["cum_gp"] - r["cum_gp"]
            if gp < MIN_WINDOW_GP:
                continue
            realized = (r2["cum_fp"] - r["cum_fp"]) / gp
            for s in SOURCES:
                if r.get(s) is None:
                    continue
                for k in ("all", r["group"]):
                    err[s][k][0] += abs(r[s] - realized) * gp
                    err[s][k][1] += gp
    first = days[0] + timedelta(days=WINDOW) if days else today + timedelta(days=WINDOW)
    table = {s: {k: (round(v[0] / v[1], 3) if v[1] else None) for k, v in g.items()} for s, g in err.items()}
    games = {s: err[s]["all"][1] for s in SOURCES}
    graded = {s: v for s, v in table.items() if v["all"] is not None}
    best = min(graded, key=lambda s: graded[s]["all"]) if graded else None
    return {"table": table, "games": games, "windows": windows, "best": best,
            "first_grade": first.isoformat(), "snapshots": len(days)}
