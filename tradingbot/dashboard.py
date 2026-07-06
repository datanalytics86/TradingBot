"""Genera un dashboard HTML estático para ver posiciones, señales y equity.

Uso:
    python -m tradingbot.dashboard

Salida por defecto: docs/index.html (compatible con GitHub Pages).
"""
from __future__ import annotations

import csv
import html
import json
from datetime import datetime, timezone
from pathlib import Path

from tradingbot.config import (
    EQUITY_HISTORY_FILE,
    HALT_FILE,
    LAST_RUN_FILE,
    ROOT,
    STATE_FILE,
    load_config,
)

OUTPUT = ROOT / "docs" / "index.html"


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _load_equity_rows() -> list[tuple[str, float]]:
    if not EQUITY_HISTORY_FILE.exists():
        return []
    rows: list[tuple[str, float]] = []
    with EQUITY_HISTORY_FILE.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            fecha = (row.get("fecha") or "").strip()
            equity_raw = (row.get("equity") or "").strip()
            if not fecha or not equity_raw:
                continue
            try:
                rows.append((fecha, float(equity_raw)))
            except ValueError:
                continue
    return rows


def _broker_snapshot(cfg_mode: str) -> dict | None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
        from tradingbot.broker import AlpacaBroker

        broker = AlpacaBroker(paper=(cfg_mode == "paper"))
        positions = broker.list_positions()
        equity = broker.get_equity()
        return {"equity": equity, "positions": positions}
    except Exception:
        return None


def _sparkline_svg(values: list[float], width: int = 320, height: int = 80) -> str:
    if len(values) < 2:
        return '<p class="muted">Sin historial suficiente</p>'
    vmin, vmax = min(values), max(values)
    span = vmax - vmin or 1.0
    step = width / (len(values) - 1)
    points = []
    for i, v in enumerate(values):
        x = i * step
        y = height - ((v - vmin) / span) * (height - 8) - 4
        points.append(f"{x:.1f},{y:.1f}")
    poly = " ".join(points)
    return (
        f'<svg viewBox="0 0 {width} {height}" class="spark" aria-hidden="true">'
        f'<polyline fill="none" stroke="#3b82f6" stroke-width="2" points="{poly}"/>'
        f"</svg>"
    )


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "n/d"
    return f"${value:,.2f}"


def _position_rows(state: dict | None, broker: dict | None) -> str:
    if not state:
        return '<tr><td colspan="6" class="muted">Sin state.json</td></tr>'

    broker_pos = (broker or {}).get("positions", {})
    positions = state.get("positions", {})
    rows: list[str] = []

    for universe_sym, pos in positions.items():
        traded = pos.get("traded_symbol")
        signal_dir = pos.get("signal_direction") or "—"
        stop = pos.get("stop")
        stop_txt = f"{stop:.2f}" if isinstance(stop, (int, float)) else "—"
        qty = broker_pos.get(traded, 0.0) if traded else 0.0
        status = "ABIERTA" if traded and qty else "SIN POSICIÓN"
        badge = "open" if status == "ABIERTA" else "flat"
        rows.append(
            "<tr>"
            f"<td><strong>{html.escape(universe_sym)}</strong></td>"
            f"<td>{html.escape(traded or '—')}</td>"
            f'<td><span class="badge {badge}">{html.escape(status)}</span></td>'
            f"<td>{html.escape(str(signal_dir))}</td>"
            f"<td>{qty:.3f}</td>"
            f"<td>{html.escape(stop_txt)}</td>"
            "</tr>"
        )
    return "\n".join(rows) if rows else '<tr><td colspan="6" class="muted">Universo vacío</td></tr>'


def _last_run_rows(last_run: dict | None) -> str:
    if not last_run:
        return '<tr><td colspan="3" class="muted">Sin last_run.json</td></tr>'
    rows = []
    for item in last_run.get("summaries", []):
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('symbol', '—')))}</td>"
            f"<td>{html.escape(str(item.get('signal', '—')))}</td>"
            f"<td>{html.escape(str(item.get('action', '—')))}</td>"
            "</tr>"
        )
    return "\n".join(rows) if rows else '<tr><td colspan="3" class="muted">Sin resumen</td></tr>'


