"""In-season dashboard: one self-contained dark HTML page with tabs, rendered from the context dict."""
from __future__ import annotations

import html


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
    wyr = ctx["where_you_rank"]
    size = ctx["league"]["size"]
    rank_rows = [[g, "%.0f" % d["mine"], '<span class="%s">#%d</span>' % ("good" if d["rank"] <= 2 else "bad" if d["rank"] >= size - 1 else "", d["rank"]),
                  "%.0f" % d["median"], "%.0f" % d["best"]] for g, d in wyr.items()]
    weakest = ", ".join("%s (%s, %.0f)" % (_e(p["name"]), p["group"], p["ros"]) for p in ctx["weakest_starters"])
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
           '<span class="good">confirmed</span>' if g["confirmed"] else '<span class="mute">unconfirmed</span>',
           "%.1f" % g["exp"], "%.2f" % g["opp_gf"] if g["opp_gf"] else "-", _e(g["waiver"])] for g in ctx["goalies_today"]]
    nw = "".join(_news_li(n) for n in ctx["news"])
    sw = "".join(_news_li(n, ' <span class="inj">%s</span>' % _e(n["status"])) for n in ctx["status_watch"])
    land = [["<b>%s</b>" % _e(s["name"]) if s["id"] == myid else _e(s["name"]), _rec(s), "%.0f" % s["pf"], "%.0f" % s["strength"], _pct(s["odds"])]
            for s in sorted(ctx["standings"], key=lambda s: -s["strength"])]
    cad = ctx["cadence"]
    return """
<div class="hero"><div class="grade">{grade}</div><div><div class="hl">{headline}</div>
<div class="kpis"><span><b>{odds}</b> playoff odds</span><span><b>#{rank}</b> of {size} strength</span><span>{posture}</span></div></div></div>
<div class="card urgent"><h3>Most urgent</h3><p>{urgent}</p></div>
<div class="card"><h3>Today's notes</h3><ul>{notes}</ul><p class="mute">Numbers and these notes refresh daily at {daily}. Written analysis below is updated {takes} (last: {tdate}).</p></div>
{take}
<div class="grid">
<div class="card"><h3>Add alerts</h3><ul>{adds}</ul></div>
<div class="card"><h3>Drop watch</h3><ul>{drops}</ul><p class="mute">Full ranking on Free Agents.</p></div>
</div>
<div class="card"><h3>Goalies to add today</h3>{gt}<p class="mute">Free-agent goalies whose team plays today. Confirmed = a "slated to start" report in the last 20h. Opp GF = opponent goals per game (this season, steadied with last season).</p></div>
<div class="card"><h3>News that matters</h3><ul class="news">{nw}</ul>{sw}</div>
<div class="grid">
<div class="card"><h3>Where you rank (projected pts/week)</h3>{ranks}<p class="mute">Starter points from optimal daily lineups over a full week, vs all {size} teams. Weakest starters (ROS): {weakest}.</p></div>
<div class="card"><h3>League landscape</h3>{land}</div>
</div>""".format(
        grade=_e(me["grade"]), headline=_e(me["headline"]), odds=_pct(me["playoff_odds"]), rank=me["rank"], size=size,
        posture=_e(me["posture"]), urgent=_e(me["urgent"]), notes="".join("<li>%s</li>" % _e(n) for n in ctx["notes"]),
        daily=_e(cad["daily_at"]), takes=_e(cad["takes_days"]), tdate=_e(manual.get("date", "never")),
        take=_take(manual, "overview"), adds=adds or "<li class=mute>No add clears the bar today.</li>",
        drops=drops or "<li class=mute>Nothing to cut.</li>",
        gt=_table(["Goalie", "Team", "Opp", "Start", "Exp pts", "Opp GF", "Waivers"], gt),
        nw=nw or "<li class=mute>No fresh news on your players in the last 72h.</li>",
        sw='<h3 style="margin-top:10px">Status watch</h3><ul class="news">%s</ul>' % sw if sw else "",
        ranks=_table(["Group", "Mine", "Rank", "Median", "Best"], rank_rows), weakest=weakest or "-",
        land=_table(["Team", "Record", "PF", "Strength", "Playoff odds"], land))


