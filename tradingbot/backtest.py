"""Motor de backtest simple y honesto para la estrategia trend-following.

Ejecutable como::

    python3 -m tradingbot.backtest --start 2015-01-01 [--config config.yaml]

Simplificaciones documentadas:

- El universo real opera ETFs inversos (short_proxy) para tomar exposición
  corta, porque con una cuenta cash de USD 500 no se pueden hacer shorts
  directos de acciones. En el backtest, por simplicidad y porque no siempre
  hay historia larga y limpia del ETF inverso, la señal SHORT se modela
  como una posición corta sobre el MISMO símbolo subyacente. Económicamente
  es similar a estar largo en el ETF inverso diario (mismo signo de
  exposición al mercado, con algo de tracking error / decaimiento por
  rebalanceo diario que el backtest no captura).
- Las órdenes se ejecutan al Open de la barra SIGUIENTE a la señal (nunca al
  Close de la misma barra en la que se generó la señal) para evitar
  look-ahead bias.
- Comisión de 0 (consistente con Alpaca) pero con un slippage configurable
  aplicado a cada ejecución (por defecto 0.05% por lado, en contra de quien
  opera).
"""
from __future__ import annotations

import argparse
from typing import Optional

import numpy as np
import pandas as pd

from tradingbot.config import Config, RiskParams, StrategyParams, load_config
from tradingbot.data import fetch_daily
from tradingbot.risk import position_size
from tradingbot.strategy import compute_indicators, signal, trailing_stop

DEFAULT_SLIPPAGE = 0.0005  # 0.05% por lado
TRADING_DAYS_PER_YEAR = 252


def _simulate_symbol(
    df_ind: pd.DataFrame,
    initial_alloc: float,
    strategy: StrategyParams,
    risk: RiskParams,
    slippage: float = DEFAULT_SLIPPAGE,
) -> tuple[pd.Series, list[dict]]:
    """Simula un único símbolo y devuelve (equity_series, trades).

    `equity_series` cubre todo el índice de `df_ind`: antes de que los
    indicadores estén completos, el capital asignado permanece en cash
    (sin invertir).
    """
    required = ("ema_fast", "ema_slow", "sma_trend", "atr", "adx")
    valid_mask = df_ind[list(required)].notna().all(axis=1)
    if not valid_mask.any():
        return pd.Series(initial_alloc, index=df_ind.index), []

    start_idx = int(np.argmax(valid_mask.values))

    cash = initial_alloc
    position: Optional[dict] = None
    equity_values = [initial_alloc] * start_idx
    trades: list[dict] = []

    n = len(df_ind)
    for i in range(start_idx, n):
        row = df_ind.iloc[i]
        date = df_ind.index[i]
        has_next = i + 1 < n
        next_open = df_ind.iloc[i + 1]["Open"] if has_next else None

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
                exit_price = raw_price * (1 - slippage)
                cash += position["qty"] * exit_price
                pnl = (exit_price - position["entry_price"]) * position["qty"]
            else:
                exit_price = raw_price * (1 + slippage)
                cash -= position["qty"] * exit_price
                pnl = (position["entry_price"] - exit_price) * position["qty"]
            trades.append(
                {
                    "direction": position["direction"],
                    "entry_date": position["entry_date"],
                    "entry_price": position["entry_price"],
                    "exit_date": df_ind.index[i + 1],
                    "exit_price": exit_price,
                    "qty": position["qty"],
                    "pnl": pnl,
                    "reason": exit_reason,
                }
            )
            position = None

        if position is None and sig in ("LONG", "SHORT") and has_next:
            atr_now = row["atr"]
            stop_distance = strategy.atr_stop_mult * atr_now
            raw_price = next_open
            qty = position_size(cash, raw_price, stop_distance, risk)
            if qty > 0:
                if sig == "LONG":
                    entry_price = raw_price * (1 + slippage)
                    cash -= qty * entry_price
                    stop = entry_price - strategy.atr_stop_mult * atr_now
                else:
                    entry_price = raw_price * (1 - slippage)
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

    equity_series = pd.Series(equity_values, index=df_ind.index)
    return equity_series, trades


