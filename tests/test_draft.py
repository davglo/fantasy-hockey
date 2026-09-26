from __future__ import annotations

import unittest

from fh import draft, espn, live, rankings, valuation
from fh.valuation import Player

SWID = "{AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA}"


def raw_league(order=(4, 3, 8, 7, 6, 1, 2, 5), made=(), in_progress=False, drafted=False, rounds=23):
    """Minimal ESPN league payload shaped like the real mSettings+mTeam+mDraftDetail response."""
    teams = snake = espn.snake_order(list(order), rounds)
    picks = [{"overallPickNumber": i + 1, "teamId": t, "playerId": made[i] if i < len(made) else -1}
             for i, t in enumerate(snake)]
    return {
        "settings": {
            "name": "Test", "size": 8,
            "scoringSettings": {"scoringType": "H2H_POINTS", "scoringItems": [{"statId": 13, "points": 2}]},
            "rosterSettings": {"lineupSlotCounts": {"3": 9, "4": 5, "5": 2, "6": 2, "7": 5, "8": 2},
                               "positionLimits": {"5": 4}},
            "draftSettings": {"type": "SNAKE", "pickOrder": list(order), "timePerSelection": 60,
                              "date": 1790555400000, "auctionBudget": 260},
            "scheduleSettings": {"playoffTeamCount": 4},
        },
        "teams": [{"id": t, "name": "T%d" % t, "owners": [SWID] if t == 5 else ["{X}"]} for t in range(1, 9)],
        "draftDetail": {"picks": picks, "inProgress": in_progress, "drafted": drafted},
    }


def mk(i, group="F", fp=100.0, adp=50.0):
    return Player(id=i, name="P%d" % i, group=group, positions=group, team="X", injury="ACTIVE", fp=fp,
                  source="sheet", espn_fp=fp, gp=82, espn_rank=None, espn_adp=adp, sheet_rank=None,
                  boost="", pct_owned=50.0)


class TestSchedule(unittest.TestCase):
    def test_my_picks_from_order(self):
        lg = espn.parse_league(raw_league(), SWID)
        self.assertEqual(lg.my_team_id, 5)
        self.assertEqual(lg.rounds, 23)
        self.assertEqual(lg.my_picks[:4], [8, 9, 24, 25])
        self.assertEqual(len(lg.my_picks), 23)

    def test_next_contested_pick(self):
        mp = [8, 9, 24, 25, 40]
        self.assertEqual(draft.next_contested_pick(mp, 8), 24)   # 9 is back-to-back: nobody picks between
        self.assertEqual(draft.next_contested_pick(mp, 9), 24)
        self.assertEqual(draft.next_contested_pick(mp, 3), 8)
        self.assertEqual(draft.next_contested_pick(mp, 24), 40)
        self.assertIsNone(draft.next_contested_pick([184], 184))

    def test_p_available(self):
        self.assertEqual(draft.p_available(10, 5, 5), 1.0)
        self.assertLess(draft.p_available(10, 30, 1), 0.05)
        self.assertGreater(draft.p_available(60, 24, 1), 0.95)
        self.assertGreater(draft.p_available(20, 24, 1), draft.p_available(20, 24, 1) * 0.99)


class TestNeedsAndValue(unittest.TestCase):
    def setUp(self):
        self.lg = espn.parse_league(raw_league(), SWID)

    def test_util_then_bench(self):
        f9 = [mk(i) for i in range(9)]
        self.assertEqual(draft.needs(f9, self.lg).weight("F", self.lg.pos_limits), 1.0)  # UTIL open
        f11 = [mk(i) for i in range(11)]
        self.assertEqual(draft.needs(f11, self.lg).weight("F", self.lg.pos_limits), draft.BENCH_WEIGHT)
        self.assertEqual(draft.needs(f11, self.lg).weight("D", self.lg.pos_limits), 1.0)

    def test_goalie_limit(self):
        g = [mk(i, "G") for i in range(4)]
        self.assertEqual(draft.needs(g, self.lg).weight("G", self.lg.pos_limits), 0.0)
        self.assertEqual(draft.needs(g[:2], self.lg).weight("G", self.lg.pos_limits), draft.G3_WEIGHT)

    def test_replacement_levels(self):
        ps = [mk(i, "F", fp=300 - i) for i in range(100)] + [mk(200 + i, "D", fp=200 - i) for i in range(60)] \
            + [mk(400 + i, "G", fp=250 - i) for i in range(30)]
        repl = valuation.replacement_levels(ps, self.lg)
        self.assertEqual(repl["G"], 250 - 16)          # 17th goalie
        # 72 F + 40 D starters, then 16 UTIL go to the best leftovers (F 228.. vs D 160..): all F
        self.assertEqual(repl["F"], 300 - 88)
        self.assertEqual(repl["D"], 200 - 40)


