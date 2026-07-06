"""Tests del generador de dashboard HTML."""
from __future__ import annotations

from pathlib import Path

from tradingbot.dashboard import generate_dashboard


def test_generate_dashboard_creates_html(tmp_path: Path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "config.yaml").write_text(
        """
mode: paper
initial_capital: 500
universe:
  - symbol: SPY
    short_proxy: SH
strategy: {}
risk: {}
data: {}
""".strip(),
        encoding="utf-8",
    )
    (root / "state.json").write_text(
        '{"peak_equity": 1000.0, "positions": {"SPY": {"traded_symbol": "SPY", "signal_direction": "LONG", "stop": 400.0, "direction": "LONG"}}}',
        encoding="utf-8",
    )
    (root / "last_run.json").write_text(
        '{"timestamp": "2026-01-01T00:00:00+00:00", "status": "ok", "equity": 1000.0, "errors": [], "summaries": [{"symbol": "SPY", "signal": "LONG", "action": "mantiene SPY"}]}',
        encoding="utf-8",
    )
    (root / "equity_history.csv").write_text(
        "fecha,equity\n2026-01-01T00:00:00+00:00,1000.00\n2026-01-02T00:00:00+00:00,1010.00\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("tradingbot.config.ROOT", root)
    monkeypatch.setattr("tradingbot.config.STATE_FILE", root / "state.json")
    monkeypatch.setattr("tradingbot.config.LAST_RUN_FILE", root / "last_run.json")
    monkeypatch.setattr("tradingbot.config.EQUITY_HISTORY_FILE", root / "equity_history.csv")
    monkeypatch.setattr("tradingbot.config.HALT_FILE", root / "HALT")
    monkeypatch.setattr("tradingbot.dashboard.ROOT", root)
    monkeypatch.setattr("tradingbot.dashboard.STATE_FILE", root / "state.json")
    monkeypatch.setattr("tradingbot.dashboard.LAST_RUN_FILE", root / "last_run.json")
    monkeypatch.setattr("tradingbot.dashboard.EQUITY_HISTORY_FILE", root / "equity_history.csv")
    monkeypatch.setattr("tradingbot.dashboard.HALT_FILE", root / "HALT")
    monkeypatch.setattr("tradingbot.dashboard._broker_snapshot", lambda _mode: None)

    out = generate_dashboard(root / "docs" / "index.html")
    html = out.read_text(encoding="utf-8")
    assert "TradingBot Dashboard" in html
    assert "SPY" in html
    assert "mantiene SPY" in html