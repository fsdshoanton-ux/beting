"""Отправка сигналов в Telegram (или в консоль, если бот не настроен)."""
from __future__ import annotations

import os
import urllib.parse
import urllib.request


def send(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    print(text, flush=True)
    if not (token and chat_id):
        return
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage",
                               data=data, timeout=10).read()
    except Exception as exc:  # сигнал уже выведен в консоль, не роняем цикл
        print(f"[telegram] не отправлено: {exc}", flush=True)
