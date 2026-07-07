"""Scanner premarket: detecta símbolos con gap y volumen anómalos.

Inspirado en scanners de momentum premarket (estilo bots de day-trading).
Filtra por gap, volumen relativo y rango de precio; devuelve los mejores
candidatos con un score ponderado configurable desde ``config.yaml``.

Incluye exportación CSV, umbral de gap diferenciado para ETFs y análisis
IA real vía Grok API (xAI), con caché JSON y fallback heurístico.

Ejecutable en modo prueba vía::

    python -m tradingbot.main --test-scanner
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from tradingbot.config import (
    GROK_CACHE_FILE,
    LAST_SCANNER_FILE,
    SCANNER_RESULTS_DIR,
    Config,
    Instrument,
    grok_key,
)
from tradingbot.data import PremarketQuote, fetch_market_movers, get_premarket_data

log = logging.getLogger("tradingbot")

VALID_GROK_VERDICTS = frozenset({"strong", "buy", "watch", "caution", "skip"})
_GROK_REST_URL = "https://api.x.ai/v1/chat/completions"

# ETFs conocidos además de los del universo base (proxies inversos, etc.)
_KNOWN_ETFS = frozenset({
    "SPY", "QQQ", "IWM", "DIA", "SH", "PSQ", "RWM", "VOO", "VTI", "XLF", "XLE",
    "ARKK", "TQQQ", "SQQQ", "UVXY", "GLD", "SLV", "TLT", "HYG", "EEM",
})


@dataclass
class ScanResult:
    """Resultado del scanner para un símbolo que pasó los filtros."""

    symbol: str
    score: float
    gap_pct: float
    volume_ratio: float
    price: float
    premarket_volume: float
    source: str
    asset_type: str = "STOCK"
    gap_threshold_used: float = 0.05


@dataclass
class GrokInsight:
    """Análisis IA de un candidato del scanner (capa Grok)."""

    symbol: str
    verdict: str
    summary: str
    confidence: float
    sizing_hint: str = ""
    source: str = "heuristic"  # grok | cache | heuristic


def scan_premarket(cfg: Config) -> list[ScanResult]:
    """Escanea el mercado premarket y devuelve los mejores candidatos.

    Pool de candidatos:
      1. Símbolos del universo base (si ``include_base_universe``)
      2. ``watchlist`` del scanner
      3. Gainers de Polygon/Alpaca (si hay credenciales)

    Filtros (todos deben cumplirse):
      - gap >= umbral según tipo (``etf_gap_threshold`` o ``gap_threshold``)
      - volumen >= ``volume_multiplier`` × promedio
      - precio entre ``min_price`` y ``max_price``
      - volumen promedio >= ``min_avg_volume``

    Devuelve hasta ``max_symbols`` resultados ordenados por score descendente.
    """
    scanner = cfg.scanner
    candidates = _build_candidate_pool(cfg)
    if not candidates:
        log.warning("Scanner: pool de candidatos vacío")
        return []

    log.info("Scanner: evaluando %d candidatos premarket", len(candidates))
    quotes = get_premarket_data(candidates, cfg.data)

    results: list[ScanResult] = []
    for quote in quotes:
        gap_thr = _gap_threshold_for(quote.symbol, scanner, cfg)
        if not _passes_filters(quote, scanner, gap_thr):
            continue
        score = _compute_score(quote, scanner, gap_thr)
        asset_type = "ETF" if _is_etf(quote.symbol, cfg) else "STOCK"
        results.append(
            ScanResult(
                symbol=quote.symbol,
                score=score,
                gap_pct=quote.gap_pct,
                volume_ratio=quote.volume_ratio,
                price=quote.price,
                premarket_volume=quote.volume,
                source=quote.source,
                asset_type=asset_type,
                gap_threshold_used=gap_thr,
            )
        )

    results.sort(key=lambda r: r.score, reverse=True)
    top = results[: scanner.max_symbols]

    if top:
        log.info(
            "Scanner: %d símbolos pasaron filtros (top %d)",
            len(results),
            len(top),
        )
        for i, r in enumerate(top[:5], 1):
            log.info(
                "  #%d %s [%s] score=%.3f gap=%+.1f%% (umbral %.1f%%) "
                "vol=%.1fx precio=$%.2f [%s]",
                i, r.symbol, r.asset_type, r.score, r.gap_pct * 100,
                r.gap_threshold_used * 100, r.volume_ratio, r.price, r.source,
            )
    else:
        log.info("Scanner: ningún símbolo pasó los filtros premarket")

    return top


def analyze_with_grok(cfg: Config, picks: list[ScanResult]) -> list[GrokInsight]:
    """Analiza los top picks con Grok API (xAI) o fallback heurístico.

    Flujo:
      1. Si ``api.grok_enabled`` y hay ``GROK_API_KEY`` → llama a xAI.
      2. Consulta caché JSON (``scanner_results/grok_cache.json``) primero.
      3. Si la API falla → heurística local (sin abortar el ciclo).
    """
    if not picks:
        return []

    if not cfg.api.grok_enabled:
        log.info("Grok deshabilitado en config (api.grok_enabled=false); usando heurística")
        return _heuristic_insights(picks)

    if not grok_key():
        log.warning("GROK_API_KEY no configurada; usando análisis heurístico")
        return _heuristic_insights(picks)

    cache_key = _grok_cache_key(picks)
    cached = _load_grok_cache(cache_key)
    if cached is not None:
        log.info("Grok: respuesta desde caché (%d símbolos)", len(cached))
        return cached

    try:
        raw_text = _call_grok_api(cfg, _build_grok_prompt(cfg, picks))
        insights = _parse_grok_response(raw_text, picks)
        _save_grok_cache(cache_key, insights)
        for ins in insights:
            log.info(
                "Grok %s → %s (%.0f%%) [%s]: %s | sizing: %s",
                ins.symbol, ins.verdict, ins.confidence * 100,
                ins.source, ins.summary, ins.sizing_hint or "n/d",
            )
        return insights
    except Exception as exc:  # noqa: BLE001 - fallback sin romper el scanner
        log.warning("Grok API falló (%s); usando heurística", exc)
        return _heuristic_insights(picks)


def export_to_csv(
    results: list[ScanResult],
    filename: str | None = None,
    insights: list[GrokInsight] | None = None,
    output_dir: Path | None = None,
) -> Path:
    """Exporta resultados del scanner a CSV en ``scanner_results/``.

    Si ``filename`` es None, usa ``scanner_results_YYYYMMDD.csv``.
    Incluye columnas de análisis Grok si se pasan ``insights``.
    """
    out_dir = output_dir or SCANNER_RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    if filename is None:
        filename = f"scanner_results_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"

    path = out_dir / filename
    insight_map = {i.symbol: i for i in (insights or [])}

    fieldnames = [
        "symbol", "asset_type", "score", "gap_pct", "gap_threshold_used",
        "volume_ratio", "price", "premarket_volume", "source",
        "grok_verdict", "grok_confidence", "grok_summary", "grok_sizing", "grok_source",
    ]

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            ins = insight_map.get(r.symbol)
            writer.writerow({
                "symbol": r.symbol,
                "asset_type": r.asset_type,
                "score": f"{r.score:.4f}",
                "gap_pct": f"{r.gap_pct:.6f}",
                "gap_threshold_used": f"{r.gap_threshold_used:.6f}",
                "volume_ratio": f"{r.volume_ratio:.4f}",
                "price": f"{r.price:.2f}",
                "premarket_volume": f"{r.premarket_volume:.0f}",
                "source": r.source,
                "grok_verdict": ins.verdict if ins else "",
                "grok_confidence": f"{ins.confidence:.2f}" if ins else "",
                "grok_summary": ins.summary if ins else "",
                "grok_sizing": ins.sizing_hint if ins else "",
                "grok_source": ins.source if ins else "",
            })

    log.info("Scanner: resultados exportados a %s (%d filas)", path, len(results))
    return path


def save_last_scanner_run(
    results: list[ScanResult],
    insights: list[GrokInsight] | None = None,
    csv_path: Path | None = None,
) -> None:
    """Persiste el último escaneo en ``last_scanner.json`` para el reporte."""
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "csv_path": str(csv_path) if csv_path else None,
        "results": [asdict(r) for r in results],
        "insights": [asdict(i) for i in (insights or [])],
    }
    try:
        LAST_SCANNER_FILE.write_text(json.dumps(payload, indent=2, default=str))
    except OSError as exc:  # noqa: BLE001
        log.warning("No se pudo escribir last_scanner.json: %s", exc)


def finalize_scanner_outputs(
    cfg: Config,
    results: list[ScanResult],
) -> tuple[list[GrokInsight], Path | None]:
    """Ejecuta análisis Grok, exporta CSV y guarda estado según config."""
    insights: list[GrokInsight] = []
    csv_path: Path | None = None

    if results:
        insights = analyze_with_grok(cfg, results)

    if cfg.scanner.export_csv:
        csv_path = export_to_csv(results, insights=insights)

    save_last_scanner_run(results, insights, csv_path)
    return insights, csv_path


def apply_scanner_to_universe(
    cfg: Config,
    scan_results: list[ScanResult],
    open_position_symbols: set[str],
) -> list[Instrument]:
    """Construye un universo reducido: top del scanner + posiciones abiertas.

    Los símbolos del universo base conservan su ``short_proxy`` si aparecen
    en los resultados del scanner. Los nuevos símbolos se añaden sin proxy.
    """
    base_map = {inst.symbol: inst for inst in cfg.universe}
    seen: set[str] = set()
    new_universe: list[Instrument] = []

    for result in scan_results:
        sym = result.symbol
        if sym in seen:
            continue
        seen.add(sym)
        if sym in base_map:
            new_universe.append(base_map[sym])
        else:
            new_universe.append(Instrument(symbol=sym, short_proxy=None))

    for sym in sorted(open_position_symbols):
        if sym not in seen and sym in base_map:
            seen.add(sym)
            new_universe.append(base_map[sym])

    if not new_universe:
        log.warning(
            "Scanner no produjo universo; se mantiene el universo base (%d símbolos)",
            len(cfg.universe),
        )
        return list(cfg.universe)

    log.info(
        "Universo ajustado por scanner: %d símbolos (%s)",
        len(new_universe),
        ", ".join(i.symbol for i in new_universe),
    )
    return new_universe


def _build_grok_prompt(cfg: Config, picks: list[ScanResult]) -> str:
    """Construye el prompt para Grok con contexto de riesgo del bot."""
    picks_data = [asdict(p) for p in picks]
    return (
        "Eres un analista cuantitativo de trading premarket. "
        "Analiza estos candidatos del scanner y responde SOLO con JSON válido "
        "(sin markdown, sin texto extra).\n\n"
        f"Capital aproximado: USD {cfg.initial_capital:.0f}. "
        f"Riesgo por trade: {cfg.risk.risk_per_trade * 100:.1f}%. "
        f"Max posiciones activas: {cfg.risk.max_active_positions}.\n\n"
        "Formato esperado — array JSON:\n"
        '[{"symbol":"TICKER","verdict":"strong|buy|watch|caution|skip",'
        '"confidence":0.0-1.0,"summary":"razón breve en español",'
        '"sizing_hint":"sugerencia de sizing/riesgo en español"}]\n\n'
        f"Candidatos:\n{json.dumps(picks_data, ensure_ascii=False, indent=2)}"
    )


def _call_grok_api(cfg: Config, prompt: str) -> str:
    """Llama a Grok vía xai-sdk; fallback a REST OpenAI-compatible."""
    api_key = grok_key()
    if not api_key:
        raise RuntimeError("GROK_API_KEY no configurada")

    try:
        return _call_grok_xai_sdk(api_key, cfg.api.grok_model, prompt, cfg.api.timeout)
    except ImportError:
        log.debug("xai-sdk no instalado, usando REST directo")
    except Exception as exc:  # noqa: BLE001
        log.warning("xai-sdk falló (%s), intentando REST", exc)

    return _call_grok_rest(api_key, cfg.api.grok_model, prompt, cfg.api.timeout)


def _call_grok_xai_sdk(api_key: str, model: str, prompt: str, timeout: float) -> str:
    from xai_sdk import Client
    from xai_sdk.chat import system, user

    client = Client(api_key=api_key, timeout=timeout)
    chat = client.chat.create(model=model)
    chat.append(system(
        "Respondes únicamente con JSON válido. Analista de trading conservador."
    ))
    chat.append(user(prompt))
    response = chat.sample()
    content = getattr(response, "content", None) or str(response)
    if not content or not content.strip():
        raise RuntimeError("Grok devolvió respuesta vacía")
    return content.strip()


def _call_grok_rest(api_key: str, model: str, prompt: str, timeout: float) -> str:
    """Fallback REST compatible con OpenAI (https://api.x.ai/v1)."""
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "Respondes únicamente con JSON válido."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }).encode("utf-8")

    req = urllib.request.Request(
        _GROK_REST_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "TradingBot/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Grok HTTP {exc.code}: {body[:300]}") from exc

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"Grok REST sin choices: {data}")

    message = choices[0].get("message") or {}
    content = message.get("content", "").strip()
    if not content:
        raise RuntimeError("Grok REST devolvió contenido vacío")
    return content


