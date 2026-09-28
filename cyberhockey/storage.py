"""Хранение матчей, снимков коэффициентов и выданных сигналов в SQLite."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .models import Match

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    match_id   TEXT PRIMARY KEY,
    source     TEXT NOT NULL,
    league     TEXT NOT NULL,
    start_time TEXT NOT NULL,
    team1      TEXT, team2 TEXT,
    player1    TEXT NOT NULL,
    player2    TEXT NOT NULL,
    finished   INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS periods (
    match_id  TEXT NOT NULL REFERENCES matches(match_id),
    period_no INTEGER NOT NULL,
    goals1    INTEGER NOT NULL,
    goals2    INTEGER NOT NULL,
    PRIMARY KEY (match_id, period_no)
);
CREATE TABLE IF NOT EXISTS odds (
    ts         TEXT NOT NULL,
    match_id   TEXT NOT NULL,
    period_no  INTEGER NOT NULL,
    line       REAL NOT NULL,
    over_odds  REAL,
    under_odds REAL
);
CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    match_id    TEXT,
    player1     TEXT NOT NULL,
    player2     TEXT NOT NULL,
    period_no   INTEGER NOT NULL,
    line        REAL NOT NULL,
    odds        REAL NOT NULL,
    prob        REAL NOT NULL,
    edge        REAL NOT NULL,
    result      TEXT,            -- win / loss / NULL (не рассчитан)
    UNIQUE (match_id, period_no, line)
);
CREATE INDEX IF NOT EXISTS idx_matches_p1 ON matches(player1);
CREATE INDEX IF NOT EXISTS idx_matches_p2 ON matches(player2);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage:
    def __init__(self, path: str = "data/cyberhockey.db"):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # --- матчи -------------------------------------------------------------
    def upsert_match(self, m: Match, finished: bool = True) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO matches VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(match_id) DO UPDATE SET
                     league=excluded.league, start_time=excluded.start_time,
                     team1=excluded.team1, team2=excluded.team2,
                     player1=excluded.player1, player2=excluded.player2,
                     finished=excluded.finished""",
                (m.match_id, m.source, m.league, m.start_time, m.team1, m.team2,
                 m.player1, m.player2, int(finished)),
            )
            for i, (g1, g2) in enumerate(m.periods, start=1):
                self.conn.execute(
                    """INSERT INTO periods VALUES (?,?,?,?)
                       ON CONFLICT(match_id, period_no) DO UPDATE SET
                         goals1=excluded.goals1, goals2=excluded.goals2""",
                    (m.match_id, i, g1, g2),
                )

    def load_matches(self, league: str | None = None,
                     finished_only: bool = True) -> list[Match]:
        sql = "SELECT * FROM matches WHERE 1=1"
        args: list = []
        if finished_only:
            sql += " AND finished=1"
        if league:
            sql += " AND league LIKE ?"
            args.append(f"%{league}%")
        sql += " ORDER BY start_time"
        rows = self.conn.execute(sql, args).fetchall()
        periods: dict[str, list[tuple[int, int]]] = {}
        for r in self.conn.execute(
                "SELECT match_id, goals1, goals2 FROM periods ORDER BY match_id, period_no"):
            periods.setdefault(r["match_id"], []).append((r["goals1"], r["goals2"]))
        return [
            Match(match_id=r["match_id"], league=r["league"], start_time=r["start_time"],
                  player1=r["player1"], player2=r["player2"], team1=r["team1"] or "",
                  team2=r["team2"] or "", source=r["source"],
                  periods=periods.get(r["match_id"], []))
            for r in rows
        ]

    # --- коэффициенты --------------------------------------------------------
    def save_odds(self, match_id: str, period: int, line: float,
                  over: float | None, under: float | None) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO odds VALUES (?,?,?,?,?,?)",
                              (utcnow(), match_id, period, line, over, under))

    # --- сигналы -----------------------------------------------------------
    def save_signal(self, match_id: str | None, p1: str, p2: str, period: int,
                    line: float, odds: float, prob: float, edge: float) -> bool:
        """Возвращает False, если такой сигнал уже был (чтобы не спамить)."""
        try:
            with self.conn:
                self.conn.execute(
                    """INSERT INTO signals (ts, match_id, player1, player2, period_no,
                                            line, odds, prob, edge)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (utcnow(), match_id, p1, p2, period, line, odds, prob, edge))
            return True
        except sqlite3.IntegrityError:
            return False

    def settle_signals(self) -> int:
        """Проставляет win/loss сигналам, по матчам которых есть результат."""
        rows = self.conn.execute(
            """SELECT s.id, s.line, p.goals1 + p.goals2 AS total
               FROM signals s JOIN matches m ON m.match_id = s.match_id AND m.finished = 1
               JOIN periods p ON p.match_id = s.match_id AND p.period_no = s.period_no
               WHERE s.result IS NULL""").fetchall()
        with self.conn:
            for r in rows:
                res = "win" if r["total"] > r["line"] else "loss"
                self.conn.execute("UPDATE signals SET result=? WHERE id=?", (res, r["id"]))
        return len(rows)

    def signals(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM signals ORDER BY ts").fetchall()
