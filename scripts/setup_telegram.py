"""Configura Telegram: actualiza .env y sube secrets a GitHub Actions."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"


def _fetch_chat_id(token: str) -> str | None:
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None

    for item in reversed(data.get("result", [])):
        message = item.get("message") or item.get("edited_message")
        if not message:
            continue
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        if chat_id is not None:
            return str(chat_id)
    return None


def _update_env(token: str, chat_id: str) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    out: list[str] = []
    seen_token = seen_chat = False
    for line in lines:
        if line.startswith("TELEGRAM_BOT_TOKEN="):
            out.append(f"TELEGRAM_BOT_TOKEN={token}")
            seen_token = True
        elif line.startswith("TELEGRAM_CHAT_ID="):
            out.append(f"TELEGRAM_CHAT_ID={chat_id}")
            seen_chat = True
        else:
            out.append(line)
    if not seen_token:
        out.append(f"TELEGRAM_BOT_TOKEN={token}")
    if not seen_chat:
        out.append(f"TELEGRAM_CHAT_ID={chat_id}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


def _test_message(token: str, chat_id: str) -> bool:
    payload = json.dumps(
        {"chat_id": chat_id, "text": "TradingBot: Telegram configurado correctamente."}
    ).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return False


def main() -> int:
    if len(sys.argv) < 2:
        print("Uso: python scripts/setup_telegram.py <TELEGRAM_BOT_TOKEN> [TELEGRAM_CHAT_ID]")
        return 1

    token = sys.argv[1].strip()
    if not re.match(r"^\d+:[A-Za-z0-9_-]+$", token):
        print("Token inválido. Debe verse como 123456789:ABCdef...")
        return 1

    chat_id = sys.argv[2].strip() if len(sys.argv) > 2 else ""
    if not chat_id:
        print("Buscando chat_id en getUpdates (envía /start a tu bot primero)...")
        chat_id = _fetch_chat_id(token) or ""
    if not chat_id:
        print("No se encontró chat_id. Envía un mensaje a tu bot y vuelve a ejecutar.")
        return 1

    _update_env(token, chat_id)
    print(f".env actualizado (chat_id={chat_id})")

    if not _test_message(token, chat_id):
        print("AVISO: no se pudo enviar mensaje de prueba. Revisa token/chat_id.")
    else:
        print("Mensaje de prueba enviado a Telegram.")

    print("Subiendo secrets a GitHub Actions...")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "github_actions_setup.py")],
        cwd=ROOT,
    )
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())