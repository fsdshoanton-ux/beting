"""Командная строка: python -m cyberhockey <команда> ..."""
from __future__ import annotations

import argparse
import os
import sys
import time

from . import backtest, notify
from .collectors import csv_import
from .model import SignalRules, estimate, format_estimate, is_signal
from .models import Match
from .stats import History, pair_table, player_table
from .storage import Storage


def _history(db: Storage, args) -> History:
    return History(db.load_matches(league=args.league), last_n=args.last_n)


def cmd_demo(args, db: Storage) -> None:
    matches = csv_import.generate_demo(args.n)
    csv_import.write_csv(args.out, matches)
    print(f"Записано {len(matches)} синтетических матчей в {args.out}")


def cmd_import(args, db: Storage) -> None:
    matches = csv_import.read_csv(args.file, source=args.source)
    for m in matches:
        db.upsert_match(m)
    print(f"Импортировано матчей: {len(matches)}")


def cmd_stats(args, db: Storage) -> None:
    h = _history(db, args)
    if not h.matches:
        sys.exit("База пуста — сначала import.")
    print(f"Матчей: {len(h.matches)}, игроков: {len(h.players())}")
    for p in range(1, h.max_period() + 1):
        s = h.league(p)
        print(f"  {p}-й период: в среднем {s.mean:.2f} гола, ТБ {args.line}: {s.over_rate(args.line):.1%}")
    period = args.period
    print(f"\nИгроки, {period}-й период, ТБ {args.line} (мин. {args.min_games} матчей):")
    print(f"{'игрок':<20}{'матчей':>8}{'ср.голов':>10}{'ТБ %':>8}")
    for r in player_table(h, period, args.line, args.min_games)[:args.top]:
        print(f"{r.player:<20}{r.games:>8}{r.mean:>10.2f}{r.over_rate:>8.1%}")


def cmd_pairs(args, db: Storage) -> None:
    h = _history(db, args)
    print(f"Пары, {args.period}-й период, ТБ {args.line} (мин. {args.min_games} встреч):")
    print(f"{'пара':<32}{'встреч':>8}{'ср.голов':>10}{'ТБ %':>8}")
    for _, _, r in pair_table(h, args.period, args.line, args.min_games)[:args.top]:
        print(f"{r.player:<32}{r.games:>8}{r.mean:>10.2f}{r.over_rate:>8.1%}")


def cmd_estimate(args, db: Storage) -> None:
    h = _history(db, args)
    goals = sum(int(x) for x in args.score.replace(":", "-").split("-")) if args.score else 0
    est = estimate(h, args.p1, args.p2, args.period, args.line,
                   elapsed=args.elapsed, current_goals=goals)
    print(format_estimate(est, args.odds))
    if args.odds:
        ok = is_signal(est, args.odds, _rules(args))
        print("  => СИГНАЛ: ТБ" if ok else "  => сигнала нет")


def _rules(args) -> SignalRules:
    return SignalRules(min_edge=args.min_edge, min_prob=args.min_prob,
                       min_games=args.min_games_signal)


def cmd_backtest(args, db: Storage) -> None:
    matches = db.load_matches(league=args.league)
    snapshot = {(r["match_id"], r["period_no"], r["line"]): r["over_odds"]
                for r in db.conn.execute(
                    "SELECT * FROM odds WHERE over_odds IS NOT NULL ORDER BY ts DESC")}
    # ORDER BY ts DESC + dict -> остаётся самый ранний снимок, т.е. кэф до начала периода

    def odds_for(m: Match):
        return snapshot.get((m.match_id, args.period, args.line), args.odds)

    note = f"реальные кэфы из базы, иначе {args.odds}" if snapshot else f"кэф {args.odds} для всех"
    r = backtest.run(matches, args.period, args.line, odds_for, _rules(args),
                     warmup=args.warmup, last_n=args.last_n)
    print(f"Бэктест: {args.period}-й период, ТБ {args.line}")
    print(backtest.format_result(r, note))


def cmd_watch(args, db: Storage) -> None:
    from .collectors import fonbet

    rules = _rules(args)
    seen: dict[str, fonbet.LiveMatch] = {}
    period_len = args.period_minutes * 60
    print(f"Слежу за лайвом, опрос раз в {args.interval} с. Ctrl+C — выход.")
    while True:
        try:
            live = fonbet.parse_feed(fonbet.fetch_feed(args.url))
        except Exception as exc:
            print(f"[fonbet] ошибка запроса: {exc}", flush=True)
            time.sleep(args.interval)
            continue

        h = _history(db, args)
        now_ids = set()
        for lm in live:
            if args.league and args.league.lower() not in lm.league.lower():
                continue
            now_ids.add(lm.match_id)
            seen[lm.match_id] = lm
            db.upsert_match(Match(lm.match_id, lm.league, lm.start_time, lm.player1, lm.player2,
                                  lm.periods, lm.team1, lm.team2, "fonbet"), finished=False)
            for period, totals in lm.period_totals.items():
                if period < lm.current_period:
                    continue
                elapsed, goals = 0.0, 0
                if period == lm.current_period and lm.periods:
                    goals = sum(lm.periods[period - 1])
                    if lm.timer_seconds is not None:
                        elapsed = (lm.timer_seconds - (period - 1) * period_len) / period_len
                for line, over, under in totals:
                    if not over:
                        continue
                    if elapsed <= 0:
                        db.save_odds(lm.match_id, period, line, over, under)
                    est = estimate(h, lm.player1, lm.player2, period, line,
                                   elapsed=max(elapsed, 0.0), current_goals=goals)
                    if is_signal(est, over, rules) and db.save_signal(
                            lm.match_id, lm.player1, lm.player2, period, line, over,
                            est.prob, est.edge(over)):
                        notify.send(f"СИГНАЛ ТБ | {lm.league}\n"
                                    f"счёт {lm.periods or '-'}\n" + format_estimate(est, over))

        # матч пропал из лайва — считаем завершённым с последним увиденным счётом
        for mid in list(seen):
            if mid not in now_ids:
                lm = seen.pop(mid)
                db.upsert_match(Match(mid, lm.league, lm.start_time, lm.player1, lm.player2,
                                      lm.periods, lm.team1, lm.team2, "fonbet"), finished=True)
        db.settle_signals()
        time.sleep(args.interval)


