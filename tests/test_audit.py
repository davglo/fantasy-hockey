"""Regression tests for the 2026-11-15 dashboard audit."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from fh import accuracy, advice, analysis, config, dashboard, espn, season
from tests.helpers import SWID, raw_league
from tests.test_inseason import pl, pro_raw


class TestOpenSlots(unittest.TestCase):
    def test_day_without_nhl_games_is_not_a_streaming_day(self):
        lg = espn.parse_league(raw_league(), SWID)
        cal = season.build_calendar(pro_raw(), 27, 194)
        cal.team_games = {t: {p for p in g if p != 3} for t, g in cal.team_games.items()}   # period 3: no NHL games
        rows = {r["period"]: r for r in advice.open_slots([pl(1, "F")], lg, cal, [1, 3])}
        self.assertFalse(rows[3]["nhl"])
        self.assertEqual((rows[3]["G"], rows[3]["skater"]), (0, 0))
        self.assertTrue(rows[1]["nhl"])
        self.assertGreater(rows[1]["skater"], 0)


class TestDisagreement(unittest.TestCase):
    def test_last_15_days_alone_does_not_flag(self):
        p = pl(1, "F")
        p.sources = {"model": 2.0, "espn": 2.1, "pace": 2.0, "l15": 4.0}
        self.assertLess(p.disagreement, 0.25)
        p.sources["espn"] = 3.0
        self.assertGreater(p.disagreement, 0.25)


class TestAccuracyHeadToHead(unittest.TestCase):
    def test_sources_graded_on_the_same_players(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(accuracy, "LOG_DIR", Path(tmp)):
            full = {"model": 2.0, "sheet": 2.0, "espn": 2.0, "pace": 2.0, "l15": 2.0, "consensus": 2.0}
            a = {"1": {"name": "A", "group": "F", "cum_fp": 0, "cum_gp": 0, **full},
                 "2": {"name": "B", "group": "F", "cum_fp": 0, "cum_gp": 0, **dict(full, espn=None)}}
            b = {"1": {"name": "A", "group": "F", "cum_fp": 6, "cum_gp": 3, **full},
                 "2": {"name": "B", "group": "F", "cum_fp": 30, "cum_gp": 3, **full}}
            (Path(tmp) / "2026-11-01.json").write_text(json.dumps(a))
            (Path(tmp) / "2026-11-08.json").write_text(json.dumps(b))
            res = accuracy.evaluate(date(2026, 11, 8))
        self.assertEqual(len(set(res["games"].values())), 1)        # every source graded on the same games
        self.assertEqual(res["games"]["model"], 3)                   # player B (no ESPN number) left out
        self.assertEqual(res["table"]["model"]["all"], 0.0)


class TestSanity(unittest.TestCase):
    def ctx(self, injury):
        return {"league": {"slots": {"F": 1}, "playoff_teams": 1}, "standings": [{"odds": 1.0}], "matchup": None,
                "roster": [], "rosters": {}, "free_agents": [], "drop_ranking": [], "trades": [],
                "goalies_today": [{"injury": injury}], "playoff_usable": {"days": []},
                "streaming": {"goalies": [], "this": {"moves": []}, "next": {"moves": []}}}

    def test_day_to_day_goalie_is_not_an_alarm(self):
        self.assertEqual(analysis.sanity(self.ctx("DAY_TO_DAY")), [])
        self.assertIn("injured goalie suggested", analysis.sanity(self.ctx("INJURY_RESERVE")))


class TestRender(unittest.TestCase):
    def test_dashboard_renders_from_committed_context(self):
        # The cloud analysis refresh re-renders from this file; any formatting slip in dashboard.py fails here.
        ctx = json.loads((config.STATE / "context.json").read_text())
        html = dashboard.render(ctx, {})
        self.assertIn("Hockey Co-GM", html)
        self.assertNotIn("%%", html)


    def test_stale_takes_hidden_fresh_shown(self):
        ctx = json.loads((config.STATE / "context.json").read_text())
        manual = {"date": ctx["period"]["date"], "takes": {"overview": "FRESH TAKE TEXT"}}
        self.assertIn("FRESH TAKE TEXT", dashboard.render(ctx, manual))
        manual["date"] = "2026-01-01"
        html = dashboard.render(ctx, manual)
        self.assertNotIn("FRESH TAKE TEXT", html)
        self.assertIn("hidden until it", html)

    def test_playoff_schedule_card(self):
        ctx = json.loads((config.STATE / "context.json").read_text())
        if ctx["playoffs"].get("schedule"):
            self.assertIn("Playoff schedule, both weeks", dashboard.render(ctx, {}))


if __name__ == "__main__":
    unittest.main()
