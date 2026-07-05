"""Tests de notificaciones Telegram (sin red real)."""
from __future__ import annotations

import urllib.error

from tradingbot import notify


def test_send_telegram_noop_without_env_vars(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    assert notify.send_telegram("hola") is False


def test_send_telegram_noop_with_partial_env_vars(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    assert notify.send_telegram("hola") is False


def test_send_telegram_success(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    def fake_urlopen(request, timeout=10):
        assert "api.telegram.org" in request.full_url
        return FakeResponse()

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)

    assert notify.send_telegram("hola") is True


def test_send_telegram_network_failure_returns_false_and_does_not_raise(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")

    def fake_urlopen(request, timeout=10):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)

    assert notify.send_telegram("hola") is False
