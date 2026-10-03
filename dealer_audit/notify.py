"""Короткая сводка в Telegram после прогона (опционально)."""
import os
import httpx


def telegram(text: str) -> None:
    try:
        httpx.post(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage", timeout=20,
                   json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text[:4000], "parse_mode": "Markdown", "disable_web_page_preview": True})
    except Exception as e:  # noqa: BLE001
        print("Telegram: не отправлено:", e)
