"""In-season dashboard: one self-contained dark HTML page with tabs, rendered from the context dict."""
from __future__ import annotations

import html

from fh import engine


def _e(x) -> str:
    return html.escape(str(x))


def _pct(x: float) -> str:
    return "%.0f%%" % (x * 100)


def _table(headers: list, rows: list, cls: str = "") -> str:
    head = "".join("<th>%s</th>" % _e(h) for h in headers)
    body = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in r) for r in rows)
    return '<div class="tw"><table class="%s"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (
        cls, head, body or '<tr><td colspan="%d" class="mute">Nothing yet</td></tr>' % len(headers))


def _inj(p: dict) -> str:
    return ' <span class="inj">%s</span>' % _e(p["injury"]) if p.get("injury") else ""


SRC_LABELS = (("model", "Model"), ("espn", "ESPN"), ("pace", "Pace"), ("l15", "L15"))


def _v(x) -> str:
    return "%.2f" % x if x is not None else '<span class="mute">-</span>'


def _flag(p: dict, ctx: dict) -> str:
    """Warning marker when a player's projection sources disagree."""
    if p.get("dis", 0) <= ctx.get("disagree_threshold", 0.25):
        return ""
    tip = ", ".join("%s %.2f" % (lab, p["src"][k]) for k, lab in SRC_LABELS if p["src"].get(k) is not None)
    return ' <span class="flag" title="Sources disagree by %d%%: %s">&#9888;</span>' % (round(p["dis"] * 100), _e(tip))


def _name_flag(name: str, ctx: dict) -> str:
    d = ctx.get("flags", {}).get(name)
    return ' <span class="flag" title="Projection sources disagree by %d%%">&#9888;</span>' % round(d * 100) if d else ""


def _accuracy_card(ctx: dict) -> str:
    a = ctx["accuracy"]
    names = {"model": "Model (sheet + curve)", "sheet": "Spreadsheet only", "espn": "ESPN projection",
             "pace": "Season pace", "l15": "Last 15 days", "consensus": "Consensus"}
    if not a["windows"]:
        body = ('<p>Collecting data: snapshot %d logged. First grades on <b>%s</b> (each prediction is checked against the '
                'next 7 days of games), and it gets more reliable every week after.</p>' % (a["snapshots"], _e(a["first_grade"])))
    else:
        rows = []
        for k, lab in names.items():
            t = a["table"][k]
            if t["all"] is None:
                continue
            best = ' <span class="good">best</span>' if k == a["best"] else ""
            rows.append([lab + best] + [_v(t[g]) for g in ("all", "F", "D", "G")] + ["%d" % a["games"][k]])
        body = _table(["Source", "Error/game", "F", "D", "G", "Player-games"], rows) + \
            '<p class="mute">Average miss in fantasy points per game over the following 7 days (lower is better), %d weekly windows so far.</p>' % a["windows"]
    return '<div class="card"><h3>Which projection is winning</h3>%s</div>' % body


def _take(manual: dict, key: str) -> str:
    t = (manual.get("takes") or {}).get(key)
    if not t:
        return ""
    return '<div class="take"><p>%s</p><div class="stamp">Analysis %s</div></div>' % (
        _e(t), _e(manual.get("date", "")))


def _news_li(n: dict, extra: str = "") -> str:
    badge = '<span class="ins">insider</span> ' if n.get("insider") else ""
    return ('<li>%s<b>%s</b>%s: %s <span class="cred">%s &middot; %s &middot; <a href="%s" target="_blank" rel="noopener">ESPN news</a></span></li>'
            % (badge, _e(n["player"]), extra, _e(n["summary"]), _e(n["credit"]), _e(n["date"]), _e(n["link"])))


def _rec(p: dict) -> str:
    return "%s-%s" % (p["w"], p["l"]) + ("-%d" % p["t"] if p["t"] else "")


