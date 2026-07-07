"""Reporte de estado del bot, ejecutable como ``python3 -m tradingbot.report``.

Muestra, sin requerir intervención ni modificar nada:

- Modo (paper/live), equity actual y posiciones reales según el broker (si
  hay credenciales configuradas y alpaca-py instalado; si no, lo indica y
  sigue con la parte local).
- Estado local del bot (`state.json`): posiciones que el bot cree tener y
  sus stops.
- **Scanner Results**: tabla del último escaneo premarket (si existe).
- Las últimas 10 líneas de `equity_history.csv`, con la variación % entre
  filas consecutivas.
- El contenido del archivo `HALT`, si existe, bien visible.

Opcionalmente genera un dashboard HTML del scanner si
``scanner.generate_html: true`` en config.yaml.
"""
from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from tradingbot.config import (
    EQUITY_HISTORY_FILE,
    HALT_FILE,
    LAST_RUN_FILE,
    LAST_SCANNER_FILE,
    SCANNER_HTML_FILE,
    STATE_FILE,
    Config,
    load_config,
)
from tradingbot.scanner import GrokInsight, ScanResult

log = logging.getLogger("tradingbot")


@dataclass
class ScannerRunData:
    """Datos del último escaneo cargados desde ``last_scanner.json``."""

    timestamp: str
    results: list[ScanResult]
    insights: list[GrokInsight]
    csv_path: str | None


def _print_header(title: str) -> None:
    print("=" * 60)
    print(title)
    print("=" * 60)


