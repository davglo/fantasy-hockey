# Weekly analysis refresh

Run every Monday morning (new matchup week). Goal: rewrite the written takes in
`state/ai_manual.json` so they agree with this week's numbers. Numbers are never
hand-typed into takes unless they come from `state/context.json` of this build.

1. `cd ~/Claude/fantasy-hockey && git pull`
2. `python3 build.py` — confirm the log shows `Dashboard written` and read the
   `Analysis check:` line (stale takes, traded players, odds moves).
3. Read `state/context.json`: `me` (grade, odds, needs, urgent), `matchup`, `streaming`, `playoffs`,
   `free_agents` + `fa_summary`, `trades`, `standings`, `lineup`.
4. Rewrite `state/ai_manual.json`:
   - `date`: today (YYYY-MM-DD); `playoff_odds`: `me.playoff_odds`; `my_players`: names in `roster`.
   - `takes.overview` / `matchup` / `roster` / `free_agents` / `trades` / `playoffs`: 2-4 sentences each,
     opinionated, naming real players. Buy/sell follows playoff odds (`me.posture`):
     contenders hold producing starters and buy at the biggest need.
   - Injury/suspension statuses are flagged, never assumed long-term — say "check news".
5. `python3 build.py` again — the `Analysis check` should report only the takes' age.
6. `cp output/index.html site/index.html && git add state/ai_manual.json site/index.html &&
   git commit -m "Weekly analysis <date>" && git push` (Pages redeploys when `site/` changes).

Advice only: never change the ESPN lineup or roster.
