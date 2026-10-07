"""Модель теннисного матча и чтение/запись CSV."""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field

FIELDS = ["match_id", "start_time", "gender", "tour", "tournament",
          "player1", "player2", "score", "winner", "status", "source"]


def set_complete(a: int, b: int) -> bool:
    """Сыгран ли сет до конца (6-4, 7-5, 7-6). Нужен для матчей с отказом."""
    hi, lo = max(a, b), min(a, b)
    return (hi >= 6 and hi - lo >= 2) or (hi == 7 and lo == 6)


def parse_score(text: str) -> list[tuple[int, int]]:
    """'6-4 3-6 7-6' -> [(6, 4), (3, 6), (7, 6)]; счёт со стороны player1."""
    out = []
    for part in (text or "").replace(",", " ").split():
        a, b = part.replace(":", "-").split("-")[:2]
        out.append((int(a), int(b)))
    return out


def format_score(sets: list[tuple[int, int]]) -> str:
    return " ".join(f"{a}-{b}" for a, b in sets)


@dataclass
class TennisMatch:
    match_id: str
    start_time: str                   # ISO 8601, UTC
    gender: str                       # M — мужчины, W — женщины
    tour: str                         # ATP / WTA / Challenger / ITF Men / ITF Women ...
    tournament: str
    player1: str
    player2: str
    sets: list[tuple[int, int]] = field(default_factory=list)  # геймы по сетам, player1 первым
    winner: int = 0                   # 1 или 2
    status: str = "finished"          # finished / retired / walkover
    source: str = ""

    def side(self, player: str) -> int:
        return 1 if player == self.player1 else 2

    def opponent(self, player: str) -> str:
        return self.player2 if player == self.player1 else self.player1

    def won(self, player: str) -> bool:
        return self.winner == self.side(player)

    def sets_won(self, player: str) -> int:
        s = self.side(player)
        return sum(1 for a, b in self.sets
                   if set_complete(a, b) and ((a > b) if s == 1 else (b > a)))

    def straight_loss(self, player: str) -> bool:
        """Проиграл, не взяв ни сета, и матч доигран (2-0 / 3-0 в пользу соперника)."""
        return self.status == "finished" and not self.won(player) and self.sets_won(player) == 0

    def first_set_winner(self) -> int | None:
        """1 или 2; None, если первый сет не доигран (отказ, неполные данные)."""
        if not self.sets or not set_complete(*self.sets[0]):
            return None
        a, b = self.sets[0]
        return 1 if a > b else 2


def read_csv(path: str) -> list[TennisMatch]:
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append(TennisMatch(
                match_id=row["match_id"], start_time=row["start_time"],
                gender=row.get("gender") or "", tour=row.get("tour") or "",
                tournament=row.get("tournament") or "",
                player1=row["player1"], player2=row["player2"],
                sets=parse_score(row.get("score", "")), winner=int(row.get("winner") or 0),
                status=row.get("status") or "finished", source=row.get("source") or ""))
    return out


def write_csv(path: str, matches: list[TennisMatch]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        for m in sorted(matches, key=lambda m: (m.start_time, m.match_id)):
            w.writerow([m.match_id, m.start_time, m.gender, m.tour, m.tournament,
                        m.player1, m.player2, format_score(m.sets), m.winner, m.status, m.source])


def merge(old: list[TennisMatch], new: list[TennisMatch]) -> list[TennisMatch]:
    """Объединить по match_id, новые записи заменяют старые."""
    by_id = {m.match_id: m for m in old}
    by_id.update({m.match_id: m for m in new})
    return list(by_id.values())