def _overview(ctx, manual):
    me, myid = ctx["me"], ctx["me"]["id"]
    size = ctx["league"]["size"]
    rum = "".join('<li><b>%s</b>: <a href="%s" target="_blank" rel="noopener">%s</a> <span class="cred">Yahoo Sports &middot; %s</span></li>' % (
        _e(", ".join(r["players"])), _e(r["link"]), _e(r["title"]), _e(r["date"])) for r in ctx.get("rumors", []))
    adds = "".join("<li><b>%s</b> (%s, %s) +%.0f ROS for %s%s</li>" % (
        _e(a["name"]), _e(a["pos"]), _e(a["team"]), a["gain"], _e(a["drop"]), " &middot; " + _e(a["waiver"]) if a["waiver"] else "")
        for a in ctx["add_alerts"][:4])
    adds += "".join("<li><b>%s</b> (%s) - %s %s %s</li>" % (
        _e(o["beneficiaries"][0]["name"]), _e(o["beneficiaries"][0]["team"]), _e(o["about"]), "/".join(o["kinds"]), "(news)")
        for o in ctx["opportunities"][:2])
    drops = "".join("<li><b>%s</b> (%s) - costs %.0f ROS if dropped</li>" % (_e(d["name"]), d["group"], d["cost"]) for d in ctx["drop_watch"])
    drops += "".join("<li><b>%s</b> running %.2f pts/GP vs %.2f projected</li>" % (_e(u["name"]), u["actual"], u["proj"])
                     for u in ctx["underperformers"])
    gt = [[_e(g["name"]) + _inj(g), _e(g["team"]), _e(g["opp"]),
           {"confirmed": '<span class="good">confirmed</span>', "expected": '<span class="warnc">expected</span>',
            "backup": '<span class="bad">backup tonight</span>'}.get(g["confirmed"], '<span class="mute">not reported</span>'),
           "%.1f" % g["exp"] if g["exp"] else "-", "%.0f%%" % (g["win"] * 100) if g.get("win") is not None else "-",
           "%.1f" % g["opp_goals"] if g.get("opp_goals") is not None else "-",
           "%.2f" % g["opp_gf"] if g["opp_gf"] else "-", _e(g["waiver"])] for g in ctx["goalies_today"]]
    nw = "".join(_news_li(n) for n in ctx["news"])
    sw = "".join(_news_li(n, ' <span class="inj">%s</span>' % _e(n["status"])) for n in ctx["status_watch"])
    cad = ctx["cadence"]
    return """
<div class="hero"><div class="grade">{grade}</div><div><div class="hl">{headline}</div>
<div class="kpis"><span><b>{odds}</b> playoff odds</span><span><b>#{rank}</b> of {size} strength</span><span>{posture}</span></div></div></div>
{calwarn}<div class="card"><h3>Today's notes</h3><ul>{notes}</ul><p class="mute">Numbers and these notes refresh daily at {daily}. Written analysis below is updated {takes} (last: {tdate}).</p></div>
<div class="card"><h3>News that matters</h3><ul class="news">{nw}</ul>{sw}{rum}</div>
{take}
<div class="grid">
<div class="card"><h3>Add alerts</h3><ul>{adds}</ul></div>
<div class="card"><h3>Drop watch</h3><ul>{drops}</ul><p class="mute">Skaters only. Full ranking on Free Agents.</p></div>
</div>
<div class="card"><h3>Goalies to add today</h3>{gt}<p class="mute">Free-agent goalies whose team plays today. Start status comes from RotoWire's reports (via ESPN), which relay beat reporters and team sources - the same sources Daily Faceoff uses; a note counts when it names today's game. Backup = a teammate is confirmed. Win / Opp exp G = DraftKings betting line (via ESPN): his team's chance to win and the opponent's expected goals tonight; Exp pts already includes them. Opp GF = opponent goals per game (this season, steadied with last season).</p></div>""".format(
        grade=_e(me["grade"]), headline=_e(me["headline"]), odds=_pct(me["playoff_odds"]), rank=me["rank"], size=size,
        posture=_e(me["posture"]),
        calwarn='<div class="card urgent"><h3>Calendar check failed</h3><p>%s. Week-based numbers may be off until fixed.</p></div>'
                % _e(ctx["calendar_warning"]) if ctx.get("calendar_warning") else "", notes="".join("<li>%s</li>" % _e(n) for n in ctx["notes"]),
        daily=_e(cad["daily_at"]), takes=_e(cad["takes_days"]), tdate=_e(manual.get("date", "never")),
        take=_take(manual, "overview"), adds=adds or "<li class=mute>No add clears the bar today.</li>",
        drops=drops or "<li class=mute>No skater worth cutting.</li>",
        gt=_table(["Goalie", "Team", "Opp", "Start", "Exp pts", "Win", "Opp exp G", "Opp GF", "Waivers"], gt),
        nw=nw or "<li class=mute>No fresh news on your players in the last 72h.</li>",
        sw='<h3 style="margin-top:10px">Status watch</h3><ul class="news">%s</ul>' % sw if sw else "",
        rum='<h3 style="margin-top:10px">Rumors &amp; reports</h3><ul class="news">%s</ul>' % rum if rum else "")


