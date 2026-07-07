"""Descarga de datos históricos diarios (OHLCV) y cotizaciones premarket.

Fuentes soportadas (en orden configurable): Polygon.io, Alpaca, yfinance.
Si la fuente principal falla, se intentan los fallbacks definidos en
``config.yaml`` → ``data.fallback_sources``.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from tradingbot.config import DataParams, polygon_key

log = logging.getLogger("tradingbot")

REQUIRED_COLS = ["Open", "High", "Low", "Close", "Volume"]
MAX_DATA_AGE_DAYS = 5
POLYGON_BASE_URL = "https://api.polygon.io"
_HTTP_TIMEOUT = 20


@dataclass
class PremarketQuote:
    """Cotización premarket de un símbolo con métricas para el scanner."""

    symbol: str
    price: float
    prev_close: float
    gap_pct: float
    volume: float
    avg_volume: float
    volume_ratio: float
    source: str


def fetch_daily(
    symbol: str,
    start: str | None = None,
    days: int | None = None,
    validate_freshness: bool = True,
    retries: int = 3,
    retry_delay_seconds: float = 2.0,
    data_params: DataParams | None = None,
) -> pd.DataFrame:
    """Devuelve un DataFrame diario con columnas Open/High/Low/Close/Volume.

    Reintenta automáticamente si todas las fuentes fallan (hasta ``retries``
    intentos con espera exponencial). Sin ``data_params``, usa solo yfinance
    (comportamiento histórico del módulo, compatible con backtest/tests).
    """
    last_error: Exception | None = None
    for attempt in range(1, max(retries, 1) + 1):
        try:
            df = _download_once(symbol, start=start, days=days, data_params=data_params)
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


def get_premarket_data(
    symbols: list[str],
    data_params: DataParams | None = None,
) -> list[PremarketQuote]:
    """Obtiene cotizaciones premarket para una lista de símbolos.

    Intenta cada fuente configurada hasta obtener datos para el símbolo.
    Los símbolos sin datos en ninguna fuente se omiten (con warning en log).
    """
    params = data_params or DataParams()
    sources = _ordered_sources(params)
    quotes: list[PremarketQuote] = []

    for symbol in symbols:
        sym = symbol.upper().strip()
        if not sym:
            continue
        quote: PremarketQuote | None = None
        for source in sources:
            try:
                quote = _fetch_premarket_from_source(sym, source, params)
                if quote is not None:
                    break
            except Exception as exc:  # noqa: BLE001
                log.debug("Premarket %s vía %s falló: %s", sym, source, exc)
        if quote is None:
            log.warning("Sin datos premarket para %s en ninguna fuente", sym)
        else:
            quotes.append(quote)

    return quotes


def fetch_market_movers(data_params: DataParams | None = None) -> list[str]:
    """Devuelve símbolos candidatos (gainers) desde Polygon o Alpaca.

    Si ninguna fuente de mercado está disponible, devuelve lista vacía.
    """
    params = data_params or DataParams()
    symbols: list[str] = []

    if _source_available("polygon", params):
        try:
            symbols = _polygon_gainers()
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudieron obtener gainers de Polygon: %s", exc)

    if not symbols and _source_available("alpaca", params):
        try:
            symbols = _alpaca_movers()
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudieron obtener movers de Alpaca: %s", exc)

    return symbols


def _ordered_sources(data_params: DataParams) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for source in [data_params.primary_source, *data_params.fallback_sources]:
        if source not in seen:
            seen.add(source)
            ordered.append(source)
    return ordered


def _source_available(source: str, data_params: DataParams) -> bool:
    if source == "polygon":
        return polygon_key() is not None
    if source == "alpaca":
        return bool(os.environ.get("ALPACA_API_KEY")) and bool(os.environ.get("ALPACA_SECRET_KEY"))
    return True  # yfinance no requiere credenciales


def _download_once(
    symbol: str,
    start: str | None = None,
    days: int | None = None,
    data_params: DataParams | None = None,
) -> pd.DataFrame:
    if data_params is None:
        return _download_yfinance(symbol, start=start, days=days)

    errors: list[str] = []
    for source in _ordered_sources(data_params):
        if not _source_available(source, data_params):
            continue
        try:
            if source == "polygon":
                return _download_polygon(symbol, start=start, days=days)
            if source == "alpaca":
                return _download_alpaca(symbol, start=start, days=days)
            return _download_yfinance(symbol, start=start, days=days)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{source}: {exc}")
            log.warning("Fuente %s falló para %s: %s", source, symbol, exc)

    raise RuntimeError(f"Todas las fuentes fallaron para {symbol}: {'; '.join(errors)}")


def _download_yfinance(
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

    return _normalize_ohlcv(df, symbol)


def _download_polygon(
    symbol: str,
    start: str | None = None,
    days: int | None = None,
) -> pd.DataFrame:
    api_key = polygon_key()
    if not api_key:
        raise RuntimeError("POLYGON_API_KEY no configurada")

    end_date = date.today()
    if start:
        start_date = date.fromisoformat(start[:10])
    else:
        start_date = end_date - timedelta(days=int(days or 400))

    path = (
        f"/v2/aggs/ticker/{urllib.parse.quote(symbol.upper())}/range/1/day/"
        f"{start_date.isoformat()}/{end_date.isoformat()}"
    )
    params = {
        "adjusted": "true",
        "sort": "asc",
        "limit": "50000",
        "apiKey": api_key,
    }
    payload = _http_get_json(f"{POLYGON_BASE_URL}{path}", params)
    results = payload.get("results") or []
    if not results:
        raise RuntimeError(f"Polygon devolvió datos vacíos para {symbol}")

    rows = []
    index = []
    for bar in results:
        ts = datetime.fromtimestamp(bar["t"] / 1000, tz=timezone.utc)
        rows.append({
            "Open": float(bar["o"]),
            "High": float(bar["h"]),
            "Low": float(bar["l"]),
            "Close": float(bar["c"]),
            "Volume": float(bar["v"]),
        })
        index.append(ts)

    df = pd.DataFrame(rows, index=pd.DatetimeIndex(index))
    return _normalize_ohlcv(df, symbol)


def _download_alpaca(
    symbol: str,
    start: str | None = None,
    days: int | None = None,
) -> pd.DataFrame:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    from tradingbot.config import alpaca_keys

    key, secret = alpaca_keys()
    client = StockHistoricalDataClient(key, secret)

    end = datetime.now(timezone.utc)
    if start:
        start_dt = datetime.fromisoformat(start[:10]).replace(tzinfo=timezone.utc)
    else:
        start_dt = end - timedelta(days=int(days or 400))

    request = StockBarsRequest(
        symbol_or_symbols=symbol.upper(),
        timeframe=TimeFrame.Day,
        start=start_dt,
        end=end,
    )
    bars = client.get_stock_bars(request)
    df = bars.df
    if df is None or df.empty:
        raise RuntimeError(f"Alpaca devolvió datos vacíos para {symbol}")

    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index(level=0, drop=True)

    rename = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    return _normalize_ohlcv(df, symbol)


def _normalize_ohlcv(df: pd.DataFrame | None, symbol: str) -> pd.DataFrame:
    if df is None or df.empty:
        raise RuntimeError(f"Sin datos para {symbol}")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df[[c for c in REQUIRED_COLS if c in df.columns]].dropna()
    missing = set(REQUIRED_COLS) - set(df.columns)
    if missing:
        raise RuntimeError(f"Faltan columnas {missing} en los datos de {symbol}")

    return df


def _fetch_premarket_from_source(
    symbol: str,
    source: str,
    data_params: DataParams,
) -> PremarketQuote | None:
    if source == "polygon":
        return _premarket_polygon(symbol, data_params)
    if source == "alpaca":
        return _premarket_alpaca(symbol, data_params)
    return _premarket_yfinance(symbol, data_params)


def _premarket_polygon(symbol: str, data_params: DataParams) -> PremarketQuote | None:
    api_key = polygon_key()
    if not api_key:
        return None

    path = f"/v2/snapshot/locale/us/markets/stocks/tickers/{urllib.parse.quote(symbol)}"
    snap = _http_get_json(f"{POLYGON_BASE_URL}{path}", {"apiKey": api_key})
    ticker = snap.get("ticker") or {}

    day = ticker.get("day") or {}
    prev = ticker.get("prevDay") or {}
    pre = ticker.get("preMarket") or {}

    prev_close = float(prev.get("c") or day.get("c") or 0)
    price = float(pre.get("p") or ticker.get("lastTrade", {}).get("p") or day.get("c") or 0)
    volume = float(pre.get("v") or day.get("v") or 0)

    if prev_close <= 0 or price <= 0:
        return None

    avg_volume = _avg_volume_from_daily(symbol, data_params, fallback=volume)
    gap_pct = (price - prev_close) / prev_close
    volume_ratio = volume / avg_volume if avg_volume > 0 else 0.0

    return PremarketQuote(
        symbol=symbol,
        price=price,
        prev_close=prev_close,
        gap_pct=gap_pct,
        volume=volume,
        avg_volume=avg_volume,
        volume_ratio=volume_ratio,
        source="polygon",
    )


def _premarket_alpaca(symbol: str, data_params: DataParams) -> PremarketQuote | None:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestQuoteRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    from tradingbot.config import alpaca_keys

    key, secret = alpaca_keys()
    client = StockHistoricalDataClient(key, secret)

    quote_resp = client.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=symbol))
    quote = quote_resp[symbol]
    price = float(quote.ask_price or quote.bid_price or 0)
    if price <= 0:
        return None

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=25)
    bars = client.get_stock_bars(
        StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            limit=20,
        )
    )
    bars_df = bars.df
    if bars_df is None or bars_df.empty:
        return None
    if isinstance(bars_df.index, pd.MultiIndex):
        bars_df = bars_df.reset_index(level=0, drop=True)

    prev_close = float(bars_df["close"].iloc[-1])
    avg_volume = float(bars_df["volume"].tail(20).mean())
    volume = float(bars_df["volume"].iloc[-1]) if len(bars_df) else 0.0
    gap_pct = (price - prev_close) / prev_close if prev_close > 0 else 0.0
    volume_ratio = volume / avg_volume if avg_volume > 0 else 0.0

    return PremarketQuote(
        symbol=symbol,
        price=price,
        prev_close=prev_close,
        gap_pct=gap_pct,
        volume=volume,
        avg_volume=avg_volume,
        volume_ratio=volume_ratio,
        source="alpaca",
    )


def _premarket_yfinance(symbol: str, data_params: DataParams) -> PremarketQuote | None:
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    info = {}
    try:
        info = ticker.fast_info
    except Exception:  # noqa: BLE001
        pass

    price = float(getattr(info, "last_price", 0) or getattr(info, "open", 0) or 0)
    prev_close = float(getattr(info, "previous_close", 0) or 0)

    hist = ticker.history(period="1mo", auto_adjust=True)
    if hist is None or hist.empty:
        return None

    if price <= 0:
        price = float(hist["Close"].iloc[-1])
    if prev_close <= 0 and len(hist) >= 2:
        prev_close = float(hist["Close"].iloc[-2])
    if prev_close <= 0:
        return None

    avg_volume = float(hist["Volume"].tail(20).mean())
    volume = float(hist["Volume"].iloc[-1])
    gap_pct = (price - prev_close) / prev_close
    volume_ratio = volume / avg_volume if avg_volume > 0 else 0.0

    return PremarketQuote(
        symbol=symbol,
        price=price,
        prev_close=prev_close,
        gap_pct=gap_pct,
        volume=volume,
        avg_volume=avg_volume,
        volume_ratio=volume_ratio,
        source="yfinance",
    )


def _avg_volume_from_daily(symbol: str, data_params: DataParams, fallback: float) -> float:
    try:
        df = fetch_daily(symbol, days=30, validate_freshness=False, data_params=data_params)
        return float(df["Volume"].tail(20).mean())
    except Exception:  # noqa: BLE001
        return fallback


def _polygon_gainers() -> list[str]:
    api_key = polygon_key()
    if not api_key:
        return []

    path = "/v2/snapshot/locale/us/markets/stocks/gainers"
    payload = _http_get_json(f"{POLYGON_BASE_URL}{path}", {"apiKey": api_key})
    tickers = payload.get("tickers") or []
    return [str(t.get("ticker", "")).upper() for t in tickers if t.get("ticker")]


def _alpaca_movers() -> list[str]:
    """Usa los símbolos más activos del snapshot de Alpaca (si está disponible)."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockSnapshotRequest

    from tradingbot.config import alpaca_keys

    key, secret = alpaca_keys()
    client = StockHistoricalDataClient(key, secret)
    # Alpaca no expone gainers directamente en todas las cuentas; devolvemos vacío
    # y dejamos que el pool venga del universo/watchlist.
    _ = (client, StockSnapshotRequest)
    return []


def _http_get_json(url: str, params: dict[str, str]) -> dict:
    query = urllib.parse.urlencode(params)
    full_url = f"{url}?{query}" if query else url
    req = urllib.request.Request(full_url, headers={"User-Agent": "TradingBot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} en {url}: {body[:200]}") from exc


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