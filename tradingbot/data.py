"""Descarga de datos históricos diarios (OHLCV)."""
from __future__ import annotations

import pandas as pd

REQUIRED_COLS = ["Open", "High", "Low", "Close", "Volume"]


def fetch_daily(symbol: str, start: str | None = None, days: int | None = None) -> pd.DataFrame:
    """Devuelve un DataFrame diario con columnas Open/High/Low/Close/Volume.

    Usa yfinance (gratis, sin API key). Para el ciclo en vivo basta `days`;
    para backtests usa `start` (ej. '2015-01-01').
    """
    import yfinance as yf

    if start:
        df = yf.download(symbol, start=start, auto_adjust=True, progress=False)
    else:
        period = f"{days or 400}d"
        df = yf.download(symbol, period=period, auto_adjust=True, progress=False)

    if df is None or df.empty:
        raise RuntimeError(f"Sin datos para {symbol}")

    # yfinance puede devolver columnas MultiIndex (símbolo como segundo nivel)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df[[c for c in REQUIRED_COLS if c in df.columns]].dropna()
    missing = set(REQUIRED_COLS) - set(df.columns)
    if missing:
        raise RuntimeError(f"Faltan columnas {missing} en los datos de {symbol}")
    return df
