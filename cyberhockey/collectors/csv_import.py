"""Импорт завершённых матчей из CSV.

Формат (заголовок обязателен):
    match_id,league,start_time,team1,team2,periods
    123,Киберхоккей. H2H GG League,2026-09-01T10:00:00+00:00,Boston Bruins (Kraken),Tampa Bay Lightning (Ghost),2:1;0:0;1:2

team1/team2 — как пишет букмекер, ник игрока в скобках.
Так можно загрузить историю из любого источника: выгрузку, парсер,
ручной ввод.
"""
from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta, timezone

from ..models import Match, format_periods, parse_participant, parse_periods


def read_csv(path: str, source: str = "csv") -> list[Match]:
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            team1, p1 = parse_participant(row["team1"])
            team2, p2 = parse_participant(row["team2"])
            out.append(Match(
                match_id=row["match_id"], league=row.get("league") or "",
                start_time=row["start_time"], player1=p1, player2=p2,
                team1=team1, team2=team2, periods=parse_periods(row["periods"]),
                source=source))
    return out


def write_csv(path: str, matches: list[Match]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["match_id", "league", "start_time", "team1", "team2", "periods"])
        for m in matches:
            w.writerow([m.match_id, m.league, m.start_time, f"{m.team1} ({m.player1})",
                        f"{m.team2} ({m.player2})", format_periods(m.periods)])


def generate_demo(n: int = 3000, seed: int = 7, periods: int = 3) -> list[Match]:
    """Синтетические матчи для проверки работы — НЕ реальная статистика.

    Каждому игроку задан скрытый «темп» голов, у пары есть небольшой
    собственный эффект, 2-й и 3-й периоды чуть результативнее первого.
    """
    rng = random.Random(seed)
    names = ["Kraken", "Ghost", "Viper", "Titan", "Blaze", "Frost", "Rocket",
             "Shadow", "Storm", "Hawk", "Wolf", "Nova"]
    teams = ["Boston Bruins", "Tampa Bay Lightning", "Colorado Avalanche",
             "Vegas Golden Knights", "New York Rangers", "Edmonton Oilers"]
    pace = {p: rng.uniform(0.7, 1.4) for p in names}
    pair_eff = {}
    period_base = [1.6, 1.9, 2.0, 2.0][:periods]
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)

    def poisson(lam: float) -> int:
        # алгоритм Кнута, для малых lam достаточно
        l, k, p = pow(2.718281828, -lam), 0, 1.0
        while True:
            p *= rng.random()
            if p <= l:
                return k
            k += 1

    out = []
    for i in range(n):
        a, b = rng.sample(names, 2)
        key = frozenset((a, b))
        pair_eff.setdefault(key, rng.uniform(0.85, 1.15))
        per = []
        for base in period_base:
            lam = base * pace[a] * pace[b] * pair_eff[key] / 2
            per.append((poisson(lam), poisson(lam)))
        out.append(Match(
            match_id=f"demo{i}", league="Киберхоккей. Demo League",
            start_time=(start + timedelta(minutes=20 * i)).isoformat(timespec="seconds"),
            player1=a, player2=b, team1=rng.choice(teams), team2=rng.choice(teams),
            periods=per, source="demo"))
    return out
