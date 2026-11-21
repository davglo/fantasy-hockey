"""Compute every dashboard number on each build into one context dict (also written to state/context.json)."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from types import SimpleNamespace

from fh import absence, accuracy, advice, config, engine, espn, news, rankings, season, valuation, vegas
from fh.espn import fantasy_points
from fh.config import ET

log = logging.getLogger(__name__)

GRADES = ["A", "A-", "B+", "B", "C+", "C", "D+", "D"]
DISAGREE = 0.25   # flag a player when his projection sources differ by more than this share of the consensus


HOT_WINDOWS = (("l7", 3), ("l15", 6), ("l30", 10), ("season", 10))   # (form key, minimum games)


def _health(ps: list) -> list:
    """Flagged players (injured / IR / suspended) with whether a stated return date is already in the projection."""
    return [{"name": p.name, "pos": p.base.positions, "team": p.team, "status": p.injury if p.injury != "ACTIVE" else "IR slot",
             "note": p.out_note, "counted": bool(p.out_until) or p.slot == season.IR_SLOT or p.injury == "INJURY_RESERVE"}
            for p in sorted(ps, key=lambda p: -p.rate) if p.injury not in ("ACTIVE", "DAY_TO_DAY") or p.slot == season.IR_SLOT]


def _pl(p, cal=None, periods=None, extra=None) -> dict:
    src = p.sources or {}
    d = {"id": p.id, "name": p.name, "pos": p.base.positions, "group": p.group, "team": p.team,
         "injury": "" if p.injury == "ACTIVE" else p.injury, "rate": round(p.rate, 2),
         "src": {k: (round(v, 2) if v is not None else None) for k, v in src.items()},
         "cons": round(p.consensus, 2) if p.consensus is not None else None, "dis": round(p.disagreement, 2),
         "exp_game": round(p.exp_game(), 2), "gp": p.act_gp, "fp": round(p.act_fp, 1), "f": p.form_list(), "hot": p.is_hot(),
         "out": p.out_note}
    if cal is not None:
        d["ros"] = round(p.ros(cal, periods), 1)
    d.update(extra or {})
    return d


def _span(cal, periods) -> str:
    if not periods:
        return "-"
    return "%s-%s" % (cal.date_of(periods[0]).strftime("%b %-d"), cal.date_of(periods[-1]).strftime("%b %-d"))


def _nhl_week(cal, periods, today) -> dict:
    """Every NHL team's games this matchup week, by day: 'BOS' = home vs BOS, '@BOS' = at BOS."""
    teams = sorted(cal.team_games, key=lambda t: (-cal.games(t, periods), t))
    light = set(advice.light_days(cal, periods))
    return {"days": [{"date": cal.date_of(d).strftime("%a %-d"), "past": d < today,
                      "n": sum(1 for t in teams if d in cal.team_games[t]), "light": d in light} for d in periods],
            "teams": [{"team": t, "games": cal.games(t, periods),
                       "cells": [("@" if (t, d) in cal.away else "") + cal.opponents[(t, d)]
                                 if d in cal.team_games[t] else "" for d in periods]} for t in teams]}


def _move(m) -> dict:
    return {"add": m.add.name, "add_pos": m.add.group, "add_team": m.add.team, "drop": m.drop.name,
            "gain": round(m.gain, 1), "days": m.days, "waivers": m.add.status == "WAIVERS"}


def _waiver(ms, status: str) -> str:
    if status != "WAIVERS":
        return ""
    if not ms:
        return "on waivers"
    return "clears " + datetime.fromtimestamp(ms / 1000, tz=ET).strftime("%a %-I%p").replace("AM", "am").replace("PM", "pm")


def _news(i) -> dict:
    return {"player": i.player, "summary": i.summary, "credit": news.credit(i), "link": i.link,
            "insider": i.insider, "kinds": sorted(i.kinds), "date": i.published.astimezone(ET).strftime("%b %-d %-I:%M%p")}


STAT_LINE = (("13", "G"), ("14", "A"), ("29", "SOG"), ("32", "BLK"), ("31", "HIT"), ("1", "W"), ("6", "SV"), ("4", "GA"), ("7", "SO"))


def _perf(p, st: dict, pts: float) -> dict:
    line = ", ".join("%d %s" % (st[k], lab) for k, lab in STAT_LINE if st.get(k))
    return _pl(p, extra={"pts": round(pts, 1), "line": line, "own_chg": round(p.pct_change, 1)})