def _parse_grok_response(raw_text: str, picks: list[ScanResult]) -> list[GrokInsight]:
    """Parsea la respuesta JSON de Grok; rellena símbolos faltantes con heurística."""
    parsed = _extract_json_array(raw_text)
    by_symbol: dict[str, GrokInsight] = {}

    for item in parsed:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).upper().strip()
        if not symbol:
            continue
        verdict = str(item.get("verdict", "watch")).lower().strip()
        if verdict not in VALID_GROK_VERDICTS:
            verdict = "watch"
        confidence = float(item.get("confidence", 0.6))
        confidence = max(0.0, min(confidence, 1.0))
        by_symbol[symbol] = GrokInsight(
            symbol=symbol,
            verdict=verdict,
            summary=str(item.get("summary", "")).strip() or "Sin resumen de Grok.",
            confidence=confidence,
            sizing_hint=str(item.get("sizing_hint", "")).strip(),
            source="grok",
        )

    insights: list[GrokInsight] = []
    for pick in picks:
        if pick.symbol in by_symbol:
            insights.append(by_symbol[pick.symbol])
        else:
            verdict, summary, confidence = _heuristic_grok_analysis(pick)
            insights.append(GrokInsight(
                symbol=pick.symbol,
                verdict=verdict,
                summary=f"[fallback] {summary}",
                confidence=confidence,
                sizing_hint=_default_sizing_hint(pick, cfg_risk_note=True),
                source="heuristic",
            ))
    return insights


