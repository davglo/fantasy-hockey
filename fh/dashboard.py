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


def render(ctx: dict, manual: dict) -> str:
    me, st = ctx["me"], ctx["standings"]
    myid = me["id"]
    tabs = []

    # Overview
    needs = ctx["me"]["needs"]
    need_rows = [[g, "%.0f" % d["weakest"], "%.0f" % d["typical"],
                  '<span class="%s">%+.0f</span>' % ("bad" if d["gap"] > 5 else "good" if d["gap"] < -5 else "", -d["gap"]), d["count"]]
                 for g, d in needs.items()]
    land = [["<b>%s</b>" % _e(s["name"]) if s["id"] == myid else _e(s["name"]), "%d-%d%s" % (s["w"], s["l"], "-%d" % s["t"] if s["t"] else ""),
             "%.0f" % s["pf"], "%.0f" % s["strength"], _pct(s["odds"])] for s in sorted(st, key=lambda s: -s["strength"])]
    tabs.append(("overview", "Overview", """
<div class="hero"><div class="grade">{grade}</div><div><div class="hl">{headline}</div>
<div class="kpis"><span><b>{odds}</b> playoff odds</span><span><b>#{rank}</b> of {size} strength</span></div></div></div>
<div class="card urgent"><h3>Most urgent</h3><p>{urgent}</p></div>
{take}
<div class="grid">
<div class="card"><h3>Posture</h3><p>{posture}</p></div>
<div class="card"><h3>Positional needs</h3>{needs}<p class="mute">ROS points: my weakest starter vs a typical league starter. Negative = below the league.</p></div>
</div>
<div class="card"><h3>League landscape</h3>{land}<p class="mute">Strength = projected points per full week with an optimal daily lineup, rest of season.</p></div>
""".format(grade=_e(me["grade"]), headline=_e(me["headline"]), odds=_pct(me["playoff_odds"]), rank=me["rank"],
           size=ctx["league"]["size"], urgent=_e(me["urgent"]), take=_take(manual, "overview"), posture=_e(me["posture"]),
           needs=_table(["Pos", "My weakest", "Typical", "vs league", "Rostered"], need_rows),
           land=_table(["Team", "Record", "PF", "Strength", "Playoff odds"], land))))

    # This week
    m = ctx.get("matchup")
    if m:
        days = [[d["date"], "%.1f" % d["me"], "%.1f" % d["opp"], "%d / %d" % (d["me_starts"], d["opp_starts"])] for d in m["days"]]
        wk = """
<div class="hero"><div class="grade">{wp}</div><div><div class="hl">vs {opp} &middot; {dates}</div>
<div class="kpis"><span>Now <b>{am:.1f}</b> - <b>{ao:.1f}</b></span><span>Projected <b>{pm:.0f}</b> - <b>{po:.0f}</b></span>
<span>Starts left <b>{sm}</b> vs <b>{so}</b></span></div></div></div>
{take}
<div class="card"><h3>Day by day (optimal lineups)</h3>{days}
<p class="mute">Win chance uses a normal model with each team's weekly score varying about 15%. Points league: every start counts, so the games-played edge matters.</p></div>
""".format(wp=_pct(m["win_prob"]), opp=_e(m["opp"]), dates=_e(m["dates"]), am=m["actual_me"], ao=m["actual_opp"],
           pm=m["proj_me"], po=m["proj_opp"], sm=m["starts_me"], so=m["starts_opp"], take=_take(manual, "matchup"),
           days=_table(["Day", "Me", "Opp", "Starts me / opp"], days))
    else:
        wk = '<div class="card"><p>No matchup this week.</p></div>'
    tabs.append(("week", "This Week", wk))

    # Roster
    rows = [[_e(p["slot"]), _e(p["name"]) + _inj(p), _e(p["pos"]), _e(p["team"]), "%.0f" % p["ros"], "%.2f" % p["rate"],
             "%d / %d" % (p["g_this"], p["g_next"]), "%d" % p["gp"], "%.1f" % p["fp"], _e(p["luck"])] for p in ctx["roster"]]
    tabs.append(("roster", "Roster", _take(manual, "roster") + '<div class="card">' + _table(
        ["Slot", "Player", "Pos", "Team", "ROS pts", "Pts/GP", "Games wk/next", "GP", "Pts", "Signal"], rows) +
        '<p class="mute">Pts/GP blends the preseason projection (worth %s games) with this season\'s actual rate. Line and power-play role: not available from ESPN.</p></div>' % 15))

    # Lineup
    lu = []
    for key in ("today", "tomorrow"):
        d = ctx["lineup"][key]
        iss = "".join('<li class="%s">%s</li>' % (i["severity"], _e(i["text"])) for i in d["issues"]) or '<li class="ok">Lineup is optimal.</li>'
        playing = ", ".join(_e(p["name"]) + (" (vs %s)" % _e(p["opp"]) if p["opp"] else "") for p in d["playing"]) or "nobody"
        lu.append('<div class="card"><h3>%s &middot; %s</h3><ul class="issues">%s</ul>%s<p><span class="mute">Playing:</span> %s</p></div>' % (
            key.title(), _e(d["date"]), iss, '<p class="mute">Points left on the table: %.1f</p>' % d["gain"] if d["gain"] > 0.05 else "", playing))
    tabs.append(("lineup", "Lineup", '<p class="mute">Advice only - nothing here changes your ESPN lineup.</p>' + "".join(lu)))

    # Free agents (rest of season)
    fa = [[_e(p["name"]) + _inj(p) + (' <span class="tag">waivers</span>' if p["status"] == "WAIVERS" else ""), _e(p["pos"]),
           _e(p["team"]), "%+.1f" % p["gain"], "%d" % p["po_games"], "%+.1f" % p["week_gain"], "%+.1f" % p["own_chg"],
           _e(p["drop"]), _e(p["luck"])] for p in ctx["free_agents"]]
    tabs.append(("fa", "Free Agents", """{take}
<div class="card"><h3>Best rest-of-season adds</h3><ul>{summ}</ul>{fa}
<p class="mute">Gain = projected points added over the rest of the season with day-by-day optimal lineups, playoff weeks counted x{w:g}, using the best drop for each player.
Drops never include IR or injury/suspension-flagged players, and never leave you under 2 healthy goalies. Short-term pickups live on the Streaming tab.</p></div>""".format(
        take=_take(manual, "free_agents"), summ="".join("<li>%s</li>" % _e(t) for t in ctx["fa_summary"]),
        w=ctx["playoffs"]["weight"],
        fa=_table(["Player", "Pos", "Team", "Gain", "Playoff gms", "Next 2 wks", "Own chg", "Drop", "Signal"], fa))))

    # Streaming (matchup to matchup)
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
    gs = [[_e(p["name"]) + _inj(p) + (' <span class="tag">waivers</span>' if p["status"] == "WAIVERS" else ""), _e(p["team"]),
           p["games"], "%.1f" % p["exp"], _e(", ".join(p["opps"]))] for p in s["goalies"]]
    parts.append('<div class="grid"><div class="card"><h3>Most games (this wk / next)</h3>%s</div>'
                 '<div class="card"><h3>Goalie streams this week</h3>%s<p class="mute">Saves are 0.1 here, so a streamed start can go negative - '
                 'only stream volume, not hope.</p></div></div>' % (_table(["Team", "This", "Next"], gm),
                                                                    _table(["Goalie", "Team", "Games", "Exp pts", "Opponents"], gs)))
    tabs.append(("stream", "Streaming", "".join(parts)))

    # Playoffs
    po = ctx["playoffs"]
    wk_h = [w.split(" (")[0] for w in po["weeks"]]
    pt = [["<b>%s</b>" % _e(t["name"]) if t["me"] else _e(t["name"])] + ["%.0f" % x for x in t["weeks"]] + ["%.0f" % t["total"], _pct(t["odds"])]
          for t in po["teams"]]
    pm = [[_e(r["name"]), r["group"], _e(r["team"])] + [str(x) for x in r["weeks"]] +
          ['<span class="%s">%d</span>' % ("bad" if r["total"] < po["avg_games"] - 0.5 else "good" if r["total"] > po["avg_games"] + 0.5 else "", r["total"])]
          for r in po["mine"]]
    pn = [[_e(r["team"])] + [str(x) for x in r["weeks"]] + [str(r["total"])] for r in po["nhl"]]
    tabs.append(("playoffs", "Playoffs", """{take}
<p class="mute">Fantasy playoffs: {weeks}. Every add and trade on this site already counts playoff-week points x{w:g}.</p>
<div class="card"><h3>Projected playoff-week points (current rosters)</h3>{pt}</div>
<div class="grid"><div class="card"><h3>My players' playoff games (avg {avg})</h3>{pm}<p class="mute">Red = light playoff schedule: first to go in a trade or drop late in the season.</p></div>
<div class="card"><h3>NHL teams: playoff-week games</h3>{pn}</div></div>""".format(
        take=_take(manual, "playoffs"), weeks=_e(", ".join(po["weeks"])), w=po["weight"], avg=po["avg_games"],
        pt=_table(["Team"] + wk_h + ["Total", "Playoff odds"], pt), pm=_table(["Player", "Pos", "Team"] + wk_h + ["Total"], pm),
        pn=_table(["Team"] + wk_h + ["Total"], pn))))

    # Trades
    tr = [[_e(t["partner"]), _e(" + ".join(t["give"])), _e(" + ".join(t["get"])), "%+.1f" % t["my_gain"], "%+.1f" % t["their_gain"],
           "%.0f / %.0f" % (t["market_give"], t["market_get"]), _e(t["backfill"]), _pct(t["partner_odds"])] for t in ctx["trades"]]
    tabs.append(("trades", "Trade Finder", _take(manual, "trades") + '<div class="card">' + _table(
        ["Partner", "I give", "I get", "My gain", "Their gain", "Market give / get", "Roster move", "Their odds"], tr) +
        '<p class="mute">Shown only if the partner would accept on ESPN market value (what they see: ESPN rank/ADP, shifting to ESPN\'s season '
        'player rater as games are played, stars weighted up) and doesn\'t lose more than %.0f projected points. Gains = rest-of-season + playoff-week '
        'points with optimal daily lineups, recomputed every build.</p></div>' % 10))

    # Standings
    sr = [["<b>%s</b>" % _e(s["name"]) if s["id"] == myid else _e(s["name"]), "%d-%d%s" % (s["w"], s["l"], "-%d" % s["t"] if s["t"] else ""),
           "%.1f" % s["pf"], "%.1f" % s["pa"], "%.1f" % s["exp_wins"], _pct(s["odds"])] for s in st]
    tabs.append(("standings", "Standings", '<div class="card">' + _table(["Team", "Record", "PF", "PA", "Proj wins", "Playoff odds"], sr) +
                 '<p class="mute">Playoff odds: %d simulated seasons from current record + points for + each remaining matchup projected from today\'s rosters (top %d make it).</p></div>'
                 % (4000, ctx["league"]["playoff_teams"])))

    nav = "".join('<button data-t="%s">%s</button>' % (k, _e(n)) for k, n, _ in tabs)
    panes = "".join('<section id="t-%s">%s</section>' % (k, body) for k, _, body in tabs)
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