def _week(ctx, manual):
    m = ctx.get("matchup")
    if not m:
        return '<div class="card"><p>No matchup this week.</p></div>'
    days = [[d["date"], "%.1f" % d["me"], "%.1f" % d["opp"], "%d / %d" % (d["me_starts"], d["opp_starts"])] for d in m["days"]]
    return """
<div class="hero"><div class="grade">{wp}</div><div><div class="hl">vs {opp} &middot; {dates}</div>
<div class="kpis"><span>Now <b>{am:.1f}</b> - <b>{ao:.1f}</b></span><span>Projected <b>{pm:.0f}</b> - <b>{po:.0f}</b></span>
<span>Starts left <b>{sm}</b> vs <b>{so}</b></span></div></div></div>
{take}
<div class="card"><h3>Day by day (optimal lineups)</h3>{days}
<p class="mute">Win chance uses a normal model with each team's weekly score varying about 15%. Points league: every start counts, so the games-played edge matters.</p></div>
""".format(wp=_pct(m["win_prob"]), opp=_e(m["opp"]), dates=_e(m["dates"]), am=m["actual_me"], ao=m["actual_opp"],
           pm=m["proj_me"], po=m["proj_opp"], sm=m["starts_me"], so=m["starts_opp"], take=_take(manual, "matchup"),
           days=_table(["Day", "Me", "Opp", "Starts me / opp"], days))


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
    gm = [[_e(g["team"]), g["this"], g["next"]] for g in s["games"][:12]]
    gs = [[_e(p["name"]) + _inj(p), _e(p["team"]), p["games"], "%.1f" % p["exp"], _e(", ".join(p["opps"]))] for p in s["goalies"]]
    parts.append('<div class="grid"><div class="card"><h3>Most games (this wk / next)</h3>%s</div>'
                 '<div class="card"><h3>Goalie streams this week</h3>%s</div></div>' % (
                     _table(["Team", "This", "Next"], gm), _table(["Goalie", "Team", "Games", "Exp pts", "Opponents"], gs)))
    return "".join(parts)


def _free_agents(ctx, manual):
    opp = "".join('<li><b>%s</b> (%s %s) %s &rarr; %s <span class="cred">%s &middot; <a href="%s" target="_blank" rel="noopener">ESPN news</a></span><br><span class="mute">%s</span></li>' % (
        _e(o["about"]), _e(o["about_team"]), o["about_group"], _e("/".join(o["kinds"])),
        "; ".join("<b>%s</b> (%s, %+.0f ROS, %+.1f next 2 wks%s)" % (_e(b["name"]), _e(b["pos"]), b["gain"], b["gain_2wk"],
                                                                    ", " + _e(b["waiver"]) if b["waiver"] else "") for b in o["beneficiaries"]),
        _e(o["news"]["credit"]), _e(o["news"]["link"]), _e(o["news"]["summary"])) for o in ctx["opportunities"])
    rec = ctx["recent"]
    perf = lambda rows: _table(["Player", "Pos", "Team", "Pts", "Line", "Own chg"], [
        [_e(r["name"]) + _inj(r), _e(r["pos"]), _e(r["team"]), "%.1f" % r["pts"], _e(r["line"]), "%+.1f" % r["own_chg"]] for r in rows])
    fa = [[_e(p["name"]) + _inj(p) + (' <span class="tag">%s</span>' % _e(p["waiver"]) if p["waiver"] else ""), _e(p["pos"]),
           _e(p["team"]), "%+.1f" % p["gain"], "%d" % p["po_games"], "%+.1f" % p["week_gain"], "%+.1f" % p["own_chg"],
           _e(p["drop"]) + ('<br><span class="mute">or %s</span>' % _e(", ".join(p["alt_drops"])) if p["alt_drops"] else ""),
           _e(p["luck"])] for p in ctx["free_agents"]]
    dr = [["%d" % (i + 1), _e(d["name"]), d["group"], _e(d["team"]), "%.0f" % d["cost"], "%.0f" % d["ros"], "%.1f" % d["last7"], d["po_games"]]
          for i, d in enumerate(ctx["drop_ranking"])]
    return """{take}
<div class="card"><h3>Opportunity alerts</h3><ul class="news">{opp}</ul><p class="mute">News that opens a role for a free agent: injuries, suspensions, trades and demotions on players across the league, matched to free agents named in the report or on the same team and position. Gains are before any role bump - the news is the edge.</p></div>
<div class="grid"><div class="card"><h3>Best free agents {yd}</h3>{pd}</div><div class="card"><h3>Best free agents, last 7 days</h3>{pw}</div></div>
<div class="card"><h3>Best rest-of-season adds</h3><ul>{summ}</ul>{fa}
<p class="mute">Gain = rest-of-season points added (optimal daily lineups, playoff weeks x{w:g}) with the best drop; next-best drops shown under it.</p></div>
<div class="card"><h3>Drop ranking</h3>{dr}<p class="mute">Points lost (rest of season + playoff weeks x{w:g}) if you cut each player, cheapest first. IR, injury/suspension-flagged players and your last two healthy goalies are excluded.</p></div>""".format(
        take=_take(manual, "free_agents"), opp=opp or "<li class=mute>No league news creating free-agent value right now.</li>",
        yd=_e(rec["yesterday"]), pd=perf(rec["day"]), pw=perf(rec["week"]),
        summ="".join("<li>%s</li>" % _e(t) for t in ctx["fa_summary"]), w=ctx["playoffs"]["weight"],
        fa=_table(["Player", "Pos", "Team", "Gain", "Playoff gms", "Next 2 wks", "Own chg", "Drop", "Signal"], fa),
        dr=_table(["#", "Player", "Pos", "Team", "Pts lost", "ROS", "Last 7", "Playoff gms"], dr))