def cmd_report(args, db: Storage) -> None:
    db.settle_signals()
    rows = db.signals()
    done = [r for r in rows if r["result"]]
    wins = sum(r["result"] == "win" for r in done)
    profit = sum((r["odds"] - 1) if r["result"] == "win" else -1 for r in done)
    print(f"Сигналов: {len(rows)}, рассчитано: {len(done)}, выиграно: {wins}")
    if done:
        print(f"Прибыль при ставке 1 ед.: {profit:+.2f}, ROI {profit / len(done):+.1%}")
    for r in rows[-args.top:]:
        print(f"{r['ts']} {r['player1']} vs {r['player2']} П{r['period_no']} ТБ{r['line']} "
              f"@{r['odds']:.2f} p={r['prob']:.0%} -> {r['result'] or '...'}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="cyberhockey", description=__doc__)
    ap.add_argument("--db", default=os.environ.get("CYBERHOCKEY_DB", "data/cyberhockey.db"))
    ap.add_argument("--league", help="фильтр по названию лиги (подстрока)")
    ap.add_argument("--last-n", type=int, default=100, help="последних матчей игрока в выборке")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def signal_opts(p):
        p.add_argument("--min-edge", type=float, default=0.05)
        p.add_argument("--min-prob", type=float, default=0.55)
        p.add_argument("--min-games-signal", type=int, default=15)

    p = sub.add_parser("demo", help="сгенерировать синтетические матчи в CSV")
    p.add_argument("--n", type=int, default=3000)
    p.add_argument("--out", default="data/demo.csv")
    p.set_defaults(fn=cmd_demo)

    p = sub.add_parser("import", help="загрузить завершённые матчи из CSV")
    p.add_argument("file")
    p.add_argument("--source", default="csv")
    p.set_defaults(fn=cmd_import)

    for name, fn, mg in (("stats", cmd_stats, 10), ("pairs", cmd_pairs, 5)):
        p = sub.add_parser(name, help="таблица игроков" if name == "stats" else "таблица пар")
        p.add_argument("--period", type=int, default=1)
        p.add_argument("--line", type=float, default=1.5)
        p.add_argument("--min-games", type=int, default=mg)
        p.add_argument("--top", type=int, default=30)
        p.set_defaults(fn=fn)

    p = sub.add_parser("estimate", help="оценить ТБ в периоде для пары игроков")
    p.add_argument("p1")
    p.add_argument("p2")
    p.add_argument("--period", type=int, required=True)
    p.add_argument("--line", type=float, required=True)
    p.add_argument("--odds", type=float)
    p.add_argument("--elapsed", type=float, default=0.0, help="доля сыгранного времени периода 0..1")
    p.add_argument("--score", help="счёт в текущем периоде, напр. 1-0")
    signal_opts(p)
    p.set_defaults(fn=cmd_estimate)

    p = sub.add_parser("backtest", help="проверка модели на истории")
    p.add_argument("--period", type=int, default=1)
    p.add_argument("--line", type=float, default=1.5)
    p.add_argument("--odds", type=float, default=1.85, help="кэф, если в базе нет реальных")
    p.add_argument("--warmup", type=int, default=300)
    signal_opts(p)
    p.set_defaults(fn=cmd_backtest)

    p = sub.add_parser("watch", help="следить за лайвом Фонбета и слать сигналы")
    p.add_argument("--interval", type=int, default=15)
    p.add_argument("--period-minutes", type=float, default=5.0,
                   help="длина периода в лиге (минуты игрового таймера)")
    p.add_argument("--url", default=None)
    signal_opts(p)
    p.set_defaults(fn=cmd_watch)

    p = sub.add_parser("report", help="итоги выданных сигналов")
    p.add_argument("--top", type=int, default=20)
    p.set_defaults(fn=cmd_report)

    args = ap.parse_args(argv)
    if getattr(args, "url", "") is None:
        from .collectors.fonbet import FEED_URL
        args.url = FEED_URL
    os.makedirs(os.path.dirname(args.db) or ".", exist_ok=True)
    db = Storage(args.db)
    try:
        args.fn(args, db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