def _extract_json_array(text: str) -> list:
    """Extrae un array JSON de la respuesta (tolera markdown)."""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if fence:
        cleaned = fence.group(1).strip()

    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "picks" in data:
            return data["picks"]
        if isinstance(data, dict) and "results" in data:
            return data["results"]
    except json.JSONDecodeError:
        pass

    match = re.search(r"\[[\s\S]*\]", cleaned)
    if match:
        return json.loads(match.group(0))
    raise ValueError(f"No se pudo parsear JSON de Grok: {text[:200]}...")


def _grok_cache_key(picks: list[ScanResult]) -> str:
    payload = [
        {
            "symbol": p.symbol,
            "score": round(p.score, 4),
            "gap": round(p.gap_pct, 5),
            "vol": round(p.volume_ratio, 3),
            "price": round(p.price, 2),
        }
        for p in picks
    ]
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{day}_{digest[:12]}"


def _load_grok_cache(cache_key: str) -> list[GrokInsight] | None:
    if not GROK_CACHE_FILE.exists():
        return None
    try:
        store = json.loads(GROK_CACHE_FILE.read_text(encoding="utf-8"))
        entry = store.get(cache_key)
        if not entry:
            return None
        insights = [GrokInsight(**{**i, "source": "cache"}) for i in entry["insights"]]
        return insights
    except (json.JSONDecodeError, OSError, TypeError, KeyError) as exc:
        log.debug("Caché Grok ilegible: %s", exc)
        return None


