"""Ciclo diario en vivo/paper del bot.

Pensado para ejecutarse una vez al día vía cron o GitHub Actions, después
del cierre del mercado (ver README). No requiere intervención humana: si
algo va mal (circuit breaker, falta de credenciales, datos obsoletos) se
detiene de forma segura y deja constancia en el archivo HALT, en los logs
(consola + `logs/bot.log`) y, si está configurado, en Telegram.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv

from tradingbot.broker import AlpacaBroker
from tradingbot.config import (
    EQUITY_HISTORY_FILE,
    HALT_FILE,
    LAST_RUN_FILE,
    LOG_FILE,
    STATE_FILE,
    Config,
    Instrument,
    load_config,
)
from tradingbot.data import fetch_daily
from tradingbot.notify import send_telegram
from tradingbot.risk import (
    apply_volatility_scaling,
    cap_by_total_exposure,
    check_circuit_breaker,
    position_size,
)
from tradingbot.strategy import compute_indicators, signal, trailing_stop, trend_strength

log = logging.getLogger("tradingbot")


def _setup_logging() -> None:
    """Configura logging a consola y a `logs/bot.log` (rotativo, 1MB x 3 backups).

    Idempotente: si el logger ya tiene handlers (ej. porque `run_cycle` se
    llamó más de una vez en el mismo proceso, como en los tests) no los
    duplica.
    """
    if log.handlers:
        return

    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    log.addHandler(console)

    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3)
        file_handler.setFormatter(fmt)
        log.addHandler(file_handler)
    except OSError as exc:  # noqa: BLE001 - sin logs a archivo el bot debe poder seguir
        log.warning("No se pudo configurar el log a archivo (%s): %s", LOG_FILE, exc)


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("state.json corrupto o ilegible, se reinicia desde cero")
    return {"peak_equity": 0.0, "positions": {}}


def _save_state(state: dict) -> None:
    """Escribe state.json de forma atómica (escribe a un temporal + os.replace).

    Evita dejar el archivo corrupto/truncado si el proceso se interrumpe a
    mitad de la escritura (ej. kill, corte de luz en el runner de CI).
    """
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
    tmp_path.write_text(json.dumps(state, indent=2, default=str))
    os.replace(tmp_path, STATE_FILE)


def _write_halt(reason: str) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    HALT_FILE.write_text(f"{timestamp} - {reason}\n")


def _append_equity_history(equity: float) -> None:
    """Appendea `fecha_iso,equity` a equity_history.csv (crea con header si no existe)."""
    try:
        EQUITY_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        is_new = not EQUITY_HISTORY_FILE.exists()
        with EQUITY_HISTORY_FILE.open("a", newline="") as fh:
            if is_new:
                fh.write("fecha,equity\n")
            fh.write(f"{datetime.now(timezone.utc).isoformat()},{equity:.2f}\n")
    except OSError as exc:  # noqa: BLE001 - no bloquear el ciclo por esto
        log.warning("No se pudo escribir equity_history.csv: %s", exc)


def _notify_safe(text: str) -> None:
    """Envía una notificación de Telegram sin dejar jamás que un fallo rompa el ciclo."""
    try:
        send_telegram(text)
    except Exception as exc:  # noqa: BLE001 - blindaje extra, send_telegram ya no lanza
        log.warning("Fallo inesperado notificando por Telegram: %s", exc)


def _fetch_daily(symbol: str, cfg: Config, **kwargs) -> object:
    """Wrapper de fetch_daily con reintentos configurados en config.yaml."""
    return fetch_daily(
        symbol,
        days=cfg.lookback_days,
        retries=cfg.data.retries,
        retry_delay_seconds=cfg.data.retry_delay_seconds,
        **kwargs,
    )


def _estimate_exposure(broker: AlpacaBroker, price_cache: dict[str, float]) -> float:
    """Estima el valor nocional total de las posiciones abiertas."""
    try:
        positions = broker.list_positions()
    except Exception:  # noqa: BLE001
        return 0.0
    total = 0.0
    for sym, qty in positions.items():
        if qty == 0:
            continue
        price = price_cache.get(sym)
        if price and price > 0:
            total += abs(qty) * price
    return total


def _rank_instruments(instruments: list[Instrument], cfg: Config, adopted: set[str]) -> list[Instrument]:
    """Ordena instrumentos por fuerza de tendencia (los más fuertes primero)."""
    ranked: list[tuple[float, Instrument]] = []
    for inst in instruments:
        if inst.symbol in adopted:
            continue
        try:
            df = _fetch_daily(inst.symbol, cfg)
            df_ind = compute_indicators(df, cfg.strategy)
            ranked.append((trend_strength(df_ind), inst))
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo rankear %s: %s — se procesará al final", inst.symbol, exc)
            ranked.append((0.0, inst))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [inst for _, inst in ranked]


def _write_last_run(status: str, equity: float, errors: list[tuple[str, str]], summaries: list[dict]) -> None:
    """Registra el resultado del último ciclo en last_run.json (monitoreo automático)."""
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "equity": round(equity, 2),
        "errors": [{"symbol": s, "message": m} for s, m in errors],
        "summaries": summaries,
    }
    try:
        LAST_RUN_FILE.write_text(json.dumps(payload, indent=2, default=str))
    except OSError as exc:  # noqa: BLE001
        log.warning("No se pudo escribir last_run.json: %s", exc)


def _desired_target(inst: Instrument, sig: str) -> tuple[str, str] | tuple[None, None]:
    """Devuelve (symbol_a_operar, señal_original) según la señal, o (None, None) si FLAT.

    LONG -> largo en el propio símbolo, señal "LONG".
    SHORT -> largo en el ETF inverso (short_proxy), señal "SHORT"; si no hay
    proxy configurado, se trata como FLAT (no hay forma de tomar exposición
    corta con esta cuenta).
    """
    if sig == "LONG":
        return inst.symbol, "LONG"
    if sig == "SHORT":
        if inst.short_proxy:
            return inst.short_proxy, "SHORT"
        return None, None
    return None, None


def _reference_data(
    symbol: str,
    inst_symbol: str,
    df_ind,
    last,
    cfg: Config,
) -> tuple[float, float]:
    """Devuelve (close, atr) DEL SÍMBOLO REALMENTE OPERADO (`symbol`).

    Si `symbol` es el propio subyacente (`inst_symbol`), reutiliza los datos
    ya descargados/calculados (`df_ind`/`last`). Si `symbol` es el ETF
    inverso (proxy), descarga y calcula sus propios indicadores: el sizing y
    el stop/trailing de una posición en el proxy deben calcularse sobre el
    precio y ATR DEL PROXY (la orden compra el proxy, no el subyacente), no
    sobre el subyacente.
    """
    if symbol == inst_symbol:
        return float(last["Close"]), float(last["atr"])

    proxy_df = _fetch_daily(symbol, cfg)
    proxy_ind = compute_indicators(proxy_df, cfg.strategy)
    proxy_last = proxy_ind.iloc[-1]
    return float(proxy_last["Close"]), float(proxy_last["atr"])


def _empty_sym_state() -> dict:
    return {"direction": None, "stop": None, "traded_symbol": None, "signal_direction": None}


def _process_instrument(
    inst: Instrument,
    cfg: Config,
    broker: AlpacaBroker,
    state: dict,
    exposure_state: dict[str, float],
    price_cache: dict[str, float],
) -> dict:
    """Procesa un instrumento del universo: calcula señal, gestiona stop/cierre y abre si corresponde.

    Devuelve un pequeño resumen (dict) usado para el reporte/notificación del ciclo.
    """
    log.info("Procesando %s", inst.symbol)
    df = _fetch_daily(inst.symbol, cfg)
    df_ind = compute_indicators(df, cfg.strategy)
    sig = signal(df_ind, cfg.strategy)
    last = df_ind.iloc[-1]
    strength = trend_strength(df_ind)
    price_cache[inst.symbol] = float(last["Close"])
    log.info(
        "%s señal=%s close=%.2f atr=%.4f adx=%.1f fuerza=%.3f",
        inst.symbol, sig, last["Close"], last["atr"],
        float(last.get("adx", 0) or 0), strength,
    )

    summary = {"symbol": inst.symbol, "signal": sig, "action": "ninguna"}

    # target_symbol es el símbolo que efectivamente debe operarse (el propio
    # subyacente si LONG, el ETF inverso si SHORT); target_signal es la señal
    # ORIGINAL ("LONG"/"SHORT") que lo originó. La posición del broker
    # siempre se abre en largo (comprando target_symbol), incluso para
    # tomar exposición corta vía el proxy inverso.
    target_symbol, target_signal = _desired_target(inst, sig)

    positions_state = state.setdefault("positions", {})
    sym_state = positions_state.get(inst.symbol, _empty_sym_state())

    current_traded_symbol = sym_state.get("traded_symbol")
    current_signal_direction = sym_state.get("signal_direction")

    # 1) Si hay una posición abierta actualmente, comprobar el trailing stop
    #    guardado y/o si el objetivo cambió (incluye la transición
    #    LONG -> SHORT: si cambia, se cierra aquí y se abre el proxy en el
    #    paso 2 dentro del mismo ciclo).
    if current_traded_symbol:
        current_qty = broker.get_position(current_traded_symbol)
        if current_qty != 0:
            ref_close, ref_atr = _reference_data(current_traded_symbol, inst.symbol, df_ind, last, cfg)
            stop = sym_state.get("stop")
            # La posición del broker siempre es LARGA (se compre el
            # subyacente o el proxy inverso: el proxy ya invierte al
            # subyacente, así que operarlo en largo es la exposición
            # corta correcta). El stop/trailing se calcula con dirección
            # "LONG" sobre el precio/ATR del símbolo REALMENTE operado
            # (traded_symbol), sea el subyacente o el proxy.
            stop_hit = stop is not None and ref_close <= stop
            same_target = target_symbol == current_traded_symbol and target_signal == current_signal_direction

            if stop_hit or not same_target:
                reason = "stop" if stop_hit else "cambio de señal"
                log.info("Cerrando posición en %s (%s)", current_traded_symbol, reason)
                broker.close_position(current_traded_symbol)
                closed_notional = abs(current_qty) * ref_close
                exposure_state["total"] = max(0.0, exposure_state["total"] - closed_notional)
                summary["action"] = f"cierre {current_traded_symbol} ({reason})"
                sym_state = _empty_sym_state()
            else:
                new_stop = trailing_stop(ref_close, ref_atr, stop, "LONG", cfg.strategy.atr_stop_mult)
                sym_state["stop"] = new_stop
                summary["action"] = f"mantiene {current_traded_symbol}, stop={new_stop:.2f}"
                log.info("Manteniendo posición en %s, nuevo stop=%.2f", current_traded_symbol, new_stop)
        else:
            # El broker no tiene la posición que creíamos tener: limpiar estado.
            sym_state = _empty_sym_state()

    # 2) Si tras el paso anterior no hay posición y el objetivo es LONG/SHORT, abrir.
    if sym_state.get("traded_symbol") is None and target_symbol is not None:
        entry_close, entry_atr = _reference_data(target_symbol, inst.symbol, df_ind, last, cfg)
        price_cache[target_symbol] = entry_close
        equity = broker.get_equity()
        stop_distance = cfg.strategy.atr_stop_mult * entry_atr
        atr_avg = float(last.get("atr_avg") or entry_atr)
        qty = position_size(equity, entry_close, stop_distance, cfg.risk)
        qty = apply_volatility_scaling(qty, entry_atr, atr_avg, cfg.strategy)
        proposed_notional = qty * entry_close
        allowed_notional = cap_by_total_exposure(
            equity, exposure_state["total"], proposed_notional, cfg.risk,
        )
        if allowed_notional < proposed_notional and entry_close > 0:
            qty = round(allowed_notional / entry_close, 3)
        if qty > 0:
            log.info(
                "Abriendo señal=%s en %s (target=%s) qty=%.3f precio=%.2f exposición=%.0f/%.0f",
                sig, inst.symbol, target_symbol, qty, entry_close,
                exposure_state["total"], equity * cfg.risk.max_total_exposure_pct,
            )
            broker.market_order(target_symbol, qty, "buy")
            exposure_state["total"] += qty * entry_close
            new_stop = trailing_stop(entry_close, entry_atr, None, "LONG", cfg.strategy.atr_stop_mult)
            sym_state = {
                "direction": "LONG",
                "stop": new_stop,
                "traded_symbol": target_symbol,
                "signal_direction": target_signal,
            }
            summary["action"] = f"abre {target_symbol} qty={qty:.3f}"
        else:
            log.info(
                "Señal %s en %s pero tamaño=0 (riesgo, vol alta o tope de exposición total)",
                sig, inst.symbol,
            )

    positions_state[inst.symbol] = sym_state
    return summary


def _reconcile_positions(cfg: Config, broker: AlpacaBroker, state: dict) -> set[str]:
    """Compara las posiciones reales del broker contra lo que el estado conoce.

    Si el broker tiene posiciones abiertas en símbolos del universo (el
    propio símbolo o su `short_proxy`) que el estado no sabía que existían,
    registra un warning y las ADOPTA al estado (con un stop inicializado
    desde el precio/ATR actual), pero NO opera nada más sobre ellas en este
    mismo ciclo: se gestionarán (trailing/cierre) normalmente a partir del
    ciclo siguiente.

    Devuelve el conjunto de `inst.symbol` (claves del universo) que fueron
    adoptadas este ciclo, para que `run_cycle` se salte su procesamiento
    normal en esta pasada.
    """
    try:
        broker_positions = broker.list_positions()
    except Exception as exc:  # noqa: BLE001 - reconciliación es best-effort
        log.warning("No se pudo listar posiciones del broker para reconciliar: %s", exc)
        return set()

    positions_state = state.setdefault("positions", {})
    known_traded_symbols = {
        s.get("traded_symbol") for s in positions_state.values() if s.get("traded_symbol")
    }

    universe_map: dict[str, tuple[Instrument, str]] = {}
    for inst in cfg.universe:
        universe_map[inst.symbol] = (inst, "LONG")
        if inst.short_proxy:
            universe_map[inst.short_proxy] = (inst, "SHORT")

    orphan_symbols = [
        sym
        for sym, qty in broker_positions.items()
        if qty != 0 and sym in universe_map and sym not in known_traded_symbols
    ]

    adopted: set[str] = set()
    if not orphan_symbols:
        return adopted

    log.warning(
        "Posiciones del broker no reconocidas por el estado local: %s. "
        "Se adoptan al estado sin operar sobre ellas este ciclo.",
        ", ".join(sorted(orphan_symbols)),
    )

    for sym in orphan_symbols:
        inst, signal_direction = universe_map[sym]
        try:
            df = _fetch_daily(sym, cfg)
            df_ind = compute_indicators(df, cfg.strategy)
            last = df_ind.iloc[-1]
            stop = trailing_stop(float(last["Close"]), float(last["atr"]), None, "LONG", cfg.strategy.atr_stop_mult)
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo inicializar el stop de la posición huérfana %s: %s", sym, exc)
            continue

        positions_state[inst.symbol] = {
            "direction": "LONG",
            "stop": stop,
            "traded_symbol": sym,
            "signal_direction": signal_direction,
        }
        adopted.add(inst.symbol)

    return adopted


def run_cycle() -> int:
    _setup_logging()
    load_dotenv()

    if HALT_FILE.exists():
        reason = HALT_FILE.read_text().strip()
        log.error("Archivo HALT presente, el bot está detenido: %s", reason)
        _notify_safe(f"Bot detenido (HALT presente): {reason}")
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
        _notify_safe(f"CIRCUIT BREAKER ACTIVADO. {reason}. Todas las posiciones fueron cerradas.")
        return 1

    adopted = _reconcile_positions(cfg, broker, state)

    price_cache: dict[str, float] = {}
    exposure_state = {"total": _estimate_exposure(broker, price_cache)}
    ranked = _rank_instruments(cfg.universe, cfg, adopted)

    errors: list[tuple[str, str]] = []
    summaries: list[dict] = []
    for sym in adopted:
        log.info(
            "%s: posición adoptada este ciclo tras reconciliación, se gestiona desde el próximo ciclo",
            sym,
        )
        summaries.append({"symbol": sym, "signal": "n/d", "action": "adoptada (sin operar)"})

    for inst in ranked:
        try:
            summaries.append(
                _process_instrument(inst, cfg, broker, state, exposure_state, price_cache)
            )
        except Exception as exc:  # noqa: BLE001 - no dejar que un símbolo aborte el ciclo
            log.exception("Error procesando %s: %s", inst.symbol, exc)
            errors.append((inst.symbol, str(exc)))

    _save_state(state)
    _append_equity_history(equity)

    status = "error" if errors else "ok"
    _write_last_run(status, equity, errors, summaries)

    lines = [f"Ciclo diario completado. Equity: {equity:.2f}"]
    for s in summaries:
        lines.append(f"  {s['symbol']}: señal={s['signal']} -> {s['action']}")
    if errors:
        lines.append("Errores:")
        for sym, err in errors:
            lines.append(f"  {sym}: {err}")
        log.warning("Ciclo terminado con errores en: %s", ", ".join(s for s, _ in errors))
        _notify_safe("⚠️ Ciclo con errores\n" + "\n".join(lines))
    else:
        log.info("Ciclo diario completado sin errores")
        _notify_safe("\n".join(lines))

    return 1 if errors else 0


def main() -> None:
    sys.exit(run_cycle())


if __name__ == "__main__":
    main()