def _matchup_block(m, take, now=True):
    days = [[d["date"], "%.1f" % d["me"], "%.1f" % d["opp"], "%d / %d" % (d["me_starts"], d["opp_starts"])] for d in m["days"]]
    score = "<span>Now <b>%.1f</b> - <b>%.1f</b></span>" % (m["actual_me"], m["actual_opp"]) if now else ""
    return """
<div class="hero"><div class="grade">{wp}</div><div><div class="hl">vs {opp} &middot; {dates}</div>
<div class="kpis">{score}<span>Projected <b>{pm:.0f}</b> - <b>{po:.0f}</b></span>
<span>Starts{left} <b>{sm}</b> vs <b>{so}</b></span></div></div></div>
{take}
<div class="card"><h3>Day by day (optimal lineups)</h3>{days}</div>
""".format(wp=_pct(m["win_prob"]), opp=_e(m["opp"]), dates=_e(m["dates"]), score=score, left=" left" if now else "",
           pm=m["proj_me"], po=m["proj_opp"], sm=m["starts_me"], so=m["starts_opp"], take=take,
           days=_table(["Day", "Me", "Opp", "Starts me / opp"], days))


def _week(ctx, manual):
    m, n = ctx.get("matchup"), ctx.get("next_matchup")
    parts = [_matchup_block(m, _take(manual, "matchup")) if m else '<div class="card"><p>No matchup this week.</p></div>']
    if n:
        parts.append('<h3 class="sec">Next week</h3>' + _matchup_block(n, "", now=False))
    a = ctx.get("after_matchup")
    if a:
        parts.append('<h3 class="sec">Week after</h3>' + _matchup_block(a, "", now=False))
    parts.append('<p class="mute">Win chance uses a normal model with each team\'s weekly score varying about 15%. '
                 'Points league: every start counts, so the games-played edge matters. Future weeks assume both rosters '
                 'stay as they are today (IR players counted).</p>')
    return "".join(parts)


def _streaming(ctx, manual):
    s = ctx["streaming"]
    parts = ['<p class="mute">Adds used this matchup: <b>%d of %d</b>. Plans are greedy: each step takes the pickup that adds the most '
             'projected points for that matchup; your top 14 players are never dropped.</p>' % (s["adds_used"], s["adds_limit"])]
    for key in ("this", "next"):
        blk = s[key]
        mv = [["%d" % (i + 1), _e(m["add"]) + (' <span class="tag">claim</span>' if m["waivers"] else ""), _e(m["add_pos"]),
               _e(m["add_team"]), _e(m["drop"]), "%+.1f" % m["gain"], _e(", ".join(m["days"]))] for i, m in enumerate(blk["moves"])]
        heat = "".join('<div class="day"><div>%s</div><div class="%s">G %d</div><div class="%s">Sk %d</div></div>' % (
            _e(o["date"][:3] + " " + o["date"].split()[-1]), "hot" if o["G"] else "", o["G"], "hot" if o["skater"] >= 3 else "", o["skater"])
            for o in blk["open"])
        parts.append('<div class="card"><h3>%s</h3><div class="days">%s</div><p class="mute">Open lineup slots per day with your current roster '
                     '(G = goalie slots, Sk = skater slots). Highlighted days are where a streamer scores.</p>%s</div>' % (
                         _e(blk["label"]), heat, _table(["#", "Add", "Pos", "Team", "Drop", "Gain", "Game days"], mv)))
    sch = s.get("schedule")
    if sch and sch["days"]:
        head = "".join('<th class="%s">%s</th>' % ("past" if d["past"] else "", _e(d["date"])) for d in sch["days"])
        rows = "".join('<tr><td><b>%s</b></td><td>%d</td>%s</tr>' % (_e(t["team"]), t["games"], "".join(
            '<td class="%s">%s</td>' % ("past" if d["past"] else "", _e(c)) for d, c in zip(sch["days"], t["cells"])))
            for t in sch["teams"])
        foot = '<tr class="sum"><td>Teams playing</td><td></td>%s</tr>' % "".join(
            '<td class="%s">%d</td>' % ("past" if d["past"] else ("hot" if d.get("light") else ""), d["n"]) for d in sch["days"])
        parts.append('<div class="card"><h3>NHL schedule this week</h3><div class="tw"><table class="sched"><thead><tr>'
                     '<th>Team</th><th>G</th>%s</tr></thead><tbody>%s%s</tbody></table></div>'
                     '<p class="mute">Opponent each day: <b>BOS</b> = home vs Boston, <b>@BOS</b> = at Boston. '
                     'Sorted by games this week. Highlighted counts are light days (half the league or less playing): '
                     'the easiest days to fit a streamer into your lineup. Faded columns are already played.</p></div>' % (head, rows, foot))
    gm = [[_e(g["team"]), g["this"], g["next"]] for g in s["games"][:12]]
    gs = [[_e(p["name"]) + _inj(p), _e(p["team"]), p["games"], "%.1f" % p["exp"], _e(", ".join(p["opps"]))] for p in s["goalies"]]
    parts.append('<div class="grid"><div class="card"><h3>Most games (this wk / next)</h3>%s</div>'
                 '<div class="card"><h3>Goalie streams this week</h3>%s</div></div>' % (
                     _table(["Team", "This", "Next"], gm), _table(["Goalie", "Team", "Games", "Exp pts", "Opponents"], gs)))
    return "".join(parts)


