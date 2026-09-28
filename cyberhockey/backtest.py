"""Пошаговый бэктест: каждый матч оцениваем только по матчам ДО него.

Показывает калибровку (совпадают ли предсказанные вероятности с
фактом) и результат ставок по правилам сигналов. Без этой проверки
любой «сигнал» — гадание.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import SignalRules, estimate, is_signal
from .models import Match
from .stats import History


@dataclass
class BacktestResult:
    n_predictions: int = 0
    brier: float = 0.0
    brier_baseline: float = 0.0          # если всегда предсказывать среднюю частоту лиги
    buckets: dict[int, list[int]] = field(default_factory=dict)  # десяток % -> [прогнозов, попаданий]
    bets: int = 0
    wins: int = 0
    profit: float = 0.0

    @property
    def roi(self) -> float:
        return self.profit / self.bets if self.bets else 0.0


def run(matches: list[Match], period: int, line: float, odds_for, rules: SignalRules,
        warmup: int = 300, last_n: int = 100) -> BacktestResult:
    """``odds_for(match) -> float | None`` — кэф на ТБ, доступный до начала периода."""
    matches = sorted(matches, key=lambda m: m.start_time)
    h = History(matches[:warmup], last_n=last_n)
    res = BacktestResult()
    for m in matches[warmup:]:
        total = m.period_total(period)
        if total is not None:
            est = estimate(h, m.player1, m.player2, period, line)
            hit = int(total > line)
            base = h.league(period).over_rate(line)
            res.n_predictions += 1
            res.brier += (est.prob - hit) ** 2
            res.brier_baseline += (base - hit) ** 2
            b = res.buckets.setdefault(min(int(est.prob * 10), 9), [0, 0])
            b[0] += 1
            b[1] += hit
            odds = odds_for(m)
            if odds and is_signal(est, odds, rules):
                res.bets += 1
                res.wins += hit
                res.profit += (odds - 1) if hit else -1
        h.add(m)
    if res.n_predictions:
        res.brier /= res.n_predictions
        res.brier_baseline /= res.n_predictions
    return res


def format_result(r: BacktestResult, odds_note: str) -> str:
    out = [f"Прогнозов: {r.n_predictions}",
           f"Brier: модель {r.brier:.4f} vs «средняя лиги» {r.brier_baseline:.4f} "
           f"({'модель лучше' if r.brier < r.brier_baseline else 'модель НЕ лучше среднего'})",
           "Калибровка (прогноз -> факт):"]
    for k in sorted(r.buckets):
        n, hits = r.buckets[k]
        out.append(f"  {k * 10:>3}-{k * 10 + 10}%: {n:>5} прогнозов, факт {hits / n:.1%}")
    out.append(f"Ставки по сигналам ({odds_note}): {r.bets}, выиграно {r.wins}"
               + (f" ({r.wins / r.bets:.1%})" if r.bets else "")
               + f", прибыль {r.profit:+.1f} ед., ROI {r.roi:+.1%}")
    return "\n".join(out)