def _po_targets(fas, players, me, teams, cal, po_periods, state) -> list:
    """Players whose playoff-week games land on my open-slot days (FAs to grab, others to trade for)."""
    rows = []
    # Realistic trade tier: nobody moves a franchise player. Skip the top 60 by ESPN market (ADP/rank) and each
    # team's own top 5 by projection.
    core = set()
    for tid in {q.owner for q in players if q.owner not in (None, me)}:
        core |= {q.id for q in sorted((q for q in players if q.owner == tid), key=lambda q: -q.exp_game())[:5]}
    for p in list(fas) + [q for q in players if q.owner != me]:
        t = teams.get(p.team)
        if not t:
            continue
        fit = t["g_fit"] if p.group == "G" else t["sk_fit"]
        if fit:
            rows.append({"name": p.name, "team": p.team, "group": p.group, "fit": fit, "games": t["games"],
                         "usable_pts": round(fit * p.exp_game(), 1),
                         "owner": "FA" if p.owner is None else state.teams[p.owner].name,
                         "tradeable": p.owner is not None and p.base.market > 60 and p.id not in core})
    rows.sort(key=lambda r: -r["usable_pts"])
    return {"fa": [r for r in rows if r["owner"] == "FA"][:8],
            "trade": [r for r in rows if r["owner"] != "FA" and r["tradeable"]][:8]}


def _drop_caution(drop, mine: list) -> str:
    """Injury statuses are flagged, never auto-discounted: warn when dropping depth behind a flagged player."""
    flagged = [p for p in mine if p.group == drop.group and p.id != drop.id and p.slot != season.IR_SLOT
               and p.injury not in ("ACTIVE", "DAY_TO_DAY")]
    if not flagged:
        return ""
    return " Caution: %s listed %s - check news before dropping %s depth." % (
        ", ".join(p.name for p in flagged), "/".join(sorted({p.injury for p in flagged})), drop.group)


