"""Tests del motor de backtest, con datos sintéticos (sin red)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradingbot import backtest
from tradingbot.config import Config, DataParams, Instrument, RiskParams, StrategyParams


def make_trend_df(n: int, slope: float, start_price: float = 100.0, noise: float = 0.3, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2019-01-01", periods=n, freq="B")
    base = start_price + slope * np.arange(n) + rng.normal(0, noise, size=n)
    close = pd.Series(base, index=idx)
    high = close + abs(noise) + 0.3
    low = close - abs(noise) - 0.3
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(1_000_000, index=idx)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


def make_cfg(symbols=("SPYX", "QQQX")) -> Config:
    return Config(
        mode="paper",
        initial_capital=500.0,
        universe=[Instrument(symbol=s, short_proxy=None) for s in symbols],
        strategy=StrategyParams(
            ema_fast=20, ema_slow=50, trend_filter=200, atr_period=14, atr_stop_mult=2.5,
            adx_period=14, min_adx=15,
        ),
        risk=RiskParams(risk_per_trade=0.02, max_position_pct=0.45, max_total_exposure_pct=0.70, max_drawdown=0.15),
        data=DataParams(lookback_days=400),
    )


@pytest.fixture
def uptrend_data():
    return make_trend_df(320, slope=0.6, start_price=100.0, noise=0.3)


def test_run_backtest_no_nans(monkeypatch, uptrend_data):
    cfg = make_cfg()
    monkeypatch.setattr(backtest, "fetch_daily", lambda symbol, start=None, days=None, validate_freshness=True: uptrend_data.copy())

    equity_curve, trades = backtest.run_backtest(cfg, start="2019-01-01")

    assert not equity_curve.isna().any()
    assert isinstance(trades, list)


def test_equity_curve_starts_at_initial_capital(monkeypatch, uptrend_data):
    cfg = make_cfg()
    monkeypatch.setattr(backtest, "fetch_daily", lambda symbol, start=None, days=None, validate_freshness=True: uptrend_data.copy())

    equity_curve, _ = backtest.run_backtest(cfg, start="2019-01-01")

    assert equity_curve.iloc[0] == pytest.approx(cfg.initial_capital, rel=1e-6)


def test_strong_uptrend_yields_positive_return(monkeypatch, uptrend_data):
    cfg = make_cfg()
    monkeypatch.setattr(backtest, "fetch_daily", lambda symbol, start=None, days=None, validate_freshness=True: uptrend_data.copy())

    equity_curve, trades = backtest.run_backtest(cfg, start="2019-01-01")

    total_return = equity_curve.iloc[-1] / equity_curve.iloc[0] - 1
    assert total_return > 0
