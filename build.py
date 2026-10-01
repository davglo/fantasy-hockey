"""ESPN fantasy hockey co-GM.

  python3 build.py                 in-season dashboard -> output/index.html + state/context.json
  python3 build.py --lineup-check  today's lineup problems (Mac notification if any); advice only
  python3 build.py --draft-board   draft big board -> output/draft_board.html
  python3 build.py --draft-live    poll the live draft every 5s, alert on my turn
  python3 build.py --simulate      offline mock draft through the live pipeline (plumbing test)
"""
from __future__ import annotations

import argparse
import logging
import random
import sys

from fh import analysis, board, config, dashboard, draft, espn, live, rankings, report, valuation

log = logging.getLogger("build")


def setup_logging() -> None:
    config.STATE.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(config.STATE / "build.log")):
        h.setFormatter(fmt)
        root.addHandler(h)


def load_all():
    env = espn.load_env()
    lg = espn.parse_league(espn.fetch_league(), env["ESPN_SWID"])
    sheet = rankings.load()
    pro = espn.fetch_pro_teams()
    try:
        sheet_pts = rankings.sheet_scoring()
    except Exception as e:  # noqa: BLE001 - no workbook -> no rescoring needed
        log.warning("workbook scoring unreadable (%s); workbook FP used as-is", e)
        sheet_pts = {}
    players = valuation.build_players(espn.fetch_pool(), sheet, lg, pro, sheet_pts)
    repl = valuation.value(players, lg)

    def resolve(ids):
        extra = valuation.build_players(espn.fetch_players_by_id(ids), sheet, lg, pro, sheet_pts)
        for p in extra:
            p.vor = p.fp - repl[p.group]
        return extra

    return env, lg, players, repl, resolve


def summary(lg: espn.League, repl: dict) -> None:
    log.info("League: %s | %s | %d teams | playoffs: top %d", lg.name, lg.scoring_type, lg.size, lg.playoff_teams)
    log.info("Scoring: %s", ", ".join("%s %g" % (config.STAT_NAMES.get(k, k), v) for k, v in lg.scoring.items()))
    log.info("Roster slots: %s | limits: %s", lg.slots, lg.pos_limits)
    dt = lg.draft_time.astimezone(board.ET).strftime("%a %b %-d %-I:%M %p ET") if lg.draft_time else "?"
    log.info("Draft: %s, %d rounds, %ds/pick, %s | in progress: %s, done: %s",
             lg.draft_type, lg.rounds, lg.time_per_pick, dt, lg.in_progress, lg.drafted)
    log.info("Me: team %s '%s' | slot %s of %d | picks %s", lg.my_team_id, lg.teams.get(lg.my_team_id),
             lg.my_slot, lg.size, ", ".join(map(str, lg.my_picks)))
    if not lg.in_progress and not lg.drafted:
        log.info("NOTE: draft order is re-randomized 1 hr before the draft; slot above is provisional.")
    log.info("Replacement FP: %s", ", ".join("%s %.1f" % kv for kv in repl.items()))


def check_scoring(lg: espn.League) -> None:
    issues = rankings.check_scoring(lg.scoring)
    hard = [i for i in issues if "can't model" not in i]
    for i in issues:
        note = "" if i not in hard else " -> workbook FP re-scored from its stat columns"
        log.info("Workbook scoring check: %s%s", i, note)
    if config.SCORING_OVERRIDES:
        log.info("Scoring overrides (not yet in ESPN): %s", ", ".join(
            "%s %g" % (config.STAT_NAMES.get(k, k), v) for k, v in config.SCORING_OVERRIDES.items()))
    if not hard:
        log.info("Workbook scoring check: all modeled stats match ESPN league settings")


