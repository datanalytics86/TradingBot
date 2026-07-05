"""Tests de la validación de frescura de datos en `fetch_daily` (sin red real)."""
from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from tradingbot import data as data_module


def _make_yf_df(last_date: datetime) -> pd.DataFrame:
    idx = pd.date_range(end=last_date, periods=5, freq="D")
    return pd.DataFrame(
        {
            "Open": [1.0, 2.0, 3.0, 4.0, 5.0],
            "High": [1.5, 2.5, 3.5, 4.5, 5.5],
            "Low": [0.5, 1.5, 2.5, 3.5, 4.5],
            "Close": [1.2, 2.2, 3.2, 4.2, 5.2],
            "Volume": [100, 100, 100, 100, 100],
        },
        index=idx,
    )


def _install_fake_yfinance(monkeypatch, df: pd.DataFrame) -> None:
    fake_yf = types.SimpleNamespace(download=lambda *args, **kwargs: df.copy())
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)


def test_fetch_daily_raises_on_stale_data(monkeypatch):
    stale_date = datetime.now(timezone.utc) - timedelta(days=10)
    _install_fake_yfinance(monkeypatch, _make_yf_df(stale_date))

    with pytest.raises(RuntimeError, match="obsoletos"):
        data_module.fetch_daily("SPY", days=400)


def test_fetch_daily_accepts_fresh_data(monkeypatch):
    fresh_date = datetime.now(timezone.utc)
    _install_fake_yfinance(monkeypatch, _make_yf_df(fresh_date))

    out = data_module.fetch_daily("SPY", days=400)
    assert not out.empty


def test_fetch_daily_freshness_check_can_be_disabled(monkeypatch):
    stale_date = datetime.now(timezone.utc) - timedelta(days=365)
    _install_fake_yfinance(monkeypatch, _make_yf_df(stale_date))

    out = data_module.fetch_daily("SPY", days=400, validate_freshness=False)
    assert not out.empty


def test_fetch_daily_stale_boundary_ok_just_inside_limit(monkeypatch):
    # 4 días de antigüedad: por debajo del límite de 5, no debe lanzar.
    date = datetime.now(timezone.utc) - timedelta(days=4)
    _install_fake_yfinance(monkeypatch, _make_yf_df(date))

    out = data_module.fetch_daily("SPY", days=400)
    assert not out.empty
