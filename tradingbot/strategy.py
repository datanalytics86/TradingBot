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
    - ``sma_trend``: SMA de `params.trend_filter` periodos sobre Close.
    - ``atr``: media simple del True Range sobre `params.atr_period` periodos.
    - ``adx``: fuerza de tendencia (ADX) sobre `params.adx_period` periodos.
    - ``plus_di`` / ``minus_di``: componentes direccionales del ADX.
    - ``atr_avg``: media móvil de 20 periodos del ATR (para scaling de vol).
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
    out["atr_avg"] = out["atr"].rolling(20).mean()

    plus_di, minus_di, adx = _compute_dmi(out, params.adx_period)
    out["plus_di"] = plus_di
    out["minus_di"] = minus_di
    out["adx"] = adx

    return out


def _compute_dmi(df: pd.DataFrame, period: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Calcula +DI, -DI y ADX con suavizado de Wilder."""
    prev_high = df["High"].shift(1)
    prev_low = df["Low"].shift(1)

    up_move = df["High"] - prev_high
    down_move = prev_low - df["Low"]

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    alpha = 1.0 / period
    atr_w = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=alpha, adjust=False).mean() / atr_w
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=alpha, adjust=False).mean() / atr_w

    di_sum = plus_di + minus_di
    dx = 100 * (plus_di - minus_di).abs() / di_sum.replace(0, np.nan)
    adx = dx.ewm(alpha=alpha, adjust=False).mean()
    return plus_di, minus_di, adx


def signal(df: pd.DataFrame, params: StrategyParams) -> str:
    """Devuelve "LONG", "SHORT" o "FLAT" para la última barra del DataFrame.

    Requiere alineación de EMAs + filtro SMA 200. Si ADX está por debajo de
    ``min_adx``, devuelve FLAT aunque las EMAs coincidan (evita whipsaws en
    mercados laterales).
    """
    if df.empty:
        return "FLAT"

    last = df.iloc[-1]
    needed = ("ema_fast", "ema_slow", "sma_trend", "Close", "adx")
    if any(col not in df.columns for col in needed):
        return "FLAT"
    if any(pd.isna(last[col]) for col in needed):
        return "FLAT"

    if last["adx"] < params.min_adx:
        return "FLAT"

    plus_di = last.get("plus_di")
    minus_di = last.get("minus_di")
    if params.use_di_filter:
        if plus_di is None or minus_di is None or pd.isna(plus_di) or pd.isna(minus_di):
            return "FLAT"

    if last["ema_fast"] > last["ema_slow"] and last["Close"] > last["sma_trend"]:
        if not params.use_di_filter or plus_di > minus_di:
            return "LONG"
    if last["ema_fast"] < last["ema_slow"] and last["Close"] < last["sma_trend"]:
        if not params.use_di_filter or minus_di > plus_di:
            return "SHORT"
    return "FLAT"


def trend_strength(df: pd.DataFrame) -> float:
    """Fuerza relativa de la tendencia (0-1) basada en separación EMA y ADX.

    Usado para priorizar instrumentos cuando el capital total es limitado.
    """
    if df.empty:
        return 0.0
    last = df.iloc[-1]
    if any(col not in df.columns for col in ("ema_fast", "ema_slow", "adx", "Close")):
        return 0.0
    if any(pd.isna(last[col]) for col in ("ema_fast", "ema_slow", "adx", "Close")):
        return 0.0
    if last["Close"] <= 0:
        return 0.0

    ema_spread = abs(last["ema_fast"] - last["ema_slow"]) / last["Close"]
    adx_component = min(last["adx"] / 50.0, 1.0)
    return round(min(ema_spread * 100 + adx_component * 0.5, 1.0), 4)


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