def simulate(lg, players, repl, swid) -> None:
    """Mock draft: every team picks near ADP (with noise); I take the top recommendation."""
    rng = random.Random(7)
    raw = espn.fetch_league()
    picks = sorted(raw["draftDetail"]["picks"], key=lambda p: p["overallPickNumber"])
    for p in picks:
        p["playerId"] = -1
    raw["draftDetail"].update(inProgress=True, drafted=False)
    t = [0.0]
    alerts = []
    ld = live.LiveDraft(list(players), repl, swid, clock=lambda: t[0], alert=lambda a, b: alerts.append(b),
                        out_path=config.OUTPUT / "draft_board_sim.html", refresh=0)
    taken = set()
    for p in picks:
        for e in ld.update(raw):
            log.info(e)
        avail = [x for x in players if x.id not in taken]
        if p["teamId"] == ld.league.my_team_id:
            mine = [x for x in players if x.id in taken and ld.seen.get(x.id, (0, 0))[1] == ld.league.my_team_id]
            choice = draft.recommend(avail, mine, ld.league, p["overallPickNumber"], n=1)[0].player
        else:
            choice = min(avail, key=lambda x: x.market + rng.gauss(0, max(2, 0.2 * x.market)))
        p["playerId"] = choice.id
        taken.add(choice.id)
        t[0] += 30
    raw["draftDetail"].update(inProgress=False, drafted=True)
    for e in ld.update(raw):
        log.info(e)
    ld.write_board()
    mine = [ld.by_id[pid] for pid, (_, tm) in ld.seen.items() if tm == ld.league.my_team_id]
    log.info("SIM done: %d picks, %d alerts, my roster: %s", len(ld.seen), len(alerts),
             ", ".join("%s(%s)" % (x.name, x.group) for x in mine))
    log.info("SIM my projected FP (top 18 non-bench approx): %.0f", sum(sorted((x.fp for x in mine), reverse=True)[:18]))


def build_dashboard(swid: str) -> dict:
    ctx = report.build(swid)
    report.write_context(ctx)
    manual = analysis.load_manual()
    log.info("Analysis check: %s", "; ".join(analysis.check(ctx, manual)))
    errs = analysis.sanity(ctx)
    (log.warning if errs else log.info)("Sanity check: %s", "; ".join(errs) if errs else "all invariants hold")
    config.OUTPUT.mkdir(exist_ok=True)
    out = config.OUTPUT / "index.html"
    out.write_text(dashboard.render(ctx, manual))
    me = ctx["me"]
    log.info("%s | grade %s | playoff odds %.0f%% | urgent: %s", me["name"], me["grade"], me["playoff_odds"] * 100, me["urgent"])
    log.info("Dashboard written: %s", out)
    return ctx


def lineup_check(ctx: dict) -> None:
    today = ctx["lineup"]["today"]
    fixes = [i["text"] for i in today["issues"] if i["severity"] == "fix"]
    for i in today["issues"]:
        log.info("Lineup %s [%s] %s", today["date"], i["severity"], i["text"])
    if fixes:
        live.notify("Lineup check %s" % today["date"], "%d fix%s: %s" % (len(fixes), "es" * (len(fixes) > 1), fixes[0]))
    else:
        log.info("Lineup %s: no fixes needed", today["date"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft-board", action="store_true")
    ap.add_argument("--draft-live", action="store_true")
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--lineup-check", action="store_true")
    ap.add_argument("--interval", type=float, default=config.POLL_SECONDS)
    ap.add_argument("--polls", type=int, default=None, help="stop live mode after N polls (testing)")
    args = ap.parse_args()
    setup_logging()
    if not (args.draft_board or args.draft_live or args.simulate):
        try:
            ctx = build_dashboard(espn.load_env()["ESPN_SWID"])
        except espn.FetchError as e:
            log.error("STOP: %s", e)
            return 1
        if args.lineup_check:
            lineup_check(ctx)
        return 0
    try:
        env, lg, players, repl, resolve = load_all()
    except espn.FetchError as e:
        log.error("STOP: %s", e)
        return 1
    summary(lg, repl)
    check_scoring(lg)
    if args.simulate:
        simulate(lg, players, repl, env["ESPN_SWID"])
        return 0
    if args.draft_live:
        ld = live.LiveDraft(players, repl, env["ESPN_SWID"], resolve=resolve)
        log.info("Live draft co-pilot: polling every %.0fs. Board: %s (auto-refreshes)", args.interval, ld.out_path)
        live.run(ld, espn.fetch_draft_live, interval=args.interval, max_polls=args.polls)
        return 0
    drafted = {pid: (ov, t) for ov, t, pid in lg.picks}
    out = config.OUTPUT / "draft_board.html"
    config.OUTPUT.mkdir(exist_ok=True)
    out.write_text(board.render(lg, players, drafted, len(lg.picks) + 1, repl))
    log.info("Board written: %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
