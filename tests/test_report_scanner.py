"""Tests de la sección Scanner Results en report.py (sin red)."""
from __future__ import annotations

import json

from tradingbot import report
from tradingbot.scanner import GrokInsight, ScanResult


def test_format_scanner_table_renders_rows():
    results = [
        ScanResult("NVDA", 0.85, 0.08, 3.0, 120.0, 1e6, "polygon", "STOCK", 0.05),
    ]
    insights = [GrokInsight("NVDA", "strong", "Momentum fuerte", 0.82, "2% equity", "grok")]
    table = report.format_scanner_table(results, insights)
    assert "NVDA" in table
    assert "strong" in table
    assert "0.850" in table


def test_report_scanner_results_from_file(capsys, tmp_path, monkeypatch):
    scanner_file = tmp_path / "last_scanner.json"
    scanner_file.write_text(
        json.dumps({
            "timestamp": "2026-07-07T12:00:00+00:00",
            "csv_path": "scanner_results/test.csv",
            "results": [{
                "symbol": "TSLA",
                "score": 0.75,
                "gap_pct": 0.07,
                "volume_ratio": 2.8,
                "price": 250.0,
                "premarket_volume": 1e6,
                "source": "polygon",
                "asset_type": "STOCK",
                "gap_threshold_used": 0.05,
            }],
            "insights": [{
                "symbol": "TSLA",
                "verdict": "watch",
                "summary": "Gap notable",
                "confidence": 0.65,
            }],
        })
    )
    monkeypatch.setattr(report, "LAST_SCANNER_FILE", scanner_file)

    report._report_scanner_results()
    out = capsys.readouterr().out
    assert "TSLA" in out
    assert "watch" in out
    assert "test.csv" in out
    assert "ANÁLISIS GROK" in out or "Veredicto" in out


def test_generate_scanner_html(tmp_path):
    results = [ScanResult("AMD", 0.6, 0.06, 2.2, 150.0, 8e5, "yfinance", "STOCK", 0.05)]
    path = report.generate_scanner_html(results, output_path=tmp_path / "scan.html")
    html = path.read_text(encoding="utf-8")
    assert "AMD" in html
    assert "<table>" in html