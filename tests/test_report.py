"""Tests del módulo de reporte (sin alpaca-py, sin credenciales, sin red)."""
from __future__ import annotations

import json

from tradingbot import report


def test_main_runs_without_traceback_without_keys(capsys, monkeypatch, tmp_path):
    # Redirige los archivos locales a un directorio temporal vacío, para no
    # depender del estado real del repo ni de credenciales.
    monkeypatch.setattr(report, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(report, "EQUITY_HISTORY_FILE", tmp_path / "equity_history.csv")
    monkeypatch.setattr(report, "HALT_FILE", tmp_path / "HALT")

    report.main()

    out = capsys.readouterr().out
    assert "REPORTE DEL BOT" in out
    assert "Traceback" not in out


def test_report_state_shows_positions(capsys, tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps(
            {
                "peak_equity": 550.0,
                "positions": {
                    "SPY": {
                        "direction": "LONG",
                        "stop": 540.0,
                        "traded_symbol": "SH",
                        "signal_direction": "SHORT",
                    }
                },
            }
        )
    )
    monkeypatch.setattr(report, "STATE_FILE", state_file)

    report._report_state()

    out = capsys.readouterr().out
    assert "SH" in out
    assert "SHORT" in out


def test_report_equity_history_shows_pct_change(capsys, tmp_path, monkeypatch):
    history_file = tmp_path / "equity_history.csv"
    history_file.write_text("fecha,equity\n2026-01-01T00:00:00+00:00,500.00\n2026-01-02T00:00:00+00:00,510.00\n")
    monkeypatch.setattr(report, "EQUITY_HISTORY_FILE", history_file)

    report._report_equity_history()

    out = capsys.readouterr().out
    assert "510.00" in out
    assert "+2.00%" in out


def test_report_halt_shown_when_present(capsys, tmp_path, monkeypatch):
    halt_file = tmp_path / "HALT"
    halt_file.write_text("2026-01-01T00:00:00+00:00 - circuit breaker\n")
    monkeypatch.setattr(report, "HALT_FILE", halt_file)

    report._report_halt()

    out = capsys.readouterr().out
    assert "BOT DETENIDO" in out
    assert "circuit breaker" in out
