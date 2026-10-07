"""Telegram-бот: присылает предстоящие матчи, подходящие под стратегию, и их итог.

Запуск:  python -m tennis bot --token <токен от @BotFather>
Потом напишите боту /start — он запомнит чат и начнёт присылать матчи.

Что делает в цикле (раз в ``--interval`` минут):
1. докачивает с Sofascore результаты за вчера и сегодня;
2. по уже отправленным матчам присылает итог: взял игрок 1-й сет или нет;
3. ищет матчи на ближайшие ``--ahead`` часов, где игрок подходит под правило,
   и присылает новые.
Между циклами отвечает на команды: /check, /stats, /criteria, /help.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import sofascore
from .models import TennisMatch, merge, read_csv, write_csv
from .strategy import Rules, Trigger, find_candidates


class Telegram:
    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}/"

    def call(self, method: str, params: dict, http_timeout: int = 40) -> dict:
        data = urllib.parse.urlencode(params).encode()
        with urllib.request.urlopen(self.base + method, data=data, timeout=http_timeout) as r:
            return json.load(r)

    def send(self, chat_id, text: str) -> None:
        self.call("sendMessage", {"chat_id": chat_id, "text": text,
                                  "disable_web_page_preview": "true"})

    def updates(self, offset: int, timeout: int) -> list[dict]:
        """Долгий опрос: ждёт новые сообщения до timeout секунд."""
        return self.call("getUpdates", {"offset": offset, "timeout": timeout},
                         http_timeout=timeout + 15).get("result", [])


def _side_score(m: TennisMatch, player: str) -> str:
    sets = m.sets if m.side(player) == 1 else [(b, a) for a, b in m.sets]
    return " ".join(f"{a}-{b}" for a, b in sets)


class Bot:
    def __init__(self, data_path: str, state_path: str, rules: Rules, tours: tuple[str, ...],
                 gender: str = "M", ahead_hours: float = 24, history_days: int = 60,
                 tz: str = "Europe/Moscow", fetch_day=None, log=print):
        self.data_path, self.state_path = data_path, state_path
        self.rules, self.tours, self.gender = rules, tours, gender
        self.ahead, self.history_days = ahead_hours, history_days
        self.tz = ZoneInfo(tz)
        self.log = log
        self.fetch_day = fetch_day or (lambda d: sofascore.fetch_day(d, tours))
        self.state = {"chat_id": None, "offset": 0, "sent": {}}
        if os.path.exists(state_path):
            with open(state_path, encoding="utf-8") as f:
                self.state.update(json.load(f))
        self.history: list[TennisMatch] = read_csv(data_path) if os.path.exists(data_path) else []

    # --- состояние --------------------------------------------------------

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=1)

    def local(self, iso: str) -> str:
        return datetime.fromisoformat(iso).astimezone(self.tz).strftime("%d.%m %H:%M")

    # --- данные -----------------------------------------------------------

    def refresh_history(self, today: date | None = None) -> None:
        """Первый запуск — история за history_days, дальше — только вчера и сегодня."""
        today = today or datetime.now(timezone.utc).date()
        first = not self.history
        start = today - timedelta(days=self.history_days if first else 1)
        if first:
            self.log(f"Загружаю историю за {self.history_days} дней, это несколько минут…")
        new, day = [], start
        while day <= today:
            try:
                new += sofascore.parse_events(self.fetch_day(day), self.tours)
            except Exception as e:
                self.log(f"{day}: ошибка загрузки — {e}")
            day += timedelta(days=1)
        self.history = merge(self.history, [m for m in new if self.gender in ("all", m.gender)])
        write_csv(self.data_path, self.history)

    def upcoming(self, now: datetime | None = None) -> list[TennisMatch]:
        now = now or datetime.now(timezone.utc)
        until = now + timedelta(hours=self.ahead)
        out = {}
        for d in sorted({now.date(), until.date()}):
            try:
                for m in sofascore.parse_events(self.fetch_day(d), self.tours, finished=False):
                    out[m.match_id] = m
            except Exception as e:
                self.log(f"{d}: не удалось загрузить расписание — {e}")
        return [m for m in out.values()
                if self.gender in ("all", m.gender) and m.start_time
                and now.isoformat() <= m.start_time <= until.isoformat()]

    # --- сообщения --------------------------------------------------------

    def format_signal(self, t: Trigger) -> str:
        m = t.match
        prev = "\n".join(f"  {self.local(x.start_time)} vs {x.opponent(t.player)}: {_side_score(x, t.player)}"
                         for x in t.previous)
        return (f"🎾 {m.tour} · {m.tournament}\n"
                f"🕒 {self.local(m.start_time)}\n"
                f"{m.player1} — {m.player2}\n"
                f"👉 Ставка: {t.player} выиграет 1-й сет\n"
                f"Последние матчи {t.player}:\n{prev}")

    def criteria(self) -> str:
        gender = {"M": "мужчины", "W": "женщины", "all": "мужчины и женщины"}[self.gender]
        return (f"Критерии: {self.rules.describe()}\n"
                f"Туры: {', '.join(self.tours)} ({gender}), без парных\n"
                f"Матчи на ближайшие {self.ahead:g} ч.")

    def stats(self) -> str:
        done = [s for s in self.state["sent"].values() if s.get("result") in ("win", "loss")]
        wins = sum(s["result"] == "win" for s in done)
        pending = sum(1 for s in self.state["sent"].values() if not s.get("result"))
        if not done:
            return f"Итогов пока нет. Ждут результата: {pending}."
        return (f"Итого: 1-й сет взят в {wins} из {len(done)} ({wins / len(done):.0%}).\n"
                f"Ждут результата: {pending}.")

    # --- шаги цикла -------------------------------------------------------

    def resolve(self, now: datetime | None = None) -> list[str]:
        """Итоги по отправленным матчам, которые уже сыграны."""
        now = now or datetime.now(timezone.utc)
        by_id = {m.match_id: m for m in self.history}
        out = []
        for key, s in self.state["sent"].items():
            if s.get("result"):
                continue
            m = by_id.get(s["match_id"])
            if m is None:
                # матч отменён/перенесён и не появился в результатах за 3 дня
                if now - datetime.fromisoformat(s["start_time"]) > timedelta(days=3):
                    s["result"] = "void"
                continue
            fs = m.first_set_winner()
            if fs is None:
                s["result"] = "void"
                out.append(f"⚪ {s['player']} — {m.opponent(s['player'])}: 1-й сет не доигран, ставка не в счёт")
                continue
            won = fs == m.side(s["player"])
            s["result"] = "win" if won else "loss"
            out.append(f"{'✅' if won else '❌'} {s['player']} {'взял' if won else 'проиграл'} 1-й сет "
                       f"vs {m.opponent(s['player'])} ({_side_score(m, s['player'])})")
        if out:
            out.append(self.stats())
        return out

    def new_signals(self, now: datetime | None = None) -> list[str]:
        out = []
        for t in find_candidates(self.history, self.upcoming(now), self.rules):
            key = f"{t.match.match_id}:{t.player}"
            if key in self.state["sent"]:
                continue
            self.state["sent"][key] = {"match_id": t.match.match_id, "player": t.player,
                                       "start_time": t.match.start_time, "result": None}
            out.append(self.format_signal(t))
        return out

    def cycle(self, now: datetime | None = None) -> list[str]:
        self.refresh_history()
        msgs = self.resolve(now) + self.new_signals(now)
        self.save()
        return msgs

    def handle(self, text: str, chat_id) -> list[str]:
        cmd = (text or "").split()[0].split("@")[0].lower() if text else ""
        if cmd == "/start":
            self.state["chat_id"] = chat_id
            self.save()
            return ["Привет! Буду присылать матчи под стратегию.\n" + self.criteria()
                    + "\n\nКоманды: /check — проверить сейчас, /stats — итоги, /criteria — критерии"]
        if cmd == "/check":
            msgs = self.cycle()
            return msgs or ["Новых подходящих матчей нет."]
        if cmd == "/stats":
            return [self.stats()]
        if cmd == "/criteria":
            return [self.criteria()]
        return ["Команды: /check — проверить сейчас, /stats — итоги, /criteria — критерии"]


def run(bot: Bot, tg: Telegram, interval_min: float) -> None:
    """Основной цикл: команды из Telegram + проверка раз в interval_min минут."""
    next_check = 0.0
    while True:
        try:
            for upd in tg.updates(bot.state["offset"], timeout=25):
                bot.state["offset"] = upd["update_id"] + 1
                msg = upd.get("message") or {}
                chat = msg.get("chat", {}).get("id")
                if chat is None:
                    continue
                if bot.state["chat_id"] not in (None, chat):
                    continue  # бот отвечает только своему владельцу
                for text in bot.handle(msg.get("text", ""), chat):
                    tg.send(chat, text)
                bot.save()
        except Exception as e:
            bot.log(f"[telegram] {e}")
            time.sleep(5)
        if time.time() >= next_check:
            next_check = time.time() + interval_min * 60
            try:
                msgs = bot.cycle()
            except Exception as e:
                bot.log(f"[проверка] {e}")
                continue
            for text in msgs:
                bot.log(text)
                if bot.state["chat_id"]:
                    try:
                        tg.send(bot.state["chat_id"], text)
                    except Exception as e:
                        bot.log(f"[telegram] не отправлено: {e}")
            if not bot.state["chat_id"]:
                bot.log("Напишите боту /start в Telegram, чтобы получать матчи.")
