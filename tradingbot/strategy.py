"""Indicadores técnicos y lógica de señal de la estrategia trend-following.

Todas las funciones son puras (no acceden a red ni a disco) para poder
testearlas con datos sintéticos.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradingbot.config import StrategyParams


def compute_indicators(df: pd.DataFrame, params: StrategyParams) -> pd.DataFrame:
    """Agrega columnas de indicadores a un DataFrame OHLC.

    Columnas agregadas:
    - ``ema_fast``: EMA de `params.ema_fast` periodos sobre Close.
    - ``ema_slow``: EMA de `params.ema_slow` periodos sobre Close.
    - ``sma_trend``: SMA de `params.trend_filter` periodos sobre Close (filtro de tendencia).
    - ``atr``: Average True Range calculado como la MEDIA MÓVIL SIMPLE (no Wilder)
      del True Range sobre `params.atr_period` periodos. Se elige la media simple
      por ser más transparente y fácil de razonar/testear; para esta estrategia
      de trailing-stop la diferencia frente al suavizado de Wilder es marginal.

    Devuelve una copia del DataFrame de entrada con las columnas agregadas.
    """
    out = df.copy()

    out["ema_fast"] = out["Close"].ewm(span=params.ema_fast, adjust=False).mean()
    out["ema_slow"] = out["Close"].ewm(span=params.ema_slow, adjust=False).mean()
    out["sma_trend"] = out["Close"].rolling(params.trend_filter).mean()

    prev_close = out["Close"].shift(1)
    high_low = out["High"] - out["Low"]
    high_prev_close = (out["High"] - prev_close).abs()
    low_prev_close = (out["Low"] - prev_close).abs()
    true_range = pd.concat([high_low, high_prev_close, low_prev_close], axis=1).max(axis=1)
    out["atr"] = true_range.rolling(params.atr_period).mean()

    return out


def signal(df: pd.DataFrame, params: StrategyParams) -> str:
    """Devuelve "LONG", "SHORT" o "FLAT" para la última barra del DataFrame.

    `df` debe ser el resultado de `compute_indicators`. Si algún indicador
    necesario es NaN en la última barra (datos insuficientes), devuelve "FLAT".
    """
    if df.empty:
        return "FLAT"

    last = df.iloc[-1]
    needed = ("ema_fast", "ema_slow", "sma_trend", "Close")
    if any(col not in df.columns for col in needed):
        return "FLAT"
    if any(pd.isna(last[col]) for col in needed):
        return "FLAT"

    if last["ema_fast"] > last["ema_slow"] and last["Close"] > last["sma_trend"]:
        return "LONG"
    if last["ema_fast"] < last["ema_slow"] and last["Close"] < last["sma_trend"]:
        return "SHORT"
    return "FLAT"


def trailing_stop(
    close: float,
    atr: float,
    prev_stop: float | None,
    direction: str,
    mult: float,
) -> float:
    """Calcula el nuevo stop de seguimiento.

    - Long: el stop solo puede subir -> ``max(prev_stop, close - mult*atr)``.
    - Short: el stop solo puede bajar -> ``min(prev_stop, close + mult*atr)``.
    - Si `prev_stop` es None (posición recién abierta), se inicializa
      directamente desde el precio/ATR actual.
    """
    if direction == "LONG":
        candidate = close - mult * atr
        if prev_stop is None:
            return candidate
        return max(prev_stop, candidate)
    if direction == "SHORT":
        candidate = close + mult * atr
        if prev_stop is None:
            return candidate
        return min(prev_stop, candidate)
    raise ValueError(f"direction inválida: {direction!r} (debe ser 'LONG' o 'SHORT')")
