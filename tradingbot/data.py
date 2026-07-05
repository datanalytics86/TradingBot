"""Descarga de datos históricos diarios (OHLCV)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

REQUIRED_COLS = ["Open", "High", "Low", "Close", "Volume"]

# Máxima antigüedad (en días calendario) que se tolera para la última barra
# antes de considerar los datos obsoletos (ver `_check_freshness`).
MAX_DATA_AGE_DAYS = 5


def fetch_daily(
    symbol: str,
    start: str | None = None,
    days: int | None = None,
    validate_freshness: bool = True,
) -> pd.DataFrame:
    """Devuelve un DataFrame diario con columnas Open/High/Low/Close/Volume.

    Usa yfinance (gratis, sin API key). Para el ciclo en vivo basta `days`;
    para backtests usa `start` (ej. '2015-01-01').

    Si `validate_freshness` es True (default), valida que la última barra no
    sea más vieja que `MAX_DATA_AGE_DAYS` días calendario respecto a hoy; si
    lo es, lanza `RuntimeError` (evita operar en vivo con datos obsoletos si
    yfinance degrada). El backtest, que trabaja con datos históricos, debe
    llamar con `validate_freshness=False`.
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

    if validate_freshness:
        _check_freshness(df, symbol)

    return df


def _check_freshness(df: pd.DataFrame, symbol: str, max_age_days: int = MAX_DATA_AGE_DAYS) -> None:
    """Lanza RuntimeError si la última barra de `df` es más vieja que `max_age_days`."""
    last_ts = df.index[-1]
    last_dt = last_ts.to_pydatetime() if hasattr(last_ts, "to_pydatetime") else last_ts

    if last_dt.tzinfo is not None:
        now = datetime.now(timezone.utc)
        last_dt = last_dt.astimezone(timezone.utc)
    else:
        now = datetime.now()

    age = now - last_dt
    if age > timedelta(days=max_age_days):
        raise RuntimeError(
            f"Datos obsoletos para {symbol}: última barra {last_dt.date()} "
            f"(hace {age.days} días, máximo tolerado {max_age_days})"
        )
