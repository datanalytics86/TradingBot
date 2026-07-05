"""Descarga de datos históricos diarios (OHLCV)."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

log = logging.getLogger("tradingbot")

REQUIRED_COLS = ["Open", "High", "Low", "Close", "Volume"]
MAX_DATA_AGE_DAYS = 5


def fetch_daily(
    symbol: str,
    start: str | None = None,
    days: int | None = None,
    validate_freshness: bool = True,
    retries: int = 3,
    retry_delay_seconds: float = 2.0,
) -> pd.DataFrame:
    """Devuelve un DataFrame diario con columnas Open/High/Low/Close/Volume.

    Reintenta automáticamente si yfinance falla o devuelve datos vacíos
    (hasta ``retries`` intentos con espera exponencial).
    """
    last_error: Exception | None = None
    for attempt in range(1, max(retries, 1) + 1):
        try:
            df = _download_once(symbol, start=start, days=days)
            if validate_freshness:
                _check_freshness(df, symbol)
            return df
        except Exception as exc:  # noqa: BLE001 - reintentar ante cualquier fallo de red/datos
            last_error = exc
            if attempt < retries:
                wait = retry_delay_seconds * attempt
                log.warning(
                    "Intento %d/%d fallido para %s: %s. Reintentando en %.1fs...",
                    attempt, retries, symbol, exc, wait,
                )
                time.sleep(wait)

    raise RuntimeError(f"Sin datos para {symbol} tras {retries} intentos: {last_error}")


def _download_once(
    symbol: str,
    start: str | None = None,
    days: int | None = None,
) -> pd.DataFrame:
    import yfinance as yf

    if start:
        df = yf.download(symbol, start=start, auto_adjust=True, progress=False)
    else:
        period = f"{days or 400}d"
        df = yf.download(symbol, period=period, auto_adjust=True, progress=False)

    if df is None or df.empty:
        raise RuntimeError(f"Sin datos para {symbol}")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df[[c for c in REQUIRED_COLS if c in df.columns]].dropna()
    missing = set(REQUIRED_COLS) - set(df.columns)
    if missing:
        raise RuntimeError(f"Faltan columnas {missing} en los datos de {symbol}")

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