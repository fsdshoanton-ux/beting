"""Оценка вероятности ТБ в периоде и поиск перевеса над коэффициентом.

Идея модели:
  1. База — среднее голов в этом периоде по лиге (mu).
  2. У каждого игрока свой множитель f = (среднее в его матчах) / mu,
     «стянутый» к 1, пока матчей мало (байесовское сглаживание).
  3. Ожидаемые голы lam = mu * f1 * f2; если есть история личных встреч,
     lam дополнительно стягивается к их среднему.
  4. P(ТБ) по Пуассону, затем уточняется фактической частотой ТБ
     в матчах этих игроков (Пуассон в роли априорного значения).
  5. Live: если период идёт, считаем голы на оставшееся время.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .stats import History, PeriodSample

PLAYER_PRIOR = 15   # «виртуальных матчей» лиги в оценке игрока
PAIR_PRIOR = 10     # столько же для личных встреч
EMPIRIC_PRIOR = 20  # вес Пуассона против фактической частоты ТБ


def poisson_over(lam: float, line: float, already: int = 0) -> float:
    """P(already + Poisson(lam) > line)."""
    need = math.floor(line) + 1 - already  # сколько голов ещё нужно
    if need <= 0:
        return 1.0
    if lam <= 0:
        return 0.0
    term = math.exp(-lam)
    cdf = term
    for k in range(1, need):
        term *= lam / k
        cdf += term
    return max(0.0, 1.0 - cdf)


def _shrunk_factor(sample: PeriodSample, mu: float, prior: float) -> float:
    if mu <= 0:
        return 1.0
    return (sum(sample.totals) + prior * mu) / ((sample.n + prior) * mu)


@dataclass
class Estimate:
    player1: str
    player2: str
    period: int
    line: float
    lam: float           # ожидаемые голы в периоде (на весь период)
    p_poisson: float     # вероятность ТБ по модели Пуассона
    p_hist: float | None # фактическая частота ТБ у этих игроков
    prob: float          # итоговая оценка
    n1: int
    n2: int
    n_pair: int
    league_mean: float
    live: bool = False

    def fair_odds(self) -> float:
        return 1 / self.prob if self.prob > 0 else float("inf")

    def edge(self, odds: float) -> float:
        """Ожидаемый доход на 1 ед. ставки: p * k - 1."""
        return self.prob * odds - 1


def estimate(h: History, p1: str, p2: str, period: int, line: float,
             elapsed: float = 0.0, current_goals: int = 0) -> Estimate:
    """Оценка ТБ ``line`` в периоде ``period`` для пары игроков.

    ``elapsed`` — доля уже сыгранного времени периода (0..1), ``current_goals``
    — голов в этом периоде на данный момент (для live).
    """
    league = h.league(period)
    mu = league.mean
    s1, s2, sp = h.player(p1, period), h.player(p2, period), h.pair(p1, p2, period)

    lam = mu * _shrunk_factor(s1, mu, PLAYER_PRIOR) * _shrunk_factor(s2, mu, PLAYER_PRIOR)
    if sp.n:
        lam = (sum(sp.totals) + PAIR_PRIOR * lam) / (sp.n + PAIR_PRIOR)

    live = elapsed > 0 or current_goals > 0
    elapsed = min(max(elapsed, 0.0), 1.0)
    p_pois = poisson_over(lam * (1 - elapsed), line, current_goals)

    if live:
        # По ходу периода частота ТБ «с нуля» неприменима — только модель.
        p_hist, prob = None, p_pois
    else:
        # Личные встречи уже входят в обе выборки игроков, т.е. учитываются дважды.
        n = s1.n + s2.n
        hits = s1.over_hits(line) + s2.over_hits(line)
        p_hist = hits / n if n else None
        prob = (hits + EMPIRIC_PRIOR * p_pois) / (n + EMPIRIC_PRIOR)

    return Estimate(p1, p2, period, line, lam, p_pois, p_hist, prob,
                    s1.n, s2.n, sp.n, mu, live)


@dataclass
class SignalRules:
    min_edge: float = 0.05     # перевес не меньше 5%
    min_prob: float = 0.55
    min_games: int = 15        # у каждого игрока в этом периоде
    min_odds: float = 1.5
    max_odds: float = 3.5


def is_signal(est: Estimate, odds: float, rules: SignalRules) -> bool:
    return (
        rules.min_odds <= odds <= rules.max_odds
        and min(est.n1, est.n2) >= rules.min_games
        and est.prob >= rules.min_prob
        and est.edge(odds) >= rules.min_edge
    )


def kelly_fraction(prob: float, odds: float, fraction: float = 0.25) -> float:
    """Доля банка по дробному Келли (по умолчанию 1/4 — полный слишком агрессивен)."""
    b = odds - 1
    if b <= 0:
        return 0.0
    return max(0.0, (prob * odds - 1) / b * fraction)


def format_estimate(est: Estimate, odds: float | None = None) -> str:
    lines = [
        f"{est.player1} vs {est.player2} | {est.period}-й период | ТБ {est.line}",
        f"  ожидаемо голов: {est.lam:.2f} (лига: {est.league_mean:.2f})",
        f"  матчей в выборке: {est.player1}={est.n1}, {est.player2}={est.n2}, личных={est.n_pair}",
        f"  P(ТБ) Пуассон: {est.p_poisson:.1%}"
        + (f", факт: {est.p_hist:.1%}" if est.p_hist is not None else "")
        + f" -> итог {est.prob:.1%} (справедливый кэф {est.fair_odds():.2f})",
    ]
    if odds:
        lines.append(f"  кэф {odds:.2f}: перевес {est.edge(odds):+.1%}, "
                     f"ставка по 1/4 Келли {kelly_fraction(est.prob, odds):.1%} банка")
    return "\n".join(lines)