def _free_agents(ctx, manual):
    opp = "".join('<li><b>%s</b> (%s %s) %s &rarr; %s <span class="cred">%s &middot; <a href="%s" target="_blank" rel="noopener">ESPN news</a></span><br><span class="mute">%s</span></li>' % (
        _e(o["about"]), _e(o["about_team"]), o["about_group"],
        ('<span class="good">free agent: %s</span>' % _e("/".join(o["kinds"]))) if o.get("self") else _e("/".join(o["kinds"])),
        "; ".join("<b>%s</b> (%s, %+.0f ROS, %+.1f next 2 wks%s)" % (_e(b["name"]), _e(b["pos"]), b["gain"], b["gain_2wk"],
                                                                    ", " + _e(b["waiver"]) if b["waiver"] else "") for b in o["beneficiaries"]),
        _e(o["news"]["credit"]), _e(o["news"]["link"]), _e(o["news"]["summary"])) for o in ctx["opportunities"])
    rec = ctx["recent"]
    perf = lambda rows: _table(["Player", "Pos", "Team", "Pts", "Line", "Model/gm", "Consensus", "Own chg"], [
        [_e(r["name"]) + _inj(r) + _flag(r, ctx), _e(r["pos"]), _e(r["team"]), "%.1f" % r["pts"], _e(r["line"]),
         _v(r["src"].get("model")), _v(r["cons"]), "%+.1f" % r["own_chg"]] for r in rows])
    fa = [[_e(p["name"]) + _inj(p) + _flag(p, ctx) + (' <span class="tag">%s</span>' % _e(p["waiver"]) if p["waiver"] else ""), _e(p["pos"]),
           _e(p["team"]), "%+.1f" % p["gain"], _v(p["src"].get("model")), _v(p["src"].get("espn")), _v(p["cons"]),
           "%d" % p["light"], "%d" % p["po_games"], "%+.1f" % p["week_gain"], "%+.1f" % p["own_chg"],
           _e(p["drop"]) + ('<br><span class="mute">or %s</span>' % _e(", ".join(p["alt_drops"])) if p["alt_drops"] else ""),
           _e(p["luck"])] for p in ctx["free_agents"]]
    dr = [["%d" % (i + 1), _e(d["name"]) + _flag(d, ctx), d["group"], _e(d["team"]), "%.0f" % d["cost"], "%.0f" % d["ros"],
           _v(d["src"].get("model")), _v(d["cons"]), "%.1f" % d["last7"], d["light"], d["po_games"]]
          for i, d in enumerate(ctx["drop_ranking"])]
    return """<div class="card"><h3>Opportunity alerts</h3><ul class="news">{opp}</ul><p class="mute">News that creates free-agent value: injuries, suspensions, trades and demotions around the league (matched to free agents named in the report or on the same team and position), plus good news about free agents themselves - call-ups, promotions to the top line or first power-play unit, new starting jobs, returns and trades. Last 2 days only. Gains are before any role bump - the news is the edge.</p></div>
<div class="card"><h3>Injuries around the league (last 48h)</h3><ul class="news">{inj}</ul><p class="mute">Injury and suspension reports on anyone rostered in this league or owned in 10%+ of ESPN leagues. Owner in brackets.</p></div>
<div class="grid"><div class="card"><h3>Best free agents {yd}</h3>{pd}</div><div class="card"><h3>Best free agents, last 7 days</h3>{pw}</div></div>
<div class="card"><h3>Best rest-of-season adds</h3><ul>{summ}</ul>{fa}
<p class="mute">Gain = rest-of-season points added (optimal daily lineups, playoff weeks x{w:g}) with the best drop; next-best drops shown under it. {light}</p></div>
<div class="card"><h3>Drop ranking</h3>{dr}<p class="mute">Points lost (rest of season + playoff weeks x{w:g}) if you cut each player, cheapest first. IR, injury/suspension-flagged players and your last two healthy goalies are excluded.</p></div>""".format(
        light=LIGHT_NOTE % ctx["light_nights"], opp=opp or "<li class=mute>No news in the last 2 days creating free-agent value.</li>",
        yd=_e(rec["yesterday"]), pd=perf(rec["day"]), pw=perf(rec["week"]),
        inj="".join(_news_li(n, ' <span class="mute">[%s, %s %s]</span>%s' % (
            _e(n["owner"]), _e(n["team"]), n["group"], ' <span class="inj">%s</span>' % _e(n["status"]) if n["status"] != "ACTIVE" else ""))
            for n in ctx["injuries"]) or "<li class=mute>No notable injury news in the last 48 hours.</li>",
        summ="".join("<li>%s</li>" % _e(t) for t in ctx["fa_summary"]), w=ctx["playoffs"]["weight"],
        fa=_table(["Player", "Pos", "Team", "Gain", "Model/gm", "ESPN/gm", "Consensus", "Light nights", "Playoff gms", "Next 2 wks", "Own chg", "Drop", "Signal"], fa),
        dr=_table(["#", "Player", "Pos", "Team", "Pts lost", "ROS", "Model/gm", "Consensus", "Last 7", "Light nights", "Playoff gms"], dr))


