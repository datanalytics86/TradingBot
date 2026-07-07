"""Optimización simple de parámetros por grid search.

Prueba combinaciones de EMA, ATR multiplier y gap thresholds; guarda
los mejores resultados en ``optimization_results.json``.

Uso::

    python scripts/optimize_params.py --start 2018-01-01
    python scripts/optimize_params.py --start 2018-01-01 --quick
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from copy import deepcopy
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tradingbot.backtest import _build_equity_curve, compute_full_metrics
from tradingbot.config import OPTIMIZATION_RESULTS_FILE, load_config

log = logging.getLogger("tradingbot")


def _score_metrics(metrics) -> float:
    """Score compuesto para rankear combinaciones (mayor = mejor)."""
    pf = min(metrics.profit_factor, 5.0) if metrics.profit_factor != float("inf") else 5.0
    return (
        metrics.sharpe * 0.35
        + metrics.calmar * 0.25
        + metrics.win_rate * 0.15
        + (pf / 5.0) * 0.15
        + metrics.cagr * 0.10
    )


def run_optimization(start: str, config_path: str | None, quick: bool) -> dict:
    cfg = load_config(config_path)

    ema_fast_opts = [15, 20] if quick else [15, 20, 25]
    ema_slow_opts = [40, 50] if quick else [40, 50, 60]
    atr_opts = [2.0, 2.5, 3.0] if not quick else [2.0, 2.5]
    gap_opts = [0.03, 0.05] if quick else [0.03, 0.05, 0.07]

    results: list[dict] = []
    combos = list(product(ema_fast_opts, ema_slow_opts, atr_opts, gap_opts))
    log.info("Evaluando %d combinaciones...", len(combos))

    for ema_f, ema_s, atr_m, gap in combos:
        if ema_f >= ema_s:
            continue
        trial = deepcopy(cfg)
        trial.strategy.ema_fast = ema_f
        trial.strategy.ema_slow = ema_s
        trial.strategy.atr_stop_mult = atr_m
        trial.scanner.gap_threshold = gap

        try:
            equity, trades = _build_equity_curve(trial, start)
            if equity.empty or len(equity) < 50:
                continue
            metrics = compute_full_metrics(equity, trades)
            results.append({
                "params": {
                    "ema_fast": ema_f,
                    "ema_slow": ema_s,
                    "atr_stop_mult": atr_m,
                    "gap_threshold": gap,
                },
                "score": _score_metrics(metrics),
                "sharpe": metrics.sharpe,
                "cagr": metrics.cagr,
                "max_drawdown": metrics.max_drawdown,
                "profit_factor": metrics.profit_factor if metrics.profit_factor != float("inf") else 99.0,
                "n_trades": metrics.n_trades,
            })
        except Exception as exc:  # noqa: BLE001
            log.debug("Combo %s falló: %s", (ema_f, ema_s, atr_m, gap), exc)

    results.sort(key=lambda r: r["score"], reverse=True)
    top = results[:10]

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "start_date": start,
        "combinations_tested": len(results),
        "best": top[0] if top else None,
        "top_10": top,
    }
    OPTIMIZATION_RESULTS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log.info("Resultados guardados en %s", OPTIMIZATION_RESULTS_FILE)

    if top:
        b = top[0]
        print("\n=== MEJOR COMBINACIÓN ===")
        print(f"  Params:  {b['params']}")
        print(f"  Score:   {b['score']:.4f}")
        print(f"  Sharpe:  {b['sharpe']:.2f}")
        print(f"  CAGR:    {b['cagr'] * 100:+.2f}%")
        print(f"  Max DD:  {b['max_drawdown'] * 100:.2f}%")
    else:
        print("Sin resultados válidos.")

    return payload


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Optimización de parámetros (grid search)")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--config", default=None)
    parser.add_argument("--quick", action="store_true", help="Grid reducido para prueba rápida")
    args = parser.parse_args()
    run_optimization(args.start, args.config, args.quick)


if __name__ == "__main__":
    main()