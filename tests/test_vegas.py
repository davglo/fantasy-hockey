from __future__ import annotations

import unittest

from fh import advice, season, vegas
from tests.test_inseason import pl, pro_raw

RATES = {"TOR": {"gf": 3.0, "ga": 3.0, "win": 0.5}, "BOS": {"gf": 3.0, "ga": 3.0, "win": 0.5}}
SCORING = {1: 3.0, 4: -1.0}


class TestImpliedGoals(unittest.TestCase):
    def test_even_line_splits_evenly(self):
        h, a, p = vegas.implied_goals(6.0, None, None, -110, -110)
        self.assertAlmostEqual(h, 3.0, places=3)
        self.assertAlmostEqual(a, 3.0, places=3)
        self.assertAlmostEqual(p, 0.5)

    def test_favorite_scores_more_and_total_holds(self):
        h, a, p = vegas.implied_goals(5.5, -110, -110, -250, 200)
        self.assertGreater(h, a + 0.8)
        self.assertGreater(p, 0.65)
        self.assertAlmostEqual(h + a, 5.5, delta=0.6)    # juice-free over/under puts the mean near the line

    def test_juiced_over_raises_total(self):
        h, a, _ = vegas.implied_goals(6.5, -150, 125, -110, -110)
        self.assertGreater(h + a, 6.5)


class TestAdjustments(unittest.TestCase):
    def test_favorite_skaters_up_goalie_up_underdog_down(self):
        lines = {"TOR": vegas.Line(3.6, 2.4, 0.68, "BOS"), "BOS": vegas.Line(2.4, 3.6, 0.32, "TOR")}
        adj = vegas.adjustments(lines, RATES, SCORING)
        self.assertGreater(adj["TOR"].sk_mult["F"], adj["TOR"].sk_mult["D"] > 1)
        self.assertLess(adj["BOS"].sk_mult["F"], 1)
        self.assertGreater(adj["TOR"].g_delta, 0)
        self.assertLess(adj["BOS"].g_delta, 0)

    def test_slate_inflation_removed(self):
        # Both teams 'expected' 3.3 goals vs a usual 3.0: no edge, so no bump
        lines = {"TOR": vegas.Line(3.3, 3.3, 0.5, "BOS"), "BOS": vegas.Line(3.3, 3.3, 0.5, "TOR")}
        adj = vegas.adjustments(lines, RATES, SCORING)
        self.assertAlmostEqual(adj["TOR"].sk_mult["F"], 1.0)
        self.assertAlmostEqual(adj["TOR"].g_delta, 0.0)


class TestExpDay(unittest.TestCase):
    def setUp(self):
        self.cal = season.build_calendar(pro_raw(), 27, 194)
        lines = {"TOR": vegas.Line(3.6, 2.4, 0.68, "BOS"), "BOS": vegas.Line(2.4, 3.6, 0.32, "TOR")}
        self.adj = vegas.adjustments(lines, RATES, SCORING)
        self.cal.vegas = {("TOR", 1): self.adj["TOR"]}

    def test_lines_apply_only_on_their_day(self):
        p = pl(1, "F", "TOR", rate=2.0)
        self.assertAlmostEqual(p.exp_day(self.cal, 1), 2.0 * self.adj["TOR"].sk_mult["F"])
        self.assertAlmostEqual(p.exp_day(self.cal, 3), 2.0)       # no line posted
        self.assertEqual(p.exp_day(self.cal, 2), 0.0)             # no game

    def test_goalie_today_uses_line(self):
        g = pl(2, "G", "TOR", rate=4.0)
        rows = advice.goalies_today([g], self.cal, 1, {g.id: "confirmed"}, {})
        self.assertAlmostEqual(rows[0]["exp"], 4.0 + self.adj["TOR"].g_delta)
        self.assertAlmostEqual(rows[0]["win"], 0.68)


if __name__ == "__main__":
    unittest.main()
