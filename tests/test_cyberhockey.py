import math
import os
import tempfile
import unittest

from cyberhockey import backtest
from cyberhockey.collectors import csv_import, fonbet
from cyberhockey.model import SignalRules, estimate, is_signal, poisson_over
from cyberhockey.models import Match, parse_participant, parse_periods
from cyberhockey.stats import History
from cyberhockey.storage import Storage


def m(i, a, b, *periods):
    return Match(f"m{i}", "L", f"2026-01-01T00:{i:02d}:00", a, b, list(periods))


class ParsingTest(unittest.TestCase):
    def test_participant(self):
        self.assertEqual(parse_participant("Boston Bruins (Kraken)"), ("Boston Bruins", "Kraken"))
        self.assertEqual(parse_participant("Kraken"), ("Kraken", "Kraken"))

    def test_periods(self):
        self.assertEqual(parse_periods("(2-1 0-0 1-2)"), [(2, 1), (0, 0), (1, 2)])
        self.assertEqual(parse_periods("2:1;0:3"), [(2, 1), (0, 3)])
        self.assertEqual(parse_periods(""), [])


class ModelTest(unittest.TestCase):
    def test_poisson_over(self):
        lam = 2.0
        expected = 1 - math.exp(-lam) * (1 + lam)  # P(X >= 2)
        self.assertAlmostEqual(poisson_over(lam, 1.5), expected)
        self.assertEqual(poisson_over(1.0, 1.5, already=2), 1.0)
        self.assertEqual(poisson_over(0.0, 0.5), 0.0)

    def test_high_scoring_pair_gets_higher_probability(self):
        ms = []
        for i in range(40):
            ms.append(m(i, "Fast", "Other", (3, 2)))
            ms.append(m(i + 100, "Slow", "Other", (0, 1)))
        h = History(ms)
        fast = estimate(h, "Fast", "Other", 1, 2.5)
        slow = estimate(h, "Slow", "Other", 1, 2.5)
        self.assertGreater(fast.prob, slow.prob + 0.3)
        self.assertLess(slow.prob, 0.4)

    def test_live_uses_remaining_time(self):
        h = History([m(i, "A", "B", (1, 1)) for i in range(30)])
        pre = estimate(h, "A", "B", 1, 2.5)
        late = estimate(h, "A", "B", 1, 2.5, elapsed=0.9, current_goals=0)
        done = estimate(h, "A", "B", 1, 2.5, elapsed=0.5, current_goals=3)
        self.assertLess(late.prob, pre.prob)
        self.assertEqual(done.prob, 1.0)

    def test_signal_rules(self):
        h = History([m(i, "A", "B", (2, 2)) for i in range(30)])
        est = estimate(h, "A", "B", 1, 2.5)
        self.assertTrue(is_signal(est, 1.9, SignalRules()))
        self.assertFalse(is_signal(est, 1.9, SignalRules(min_games=100)))


class BacktestTest(unittest.TestCase):
    def test_runs_on_demo(self):
        ms = csv_import.generate_demo(800)
        r = backtest.run(ms, 1, 1.5, lambda _: 1.85, SignalRules(), warmup=200)
        self.assertEqual(r.n_predictions, 600)
        self.assertLess(r.brier, 0.3)


class StorageTest(unittest.TestCase):
    def test_roundtrip_and_settle(self):
        with tempfile.TemporaryDirectory() as d:
            db = Storage(os.path.join(d, "t.db"))
            db.upsert_match(m(1, "A", "B", (1, 0), (2, 1)))
            self.assertTrue(db.save_signal("m1", "A", "B", 2, 2.5, 1.9, 0.6, 0.14))
            self.assertFalse(db.save_signal("m1", "A", "B", 2, 2.5, 1.9, 0.6, 0.14))
            self.assertEqual(db.load_matches()[0].periods, [(1, 0), (2, 1)])
            self.assertEqual(db.settle_signals(), 1)
            self.assertEqual(db.signals()[0]["result"], "win")
            db.close()

    def test_csv_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.csv")
            ms = csv_import.generate_demo(5)
            csv_import.write_csv(path, ms)
            back = csv_import.read_csv(path)
            self.assertEqual([x.periods for x in back], [x.periods for x in ms])
            self.assertEqual(back[0].player1, ms[0].player1)


class FonbetParseTest(unittest.TestCase):
    FEED = {
        "sports": [
            {"id": 10, "kind": "segment", "name": "Киберхоккей. H2H GG League"},
            {"id": 11, "kind": "segment", "name": "Футбол. АПЛ"},
        ],
        "events": [
            {"id": 1, "level": 1, "sportId": 10, "team1": "Boston Bruins (Kraken)",
             "team2": "New York Rangers (Ghost)", "startTime": 1790000000, "place": "live"},
            {"id": 2, "parentId": 1, "level": 2, "sportId": 10, "name": "2-й период"},
            {"id": 3, "level": 1, "sportId": 11, "team1": "X", "team2": "Y"},
        ],
        "eventMiscs": [{"id": 1, "score1": 2, "score2": 1, "comment": "(2-1 0-0)", "timerSeconds": 400}],
        "customFactors": [
            {"e": 2, "factors": [{"f": 930, "v": 1.85, "pt": "1.5"}, {"f": 931, "v": 1.95, "pt": "1.5"},
                                 {"f": 1696, "v": 2.6, "pt": "2.5"}]},
        ],
    }

    def test_parse(self):
        [lm] = fonbet.parse_feed(self.FEED)
        self.assertEqual((lm.player1, lm.player2), ("Kraken", "Ghost"))
        self.assertEqual(lm.periods, [(2, 1), (0, 0)])
        self.assertEqual(lm.current_period, 2)
        self.assertEqual(lm.period_totals[2], [(1.5, 1.85, 1.95), (2.5, 2.6, None)])


if __name__ == "__main__":
    unittest.main()
