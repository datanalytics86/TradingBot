"""Reporte de estado del bot, ejecutable como ``python3 -m tradingbot.report``.

Muestra, sin requerir intervención ni modificar nada:

- Modo (paper/live), equity actual y posiciones reales según el broker (si
  hay credenciales configuradas y alpaca-py instalado; si no, lo indica y
  sigue con la parte local).
- Estado local del bot (`state.json`): posiciones que el bot cree tener y
  sus stops.
- Las últimas 10 líneas de `equity_history.csv`, con la variación % entre
  filas consecutivas.
- El contenido del archivo `HALT`, si existe, bien visible.

Import perezoso de `alpaca-py` (dentro de `_report_broker`) para que este
módulo funcione siempre, esté o no instalada esa librería.
"""
from __future__ import annotations

import csv
import json

from tradingbot.config import EQUITY_HISTORY_FILE, HALT_FILE, STATE_FILE, Config, load_config


def _print_header(title: str) -> None:
    print("=" * 60)
    print(title)
    print("=" * 60)


def _report_broker(cfg: Config) -> None:
    print(f"Modo: {cfg.mode}")
    try:
        from tradingbot.broker import AlpacaBroker
    except ImportError as exc:
        print(f"alpaca-py no está instalado, no se puede consultar el broker ({exc}).")
        return

    try:
        broker = AlpacaBroker(paper=(cfg.mode == "paper"))
        equity = broker.get_equity()
        positions = broker.list_positions()
    except Exception as exc:  # noqa: BLE001 - cualquier fallo de credenciales/red no debe romper el reporte
        print(f"No se pudo consultar el broker (¿faltan credenciales?): {exc}")
        return

    print(f"Equity actual: {equity:,.2f}")
    if positions:
        print("Posiciones abiertas en el broker:")
        for symbol, qty in sorted(positions.items()):
            print(f"  {symbol}: {qty}")
    else:
        print("Sin posiciones abiertas en el broker.")


def _report_state() -> None:
    if not STATE_FILE.exists():
        print("No existe state.json todavía (el bot no ha corrido ningún ciclo).")
        return
    try:
        state = json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(f"No se pudo leer state.json: {exc}")
        return

    print(f"Peak equity registrado: {float(state.get('peak_equity', 0.0)):,.2f}")
    positions = state.get("positions", {}) or {}
    open_positions = {k: v for k, v in positions.items() if v.get("traded_symbol")}
    if not open_positions:
        print("Sin posiciones abiertas según el estado del bot.")
        return
    print("Posiciones según el estado del bot:")
    for symbol, info in open_positions.items():
        print(
            f"  {symbol}: opera {info.get('traded_symbol')} "
            f"(señal original={info.get('signal_direction')}, stop={info.get('stop')})"
        )


def _report_equity_history(n: int = 10) -> None:
    if not EQUITY_HISTORY_FILE.exists():
        print("Todavía no existe equity_history.csv (se crea al primer ciclo exitoso).")
        return
    with EQUITY_HISTORY_FILE.open(newline="") as fh:
        rows = list(csv.reader(fh))

    data_rows = rows[1:] if rows else []
    if not data_rows:
        print("equity_history.csv está vacío.")
        return

    last_rows = data_rows[-n:]
    prev_idx = len(data_rows) - len(last_rows) - 1
    prev_equity = float(data_rows[prev_idx][1]) if prev_idx >= 0 else None

    print(f"Últimas {len(last_rows)} entradas de equity_history.csv:")
    for fecha, equity_str, *_ in last_rows:
        equity = float(equity_str)
        if prev_equity is not None and prev_equity != 0:
            pct_str = f"{(equity / prev_equity - 1) * 100:+.2f}%"
        else:
            pct_str = "n/d"
        print(f"  {fecha}  {equity:,.2f}  ({pct_str})")
        prev_equity = equity


def _report_halt() -> None:
    if not HALT_FILE.exists():
        return
    print()
    print("!" * 60)
    print("BOT DETENIDO: existe el archivo HALT")
    print("!" * 60)
    print(HALT_FILE.read_text().strip())
    print("!" * 60)


def main() -> None:
    _print_header("REPORTE DEL BOT")

    try:
        cfg = load_config()
    except Exception as exc:  # noqa: BLE001
        print(f"No se pudo cargar config.yaml: {exc}")
        cfg = None

    if cfg is not None:
        _report_broker(cfg)

    print("-" * 60)
    _report_state()
    print("-" * 60)
    _report_equity_history()

    _report_halt()


if __name__ == "__main__":
    main()
