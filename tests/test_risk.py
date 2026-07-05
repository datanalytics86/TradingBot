"""Tests de las reglas de riesgo: sizing y circuit breaker."""
from __future__ import annotations

import pytest

from tradingbot.config import RiskParams
from tradingbot.config import StrategyParams
from tradingbot.risk import (
    apply_volatility_scaling,
    cap_by_total_exposure,
    check_circuit_breaker,
    position_size,
)

STRATEGY = StrategyParams()

RISK = RiskParams(risk_per_trade=0.02, max_position_pct=0.45, max_total_exposure_pct=0.70, max_drawdown=0.15)


def test_position_size_respects_risk_per_trade():
    equity = 500.0
    price = 50.0
    stop_distance = 5.0  # riesgo por unidad (holgado para que el riesgo, no el tope, limite)
    qty = position_size(equity, price, stop_distance, RISK)
    # riesgo esperado ~= equity * risk_per_trade
    expected_risk = equity * RISK.risk_per_trade
    assert abs(qty * stop_distance - expected_risk) < 1e-6
    # y no debe superar el tope de posición máxima
    assert qty * price <= equity * RISK.max_position_pct + 1e-9


def test_position_size_capped_by_max_position_pct():
    equity = 500.0
    price = 100.0
    stop_distance = 0.01  # riesgo por unidad muy pequeño -> sizing por riesgo sería enorme
    qty = position_size(equity, price, stop_distance, RISK)
    max_notional = equity * RISK.max_position_pct
    assert qty * price <= max_notional + 1e-9
    # el tope debe ser el factor limitante en este caso
    assert abs(qty * price - max_notional) < 1e-2


def test_position_size_zero_when_stop_distance_not_positive():
    assert position_size(500.0, 100.0, 0.0, RISK) == 0.0
    assert position_size(500.0, 100.0, -1.0, RISK) == 0.0


def test_position_size_zero_when_price_not_positive():
    assert position_size(500.0, 0.0, 2.0, RISK) == 0.0
    assert position_size(500.0, -10.0, 2.0, RISK) == 0.0


def test_position_size_rounded_to_three_decimals():
    qty = position_size(500.0, 33.333, 1.111, RISK)
    assert round(qty, 3) == qty


def test_circuit_breaker_triggers_exactly_below_threshold():
    peak = 1000.0
    threshold = peak * (1 - RISK.max_drawdown)  # 850.0
    assert check_circuit_breaker(threshold - 0.01, peak, RISK) is True
    assert check_circuit_breaker(threshold, peak, RISK) is False
    assert check_circuit_breaker(threshold + 0.01, peak, RISK) is False


def test_circuit_breaker_false_when_no_peak():
    assert check_circuit_breaker(500.0, 0.0, RISK) is False


def test_volatility_scaling_reduces_qty_in_high_vol():
    base_qty = 10.0
    scaled = apply_volatility_scaling(base_qty, current_atr=3.0, atr_average=1.0, strategy=STRATEGY)
    assert scaled == pytest.approx(base_qty * STRATEGY.vol_scale_factor)


def test_volatility_scaling_unchanged_in_normal_vol():
    base_qty = 10.0
    scaled = apply_volatility_scaling(base_qty, current_atr=1.2, atr_average=1.0, strategy=STRATEGY)
    assert scaled == base_qty


def test_cap_by_total_exposure_limits_notional():
    allowed = cap_by_total_exposure(500.0, current_exposure=300.0, proposed_notional=200.0, risk=RISK)
    assert allowed == pytest.approx(50.0)
