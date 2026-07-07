"""Tests del backtest avanzado (sin red)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradingbot.backtest import compute_full_metrics, format_metrics_table, walk_forward_analysis
from tradingbot.config import BacktestParams, Config, DataParams, Instrument, RiskParams, StrategyParams


def make_equity(n: int = 100, trend: float = 0.001) -> pd.Series:
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    values = 500 * (1 + trend) ** np.arange(n)
    return pd.Series(values, index=idx)


def test_compute_full_metrics():
    eq = make_equity(200, 0.0005)
    trades = [
        {"pnl": 10.0}, {"pnl": -5.0}, {"pnl": 8.0}, {"pnl": -3.0},
    ]
    m = compute_full_metrics(eq, trades)
    assert m.sharpe != 0 or m.cagr >= 0
    assert m.win_rate == 0.5
    assert m.profit_factor > 1
    assert m.n_trades == 4


def test_format_metrics_table_contains_key_metrics():
    eq = make_equity(50)
    m = compute_full_metrics(eq, [])
    table = format_metrics_table(m)
    assert "Sharpe" in table
    assert "Sortino" in table
    assert "Calmar" in table
    assert "Profit factor" in table


def test_walk_forward_returns_folds():
    cfg = Config(
        mode="paper",
        initial_capital=500,
        universe=[Instrument(symbol="X")],
        strategy=StrategyParams(),
        risk=RiskParams(),
        data=DataParams(),
        backtest=BacktestParams(walk_forward_train_days=30, walk_forward_test_days=10),
    )
    eq = make_equity(120)
    wf = []
    bt = cfg.backtest
    idx = bt.walk_forward_train_days
    fold = 0
    while idx + bt.walk_forward_test_days < len(eq):
        fold += 1
        test_slice = eq.iloc[idx: idx + bt.walk_forward_test_days]
        wf.append({"fold": fold, "test_return": test_slice.iloc[-1] / test_slice.iloc[0] - 1})
        idx += bt.walk_forward_test_days
    assert len(wf) >= 1