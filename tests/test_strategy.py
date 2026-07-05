"""Tests de indicadores y señal, con datos 100% sintéticos (sin red)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradingbot.config import StrategyParams
from tradingbot.strategy import compute_indicators, signal, trailing_stop

PARAMS = StrategyParams(
    ema_fast=20, ema_slow=50, trend_filter=200, atr_period=14, atr_stop_mult=2.5,
    adx_period=14, min_adx=15,
)


def make_trend_df(n: int, slope: float, start_price: float = 100.0, noise: float = 0.0, seed: int = 0) -> pd.DataFrame:
    """Genera un DataFrame OHLCV sintético con una tendencia lineal + ruido pequeño."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    base = start_price + slope * np.arange(n)
    if noise:
        base = base + rng.normal(0, noise, size=n)
    close = pd.Series(base, index=idx)
    high = close + abs(noise) + 0.3
    low = close - abs(noise) - 0.3
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(1_000_000, index=idx)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


def test_indicators_have_expected_columns():
    df = make_trend_df(60, slope=0.1)
    out = compute_indicators(df, PARAMS)
    for col in ("ema_fast", "ema_slow", "sma_trend", "atr", "adx", "atr_avg"):
        assert col in out.columns


def test_signal_flat_when_adx_below_threshold():
    df = make_trend_df(300, slope=0.5, noise=0.2)
    ind = compute_indicators(df, PARAMS)
    ind.iloc[-1, ind.columns.get_loc("adx")] = 10.0
    assert signal(ind, PARAMS) == "FLAT"


def test_signal_long_in_sustained_uptrend():
    df = make_trend_df(300, slope=0.5, noise=0.2)
    ind = compute_indicators(df, PARAMS)
    assert signal(ind, PARAMS) == "LONG"


def test_signal_short_in_sustained_downtrend():
    df = make_trend_df(300, slope=-0.5, start_price=300.0, noise=0.2)
    ind = compute_indicators(df, PARAMS)
    assert signal(ind, PARAMS) == "SHORT"


def test_signal_flat_when_insufficient_data():
    # Menos barras que trend_filter (200) -> sma_trend es NaN en la última barra.
    df = make_trend_df(50, slope=0.5)
    ind = compute_indicators(df, PARAMS)
    assert signal(ind, PARAMS) == "FLAT"


def test_signal_flat_in_sideways_market():
    n = 300
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    rng = np.random.default_rng(1)
    close = pd.Series(100 + rng.normal(0, 0.5, size=n), index=idx)
    high = close + 0.5
    low = close - 0.5
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(1_000_000, index=idx)
    df = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})
    ind = compute_indicators(df, PARAMS)
    assert signal(ind, PARAMS) == "FLAT"


def test_trailing_stop_only_rises_for_long():
    stop = None
    closes = [100, 102, 101, 105, 103, 108]
    atr = 2.0
    stops = []
    for c in closes:
        stop = trailing_stop(c, atr, stop, "LONG", mult=2.5)
        stops.append(stop)
    for prev, nxt in zip(stops, stops[1:]):
        assert nxt >= prev


def test_trailing_stop_only_falls_for_short():
    stop = None
    closes = [100, 98, 99, 95, 97, 92]
    atr = 2.0
    stops = []
    for c in closes:
        stop = trailing_stop(c, atr, stop, "SHORT", mult=2.5)
        stops.append(stop)
    for prev, nxt in zip(stops, stops[1:]):
        assert nxt <= prev


def test_trailing_stop_invalid_direction_raises():
    with pytest.raises(ValueError):
        trailing_stop(100, 2.0, None, "SIDEWAYS", 2.5)
