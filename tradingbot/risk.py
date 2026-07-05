"""Reglas de gestión de riesgo: tamaño de posición y circuit breaker."""
from __future__ import annotations

from tradingbot.config import RiskParams


def position_size(equity: float, price: float, stop_distance: float, risk: RiskParams) -> float:
    """Calcula la cantidad de acciones/ETF a comprar (o vender en corto).

    La cantidad se elige de forma que la pérdida potencial hasta el stop
    (``qty * stop_distance``) sea aproximadamente ``equity * risk_per_trade``,
    con un tope adicional: el valor nominal de la posición
    (``qty * price``) no puede superar ``equity * max_position_pct``.

    Devuelve 0 si `stop_distance` o `price` no son positivos.
    Acepta cantidades fraccionarias (Alpaca soporta fractional shares),
    redondeadas a 3 decimales.
    """
    if stop_distance <= 0 or price <= 0 or equity <= 0:
        return 0.0

    risk_amount = equity * risk.risk_per_trade
    qty_by_risk = risk_amount / stop_distance

    max_notional = equity * risk.max_position_pct
    qty_by_cap = max_notional / price

    qty = min(qty_by_risk, qty_by_cap)
    qty = max(qty, 0.0)
    return round(qty, 3)


def check_circuit_breaker(equity: float, peak_equity: float, risk: RiskParams) -> bool:
    """True si el equity cayó lo suficiente desde su máximo como para apagar el bot.

    Se activa cuando ``equity < peak_equity * (1 - max_drawdown)``.
    """
    if peak_equity <= 0:
        return False
    threshold = peak_equity * (1 - risk.max_drawdown)
    return equity < threshold
