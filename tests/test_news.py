from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from fh import advice, espn, news, season
from tests.helpers import SWID, raw_league
from tests.test_inseason import pl, pro_raw

NOW = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)


def raw(desc, hours_ago=2, story="", kind="Rotowire"):
    pub = (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"type": kind, "description": desc, "story": story, "published": pub}


class TestParse(unittest.TestCase):
    def test_reporter_and_insider(self):
        it = news.parse(raw("Hellebuyck has been traded to Ottawa, Elliotte Friedman of Sportsnet reports."), 1, "Connor Hellebuyck")
        self.assertEqual(it.insider, "Elliotte Friedman")
        self.assertIn("trade", it.kinds)
        self.assertIn("citing Elliotte Friedman", news.credit(it))
        self.assertEqual(it.link, news.PLAYER_PAGE % 1)

    def test_tags_from_summary_not_story(self):
        it = news.parse(raw("Vladar allowed seven goals on 38 shots.", story="He missed time with an injury last year."), 2, "Dan Vladar")
        self.assertEqual(it.kinds, set())

    def test_feature_stories_skipped(self):
        self.assertIsNone(news.parse(raw("Bounce-back picks", kind="Story"), 3, "X"))

    def test_goalie_start_status_real_phrasings(self):
        day = NOW.date()   # Thursday Oct 1
        st = lambda desc, h: news.start_status([news.parse(raw(desc, hours_ago=h), 1, "G")], day)
        self.assertEqual(st("Allen will be between the home pipes against the Flyers on Thursday, Amanda Stein of the Devils' official site reports.", 3), "confirmed")
        self.assertEqual(st("Wolf is set to start at home against Seattle on Thursday, per Pat Steinberg of QR Calgary 770 AM.", 24), "confirmed")
        self.assertEqual(st("Luukkonen (lower body) is slated to start on the road against Columbus on Thursday.", 26), "confirmed")
        self.assertEqual(st("Knight is expected to defend the road net against the Golden Knights on Tuesday.", 48), "")   # other day
        self.assertEqual(st("Daccord is expected to start Thursday against Calgary.", 5), "expected")
        self.assertEqual(st("Daccord is set to start in Vancouver for Thursday's preseason outing.", 5), "")
        self.assertEqual(st("Swayman will guard the cage tonight.", 30), "")     # 'tonight' from yesterday is stale

    def test_top_caps_and_ranks(self):
        items = [news.parse(raw("A will miss the next game with an injury.", hours_ago=10), 5, "A"),
                 news.parse(raw("B had two shots.", hours_ago=1), 6, "B"),
                 news.parse(raw("C will miss two weeks, Pierre LeBrun of The Athletic reports.", hours_ago=10), 7, "C")]
        top = news.top(items, NOW, 1)
        self.assertEqual([i.player for i in top], ["C"])          # insider + injury beats plain injury
        self.assertNotIn("B", [i.player for i in news.top(items, NOW, 5)])   # no fantasy-relevant tag -> dropped


class TestPlayoffAndDrops(unittest.TestCase):
    def setUp(self):
        self.lg = espn.parse_league(raw_league(), SWID)
        self.cal = season.build_calendar(pro_raw(), 27, 194)

    def test_usable_games_counts_waste_and_empty(self):
        # 20 TOR forwards play on odd days: 11 can start (9 F + 2 UTIL), 9 wasted; 0 on even days (all empty)
        roster = [pl(i, "F", team="TOR") for i in range(20)]
        u = advice.usable_games(roster, self.lg, self.cal, [1, 2])
        d1, d2 = u["days"]
        self.assertEqual((d1["used"], d1["wasted"]), (11, 9))
        self.assertEqual(d2["used"], 0)
        self.assertEqual(d2["empty_sk"], 16)
        self.assertEqual(u["teams"]["BUF"]["sk_fit"], 1)    # BUF plays period 2, my open day

    def test_drop_ranking_cheapest_first(self):
        v = advice.Valuer(self.lg, self.cal, range(1, 30), range(182, 195))
        roster = [pl(i, "F", rate=3.0 - i * 0.2) for i in range(12)]
        ranks = advice.drop_ranking(roster, v)
        costs = [c for _, c in ranks]
        self.assertEqual(costs, sorted(costs))
        self.assertEqual(ranks[0][0].id, 11)    # lowest-rated forward is the cheapest cut


if __name__ == "__main__":
    unittest.main()


class TestHeadlines(unittest.TestCase):
    def test_filter_keeps_transactions_drops_noise(self):
        keep, skip = news.HEADLINE_KEEP, news.HEADLINE_SKIP
        self.assertTrue(keep.search("Canucks Trade Sasson To The Rangers") and not skip.search("Canucks Trade Sasson To The Rangers"))
        self.assertTrue(keep.search("Robertson to IR, Malkin out for weekend") or keep.search("Malkin injured"))
        self.assertTrue(skip.search("NHL Power Rankings: Updated poll"))
        self.assertTrue(skip.search("Ranking the Penguins best trade assets"))


class TestGoaliesToday(unittest.TestCase):
    def test_ir_goalie_excluded(self):
        cal = season.build_calendar(pro_raw(), 27, 194)
        healthy = pl(1, "G", team="BOS", slot=None)
        hurt = pl(2, "G", team="BOS", slot=None)
        hurt.base.injury = "INJURY_RESERVE"
        rows = advice.goalies_today([healthy, hurt], cal, 1, {}, {})
        self.assertEqual([r["p"].id for r in rows], [1])