def _save_grok_cache(cache_key: str, insights: list[GrokInsight]) -> None:
    try:
        SCANNER_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        store: dict = {}
        if GROK_CACHE_FILE.exists():
            try:
                store = json.loads(GROK_CACHE_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                store = {}
        store[cache_key] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "insights": [asdict(i) for i in insights],
        }
        # Mantener solo las últimas 50 entradas
        if len(store) > 50:
            keys = sorted(store.keys())[-50:]
            store = {k: store[k] for k in keys}
        GROK_CACHE_FILE.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:  # noqa: BLE001
        log.warning("No se pudo guardar caché Grok: %s", exc)


def _heuristic_insights(picks: list[ScanResult]) -> list[GrokInsight]:
    insights: list[GrokInsight] = []
    for pick in picks:
        verdict, summary, confidence = _heuristic_grok_analysis(pick)
        insights.append(GrokInsight(
            symbol=pick.symbol,
            verdict=verdict,
            summary=summary,
            confidence=confidence,
            sizing_hint=_default_sizing_hint(pick),
            source="heuristic",
        ))
        log.info(
            "Grok (heurístico) %s → %s (%.0f%%): %s",
            pick.symbol, verdict, confidence * 100, summary,
        )
    return insights


def _default_sizing_hint(pick: ScanResult, cfg_risk_note: bool = False) -> str:
    base = "1-1.5% del equity" if pick.score < 0.7 else "hasta 2% del equity"
    if cfg_risk_note:
        return f"Considerar {base}; reducir si gap > 8%."
    return f"Considerar {base}; reducir si gap > 8%."