def _roster(ctx, manual):
    pn = ctx["player_news"]
    rows = []
    for p in ctx["roster"]:
        n = (pn.get(p["name"]) or [None])[0]
        news_cell = ('<span class="mute">%s:</span> %s <a href="%s" target="_blank" rel="noopener">more</a>' % (
            _e(n["date"].split()[0] + " " + n["date"].split()[1]), _e(n["summary"][:110] + ("..." if len(n["summary"]) > 110 else "")), _e(n["link"]))
            if n else "")
        rows.append([_e(p["name"]) + _inj(p) + _flag(p, ctx), _e(p["slot"]), _e(p["pos"]), _e(p["team"]), "%.0f" % p["ros"]]
                    + [_v(p["src"].get(k)) for k, _ in SRC_LABELS] + ["<b>%s</b>" % _v(p["cons"]),
                     "%d / %d" % (p["g_this"], p["g_next"]), "%d" % p["light"], "%d" % p["gp"], "%.1f" % p["fp"], _e(p["luck"]), news_cell])
    return _take(manual, "roster") + _accuracy_card(ctx) + '<div class="card">' + _table(
        ["Player", "Slot", "Pos", "Team", "ROS pts", "Model", "ESPN", "Pace", "L15", "Consensus", "Games wk/next", "Light nights", "GP", "Pts", "Signal", "Latest news"],
        rows, "wrap") + \
        '<p class="mute">Points per game by source. Model = your spreadsheet blended with actual results (drives every recommendation). '\
        'ESPN = ESPN\'s projection scored with your league\'s rules. Pace = this season, L15 = last 15 days (shown after 3 games). '\
        'Consensus = median of the available sources. &#9888; = sources differ by more than 25%%; hover for the numbers.</p>' + \
        ('<p class="mute">Sorted by rest-of-season points. Pts/GP blends the preseason projection with this season\'s actual '
         'results: actuals count 5%% through game 5, then %s. %s News: Rotowire via ESPN.</p></div>'
         % (", ".join("%d%% at %d" % (round(w * 100), g) for g, w in engine.ACTUAL_WEIGHT_CURVE[3:]), LIGHT_NOTE % ctx["light_nights"]))