def _load_last_scanner() -> ScannerRunData | None:
    if not LAST_SCANNER_FILE.exists():
        return None
    try:
        raw = json.loads(LAST_SCANNER_FILE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("No se pudo leer last_scanner.json: %s", exc)
        return None

    results = [ScanResult(**r) for r in raw.get("results", [])]
    insights = [GrokInsight(**i) for i in raw.get("insights", [])]
    return ScannerRunData(
        timestamp=raw.get("timestamp", "n/d"),
        results=results,
        insights=insights,
        csv_path=raw.get("csv_path"),
    )


def format_scanner_table(results: list[ScanResult], insights: list[GrokInsight] | None = None) -> str:
    """Genera una tabla ASCII con los resultados del scanner."""
    if not results:
        return "  (sin candidatos en el último escaneo)"

    insight_map = {i.symbol: i for i in (insights or [])}
    header = (
        f"  {'#':>2}  {'Símbolo':<6} {'Tipo':<5} {'Score':>6} "
        f"{'Gap%':>7} {'Vol×':>5} {'Precio':>8}  {'Grok':<8}  Fuente"
    )
    sep = "  " + "-" * 72
    rows = [header, sep]

    for i, r in enumerate(results, 1):
        ins = insight_map.get(r.symbol)
        grok = ins.verdict if ins else "—"
        rows.append(
            f"  {i:>2}  {r.symbol:<6} {r.asset_type:<5} {r.score:>6.3f} "
            f"{r.gap_pct * 100:>+6.1f}% {r.volume_ratio:>5.1f} "
            f"${r.price:>7.2f}  {grok:<8}  {r.source}"
        )

    return "\n".join(rows)


def _report_scanner_results() -> None:
    data = _load_last_scanner()
    if data is None:
        print("Sin escaneos previos (last_scanner.json no existe).")
        print("Activa scanner.enabled y corre un ciclo o --test-scanner.")
        return

    print(f"Último escaneo: {data.timestamp}")
    if data.csv_path:
        print(f"CSV: {data.csv_path}")
    print()
    print(format_scanner_table(data.results, data.insights))

    _report_grok_analysis(data.insights)


def _report_grok_analysis(insights: list[GrokInsight] | None) -> None:
    """Imprime la sección Análisis Grok con veredicto, sizing y fuente."""
    if not insights:
        print()
        print("Análisis Grok: sin datos (corre el scanner con grok_enabled).")
        return

    print()
    _print_header("ANÁLISIS GROK")
    sources = {i.source for i in insights}
    src_label = ", ".join(sorted(sources))
    print(f"Fuente: {src_label}")
    print()

    for ins in insights:
        print(f"  {ins.symbol}")
        print(f"    Veredicto:   {ins.verdict.upper()}  (confianza {ins.confidence * 100:.0f}%)")
        print(f"    Resumen:     {ins.summary}")
        if ins.sizing_hint:
            print(f"    Sizing/riesgo: {ins.sizing_hint}")
        print()


def generate_scanner_html(
    results: list[ScanResult],
    insights: list[GrokInsight] | None = None,
    output_path: Path | None = None,
    timestamp: str | None = None,
) -> Path:
    """Genera un dashboard HTML simple con los resultados del scanner."""
    out = output_path or SCANNER_HTML_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    insight_map = {i.symbol: i for i in (insights or [])}

    rows_html = ""
    for i, r in enumerate(results, 1):
        ins = insight_map.get(r.symbol)
        verdict = ins.verdict if ins else "—"
        verdict_cls = f"verdict-{verdict}" if ins else ""
        summary = ins.summary if ins else ""
        sizing = ins.sizing_hint if ins else ""
        grok_src = ins.source if ins else ""
        rows_html += f"""
        <tr>
          <td>{i}</td>
          <td><strong>{r.symbol}</strong></td>
          <td>{r.asset_type}</td>
          <td>{r.score:.3f}</td>
          <td class="{'gap-up' if r.gap_pct >= 0 else 'gap-down'}">{r.gap_pct * 100:+.1f}%</td>
          <td>{r.volume_ratio:.1f}x</td>
          <td>${r.price:.2f}</td>
          <td>{r.source}</td>
          <td class="{verdict_cls}">{verdict}</td>
          <td class="summary">{summary}<br/><small>{sizing}</small></td>
          <td class="muted">{grok_src}</td>
        </tr>"""

    empty_row = '<tr><td colspan="11" class="muted">Sin candidatos en el último escaneo</td></tr>'
    body_rows = rows_html if results else empty_row

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Scanner Premarket — TradingBot</title>
  <style>
    :root {{
      --bg: #0f172a; --card: #1e293b; --text: #e2e8f0; --muted: #94a3b8;
      --accent: #3b82f6; --ok: #22c55e; --warn: #f59e0b; --halt: #ef4444;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; font-family: system-ui, -apple-system, Segoe UI, sans-serif;
      background: var(--bg); color: var(--text); line-height: 1.45;
    }}
    .wrap {{ max-width: 1100px; margin: 0 auto; padding: 1.25rem; }}
    h1 {{ font-size: 1.35rem; margin: 0 0 0.25rem; }}
    .sub {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 1rem; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; background: var(--card);
             border-radius: 10px; overflow: hidden; }}
    th, td {{ padding: 0.6rem 0.5rem; border-bottom: 1px solid #334155; text-align: left; }}
    th {{ color: var(--muted); font-weight: 600; font-size: 0.75rem; text-transform: uppercase; }}
    .gap-up {{ color: var(--ok); font-weight: 600; }}
    .gap-down {{ color: var(--halt); font-weight: 600; }}
    .verdict-strong, .verdict-buy {{ color: var(--ok); font-weight: 700; }}
    .verdict-watch {{ color: var(--accent); }}
    .verdict-caution {{ color: var(--warn); }}
    .verdict-skip {{ color: var(--muted); }}
    .summary {{ color: var(--muted); font-size: 0.82rem; max-width: 280px; }}
    .muted {{ color: var(--muted); text-align: center; padding: 1.5rem; }}
    .badge {{ display: inline-block; background: #1d4ed8; color: white;
              padding: 0.2rem 0.5rem; border-radius: 6px; font-size: 0.75rem; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Scanner Premarket</h1>
    <p class="sub">Actualizado {ts} · <span class="badge">TradingBot</span></p>
    <table>
      <thead>
        <tr>
          <th>#</th><th>Símbolo</th><th>Tipo</th><th>Score</th>
          <th>Gap</th><th>Vol</th><th>Precio</th><th>Fuente</th>
          <th>Grok</th><th>Resumen IA</th><th>Fuente IA</th>
        </tr>
      </thead>
      <tbody>{body_rows}
      </tbody>
    </table>
  </div>
</body>
</html>"""

    out.write_text(html, encoding="utf-8")
    log.info("Dashboard HTML del scanner generado: %s", out)
    return out


def emit_scanner_report(cfg: Config, results: list[ScanResult], insights: list[GrokInsight]) -> None:
    """Imprime el reporte del scanner y genera HTML si está configurado."""
    print()
    _print_header("SCANNER RESULTS")
    print(format_scanner_table(results, insights))

    _report_grok_analysis(insights)

    if cfg.scanner.generate_html:
        html_path = generate_scanner_html(results, insights)
        print()
        print(f"Dashboard HTML: {html_path}")


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
    except Exception as exc:  # noqa: BLE001
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


def _report_last_run() -> None:
    if not LAST_RUN_FILE.exists():
        return
    try:
        data = json.loads(LAST_RUN_FILE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(f"No se pudo leer last_run.json: {exc}")
        return
    print(f"Último ciclo: {data.get('timestamp', 'n/d')} | estado={data.get('status', 'n/d')}")
    errs = data.get("errors") or []
    if errs:
        print("Errores del último ciclo:")
        for e in errs:
            print(f"  {e.get('symbol')}: {e.get('message')}")


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
    load_dotenv()
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
    _print_header("SCANNER RESULTS")
    _report_scanner_results()
    print("-" * 60)
    _report_equity_history()
    print("-" * 60)
    _report_last_run()

    if cfg is not None and cfg.scanner.generate_html:
        data = _load_last_scanner()
        if data and data.results:
            path = generate_scanner_html(data.results, data.insights, timestamp=data.timestamp)
            print(f"Dashboard HTML actualizado: {path}")

    _report_halt()


if __name__ == "__main__":
    main()