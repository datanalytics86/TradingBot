"""Tests del scanner premarket (sin red)."""
from __future__ import annotations

import csv
import json

import pytest

from tradingbot.config import ApiParams, Config, DataParams, Instrument, RiskParams, ScannerParams, StrategyParams
from tradingbot.data import PremarketQuote
from tradingbot.scanner import (
    GrokInsight,
    ScanResult,
    _compute_score,
    _gap_threshold_for,
    _heuristic_insights,
    _parse_grok_response,
    _passes_filters,
    analyze_with_grok,
    apply_scanner_to_universe,
    export_to_csv,
    format_scan_report,
    save_last_scanner_run,
    scan_premarket,
)


def make_cfg(scanner: ScannerParams | None = None, api: ApiParams | None = None) -> Config:
    return Config(
        mode="paper",
        initial_capital=500.0,
        universe=[
            Instrument(symbol="SPY", short_proxy="SH"),
            Instrument(symbol="QQQ", short_proxy="PSQ"),
        ],
        strategy=StrategyParams(),
        risk=RiskParams(),
        data=DataParams(),
        scanner=scanner or ScannerParams(
            enabled=True,
            gap_threshold=0.05,
            etf_gap_threshold=0.025,
            volume_multiplier=2.0,
            min_price=5.0,
            max_price=500.0,
            max_symbols=15,
            min_avg_volume=100_000,
        ),
        api=api or ApiParams(grok_enabled=True),
    )


def _quote(
    symbol: str,
    gap: float = 0.08,
    vol_ratio: float = 3.0,
    price: float = 50.0,
    avg_vol: float = 1_000_000,
) -> PremarketQuote:
    return PremarketQuote(
        symbol=symbol,
        price=price,
        prev_close=price / (1 + gap),
        gap_pct=gap,
        volume=avg_vol * vol_ratio,
        avg_volume=avg_vol,
        volume_ratio=vol_ratio,
        source="test",
    )


def test_gap_threshold_lower_for_etf():
    cfg = make_cfg()
    assert _gap_threshold_for("SPY", cfg.scanner, cfg) == 0.025
    assert _gap_threshold_for("NVDA", cfg.scanner, cfg) == 0.05


def test_passes_filters_etf_with_lower_gap():
    cfg = make_cfg()
    etf_quote = _quote("SPY", gap=0.03)
    stock_quote = _quote("NVDA", gap=0.03)
    assert _passes_filters(etf_quote, cfg.scanner, 0.025) is True
    assert _passes_filters(stock_quote, cfg.scanner, 0.05) is False


def test_passes_filters_ok():
    cfg = make_cfg()
    assert _passes_filters(_quote("NVDA"), cfg.scanner, 0.05) is True


def test_compute_score_bounded():
    cfg = make_cfg()
    score = _compute_score(_quote("TSLA"), cfg.scanner, 0.05)
    assert 0.0 < score <= 1.0


def test_scan_premarket_filters_and_ranks(monkeypatch):
    cfg = make_cfg()
    quotes = [
        _quote("AAA", gap=0.06, vol_ratio=2.5, price=20),
        _quote("BBB", gap=0.10, vol_ratio=4.0, price=80),
        _quote("CCC", gap=0.02, vol_ratio=5.0, price=30),
    ]

    monkeypatch.setattr(
        "tradingbot.scanner.get_premarket_data",
        lambda symbols, data_params: [q for q in quotes if q.symbol in symbols],
    )
    monkeypatch.setattr(
        "tradingbot.scanner.fetch_market_movers",
        lambda data_params: ["AAA", "BBB", "CCC"],
    )

    results = scan_premarket(cfg)
    assert len(results) == 2
    assert results[0].symbol == "BBB"


def test_analyze_with_grok_uses_heuristic_when_disabled(monkeypatch):
    cfg = make_cfg(api=ApiParams(grok_enabled=False))
    picks = [ScanResult("NVDA", 0.85, 0.08, 3.5, 120.0, 1e6, "test", "STOCK", 0.05)]
    insights = analyze_with_grok(cfg, picks)
    assert len(insights) == 1
    assert insights[0].source == "heuristic"