def _playoffs(ctx, manual):
    po, pu = ctx["playoffs"], ctx["playoff_usable"]
    wk_h = [w.split(" (")[0] for w in po["weeks"]]
    pt = [["<b>%s</b>" % _e(t["name"]) if t["me"] else _e(t["name"])] + ["%.0f" % x for x in t["weeks"]] + ["%.0f" % t["total"], _pct(t["odds"])]
          for t in po["teams"]]
    tot = pu["totals"]
    days = "".join('<div class="day"><div>%s</div><div>%d play</div><div class="%s">%d wasted</div><div class="%s">%d open</div></div>' % (
        _e(d["date"][:3] + " " + d["date"].split()[-1]), d["playing"], "bad" if d["wasted"] else "mute", d["wasted"],
        "hot" if d["empty_sk"] >= 3 else "mute", d["empty_sk"] + d["empty_g"]) for d in pu["days"])
    tg = lambda rows: _table(["Player", "Pos", "Team", "Owner", "Games on open days", "Usable pts"], [
        [_e(r["name"]), r["group"], _e(r["team"]), _e(r["owner"]), "%d of %d" % (r["fit"], r["games"]), "%.1f" % r["usable_pts"]] for r in rows])
    pm = [[_e(r["name"]), r["group"], _e(r["team"])] + [str(x) for x in r["weeks"]] +
          ['<span class="%s">%d</span>' % ("bad" if r["total"] < po["avg_games"] - 0.5 else "good" if r["total"] > po["avg_games"] + 0.5 else "", r["total"])]
          for r in po["mine"]]
    return """<p class="mute">Fantasy playoffs: {weeks}. Every add on this site already counts playoff-week points x{w:g}.</p>
<div class="card"><h3>Usable games, day by day (current roster)</h3><div class="days">{days}</div>
<p>Starts filled <b>{used}</b> &middot; games wasted on the bench <b>{wasted}</b> &middot; open skater slots <b>{esk}</b> &middot; open goalie slots <b>{eg}</b></p>
<p class="mute">Raw games don't win playoff weeks - filled slots do. Wasted = more of your players play that day than you have slots. Open = slots nobody fills. Targets below play on your open days.</p></div>
<div class="grid"><div class="card"><h3>Free agents who fill your open playoff days</h3>{tfa}</div>
<div class="card"><h3>Trade targets who fill your open playoff days</h3>{ttr}</div></div>
<div class="card"><h3>Projected playoff-week points (current rosters)</h3>{pt}</div>
<div class="card"><h3>My players' playoff games (avg {avg})</h3>{pm}</div>""".format(
        weeks=_e(", ".join(po["weeks"])), w=po["weight"], days=days, used=tot["used"],
        wasted=tot["wasted"], esk=tot["empty_sk"], eg=tot["empty_g"], tfa=tg(pu["targets"]["fa"]), ttr=tg(pu["targets"]["trade"]),
        pt=_table(["Team"] + wk_h + ["Total", "Playoff odds"], pt), avg=po["avg_games"],
        pm=_table(["Player", "Pos", "Team"] + wk_h + ["Total"], pm))


