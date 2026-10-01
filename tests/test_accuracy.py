from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from fh import accuracy
from tests.test_inseason import pl


class TestAccuracy(unittest.TestCase):
    def setUp(self):
        self.orig = accuracy.LOG_DIR
        accuracy.LOG_DIR = Path(tempfile.mkdtemp()) / "log"

    def tearDown(self):
        accuracy.LOG_DIR = self.orig

    def player(self, sources, fp, gp):
        p = pl(1, "F")
        p.sources, p.act_fp, p.act_gp = sources, fp, gp
        return p

    def test_grades_after_a_week(self):
        # Day 0: model says 2.0/gm, ESPN 3.0/gm. Over the next week he scores 10 pts in 4 games = 2.5/gm.
        accuracy.log_snapshot([self.player({"model": 2.0, "espn": 3.0, "pace": None}, 0.0, 0)], date(2026, 10, 1))
        self.assertEqual(accuracy.evaluate(date(2026, 10, 3))["windows"], 0)          # too early to grade
        accuracy.log_snapshot([self.player({"model": 2.0, "espn": 3.0, "pace": 2.5}, 10.0, 4)], date(2026, 10, 8))
        r = accuracy.evaluate(date(2026, 10, 8))
        self.assertEqual(r["windows"], 1)
        self.assertAlmostEqual(r["table"]["model"]["all"], 0.5)
        self.assertAlmostEqual(r["table"]["espn"]["F"], 0.5)
        self.assertAlmostEqual(r["table"]["consensus"]["all"], 0.0)   # median of 2.0 and 3.0 = 2.5
        self.assertIsNone(r["table"]["pace"]["all"])                  # no prediction on day 0
        self.assertEqual(r["games"]["model"], 4)

    def test_consensus_and_disagreement(self):
        p = self.player({"sheet": 9.0, "model": 2.0, "espn": 3.0, "pace": 4.0, "l15": None}, 0, 0)
        self.assertEqual(p.consensus, 3.0)                 # sheet excluded (already inside model), None ignored
        self.assertAlmostEqual(p.disagreement, 2.0 / 3.0)


if __name__ == "__main__":
    unittest.main()
