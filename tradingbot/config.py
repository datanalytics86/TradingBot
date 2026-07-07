"""Carga y validación de la configuración del bot."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
HALT_FILE = ROOT / "HALT"
STATE_FILE = ROOT / "state.json"
EQUITY_HISTORY_FILE = ROOT / "equity_history.csv"
LAST_RUN_FILE = ROOT / "last_run.json"
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "bot.log"
SCANNER_RESULTS_DIR = ROOT / "scanner_results"
LAST_SCANNER_FILE = ROOT / "last_scanner.json"
SCANNER_HTML_FILE = SCANNER_RESULTS_DIR / "scanner_report.html"
GROK_CACHE_FILE = SCANNER_RESULTS_DIR / "grok_cache.json"
BACKTEST_RESULTS_DIR = ROOT / "backtest_results"
DASHBOARD_HTML_FILE = ROOT / "docs" / "dashboard.html"
OPTIMIZATION_RESULTS_FILE = ROOT / "optimization_results.json"

VALID_DATA_SOURCES = ("polygon", "alpaca", "yfinance")


@dataclass
class Instrument:
    symbol: str
    short_proxy: str | None = None


@dataclass
class StrategyParams:
    ema_fast: int = 20
    ema_slow: int = 50
    trend_filter: int = 200
    atr_period: int = 14
    atr_stop_mult: float = 2.5
    adx_period: int = 14
    min_adx: float = 20.0
    use_di_filter: bool = True
    vol_scale_threshold: float = 1.5
    vol_scale_factor: float = 0.6


@dataclass
class RiskParams:
    risk_per_trade: float = 0.02
    max_position_pct: float = 0.45
    max_total_exposure_pct: float = 0.70
    max_drawdown: float = 0.15
    max_daily_loss: float = 0.03
    max_active_positions: int = 2
    strength_size_floor: float = 0.55


@dataclass
class DataParams:
    lookback_days: int = 400
    retries: int = 3
    retry_delay_seconds: float = 2.0
    primary_source: str = "yfinance"
    fallback_sources: list[str] = field(default_factory=lambda: ["yfinance"])


@dataclass
class BacktestParams:
    slippage_min: float = 0.0005
    slippage_max: float = 0.002
    walk_forward_train_days: int = 252
    walk_forward_test_days: int = 63


@dataclass
class DashboardParams:
    enabled: bool = True
    type: str = "html"


@dataclass
class ApiParams:
    grok_enabled: bool = True
    grok_model: str = "grok-4"
    timeout: float = 15.0


@dataclass
class ScannerParams:
    enabled: bool = False
    gap_threshold: float = 0.05
    etf_gap_threshold: float = 0.025
    volume_multiplier: float = 2.0
    min_price: float = 5.0
    max_price: float = 500.0
    max_symbols: int = 15
    min_avg_volume: float = 500_000.0
    include_base_universe: bool = True
    watchlist: list[str] = field(default_factory=list)
    gap_weight: float = 0.45
    volume_weight: float = 0.35
    momentum_weight: float = 0.20
    export_csv: bool = True
    generate_html: bool = False


@dataclass
class Config:
    mode: str = "paper"
    initial_capital: float = 500.0
    universe: list[Instrument] = field(default_factory=list)
    strategy: StrategyParams = field(default_factory=StrategyParams)
    risk: RiskParams = field(default_factory=RiskParams)
    data: DataParams = field(default_factory=DataParams)
    scanner: ScannerParams = field(default_factory=ScannerParams)
    api: ApiParams = field(default_factory=ApiParams)
    backtest: BacktestParams = field(default_factory=BacktestParams)
    dashboard: DashboardParams = field(default_factory=DashboardParams)

    @property
    def lookback_days(self) -> int:
        return self.data.lookback_days


def _parse_data_params(raw: dict) -> DataParams:
    fallback = raw.get("fallback_sources", ["yfinance"])
    if isinstance(fallback, str):
        fallback = [fallback]
    return DataParams(
        lookback_days=int(raw.get("lookback_days", 400)),
        retries=int(raw.get("retries", 3)),
        retry_delay_seconds=float(raw.get("retry_delay_seconds", 2.0)),
        primary_source=str(raw.get("primary_source", "yfinance")).lower(),
        fallback_sources=[str(s).lower() for s in fallback],
    )


def _parse_gap_threshold(value: float, default: float) -> float:
    """Acepta fracción (0.05) o porcentaje explícito (2.5 → 0.025)."""
    v = float(value) if value is not None else default
    if v > 1.0:
        return v / 100.0
    return v


def _parse_backtest_params(raw: dict) -> BacktestParams:
    return BacktestParams(
        slippage_min=float(raw.get("slippage_min", 0.0005)),
        slippage_max=float(raw.get("slippage_max", 0.002)),
        walk_forward_train_days=int(raw.get("walk_forward_train_days", 252)),
        walk_forward_test_days=int(raw.get("walk_forward_test_days", 63)),
    )


def _parse_dashboard_params(raw: dict) -> DashboardParams:
    dtype = str(raw.get("type", "html")).lower()
    return DashboardParams(
        enabled=bool(raw.get("enabled", True)),
        type=dtype,
    )


def _parse_api_params(raw: dict) -> ApiParams:
    return ApiParams(
        grok_enabled=bool(raw.get("grok_enabled", True)),
        grok_model=str(raw.get("grok_model", "grok-4")),
        timeout=float(raw.get("timeout", 15)),
    )


def _parse_scanner_params(raw: dict) -> ScannerParams:
    return ScannerParams(
        enabled=bool(raw.get("enabled", False)),
        gap_threshold=_parse_gap_threshold(raw.get("gap_threshold"), 0.05),
        etf_gap_threshold=_parse_gap_threshold(raw.get("etf_gap_threshold"), 0.025),
        volume_multiplier=float(raw.get("volume_multiplier", 2.0)),
        min_price=float(raw.get("min_price", 5.0)),
        max_price=float(raw.get("max_price", 500.0)),
        max_symbols=int(raw.get("max_symbols", 15)),
        min_avg_volume=float(raw.get("min_avg_volume", 500_000)),
        include_base_universe=bool(raw.get("include_base_universe", True)),
        watchlist=[str(s).upper() for s in raw.get("watchlist", [])],
        gap_weight=float(raw.get("gap_weight", 0.45)),
        volume_weight=float(raw.get("volume_weight", 0.35)),
        momentum_weight=float(raw.get("momentum_weight", 0.20)),
        export_csv=bool(raw.get("export_csv", True)),
        generate_html=bool(raw.get("generate_html", False)),
    )


def _validate_data_sources(primary: str, fallbacks: list[str]) -> None:
    for source in [primary, *fallbacks]:
        if source not in VALID_DATA_SOURCES:
            raise ValueError(
                f"Fuente de datos inválida: {source!r} "
                f"(debe ser una de {VALID_DATA_SOURCES})"
            )


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else ROOT / "config.yaml"
    raw = yaml.safe_load(path.read_text())

    data_raw = raw.get("data", {})
    scanner_raw = raw.get("scanner", {})
    api_raw = raw.get("api", {})
    backtest_raw = raw.get("backtest", {})
    dashboard_raw = raw.get("dashboard", {})
    data_params = _parse_data_params(data_raw)
    scanner_params = _parse_scanner_params(scanner_raw)
    api_params = _parse_api_params(api_raw)
    backtest_params = _parse_backtest_params(backtest_raw)
    dashboard_params = _parse_dashboard_params(dashboard_raw)

    cfg = Config(
        mode=raw.get("mode", "paper"),
        initial_capital=float(raw.get("initial_capital", 500)),
        universe=[Instrument(**item) for item in raw.get("universe", [])],
        strategy=StrategyParams(**raw.get("strategy", {})),
        risk=RiskParams(**raw.get("risk", {})),
        data=data_params,
        scanner=scanner_params,
        api=api_params,
        backtest=backtest_params,
        dashboard=dashboard_params,
    )

    if cfg.mode not in ("paper", "live"):
        raise ValueError(f"mode inválido: {cfg.mode!r} (debe ser 'paper' o 'live')")
    if not cfg.universe:
        raise ValueError("El universo está vacío: agrega al menos un símbolo en config.yaml")
    if not 0 < cfg.risk.risk_per_trade <= 0.05:
        raise ValueError("risk_per_trade debe estar entre 0 y 5%")
    if not 0 < cfg.risk.max_drawdown <= 0.5:
        raise ValueError("max_drawdown debe estar entre 0 y 50%")
    if not 0 < cfg.risk.max_daily_loss <= 0.2:
        raise ValueError("max_daily_loss debe estar entre 0 y 20%")
    if not 0 < cfg.risk.max_total_exposure_pct <= 1.0:
        raise ValueError("max_total_exposure_pct debe estar entre 0 y 100%")
    if cfg.strategy.ema_fast >= cfg.strategy.ema_slow:
        raise ValueError("ema_fast debe ser menor que ema_slow")
    if cfg.risk.max_active_positions < 1:
        raise ValueError("max_active_positions debe ser >= 1")
    if not 0 < cfg.risk.strength_size_floor <= 1.0:
        raise ValueError("strength_size_floor debe estar entre 0 y 1")

    _validate_data_sources(cfg.data.primary_source, cfg.data.fallback_sources)

    if cfg.scanner.gap_threshold <= 0:
        raise ValueError("scanner.gap_threshold debe ser > 0")
    if cfg.scanner.etf_gap_threshold <= 0:
        raise ValueError("scanner.etf_gap_threshold debe ser > 0")
    if cfg.scanner.volume_multiplier <= 0:
        raise ValueError("scanner.volume_multiplier debe ser > 0")
    if cfg.scanner.min_price <= 0 or cfg.scanner.max_price <= cfg.scanner.min_price:
        raise ValueError("scanner.min_price/max_price inválidos")
    if cfg.scanner.max_symbols < 1:
        raise ValueError("scanner.max_symbols debe ser >= 1")
    if cfg.api.timeout <= 0:
        raise ValueError("api.timeout debe ser > 0")
    if not cfg.api.grok_model.strip():
        raise ValueError("api.grok_model no puede estar vacío")
    if cfg.backtest.slippage_min <= 0 or cfg.backtest.slippage_max < cfg.backtest.slippage_min:
        raise ValueError("backtest.slippage_min/max inválidos")
    if cfg.dashboard.type not in ("html", "basic"):
        raise ValueError("dashboard.type debe ser 'html' o 'basic'")
    weight_sum = cfg.scanner.gap_weight + cfg.scanner.volume_weight + cfg.scanner.momentum_weight
    if not 0.99 <= weight_sum <= 1.01:
        raise ValueError(
            f"Los pesos del scanner deben sumar 1.0 (actual: {weight_sum:.2f})"
        )

    return cfg


def alpaca_keys() -> tuple[str, str]:
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise RuntimeError(
            "Faltan ALPACA_API_KEY / ALPACA_SECRET_KEY en el entorno (ver .env.example)"
        )
    return key, secret


def polygon_key() -> str | None:
    """Devuelve la API key de Polygon si está configurada (opcional)."""
    key = os.environ.get("POLYGON_API_KEY", "").strip()
    return key or None


def grok_key() -> str | None:
    """Devuelve la API key de Grok/xAI (GROK_API_KEY o XAI_API_KEY)."""
    key = os.environ.get("GROK_API_KEY", "").strip() or os.environ.get("XAI_API_KEY", "").strip()
    return key or None