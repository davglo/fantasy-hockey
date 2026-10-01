from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from fh import advice, espn, news, season
from tests.test_draft import SWID, raw_league
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

    def test_goalie_start_window(self):
        items = [news.parse(raw("Swayman will defend the home crease Tuesday against the Rangers.", hours_ago=5), 4, "Jeremy Swayman")]
        self.assertTrue(news.starting_today(items, NOW))
        items = [news.parse(raw("Swayman will defend the home crease Tuesday.", hours_ago=30), 4, "Jeremy Swayman")]
        self.assertFalse(news.starting_today(items, NOW))

    def test_top_caps_and_ranks(self):
        items = [news.parse(raw("A will miss the next game with an injury.", hours_ago=10), 5, "A"),
                 news.parse(raw("B had two shots.", hours_ago=1), 6, "B"),
                 news.parse(raw("C will miss two weeks, Pierre LeBrun of The Athletic reports.", hours_ago=10), 7, "C")]
        top = news.top(items, NOW, 1)
        self.assertEqual([i.player for i in top], ["C"])          # insider + injury beats plain injury
        self.assertNotIn("B", [i.player for i in news.top(items, NOW, 5)])   # no fantasy-relevant tag -> dropped


class TestOpportunities(unittest.TestCase):
    def test_backup_goalie_benefits(self):
        starter = pl(1, "G", team="COL", rate=3.0)
        starter.owner = 3
        backup = pl(2, "G", team="COL", rate=1.5, slot=None)
        backup.owner = None
        other = pl(3, "G", team="BOS", rate=2.5, slot=None)
        it = news.parse(raw("Wedgewood will miss the rest of the season after knee surgery."), 1, "Scott Wedgewood")
        opps = news.opportunities({1: [it]}, {1: starter, 2: backup, 3: other}, [backup, other], NOW)
        self.assertEqual(len(opps), 1)
        self.assertEqual([b.id for b in opps[0].beneficiaries], [2])
        self.assertIn("season_over", opps[0].item.kinds)

    def test_return_news_not_an_opportunity(self):
        p = pl(1, "F", team="CHI")
        p.owner = 3
        it = news.parse(raw("Bedard was activated from injured reserve Thursday."), 1, "Connor Bedard")
        self.assertEqual(news.opportunities({1: [it]}, {1: p}, [pl(2, "F", team="CHI", slot=None)], NOW), [])


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


class TestRumors(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from fh import rumors
        self.rumors = rumors
        self.tmp = Path(tempfile.mkdtemp()) / "arch.json"
        self.orig_arch, self.orig_fetch = rumors.ARCHIVE, rumors._fetch_feed
        rumors.ARCHIVE = self.tmp
        now = datetime.now(timezone.utc)
        rumors._fetch_feed = lambda: [
            {"title": "Oilers trade pitch adds Connor Hellebuyck", "link": "https://x/1", "date": now.isoformat()},
            {"title": "Hellebuyck trade saga takes a turn", "link": "https://x/2", "date": now.isoformat()},
            {"title": "Islanders release jersey schedule", "link": "https://x/3", "date": now.isoformat()},
            {"title": "Old Hellebuyck trade note", "link": "https://x/4", "date": (now - timedelta(days=20)).isoformat()}]

    def tearDown(self):
        self.rumors.ARCHIVE, self.rumors._fetch_feed = self.orig_arch, self.orig_fetch

    def test_matches_names_and_keeps_archive(self):
        out = self.rumors.refresh(["Connor Hellebuyck"])
        self.assertEqual([r["link"] for r in out], ["https://x/1", "https://x/2"])   # last-name match; old item expired
        self.rumors._fetch_feed = lambda: []                                         # feed rolled over
        self.assertEqual(len(self.rumors.refresh(["Connor Hellebuyck"])), 2)          # still shown from archive


class TestGoaliesToday(unittest.TestCase):
    def test_ir_goalie_excluded(self):
        cal = season.build_calendar(pro_raw(), 27, 194)
        healthy = pl(1, "G", team="BOS", slot=None)
        hurt = pl(2, "G", team="BOS", slot=None)
        hurt.base.injury = "INJURY_RESERVE"
        rows = advice.goalies_today([healthy, hurt], cal, 1, {}, {})
        self.assertEqual([r["p"].id for r in rows], [1])
