from __future__ import annotations

import unittest
from datetime import date, datetime

from fh import absence, engine, news, season
from tests.test_inseason import pl, pro_raw


class TestParse(unittest.TestCase):
    def test_timelines(self):
        p = absence.parse
        self.assertEqual(p("Makar (lower body) is expected to be sidelined for approximately two weeks."), {"days": 14})
        self.assertEqual(p("MacKinnon (lower body) will miss at least one month."), {"days": 30})
        self.assertEqual(p("Guenther is expected to miss 2-4 weeks."), {"days": 14})          # lower bound of a range
        self.assertEqual(p("Bedard won't play during the next four games while the team decides on surgery."), {"games": 4})
        self.assertEqual(p("He is out for the rest of the season after surgery."), {"season": True})
        self.assertEqual(p("Guenther was upgraded to day-to-day ahead of Saturday's matchup."), {"clear": True})
        self.assertEqual(p("Guenther was activated from injured reserve."), {"clear": True})
        self.assertIsNone(p("Dorofeyev (lower body) was placed on injured reserve on Saturday."))   # no timeline stated
        self.assertIsNone(p("Celebrini scored in a 3-2 win in two weeks of great play."))


class TestApply(unittest.TestCase):
    def setUp(self):
        self.cal = season.build_calendar(pro_raw(), 27, 194)          # TOR plays odd periods from Sep 29
        self.today = date(2026, 10, 12)

    def item(self, text, d=date(2026, 10, 10)):
        return news.parse({"type": "Rotowire", "description": text,
                           "published": datetime(d.year, d.month, d.day, 15, 0).strftime("%Y-%m-%dT%H:%M:%SZ")}, 1, "X")

    def test_stated_timeline_removes_games_until_return(self):
        p = pl(1, "F", "TOR", rate=2.0)
        p.base.injury, p.slot = "OUT", 3
        absence.apply([p], {1: [self.item("X is expected to be sidelined for approximately two weeks.")]}, self.cal, self.today)
        self.assertEqual(p.out_until, date(2026, 10, 24))
        self.assertEqual(p.exp_day(self.cal, self.cal.period_of(date(2026, 10, 15))), 0.0)      # still out
        self.assertGreater(p.exp_day(self.cal, self.cal.period_of(date(2026, 10, 25))), 0.0)    # back
        self.assertLess(p.ros(self.cal, range(self.cal.period_of(self.today), 195)),
                        2.0 * self.cal.games("TOR", range(self.cal.period_of(self.today), 195)))

    def test_no_timeline_is_labeled_not_assumed(self):
        p = pl(1, "F", "TOR")
        p.base.injury, p.slot = "INJURY_RESERVE", 8
        absence.apply([p], {1: [self.item("X was placed on injured reserve.")]}, self.cal, self.today)
        self.assertIsNone(p.out_until)
        self.assertEqual(p.out_note, "return date unknown")

    def test_return_news_clears_and_ir_status_not_active(self):
        p = pl(1, "F", "TOR")
        p.base.injury, p.slot = "OUT", 7
        items = [self.item("X was activated from injured reserve.", date(2026, 10, 11)),
                 self.item("X will miss two weeks.", date(2026, 10, 1))]
        absence.apply([p], {1: items}, self.cal, self.today)
        self.assertIsNone(p.out_until)
        ir = pl(2, "F", "TOR"); ir.base.injury, ir.slot = "INJURY_RESERVE", 7
        self.assertNotIn(ir, engine.active([ir]))      # IR status can't be in this week's lineup even from the bench


if __name__ == "__main__":
    unittest.main()
