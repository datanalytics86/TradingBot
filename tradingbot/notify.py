"""Notificaciones opcionales por Telegram.

Para activarlas, crea un bot con @BotFather (ver README) y define en el
entorno `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`. Si no están definidas,
todas las llamadas son no-op silencioso (no rompen el ciclo del bot).

Usa exclusivamente la librería estándar (`urllib`) para no agregar una
dependencia nueva solo por esto.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger("tradingbot")

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram(text: str, timeout: float = 10.0) -> bool:
    """Envía `text` al chat de Telegram configurado.

    Devuelve True si el envío fue exitoso, False en cualquier otro caso
    (variables de entorno ausentes, error de red, error de la API de
    Telegram). Nunca lanza una excepción: cualquier fallo se atrapa y se
    registra como warning, para que un problema de notificaciones jamás
    rompa el ciclo del bot.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False

    url = TELEGRAM_API_URL.format(token=token)
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            return 200 <= status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        log.warning("No se pudo enviar notificación de Telegram: %s", exc)
        return False
