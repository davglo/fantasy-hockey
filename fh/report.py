"""Compute every dashboard number on each build into one context dict (also written to state/context.json)."""
from __future__ import annotations

import json
import logging
from datetime import datetime

from fh import advice, config, engine, espn, market, rankings, season, valuation
from fh.board import ET

log = logging.getLogger(__name__)

GRADES = ["A", "A-", "B+", "B", "C+", "C", "D+", "D"]


def _pl(p, cal=None, periods=None, extra=None) -> dict:
    d = {"id": p.id, "name": p.name, "pos": p.base.positions, "group": p.group, "team": p.team,
         "injury": "" if p.injury == "ACTIVE" else p.injury, "rate": round(p.rate, 2),
         "exp_game": round(p.exp_game(), 2), "gp": p.act_gp, "fp": round(p.act_fp, 1)}
    if cal is not None:
        d["ros"] = round(p.ros(cal, periods), 1)
    d.update(extra or {})
    return d


def _span(cal, periods) -> str:
    if not periods:
        return "-"
    return "%s-%s" % (cal.date_of(periods[0]).strftime("%b %-d"), cal.date_of(periods[-1]).strftime("%b %-d"))


def _move(m) -> dict:
    return {"add": m.add.name, "add_pos": m.add.group, "add_team": m.add.team, "drop": m.drop.name,
            "gain": round(m.gain, 1), "days": m.days, "waivers": m.add.status == "WAIVERS"}


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

    today = state.today_period
    cm = state.current_matchup
    this_week = [d for d in cal.matchups.get(cm, []) if d >= today]
    next_week = cal.matchups.get(cm + 1, [])
    ros_periods = [d for m in range(cm, state.regular_matchups + 1) for d in cal.matchups[m] if d >= today]

    proj = engine.project(state, cal, rosters)
    rank = sorted(proj.strength, key=lambda t: -proj.strength[t])
    my_rank = rank.index(me) + 1

    # This week's matchup
    mt = next((m for m in state.schedule if m.period == cm and me in (m.home, m.away)), None)
    opp = (mt.away if mt.home == me else mt.home) if mt else None
    matchup = None
    if mt:
        my_pts, opp_pts = (mt.home_pts, mt.away_pts) if mt.home == me else (mt.away_pts, mt.home_pts)
        mu_me, mu_opp = proj.week_mu[(me, cm)], proj.week_mu[(opp, cm)]
        days = []
        for d in this_week:
            f = lambda p, d=d: p.exp_game() if d in cal.team_games.get(p.team, ()) else 0.0
            a, sa = engine.best_lineup(engine.active(mine), lg.slots, f)
            b, sb = engine.best_lineup(engine.active(rosters[opp]), lg.slots, f)
            days.append({"date": cal.date_of(d).strftime("%a %b %-d"), "me": round(a, 1), "opp": round(b, 1),
                         "me_starts": len(sa), "opp_starts": len(sb)})
        matchup = {"opp": state.teams[opp].name, "opp_id": opp, "actual_me": my_pts, "actual_opp": opp_pts,
                   "proj_me": round(mu_me, 1), "proj_opp": round(mu_opp, 1),
                   "win_prob": round(engine.win_prob(mu_me, mu_opp), 3),
                   "starts_me": engine.games_started(mine, lg, cal, this_week),
                   "starts_opp": engine.games_started(rosters[opp], lg, cal, this_week), "days": days,
                   "dates": "%s - %s" % (cal.date_of(cal.matchups[cm][0]).strftime("%b %-d"),
                                         cal.date_of(cal.matchups[cm][-1]).strftime("%b %-d"))}

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
    nd = advice.needs(mine, lg, cal, ros_periods, players)
    fa_recs = advice.free_agents(mine, fas, value, this_week, next_week)
    stream = advice.streaming(fas, cal, this_week, next_week)
    adds_used = state.teams[me].adds_by_matchup.get(cm, 0)
    adds_left = max(0, config.ADDS_PER_MATCHUP - adds_used)
    plan_this = advice.stream_plan(mine, fas, value, this_week, adds_left)
    plan_next = advice.stream_plan(mine, fas, value, next_week, config.ADDS_PER_MATCHUP)
    rater = market.rater_ranks(espn.fetch_pool())
    tv = market.trade_values(players + fas, rater, value.quick)
    trade_recs = advice.trades(rosters, me, fas, value, tv, proj.playoff_odds)
    po = advice.playoff_schedule(rosters, me, lg, cal, po_weeks)

    my_odds = proj.playoff_odds[me]
    biggest_need = max(nd, key=lambda g: nd[g]["gap"])
    fixes = [i for i in lineup["today"]["issues"] if i["severity"] == "fix"]
    if fixes:
        urgent = "Lineup today: " + fixes[0]["text"]
    elif fa_recs and fa_recs[0].score > 5:
        r = fa_recs[0]
        urgent = "Add %s (%s), drop %s: +%.0f rest-of-season pts (playoff weeks x%g), +%.0f over the next two weeks.%s" % (
            r.p.name, r.p.group, r.drop.name, r.gain, config.PLAYOFF_WEIGHT, r.week_gain, _drop_caution(r.drop, mine))
    elif trade_recs:
        t = trade_recs[0]
        urgent = "Trade idea: %s for %s with %s (+%.0f ROS)." % (
            " + ".join(p.name for p in t.give), t.get[0].name, state.teams[t.partner].name, t.my_gain)
    else:
        urgent = "Nothing urgent - set tomorrow's lineup and check back."
    headline = "%s: #%d of %d in projected strength, %.0f%% playoff odds. Weakest spot: %s." % (
        state.teams[me].name, my_rank, lg.size, my_odds * 100, biggest_need)

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

    ctx = {
        "generated": datetime.now(ET).strftime("%a %b %-d, %-I:%M %p"),
        "league": {"name": lg.name.strip(), "size": lg.size, "scoring": {config.STAT_NAMES.get(k, k): v for k, v in lg.scoring.items()},
                   "slots": lg.slots, "playoff_teams": lg.playoff_teams},
        "me": {"id": me, "name": state.teams[me].name, "grade": GRADES[min(len(GRADES) - 1, (my_rank - 1) * len(GRADES) // lg.size)],
               "rank": my_rank, "playoff_odds": round(my_odds, 3), "posture": advice.posture(my_odds),
               "headline": headline, "urgent": urgent,
               "needs": {g: {k: round(v, 1) for k, v in d.items()} for g, d in nd.items()}},
        "period": {"today": today, "date": cal.date_of(today).isoformat(), "matchup": cm,
                   "regular_matchups": state.regular_matchups},
        "standings": [{"id": tid, "name": t.name, "w": t.wins, "l": t.losses, "t": t.ties,
                       "pf": round(t.points_for, 1), "pa": round(t.points_against, 1),
                       "strength": round(proj.strength[tid], 1), "odds": round(proj.playoff_odds[tid], 3),
                       "exp_wins": round(proj.exp_wins[tid], 1)}
                      for tid, t in sorted(state.teams.items(), key=lambda kv: (-kv[1].wins, -kv[1].points_for, -proj.strength[kv[0]]))],
        "matchup": matchup,
        "lineup": lineup,
        "roster": sorted([_pl(p, cal, ros_periods, {"slot": config.SLOT_NAMES.get(p.slot, p.slot),
                                                    "g_this": cal.games(p.team, this_week), "g_next": cal.games(p.team, next_week),
                                                    "luck": advice.luck_flag(p)}) for p in mine],
                         key=lambda d: ("FDG".index(d["group"]), -d["ros"])),
        "free_agents": [_pl(r.p, cal, ros_periods, {"score": round(r.score, 1), "gain": round(r.gain, 1),
                                                     "week_gain": round(r.week_gain, 1), "drop": r.drop.name,
                                                     "g_this": r.games_this, "g_next": r.games_next, "po_games": r.po_games,
                                                     "own_chg": round(r.p.pct_change, 1), "luck": r.luck,
                                                     "status": r.p.status})
                        for r in fa_recs],
        "fa_summary": fa_text,
        "streaming": {"games": [{"team": t, "this": g[0], "next": g[1]} for t, g in
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
                     "weight": config.PLAYOFF_WEIGHT,
                     "nhl": po["nhl"], "avg_games": round(po["avg_games"], 1),
                     "teams": sorted([{"name": state.teams[tid].name, "me": tid == me, "weeks": [round(x, 1) for x in wk],
                                       "total": round(sum(wk), 1), "odds": round(proj.playoff_odds[tid], 3)}
                                      for tid, wk in po["proj"].items()], key=lambda r: -r["total"]),
                     "mine": sorted([{"name": r["p"].name, "group": r["p"].group, "team": r["p"].team, "weeks": r["weeks"],
                                      "total": sum(r["weeks"])} for r in po["mine"]], key=lambda r: r["total"])},
        "trades": [{"partner": state.teams[t.partner].name, "give": [p.name for p in t.give], "get": [p.name for p in t.get],
                    "my_gain": round(t.my_gain, 1), "their_gain": round(t.their_gain, 1),
                    "market_give": round(t.market_give), "market_get": round(t.market_get),
                    "backfill": ("add " if len(t.give) > len(t.get) else "drop ") + t.backfill.name if t.backfill else "",
                    "partner_odds": round(t.partner_odds, 3)}
                   for t in trade_recs],
        "rosters": {state.teams[tid].name: [p.name for p in sorted(ps, key=lambda p: -p.ros(cal, ros_periods))]
                    for tid, ps in rosters.items()},
    }
    return ctx


def write_context(ctx: dict) -> None:
    config.STATE.mkdir(exist_ok=True)
    (config.STATE / "context.json").write_text(json.dumps(ctx, indent=1))