def _heuristic_grok_analysis(pick: ScanResult) -> tuple[str, str, float]:
    """Fallback heurístico si Grok API no está disponible."""
    if pick.score >= 0.75 and pick.volume_ratio >= 3.0:
        return (
            "strong",
            f"Momentum fuerte: gap {pick.gap_pct * 100:+.1f}% con volumen "
            f"{pick.volume_ratio:.1f}x. Candidato prioritario.",
            0.82,
        )
    if pick.score >= 0.55 and pick.gap_pct >= pick.gap_threshold_used * 1.5:
        return (
            "buy",
            f"Gap notable ({pick.gap_pct * 100:+.1f}%) con volumen sólido; "
            f"entrada parcial recomendada.",
            0.68,
        )
    if pick.score >= 0.50:
        return (
            "watch",
            f"Gap notable ({pick.gap_pct * 100:+.1f}%) pero conviene confirmar "
            f"tendencia intradía antes de operar.",
            0.65,
        )
    if pick.volume_ratio < 2.5:
        return (
            "caution",
            f"Volumen relativo moderado ({pick.volume_ratio:.1f}x). "
            f"Riesgo de falso breakout.",
            0.45,
        )
    return (
        "skip",
        f"Señal débil en {pick.symbol}: score {pick.score:.2f}, "
        f"mejor esperar confirmación.",
        0.40,
    )


def _etf_symbols(cfg: Config) -> set[str]:
    syms = set(_KNOWN_ETFS)
    for inst in cfg.universe:
        syms.add(inst.symbol.upper())
        if inst.short_proxy:
            syms.add(inst.short_proxy.upper())
    return syms


def _is_etf(symbol: str, cfg: Config) -> bool:
    return symbol.upper() in _etf_symbols(cfg)


def _gap_threshold_for(symbol: str, scanner, cfg: Config) -> float:
    if _is_etf(symbol, cfg):
        return scanner.etf_gap_threshold
    return scanner.gap_threshold


def _build_candidate_pool(cfg: Config) -> list[str]:
    seen: set[str] = set()
    pool: list[str] = []

    def _add(symbols: list[str]) -> None:
        for sym in symbols:
            s = sym.upper().strip()
            if s and s not in seen:
                seen.add(s)
                pool.append(s)

    if cfg.scanner.include_base_universe:
        _add([inst.symbol for inst in cfg.universe])
        _add([inst.short_proxy for inst in cfg.universe if inst.short_proxy])

    _add(cfg.scanner.watchlist)

    try:
        movers = fetch_market_movers(cfg.data)
        _add(movers)
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudieron cargar market movers: %s", exc)

    return pool


def _passes_filters(quote: PremarketQuote, scanner, gap_threshold: float) -> bool:
    if quote.gap_pct < gap_threshold:
        return False
    if quote.volume_ratio < scanner.volume_multiplier:
        return False
    if not (scanner.min_price <= quote.price <= scanner.max_price):
        return False
    if quote.avg_volume < scanner.min_avg_volume:
        return False
    return True


def _compute_score(quote: PremarketQuote, scanner, gap_threshold: float) -> float:
    """Score normalizado 0–1 combinando gap, volumen y momentum."""
    gap_norm = min(quote.gap_pct / gap_threshold, 3.0) / 3.0 if gap_threshold > 0 else 0.0
    vol_norm = min(quote.volume_ratio / scanner.volume_multiplier, 3.0) / 3.0
    momentum_norm = min(abs(quote.gap_pct) * quote.volume_ratio / 10.0, 1.0)

    return (
        scanner.gap_weight * gap_norm
        + scanner.volume_weight * vol_norm
        + scanner.momentum_weight * momentum_norm
    )


def format_scan_report(results: list[ScanResult], insights: list[GrokInsight] | None = None) -> str:
    """Formatea los resultados del scanner para consola o Telegram."""
    if not results:
        return "Scanner premarket: sin candidatos que pasen los filtros."

    insight_map = {i.symbol: i for i in (insights or [])}
    lines = [f"Scanner premarket: {len(results)} candidato(s)"]
    for i, r in enumerate(results, 1):
        ins = insight_map.get(r.symbol)
        grok_tag = f" | Grok={ins.verdict}" if ins else ""
        lines.append(
            f"  {i:>2}. {r.symbol:<6} [{r.asset_type}] score={r.score:.3f}  "
            f"gap={r.gap_pct * 100:+.1f}%  vol={r.volume_ratio:.1f}x  "
            f"${r.price:.2f}  [{r.source}]{grok_tag}"
        )
    return "\n".join(lines)