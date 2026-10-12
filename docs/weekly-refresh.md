# Analysis refresh (Mondays + Thursdays)

Runs as a Claude cloud routine Monday and Thursday mornings (after the 8 AM Mac build). Numbers
and auto-notes rebuild daily at 8 AM and 5 PM ET on the Mac; this refresh is only the written takes. Goal: rewrite the written takes in
`state/ai_manual.json` so they agree with this week's numbers. Numbers are never
hand-typed into takes unless they come from `state/context.json` of this build.

## Cloud routine (default)

The cloud has no ESPN cookies or projections workbook, so it never runs a full build. The Mac's daily
build commits `state/context.json`; the routine writes takes from it and re-renders the page.

1. `git pull origin main`. Check `state/context.json` `generated` is from today; if it's older than
   24h, still write the takes but say so in the session summary (the Mac build is failing).
2. Read `state/context.json` (keys below) and the current `state/ai_manual.json`.
3. Rewrite `state/ai_manual.json` per the rules in step 4 of the Mac version below.
4. `python3 build.py --render-only && cp output/index.html site/index.html`
5. `git add state/ai_manual.json site/index.html && git commit -m "Analysis <date>"`, then
   `git pull --rebase origin main && git push origin main` (retry the pull+push if rejected).

## Mac version (fallback)

1. `cd ~/Claude/fantasy-hockey && git pull`
2. `python3 build.py` — confirm the log shows `Dashboard written` and read the
   `Analysis check:` line (stale takes, traded players, odds moves).
3. Read `state/context.json`: `me` (grade, odds, urgent), `notes`, `where_you_rank`, `matchup`,
   `streaming`, `free_agents` + `fa_summary`, `add_alerts`, `drop_ranking`, `opportunities`, `news`,
   `status_watch`, `goalies_today`, `recent`, `trades`, `playoffs` + `playoff_usable`, `standings`.
4. Rewrite `state/ai_manual.json`:
   - `date`: today (YYYY-MM-DD); `playoff_odds`: `me.playoff_odds`; `my_players`: names in `roster`.
   - `takes.overview` / `matchup` / `roster` / `free_agents` / `trades` / `playoffs`: 2-4 sentences each,
     opinionated, naming real players. Buy/sell follows playoff odds (`me.posture`):
     contenders hold producing starters and buy at the biggest need.
   - Injury/suspension statuses are flagged, never assumed long-term — say "check news". Use
     `status_watch` / `news` / `opportunities` for the latest reported facts and credit the reporter.
5. `python3 build.py` again — the `Analysis check` should report only the takes' age.
6. `cp output/index.html site/index.html && git add state/ai_manual.json site/index.html &&
   git commit -m "Analysis <date>" && git push` (Pages redeploys when `site/` changes).

Advice only: never change the ESPN lineup or roster.
