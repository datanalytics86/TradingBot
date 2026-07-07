"""Motor de backtest para la estrategia trend-following.

Ejecutable como::

    python3 -m tradingbot.backtest --start 2015-01-01
    python3 -m tradingbot.backtest --start 2018-01-01 --advanced

Incluye backtest clásico y avanzado (walk-forward, slippage variable,
métricas completas y reporte HTML con Plotly).
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from tradingbot.config import (
    BACKTEST_RESULTS_DIR,
    Config,
    RiskParams,
    StrategyParams,
    load_config,
)
from tradingbot.data import fetch_daily
from tradingbot.risk import apply_volatility_scaling, position_size
from tradingbot.strategy import compute_indicators, signal, trailing_stop, trend_strength

log = logging.getLogger("tradingbot")

DEFAULT_SLIPPAGE = 0.0005
TRADING_DAYS_PER_YEAR = 252


@dataclass
class BacktestMetrics:
    """Métricas completas de un backtest."""

    total_return: float
    cagr: float
    max_drawdown: float
    sharpe: float
    sortino: float
    calmar: float
    win_rate: float
    profit_factor: float
    n_trades: int
    avg_trade_pnl: float


def _slippage_for_bar(
    atr: float,
    atr_avg: float,
    slippage_min: float,
    slippage_max: float,
) -> float:
    """Slippage dinámico entre min y max según volatilidad relativa."""
    if atr_avg <= 0 or atr <= 0:
        return slippage_min
    vol_ratio = min(atr / atr_avg, 3.0) / 3.0
    return slippage_min + (slippage_max - slippage_min) * vol_ratio


def _simulate_symbol(
    df_ind: pd.DataFrame,
    initial_alloc: float,
    strategy: StrategyParams,
    risk: RiskParams,
    slippage: float = DEFAULT_SLIPPAGE,
    slippage_min: float | None = None,
    slippage_max: float | None = None,
) -> tuple[pd.Series, list[dict]]:
    """Simula un único símbolo y devuelve (equity_series, trades)."""
    required = ("ema_fast", "ema_slow", "sma_trend", "atr", "adx")
    valid_mask = df_ind[list(required)].notna().all(axis=1)
    if not valid_mask.any():
        return pd.Series(initial_alloc, index=df_ind.index), []

    start_idx = int(np.argmax(valid_mask.values))
    use_variable_slippage = slippage_min is not None and slippage_max is not None

    cash = initial_alloc
    position: Optional[dict] = None
    equity_values = [initial_alloc] * start_idx
    trades: list[dict] = []

    n = len(df_ind)
    for i in range(start_idx, n):
        row = df_ind.iloc[i]
        has_next = i + 1 < n
        next_open = df_ind.iloc[i + 1]["Open"] if has_next else None
        atr_now = float(row["atr"])
        atr_avg = float(row.get("atr_avg") or atr_now)
        slip = (
            _slippage_for_bar(atr_now, atr_avg, slippage_min, slippage_max)
            if use_variable_slippage
            else slippage
        )

        sig = signal(df_ind.iloc[: i + 1], strategy)

        exit_reason = None
        if position is not None:
            if position["direction"] == "LONG" and row["Close"] <= position["stop"]:
                exit_reason = "stop"
            elif position["direction"] == "SHORT" and row["Close"] >= position["stop"]:
                exit_reason = "stop"
            elif sig != position["direction"]:
                exit_reason = "signal"
            else:
                position["stop"] = trailing_stop(
                    row["Close"], row["atr"], position["stop"], position["direction"], strategy.atr_stop_mult
                )

        if exit_reason and has_next and position is not None:
            raw_price = next_open
            if position["direction"] == "LONG":
                exit_price = raw_price * (1 - slip)
                cash += position["qty"] * exit_price
                pnl = (exit_price - position["entry_price"]) * position["qty"]
            else:
                exit_price = raw_price * (1 + slip)
                cash -= position["qty"] * exit_price
                pnl = (position["entry_price"] - exit_price) * position["qty"]
            trades.append({
                "direction": position["direction"],
                "entry_date": position["entry_date"],
                "entry_price": position["entry_price"],
                "exit_date": df_ind.index[i + 1],
                "exit_price": exit_price,
                "qty": position["qty"],
                "pnl": pnl,
                "reason": exit_reason,
                "slippage": slip,
            })
            position = None

        if position is None and sig in ("LONG", "SHORT") and has_next:
            stop_distance = strategy.atr_stop_mult * atr_now
            raw_price = next_open
            qty = position_size(cash, raw_price, stop_distance, risk)
            qty = apply_volatility_scaling(qty, atr_now, atr_avg, strategy)
            strength = trend_strength(df_ind.iloc[: i + 1])
            floor = risk.strength_size_floor
            qty = round(qty * (floor + (1.0 - floor) * strength), 3)
            if qty > 0:
                if sig == "LONG":
                    entry_price = raw_price * (1 + slip)
                    cash -= qty * entry_price
                    stop = entry_price - strategy.atr_stop_mult * atr_now
                else:
                    entry_price = raw_price * (1 - slip)
                    cash += qty * entry_price
                    stop = entry_price + strategy.atr_stop_mult * atr_now
                position = {
                    "direction": sig,
                    "qty": qty,
                    "entry_price": entry_price,
                    "stop": stop,
                    "entry_date": df_ind.index[i + 1],
                }

        if position is not None:
            mtm = (
                cash + position["qty"] * row["Close"]
                if position["direction"] == "LONG"
                else cash - position["qty"] * row["Close"]
            )
        else:
            mtm = cash
        equity_values.append(mtm)

    return pd.Series(equity_values, index=df_ind.index), trades


def _build_equity_curve(
    cfg: Config,
    start: str,
    end: str | None = None,
    slippage_min: float | None = None,
    slippage_max: float | None = None,
) -> tuple[pd.Series, list[dict]]:
    """Construye curva de equity combinada y lista de trades."""
    alloc = cfg.initial_capital / len(cfg.universe)
    equity_by_symbol: dict[str, pd.Series] = {}
    all_trades: list[dict] = []

    for inst in cfg.universe:
        df = fetch_daily(inst.symbol, start=start, validate_freshness=False)
        if end:
            df = df[df.index <= pd.Timestamp(end)]
        df_ind = compute_indicators(df, cfg.strategy)
        eq_series, trades = _simulate_symbol(
            df_ind, alloc, cfg.strategy, cfg.risk,
            slippage_min=slippage_min, slippage_max=slippage_max,
        )
        equity_by_symbol[inst.symbol] = eq_series
        for t in trades:
            t["symbol"] = inst.symbol
        all_trades.extend(trades)

    combined = pd.concat(equity_by_symbol, axis=1).sort_index()
    combined = combined.ffill().bfill()
    equity_curve = combined.sum(axis=1)
    all_trades.sort(key=lambda t: t["exit_date"])
    return equity_curve, all_trades


def compute_full_metrics(equity_curve: pd.Series, trades: list[dict]) -> BacktestMetrics:
    """Calcula métricas completas: Sharpe, Sortino, Calmar, PF, etc."""
    start_equity = float(equity_curve.iloc[0])
    end_equity = float(equity_curve.iloc[-1])
    total_return = end_equity / start_equity - 1 if start_equity > 0 else 0.0

    days = (equity_curve.index[-1] - equity_curve.index[0]).days
    years = days / 365.25 if days > 0 else 0.0
    cagr = (end_equity / start_equity) ** (1 / years) - 1 if years > 0 and start_equity > 0 and end_equity > 0 else 0.0

    running_max = equity_curve.cummax()
    drawdown = equity_curve / running_max - 1
    max_drawdown = float(drawdown.min()) if len(drawdown) else 0.0

    daily_returns = equity_curve.pct_change().dropna()
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = float(daily_returns.mean() / daily_returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR))
    else:
        sharpe = 0.0

    downside = daily_returns[daily_returns < 0]
    if len(downside) > 1 and downside.std() > 0:
        sortino = float(daily_returns.mean() / downside.std() * np.sqrt(TRADING_DAYS_PER_YEAR))
    else:
        sortino = sharpe

    calmar = cagr / abs(max_drawdown) if max_drawdown < 0 else 0.0

    n_trades = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    win_rate = len(wins) / n_trades if n_trades else 0.0
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0
    avg_trade_pnl = sum(t["pnl"] for t in trades) / n_trades if n_trades else 0.0

    return BacktestMetrics(
        total_return=total_return,
        cagr=cagr,
        max_drawdown=max_drawdown,
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        win_rate=win_rate,
        profit_factor=profit_factor,
        n_trades=n_trades,
        avg_trade_pnl=avg_trade_pnl,
    )


def walk_forward_analysis(cfg: Config, start: str) -> list[dict]:
    """Walk-forward: entrena en ventana larga, evalúa en ventana corta, avanza."""
    bt = cfg.backtest
    equity_curve, _ = _build_equity_curve(cfg, start)
    if len(equity_curve) < bt.walk_forward_train_days + bt.walk_forward_test_days:
        log.warning("Datos insuficientes para walk-forward")
        return []

    results: list[dict] = []
    idx = bt.walk_forward_train_days
    fold = 0

    while idx + bt.walk_forward_test_days < len(equity_curve):
        fold += 1
        train_end = equity_curve.index[idx - 1]
        test_start = equity_curve.index[idx]
        test_end = equity_curve.index[min(idx + bt.walk_forward_test_days - 1, len(equity_curve) - 1)]

        test_slice = equity_curve.iloc[idx: idx + bt.walk_forward_test_days]
        if len(test_slice) < 2:
            break

        m = compute_full_metrics(test_slice, [])
        results.append({
            "fold": fold,
            "train_end": str(train_end.date()),
            "test_start": str(test_start.date()),
            "test_end": str(test_end.date()),
            "test_return": float(test_slice.iloc[-1] / test_slice.iloc[0] - 1),
            "test_sharpe": m.sharpe,
            "test_max_dd": m.max_drawdown,
        })
        idx += bt.walk_forward_test_days

    return results


def format_metrics_table(metrics: BacktestMetrics) -> str:
    """Tabla ASCII con todas las métricas."""
    pf = f"{metrics.profit_factor:.2f}" if metrics.profit_factor != float("inf") else "∞"
    lines = [
        "+" + "-" * 44 + "+",
        "|{:^44}|".format("MÉTRICAS DEL BACKTEST"),
        "+" + "-" * 44 + "+",
        f"| Retorno total     | {metrics.total_return * 100:>+10.2f}%     |",
        f"| CAGR              | {metrics.cagr * 100:>+10.2f}%     |",
        f"| Max drawdown      | {metrics.max_drawdown * 100:>+10.2f}%     |",
        f"| Sharpe            | {metrics.sharpe:>10.2f}       |",
        f"| Sortino           | {metrics.sortino:>10.2f}       |",
        f"| Calmar            | {metrics.calmar:>10.2f}       |",
        f"| Win rate          | {metrics.win_rate * 100:>10.1f}%     |",
        f"| Profit factor     | {pf:>10}       |",
        f"| Trades            | {metrics.n_trades:>10}       |",
        f"| Avg trade PnL     | {metrics.avg_trade_pnl:>+10.2f}       |",
        "+" + "-" * 44 + "+",
    ]
    return "\n".join(lines)


def generate_backtest_html(
    equity_curve: pd.Series,
    metrics: BacktestMetrics,
    trades: list[dict],
    walk_forward: list[dict],
    output_path: Path | None = None,
) -> Path | None:
    """Genera reporte HTML con gráficos Plotly (equity + drawdown)."""
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        log.warning("plotly no instalado; omitiendo gráficos HTML")
        return None

    out = output_path or BACKTEST_RESULTS_DIR / "backtest_report.html"
    out.parent.mkdir(parents=True, exist_ok=True)

    running_max = equity_curve.cummax()
    drawdown = (equity_curve / running_max - 1) * 100

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=("Curva de equity", "Drawdown (%)"),
        row_heights=[0.65, 0.35],
    )
    fig.add_trace(
        go.Scatter(x=equity_curve.index, y=equity_curve.values, name="Equity",
                   line=dict(color="#3b82f6", width=2)),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=drawdown.index, y=drawdown.values, name="Drawdown",
                   fill="tozeroy", line=dict(color="#ef4444")),
        row=2, col=1,
    )
    fig.update_layout(
        template="plotly_dark",
        title=f"Backtest avanzado — CAGR {metrics.cagr * 100:.2f}% | Sharpe {metrics.sharpe:.2f}",
        height=600,
        margin=dict(l=50, r=30, t=60, b=40),
    )

    chart_html = fig.to_html(full_html=False, include_plotlyjs="cdn")
    wf_rows = "".join(
        f"<tr><td>{w['fold']}</td><td>{w['test_start']}</td><td>{w['test_end']}</td>"
        f"<td>{w['test_return'] * 100:+.2f}%</td><td>{w['test_sharpe']:.2f}</td></tr>"
        for w in walk_forward
    ) or '<tr><td colspan="5" class="muted">Sin folds walk-forward</td></tr>'

    html = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"/>
<title>Backtest — TradingBot</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 1.5rem; }}
  .wrap {{ max-width: 1100px; margin: 0 auto; }}
  pre {{ background: #1e293b; padding: 1rem; border-radius: 8px; font-size: 0.85rem; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; }}
  th, td {{ padding: 0.5rem; border-bottom: 1px solid #334155; text-align: left; }}
  th {{ color: #94a3b8; }}
  .muted {{ color: #94a3b8; }}
</style></head><body><div class="wrap">
<h1>Backtest avanzado</h1>
<p class="muted">Generado {datetime.now(timezone.utc).isoformat()}</p>
{chart_html}
<pre>{format_metrics_table(metrics)}</pre>
<h2>Walk-forward</h2>
<table><thead><tr><th>Fold</th><th>Inicio test</th><th>Fin test</th><th>Retorno</th><th>Sharpe</th></tr></thead>
<tbody>{wf_rows}</tbody></table>
<p class="muted">Últimos 5 trades: {len(trades)} total</p>
</div></body></html>"""

    out.write_text(html, encoding="utf-8")
    log.info("Reporte HTML backtest: %s", out)
    return out


