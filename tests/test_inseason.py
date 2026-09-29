from __future__ import annotations

import unittest
from datetime import date

from fh import advice, analysis, engine, espn, season
from fh.engine import P
from tests.test_draft import SWID, mk, raw_league


def pro_raw():
    # Two teams; games on periods 1..14 alternating, first game 2026-09-29 19:00 ET.
    base_ms = 1790722800000  # 2026-09-29 23:00 UTC
    tor = {str(p): [{"date": base_ms + (p - 1) * 86400000, "homeProTeamId": 21, "awayProTeamId": 1}]
           for p in range(1, 195, 2)}
    bos = {k: v for k, v in tor.items()}
    return {"settings": {"proTeams": [{"id": 21, "abbrev": "TOR", "proGamesByScoringPeriod": tor},
                                      {"id": 1, "abbrev": "BOS", "proGamesByScoringPeriod": bos},
                                      {"id": 2, "abbrev": "BUF", "proGamesByScoringPeriod": {
                                          "2": [{"date": base_ms + 86400000, "homeProTeamId": 2, "awayProTeamId": 1}]}}]}}


def pl(i, group="F", team="TOR", rate=2.0, slot=3):
    b = mk(i, group)
    b.team = team
    return P(base=b, rate=rate, share=1.0, act_gp=0, act_fp=0, act_g=0, act_sog=0, pct_change=0, owner=5, slot=slot)


class TestCalendar(unittest.TestCase):
    def test_weeks_match_espn(self):
        cal = season.build_calendar(pro_raw(), 27, 194)
        self.assertEqual(cal.start, date(2026, 9, 29))
        self.assertEqual(cal.date_of(cal.matchups[1][-1]), date(2026, 10, 11))   # long opening week
        self.assertEqual(cal.date_of(cal.matchups[2][0]), date(2026, 10, 12))
        self.assertEqual(len(cal.matchups[27]), 6)
        self.assertEqual(cal.matchup_of(14), 2)


class TestLineups(unittest.TestCase):
    def setUp(self):
        self.lg = espn.parse_league(raw_league(), SWID)
        self.cal = season.build_calendar(pro_raw(), 27, 194)

    def test_best_lineup_respects_slots(self):
        ps = [pl(i, "F") for i in range(12)] + [pl(100 + i, "D") for i in range(7)] + [pl(200 + i, "G") for i in range(3)]
        pts, st = engine.best_lineup(ps, self.lg.slots, lambda p: p.exp_game())
        self.assertEqual(len(st), 18)  # 9F + 5D + 2G + 2UTIL
        self.assertEqual(sum(p.group == "G" for p in st), 2)

    def test_bench_player_with_game_flagged(self):
        # Period 2: only BUF plays. Starter on TOR (no game), BUF player on bench.
        starter = pl(1, "F", "TOR", slot=3)
        benched = pl(2, "F", "BUF", slot=7)
        issues, _, gain = advice.lineup_check([starter, benched], self.lg, self.cal, 2)
        self.assertEqual([i.severity for i in issues], ["fix"])
        self.assertIn("Start P2", issues[0].text)
        self.assertIn("P1 (no game)", issues[0].text)
        self.assertGreater(gain, 0)

    def test_no_flag_when_nobody_can_replace(self):
        issues, _, _ = advice.lineup_check([pl(1, "F", "TOR", slot=3), pl(2, "F", "TOR", slot=7)], self.lg, self.cal, 2)
        self.assertEqual(issues, [])

    def test_ir_never_starts(self):
        ps = [pl(1, "F", slot=advice.IR_SLOT, rate=9.0)]
        self.assertEqual(engine.projected_points(ps, self.lg, self.cal, [1]), 0.0)

    def test_win_prob(self):
        self.assertAlmostEqual(engine.win_prob(100, 100), 0.5)
        self.assertGreater(engine.win_prob(120, 100), 0.75)


class TestAnalysisCheck(unittest.TestCase):
    def test_flags_traded_player_and_age(self):
        ctx = {"period": {"date": "2026-10-10"}, "roster": [{"name": "A"}], "rosters": {"x": ["A", "B"]},
               "free_agents": [], "me": {"playoff_odds": 0.4}}
        manual = {"date": "2026-10-01", "my_players": ["A", "B"], "playoff_odds": 0.7,
                  "takes": {"overview": "Lean on B and A."}}
        notes = analysis.check(ctx, manual)
        self.assertIn("takes dated 2026-10-01 (9 days old)", notes)
        self.assertTrue(any("B" in n and "no longer" in n for n in notes))
        self.assertTrue(any("odds moved" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
