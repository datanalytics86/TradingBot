"""Reglas de gestión de riesgo: tamaño de posición y circuit breaker."""
from __future__ import annotations

from tradingbot.config import RiskParams, StrategyParams


def position_size(equity: float, price: float, stop_distance: float, risk: RiskParams) -> float:
    """Calcula la cantidad de acciones/ETF a comprar.

    La cantidad se elige de forma que la pérdida potencial hasta el stop
    sea aproximadamente ``equity * risk_per_trade``, con un tope adicional
    por posición individual (``max_position_pct``).
    """
    if stop_distance <= 0 or price <= 0 or equity <= 0:
        return 0.0

    risk_amount = equity * risk.risk_per_trade
    qty_by_risk = risk_amount / stop_distance

    max_notional = equity * risk.max_position_pct
    qty_by_cap = max_notional / price

    qty = min(qty_by_risk, qty_by_cap)
    return round(max(qty, 0.0), 3)


def apply_volatility_scaling(
    qty: float,
    current_atr: float,
    atr_average: float,
    strategy: StrategyParams,
) -> float:
    """Reduce el tamaño cuando la volatilidad actual supera su media reciente.

    Si ATR > ``vol_scale_threshold`` × ATR_avg, multiplica qty por
    ``vol_scale_factor`` para no sobre-apalancarse en picos de volatilidad.
    """
    if qty <= 0 or current_atr <= 0 or atr_average <= 0:
        return qty
    if current_atr > strategy.vol_scale_threshold * atr_average:
        return round(qty * strategy.vol_scale_factor, 3)
    return qty


def cap_by_total_exposure(
    equity: float,
    current_exposure: float,
    proposed_notional: float,
    risk: RiskParams,
) -> float:
    """Limita el notional propuesto para no superar ``max_total_exposure_pct``."""
    if equity <= 0 or proposed_notional <= 0:
        return 0.0
    max_total = equity * risk.max_total_exposure_pct
    room = max(0.0, max_total - current_exposure)
    return min(proposed_notional, room)


def check_circuit_breaker(equity: float, peak_equity: float, risk: RiskParams) -> bool:
    """True si el equity cayó lo suficiente desde su máximo como para apagar el bot."""
    if peak_equity <= 0:
        return False
    threshold = peak_equity * (1 - risk.max_drawdown)
    return equity < threshold


def check_daily_loss(equity: float, day_start_equity: float, risk: RiskParams) -> bool:
    """True si el equity cayó más de ``max_daily_loss`` desde el inicio del día."""
    if day_start_equity <= 0:
        return False
    threshold = day_start_equity * (1 - risk.max_daily_loss)
    return equity < threshold