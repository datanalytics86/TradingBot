"""Tests de safeguards live y pérdida diaria."""
from __future__ import annotations

from tradingbot.config import RiskParams
from tradingbot.main import _confirm_live_mode, _format_daily_summary, _sync_day_start_equity
from tradingbot.risk import check_daily_loss
from tradingbot.config import Config, DataParams, Instrument, RiskParams as RP, StrategyParams


def make_cfg(mode: str = "paper") -> Config:
    return Config(
        mode=mode,
        initial_capital=500,
        universe=[Instrument(symbol="SPY")],
        strategy=StrategyParams(),
        risk=RP(max_daily_loss=0.03),
        data=DataParams(),
    )


def test_confirm_live_blocks_without_flag(monkeypatch):
    monkeypatch.delenv("TRADINGBOT_LIVE_CONFIRM", raising=False)
    monkeypatch.setattr("tradingbot.main.sys.argv", ["main.py"])
    assert _confirm_live_mode(make_cfg("live")) is False


def test_confirm_live_allows_with_env(monkeypatch):
    monkeypatch.setenv("TRADINGBOT_LIVE_CONFIRM", "YES")
    assert _confirm_live_mode(make_cfg("live")) is True


def test_confirm_live_allows_with_cli(monkeypatch):
    monkeypatch.delenv("TRADINGBOT_LIVE_CONFIRM", raising=False)
    monkeypatch.setattr("tradingbot.main.sys.argv", ["main.py", "--confirm-live"])
    assert _confirm_live_mode(make_cfg("live")) is True


def test_paper_mode_always_confirms():
    assert _confirm_live_mode(make_cfg("paper")) is True


def test_check_daily_loss_triggers():
    risk = RiskParams(max_daily_loss=0.03)
    assert check_daily_loss(960, 1000, risk) is True
    assert check_daily_loss(980, 1000, risk) is False


def test_sync_day_start_equity():
    state = {}
    v = _sync_day_start_equity(state, 1000.0)
    assert v == 1000.0
    assert "day_start_date" in state


def test_daily_summary_format():
    text = _format_daily_summary(
        make_cfg(), 520.0, 550.0,
        [{"symbol": "SPY", "signal": "FLAT", "action": "ninguna"}],
        [], "ok",
    )
    assert "RESUMEN DIARIO" in text
    assert "SPY" in text