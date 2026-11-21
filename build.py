"""ESPN fantasy hockey co-GM.

  python3 build.py                 in-season dashboard -> output/index.html + state/context.json
  python3 build.py --lineup-check  today's lineup problems (Mac notification if any); advice only
  python3 build.py --render-only   re-render from the committed state/context.json (cloud analysis refresh)
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys

from fh import analysis, config, dashboard, espn, report

log = logging.getLogger("build")


def setup_logging() -> None:
    config.STATE.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(config.STATE / "build.log")):
        h.setFormatter(fmt)
        root.addHandler(h)


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


def render_only() -> int:
    path = config.STATE / "context.json"
    if not path.exists():
        log.error("STOP: %s missing - the Mac build commits it; git pull first", path)
        return 1
    ctx = json.loads(path.read_text())
    manual = analysis.load_manual()
    log.info("Analysis check: %s", "; ".join(analysis.check(ctx, manual)))
    config.OUTPUT.mkdir(exist_ok=True)
    out = config.OUTPUT / "index.html"
    out.write_text(dashboard.render(ctx, manual))
    log.info("Dashboard re-rendered from context of %s: %s", ctx["generated"], out)
    return 0


def notify(title: str, msg: str) -> None:
    try:
        subprocess.run(["osascript", "-e", 'display notification "%s" with title "%s" sound name "Glass"'
                        % (msg.replace('"', ""), title.replace('"', ""))], timeout=3, capture_output=True)
    except Exception:  # noqa: BLE001 - notification is best-effort
        pass


def lineup_check(ctx: dict) -> None:
    today = ctx["lineup"]["today"]
    fixes = [i["text"] for i in today["issues"] if i["severity"] == "fix"]
    for i in today["issues"]:
        log.info("Lineup %s [%s] %s", today["date"], i["severity"], i["text"])
    if fixes:
        notify("Lineup check %s" % today["date"], "%d fix%s: %s" % (len(fixes), "es" * (len(fixes) > 1), fixes[0]))
    else:
        log.info("Lineup %s: no fixes needed", today["date"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lineup-check", action="store_true")
    ap.add_argument("--render-only", action="store_true",
                    help="re-render output/index.html from the committed state/context.json + takes (no ESPN; cloud analysis refresh)")
    args = ap.parse_args()
    setup_logging()
    if args.render_only:
        return render_only()
    try:
        ctx = build_dashboard(espn.load_env()["ESPN_SWID"])
    except espn.FetchError as e:
        log.error("STOP: %s", e)
        return 1
    if args.lineup_check:
        lineup_check(ctx)
    return 0


if __name__ == "__main__":
    sys.exit(main())
