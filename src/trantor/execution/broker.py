"""Broker contract for future execution adapters.

Only this package may know broker-specific order APIs. Strategy code produces
``TradeSignal`` objects and must never import this module or submit orders.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import OrderRequest, OrderState, Position

__all__ = ["Broker", "BrokerUnavailableError", "DisabledBroker"]


class BrokerUnavailableError(RuntimeError):
    """No enabled broker adapter is available for order operations."""


@runtime_checkable
class Broker(Protocol):
    """Minimal asynchronous-capable contract for a future broker adapter."""

    def submit_order(self, request: OrderRequest) -> OrderState:
        """Submit once and return the broker's current order snapshot."""

    def get_order(self, client_order_id: str) -> OrderState | None:
        """Reconcile an order by its idempotency key, if the broker knows it."""

    def cancel_order(self, client_order_id: str) -> OrderState:
        """Request cancellation and return the resulting order snapshot."""

    def get_positions(self) -> tuple[Position, ...]:
        """Return current broker-reported positions."""


class DisabledBroker:
    """Fail-closed default adapter used until a paper broker is implemented."""

    def submit_order(self, request: OrderRequest) -> OrderState:
        raise BrokerUnavailableError(
            "broker submission is disabled: no paper broker adapter is configured"
        )

    def get_order(self, client_order_id: str) -> OrderState | None:
        raise BrokerUnavailableError(
            "broker reads are disabled: no paper broker adapter is configured"
        )

    def cancel_order(self, client_order_id: str) -> OrderState:
        raise BrokerUnavailableError(
            "broker cancellation is disabled: no paper broker adapter is configured"
        )

    def get_positions(self) -> tuple[Position, ...]:
        raise BrokerUnavailableError(
            "broker reads are disabled: no paper broker adapter is configured"
        )
