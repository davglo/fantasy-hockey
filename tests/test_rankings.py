from __future__ import annotations

import unittest

from fh import rankings


class TestRankings(unittest.TestCase):
    def test_names(self):
        self.assertEqual(rankings.norm_name("Tim St\u00fctzle"), "timstutzle")
        self.assertEqual(rankings.initial_key("Alex Romanov"), rankings.initial_key("Alexander Romanov"))
        self.assertEqual(rankings.norm_team("T.B"), "TB")


if __name__ == "__main__":
    unittest.main()
