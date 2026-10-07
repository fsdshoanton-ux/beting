"""Синтетические теннисные матчи для проверки работы — НЕ реальная статистика."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from .models import TennisMatch


def _set(rng: random.Random, p: float) -> tuple[int, int]:
    """Сет со стороны игрока, выигрывающего каждый гейм с вероятностью p (грубо)."""
    a = b = 0
    while True:
        if rng.random() < p:
            a += 1
        else:
            b += 1
        if (max(a, b) >= 6 and abs(a - b) >= 2) or max(a, b) == 7:
            return a, b
        if a == 6 and b == 6:
            return (7, 6) if rng.random() < p else (6, 7)


def generate(days: int = 28, per_day: int = 120, seed: int = 11,
             end: datetime | None = None) -> list[TennisMatch]:
    """У каждого игрока скрытая сила; игроки слабее проигрывают чаще,
    поэтому серии поражений тут — признак слабости, а не «повода отыграться»."""
    rng = random.Random(seed)
    end = end or datetime(2026, 10, 7, tzinfo=timezone.utc)
    start = end - timedelta(days=days)
    pools = {g: {f"{'Player' if g == 'M' else 'Playa'} {i:03d}": rng.gauss(0, 1) for i in range(300)}
             for g in "MW"}
    out = []
    for d in range(days):
        for i in range(per_day):
            g = "M" if i % 2 == 0 else "W"
            a, b = rng.sample(list(pools[g]), 2)
            p_game = 0.5 + 0.06 * (pools[g][a] - pools[g][b])
            p_game = min(0.8, max(0.2, p_game))
            sets, won = [], [0, 0]
            while max(won) < 2:
                s = _set(rng, p_game)
                sets.append(s)
                won[0 if s[0] > s[1] else 1] += 1
            out.append(TennisMatch(
                match_id=f"demo{d}_{i}",
                start_time=(start + timedelta(days=d, minutes=6 * i)).isoformat(timespec="seconds"),
                gender=g, tour="Demo", tournament="Demo Open", player1=a, player2=b,
                sets=sets, winner=1 if won[0] == 2 else 2, source="demo"))
    return out
