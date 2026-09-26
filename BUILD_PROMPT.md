# BUILD: ESPN Fantasy Hockey Co-GM (redraft)

## Role
Be my co-manager for a single-season (redraft) ESPN fantasy hockey league — an
opinionated advisor with real calls on my actual players, not just a tool author.
Build a self-contained dark HTML dashboard, and be my **live co-pilot during the
draft**.

## Deadline — this drives the order of work
**The draft is Sunday 2026-09-27 at 8:30 PM ET.** Phase 1 (draft board + live
draft co-pilot) must work end to end, tested, before then. Phase 2 (the full
in-season dashboard) starts AFTER the draft. Don't build Phase 2 features first.

## League (verified 2026-09-26)
- ESPN fantasy hockey, game code `fhl`, **season 2027** (the 2026-27 NHL season).
- League ID **1366258109** — **private** (the API returns 401 without login).
- Scoring type (points vs H2H categories), roster slots, team count, and draft
  type (snake vs auction) and order: **read them live** once auth works. Points vs
  categories changes the whole valuation model, and auction needs dollar values.

## Auth (private league)
The API needs two ESPN cookies, `espn_s2` and `SWID`, which I've put in
`~/Claude/fantasy-hockey/.env` (git-ignored):
```
ESPN_S2=...
ESPN_SWID={...}
```
`ESPN_S2` is URL-encoded (contains `%2F`, `%3D`) — send it exactly as stored.
Load them from `.env` only. Never print, log, write them into output files, or
commit them. Never ask me to paste them into chat. If `.env` is missing or auth
fails, stop and tell me.

## Verify reality before writing code
My last build (a Sleeper dynasty tool) had a spec that was wrong in several places,
and catching that live before coding saved it. Hit the real APIs first, confirm the
settings and data shapes below, and tell me about anything that differs.

## Data sources (checked 2026-09-26; confirm live)
- **ESPN league** (cookies required):
  `https://lm-api-reads.fantasy.espn.com/apis/v3/games/fhl/seasons/2027/segments/0/leagues/1366258109`
  with views like `mSettings`, `mTeam`, `mRoster`, `mMatchup`, `mDraftDetail`.
  Live draft picks: `?view=mDraftDetail` → `draftDetail.picks`.
- **ESPN player pool** (no auth): `.../seasons/2027/segments/0/leaguedefaults/1?view=kona_player_info`
  with an `X-Fantasy-Filter` header, e.g.
  `{"players":{"limit":400,"sortDraftRanks":{"sortPriority":100,"sortAsc":true,"value":"STANDARD"}}}`.
  Each player has `eligibleSlots` (numeric slot IDs — map them from the league
  settings), `injuryStatus`, `ownership` (`percentOwned`, `percentChange`,
  `averageDraftPosition`, `auctionValueAverage`), and `stats` entries. The entry with
  `seasonId 2027, statSourceId 1, statSplitTypeId 0` appears to be the **season
  projection**; `statSourceId 0` entries are actuals. Confirm and map stat IDs.
- **NHL official API** (no auth, verified): `https://api-web.nhle.com/v1/...` —
  schedule (games per team per day/week), standings, team rosters.
- **MoneyPuck** (free data downloads, verified reachable): advanced stats (expected
  goals, shooting %) for luck/regression signals. Respect their terms.
- **Daily Faceoff** (starting goalies, line combos): returned a redirect — optional.
  Check whether it's reachable and allowed; skip it if not.
- There is **no KTC/FantasyCalc equivalent** for hockey, and we don't need one: a
  player's value = rest-of-season projection scored under MY league's rules,
  cross-checked against ESPN rank/ADP and ownership trend.

## Phase 1 — Draft (before 8:30 PM ET Sunday)
1. **Valuation from my league's scoring.**
   - Points league: projected fantasy points under the league's point values.
   - Categories league: z-scores per category from projections, summed across the
     league's categories (weight goalie ratio stats like GAA/SV% by volume). Watch
     category balance while drafting so I don't overload one category.
   - Convert to value above replacement at each position; blend with ESPN rank/ADP
     so the board reflects when players actually go.
   - **If it's an auction:** dollar values from value above replacement, sanity-checked
     against ESPN's `auctionValueAverage`, and track every team's remaining budget.
