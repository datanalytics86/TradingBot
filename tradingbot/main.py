"""Ciclo diario en vivo/paper del bot.

Pensado para ejecutarse una vez al día vía cron, después del cierre del
mercado (ver README para un ejemplo de crontab). No requiere intervención
humana: si algo va mal (circuit breaker, falta de credenciales) se detiene
de forma segura y deja constancia en el archivo HALT o en la salida de
consola.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from tradingbot.broker import AlpacaBroker
from tradingbot.config import HALT_FILE, STATE_FILE, Config, Instrument, load_config
from tradingbot.data import fetch_daily
from tradingbot.risk import check_circuit_breaker, position_size
from tradingbot.strategy import compute_indicators, signal, trailing_stop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("tradingbot")


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("state.json corrupto o ilegible, se reinicia desde cero")
    return {"peak_equity": 0.0, "positions": {}}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def _write_halt(reason: str) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    HALT_FILE.write_text(f"{timestamp} - {reason}\n")


def _desired_target(inst: Instrument, sig: str) -> tuple[str, str] | tuple[None, None]:
    """Devuelve (symbol_a_operar, direccion) según la señal, o (None, None) si FLAT.

    LONG -> largo en el propio símbolo.
    SHORT -> largo en el ETF inverso (short_proxy), si existe; si no, FLAT.
    """
    if sig == "LONG":
        return inst.symbol, "LONG"
    if sig == "SHORT":
        if inst.short_proxy:
            return inst.short_proxy, "LONG"
        return None, None
    return None, None


def _process_instrument(inst: Instrument, cfg: Config, broker: AlpacaBroker, state: dict) -> None:
    log.info("Procesando %s", inst.symbol)
    df = fetch_daily(inst.symbol, days=cfg.lookback_days)
    df_ind = compute_indicators(df, cfg.strategy)
    sig = signal(df_ind, cfg.strategy)
    last = df_ind.iloc[-1]
    log.info("%s señal=%s close=%.2f atr=%.4f", inst.symbol, sig, last["Close"], last["atr"])

    target_symbol, target_direction = _desired_target(inst, sig)

    positions_state = state.setdefault("positions", {})
    sym_state = positions_state.get(inst.symbol, {"direction": None, "stop": None, "traded_symbol": None})

    current_traded_symbol = sym_state.get("traded_symbol")
    current_direction = sym_state.get("direction")

    # 1) Si hay una posición abierta actualmente (en el símbolo previamente operado),
    #    comprobar el trailing stop guardado y/o si el objetivo cambió.
    if current_traded_symbol:
        current_qty = broker.get_position(current_traded_symbol)
        if current_qty != 0:
            stop = sym_state.get("stop")
            # El stop y el trailing se calculan sobre el Close/ATR del
            # subyacente (inst.symbol), aunque la posición operada sea el
            # ETF inverso (short_proxy). Es la referencia de riesgo de la
            # señal, documentado en el README.
            close = last["Close"]
            stop_hit = stop is not None and (
                (current_direction == "LONG" and close <= stop)
            )
            same_target = target_symbol == current_traded_symbol and target_direction == current_direction

            if stop_hit or not same_target:
                reason = "stop" if stop_hit else "cambio de señal"
                log.info("Cerrando posición en %s (%s)", current_traded_symbol, reason)
                broker.close_position(current_traded_symbol)
                sym_state = {"direction": None, "stop": None, "traded_symbol": None}
            else:
                new_stop = trailing_stop(close, last["atr"], stop, current_direction, cfg.strategy.atr_stop_mult)
                sym_state["stop"] = new_stop
                log.info("Manteniendo posición en %s, nuevo stop=%.2f", current_traded_symbol, new_stop)
        else:
            # El broker no tiene la posición que creíamos tener: limpiar estado.
            sym_state = {"direction": None, "stop": None, "traded_symbol": None}

    # 2) Si tras el paso anterior no hay posición y el objetivo es LONG/SHORT, abrir.
    if sym_state.get("traded_symbol") is None and target_symbol is not None:
        equity = broker.get_equity()
        stop_distance = cfg.strategy.atr_stop_mult * last["atr"]
        qty = position_size(equity, last["Close"], stop_distance, cfg.risk)
        if qty > 0:
            log.info("Abriendo LONG en %s qty=%.3f", target_symbol, qty)
            broker.market_order(target_symbol, qty, "buy")
            new_stop = last["Close"] - cfg.strategy.atr_stop_mult * last["atr"]
            sym_state = {"direction": "LONG", "stop": new_stop, "traded_symbol": target_symbol}
        else:
            log.info("Señal %s en %s pero tamaño de posición calculado es 0, no se opera", sig, inst.symbol)

    positions_state[inst.symbol] = sym_state


def run_cycle() -> int:
    load_dotenv()

    if HALT_FILE.exists():
        reason = HALT_FILE.read_text().strip()
        log.error("Archivo HALT presente, el bot está detenido: %s", reason)
        return 1

    cfg = load_config()

    try:
        broker = AlpacaBroker(paper=(cfg.mode == "paper"))
    except RuntimeError as exc:
        log.error("No se pudo conectar con Alpaca: %s", exc)
        return 1
    except ImportError as exc:
        log.error("alpaca-py no está instalado: %s", exc)
        return 1

    equity = broker.get_equity()
    log.info("Equity actual: %.2f", equity)

    state = _load_state()
    peak_equity = max(state.get("peak_equity", 0.0), equity)
    state["peak_equity"] = peak_equity

    if check_circuit_breaker(equity, peak_equity, cfg.risk):
        reason = (
            f"Circuit breaker activado: equity={equity:.2f} cayó más de "
            f"{cfg.risk.max_drawdown * 100:.0f}% desde el máximo {peak_equity:.2f}"
        )
        log.error(reason)
        broker.close_all_positions()
        _write_halt(reason)
        _save_state(state)
        return 1

    errors = []
    for inst in cfg.universe:
        try:
            _process_instrument(inst, cfg, broker, state)
        except Exception as exc:  # noqa: BLE001 - no dejar que un símbolo aborte el ciclo
            log.exception("Error procesando %s: %s", inst.symbol, exc)
            errors.append((inst.symbol, str(exc)))

    _save_state(state)

    if errors:
        log.warning("Ciclo terminado con errores en: %s", ", ".join(s for s, _ in errors))
    else:
        log.info("Ciclo diario completado sin errores")

    return 0


def main() -> None:
    sys.exit(run_cycle())


if __name__ == "__main__":
    main()
