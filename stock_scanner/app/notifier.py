"""Telegram notifications."""
import requests

API = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram(token, chat_id, text):
    """Send a message via the Telegram Bot API. Returns (ok, detail)."""
    if not token or not chat_id:
        return False, "telegram not configured (missing token or chat id)"
    try:
        resp = requests.post(
            API.format(token=token),
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        if resp.status_code == 200:
            return True, "sent"
        return False, f"telegram error {resp.status_code}: {resp.text[:200]}"
    except requests.RequestException as exc:
        return False, f"request failed: {exc}"
