"""Compute every dashboard number on each build into one context dict (also written to state/context.json)."""
from __future__ import annotations

import json
import logging
from datetime import datetime

from fh import accuracy, advice, config, engine, espn, market, news, rankings, rumors, season, valuation
from fh.espn import fantasy_points
from fh.board import ET

log = logging.getLogger(__name__)

GRADES = ["A", "A-", "B+", "B", "C+", "C", "D+", "D"]
DISAGREE = 0.25   # flag a player when his projection sources differ by more than this share of the consensus


def _pl(p, cal=None, periods=None, extra=None) -> dict:
    src = p.sources or {}
    d = {"id": p.id, "name": p.name, "pos": p.base.positions, "group": p.group, "team": p.team,
         "injury": "" if p.injury == "ACTIVE" else p.injury, "rate": round(p.rate, 2),
         "src": {k: (round(v, 2) if v is not None else None) for k, v in src.items()},
         "cons": round(p.consensus, 2) if p.consensus is not None else None, "dis": round(p.disagreement, 2),
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


def _last7(p, state) -> float:
    ent = next((e for t in state.teams.values() for e, _ in t.roster if e["player"]["id"] == p.id), None)
    return fantasy_points(season.split_stats(ent, 1), state.league.scoring) if ent else 0.0


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
    accuracy.log_snapshot(players + fas, cal.date_of(state.today_period))
    acc = accuracy.evaluate(cal.date_of(state.today_period))

    today = state.today_period
    cm = state.current_matchup
    this_week = [d for d in cal.matchups.get(cm, []) if d >= today]
    next_week = cal.matchups.get(cm + 1, [])
    ros_periods = [d for m in range(cm, state.regular_matchups + 1) for d in cal.matchups[m] if d >= today]

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
            f = lambda p, d=d: p.exp_game() if d in cal.team_games.get(p.team, ()) else 0.0
            a, sa = engine.best_lineup(pool(mine), lg.slots, f)
            b, sb = engine.best_lineup(pool(rosters[opp]), lg.slots, f)
            days.append({"date": cal.date_of(d).strftime("%a %b %-d"), "me": round(a, 1), "opp": round(b, 1),
                         "me_starts": len(sa), "opp_starts": len(sb)})
        return {"opp": state.teams[opp].name, "opp_id": opp, "actual_me": my_pts, "actual_opp": opp_pts,
                "proj_me": round(mu_me, 1), "proj_opp": round(mu_opp, 1),
                "win_prob": round(engine.win_prob(mu_me, mu_opp), 3),
                "starts_me": sum(d["me_starts"] for d in days), "starts_opp": sum(d["opp_starts"] for d in days),
                "days": days,
                "dates": "%s - %s" % (cal.date_of(cal.matchups[m][0]).strftime("%b %-d"),
                                      cal.date_of(cal.matchups[m][-1]).strftime("%b %-d"))}

    matchup = _matchup(cm, this_week)
    next_matchup = _matchup(cm + 1, next_week) if next_week else None
    week_after = cal.matchups.get(cm + 2, [])
    after_matchup = _matchup(cm + 2, week_after) if week_after else None

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
    usable = advice.usable_games(mine, lg, cal, po_periods)

    # Where you rank (a full upcoming week is the fair yardstick)
    wyr = advice.where_you_rank(rosters, me, lg, cal, ros_periods)
    quick = {p.id: value.quick(p) for p in mine}
    _, season_starters = engine.best_lineup(mine, lg.slots, lambda p: quick[p.id] + 1e-9)
    weakest = sorted(season_starters, key=lambda p: quick[p.id])[:3]

    # Drops
    drop_rank = advice.drop_ranking(mine, value)
    under = advice.underperformers(mine)

    # Recent free-agent performers: yesterday + last 7 days
    yday = today - 1
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
    perf_day = sorted(perf_day, key=lambda t: -t[0])[:10]
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
    g_today = advice.goalies_today(fas, cal, today, starting, season.fetch_goals_for())[:8]
    opps = news.opportunities(nws, {p.id: p for p in players + fas}, fas, now)
    news.save_snapshot(players)
    drops3 = advice.droppable(mine, value, k=3)
    base_val = value(mine)

    short = list(this_week) + list(next_week)
    base_short = engine.projected_points(mine, lg, cal, short)

    def fa_gain(f):
        return max((value([q for q in mine if q.id != d.id] + [f]) - base_val for d in drops3), default=0.0)

    def fa_gain_short(f):
        return max((engine.projected_points([q for q in mine if q.id != d.id] + [f], lg, cal, short) - base_short
                    for d in drops3), default=0.0)

    def opp_row(f):
        return {"id": f.id, "name": f.name, "team": f.team, "pos": f.base.positions, "gain": round(fa_gain(f), 1),
                "gain_2wk": round(fa_gain_short(f), 1), "status": f.status,
                "waiver": _waiver(waiver_dates.get(f.id), f.status)}
    opp_rows = []
    for o in opps:
        bens = [r for r in (opp_row(f) for f in o.beneficiaries) if r["gain"] >= 2 or r["gain_2wk"] >= 1]
        if bens:
            opp_rows.append((o, sorted(bens, key=lambda r: -max(r["gain"], r["gain_2wk"]))))
    my_news = news.top([i for p in mine for i in nws.get(p.id, [])], now, 8)
    rumor_names = [p.name for p in mine] + [r.p.name for r in fa_recs[:8]]
    known = [e["player"]["fullName"] for e in espn.fetch_pool()] + [p.name for p in players + fas]
    rumor_items = [r for r in rumors.refresh(rumor_names, known)
                   if (now - datetime.fromisoformat(r["date"])).total_seconds() <= news.MAX_AGE_H * 3600][:8]
    # Flagged players on my roster: always show their latest item, however old (the saga matters).
    status_watch = []
    for p in mine:
        if p.injury == "ACTIVE":
            continue
        latest = next((i for i in nws.get(p.id, []) if news.newsworthy(i)), None)
        if latest and latest.age_hours(now) <= news.MAX_AGE_H:
            status_watch.append((p, latest))
    ADD_ALERT = 15.0

    my_odds = proj.playoff_odds[me]
    biggest_need = max(("F", "D", "G"), key=lambda g: (wyr[g]["quality_rank"] + wyr[g]["ros_rank"], g))
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
    if opp_rows:
        o, bens = opp_rows[0]
        if o.about.id == bens[0]["id"]:
            notes.append("Opportunity: free agent %s (%s) - %s." % (o.about.name, o.about.team, o.item.summary[:120]))
        else:
            notes.append("Opportunity: %s (%s) %s -> %s is the free agent who gains." % (
                o.about.name, o.about.team, "/".join(sorted(o.item.kinds & news.NEGATIVE)), bens[0]["name"]))
    if fa_recs and fa_recs[0].gain >= ADD_ALERT:
        notes.append("Best rest-of-season add: %s for %s (+%.0f)." % (fa_recs[0].p.name, fa_recs[0].drop.name, fa_recs[0].gain))
    conf = [r for r in g_today if r["confirmed"] == "confirmed"]
    if conf:
        notes.append("Confirmed FA goalie starts today: %s." % ", ".join("%s vs %s" % (r["p"].name, r["opp"]) for r in conf[:3]))
    if perf_day:
        pts, f, _ = perf_day[0]
        notes.append("Top free agent yesterday: %s, %.1f pts." % (f.name, pts))

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
                                                    "luck": advice.luck_flag(p)}) for p in mine],
                         key=lambda d: -d["ros"]),
        "free_agents": [_pl(r.p, cal, ros_periods, {"score": round(r.score, 1), "gain": round(r.gain, 1),
                                                     "week_gain": round(r.week_gain, 1), "drop": r.drop.name,
                                                     "g_this": r.games_this, "g_next": r.games_next, "po_games": r.po_games,
                                                     "own_chg": round(r.p.pct_change, 1), "luck": r.luck,
                                                     "status": r.p.status,
                                                     "alt_drops": ["%s (%+.0f)" % (d.name, g) for d, g in (r.alt_drops or [])],
                                                     "waiver": _waiver(waiver_dates.get(r.p.id), r.p.status)})
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
        "where_you_rank": {g: {k: (round(v, 1) if isinstance(v, float) else v) for k, v in d.items()} for g, d in wyr.items()},
        "weakest_starters": [_pl(p, cal, ros_periods) for p in weakest],
        "add_alerts": [_pl(r.p, cal, ros_periods, {"gain": round(r.gain, 1), "drop": r.drop.name,
                                                   "waiver": _waiver(waiver_dates.get(r.p.id), r.p.status)})
                       for r in fa_recs if r.gain >= ADD_ALERT][:5],
        "drop_watch": [_pl(p, cal, ros_periods, {"cost": round(c, 1)}) for p, c in drop_rank[:3]],
        "underperformers": [_pl(p, extra={"actual": round(a, 2), "proj": round(pr, 2)}) for p, a, pr in under],
        "drop_ranking": [_pl(p, cal, ros_periods, {"cost": round(c, 1), "po_games": cal.games(p.team, po_periods),
                                                   "last7": round(_last7(p, state), 1)}) for p, c in drop_rank],
        "goalies_today": [_pl(r["p"], extra={"opp": r["opp"], "confirmed": r["confirmed"], "exp": round(r["exp"], 1),
                                             "opp_gf": round(r["opp_gf"], 2) if r["opp_gf"] else None,
                                             "waiver": _waiver(waiver_dates.get(r["p"].id), r["p"].status)})
                          for r in g_today],
        "recent": {"yesterday": cal.date_of(yday).strftime("%a %b %-d") if yday >= 1 else "",
                   "day": [_perf(f, st, pts) for pts, f, st in perf_day if pts > 0],
                   "week": [_perf(f, st, pts) for pts, f, st in perf_7 if pts > 0]},
        "news": [_news(i) for i in my_news],
        "status_watch": [dict(_news(i), status=p.injury) for p, i in status_watch],
        "rumors": [{"title": r["title"], "link": r["link"], "players": r["players"],
                    "date": datetime.fromisoformat(r["date"]).astimezone(ET).strftime("%b %-d")} for r in rumor_items],
        "notes": notes,
        "opportunities": [{"news": _news(o.item), "about": o.about.name, "about_team": o.about.team,
                           "about_group": o.about.group, "self": o.about.id == bens[0]["id"],
                           "kinds": sorted(o.item.kinds & (news.NEGATIVE | news.POSITIVE)),
                           "beneficiaries": bens} for o, bens in opp_rows[:8]],
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


def write_context(ctx: dict) -> None:
    config.STATE.mkdir(exist_ok=True)
    (config.STATE / "context.json").write_text(json.dumps(ctx, indent=1))