def _roster(ctx, manual):
    pn = ctx["player_news"]
    rows = []
    for p in ctx["roster"]:
        n = (pn.get(p["name"]) or [None])[0]
        news_cell = ('<span class="mute">%s:</span> %s <a href="%s" target="_blank" rel="noopener">more</a>' % (
            _e(n["date"].split()[0] + " " + n["date"].split()[1]), _e(n["summary"][:110] + ("..." if len(n["summary"]) > 110 else "")), _e(n["link"]))
            if n else "")
        rows.append([_e(p["name"]) + _inj(p), _e(p["slot"]), _e(p["pos"]), _e(p["team"]), "%.0f" % p["ros"], "%.2f" % p["rate"],
                     "%d / %d" % (p["g_this"], p["g_next"]), "%d" % p["gp"], "%.1f" % p["fp"], _e(p["luck"]), news_cell])
    return _take(manual, "roster") + '<div class="card">' + _table(
        ["Player", "Slot", "Pos", "Team", "ROS pts", "Pts/GP", "Games wk/next", "GP", "Pts", "Signal", "Latest news"], rows, "wrap") + \
        '<p class="mute">Sorted by rest-of-season points. Pts/GP blends the preseason projection (worth 15 games) with this season. News: Rotowire via ESPN.</p></div>'