2. **Positions:** real slot counts from settings, multi-position eligibility, positional
   scarcity, and a goalie plan (when to take G1/G2 given how goalies score here).
3. **Big board:** best available with value, ADP, positions, and a "likely gone by my
   next pick" flag; my pick numbers from the real draft order and type.
4. **Live co-pilot (`--draft-live`):** poll every ~10s, drop drafted players, show names
   (not IDs), alert when I'm on the clock or 1-2 picks away, and show the best
   available at my next pick given my positional and category needs so far. Lesson
   from last time: the pick feed lagged and the draft outran a 30s poll — poll faster
   and warn me if the feed looks stale.

## Phase 2 — In-season dashboard (after the draft)
Port the architecture of my dynasty tool, minus the dynasty-only parts.

**Tabs:**
- **Overview:** team grade, headline, most urgent action, **playoff odds** (record +
  points for + remaining schedule + roster value, same yardstick for all teams),
  positional and category needs, league landscape.
- **This Week / Matchup** (H2H): my opponent, projected result by category (or points),
  which categories are swingable, and games-played edge.
- **Roster:** rest-of-season value, games this week, injury status, line/power-play
  role where available.
- **Lineup helper** (ESPN hockey sets lineups daily): who plays tonight, benched players
  who have games, empty slots, confirmed starting goalies if available. **Advise only —
  never change my lineup or roster on ESPN.**
- **Free Agents:** scored on roster need (positions AND categories) + rest-of-season
  value + short-term value (games this week, goalie starts, ownership % trending up,
  luck/regression signals). Summary text generated from the same scores, so it can't
  contradict the list. Include a **streaming planner** (games per team this week and
  next; goalie streams by matchup).
- **Trade Finder:** rest-of-season value + category fit, targeting teams whose season
  is slipping; every package's balance and fairness recomputed live on each build.
- **Standings:** record, points for, playoff odds.

**Keep from the dynasty tool:** numbers computed on every build (never hand-typed);
buy/sell driven by playoff odds (contenders hold producing starters and buy at their
biggest need); the build writes `state/context.json` and logs an `Analysis check`;
written takes live in `state/ai_manual.json` with a quiet "Analysis <date>" stamp, not
a stale-warning banner; a scheduled task refreshes the written analysis weekly
following `docs/weekly-refresh.md`.

**Drop from the dynasty tool:** KTC/FantasyCalc values, age curves and dynasty
windows, rookie draft War Room, future-pick portfolio, and "future upside" weighting
(redraft cares about this season only — short-term upside replaces it).

## Open decisions — ask me after the draft
1. **Publishing.** This league is private on ESPN. A public GitHub Pages dashboard would
   show leaguemates' rosters to anyone with the link, and a daily cloud rebuild would
   need my ESPN cookies stored as GitHub repository secrets. Options: local-only
   dashboard, a private repo (private Pages needs a paid GitHub plan), or public Pages.
2. **Daily lineup check.** A morning scheduled task that flags lineup problems (starters
   without games, goalies not starting, empty slots) — on top of the weekly refresh?

## Engineering standards
- Python 3.9.6 on this Mac: start every module with `from __future__ import annotations`.
- The system Python's SSL is old (LibreSSL). If a host rejects `requests`, fetch it
  through a `curl` subprocess instead.
- Single-purpose modules wired through `build.py`; `logging`, not print; disk caches
  with TTLs; one failed source degrades gracefully instead of crashing.
- Git: `git pull` before work, `git push` after. Never commit `.env`.

## Success criteria for the draft
1. Auth works from `.env`; `build.py` prints my league's scoring, roster slots, team
   count, draft type, and my draft slot.
2. The big board ranks players by my league's scoring (dollar values if auction),
   and my pick numbers are right.
3. `--draft-live` runs against the real draft endpoint (empty pre-draft is fine) and
   the plumbing is tested before 8:30 PM ET.
4. Then act as my co-pilot in chat during the draft.

Start by confirming auth and the league settings live, flag anything that differs
from this prompt, and propose the plan before writing code.
