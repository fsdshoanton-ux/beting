"""Базовые структуры данных."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_PLAYER_RE = re.compile(r"^\s*(?P<team>.*?)\s*\((?P<player>[^()]+)\)\s*$")


def parse_participant(name: str) -> tuple[str, str]:
    """Разбирает участника киберматча на (команда, игрок).

    Букмекеры пишут участников как "Boston Bruins (Kraken)" — в скобках ник
    игрока. Статистику считаем по игроку, а не по команде: команду он
    выбирает каждый матч сам. Если скобок нет, игроком считаем всю строку.
    """
    m = _PLAYER_RE.match(name or "")
    if m and m.group("player").strip():
        return m.group("team").strip(), m.group("player").strip()
    name = (name or "").strip()
    return name, name


@dataclass
class Match:
    match_id: str
    league: str
    start_time: str  # ISO 8601, UTC
    player1: str
    player2: str
    periods: list[tuple[int, int]] = field(default_factory=list)
    team1: str = ""
    team2: str = ""
    source: str = "manual"

    def period_total(self, period: int) -> int | None:
        """Сумма голов в периоде (нумерация с 1) или None, если его нет."""
        if 1 <= period <= len(self.periods):
            g1, g2 = self.periods[period - 1]
            return g1 + g2
        return None

    def has_player(self, player: str) -> bool:
        return player in (self.player1, self.player2)


def parse_periods(text: str) -> list[tuple[int, int]]:
    """'2:1;0:0;1-2' или '(2-1 0-0 1-2)' -> [(2, 1), (0, 0), (1, 2)]."""
    pairs = re.findall(r"(\d+)\s*[:\-]\s*(\d+)", text or "")
    return [(int(a), int(b)) for a, b in pairs]


def format_periods(periods: list[tuple[int, int]]) -> str:
    return ";".join(f"{a}:{b}" for a, b in periods)
