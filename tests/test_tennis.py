import os
import tempfile
import unittest

from tennis import sofascore
from tennis.models import TennisMatch, parse_score, read_csv, set_complete, write_csv
from tennis.strategy import Rules, binom_p_value, find_triggers, summarize, wilson


def tm(i, p1, p2, score, winner, status="finished", day=1):
    return TennisMatch(f"m{i}", f"2026-10-{day:02d}T{i:02d}:00:00+00:00", "M", "ATP", "T",
                       p1, p2, parse_score(score), winner, status)


class ModelTest(unittest.TestCase):
    def test_set_complete(self):
        self.assertTrue(set_complete(6, 4))
        self.assertTrue(set_complete(7, 6))
        self.assertTrue(set_complete(5, 7))
        self.assertFalse(set_complete(5, 4))
        self.assertFalse(set_complete(6, 5))

    def test_straight_loss(self):
        m = tm(1, "A", "B", "4-6 3-6", 2)
        self.assertTrue(m.straight_loss("A"))
        self.assertFalse(m.straight_loss("B"))
        self.assertFalse(tm(2, "A", "B", "6-4 3-6 3-6", 2).straight_loss("A"))
        # отказ при 0-1 по сетам — не «чистое» 0-2
        self.assertFalse(tm(3, "A", "B", "4-6 1-2", 2, "retired").straight_loss("A"))

    def test_first_set(self):
        self.assertEqual(tm(1, "A", "B", "7-6 3-6 6-1", 1).first_set_winner(), 1)
        self.assertIsNone(tm(2, "A", "B", "3-2", 2, "retired").first_set_winner())

    def test_csv_roundtrip(self):
        ms = [tm(1, "A", "B", "6-4 7-6", 1), tm(2, "A", "C", "3-6 6-7", 2, "retired")]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.csv")
            write_csv(path, ms)
            self.assertEqual(read_csv(path), ms)


class StrategyTest(unittest.TestCase):
    def test_trigger_after_two_straight_losses(self):
        ms = [tm(1, "A", "B", "4-6 3-6", 2),
              tm(2, "C", "A", "6-1 6-2", 1),     # A проиграл всухую, будучи player2
              tm(3, "A", "D", "6-4 2-6 3-6", 2)]  # A взял 1-й сет
        ts = find_triggers(ms, Rules())
        self.assertEqual([(t.player, t.match.match_id) for t in ts], [("A", "m3")])
        self.assertTrue(ts[0].first_set_won)
        self.assertFalse(ts[0].match_won)

    def test_three_set_loss_breaks_streak(self):
        ms = [tm(1, "A", "B", "4-6 3-6", 2), tm(2, "A", "C", "6-4 3-6 3-6", 2),
              tm(3, "A", "D", "6-4 6-4", 1)]
        self.assertEqual(find_triggers(ms, Rules()), [])
        self.assertEqual(len(find_triggers(ms, Rules(straight_sets=False))), 1)

    def test_walkover_skipped_and_window(self):
        ms = [tm(1, "A", "B", "4-6 3-6", 2, day=1), tm(2, "A", "C", "", 2, "walkover", day=2),
              tm(3, "A", "C", "2-6 2-6", 2, day=3), tm(4, "A", "D", "6-4 6-4", 1, day=9)]
        self.assertEqual(len(find_triggers(ms, Rules())), 1)
        self.assertEqual(find_triggers(ms, Rules(), since="2026-10-10"), [])
        self.assertEqual(find_triggers(ms, Rules(max_gap_days=3)), [])

    def test_summary_and_stats(self):
        ms = [tm(1, "A", "B", "4-6 3-6", 2), tm(2, "A", "C", "4-6 3-6", 2),
              tm(3, "A", "D", "6-4 6-4", 1), tm(4, "A", "E", "3-6 3-6", 2)]
        s = summarize("x", find_triggers(ms, Rules()))
        self.assertEqual((s.n, s.first_set, s.matches_won), (1, 1, 1))
        lo, hi = wilson(50, 100)
        self.assertAlmostEqual((lo + hi) / 2, 0.5, places=6)
        self.assertAlmostEqual(binom_p_value(5, 10), 1.0)
        self.assertAlmostEqual(binom_p_value(0, 10), 2 / 1024)


class SofascoreTest(unittest.TestCase):
    def test_parse(self):
        ev = {"id": 1, "startTimestamp": 1790000000, "winnerCode": 2,
              "status": {"type": "finished", "description": "Ended"},
              "homeTeam": {"name": "Rublev A."}, "awayTeam": {"name": "Sinner J."},
              "homeScore": {"period1": 4, "period2": 6, "period3": 3},
              "awayScore": {"period1": 6, "period2": 7, "period3": 6},
              "tournament": {"name": "Shanghai", "category": {"name": "ATP"}}}
        dbl = dict(ev, id=2, homeTeam={"name": "A / B"})
        women = dict(ev, id=3, tournament={"name": "Wuhan", "category": {"name": "WTA"}})
        exo = dict(ev, id=4, tournament={"name": "UTR", "category": {"name": "UTR"}})
        ms = sofascore.parse_events({"events": [ev, dbl, women, exo]})
        self.assertEqual([m.match_id for m in ms], ["ss1", "ss3"])
        self.assertEqual(ms[0].sets, [(4, 6), (6, 7), (3, 6)])
        self.assertEqual((ms[0].gender, ms[1].gender), ("M", "W"))
        self.assertTrue(ms[0].straight_loss("Rublev A."))


if __name__ == "__main__":
    unittest.main()
