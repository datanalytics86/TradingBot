"""Wrapper delgado sobre la API de Alpaca (trading).

El import de `alpaca-py` es perezoso (dentro de los métodos) para que el
resto del bot (config, data, strategy, risk, backtest, tests) funcione
aunque la librería no esté instalada. Solo se necesita `alpaca-py` de
verdad para operar en vivo/paper.
"""
from __future__ import annotations

from typing import Optional


class AlpacaBroker:
    """Cliente de trading de Alpaca en modo paper o live."""

    def __init__(self, paper: bool = True):
        from tradingbot.config import alpaca_keys

        key, secret = alpaca_keys()
        self.paper = paper
        self._client = self._make_client(key, secret, paper)

    @staticmethod
    def _make_client(key: str, secret: str, paper: bool):
        from alpaca.trading.client import TradingClient

        return TradingClient(key, secret, paper=paper)

    def get_equity(self) -> float:
        """Devuelve el equity (valor total de la cuenta) actual."""
        account = self._client.get_account()
        return float(account.equity)

    def get_position(self, symbol: str) -> float:
        """Devuelve la cantidad (con signo) de la posición actual en `symbol`.

        0.0 si no hay posición abierta.
        """
        try:
            position = self._client.get_open_position(symbol)
        except Exception as exc:  # noqa: BLE001 - la excepción de alpaca no es estable entre versiones
            if "position does not exist" in str(exc).lower() or "404" in str(exc):
                return 0.0
            raise
        return float(position.qty)

    def market_order(self, symbol: str, qty: float, side: str):
        """Envía una orden a mercado DAY. `side` es "buy" o "sell".

        Si `qty` tiene decimales (fractional shares) se envía tal cual;
        Alpaca soporta fractional trading para market orders DAY.
        """
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        request = MarketOrderRequest(
            symbol=symbol,
            qty=abs(qty),
            side=order_side,
            time_in_force=TimeInForce.DAY,
        )
        return self._client.submit_order(request)

    def close_position(self, symbol: str) -> Optional[object]:
        """Cierra (liquida) la posición abierta en `symbol`, si la hay."""
        try:
            return self._client.close_position(symbol)
        except Exception as exc:  # noqa: BLE001
            if "position does not exist" in str(exc).lower() or "404" in str(exc):
                return None
            raise

    def close_all_positions(self) -> Optional[object]:
        """Cierra todas las posiciones abiertas en la cuenta."""
        try:
            return self._client.close_all_positions(cancel_orders=True)
        except Exception as exc:  # noqa: BLE001
            if "position does not exist" in str(exc).lower() or "404" in str(exc):
                return None
            raise
