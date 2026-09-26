"""Self-contained dark HTML draft board."""
from __future__ import annotations

import html
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from fh import draft
from fh.espn import League

ET = ZoneInfo("America/New_York")


def _e(s) -> str:
    return html.escape(str(s))


def render(league: League, players: list, drafted: dict, now_pick: int, repl: dict,
           status: dict | None = None, refresh: int = 0) -> str:
    """drafted: playerId -> (overall, teamId). status: live-mode info (alert, stale, updated)."""
    status = status or {}
    by_id = {p.id: p for p in players}
    available = [p for p in players if p.id not in drafted]
    mine = [by_id[pid] for pid, (_, t) in drafted.items() if t == league.my_team_id and pid in by_id]
    horizon = draft.next_contested_pick(league.my_picks, now_pick)
    recs = draft.recommend(available, mine, league, now_pick, n=10)
    nd = draft.needs(mine, league)
    gplan = draft.goalie_plan(available, league, now_pick)
    upcoming = [p for p in league.my_picks if p >= now_pick]

    rows = []
    for p in sorted(available, key=lambda p: -p.vor)[:400]:
        pa = draft.p_available(p.market, horizon, now_pick) if horizon else 0
        rows.append({
            "o": p.overall, "n": p.name, "g": p.group, "pos": p.positions, "t": p.team,
            "fp": round(p.fp, 1), "pg": round(p.fp_per_gp, 2) if p.fp_per_gp else None,
            "vor": round(p.vor, 1), "adp": round(p.market, 1) if p.market < 400 else None,
            "sr": p.sheet_rank, "src": p.source, "inj": "" if p.injury == "ACTIVE" else p.injury,
            "b": p.boost, "pa": round(pa * 100), "val": round(p.market - p.overall, 0) if p.market < 400 else None,
        })

    alert = status.get("alert", "")
    pre = league.draft_time and not league.in_progress and not league.drafted
    order_note = ""
    if pre:
        order_note = "Draft order is re-randomized 1 hr before the draft; slot below is provisional until then."
    team_rows = "".join(
        "<tr><td>%d</td><td>%s</td><td>%s</td></tr>" % (ov, _e(league.teams.get(t, t)),
                                                         _e(by_id[pid].name + " (" + by_id[pid].group + ")") if pid in by_id else pid)
        for pid, (ov, t) in sorted(drafted.items(), key=lambda kv: -kv[1][0])[:24])
    rec_rows = "".join(
        "<tr><td>%d</td><td><b>%s</b> %s</td><td>%s</td><td>%.0f</td><td>%+.0f</td><td>%+.0f</td><td>%s</td><td>%s</td></tr>"
        % (i, _e(r.player.name), '<span class="inj">%s</span>' % _e(r.player.injury) if r.player.injury != "ACTIVE" else "",
           r.player.group, r.player.fp, r.player.vor, r.dropoff,
           "%.0f" % r.player.market if r.player.market < 400 else "-",
           "%d%%" % round(r.p_next * 100) if horizon else "-")
        for i, r in enumerate(recs, 1))
    my_rows = "".join("<tr><td>%s</td><td>%s</td><td>%.0f</td><td>%+.0f</td></tr>" % (_e(p.name), p.group, p.fp, p.vor)
                      for p in sorted(mine, key=lambda p: ("FDG".index(p.group), -p.vor)))
    open_s = ", ".join("%s %d" % (k, v) for k, v in nd.open_slots.items() if v)
    updated = datetime.now(ET).strftime("%-I:%M:%S %p ET")
    dt = league.draft_time.astimezone(ET).strftime("%a %b %-d, %-I:%M %p ET") if league.draft_time else "?"
    return TEMPLATE.format(
        refresh='<meta http-equiv="refresh" content="%d">' % refresh if refresh else "",
        title=_e(league.name), dt=dt, team=_e(league.teams.get(league.my_team_id, "?")),
        slot=league.my_slot or "?", size=league.size, now_pick=now_pick,
        total=len(league.pick_teams), on_clock=_e(league.teams.get(league.pick_teams[now_pick - 1], "?")) if now_pick <= len(league.pick_teams) else "done",
        upcoming=", ".join(str(p) for p in upcoming[:8]) or "none",
        alert='<div class="alert %s">%s</div>' % (status.get("level", "info"), _e(alert)) if alert else "",
        stale='<div class="alert warn">%s</div>' % _e(status["stale"]) if status.get("stale") else "",
        order_note='<div class="note">%s</div>' % _e(order_note) if order_note else "",
        updated=updated, rec_rows=rec_rows or "<tr><td colspan=9>-</td></tr>",
        my_rows=my_rows or "<tr><td colspan=4>No picks yet</td></tr>", open_s=_e(open_s) or "none",
        gplan="".join("<li>%s</li>" % _e(x) for x in gplan),
        repl=" · ".join("%s %.0f" % (g, v) for g, v in repl.items()),
        team_rows=team_rows or "<tr><td colspan=3>No picks yet</td></tr>",
        horizon=horizon or "-", data=json.dumps(rows).replace("</", "<\\/"),
    )


TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8">{refresh}
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Draft Board</title>
<style>
:root{{--bg:#0f1115;--panel:#171a21;--line:#262b36;--fg:#e6e8ee;--mute:#8b93a7;--acc:#5aa9ff;--good:#3fcf8e;--warn:#ffb020;--bad:#ff5c5c}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.4 -apple-system,system-ui,sans-serif}}
header{{padding:14px 16px;border-bottom:1px solid var(--line);display:flex;flex-wrap:wrap;gap:8px 24px;align-items:baseline}}
h1{{font-size:18px;margin:0}}h2{{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:0 0 8px}}
.k{{color:var(--mute)}}.wrap{{padding:12px 16px;display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(340px,1fr))}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px;overflow:auto}}
table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}td,th{{padding:4px 6px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}
th{{color:var(--mute);font-weight:500;cursor:pointer;position:sticky;top:0;background:var(--panel)}}
.alert{{margin:10px 16px 0;padding:10px 14px;border-radius:8px;font-weight:600;font-size:16px}}
.alert.clock{{background:#3a1212;border:1px solid var(--bad);color:#ffd6d6}}.alert.soon{{background:#3a2c0c;border:1px solid var(--warn)}}
.alert.warn{{background:#3a2c0c;border:1px solid var(--warn);font-size:14px}}.alert.info{{background:#10243a;border:1px solid var(--acc);font-size:14px}}
.note{{margin:10px 16px 0;color:var(--warn)}}.inj{{color:var(--bad);font-size:11px}}.gone{{color:var(--mute)}}.hi{{color:var(--good)}}.lo{{color:var(--bad)}}
.big{{grid-column:1/-1;max-height:75vh}}.ctl{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}}
input,select{{background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:5px 8px}}
ul{{margin:0;padding-left:18px}}
</style></head><body>
<header><h1>{title}</h1><span><span class="k">Me</span> {team} · slot {slot}/{size}</span>
<span><span class="k">Draft</span> {dt}</span><span><span class="k">Pick</span> {now_pick}/{total} · on clock: {on_clock}</span>
<span><span class="k">My picks</span> {upcoming}</span><span class="k">updated {updated}</span></header>
{alert}{stale}{order_note}
<div class="wrap">
<div class="card"><h2>Take now (horizon: my pick {horizon})</h2><table><tr><th>#</th><th>Player</th><th>Pos</th><th>FP</th><th>VOR</th><th>Drop</th><th>ADP</th><th>Back?</th></tr>{rec_rows}</table>
<p class="k">Drop = VOR lost at that position if I wait. Back? = chance he's still there at my next contested pick.</p></div>
<div class="card"><h2>My roster · open: {open_s}</h2><table><tr><th>Player</th><th>Pos</th><th>FP</th><th>VOR</th></tr>{my_rows}</table>
<h2 style="margin-top:12px">Goalie plan</h2><ul>{gplan}</ul><p class="k">Replacement FP: {repl}</p></div>
<div class="card"><h2>Recent picks</h2><table><tr><th>#</th><th>Team</th><th>Player</th></tr>{team_rows}</table></div>
<div class="card big"><h2>Big board (available)</h2>
<div class="ctl"><input id="q" placeholder="search"><select id="pos"><option value="">All</option><option>F</option><option>D</option><option>G</option></select></div>
<table id="bb"><thead><tr><th data-k="o">VOR rk</th><th data-k="n">Player</th><th data-k="pos">Pos</th><th data-k="t">Team</th><th data-k="fp">FP</th><th data-k="pg">FP/GP</th><th data-k="vor">VOR</th><th data-k="adp">ESPN ADP</th><th data-k="val">Value vs ADP</th><th data-k="pa">Back at my pick</th><th data-k="sr">Sheet rk</th><th data-k="b">Boost</th><th data-k="src">Proj</th></tr></thead><tbody></tbody></table></div>
</div>
<script>
const D={data};let sk="o",asc=true;
function draw(){{const q=document.getElementById('q').value.toLowerCase(),ps=document.getElementById('pos').value;
const r=D.filter(x=>(!ps||x.g===ps)&&(!q||x.n.toLowerCase().includes(q))).sort((a,b)=>{{const u=a[sk],v=b[sk];if(u==null)return 1;if(v==null)return -1;return (u<v?-1:u>v?1:0)*(asc?1:-1)}});
document.querySelector('#bb tbody').innerHTML=r.map(x=>`<tr class="${{x.pa<35?'gone':''}}"><td>${{x.o}}</td><td>${{x.n}} ${{x.inj?'<span class=inj>'+x.inj+'</span>':''}}</td><td>${{x.pos}}</td><td>${{x.t}}</td><td>${{x.fp}}</td><td>${{x.pg??''}}</td><td>${{x.vor}}</td><td>${{x.adp??''}}</td><td class="${{x.val>=15?'hi':x.val<=-15?'lo':''}}">${{x.val==null?'':(x.val>0?'+':'')+x.val}}</td><td>${{x.pa}}%${{x.pa<35?' (likely gone)':''}}</td><td>${{x.sr??''}}</td><td>${{x.b}}</td><td>${{x.src}}</td></tr>`).join('')}}
document.querySelectorAll('#bb th').forEach(th=>th.onclick=()=>{{const k=th.dataset.k;asc=sk===k?!asc:!['fp','pg','vor','val','pa'].includes(k);sk=k;draw()}});
// Keep search/filter/sort/scroll across the live auto-refresh.
function save(){{try{{localStorage.setItem('bb',JSON.stringify({{q:document.getElementById('q').value,p:document.getElementById('pos').value,sk,asc,y:scrollY}}))}}catch(e){{}}}}
try{{const s=JSON.parse(localStorage.getItem('bb')||'null');if(s){{document.getElementById('q').value=s.q;document.getElementById('pos').value=s.p;sk=s.sk;asc=s.asc;addEventListener('load',()=>scrollTo(0,s.y))}}}}catch(e){{}}
addEventListener('beforeunload',save);
document.getElementById('q').oninput=()=>{{draw();save()}};document.getElementById('pos').onchange=()=>{{draw();save()}};draw();
</script></body></html>
"""