class TestLive(unittest.TestCase):
    def make(self):
        self.t = [1790555400.0]
        self.alerts = []
        ps = [mk(i, fp=300 - i, adp=i + 1) for i in range(1, 300)]
        return live.LiveDraft(ps, {"F": 100, "D": 100, "G": 100}, SWID, clock=lambda: self.t[0],
                              alert=lambda a, b: self.alerts.append(b))

    def test_on_clock_alert_once(self):
        ld = self.make()
        ld.update(raw_league(made=list(range(1, 6)), in_progress=True))   # pick 6: 2 away from 8
        self.assertEqual(ld.status["level"], "soon")
        ev = ld.update(raw_league(made=list(range(1, 8)), in_progress=True))
        self.assertEqual(ld.status["level"], "clock")
        self.assertTrue(any("P8" in e for e in ev))   # recommendations include best available
        ld.update(raw_league(made=list(range(1, 8)), in_progress=True))
        self.assertEqual(self.alerts, ["2 picks until you (pick 8)", "ON THE CLOCK - pick 8"])

    def test_names_not_ids(self):
        ld = self.make()
        ev = ld.update(raw_league(made=[1, 2], in_progress=True))
        self.assertIn("#1 R1 T4: P1 (F, VOR +0)", ev)

    def test_stale_feed(self):
        ld = self.make()
        ld.update(raw_league(made=[1], in_progress=True))
        self.t[0] += 95
        ld.update(raw_league(made=[1], in_progress=True))
        self.assertIn("No new pick", ld.status["stale"])
        ld.update(raw_league(made=[1, 2], in_progress=True))
        self.assertNotIn("stale", ld.status)

    def test_unreachable(self):
        ld = self.make()
        ld.update(raw_league())
        self.t[0] += 25
        ld.on_fetch_error(espn.FetchError("timeout"))
        self.assertIn("unreachable", ld.status["stale"])

    def test_order_rerandomized(self):
        ld = self.make()
        ld.update(raw_league())
        ev = ld.update(raw_league(order=(5, 4, 3, 8, 7, 6, 1, 2)))
        self.assertTrue(any("order changed" in e for e in ev))
        self.assertEqual(ld.league.my_picks[:3], [1, 16, 17])

    def test_draft_complete(self):
        ld = self.make()
        ld.update(raw_league(made=list(range(1, 185)), drafted=True))
        self.assertTrue(ld.league.drafted)


class TestRankings(unittest.TestCase):
    def test_names(self):
        self.assertEqual(rankings.norm_name("Tim Stützle"), "timstutzle")
        self.assertEqual(rankings.initial_key("Alex Romanov"), rankings.initial_key("Alexander Romanov"))
        self.assertEqual(rankings.norm_team("T.B"), "TB")

    def test_workbook_scoring_matches_league(self):
        from fh import config
        if not config.RANKINGS_XLSX.exists():
            self.skipTest("workbook missing")
        league = {13: 2, 14: 1, 38: .5, 39: .5, 29: .1, 31: .1, 32: .5, 28: 1, 1: 3, 7: 3, 6: .2, 4: -1, 9: .5}
        issues = rankings.check_scoring(league)
        self.assertEqual(issues, ["HAT: league scores 1, workbook can't model it"])


if __name__ == "__main__":
    unittest.main()
