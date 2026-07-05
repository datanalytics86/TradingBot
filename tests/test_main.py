"""Tests del ciclo diario (`main.py`): sizing del lado short, transiciones de
estado y reconciliación estado<->broker. Usa un broker fake (sin alpaca-py)
y datos sintéticos (sin red)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from tradingbot import main
from tradingbot.config import Config, DataParams, Instrument, RiskParams, StrategyParams
from tradingbot.risk import position_size
from tradingbot.strategy import compute_indicators, trailing_stop

STRATEGY = StrategyParams(
    ema_fast=20, ema_slow=50, trend_filter=200, atr_period=14, atr_stop_mult=2.5,
    adx_period=14, min_adx=15,
)
RISK = RiskParams(risk_per_trade=0.02, max_position_pct=0.45, max_total_exposure_pct=0.70, max_drawdown=0.15)


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


def make_flat_df(n: int = 300, start_price: float = 100.0, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    close = pd.Series(start_price + rng.normal(0, 0.5, size=n), index=idx)
    high = close + 0.5
    low = close - 0.5
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(1_000_000, index=idx)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


def make_cfg(inst: Instrument) -> Config:
    return Config(
        mode="paper",
        initial_capital=500.0,
        universe=[inst],
        strategy=STRATEGY,
        risk=RISK,
        data=DataParams(lookback_days=400),
    )


class FakeBroker:
    """Broker fake en memoria para tests: sin alpaca-py, sin red."""

    def __init__(self, equity: float = 500.0):
        self.equity = equity
        self.positions: dict[str, float] = {}
        self.orders: list[tuple[str, float, str]] = []
        self.closed: list[str] = []

    def get_equity(self) -> float:
        return self.equity

    def get_position(self, symbol: str) -> float:
        return self.positions.get(symbol, 0.0)

    def market_order(self, symbol: str, qty: float, side: str):
        self.orders.append((symbol, qty, side))
        sign = 1 if side.lower() == "buy" else -1
        self.positions[symbol] = self.positions.get(symbol, 0.0) + sign * qty
        return None

    def close_position(self, symbol: str):
        self.closed.append(symbol)
        self.positions.pop(symbol, None)
        return None

    def close_all_positions(self):
        self.closed.extend(list(self.positions.keys()))
        self.positions.clear()
        return None

    def list_positions(self) -> dict[str, float]:
        return dict(self.positions)


def _fake_fetch_daily(data: dict[str, pd.DataFrame]):
    def _fetch(symbol, cfg, **kwargs):
        return data[symbol].copy()

    return _fetch


def _run_process(inst, cfg, broker, state):
    exposure = {"total": 0.0}
    cache: dict[str, float] = {}
    main._process_instrument(inst, cfg, broker, state, exposure, cache)


# ---------------------------------------------------------------------------
# MEJORA 1a: sizing de un SHORT debe usar precio/ATR del PROXY, no del subyacente.
# ---------------------------------------------------------------------------


def test_short_signal_sizes_using_proxy_price_and_atr(monkeypatch):
    inst = Instrument(symbol="SPY", short_proxy="SH")
    cfg = make_cfg(inst)

    spy_df = make_trend_df(300, slope=-0.5, start_price=300.0, noise=0.2, seed=1)  # downtrend -> SHORT
    sh_df = make_trend_df(300, slope=0.02, start_price=13.0, noise=0.05, seed=2)  # proxy: precio/escala muy distintos

    data = {"SPY": spy_df, "SH": sh_df}
    monkeypatch.setattr(main, "_fetch_daily", _fake_fetch_daily(data))

    broker = FakeBroker(equity=500.0)
    state: dict = {"positions": {}}

    _run_process(inst, cfg, broker, state)

    assert len(broker.orders) == 1
    order_symbol, order_qty, order_side = broker.orders[0]
    assert order_symbol == "SH"
    assert order_side == "buy"

    sh_ind = compute_indicators(sh_df, cfg.strategy)
    sh_last = sh_ind.iloc[-1]
    expected_qty = position_size(
        500.0, float(sh_last["Close"]), cfg.strategy.atr_stop_mult * float(sh_last["atr"]), cfg.risk
    )
    assert order_qty == pytest.approx(expected_qty)

    # El qty NO debe coincidir con el que resultaría de usar precio/ATR de SPY
    # (el bug original): a precios tan distintos (~300 vs ~13) el sizing
    # correcto y el incorrecto deben diferir claramente.
    spy_ind = compute_indicators(spy_df, cfg.strategy)
    spy_last = spy_ind.iloc[-1]
    wrong_qty = position_size(
        500.0, float(spy_last["Close"]), cfg.strategy.atr_stop_mult * float(spy_last["atr"]), cfg.risk
    )
    assert order_qty != pytest.approx(wrong_qty)

    sym_state = state["positions"]["SPY"]
    assert sym_state["traded_symbol"] == "SH"
    assert sym_state["signal_direction"] == "SHORT"
    expected_stop = trailing_stop(float(sh_last["Close"]), float(sh_last["atr"]), None, "LONG", cfg.strategy.atr_stop_mult)
    assert sym_state["stop"] == pytest.approx(expected_stop)


# ---------------------------------------------------------------------------
# MEJORA 1b: transición LONG -> SHORT -> FLAT actualiza traded_symbol.
# ---------------------------------------------------------------------------


def test_long_to_short_to_flat_updates_traded_symbol(monkeypatch):
    inst = Instrument(symbol="SPY", short_proxy="SH")
    cfg = make_cfg(inst)

    long_df = make_trend_df(300, slope=0.5, start_price=100.0, noise=0.2, seed=3)  # uptrend -> LONG
    short_df = make_trend_df(300, slope=-0.5, start_price=300.0, noise=0.2, seed=4)  # downtrend -> SHORT
    sh_df = make_trend_df(300, slope=0.02, start_price=13.0, noise=0.05, seed=5)
    flat_df = make_flat_df(300, start_price=150.0, seed=0)

    data: dict[str, pd.DataFrame] = {}
    monkeypatch.setattr(main, "_fetch_daily", _fake_fetch_daily(data))

    broker = FakeBroker(equity=500.0)
    state: dict = {"positions": {}}

    # Fase 1: señal LONG -> abre SPY.
    data["SPY"] = long_df
    _run_process(inst, cfg, broker, state)
    assert state["positions"]["SPY"]["traded_symbol"] == "SPY"
    assert state["positions"]["SPY"]["signal_direction"] == "LONG"
    assert broker.get_position("SPY") != 0

    # Fase 2: señal cambia a SHORT -> cierra SPY, abre el proxy SH en el mismo ciclo.
    data["SPY"] = short_df
    data["SH"] = sh_df
    _run_process(inst, cfg, broker, state)
    assert "SPY" in broker.closed
    assert broker.get_position("SPY") == 0
    assert broker.get_position("SH") != 0
    assert state["positions"]["SPY"]["traded_symbol"] == "SH"
    assert state["positions"]["SPY"]["signal_direction"] == "SHORT"

    # Fase 3: señal FLAT -> cierra SH, sin posición.
    data["SPY"] = flat_df
    _run_process(inst, cfg, broker, state)
    assert "SH" in broker.closed
    assert broker.get_position("SH") == 0
    assert state["positions"]["SPY"]["traded_symbol"] is None
    assert state["positions"]["SPY"]["signal_direction"] is None


# ---------------------------------------------------------------------------
# MEJORA 2c: reconciliación estado <-> broker.
# ---------------------------------------------------------------------------


def test_reconcile_positions_adopts_orphan_without_trading(monkeypatch):
    inst = Instrument(symbol="SPY", short_proxy="SH")
    cfg = make_cfg(inst)

    sh_df = make_trend_df(300, slope=0.02, start_price=13.0, noise=0.05, seed=7)
    data = {"SH": sh_df}
    monkeypatch.setattr(main, "_fetch_daily", _fake_fetch_daily(data))

    broker = FakeBroker(equity=500.0)
    broker.positions["SH"] = 10.0  # el broker tiene una posición que el estado desconoce

    state: dict = {"positions": {}}
    adopted = main._reconcile_positions(cfg, broker, state)

    assert adopted == {"SPY"}
    assert broker.orders == []  # no se operó nada, solo se adoptó al estado
    sym_state = state["positions"]["SPY"]
    assert sym_state["traded_symbol"] == "SH"
    assert sym_state["signal_direction"] == "SHORT"
    assert sym_state["stop"] is not None


def test_reconcile_positions_noop_when_state_already_knows_position():
    inst = Instrument(symbol="SPY", short_proxy="SH")
    cfg = make_cfg(inst)

    broker = FakeBroker(equity=500.0)
    broker.positions["SPY"] = 1.0

    state = {"positions": {"SPY": {"direction": "LONG", "stop": 90.0, "traded_symbol": "SPY", "signal_direction": "LONG"}}}
    adopted = main._reconcile_positions(cfg, broker, state)

    assert adopted == set()


def test_run_cycle_skips_new_orders_for_adopted_symbol_this_cycle(monkeypatch, tmp_path):
    inst = Instrument(symbol="SPY", short_proxy="SH")
    cfg = make_cfg(inst)

    spy_df = make_trend_df(300, slope=-0.5, start_price=300.0, noise=0.2, seed=8)
    sh_df = make_trend_df(300, slope=0.02, start_price=13.0, noise=0.05, seed=9)
    data = {"SPY": spy_df, "SH": sh_df}

    broker = FakeBroker(equity=500.0)
    broker.positions["SH"] = 5.0  # posición huérfana en el broker

    monkeypatch.setattr(main, "_fetch_daily", _fake_fetch_daily(data))
    monkeypatch.setattr(main, "load_config", lambda: cfg)
    monkeypatch.setattr(main, "AlpacaBroker", lambda paper=True: broker)
    monkeypatch.setattr(main, "send_telegram", lambda text: False)
    monkeypatch.setattr(main, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(main, "HALT_FILE", tmp_path / "HALT")
    monkeypatch.setattr(main, "EQUITY_HISTORY_FILE", tmp_path / "equity_history.csv")
    monkeypatch.setattr(main, "LOG_FILE", tmp_path / "logs" / "bot.log")
    monkeypatch.setattr(main, "LAST_RUN_FILE", tmp_path / "last_run.json")

    exit_code = main.run_cycle()

    assert exit_code == 0
    assert broker.orders == []  # no se abrió nada nuevo este ciclo para el símbolo adoptado

    state = json.loads((tmp_path / "state.json").read_text())
    assert state["positions"]["SPY"]["traded_symbol"] == "SH"
    assert (tmp_path / "equity_history.csv").exists()
