"""Стратегия: игрок проиграл N матчей подряд всухую — в следующем матче он берёт 1-й сет?

Для каждого матча берём историю игрока до его начала. Если последние
``streak`` матчей — поражения без взятых сетов (0-2, а на «Шлемах» у мужчин 0-3),
то матч — срабатывание стратегии. Смотрим, выиграл ли игрок первый сет.

Технические поражения (walkover) в историю не попадают: матча не было.
Матч с отказом (retired) прерывает серию: это не поражение 0-2 в чистом виде.
Если в целевом матче 1-й сет не доигран, исход не определён и матч не считается.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from .models import TennisMatch


@dataclass
class Rules:
    streak: int = 2              # сколько матчей подряд
    result: str = "loss"         # loss — серия поражений, win — серия побед (для сравнения)
    straight_sets: bool = True   # True — только всухую (0-2 / 0-3), False — с любым счётом
    max_gap_days: float | None = None  # макс. дней между последним матчем серии и целевым

    def describe(self) -> str:
        what = "поражений" if self.result == "loss" else "побед"
        how = " всухую" if self.straight_sets else ""
        gap = f", перерыв ≤ {self.max_gap_days:g} дн." if self.max_gap_days else ""
        return f"{self.streak} {what} подряд{how}{gap}"

    def fits(self, m: TennisMatch, player: str) -> bool:
        if self.result == "loss":
            if self.straight_sets:
                return m.straight_loss(player)
            return m.status == "finished" and not m.won(player)
        if self.straight_sets:
            return m.straight_loss(m.opponent(player))
        return m.status == "finished" and m.won(player)


@dataclass
class Trigger:
    player: str
    match: TennisMatch
    previous: list[TennisMatch]

    @property
    def first_set_won(self) -> bool | None:
        w = self.match.first_set_winner()
        return None if w is None else w == self.match.side(self.player)

    @property
    def match_won(self) -> bool:
        return self.match.won(self.player)


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def find_triggers(matches: list[TennisMatch], rules: Rules,
                  since: str | None = None, until: str | None = None) -> list[Trigger]:
    """Все срабатывания; целевой матч — в окне [since, until), история — вся."""
    history: dict[str, list[TennisMatch]] = defaultdict(list)
    out = []
    for m in sorted(matches, key=lambda m: (m.start_time, m.match_id)):
        if m.status == "walkover" or not m.winner:
            continue
        in_window = (since is None or m.start_time >= since) and (until is None or m.start_time < until)
        for p in (m.player1, m.player2):
            prev = history[p][-rules.streak:]
            if (in_window and len(prev) == rules.streak
                    and all(rules.fits(x, p) for x in prev)
                    and (rules.max_gap_days is None
                         or (_ts(m.start_time) - _ts(prev[-1].start_time)).total_seconds()
                         <= rules.max_gap_days * 86400)):
                out.append(Trigger(p, m, list(prev)))
        for p in (m.player1, m.player2):
            history[p].append(m)
    return out


# --- статистика -----------------------------------------------------------

def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% доверительный интервал Уилсона для доли k/n."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def binom_p_value(k: int, n: int, p: float = 0.5) -> float:
    """Точный двусторонний биномиальный тест: насколько k/n отличается от p случайно."""
    if n == 0:
        return 1.0

    def logpmf(i: int) -> float:
        return (math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
                + i * math.log(p) + (n - i) * math.log(1 - p))

    lk = logpmf(k)
    return min(1.0, sum(math.exp(logpmf(i)) for i in range(n + 1) if logpmf(i) <= lk + 1e-9))


@dataclass
class Summary:
    label: str
    n: int            # матчей с известным исходом 1-го сета
    first_set: int    # из них игрок выиграл 1-й сет
    matches_won: int  # из них игрок выиграл матч

    @property
    def rate(self) -> float:
        return self.first_set / self.n if self.n else 0.0

    @property
    def ci(self) -> tuple[float, float]:
        return wilson(self.first_set, self.n)

    @property
    def p_value(self) -> float:
        return binom_p_value(self.first_set, self.n)

    @property
    def break_even_odds(self) -> float | None:
        return 1 / self.rate if self.first_set else None


def summarize(label: str, triggers: list[Trigger]) -> Summary:
    known = [t for t in triggers if t.first_set_won is not None]
    return Summary(label, len(known), sum(t.first_set_won for t in known),
                   sum(t.match_won for t in known))


def format_table(rows: list[Summary]) -> str:
    head = (f"{'группа':<40}{'матчей':>7}{'1-й сет':>9}{'доля':>8}"
            f"{'95% интервал':>16}{'p':>8}{'безуб.кэф':>11}{'матч':>8}")
    lines = [head, "-" * len(head)]
    for s in rows:
        lo, hi = s.ci
        be = f"{s.break_even_odds:.2f}" if s.break_even_odds else "—"
        mw = f"{s.matches_won / s.n:.0%}" if s.n else "—"
        lines.append(f"{s.label:<40}{s.n:>7}{s.first_set:>9}{s.rate:>8.1%}"
                     f"{f'{lo:.0%}–{hi:.0%}':>16}{s.p_value:>8.3f}{be:>11}{mw:>8}")
    return "\n".join(lines)


def format_trigger(t: Trigger) -> str:
    m = t.match
    fs = {True: "1-й сет ВЗЯЛ", False: "1-й сет проиграл", None: "1-й сет не доигран"}[t.first_set_won]

    def score_for(x: TennisMatch, p: str) -> str:
        sets = x.sets if x.side(p) == 1 else [(b, a) for a, b in x.sets]
        return " ".join(f"{a}-{b}" for a, b in sets)

    prev = "; ".join(f"{x.start_time[:10]} vs {x.opponent(t.player)} {score_for(x, t.player)}"
                     for x in t.previous)
    return (f"{m.start_time[:16].replace('T', ' ')} [{m.gender} {m.tour}] {t.player} — "
            f"{m.opponent(t.player)}: {score_for(m, t.player)} → {fs}, "
            f"матч {'выиграл' if t.match_won else 'проиграл'}\n    до этого: {prev}")
