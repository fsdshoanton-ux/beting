"""Сборщик live-линии киберхоккея с публичного JSON-фида Фонбета.

ВНИМАНИЕ: формат фида восстановлен по открытым описаниям и не проверялся
из среды разработки (доступ к сайту был закрыт). Перед использованием
откройте fon.bet -> DevTools -> Network, найдите запрос ``events/list``
и сверьте поля и id факторов тотала (``TOTAL_FACTORS``) с реальным ответом.

Ожидаемая структура ответа:
    sports:        [{id, parentId, kind: "segment", name: "Киберхоккей. ..."}]
    events:        [{id, parentId?, level, sportId, team1, team2, name, startTime, place}]
                   level=1 — матч, level=2 с name "1-й период" — период матча
    eventMiscs:    [{id, score1, score2, comment: "(1-0 0-1)", timerSeconds}]
    customFactors: [{e: eventId, factors: [{f, v, pt}]}]  f — id исхода, v — кэф, pt — линия
"""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..models import parse_participant, parse_periods

FEED_URL = "https://line01.bkfon-resources.com/events/list?lang=ru&version=0&scopeMarket=1600"

# пары (id «больше», id «меньше») для основного и доп. тоталов — сверить с фидом
TOTAL_FACTORS: list[tuple[int, int]] = [
    (930, 931), (1696, 1697), (1727, 1728), (1730, 1731), (1733, 1734), (1736, 1737),
]

SEGMENT_RE = re.compile(r"кибер\s*хоккей|киберхоккей|cyber\s*hockey|ice hockey.*cyber", re.I)
PERIOD_RE = re.compile(r"(\d)\s*-?\s*й\s+период|period\s*(\d)", re.I)


@dataclass
class LiveMatch:
    match_id: str
    league: str
    team1: str
    team2: str
    player1: str
    player2: str
    start_time: str
    periods: list[tuple[int, int]]      # счёт по периодам на текущий момент
    timer_seconds: int | None
    # {номер периода: [(линия, кэф больше, кэф меньше), ...]}
    period_totals: dict[int, list[tuple[float, float | None, float | None]]] = field(default_factory=dict)

    @property
    def current_period(self) -> int:
        return max(len(self.periods), 1)


def fetch_feed(url: str = FEED_URL, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _totals(factors: list[dict]) -> list[tuple[float, float | None, float | None]]:
    by_id = {f.get("f"): f for f in factors}
    out = []
    for over_id, under_id in TOTAL_FACTORS:
        over, under = by_id.get(over_id), by_id.get(under_id)
        src = over or under
        if not src or src.get("pt") in (None, ""):
            continue
        try:
            line = float(str(src["pt"]).replace("+", ""))
        except ValueError:
            continue
        out.append((line, over and over.get("v"), under and under.get("v")))
    return sorted(out)


def parse_feed(feed: dict) -> list[LiveMatch]:
    segments = {s["id"]: s.get("name", "") for s in feed.get("sports", [])
                if SEGMENT_RE.search(s.get("name", ""))}
    events = feed.get("events", [])
    miscs = {m["id"]: m for m in feed.get("eventMiscs", [])}
    factors = {c["e"]: c.get("factors", []) for c in feed.get("customFactors", [])}

    matches: dict[int, LiveMatch] = {}
    for e in events:
        if e.get("level", 1) != 1 or e.get("sportId") not in segments:
            continue
        team1, p1 = parse_participant(e.get("team1", ""))
        team2, p2 = parse_participant(e.get("team2", ""))
        misc = miscs.get(e["id"], {})
        start = e.get("startTime")
        matches[e["id"]] = LiveMatch(
            match_id=f"fonbet:{e['id']}", league=segments[e["sportId"]],
            team1=team1, team2=team2, player1=p1, player2=p2,
            start_time=(datetime.fromtimestamp(start, timezone.utc).isoformat(timespec="seconds")
                        if start else ""),
            periods=parse_periods(misc.get("comment", "")),
            timer_seconds=misc.get("timerSeconds"),
        )

    for e in events:
        parent = matches.get(e.get("parentId"))
        if not parent or e.get("level") != 2:
            continue
        m = PERIOD_RE.search(e.get("name", ""))
        if not m:
            continue
        period = int(m.group(1) or m.group(2))
        totals = _totals(factors.get(e["id"], []))
        if totals:
            parent.period_totals[period] = totals
    return list(matches.values())