def build(swid: str) -> dict:
    state = season.fetch_state(swid)
    cal = season.fetch_calendar(state)
    if config.WEEKLY_MATCHUPS:   # Mon-Sun weeks (ESPN settings: periodTypeId week, 1 week per matchup)
        state.current_matchup = cal.matchup_of(state.today_period) or state.current_matchup
    cal_warning = season.calendar_mismatch(state, cal)
    if cal_warning:
        # ESPN decides who you play and what's banked; follow its live matchup and say so loudly.
        log.warning("CALENDAR MISMATCH: %s - following ESPN's live matchup; fix config.WEEKLY_MATCHUPS", cal_warning)
        state.current_matchup = state.espn_matchup
    lg = state.league
    me = lg.my_team_id
    sheet = rankings.load()
    try:
        sheet_pts = rankings.sheet_scoring()
    except Exception as e:  # noqa: BLE001 - no workbook (cloud build) -> ESPN projections only
        log.info("no workbook scoring (%s); ESPN projections only", e)
        sheet_pts = {}
    pro = espn.fetch_pro_teams()

    owners, entries = {}, []
    for tid, t in state.teams.items():
        for ppe, slot in t.roster:
            owners[ppe["player"]["id"]] = (tid, slot)
            entries.append(ppe)
    base = valuation.build_players(entries + state.free_agents, sheet, lg, pro, sheet_pts)
    players = engine.make_players(entries, base, lg, owners)
    fas = engine.make_players(state.free_agents, base, lg)
    rosters = {tid: [p for p in players if p.owner == tid] for tid in state.teams}
    mine = rosters[me]
    # Reported absences: games a player is stated to miss come out of every projection (mine and rivals').
    flagged = {p.id: p.name for p in players + fas if p.injury in absence.UNKNOWN_STATUS or p.slot == season.IR_SLOT}
    n_dated = absence.apply(players + fas, news.fetch_many(flagged), cal, cal.date_of(state.today_period))
    log.info("absences: %d flagged players, %d with a reported return date", len(flagged), n_dated)
    accuracy.log_snapshot(players + fas, cal.date_of(state.today_period))
    acc = accuracy.evaluate(cal.date_of(state.today_period))

    today = state.today_period
    cm = state.current_matchup
    this_week = [d for d in cal.matchups.get(cm, []) if d >= today]
    next_week = cal.matchups.get(cm + 1, [])
    ros_periods = [d for m in range(cm, state.regular_matchups + 1) for d in cal.matchups[m] if d >= today]

    team_rates = season.fetch_team_rates()
    cal.vegas = vegas.fetch(cal, [today, today + 1], team_rates, lg.scoring)
    proj = engine.project(state, cal, rosters)
    rank = sorted(proj.strength, key=lambda t: -proj.strength[t])
    my_rank = rank.index(me) + 1

    # This week's matchup, plus next week's so I can plan ahead
    def _matchup(m, periods):
        mt = next((x for x in state.schedule if x.period == m and me in (x.home, x.away)), None)
        if not mt:
            return None
        opp = mt.away if mt.home == me else mt.home
        my_pts, opp_pts = (mt.home_pts, mt.away_pts) if mt.home == me else (mt.away_pts, mt.home_pts)
        mu_me, mu_opp = proj.week_mu[(me, m)], proj.week_mu[(opp, m)]
        # Future weeks count IR players, matching engine.project.
        pool = (lambda ps: ps) if m > cm else engine.active
        days = []
        for d in periods:
            f = lambda p, d=d: p.exp_day(cal, d)
            a, sa = engine.best_lineup(pool(mine), lg.slots, f)
            b, sb = engine.best_lineup(pool(rosters[opp]), lg.slots, f)
            days.append({"date": cal.date_of(d).strftime("%a %b %-d"), "me": round(a, 1), "opp": round(b, 1),
                         "me_starts": len(sa), "opp_starts": len(sb), "stake": round(a + b, 1)})
        for d in sorted(days, key=lambda x: -x["stake"])[:2]:
            d["swing"] = True      # the two days with the most projected points at stake
        return {"opp": state.teams[opp].name, "opp_id": opp, "actual_me": my_pts, "actual_opp": opp_pts,
                "proj_me": round(mu_me, 1), "proj_opp": round(mu_opp, 1),
                "win_prob": round(engine.win_prob(mu_me, mu_opp, my_pts, opp_pts), 3),
                "starts_me": sum(d["me_starts"] for d in days), "starts_opp": sum(d["opp_starts"] for d in days),
                "days": days, "health_me": _health(mine), "health_opp": _health(rosters[opp]),
                "dates": "%s - %s" % (cal.date_of(cal.matchups[m][0]).strftime("%b %-d"),
                                      cal.date_of(cal.matchups[m][-1]).strftime("%b %-d"))}

    matchup = _matchup(cm, this_week)
    next_matchup = _matchup(cm + 1, next_week) if next_week else None
    week_after = cal.matchups.get(cm + 2, [])
    after_matchup = _matchup(cm + 2, week_after) if week_after else None

    def _recap(m):
        """Last finished matchup: result plus points left on my bench (actual lineups vs the best lineup in hindsight)."""
        mt = next((x for x in state.schedule if x.period == m and me in (x.home, x.away)), None)
        if not mt or mt.winner == "UNDECIDED":
            return None
        opp = mt.away if mt.home == me else mt.home
        mine_pts, opp_pts = (mt.home_pts, mt.away_pts) if mt.home == me else (mt.away_pts, mt.home_pts)
        won = {"HOME": mt.home, "AWAY": mt.away}.get(mt.winner)
        started = best_total = 0.0
        bench, days = [], []
        for d in cal.matchups[m]:
            rows = season.fetch_roster_day(me, d, lg.scoring, past=d < today)
            objs = [SimpleNamespace(id=i, name=n, group=g, slot=s, pts=pts) for i, (n, g, s, pts) in enumerate(rows)]
            got = sum(o.pts for o in objs if o.slot not in (season.BENCH_SLOT, season.IR_SLOT))
            best, _ = engine.best_lineup(objs, lg.slots, lambda o: o.pts)
            started, best_total = started + got, best_total + best
            days.append({"date": cal.date_of(d).strftime("%a %-d"), "left": round(max(0.0, best - got), 1)})
            bench += [(o.name, cal.date_of(d).strftime("%a %-d"), o.pts) for o in objs if o.slot == season.BENCH_SLOT and o.pts > 0]
        return {"matchup": m, "opp": state.teams[opp].name, "mine": round(mine_pts, 1), "opp_pts": round(opp_pts, 1),
                "result": "W" if won == me else "L" if won is not None else "T", "left_on_bench": round(max(0.0, best_total - started), 1),
                "days": [d for d in days if d["left"] > 0],
                "top_bench": [{"name": n, "date": d, "pts": round(pts, 1)} for n, d, pts in sorted(bench, key=lambda b: -b[2])[:3]]}
    recap = _recap(cm - 1) if cm > 1 else None

    def _trend(m, prob):
        """Win-chance history for the live matchup (one point per build, kept locally)."""
        path = config.STATE / "winprob_log.json"
        try:
            hist = json.loads(path.read_text())
        except (FileNotFoundError, ValueError):
            hist = []
        now_et = datetime.now(ET)
        hist = [h for h in hist if h["m"] == m]
        if hist and (now_et - datetime.fromisoformat(hist[-1]["t"])).total_seconds() < 2 * 3600:
            hist.pop()      # same slot (a manual re-run): replace
        hist = (hist + [{"m": m, "t": now_et.isoformat(), "p": prob}])[-40:]
        path.write_text(json.dumps(hist))
        return [{"label": datetime.fromisoformat(h["t"]).strftime("%a %-I%p"), "p": h["p"]} for h in hist]
    trend = _trend(cm, matchup["win_prob"]) if matchup else []

    # Lineup helper: today and tomorrow
    lineup = {}
    for label, d in (("today", today), ("tomorrow", today + 1)):
        issues, best, gain = advice.lineup_check(mine, lg, cal, d)
        lineup[label] = {"date": cal.date_of(d).strftime("%a %b %-d"), "period": d,
                         "issues": [{"severity": i.severity, "text": i.text} for i in issues],
                         "gain": round(gain, 1),
                         "playing": [_pl(p, extra={"opp": cal.opponents.get((p.team, d), "")})
                                     for p in mine if d in cal.team_games.get(p.team, ())],
                         "best": [p.name for p in best]}

    po_weeks = {m: cal.matchups[m] for m in sorted(cal.matchups) if m > state.regular_matchups}
    po_periods = [d for ps in po_weeks.values() for d in ps]
    value = advice.Valuer(lg, cal, ros_periods, po_periods)
    fa_recs = advice.free_agents(mine, [f for f in fas if f.group != "G"], value, this_week, next_week)   # skaters: goalies are managed separately
    plan_ahead = len(this_week) <= 2 and bool(next_week)   # weekend: plan next week's streams
    stream = advice.streaming(fas, cal, this_week, next_week, goalie_week=next_week if plan_ahead else this_week)
    adds_used = state.teams[me].adds_by_matchup.get(cm, 0)
    adds_left = max(0, config.ADDS_PER_MATCHUP - adds_used)
    plan_this = advice.stream_plan(mine, fas, value, this_week, adds_left)
    plan_next = advice.stream_plan(mine, fas, value, next_week, config.ADDS_PER_MATCHUP)
    po = advice.playoff_schedule(rosters, me, lg, cal, po_weeks)
    usable = advice.usable_games(mine, lg, cal, po_periods)

    # Where every team ranks by position; mine drives the headline
    lranks = advice.league_ranks(rosters, lg, cal, ros_periods)
    wyr = {g: d for g, d in lranks[me].items() if g not in ("need", "surplus")}
    light = advice.light_days(cal, ros_periods)
    quick = {p.id: value.quick(p) for p in mine}
    _, season_starters = engine.best_lineup(mine, lg.slots, lambda p: quick[p.id] + 1e-9)
    weakest = sorted(season_starters, key=lambda p: quick[p.id])[:3]

    # Drops
    drop_rank = advice.drop_ranking(mine, value)
    under = advice.underperformers(mine)

    # Recent free-agent performers: yesterday + last 7 days
    yday = today - 1
    while yday > 1 and not any(yday in g for g in cal.team_games.values()):
        yday -= 1     # no NHL games that day (e.g. Thanksgiving): show the last day with games
    fa_by_id = {p.id: p for p in fas}
    waiver_dates = {e["player"]["id"]: e.get("waiverProcessDate") for e in state.free_agents}
    perf_day, perf_7 = [], []
    if yday >= 1:
        for e in season.fetch_fa_day(yday):
            st = season.day_stats(e, yday)
            if st and e["player"]["id"] in fa_by_id:
                perf_day.append((fantasy_points(st, lg.scoring), fa_by_id[e["player"]["id"]], st))
    for e in state.free_agents:
        st = season.split_stats(e, 1)
        if st and e["player"]["id"] in fa_by_id:
            perf_7.append((fantasy_points(st, lg.scoring), fa_by_id[e["player"]["id"]], st))
    perf_day = sorted(perf_day, key=lambda t: -t[0])
    perf_day = perf_day[:10]
    perf_7 = sorted(perf_7, key=lambda t: -t[0])[:8]

    # News, opportunity alerts, goalies to add today
    now = news.now_utc()
    g_cands = [r["p"] for r in advice.goalies_today(fas, cal, today, {}, {})] + [p for p in mine if p.group == "G"]
    watch = news.watch_set(mine, players, fas, [r.p for r in fa_recs[:8]], g_cands)
    nws = news.fetch_many(watch)
    log.info("news: %d players watched, %d items", len(watch), sum(len(v) for v in nws.values()))
    game_day = cal.date_of(today)
    starting = {pid: news.start_status(items, game_day) for pid, items in nws.items()}
    # A goalie whose teammate is confirmed tonight is the backup.
    confirmed_teams = {p.team for p in players + fas if p.group == "G" and starting.get(p.id) == "confirmed"}
    for f in fas:
        if f.group == "G" and not starting.get(f.id) and f.team in confirmed_teams:
            starting[f.id] = "backup"
    g_today = advice.goalies_today(fas, cal, today, starting, {t: r["gf"] for t, r in team_rates.items()})[:8]
    # Hottest free-agent skaters by window (the Streaming tab re-sorts them by L7 / L15 / L30 / season)
    fmt_form = lambda p: {k: [round(r, 2), g] for k, (r, g) in (p.form or {}).items()}
    pool_fa = [f for f in fas if f.group != "G" and f.injury in ("ACTIVE", "DAY_TO_DAY") and cal.games(f.team, list(this_week) + list(next_week))]
    hot_pick = {}
    for key, min_gp in HOT_WINDOWS:
        for f in sorted((f for f in pool_fa if f.form_rate(key, min_gp) is not None), key=lambda f: -f.form_rate(key, min_gp))[:12]:
            hot_pick[f.id] = f
    trending = sorted((f for f in fas if f.pct_change >= 1.0), key=lambda f: -f.pct_change)[:8]
    # Goalie starts by day for the matchup week: who plays when (start status is only known for today)
    my_goalies = [p for p in engine.active(mine) if p.group == "G"]
    goalie_days = {"days": [cal.date_of(d).strftime("%a %-d") for d in this_week],
                   "rows": [{"name": g.name, "team": g.team, "out": g.out_note,
                             "cells": [{"opp": cal.opponents.get((g.team, d), "") if d in cal.team_games.get(g.team, ()) and not g.is_out(cal, d) else "",
                                        "status": starting.get(g.id, "") if d == today else ""} for d in this_week]} for g in my_goalies]}
    # League news: my players, big news on everyone else rostered here, and league-wide headlines
    by_id_all = {p.id: p for p in players + fas}
    my_items = news.mine_items(mine, nws, now)
    around = news.around_items(nws, players + fas, {p.id for p in mine}, now)
    headlines = news.league_headlines(now)
    ADD_ALERT = 15.0

    my_odds = proj.playoff_odds[me]
    biggest_need = max(("F", "D", "G"), key=lambda g: (wyr[g]["quality_rank"] + wyr[g]["ros_rank"], g))
    fixes = [i for i in lineup["today"]["issues"] if i["severity"] == "fix"]
    if fixes:
        urgent = "Lineup today: " + fixes[0]["text"]
    elif fa_recs and fa_recs[0].score > 5:
        r = fa_recs[0]
        urgent = "Add %s (%s), drop %s: +%.0f rest-of-season pts (playoff weeks x%g), %+.0f over the next two weeks.%s" % (
            r.p.name, r.p.group, r.drop.name, r.gain, config.PLAYOFF_WEIGHT, r.week_gain, _drop_caution(r.drop, mine))
    else:
        urgent = "Nothing urgent - set tomorrow's lineup and check back."
    headline = "%s: #%d of %d in projected strength, %.0f%% playoff odds. Weakest group: %s (#%d quality, #%d rest of season)." % (
        state.teams[me].name, my_rank, lg.size, my_odds * 100, biggest_need, wyr[biggest_need]["quality_rank"],
        wyr[biggest_need]["ros_rank"])

    fa_text = []
    for r in fa_recs[:3]:
        why = []
        if r.gain > 1:
            why.append("+%.0f ROS" % r.gain)
        if r.po_games:
            why.append("%d playoff-week games" % r.po_games)
        if r.week_gain > 1:
            why.append("+%.0f next 2 wks (%d+%d games)" % (r.week_gain, r.games_this, r.games_next))
        if r.p.pct_change > 1:
            why.append("owned %+.1f%%" % r.p.pct_change)
        if r.luck:
            why.append(r.luck)
        fa_text.append("%s (%s, %s) for %s: %s.%s" % (r.p.name, r.p.group, r.p.team, r.drop.name,
                                                       ", ".join(why) or "marginal", _drop_caution(r.drop, mine)))

    # Daily auto-notes, generated from this build's numbers (written takes come Mon & Thu)
    notes = []
    if matchup:
        notes.append("Week %d vs %s: projected %.0f-%.0f, %.0f%% to win (starts left %d vs %d)." % (
            cm, matchup["opp"], matchup["proj_me"], matchup["proj_opp"], matchup["win_prob"] * 100,
            matchup["starts_me"], matchup["starts_opp"]))
    if next_matchup and len(this_week) <= 2:
        notes.append("Next week (%s) vs %s: projected %.0f-%.0f, %.0f%% to win (starts %d vs %d)." % (
            next_matchup["dates"], next_matchup["opp"], next_matchup["proj_me"], next_matchup["proj_opp"],
            next_matchup["win_prob"] * 100, next_matchup["starts_me"], next_matchup["starts_opp"]))
    # Betting lines: my teams tonight whose implied goals differ most from their usual scoring
    tonight = sorted({p.team: cal.vegas[(p.team, today)] for p in engine.active(mine) if (p.team, today) in cal.vegas
                      and p.group != "G"}.items(), key=lambda ta: -abs(ta[1].line.goals / ta[1].base_gf - 1))
    swings = [(t, a) for t, a in tonight if abs(a.line.goals / a.base_gf - 1) >= 0.1][:4]
    if swings:
        notes.append("Vegas tonight: " + ", ".join("%s %.1f goals vs %s (usual %.1f, %+.0f%% skater pts)" % (
            t, a.line.goals, a.line.opp, a.base_gf, (a.sk_mult["F"] - 1) * 100) for t, a in swings) + ".")
    conf = [r for r in g_today if r["confirmed"] == "confirmed"]
    if conf:
        notes.append("Confirmed FA goalie starts today: %s." % ", ".join("%s vs %s" % (r["p"].name, r["opp"]) for r in conf[:3]))

    ctx = {
        "generated": datetime.now(ET).strftime("%a %b %-d, %-I:%M %p"),
        "league": {"name": lg.name.strip(), "size": lg.size, "scoring": {config.STAT_NAMES.get(k, k): v for k, v in lg.scoring.items()},
                   "slots": lg.slots, "playoff_teams": lg.playoff_teams},
        "me": {"id": me, "name": state.teams[me].name, "grade": GRADES[min(len(GRADES) - 1, (my_rank - 1) * len(GRADES) // lg.size)],
               "rank": my_rank, "playoff_odds": round(my_odds, 3), "posture": advice.posture(my_odds),
               "headline": headline, "urgent": urgent},
        "period": {"today": today, "date": cal.date_of(today).isoformat(), "matchup": cm,
                   "regular_matchups": state.regular_matchups},
        "standings": [{"id": tid, "name": t.name, "w": t.wins, "l": t.losses, "t": t.ties,
                       "pf": round(t.points_for, 1), "pa": round(t.points_against, 1),
                       "strength": round(proj.strength[tid], 1), "odds": round(proj.playoff_odds[tid], 3),
                       "exp_wins": round(proj.exp_wins[tid], 1)}
                      for tid, t in sorted(state.teams.items(), key=lambda kv: (-kv[1].wins, -kv[1].points_for, -proj.strength[kv[0]]))],
        "matchup": matchup,
        "next_matchup": next_matchup,
        "after_matchup": after_matchup,
        "lineup": lineup,
        "roster": sorted([_pl(p, cal, ros_periods, {"slot": config.SLOT_NAMES.get(p.slot, p.slot),
                                                    "g_this": cal.games(p.team, this_week), "g_next": cal.games(p.team, next_week),
                                                    "luck": advice.luck_flag(p),
                                                    "light": cal.games(p.team, light)}) for p in mine],
                         key=lambda d: -d["ros"]),
        "free_agents": [_pl(r.p, cal, ros_periods, {"score": round(r.score, 1), "gain": round(r.gain, 1),
                                                     "week_gain": round(r.week_gain, 1), "drop": r.drop.name,
                                                     "g_this": r.games_this, "g_next": r.games_next, "po_games": r.po_games,
                                                     "own_chg": round(r.p.pct_change, 1), "luck": r.luck,
                                                     "status": r.p.status, "light": cal.games(r.p.team, light),
                                                     "alt_drops": ["%s (%+.0f)" % (d.name, g) for d, g in (r.alt_drops or [])],
                                                     "waiver": _waiver(waiver_dates.get(r.p.id), r.p.status)})
                        for r in fa_recs],
        "fa_summary": fa_text,
        "streaming": {"schedule": _nhl_week(cal, cal.matchups.get(cm + 1 if plan_ahead else cm, []), today),
                      "week_label": "next week" if plan_ahead else "this week",
                      "games": [{"team": t, "this": g[0], "next": g[1]} for t, g in
                                sorted(stream["games"].items(), key=lambda kv: -(kv[1][0] + kv[1][1]))],
                      "goalies": [_pl(r["p"], extra={"exp": round(r["exp"], 1), "games": r["games"], "opps": r["opps"],
                                                     "status": r["p"].status})
                                  for r in stream["goalies"]],
                      "adds_used": adds_used, "adds_limit": config.ADDS_PER_MATCHUP,
                      "this": {"label": "Matchup %d (%s)" % (cm, _span(cal, this_week)), "open": advice.open_slots(mine, lg, cal, this_week),
                               "moves": [_move(m) for m in plan_this]},
                      "next": {"label": "Matchup %d (%s)" % (cm + 1, _span(cal, next_week)), "open": advice.open_slots(mine, lg, cal, next_week),
                               "moves": [_move(m) for m in plan_next]}},
        "playoffs": {"weeks": ["Wk %d (%s)" % (m, _span(cal, ps)) for m, ps in po_weeks.items()],
                     "schedule": dict(_nhl_week(cal, po_periods, today), week_starts=[ps[0] for ps in po_weeks.values()],
                                      mine={t: sorted(p.name.split()[-1] for p in mine if p.team == t)
                                            for t in {p.team for p in mine}},
                                      my_playing=[sum(1 for p in engine.active(mine) if d in cal.team_games.get(p.team, ()))
                                                  for d in po_periods]),
                     "weight": config.PLAYOFF_WEIGHT,
                     "nhl": po["nhl"], "avg_games": round(po["avg_games"], 1),
                     "teams": sorted([{"name": state.teams[tid].name, "me": tid == me, "weeks": [round(x, 1) for x in wk],
                                       "total": round(sum(wk), 1), "odds": round(proj.playoff_odds[tid], 3)}
                                      for tid, wk in po["proj"].items()], key=lambda r: -r["total"]),
                     "mine": sorted([{"name": r["p"].name, "group": r["p"].group, "team": r["p"].team, "weeks": r["weeks"],
                                      "total": sum(r["weeks"])} for r in po["mine"]], key=lambda r: r["total"])},
        "league_teams": [{"id": tid, "name": state.teams[tid].name, "me": tid == me,
                          "need": lranks[tid]["need"], "surplus": lranks[tid]["surplus"],
                          "ranks": {g: {"q": lranks[tid][g]["quality_rank"], "ros": lranks[tid][g]["ros_rank"],
                                        "ros_pts": round(lranks[tid][g]["ros"])} for g in ("F", "D", "G", "Total")},
                          "players": [_pl(p, cal, ros_periods, {"slot": config.SLOT_NAMES.get(p.slot, p.slot)})
                                      for p in sorted(ps, key=lambda p: -p.ros(cal, ros_periods))]}
                         for tid, ps in rosters.items()],
        "light_nights": len(light),
        "where_you_rank": {g: {k: (round(v, 1) if isinstance(v, float) else v) for k, v in d.items()} for g, d in wyr.items()},
        "weakest_starters": [_pl(p, cal, ros_periods) for p in weakest],
        "add_alerts": [_pl(r.p, cal, ros_periods, {"gain": round(r.gain, 1), "drop": r.drop.name,
                                                   "waiver": _waiver(waiver_dates.get(r.p.id), r.p.status)})
                       for r in fa_recs if r.gain >= ADD_ALERT][:5],
        "goalie_days": goalie_days,
        "recap": recap, "winprob_trend": trend,
        "trending": [_pl(f, extra={"own": round(f.base.pct_owned, 1), "own_chg": round(f.pct_change, 1),
                                   "waiver": _waiver(waiver_dates.get(f.id), f.status)}) for f in trending],
        "hot_fas": [_pl(f, extra={"form": fmt_form(f), "g_this": cal.games(f.team, this_week), "g_next": cal.games(f.team, next_week),
                                  "waiver": _waiver(waiver_dates.get(f.id), f.status)}) for f in hot_pick.values()],
        "drop_watch": [_pl(p, cal, ros_periods, {"cost": round(c, 1)}) for p, c in drop_rank if p.group != "G"][:3],
        "underperformers": [_pl(p, extra={"actual": round(a, 2), "proj": round(pr, 2)}) for p, a, pr in under if p.group != "G"],
        "drop_ranking": [_pl(p, cal, ros_periods, {"cost": round(c, 1), "po_games": cal.games(p.team, po_periods),
                                                   "light": cal.games(p.team, light)}) for p, c in drop_rank if p.group != "G"],
        "goalies_today": [_pl(r["p"], extra={"opp": r["opp"], "confirmed": r["confirmed"], "exp": round(r["exp"], 1),
                                             "opp_gf": round(r["opp_gf"], 2) if r["opp_gf"] else None,
                                             "win": round(r["win"], 3) if r["win"] is not None else None, "ml": r["ml"], "total": r["total"],
                                             "opp_goals": round(r["opp_goals"], 2) if r["opp_goals"] is not None else None,
                                             "waiver": _waiver(waiver_dates.get(r["p"].id), r["p"].status)})
                          for r in g_today],
        "recent": {"yesterday": cal.date_of(yday).strftime("%a %b %-d") if yday >= 1 else "",
                   "day": [_perf(f, st, pts) for pts, f, st in perf_day if pts > 0],
                   "week": [_perf(f, st, pts) for pts, f, st in perf_7 if pts > 0]},
        "league_news": {
            "mine": [dict(_news(i), status=by_id_all[i.player_id].injury if by_id_all[i.player_id].injury != "ACTIVE" else "")
                     for i in my_items],
            "around": [dict(_news(i), owner=state.teams[p.owner].name if p.owner else "FA", team=p.team, group=p.group,
                            status=p.injury if p.injury != "ACTIVE" else "") for p, i in around],
            "headlines": [{"title": h["title"], "link": h["link"], "source": h["source"],
                           "date": h["published"].astimezone(ET).strftime("%b %-d")} for h in headlines]},
        "notes": notes,
        "player_news": {p.name: [_news(i) for i in nws.get(p.id, [])[:2]] for p in mine},
        "playoff_usable": {"days": usable["days"], "totals": {k: sum(d[k] for d in usable["days"]) for k in ("used", "wasted", "empty_sk", "empty_g")},
                           "targets": _po_targets(fas, players, me, usable["teams"], cal, po_periods, state)},
        "trade_deadline": state.trade_deadline.strftime("%b %-d, %Y") if state.trade_deadline else "",
        "days_to_deadline": (state.trade_deadline.date() - cal.date_of(today)).days if state.trade_deadline else None,
        "cadence": {"takes_days": "Mon & Thu", "daily_at": "8:00 AM and 5:00 PM ET"},
        "calendar_warning": cal_warning,
        "accuracy": acc,
        "flags": {p.name: round(p.disagreement, 2) for p in players + fas if p.disagreement > DISAGREE},
        "disagree_threshold": DISAGREE,
        "rosters": {state.teams[tid].name: [p.name for p in sorted(ps, key=lambda p: -p.ros(cal, ros_periods))]
                    for tid, ps in rosters.items()},
    }
    return ctx


def digest(ctx: dict) -> dict:
    """Compact slice of the context (~5 KB vs ~230 KB) holding everything the written takes cite.
    The cloud routine reads only this, so a weekly refresh costs a few thousand tokens, not ~60k."""
    def mt(m):
        return m and {k: m[k] for k in ("opp", "dates", "actual_me", "actual_opp", "proj_me", "proj_opp", "win_prob",
                                        "starts_me", "starts_opp")}
    pick = lambda rows, keys: [{k: r.get(k) for k in keys} for r in rows]
    mine = ctx["roster"]
    return {
        "generated": ctx["generated"], "date": ctx["period"]["date"], "me": ctx["me"], "matchup": mt(ctx["matchup"]),
        "next_matchup": mt(ctx["next_matchup"]), "where_you_rank": ctx["where_you_rank"],
        "swing_days": [d["date"] for d in (ctx["matchup"] or {}).get("days", []) if d.get("swing")],
        "health_me": [(h["name"], h["status"], h["note"]) for h in (ctx["matchup"] or {}).get("health_me", [])],
        "health_opp": [(h["name"], h["status"], h["note"]) for h in (ctx["matchup"] or {}).get("health_opp", [])],
        "recap": ctx["recap"],
        "playoff_weeks": [(t["name"], t["total"]) for t in ctx["playoffs"]["teams"][:4]],
        "teams": [{"name": t["name"], "need": t["need"], "surplus": t["surplus"]} for t in ctx["league_teams"]],
        "standings": pick(ctx["standings"][:4], ("name", "w", "l", "odds", "strength")),
        "league_news": {"mine": pick(ctx["league_news"]["mine"][:4], ("player", "summary", "credit", "status")),
                        "around": pick(ctx["league_news"]["around"][:4], ("player", "owner", "summary", "credit")),
                        "headlines": [h["title"] for h in ctx["league_news"]["headlines"][:4]]},
        "roster": [{"name": r["name"], "pos": r["pos"], "slot": r["slot"], "injury": r["injury"], "out": r["out"], "luck": r["luck"],
                    "l7_l15_l30": r["f"]} for r in mine],
        "days_to_deadline": ctx["days_to_deadline"],
    }


def write_context(ctx: dict) -> None:
    config.STATE.mkdir(exist_ok=True)
    (config.STATE / "context.json").write_text(json.dumps(ctx, indent=1))
    (config.STATE / "digest.json").write_text(json.dumps(digest(ctx), separators=(",", ":")))
