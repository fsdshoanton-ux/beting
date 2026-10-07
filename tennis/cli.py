"""Командная строка: python -m tennis <команда> ..."""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from . import demo, sofascore
from .models import merge, read_csv, write_csv
from .strategy import Rules, find_candidates, find_triggers, format_table, format_trigger, summarize


def _load(path: str):
    if not os.path.exists(path):
        sys.exit(f"Нет файла {path} — сначала collect (или demo).")
    return read_csv(path)


def cmd_collect(args) -> None:
    end = date.fromisoformat(args.until) if args.until else datetime.now(timezone.utc).date() - timedelta(days=1)
    start = end - timedelta(days=args.days + args.history_days - 1)
    tours = tuple(t.strip() for t in args.tours.split(",")) if args.tours else ()
    print(f"Sofascore: {start} … {end}, туры: {', '.join(tours) or 'все'}")
    new = sofascore.collect(start, end, tours, pause=args.pause)
    old = read_csv(args.data) if os.path.exists(args.data) else []
    allm = merge(old, new)
    write_csv(args.data, allm)
    print(f"Загружено {len(new)}, всего в {args.data}: {len(allm)}")


def cmd_demo(args) -> None:
    ms = demo.generate(days=args.days)
    write_csv(args.data, ms)
    print(f"Записано {len(ms)} СИНТЕТИЧЕСКИХ матчей в {args.data}")


def _window(args, matches) -> tuple[str, str]:
    if args.until:
        end = datetime.fromisoformat(args.until).replace(tzinfo=timezone.utc)
    else:  # конец последнего дня в данных
        last = max(m.start_time for m in matches)
        end = datetime.fromisoformat(last[:10]).replace(tzinfo=timezone.utc) + timedelta(days=1)
    start = end - timedelta(days=args.days)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


def _filter(matches, args):
    tours = {t.strip() for t in args.tours.split(",")} if args.tours else None
    return [m for m in matches
            if (args.gender in ("all", m.gender)) and (tours is None or m.tour in tours)]


def cmd_analyze(args) -> None:
    matches = _filter(_load(args.data), args)
    if not matches:
        sys.exit("После фильтров матчей нет.")
    since, until = _window(args, matches)
    in_win = [m for m in matches if since <= m.start_time < until]
    rules = Rules(streak=args.streak, straight_sets=not args.any_score, max_gap_days=args.max_gap)
    print(f"Окно анализа: {since[:10]} … {until[:10]} (история для серий — с {min(m.start_time for m in matches)[:10]})")
    by_g = defaultdict(int)
    for m in in_win:
        by_g[m.gender] += 1
    print(f"Матчей в окне: {len(in_win)} (мужчины {by_g['M']}, женщины {by_g['W']})\n")

    triggers = find_triggers(matches, rules, since, until)
    rows = [summarize(f"СТРАТЕГИЯ: {rules.describe()}", triggers)]
    for g, name in (("M", "  мужчины"), ("W", "  женщины")):
        if args.gender == "all":
            rows.append(summarize(name, [t for t in triggers if t.match.gender == g]))
    tours = sorted({t.match.tour for t in triggers})
    if len(tours) > 1:
        for tour in tours:
            rows.append(summarize(f"  {tour}", [t for t in triggers if t.match.tour == tour]))
    if args.days > 31:  # держится ли результат во времени
        for month in sorted({t.match.start_time[:7] for t in triggers}):
            rows.append(summarize(f"  {month}", [t for t in triggers if t.match.start_time[:7] == month]))
    if not args.no_compare:  # контрольные группы — с чем сравнивать
        for r in (Rules(args.streak, "loss", False, args.max_gap),
                  Rules(args.streak, "win", True, args.max_gap),
                  Rules(args.streak, "win", False, args.max_gap)):
            rows.append(summarize(f"сравн.: {r.describe()}", find_triggers(matches, r, since, until)))
    print(format_table(rows))
    print("\nдоля — как часто игрок взял 1-й сет; p — вероятность получить такое отклонение от 50%"
          "\nслучайно (p < 0.05 — уже не похоже на случайность); безуб.кэф — минимальный кэф"
          "\nна «победа в 1-м сете», при котором стратегия в ноль; матч — доля выигранных матчей.")
    if args.list:
        print()
        for t in triggers:
            print(format_trigger(t))
    if args.out:
        import csv
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["start_time", "gender", "tour", "tournament", "player", "opponent",
                        "score", "first_set_won", "match_won", "prev1", "prev2"])
            for t in triggers:
                m = t.match
                w.writerow([m.start_time, m.gender, m.tour, m.tournament, t.player,
                            m.opponent(t.player), " ".join(f"{a}-{b}" for a, b in m.sets),
                            t.first_set_won, t.match_won,
                            *[f"{x.start_time[:10]} vs {x.opponent(t.player)}" for x in t.previous]])
        print(f"\nСрабатывания записаны в {args.out}")