def test_analyze_with_grok_calls_api(monkeypatch):
    cfg = make_cfg()
    picks = [ScanResult("NVDA", 0.85, 0.08, 3.5, 120.0, 1e6, "test", "STOCK", 0.05)]

    monkeypatch.setattr("tradingbot.scanner.grok_key", lambda: "fake-key")
    monkeypatch.setattr("tradingbot.scanner._load_grok_cache", lambda key: None)
    monkeypatch.setattr("tradingbot.scanner._save_grok_cache", lambda key, ins: None)
    monkeypatch.setattr(
        "tradingbot.scanner._call_grok_api",
        lambda c, p: json.dumps([{
            "symbol": "NVDA",
            "verdict": "strong",
            "confidence": 0.88,
            "summary": "Gap y volumen confirman momentum.",
            "sizing_hint": "Usar 1.5% del equity con stop ATR.",
        }]),
    )

    insights = analyze_with_grok(cfg, picks)
    assert insights[0].symbol == "NVDA"
    assert insights[0].verdict == "strong"
    assert insights[0].source == "grok"
    assert "1.5%" in insights[0].sizing_hint


def test_analyze_with_grok_falls_back_on_api_error(monkeypatch):
    cfg = make_cfg()
    picks = [ScanResult("TSLA", 0.6, 0.06, 2.5, 250.0, 5e5, "test", "STOCK", 0.05)]

    monkeypatch.setattr("tradingbot.scanner.grok_key", lambda: "fake-key")
    monkeypatch.setattr("tradingbot.scanner._load_grok_cache", lambda key: None)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("API down")

    monkeypatch.setattr("tradingbot.scanner._call_grok_api", _boom)

    insights = analyze_with_grok(cfg, picks)
    assert len(insights) == 1
    assert insights[0].source == "heuristic"


def test_analyze_with_grok_uses_cache(monkeypatch):
    cfg = make_cfg()
    picks = [ScanResult("AMD", 0.7, 0.07, 2.8, 150.0, 8e5, "test", "STOCK", 0.05)]
    cached = [GrokInsight("AMD", "buy", "desde caché", 0.7, "1%", "cache")]

    monkeypatch.setattr("tradingbot.scanner.grok_key", lambda: "fake-key")
    monkeypatch.setattr("tradingbot.scanner._load_grok_cache", lambda key: cached)

    insights = analyze_with_grok(cfg, picks)
    assert insights[0].source == "cache"
    assert insights[0].verdict == "buy"


def test_parse_grok_response_fills_missing_symbols():
    picks = [
        ScanResult("NVDA", 0.85, 0.08, 3.5, 120.0, 1e6, "test", "STOCK", 0.05),
        ScanResult("TSLA", 0.6, 0.06, 2.5, 250.0, 5e5, "test", "STOCK", 0.05),
    ]
    raw = json.dumps([{
        "symbol": "NVDA", "verdict": "strong", "confidence": 0.9,
        "summary": "Ok", "sizing_hint": "2%",
    }])
    insights = _parse_grok_response(raw, picks)
    assert len(insights) == 2
    assert insights[0].source == "grok"
    assert insights[1].source == "heuristic"


def test_export_to_csv_with_grok_columns(tmp_path):
    results = [ScanResult("NVDA", 0.85, 0.08, 3.0, 120.0, 1e6, "polygon", "STOCK", 0.05)]
    insights = [GrokInsight("NVDA", "strong", "Momentum", 0.9, "2% equity", "grok")]
    path = export_to_csv(results, filename="test.csv", insights=insights, output_dir=tmp_path)

    with path.open(encoding="utf-8") as fh:
        row = next(csv.DictReader(fh))
    assert row["grok_verdict"] == "strong"
    assert row["grok_sizing"] == "2% equity"
    assert row["grok_source"] == "grok"


def test_heuristic_insights_valid_verdicts():
    picks = [ScanResult("X", 0.4, 0.05, 2.0, 50.0, 1e6, "test")]
    insights = _heuristic_insights(picks)
    assert insights[0].verdict in ("strong", "buy", "watch", "caution", "skip")


def test_apply_scanner_preserves_short_proxy():
    cfg = make_cfg()
    results = [
        ScanResult("SPY", 0.9, 0.08, 3.0, 450.0, 1e6, "test", "ETF", 0.025),
        ScanResult("NVDA", 0.8, 0.07, 2.5, 120.0, 5e5, "test", "STOCK", 0.05),
    ]
    universe = apply_scanner_to_universe(cfg, results, open_position_symbols=set())
    spy = next(i for i in universe if i.symbol == "SPY")
    assert spy.short_proxy == "SH"


def test_etf_gap_threshold_parses_percentage():
    from tradingbot.config import _parse_scanner_params

    params = _parse_scanner_params({"etf_gap_threshold": 2.5})
    assert params.etf_gap_threshold == pytest.approx(0.025)