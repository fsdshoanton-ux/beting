"""Статистика голов по периодам: лига, игроки, пары игроков."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .models import Match


@dataclass
class PeriodSample:
    """Выборка сумм голов в одном периоде."""
    totals: list[int]

    @property
    def n(self) -> int:
        return len(self.totals)

    @property
    def mean(self) -> float:
        return sum(self.totals) / self.n if self.n else 0.0

    def over_rate(self, line: float) -> float:
        return sum(t > line for t in self.totals) / self.n if self.n else 0.0

    def over_hits(self, line: float) -> int:
        return sum(t > line for t in self.totals)


class History:
    """Индекс завершённых матчей для быстрых выборок.

    ``last_n`` ограничивает выборку по игроку/паре последними N матчами:
    форма игроков в киберхоккее меняется быстро, старые матчи мешают.
    """

    def __init__(self, matches: list[Match], last_n: int = 100):
        self.matches = sorted(matches, key=lambda m: m.start_time)
        self.last_n = last_n
        self.by_player: dict[str, list[Match]] = defaultdict(list)
        self.by_pair: dict[frozenset, list[Match]] = defaultdict(list)
        self._league: dict[int, list[int]] = defaultdict(list)
        for m in self.matches:
            self._index(m)

    def _index(self, m: Match) -> None:
        self.by_player[m.player1].append(m)
        self.by_player[m.player2].append(m)
        self.by_pair[frozenset((m.player1, m.player2))].append(m)
        for i, (g1, g2) in enumerate(m.periods, start=1):
            self._league[i].append(g1 + g2)

    def add(self, m: Match) -> None:
        """Добавляет более поздний матч (для пошагового бэктеста)."""
        self.matches.append(m)
        self._index(m)

    @staticmethod
    def _sample(matches: list[Match], period: int, last_n: int | None) -> PeriodSample:
        if last_n:
            matches = matches[-last_n:]
        return PeriodSample([t for m in matches if (t := m.period_total(period)) is not None])

    def league(self, period: int) -> PeriodSample:
        return PeriodSample(self._league.get(period, []))

    def player(self, player: str, period: int) -> PeriodSample:
        return self._sample(self.by_player.get(player, []), period, self.last_n)

    def pair(self, p1: str, p2: str, period: int) -> PeriodSample:
        return self._sample(self.by_pair.get(frozenset((p1, p2)), []), period, self.last_n)

    def players(self) -> list[str]:
        return sorted(self.by_player)

    def max_period(self) -> int:
        return max(self._league, default=0)


@dataclass
class PlayerRow:
    player: str
    games: int
    mean: float
    over_rate: float


def player_table(h: History, period: int, line: float, min_games: int = 10) -> list[PlayerRow]:
    """Игроки, отсортированные по доле ТБ в заданном периоде."""
    rows = []
    for p in h.players():
        s = h.player(p, period)
        if s.n >= min_games:
            rows.append(PlayerRow(p, s.n, s.mean, s.over_rate(line)))
    rows.sort(key=lambda r: (r.over_rate, r.mean), reverse=True)
    return rows


def pair_table(h: History, period: int, line: float, min_games: int = 5) -> list[tuple[str, str, PlayerRow]]:
    rows = []
    for key in h.by_pair:
        if len(key) != 2:
            continue
        a, b = sorted(key)
        s = h.pair(a, b, period)
        if s.n >= min_games:
            rows.append((a, b, PlayerRow(f"{a} vs {b}", s.n, s.mean, s.over_rate(line))))
    rows.sort(key=lambda r: (r[2].over_rate, r[2].mean), reverse=True)
    return rows