def cmd_candidates(args) -> None:
    """Предстоящие матчи, где игрок подходит под стратегию."""
    history = _filter(_load(args.data), args)
    rules = Rules(streak=args.streak, straight_sets=not args.any_score, max_gap_days=args.max_gap)
    today = datetime.now(timezone.utc).date()
    tours = tuple(t.strip() for t in args.tours.split(",")) if args.tours else ()
    upcoming = []
    for d in (today, today + timedelta(days=1)):
        try:
            upcoming += sofascore.parse_events(sofascore.fetch_day(d, tours), tours, finished=False)
        except Exception as e:
            print(f"{d}: не удалось загрузить расписание — {e}")
    upcoming = [m for m in {m.match_id: m for m in upcoming}.values()
                if args.gender in ("all", m.gender)]
    found = find_candidates(history, upcoming, rules)
    for t in found:
        m = t.match
        print(f"{m.start_time[:16].replace('T', ' ')} UTC [{m.gender} {m.tour}] "
              f"{m.tournament}: {t.player} — {m.opponent(t.player)}  (ставка: {t.player} выиграет 1-й сет)")
    if not found:
        print("Подходящих предстоящих матчей не найдено.")


def cmd_bot(args) -> None:
    from .bot import Bot, Telegram, run
    token = args.token or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("Нужен токен бота: --token <токен> или переменная TELEGRAM_BOT_TOKEN "
                 "(токен выдаёт @BotFather в Telegram).")
    tours = tuple(t.strip() for t in args.tours.split(",") if t.strip())
    rules = Rules(streak=args.streak, straight_sets=not args.any_score, max_gap_days=args.max_gap)
    bot = Bot(args.history_file, args.state, rules, tours, gender=args.gender, ahead_hours=args.ahead,
              history_days=args.history_days, tz=args.tz)
    print(bot.criteria())
    print("Бот запущен. Напишите ему /start в Telegram. Остановить — Ctrl+C.")
    try:
        run(bot, Telegram(token), args.interval)
    except KeyboardInterrupt:
        bot.save()
        print("Остановлен.")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="tennis", description=__doc__)
    ap.add_argument("--data", default="data/tennis.csv", help="CSV с матчами")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def filters(p):
        p.add_argument("--gender", choices=["all", "M", "W"], default="all", help="M — мужчины, W — женщины")
        p.add_argument("--tours", default="", help="через запятую: ATP,WTA,WTA 125,Challenger,ITF Men,ITF Women")
        p.add_argument("--streak", type=int, default=2, help="сколько поражений подряд")
        p.add_argument("--any-score", action="store_true", help="поражения с любым счётом, не только 0-2")

    p = sub.add_parser("collect", help="скачать результаты с Sofascore")
    p.add_argument("--days", type=int, default=7, help="дней для анализа")
    p.add_argument("--history-days", type=int, default=21,
                   help="доп. дней до окна, чтобы знать прошлые матчи игроков")
    p.add_argument("--until", help="последний день YYYY-MM-DD (по умолчанию вчера)")
    p.add_argument("--tours", default=",".join(sofascore.DEFAULT_TOURS))
    p.add_argument("--pause", type=float, default=0.5)
    p.set_defaults(fn=cmd_collect)

    p = sub.add_parser("demo", help="синтетические данные для проверки")
    p.add_argument("--days", type=int, default=28)
    p.set_defaults(fn=cmd_demo)

    p = sub.add_parser("analyze", help="проверить стратегию на собранных матчах")
    filters(p)
    p.add_argument("--days", type=int, default=7, help="окно анализа, дней")
    p.add_argument("--until", help="конец окна YYYY-MM-DD (не включая)")
    p.add_argument("--max-gap", type=float, help="макс. дней между серией и целевым матчем")
    p.add_argument("--list", action="store_true", help="показать каждое срабатывание")
    p.add_argument("--no-compare", action="store_true", help="без контрольных групп")
    p.add_argument("--out", help="сохранить срабатывания в CSV")
    p.set_defaults(fn=cmd_analyze)

    p = sub.add_parser("candidates", help="предстоящие матчи под стратегию (Sofascore)")
    filters(p)
    p.add_argument("--max-gap", type=float, help="макс. дней между серией и матчем")
    p.set_defaults(fn=cmd_candidates)

    p = sub.add_parser("bot", help="Telegram-бот: присылает подходящие матчи")
    p.add_argument("--token", help="токен от @BotFather (или TELEGRAM_BOT_TOKEN)")
    p.add_argument("--gender", choices=["all", "M", "W"], default="M")
    p.add_argument("--tours", default="ATP,Challenger",
                   help="через запятую: ATP,WTA,WTA 125,Challenger,ITF Men,ITF Women")
    p.add_argument("--streak", type=int, default=2, help="сколько поражений подряд")
    p.add_argument("--any-score", action="store_true", help="поражения с любым счётом, не только 0-2")
    p.add_argument("--max-gap", type=float, help="макс. дней между серией и матчем")
    p.add_argument("--ahead", type=float, default=24, help="на сколько часов вперёд искать матчи")
    p.add_argument("--interval", type=float, default=30, help="проверка раз в N минут")
    p.add_argument("--history-days", type=int, default=60, help="история при первом запуске, дней")
    p.add_argument("--tz", default="Europe/Moscow", help="часовой пояс для времени матчей")
    p.add_argument("--state", default="data/bot_state.json")
    p.add_argument("--history-file", default="data/bot_history.csv", help="куда бот сохраняет матчи")
    p.set_defaults(fn=cmd_bot)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
