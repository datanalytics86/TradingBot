"""Generador de dashboard unificado (equity, drawdown, scanner + Grok).

Uso::

    python -m tradingbot.dashboard_builder

Controlado por ``config.yaml`` → ``dashboard.enabled`` y ``dashboard.type``.
"""
from __future__ import annotations

import csv
import html
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from tradingbot.config import (
    DASHBOARD_HTML_FILE,
    EQUITY_HISTORY_FILE,
    HALT_FILE,
    LAST_RUN_FILE,
    LAST_SCANNER_FILE,
    ROOT,
    SCANNER_HTML_FILE,
    Config,
    load_config,
)
from tradingbot.scanner import GrokInsight, ScanResult

log = logging.getLogger("tradingbot")


def _load_equity_rows() -> list[tuple[str, float]]:
    if not EQUITY_HISTORY_FILE.exists():
        return []
    rows: list[tuple[str, float]] = []
    with EQUITY_HISTORY_FILE.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                rows.append((row["fecha"], float(row["equity"])))
            except (KeyError, ValueError):
                continue
    return rows


def _load_scanner_data() -> tuple[list[ScanResult], list[GrokInsight], str]:
    if not LAST_SCANNER_FILE.exists():
        return [], [], ""
    try:
        raw = json.loads(LAST_SCANNER_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return [], [], ""
    results = [ScanResult(**r) for r in raw.get("results", [])]
    insights = [GrokInsight(**i) for i in raw.get("insights", [])]
    return results, insights, raw.get("timestamp", "")


def _plotly_charts(equity_values: list[float], dates: list[str]) -> str:
    """Genera HTML de gráficos Plotly o SVG básico como fallback."""
    if len(equity_values) < 2:
        return '<p class="muted">Sin datos de equity suficientes</p>'

    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        peak = equity_values[0]
        dd = []
        for v in equity_values:
            peak = max(peak, v)
            dd.append((v / peak - 1) * 100 if peak > 0 else 0)

        fig = make_subplots(
            rows=2, cols=1, shared_xaxes=True,
            subplot_titles=("Equity", "Drawdown (%)"),
            vertical_spacing=0.1,
        )
        fig.add_trace(go.Scatter(x=dates, y=equity_values, name="Equity",
                                 line=dict(color="#22c55e", width=2)), row=1, col=1)
        fig.add_trace(go.Scatter(x=dates, y=dd, name="Drawdown",
                                 fill="tozeroy", line=dict(color="#ef4444")), row=2, col=1)
        fig.update_layout(template="plotly_dark", height=480, margin=dict(t=40, b=30))
        return fig.to_html(full_html=False, include_plotlyjs="cdn")
    except ImportError:
        return _basic_svg_chart(equity_values)


def _basic_svg_chart(values: list[float], width: int = 600, height: int = 120) -> str:
    if len(values) < 2:
        return ""
    vmin, vmax = min(values), max(values)
    span = vmax - vmin or 1.0
    step = width / (len(values) - 1)
    pts = " ".join(
        f"{i * step:.1f},{height - ((v - vmin) / span) * (height - 8) - 4:.1f}"
        for i, v in enumerate(values)
    )
    return f'<svg viewBox="0 0 {width} {height}" class="spark"><polyline fill="none" stroke="#22c55e" stroke-width="2" points="{pts}"/></svg>'


def _scanner_table_html(results: list[ScanResult], insights: list[GrokInsight]) -> str:
    if not results:
        return '<p class="muted">Sin picks del scanner en el último ciclo.</p>'
    insight_map = {i.symbol: i for i in insights}
    rows = []
    for i, r in enumerate(results, 1):
        ins = insight_map.get(r.symbol)
        verdict = html.escape(ins.verdict if ins else "—")
        summary = html.escape(ins.summary if ins else "")
        sizing = html.escape(ins.sizing_hint if ins else "")
        src = html.escape(ins.source if ins else "")
        rows.append(
            f"<tr><td>{i}</td><td><strong>{html.escape(r.symbol)}</strong></td>"
            f"<td>{r.score:.3f}</td><td>{r.gap_pct * 100:+.1f}%</td>"
            f"<td class='v-{verdict}'>{verdict}</td>"
            f"<td>{summary}<br/><small>{sizing}</small></td><td>{src}</td></tr>"
        )
    return (
        "<table><thead><tr><th>#</th><th>Símbolo</th><th>Score</th><th>Gap</th>"
        "<th>Grok</th><th>Análisis</th><th>Fuente</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table>"
    )


def build_dashboard(cfg: Config, output_path: Path | None = None) -> Path:
    """Genera el dashboard según ``cfg.dashboard.type``."""
    out = output_path or (DASHBOARD_HTML_FILE if cfg.dashboard.type == "html" else SCANNER_HTML_FILE)
    out.parent.mkdir(parents=True, exist_ok=True)

    equity_rows = _load_equity_rows()
    dates = [r[0][:10] for r in equity_rows]
    values = [r[1] for r in equity_rows]
    scanner_results, scanner_insights, scan_ts = _load_scanner_data()

    last_run = {}
    if LAST_RUN_FILE.exists():
        try:
            last_run = json.loads(LAST_RUN_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    halt_banner = ""
    if HALT_FILE.exists():
        halt_banner = f'<div class="halt">BOT DETENIDO — {html.escape(HALT_FILE.read_text().strip()[:200])}</div>'

    charts = _plotly_charts(values, dates) if cfg.dashboard.type == "html" else _basic_svg_chart(values)
    scanner_html = _scanner_table_html(scanner_results, scanner_insights)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    equity_now = f"${values[-1]:,.2f}" if values else "n/d"
    mode = html.escape(cfg.mode)
    status = html.escape(last_run.get("status", "n/d"))

    page = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>TradingBot Dashboard</title>
  <style>
    :root {{ --bg:#0f172a; --card:#1e293b; --text:#e2e8f0; --muted:#94a3b8; --ok:#22c55e; --warn:#f59e0b; --halt:#ef4444; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:system-ui,sans-serif; background:var(--bg); color:var(--text); line-height:1.45; }}
    .wrap {{ max-width:1100px; margin:0 auto; padding:1.25rem; }}
    h1 {{ margin:0 0 0.25rem; font-size:1.4rem; }}
    .sub {{ color:var(--muted); font-size:0.9rem; margin-bottom:1rem; }}
    .grid {{ display:grid; gap:0.75rem; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); margin-bottom:1rem; }}
    .card {{ background:var(--card); border-radius:12px; padding:1rem; border:1px solid #334155; }}
    .label {{ color:var(--muted); font-size:0.75rem; text-transform:uppercase; }}
    .value {{ font-size:1.4rem; font-weight:700; margin-top:0.2rem; }}
    table {{ width:100%; border-collapse:collapse; font-size:0.88rem; background:var(--card); border-radius:10px; overflow:hidden; }}
    th,td {{ padding:0.55rem 0.45rem; border-bottom:1px solid #334155; text-align:left; }}
    th {{ color:var(--muted); font-size:0.75rem; text-transform:uppercase; }}
    .halt {{ background:#450a0a; border:1px solid var(--halt); color:#fecaca; padding:0.75rem; border-radius:10px; margin-bottom:1rem; }}
    .v-strong,.v-buy {{ color:var(--ok); font-weight:700; }}
    .v-watch {{ color:#3b82f6; }}
    .v-caution {{ color:var(--warn); }}
    .v-skip {{ color:var(--muted); }}
    .muted {{ color:var(--muted); }}
    .spark {{ width:100%; height:120px; }}
    section {{ margin-top:1.5rem; }}
  </style>
</head>
<body>
<div class="wrap">
  <h1>TradingBot Dashboard</h1>
  <p class="sub">Modo <strong>{mode}</strong> · {ts} · tipo={cfg.dashboard.type}</p>
  {halt_banner}
  <div class="grid">
    <div class="card"><div class="label">Equity</div><div class="value">{equity_now}</div></div>
    <div class="card"><div class="label">Último ciclo</div><div class="value" style="font-size:1rem">{status}</div></div>
    <div class="card"><div class="label">Scanner</div><div class="value" style="font-size:1rem">{len(scanner_results)} picks</div></div>
  </div>
  <section><h2>Equity y drawdown</h2>{charts}</section>
  <section><h2>Scanner + Análisis Grok</h2>
    <p class="muted">Último escaneo: {html.escape(scan_ts or 'n/d')}</p>
    {scanner_html}
  </section>
</div>
</body></html>"""

    out.write_text(page, encoding="utf-8")
    # Espejo para GitHub Pages (publica docs/index.html)
    pages_index = ROOT / "docs" / "index.html"
    if out.resolve() != pages_index.resolve():
        pages_index.write_text(page, encoding="utf-8")
    log.info("Dashboard generado: %s", out)
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = load_config()
    if not cfg.dashboard.enabled:
        print("dashboard.enabled=false — nada que generar.")
        return
    path = build_dashboard(cfg)
    print(f"Dashboard: {path}")


if __name__ == "__main__":
    main()