def _league(ctx, manual):
    myid, size = ctx["me"]["id"], ctx["league"]["size"]
    rk = lambda r: '<span class="%s">#%d</span>' % ("good" if r <= 2 else "bad" if r >= size - 1 else "", r)
    bold = lambda name, mine: "<b>%s</b>" % _e(name) if mine else _e(name)
    wyr = ctx["where_you_rank"]
    rank_rows = [[g, rk(d["quality_rank"]), rk(d["cons_rank"]), "%.1f" % d["quality"], rk(d["ros_rank"]), "%.0f" % d["ros"], "%.0f" % d["ros_best"]]
                 for g, d in wyr.items()]
    weakest = ", ".join("%s (%s, %.0f)" % (_e(p["name"]), p["group"], p["ros"]) for p in ctx["weakest_starters"])
    st = {s["id"]: s for s in ctx["standings"]}
    teams = sorted(ctx["league_teams"], key=lambda t: -st[t["id"]]["strength"])
    pair = lambda r: "%s / %s" % (rk(r["q"]), rk(r["ros"]))
    land = [[bold(t["name"], t["me"]), _rec(st[t["id"]]), "%.0f" % st[t["id"]]["strength"]]
            + [pair(t["ranks"][g]) for g in ("F", "D", "G")]
            + ['<span class="bad">%s</span>' % t["need"], '<span class="good">%s</span>' % t["surplus"], _pct(st[t["id"]]["odds"])]
            for t in teams]
    sr = [[bold(s["name"], s["id"] == myid), _rec(s), "%.1f" % s["pf"], "%.1f" % s["pa"],
           "%.1f" % s["exp_wins"], _pct(s["odds"])] for s in ctx["standings"]]
    dives = []
    for t in teams:
        groups = []
        for g, lab in (("F", "Forwards"), ("D", "Defense"), ("G", "Goalies")):
            r = t["ranks"][g]
            rows = [[_e(p["name"]) + _inj(p), _e(p["pos"]), _e(p["team"]), _e(p["slot"]), "%.0f" % p["ros"], _v(p["cons"])]
                    for p in t["players"] if p["group"] == g]
            groups.append('<h3 style="margin-top:10px">%s &middot; quality %s, rest of season %s (%d pts)</h3>%s' % (
                lab, rk(r["q"]), rk(r["ros"]), r["ros_pts"], _table(["Player", "Pos", "Team", "Slot", "ROS pts", "Consensus"], rows)))
        dives.append('<details class="card"><summary><b>%s</b> <span class="mute">%s &middot; strength #%d &middot; needs %s, deep at %s</span></summary>%s</details>' % (
            _e(t["name"]), _rec(st[t["id"]]), teams.index(t) + 1, t["need"], t["surplus"], "".join(groups)))
    dl = ""
    if ctx.get("trade_deadline"):
        dl = '<p class="mute">Trade deadline: <b>%s</b> (%d days).</p>' % (_e(ctx["trade_deadline"]), ctx["days_to_deadline"])
    return """<div class="card"><h3>Where you rank</h3>{ranks}<p class="mute">Quality = per-game points of your top starters at each position (IR included, schedule ignored) - how roster rankers like Lineup Experts judge teams. Rest of season = starter points from optimal daily lineups with the real schedule; D ranks lower here because UTIL slots usually go to forwards. Weakest starters (ROS): {weakest}.</p></div>
<div class="card"><h3>League landscape</h3>{land}<p class="mute">Position ranks are quality / rest of season. Needs = each team's weakest group, deep at = its strongest: a team deep where you're thin and thin where you're deep is your natural trade partner.</p>{dl}</div>
<div class="card"><h3>Standings</h3>{sr}<p class="mute">Playoff odds: 4000 simulated seasons from current record + points for + each remaining matchup projected from today's rosters (top {po} make it).</p></div>
<h3 class="sec">Team by team</h3>{dives}""".format(
        ranks=_table(["Group", "Quality (model)", "Quality (consensus)", "Pts/game", "Rest of season", "ROS pts", "League best"], rank_rows),
        weakest=weakest or "-", dl=dl,
        land=_table(["Team", "Record", "Strength", "F", "D", "G", "Needs", "Deep at", "Playoff odds"], land),
        sr=_table(["Team", "Record", "PF", "PA", "Proj wins", "Playoff odds"], sr), po=ctx["league"]["playoff_teams"],
        dives="".join(dives))


LIGHT_NOTE = ("Light nights = rest-of-season games on nights when half the NHL or fewer plays (%d such nights left): "
              "the nights most of your lineup slots sit open, so these games almost always count.")


TABS = [("overview", "Overview", _overview), ("week", "Matchup", _week), ("stream", "Streaming", _streaming),
        ("fa", "Free Agents", _free_agents), ("roster", "Roster", _roster),
        ("playoffs", "Playoffs", _playoffs), ("league", "League", _league)]