def generate_dashboard(output: Path | None = None) -> Path:
    output = output or OUTPUT
    output.parent.mkdir(parents=True, exist_ok=True)

    cfg = load_config()
    state = _load_json(STATE_FILE)
    last_run = _load_json(LAST_RUN_FILE)
    equity_rows = _load_equity_rows()
    broker = _broker_snapshot(cfg.mode)

    equities = [e for _, e in equity_rows]
    peak = (state or {}).get("peak_equity")
    last_equity = equities[-1] if equities else (last_run or {}).get("equity")
    broker_equity = (broker or {}).get("equity")
    display_equity = broker_equity if broker_equity is not None else last_equity

    last_ts = (last_run or {}).get("timestamp", "—")
    last_status = (last_run or {}).get("status", "—")
    status_class = "ok" if last_status == "ok" else "warn"

    halt_banner = ""
    if HALT_FILE.exists():
        halt_text = HALT_FILE.read_text(encoding="utf-8").strip()
        halt_banner = (
            '<div class="halt">'
            f"<strong>BOT DETENIDO (HALT)</strong><br>{html.escape(halt_text)}"
            "</div>"
        )

    dd_pct = None
    if peak and display_equity and peak > 0:
        dd_pct = (display_equity / peak - 1) * 100

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    alpaca_url = (
        "https://app.alpaca.markets/paper/dashboard/overview"
        if cfg.mode == "paper"
        else "https://app.alpaca.markets/live/dashboard/overview"
    )

    page = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>TradingBot Dashboard</title>
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
    .wrap {{ max-width: 960px; margin: 0 auto; padding: 1rem; }}
    h1 {{ font-size: 1.35rem; margin: 0 0 0.25rem; }}
    .sub {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 1rem; }}
    .grid {{ display: grid; gap: 0.75rem; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }}
    .card {{
      background: var(--card); border-radius: 12px; padding: 1rem;
      border: 1px solid #334155;
    }}
    .label {{ color: var(--muted); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em; }}
    .value {{ font-size: 1.5rem; font-weight: 700; margin-top: 0.2rem; }}
    .status.ok {{ color: var(--ok); }}
    .status.warn {{ color: var(--warn); }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
    th, td {{ padding: 0.55rem 0.4rem; border-bottom: 1px solid #334155; text-align: left; }}
    th {{ color: var(--muted); font-weight: 600; font-size: 0.75rem; text-transform: uppercase; }}
    .badge {{ padding: 0.15rem 0.5rem; border-radius: 999px; font-size: 0.75rem; font-weight: 600; }}
    .badge.open {{ background: #14532d; color: #bbf7d0; }}
    .badge.flat {{ background: #334155; color: #cbd5e1; }}
    .spark {{ width: 100%; height: 80px; }}
    .halt {{
      background: #450a0a; border: 1px solid var(--halt); color: #fecaca;
      padding: 0.75rem 1rem; border-radius: 10px; margin-bottom: 1rem;
    }}
    a {{ color: var(--accent); }}
    .links {{ display: flex; flex-wrap: wrap; gap: 0.75rem; margin-top: 0.75rem; }}
    .links a {{ text-decoration: none; background: #1d4ed8; color: white; padding: 0.45rem 0.8rem; border-radius: 8px; font-size: 0.85rem; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>TradingBot Dashboard</h1>
    <p class="sub">Modo <strong>{html.escape(cfg.mode)}</strong> · Actualizado {generated_at}</p>
    {halt_banner}
    <div class="grid">
      <div class="card">
        <div class="label">Equity</div>
        <div class="value">{_fmt_money(display_equity)}</div>
      </div>
      <div class="card">
        <div class="label">Peak equity</div>
        <div class="value">{_fmt_money(peak)}</div>
      </div>
      <div class="card">
        <div class="label">Drawdown vs peak</div>
        <div class="value">{(f"{dd_pct:.2f}%" if dd_pct is not None else "n/d")}</div>
      </div>
      <div class="card">
        <div class="label">Último ciclo</div>
        <div class="value status {status_class}">{html.escape(str(last_status))}</div>
        <div class="sub">{html.escape(str(last_ts))}</div>
      </div>
    </div>

    <div class="card" style="margin-top:0.75rem">
      <div class="label">Evolución equity</div>
      {_sparkline_svg(equities)}
    </div>

    <div class="card" style="margin-top:0.75rem">
      <div class="label">Posiciones / papeles</div>
      <table>
        <thead>
          <tr>
            <th>Universo</th><th>Operado</th><th>Estado</th><th>Señal</th><th>Cantidad</th><th>Stop</th>
          </tr>
        </thead>
        <tbody>
          {_position_rows(state, broker)}
        </tbody>
      </table>
    </div>

    <div class="card" style="margin-top:0.75rem">
      <div class="label">Último ciclo — señales y acciones</div>
      <table>
        <thead><tr><th>Símbolo</th><th>Señal</th><th>Acción</th></tr></thead>
        <tbody>{_last_run_rows(last_run)}</tbody>
      </table>
    </div>

    <div class="links">
      <a href="{alpaca_url}" target="_blank" rel="noopener">Alpaca {html.escape(cfg.mode)}</a>
      <a href="https://github.com/datanalytics86/TradingBot/actions" target="_blank" rel="noopener">GitHub Actions</a>
      <a href="plan-validacion-paper.html" target="_blank" rel="noopener">Plan validación 8 semanas</a>
    </div>
  </div>
</body>
</html>
"""
    output.write_text(page, encoding="utf-8")
    return output


def main() -> None:
    path = generate_dashboard()
    print(f"Dashboard generado: {path}")


if __name__ == "__main__":
    main()