def run_backtest(cfg: Config, start: str) -> tuple[pd.Series, list[dict]]:
    """Corre el backtest sobre todo el universo de `cfg` desde `start`.

    Divide `cfg.initial_capital` en partes iguales entre los símbolos del
    universo y simula cada uno de forma independiente. Devuelve la curva de
    equity combinada (suma de las curvas por símbolo) y la lista combinada
    de operaciones (con el símbolo incluido en cada una).
    """
    symbols = [inst.symbol for inst in cfg.universe]
    if not symbols:
        raise ValueError("El universo está vacío")
    alloc = cfg.initial_capital / len(symbols)

    equity_by_symbol: dict[str, pd.Series] = {}
    all_trades: list[dict] = []

    for inst in cfg.universe:
        df = fetch_daily(inst.symbol, start=start, validate_freshness=False)
        df_ind = compute_indicators(df, cfg.strategy)
        eq_series, trades = _simulate_symbol(df_ind, alloc, cfg.strategy, cfg.risk)
        equity_by_symbol[inst.symbol] = eq_series
        for t in trades:
            t["symbol"] = inst.symbol
        all_trades.extend(trades)

    combined = pd.concat(equity_by_symbol, axis=1).sort_index()
    combined = combined.ffill().bfill()
    equity_curve = combined.sum(axis=1)
    all_trades.sort(key=lambda t: t["exit_date"])

    _print_report(equity_curve, all_trades)
    return equity_curve, all_trades


def _metrics(equity_curve: pd.Series) -> dict:
    start_equity = float(equity_curve.iloc[0])
    end_equity = float(equity_curve.iloc[-1])
    total_return = end_equity / start_equity - 1 if start_equity > 0 else 0.0

    days = (equity_curve.index[-1] - equity_curve.index[0]).days
    years = days / 365.25 if days > 0 else 0.0
    if years > 0 and start_equity > 0 and end_equity > 0:
        cagr = (end_equity / start_equity) ** (1 / years) - 1
    else:
        cagr = 0.0

    running_max = equity_curve.cummax()
    drawdown = equity_curve / running_max - 1
    max_drawdown = float(drawdown.min()) if len(drawdown) else 0.0

    daily_returns = equity_curve.pct_change().dropna()
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = float(daily_returns.mean() / daily_returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR))
    else:
        sharpe = 0.0

    return {
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
    }


def _print_report(equity_curve: pd.Series, trades: list[dict]) -> None:
    m = _metrics(equity_curve)
    n_trades = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    win_rate = len(wins) / n_trades if n_trades else 0.0

    print("=" * 60)
    print("RESULTADOS DEL BACKTEST")
    print("=" * 60)
    print(f"Periodo:            {equity_curve.index[0].date()} -> {equity_curve.index[-1].date()}")
    print(f"Equity inicial:     {equity_curve.iloc[0]:,.2f}")
    print(f"Equity final:       {equity_curve.iloc[-1]:,.2f}")
    print(f"Retorno total:      {m['total_return'] * 100:.2f}%")
    print(f"CAGR:               {m['cagr'] * 100:.2f}%")
    print(f"Max drawdown:       {m['max_drawdown'] * 100:.2f}%")
    print(f"Sharpe (anualizado):{m['sharpe']:.2f}")
    print(f"Numero de trades:   {n_trades}")
    print(f"Win rate:           {win_rate * 100:.1f}%")
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
    parser = argparse.ArgumentParser(description="Backtest de la estrategia trend-following")
    parser.add_argument("--start", default="2015-01-01", help="Fecha de inicio (YYYY-MM-DD)")
    parser.add_argument("--config", default=None, help="Ruta a config.yaml (default: config.yaml del repo)")
    args = parser.parse_args()

    cfg = load_config(args.config)
    run_backtest(cfg, args.start)


if __name__ == "__main__":
    main()
