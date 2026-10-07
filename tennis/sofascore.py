"""Сбор результатов тенниса с публичного JSON API Sofascore.

Общий адрес ``sport/tennis/scheduled-events/{день}`` отвечает 404 (проверено
в октябре 2026), поэтому матчи берутся по категориям:
``category/{id}/scheduled-events/{день}``. Ответ за день захватывает и
соседние дни (часовой пояс), дубли убираются по id матча.

Ожидаемая структура (один элемент ``events``):
    id, startTimestamp, winnerCode (1/2),
    status: {type: "finished" | "notstarted" | "inprogress", description: "Ended" | "Retired" | "Walkover"},
    homeTeam/awayTeam: {name, gender: "M" | "F", subTeams: [...] — только у пар},
    homeScore/awayScore: {period1: 6, period2: 4, ...},
    tournament: {name, category: {name: "ATP" | "WTA" | "Challenger" | "ITF Men" | "ITF Women"}}
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

from .models import TennisMatch

DAY_URL = "https://api.sofascore.com/api/v1/category/{category}/scheduled-events/{day}"
# id категорий Sofascore: /api/v1/sport/tennis/categories
CATEGORIES = {"ATP": 3, "WTA": 6, "Challenger": 72, "WTA 125": 871,
              "ITF Men": 785, "ITF Women": 213}
DEFAULT_TOURS = tuple(CATEGORIES)
WOMEN_CATEGORIES = ("WTA", "ITF Women", "WTA 125")


def fetch_category_day(category_id: int, day: date, timeout: int = 20) -> dict:
    url = DAY_URL.format(category=category_id, day=day.isoformat())
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fetch_day(day: date, tours=DEFAULT_TOURS, pause: float = 0.5) -> dict:
    """События всех нужных категорий за день, склеенные в один ответ."""
    events, seen = [], set()
    for tour in tours or DEFAULT_TOURS:
        if tour not in CATEGORIES:
            raise ValueError(f"неизвестный тур {tour!r}, есть: {', '.join(CATEGORIES)}")
        for ev in fetch_category_day(CATEGORIES[tour], day).get("events", []):
            if ev.get("id") not in seen:
                seen.add(ev.get("id"))
                events.append(ev)
        time.sleep(pause)
    return {"events": events}


def _is_doubles(ev: dict) -> bool:
    home, away = ev.get("homeTeam", {}), ev.get("awayTeam", {})
    name = ev.get("tournament", {}).get("name", "")
    return bool(home.get("subTeams") or away.get("subTeams")
                or "/" in home.get("name", "") or "doubles" in name.lower())


def _gender(ev: dict, category: str) -> str:
    if category in WOMEN_CATEGORIES:
        return "W"
    if category in ("ATP", "Challenger", "ITF Men"):
        return "M"
    return "W" if ev.get("homeTeam", {}).get("gender") == "F" else "M"


def _sets(ev: dict) -> list[tuple[int, int]]:
    hs, as_ = ev.get("homeScore") or {}, ev.get("awayScore") or {}
    out = []
    for i in range(1, 6):
        a, b = hs.get(f"period{i}"), as_.get(f"period{i}")
        if a is None or b is None:
            break
        out.append((int(a), int(b)))
    return out


def _status(ev: dict) -> str:
    desc = (ev.get("status", {}).get("description") or "").lower()
    if "walkover" in desc:
        return "walkover"
    if "retired" in desc or "abandon" in desc or "default" in desc:
        return "retired"
    return "finished"


def parse_events(data: dict, tours=DEFAULT_TOURS, finished: bool = True) -> list[TennisMatch]:
    """Одиночные матчи из ответа scheduled-events.

    finished=True — только завершённые (с победителем),
    finished=False — только ещё не начавшиеся (для поиска кандидатов).
    """
    out = []
    for ev in data.get("events", []):
        state = ev.get("status", {}).get("type")
        if finished and (state != "finished" or ev.get("winnerCode") not in (1, 2)):
            continue
        if not finished and state != "notstarted":
            continue
        category = ev.get("tournament", {}).get("category", {}).get("name", "")
        if tours and category not in tours:
            continue
        if _is_doubles(ev):
            continue
        ts = ev.get("startTimestamp")
        out.append(TennisMatch(
            match_id=f"ss{ev['id']}",
            start_time=datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else "",
            gender=_gender(ev, category), tour=category,
            tournament=ev.get("tournament", {}).get("name", ""),
            player1=ev.get("homeTeam", {}).get("name", ""),
            player2=ev.get("awayTeam", {}).get("name", ""),
            sets=_sets(ev), winner=ev.get("winnerCode") if finished else 0,
            status=_status(ev) if finished else "scheduled", source="sofascore"))
    return out


def collect(start: date, end: date, tours=DEFAULT_TOURS, pause: float = 1.0,
            log=print) -> list[TennisMatch]:
    """Завершённые одиночные матчи за дни [start, end] включительно."""
    out, day = [], start
    while day <= end:
        try:
            ms = parse_events(fetch_day(day, tours, pause), tours)
            log(f"{day}: {len(ms)} матчей")
            out.extend(ms)
        except Exception as e:  # сеть/блокировка — пропускаем день, но сообщаем
            log(f"{day}: ошибка загрузки — {e}")
        day += timedelta(days=1)
    return out
