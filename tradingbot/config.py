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
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "bot.log"


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


@dataclass
class RiskParams:
    risk_per_trade: float = 0.02
    max_position_pct: float = 0.45
    max_drawdown: float = 0.15


@dataclass
class Config:
    mode: str = "paper"
    initial_capital: float = 500.0
    universe: list[Instrument] = field(default_factory=list)
    strategy: StrategyParams = field(default_factory=StrategyParams)
    risk: RiskParams = field(default_factory=RiskParams)
    lookback_days: int = 400


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else ROOT / "config.yaml"
    raw = yaml.safe_load(path.read_text())

    cfg = Config(
        mode=raw.get("mode", "paper"),
        initial_capital=float(raw.get("initial_capital", 500)),
        universe=[Instrument(**item) for item in raw.get("universe", [])],
        strategy=StrategyParams(**raw.get("strategy", {})),
        risk=RiskParams(**raw.get("risk", {})),
        lookback_days=int(raw.get("data", {}).get("lookback_days", 400)),
    )

    if cfg.mode not in ("paper", "live"):
        raise ValueError(f"mode inválido: {cfg.mode!r} (debe ser 'paper' o 'live')")
    if not cfg.universe:
        raise ValueError("El universo está vacío: agrega al menos un símbolo en config.yaml")
    if not 0 < cfg.risk.risk_per_trade <= 0.05:
        raise ValueError("risk_per_trade debe estar entre 0 y 5%")
    if not 0 < cfg.risk.max_drawdown <= 0.5:
        raise ValueError("max_drawdown debe estar entre 0 y 50%")
    if cfg.strategy.ema_fast >= cfg.strategy.ema_slow:
        raise ValueError("ema_fast debe ser menor que ema_slow")
    return cfg


def alpaca_keys() -> tuple[str, str]:
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise RuntimeError(
            "Faltan ALPACA_API_KEY / ALPACA_SECRET_KEY en el entorno (ver .env.example)"
        )
    return key, secret