def render(ctx: dict, manual: dict) -> str:
    me = ctx["me"]
    nav = "".join('<button data-t="%s">%s</button>' % (k, _e(n)) for k, n, _ in TABS)
    panes = "".join('<section id="t-%s">%s</section>' % (k, fn(ctx, manual)) for k, _, fn in TABS)
    return PAGE.replace("{{NAV}}", nav).replace("{{PANES}}", panes).replace("{{LEAGUE}}", _e(ctx["league"]["name"])).replace(
        "{{TEAM}}", _e(me["name"])).replace("{{UPDATED}}", _e(ctx["generated"])).replace(
        "{{WEEK}}", "Week %d of %d" % (ctx["period"]["matchup"], ctx["period"]["regular_matchups"]))


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Hockey Co-GM</title>
<style>
:root{--bg:#0e1015;--panel:#161a22;--line:#262c38;--fg:#e7e9ef;--mute:#8a93a6;--acc:#5aa9ff;--good:#3fcf8e;--warn:#ffb020;--bad:#ff6b6b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 -apple-system,system-ui,Segoe UI,sans-serif}
header{padding:14px 16px 0;display:flex;flex-wrap:wrap;gap:4px 16px;align-items:baseline}h1{font-size:18px;margin:0}
.mute{color:var(--mute);font-size:13px}nav{display:flex;gap:4px;overflow-x:auto;padding:10px 16px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:2}
nav button{background:none;border:1px solid var(--line);color:var(--mute);padding:6px 12px;border-radius:999px;font:inherit;font-size:14px;white-space:nowrap;cursor:pointer}
nav button.on{background:var(--acc);border-color:var(--acc);color:#081018;font-weight:600}
main{padding:12px 16px 40px;max-width:1100px;margin:0 auto}section{display:none}section.on{display:block}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:0 0 12px}
h3{margin:0 0 8px;font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mute)}
table.sched td,table.sched th{text-align:center;white-space:nowrap}table.sched td:first-child{text-align:left}.past{opacity:.4}table.sched td.hot{color:var(--good);font-weight:700}tr.sum td{font-weight:600;border-top:2px solid var(--line)}
h3.sec{margin:22px 0 6px;font-size:14px;color:var(--fg)}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(min(300px,100%),1fr))}.grid .card{margin:0 0 12px}
.hero{display:flex;gap:14px;align-items:center;margin:4px 0 14px}.grade{font-size:34px;font-weight:800;min-width:70px;text-align:center;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:8px}
.hl{font-size:17px;font-weight:600}.kpis{display:flex;flex-wrap:wrap;gap:4px 16px;color:var(--mute);margin-top:4px}.kpis b{color:var(--fg)}
.urgent{border-color:var(--warn)}.urgent p{margin:0;font-weight:600}
.take{border-left:3px solid var(--acc);padding:4px 12px;margin:0 0 12px}.take p{margin:0 0 4px;white-space:pre-line}.stamp{color:var(--mute);font-size:12px}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:14px}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);white-space:nowrap}th{color:var(--mute);font-weight:500}
.inj{background:var(--warn);color:#1a0f00;font-size:11px;font-weight:700;padding:1px 5px;border-radius:4px}
.good{color:var(--good)}.warnc{color:var(--warn)}.flag{color:var(--warn);cursor:help}.bad{color:var(--bad)}ul{margin:0 0 8px;padding-left:18px}
.tag{border:1px solid var(--line);color:var(--mute);font-size:11px;padding:0 5px;border-radius:4px}
.days{display:flex;gap:6px;overflow-x:auto;margin-bottom:6px}.day{min-width:62px;text-align:center;border:1px solid var(--line);border-radius:8px;padding:4px;font-size:12px}
.day .hot{color:var(--good);font-weight:700}
.news li{margin-bottom:8px}.cred{color:var(--mute);font-size:12px}.cred a{color:var(--acc)}
.ins{background:var(--acc);color:#081018;font-size:11px;font-weight:700;padding:1px 5px;border-radius:4px}
details.card summary{cursor:pointer}details.card[open] summary{margin-bottom:6px}
table.wrap td:last-child{white-space:normal;min-width:260px;font-size:13px}
.issues li.fix{color:var(--bad)}.issues li.check{color:var(--warn)}.issues li.ok{color:var(--good)}
</style></head><body>
<header><h1>{{TEAM}}</h1><span class="mute">{{LEAGUE}} &middot; {{WEEK}} &middot; updated {{UPDATED}} ET</span></header>
<nav>{{NAV}}</nav><main>{{PANES}}</main>
<script>
const bs=[...document.querySelectorAll('nav button')];
function show(k){bs.forEach(b=>b.classList.toggle('on',b.dataset.t===k));document.querySelectorAll('section').forEach(s=>s.classList.toggle('on',s.id==='t-'+k));try{localStorage.setItem('tab',k)}catch(e){}}
bs.forEach(b=>b.onclick=()=>show(b.dataset.t));let k='overview';try{k=localStorage.getItem('tab')||k}catch(e){}
show(bs.some(b=>b.dataset.t===k)?k:'overview');
</script></body></html>
"""