def run_backtest(cfg: Config, start: str) -> tuple[pd.Series, list[dict]]:
    """Backtest clásico (compatible con versión anterior)."""
    equity_curve, all_trades = _build_equity_curve(cfg, start)
    metrics = compute_full_metrics(equity_curve, all_trades)
    _print_report(equity_curve, all_trades, metrics)
    return equity_curve, all_trades


def run_advanced_backtest(cfg: Config, start: str) -> dict:
    """Backtest avanzado con slippage variable, walk-forward y reporte Plotly."""
    bt = cfg.backtest
    log.info(
        "Backtest avanzado: slippage %.3f%%-%.3f%%, walk-forward %d/%d días",
        bt.slippage_min * 100, bt.slippage_max * 100,
        bt.walk_forward_train_days, bt.walk_forward_test_days,
    )

    equity_curve, trades = _build_equity_curve(
        cfg, start,
        slippage_min=bt.slippage_min,
        slippage_max=bt.slippage_max,
    )
    metrics = compute_full_metrics(equity_curve, trades)
    wf = walk_forward_analysis(cfg, start)

    print(format_metrics_table(metrics))
    if wf:
        print("\nWalk-forward (OOS):")
        print(f"  {'Fold':>4}  {'Test':>23}  {'Retorno':>8}  {'Sharpe':>7}")
        for w in wf:
            print(
                f"  {w['fold']:>4}  {w['test_start']} → {w['test_end']}  "
                f"{w['test_return'] * 100:>+7.2f}%  {w['test_sharpe']:>7.2f}"
            )

    html_path = generate_backtest_html(equity_curve, metrics, trades, wf)
    BACKTEST_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = BACKTEST_RESULTS_DIR / f"backtest_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "period": {"start": str(equity_curve.index[0].date()), "end": str(equity_curve.index[-1].date())},
        "metrics": asdict(metrics),
        "walk_forward": wf,
        "html_report": str(html_path) if html_path else None,
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nJSON guardado: {json_path}")
    if html_path:
        print(f"HTML guardado: {html_path}")

    return payload


