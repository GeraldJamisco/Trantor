"""In-process paper broker. It never contacts a broker or sends an order.

Fill model (declared, conservative, identical to the research backtester):
market orders fill completely at the supplied reference price — the open of
the M15 bar after the decision. No slippage, spread or commission is applied;
paper results are labelled gross.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from ..models import OrderRequest, OrderState, OrderStatus, OrderType, Position

__all__ = ["PaperBroker", "PaperFillError"]

UTC = timezone.utc


class PaperFillError(RuntimeError):
    """The paper broker cannot fill the request."""


class PaperBroker:
    """Implements the :class:`trantor.execution.broker.Broker` protocol."""

    is_paper = True

    def __init__(self, positions: Callable[[], tuple[Position, ...]] | None = None) -> None:
        self._orders: dict[str, OrderState] = {}
        self._reference: dict[str, float] = {}
        self._positions = positions or (lambda: ())

    def set_reference_price(self, client_order_id: str, price: float) -> None:
        if price <= 0:
            raise PaperFillError("reference price must be positive")
        self._reference[client_order_id] = price

    def submit_order(self, request: OrderRequest) -> OrderState:
        if request.client_order_id in self._orders:
            # Idempotent: a repeated submission returns the original outcome.
            return self._orders[request.client_order_id]
        if request.order_type is not OrderType.MARKET:
            state = OrderState(
                client_order_id=request.client_order_id,
                status=OrderStatus.REJECTED,
                requested_quantity=request.quantity,
                updated_at=datetime.now(UTC),
            )
            self._orders[request.client_order_id] = state
            return state
        price = self._reference.get(request.client_order_id)
        if price is None:
            state = OrderState(
                client_order_id=request.client_order_id,
                status=OrderStatus.REJECTED,
                requested_quantity=request.quantity,
                updated_at=datetime.now(UTC),
            )
        else:
            state = OrderState(
                client_order_id=request.client_order_id,
                status=OrderStatus.FILLED,
                requested_quantity=request.quantity,
                updated_at=datetime.now(UTC),
                broker_order_id=f"paper-{request.client_order_id}",
                filled_quantity=request.quantity,
                average_fill_price=price,
            )
        self._orders[request.client_order_id] = state
        return state

    def get_order(self, client_order_id: str) -> OrderState | None:
        return self._orders.get(client_order_id)

    def cancel_order(self, client_order_id: str) -> OrderState:
        state = self._orders.get(client_order_id)
        if state is None:
            raise PaperFillError(f"unknown paper order {client_order_id!r}")
        return state

    def get_positions(self) -> tuple[Position, ...]:
        return self._positions()