def _trades(ctx, manual):
    tr = [[_e(t["partner"]), _e(" + ".join(t["give"])), _e(" + ".join(t["get"])), "%+.1f" % t["my_gain"], "%+.1f" % t["their_gain"],
           "%.0f / %.0f" % (t["market_give"], t["market_get"]), _e(t["backfill"]), _pct(t["partner_odds"])] for t in ctx["trades"]]
    dl = ""
    if ctx.get("trade_deadline"):
        dl = '<p class="mute">Trade deadline: <b>%s</b> (%d days).</p>' % (_e(ctx["trade_deadline"]), ctx["days_to_deadline"])
    return _take(manual, "trades") + dl + '<div class="card">' + _table(
        ["Partner", "I give", "I get", "My gain", "Their gain", "Market give / get", "Roster move", "Their odds"], tr) + \
        ('<p class="mute">Shown only if the partner would accept on ESPN market value (ESPN rank/ADP, shifting to ESPN\'s season player rater as '
         'games are played, stars weighted up) and doesn\'t lose more than 10 projected points. Gains = rest-of-season + playoff-week points.</p></div>')


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
    return """{take}
<p class="mute">Fantasy playoffs: {weeks}. Every add and trade on this site already counts playoff-week points x{w:g}.</p>
<div class="card"><h3>Usable games, day by day (current roster)</h3><div class="days">{days}</div>
<p>Starts filled <b>{used}</b> &middot; games wasted on the bench <b>{wasted}</b> &middot; open skater slots <b>{esk}</b> &middot; open goalie slots <b>{eg}</b></p>
<p class="mute">Raw games don't win playoff weeks - filled slots do. Wasted = more of your players play that day than you have slots. Open = slots nobody fills. Targets below play on your open days.</p></div>
<div class="grid"><div class="card"><h3>Free agents who fill your open playoff days</h3>{tfa}</div>
<div class="card"><h3>Trade targets who fill your open playoff days</h3>{ttr}</div></div>
<div class="card"><h3>Projected playoff-week points (current rosters)</h3>{pt}</div>
<div class="card"><h3>My players' playoff games (avg {avg})</h3>{pm}</div>""".format(
        take=_take(manual, "playoffs"), weeks=_e(", ".join(po["weeks"])), w=po["weight"], days=days, used=tot["used"],
        wasted=tot["wasted"], esk=tot["empty_sk"], eg=tot["empty_g"], tfa=tg(pu["targets"]["fa"]), ttr=tg(pu["targets"]["trade"]),
        pt=_table(["Team"] + wk_h + ["Total", "Playoff odds"], pt), avg=po["avg_games"],
        pm=_table(["Player", "Pos", "Team"] + wk_h + ["Total"], pm))


def _standings(ctx, manual):
    myid = ctx["me"]["id"]
    sr = [["<b>%s</b>" % _e(s["name"]) if s["id"] == myid else _e(s["name"]), _rec(s), "%.1f" % s["pf"], "%.1f" % s["pa"],
           "%.1f" % s["exp_wins"], _pct(s["odds"])] for s in ctx["standings"]]
    return '<div class="card">' + _table(["Team", "Record", "PF", "PA", "Proj wins", "Playoff odds"], sr) + \
        ('<p class="mute">Playoff odds: 4000 simulated seasons from current record + points for + each remaining matchup projected from '
         'today\'s rosters (top %d make it).</p></div>' % ctx["league"]["playoff_teams"])


TABS = [("overview", "Overview", _overview), ("week", "This Week", _week), ("stream", "Streaming", _streaming),
        ("fa", "Free Agents", _free_agents), ("roster", "Roster", _roster), ("trades", "Trade Finder", _trades),
        ("playoffs", "Playoffs", _playoffs), ("standings", "Standings", _standings)]


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
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(min(300px,100%),1fr))}.grid .card{margin:0 0 12px}
.hero{display:flex;gap:14px;align-items:center;margin:4px 0 14px}.grade{font-size:34px;font-weight:800;min-width:70px;text-align:center;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:8px}
.hl{font-size:17px;font-weight:600}.kpis{display:flex;flex-wrap:wrap;gap:4px 16px;color:var(--mute);margin-top:4px}.kpis b{color:var(--fg)}
.urgent{border-color:var(--warn)}.urgent p{margin:0;font-weight:600}
.take{border-left:3px solid var(--acc);padding:4px 12px;margin:0 0 12px}.take p{margin:0 0 4px;white-space:pre-line}.stamp{color:var(--mute);font-size:12px}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:14px}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);white-space:nowrap}th{color:var(--mute);font-weight:500}
.inj{background:var(--warn);color:#1a0f00;font-size:11px;font-weight:700;padding:1px 5px;border-radius:4px}
.good{color:var(--good)}.bad{color:var(--bad)}ul{margin:0 0 8px;padding-left:18px}
.tag{border:1px solid var(--line);color:var(--mute);font-size:11px;padding:0 5px;border-radius:4px}
.days{display:flex;gap:6px;overflow-x:auto;margin-bottom:6px}.day{min-width:62px;text-align:center;border:1px solid var(--line);border-radius:8px;padding:4px;font-size:12px}
.day .hot{color:var(--good);font-weight:700}
.news li{margin-bottom:8px}.cred{color:var(--mute);font-size:12px}.cred a{color:var(--acc)}
.ins{background:var(--acc);color:#081018;font-size:11px;font-weight:700;padding:1px 5px;border-radius:4px}
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