def _metrics(equity_curve: pd.Series) -> dict:
    """Compatibilidad con código/tests que usan el dict simple."""
    m = compute_full_metrics(equity_curve, [])
    return {
        "total_return": m.total_return,
        "cagr": m.cagr,
        "max_drawdown": m.max_drawdown,
        "sharpe": m.sharpe,
    }


def _print_report(equity_curve: pd.Series, trades: list[dict], metrics: BacktestMetrics | None = None) -> None:
    m = metrics or compute_full_metrics(equity_curve, trades)
    print("=" * 60)
    print("RESULTADOS DEL BACKTEST")
    print("=" * 60)
    print(f"Periodo:            {equity_curve.index[0].date()} -> {equity_curve.index[-1].date()}")
    print(f"Equity inicial:     {equity_curve.iloc[0]:,.2f}")
    print(f"Equity final:       {equity_curve.iloc[-1]:,.2f}")
    print(format_metrics_table(m))
    print("-" * 60)
    print("Ultimas 5 operaciones:")
    for t in trades[-5:]:
        print(
            f"  {t['symbol']:>5} {t['direction']:>5}  "
            f"entrada {t['entry_date'].date()} @ {t['entry_price']:.2f}  ->  "
            f"salida {t['exit_date'].date()} @ {t['exit_price']:.2f}  "
            f"qty={t['qty']:.3f}  pnl={t['pnl']:+.2f}  ({t['reason']})"
        )
    print("=" * 60)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Backtest de la estrategia trend-following")
    parser.add_argument("--start", default="2015-01-01", help="Fecha de inicio (YYYY-MM-DD)")
    parser.add_argument("--config", default=None, help="Ruta a config.yaml")
    parser.add_argument("--advanced", action="store_true", help="Backtest avanzado con walk-forward y Plotly")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.advanced:
        run_advanced_backtest(cfg, args.start)
    else:
        run_backtest(cfg, args.start)


if __name__ == "__main__":
    main()