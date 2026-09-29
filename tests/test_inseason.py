from __future__ import annotations

import unittest
from datetime import date

from fh import advice, analysis, engine, espn, market, season
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


    def test_weekly_matchups(self):
        cal = season.build_calendar(pro_raw(), 27, 194, weekly=True)
        self.assertEqual(cal.date_of(cal.matchups[1][-1]), date(2026, 10, 4))    # through first Sunday
        self.assertEqual(cal.date_of(cal.matchups[2][0]), date(2026, 10, 5))
        self.assertTrue(all(len(cal.matchups[m]) == 7 for m in range(2, 28)))
        self.assertEqual(cal.date_of(cal.matchups[27][-1]), date(2027, 4, 4))


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


class TestMovesAndTrades(unittest.TestCase):
    def setUp(self):
        self.lg = espn.parse_league(raw_league(), SWID)
        self.cal = season.build_calendar(pro_raw(), 27, 194)
        self.value = advice.Valuer(self.lg, self.cal, range(1, 30), range(182, 195))

    def roster(self):
        ps = [pl(i, "F", rate=3.0 - i * 0.1) for i in range(12)] + [pl(100 + i, "D", rate=2.0) for i in range(6)]
        ps += [pl(200, "G", rate=2.5, slot=5), pl(201, "G", rate=1.0, slot=5), pl(202, "G", rate=0.5, slot=7)]
        return ps

    def test_droppable_protects_ir_flagged_and_goalie_floor(self):
        ps = self.roster()
        ps[11].slot = advice.IR_SLOT                 # IR: never dropped
        ps[10].base.injury = "SUSPENSION"            # flagged: never dropped
        ps[-3].base.injury = "SUSPENSION"            # only 2 healthy goalies left -> both protected
        ids = {p.id for p in advice.droppable(ps, self.value, k=30)}
        self.assertNotIn(ps[11].id, ids)
        self.assertNotIn(ps[10].id, ids)
        self.assertNotIn(201, ids)
        self.assertNotIn(202, ids)

    def test_ir_counts_rest_of_season(self):
        ps = self.roster()
        before = self.value(ps)
        ps[0].slot = advice.IR_SLOT
        self.assertAlmostEqual(self.value(ps), before)   # no discount for IR over the rest of the season

    def test_trade_requires_market_fairness(self):
        mine, theirs = self.roster(), [pl(300 + i, "F", rate=2.0) for i in range(12)] + [pl(400 + i, "D", rate=2.0) for i in range(6)]
        for p in theirs:
            p.owner = 2
        theirs[0].rate = 4.0                         # their star
        tv = {p.id: 1.0 for p in mine + theirs}
        tv[theirs[0].id] = 100.0                     # market loves him: nothing of mine is worth it
        recs = advice.trades({5: mine, 2: theirs}, 5, [], self.value, tv, {2: 0.5})
        self.assertFalse(any(theirs[0].id in [p.id for p in t.get] for t in recs))

    def test_trade_keeps_goalie_floor(self):
        mine = self.roster()
        mine[-3].base.injury = "SUSPENSION"           # 2 healthy goalies left
        theirs = [pl(300 + i, "F", rate=4.0) for i in range(3)]
        for p in theirs:
            p.owner = 2
        tv = {p.id: 1.0 for p in mine + theirs}
        recs = advice.trades({5: mine, 2: theirs}, 5, [], self.value, tv, {2: 0.5})
        self.assertFalse(any(p.group == "G" for t in recs for p in t.give))

    def test_stream_plan_respects_adds_left(self):
        fas = [pl(500 + i, "F", team="BUF", rate=5.0, slot=None) for i in range(5)]
        self.assertEqual(advice.stream_plan(self.roster(), fas, self.value, [2], adds_left=0), [])
        self.assertLessEqual(len(advice.stream_plan(self.roster(), fas, self.value, [2], adds_left=1)), 1)

    def test_market_values_superlinear(self):
        ps = self.roster()
        for i, p in enumerate(ps):
            p.base.espn_adp = i + 1
        tv = market.trade_values(ps, {}, lambda p: 100.0 - p.base.espn_adp * 3)
        # rank 1 sits 60 pts over replacement, rank 11 sits 30: one star > two halves (consolidation premium)
        self.assertGreater(tv[ps[0].id], 2 * tv[ps[10].id])


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
