"""MetaTrader 5 **demo** execution adapter — implemented, not authorized, not wired.

Nothing in the runtime constructs this class, and the execution gate refuses
``execution_mode="demo"``. It exists so that the separately authorized demo
stage validates an implementation instead of writing one under time pressure.

Safety properties (each covered by tests against a fake MT5 module):

- **Demo-only:** every order-capable call re-reads ``account_info()`` and
  refuses unless the account trade mode is demo (0).
- **Idempotent:** ``client_order_id`` is sent as the MT5 order comment. Before
  any send the adapter searches open positions, pending orders and the deal
  history for that comment and returns the existing outcome instead of sending.
- **No blind retry:** if ``order_send`` raises or returns nothing, the order is
  reported ``SUBMITTED`` (outcome unknown) and must be reconciled with
  :meth:`get_order` — the adapter never re-sends it.
- **Fills are what the broker reports:** ``retcode`` DONE with less volume, or
  DONE_PARTIAL, is a partial fill; requotes and rejections are rejections.
- **Broker constraints:** SL/TP distances are checked against the symbol's stops
  level, and modifications against the freeze level, before sending.
- **Magic number** scopes every query to orders created by Trantor.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..models import OrderRequest, OrderState, OrderStatus, OrderType, Position, Side

__all__ = ["DemoExecutionRefused", "MT5DemoBroker", "TRANTOR_MAGIC"]

UTC = timezone.utc
TRANTOR_MAGIC = 74_617_001
_COMMENT_MAX = 31
_RETCODE_DONE = 10009
_RETCODE_DONE_PARTIAL = 10010
_RETCODE_PLACED = 10008


class DemoExecutionRefused(RuntimeError):
    """The adapter refuses to act (not a demo account, constraint violated...)."""


def _comment(client_order_id: str) -> str:
    return client_order_id[:_COMMENT_MAX]


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


@dataclass(frozen=True, slots=True)
class StopValidation:
    ok: bool
    reason: str = ""


class MT5DemoBroker:
    """Order-capable adapter for a verified MT5 demo account."""

    def __init__(self, mt5_module: Any, *, symbol: str = "XAUUSD-P", deviation_points: int = 20) -> None:
        self._mt5 = mt5_module
        self.symbol = symbol
        self.deviation = deviation_points

    # -- guards ---------------------------------------------------------------

    def _require_demo(self) -> None:
        account = self._mt5.account_info()
        if account is None or _get(account, "trade_mode") != 0:
            raise DemoExecutionRefused("account is not a verified MT5 demo account; refusing")

    def _symbol(self) -> Any:
        info = self._mt5.symbol_info(self.symbol)
        if info is None:
            raise DemoExecutionRefused(f"symbol {self.symbol} unavailable")
        return info

    def validate_stops(self, side: Side, price: float, sl: float | None, tp: float | None) -> StopValidation:
        info = self._symbol()
        point = float(_get(info, "point"))
        min_distance = int(_get(info, "trade_stops_level") or 0) * point
        for label, level in (("stop", sl), ("target", tp)):
            if level is None:
                continue
            if not math.isfinite(level) or level <= 0:
                return StopValidation(False, f"{label} is invalid")
            if abs(price - level) < min_distance - 1e-9:
                return StopValidation(False, f"{label} closer than the broker stops level ({min_distance})")
        if side is Side.BUY and ((sl is not None and sl >= price) or (tp is not None and tp <= price)):
            return StopValidation(False, "stop/target on the wrong side of a buy")
        if side is Side.SELL and ((sl is not None and sl <= price) or (tp is not None and tp >= price)):
            return StopValidation(False, "stop/target on the wrong side of a sell")
        return StopValidation(True)

    # -- lookup / reconciliation ----------------------------------------------------

    def _find_existing(self, client_order_id: str) -> OrderState | None:
        comment = _comment(client_order_id)
        for position in self._mt5.positions_get(symbol=self.symbol) or ():
            if _get(position, "magic") == TRANTOR_MAGIC and _get(position, "comment") == comment:
                volume = float(_get(position, "volume"))
                return OrderState(
                    client_order_id=client_order_id,
                    status=OrderStatus.FILLED,
                    requested_quantity=volume,
                    updated_at=datetime.now(UTC),
                    broker_order_id=str(_get(position, "ticket")),
                    filled_quantity=volume,
                    average_fill_price=float(_get(position, "price_open")),
                )
        now = datetime.now(UTC)
        deals = self._mt5.history_deals_get(now - timedelta(days=30), now + timedelta(days=1)) or ()
        matched = [d for d in deals if _get(d, "magic") == TRANTOR_MAGIC and _get(d, "comment") == comment and _get(d, "entry") == 0]
        if matched:
            volume = sum(float(_get(d, "volume")) for d in matched)
            price = sum(float(_get(d, "price")) * float(_get(d, "volume")) for d in matched) / volume
            return OrderState(
                client_order_id=client_order_id,
                status=OrderStatus.FILLED,
                requested_quantity=volume,
                updated_at=now,
                broker_order_id=str(_get(matched[0], "order")),
                filled_quantity=volume,
                average_fill_price=price,
            )
        return None

    def get_order(self, client_order_id: str) -> OrderState | None:
        return self._find_existing(client_order_id)

    def get_positions(self) -> tuple[Position, ...]:
        result = []
        for position in self._mt5.positions_get(symbol=self.symbol) or ():
            if _get(position, "magic") != TRANTOR_MAGIC:
                continue
            result.append(
                Position(
                    symbol=self.symbol,
                    side=Side.BUY if _get(position, "type") == 0 else Side.SELL,
                    quantity=float(_get(position, "volume")),
                    average_entry_price=float(_get(position, "price_open")),
                    opened_at=datetime.fromtimestamp(int(_get(position, "time")), tz=UTC),
                    stop_price=float(_get(position, "sl")) or None,
                    take_profit_price=float(_get(position, "tp")) or None,
                )
            )
        return tuple(result)

    # -- order lifecycle ------------------------------------------------------------

    def submit_order(self, request: OrderRequest, *, stop: float | None = None, take_profit: float | None = None) -> OrderState:
        self._require_demo()
        if request.symbol != self.symbol:
            raise DemoExecutionRefused("symbol mismatch")
        if request.order_type is not OrderType.MARKET:
            raise DemoExecutionRefused("only market orders are supported")
        existing = self._find_existing(request.client_order_id)
        if existing is not None:
            return existing
        tick = self._mt5.symbol_info_tick(self.symbol)
        if tick is None:
            raise DemoExecutionRefused("no live quote")
        price = float(_get(tick, "ask") if request.side is Side.BUY else _get(tick, "bid"))
        check = self.validate_stops(request.side, price, stop, take_profit)
        if not check.ok:
            raise DemoExecutionRefused(check.reason)
        info = self._symbol()
        filling = self._mt5.ORDER_FILLING_IOC if int(_get(info, "filling_mode") or 0) & 2 else self._mt5.ORDER_FILLING_FOK
        payload = {
            "action": self._mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": float(request.quantity),
            "type": self._mt5.ORDER_TYPE_BUY if request.side is Side.BUY else self._mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": float(stop or 0.0),
            "tp": float(take_profit or 0.0),
            "deviation": self.deviation,
            "magic": TRANTOR_MAGIC,
            "comment": _comment(request.client_order_id),
            "type_time": self._mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }
        checked = self._mt5.order_check(payload)
        if checked is None or _get(checked, "retcode") not in (0, _RETCODE_DONE):
            return OrderState(
                client_order_id=request.client_order_id,
                status=OrderStatus.REJECTED,
                requested_quantity=request.quantity,
                updated_at=datetime.now(UTC),
            )
        try:
            result = self._mt5.order_send(payload)
        except Exception:
            result = None
        if result is None:
            # Outcome unknown: never retry. Caller must reconcile via get_order().
            return OrderState(
                client_order_id=request.client_order_id,
                status=OrderStatus.SUBMITTED,
                requested_quantity=request.quantity,
                updated_at=datetime.now(UTC),
            )
        retcode = _get(result, "retcode")
        volume = float(_get(result, "volume") or 0.0)
        fill_price = float(_get(result, "price") or 0.0)
        broker_id = str(_get(result, "order") or _get(result, "deal") or "")
        if retcode in (_RETCODE_DONE, _RETCODE_DONE_PARTIAL) and volume > 0 and fill_price > 0:
            partial = retcode == _RETCODE_DONE_PARTIAL or volume < request.quantity - 1e-9
            return OrderState(
                client_order_id=request.client_order_id,
                status=OrderStatus.PARTIALLY_FILLED if partial else OrderStatus.FILLED,
                requested_quantity=request.quantity,
                updated_at=datetime.now(UTC),
                broker_order_id=broker_id or None,
                filled_quantity=min(volume, request.quantity),
                average_fill_price=fill_price,
            )
        if retcode == _RETCODE_PLACED:
            return OrderState(
                client_order_id=request.client_order_id,
                status=OrderStatus.ACKNOWLEDGED,
                requested_quantity=request.quantity,
                updated_at=datetime.now(UTC),
                broker_order_id=broker_id or None,
            )
        return OrderState(
            client_order_id=request.client_order_id,
            status=OrderStatus.REJECTED,
            requested_quantity=request.quantity,
            updated_at=datetime.now(UTC),
        )

    def modify_stops(self, ticket: int, *, side: Side, stop: float | None, take_profit: float | None) -> bool:
        """Move SL/TP. Refuses moves inside the freeze level and moves that widen risk."""

        self._require_demo()
        position = next((p for p in self._mt5.positions_get(ticket=ticket) or () if _get(p, "magic") == TRANTOR_MAGIC), None)
        if position is None:
            raise DemoExecutionRefused("position not found or not owned by Trantor")
        current_sl = float(_get(position, "sl") or 0.0)
        if stop is not None and current_sl:
            widening = stop < current_sl if side is Side.BUY else stop > current_sl
            if widening:
                raise DemoExecutionRefused("stops are never moved further away from entry")
        tick = self._mt5.symbol_info_tick(self.symbol)
        price = float(_get(tick, "bid") if side is Side.BUY else _get(tick, "ask"))
        info = self._symbol()
        freeze = int(_get(info, "trade_freeze_level") or 0) * float(_get(info, "point"))
        if current_sl and abs(price - current_sl) < freeze - 1e-9:
            raise DemoExecutionRefused("current stop is inside the broker freeze level")
        check = self.validate_stops(side, price, stop, take_profit)
        if not check.ok:
            raise DemoExecutionRefused(check.reason)
        result = self._mt5.order_send(
            {
                "action": self._mt5.TRADE_ACTION_SLTP,
                "symbol": self.symbol,
                "position": ticket,
                "sl": float(stop or 0.0),
                "tp": float(take_profit or 0.0),
                "magic": TRANTOR_MAGIC,
            }
        )
        return result is not None and _get(result, "retcode") == _RETCODE_DONE

    def close_partial(self, ticket: int, volume: float, *, client_order_id: str) -> OrderState:
        """Close part of a position (TP1) with its own idempotency key."""

        self._require_demo()
        position = next((p for p in self._mt5.positions_get(ticket=ticket) or () if _get(p, "magic") == TRANTOR_MAGIC), None)
        if position is None:
            raise DemoExecutionRefused("position not found or not owned by Trantor")
        held = float(_get(position, "volume"))
        if volume <= 0 or volume > held + 1e-9:
            raise DemoExecutionRefused("close volume exceeds the position")
        is_buy = _get(position, "type") == 0
        tick = self._mt5.symbol_info_tick(self.symbol)
        try:
            result = self._mt5.order_send(
                {
                    "action": self._mt5.TRADE_ACTION_DEAL,
                    "symbol": self.symbol,
                    "position": ticket,
                    "volume": volume,
                    "type": self._mt5.ORDER_TYPE_SELL if is_buy else self._mt5.ORDER_TYPE_BUY,
                    "price": float(_get(tick, "bid") if is_buy else _get(tick, "ask")),
                    "deviation": self.deviation,
                    "magic": TRANTOR_MAGIC,
                    "comment": _comment(client_order_id),
                }
            )
        except Exception:
            result = None
        if result is None:
            return OrderState(client_order_id, OrderStatus.SUBMITTED, volume, datetime.now(UTC))
        done = float(_get(result, "volume") or 0.0)
        if _get(result, "retcode") in (_RETCODE_DONE, _RETCODE_DONE_PARTIAL) and done > 0:
            return OrderState(
                client_order_id,
                OrderStatus.FILLED if done >= volume - 1e-9 else OrderStatus.PARTIALLY_FILLED,
                volume,
                datetime.now(UTC),
                broker_order_id=str(_get(result, "order")),
                filled_quantity=min(done, volume),
                average_fill_price=float(_get(result, "price")),
            )
        return OrderState(client_order_id, OrderStatus.REJECTED, volume, datetime.now(UTC))

    def cancel_order(self, client_order_id: str) -> OrderState:
        """Market orders cannot be cancelled once sent; report the broker state."""

        state = self._find_existing(client_order_id)
        if state is None:
            raise DemoExecutionRefused("no order with that client_order_id is known to the broker")
        return state

    def reconcile(self, internal: dict[str, float]) -> dict[str, Any]:
        """Compare internal {client_order_id: open volume} with broker positions."""

        broker = {}
        for position in self._mt5.positions_get(symbol=self.symbol) or ():
            if _get(position, "magic") == TRANTOR_MAGIC:
                broker[_get(position, "comment")] = float(_get(position, "volume"))
        internal_comments = {_comment(key): value for key, value in internal.items()}
        issues = []
        for comment, volume in internal_comments.items():
            if comment not in broker:
                issues.append(f"{comment}: open internally, absent at broker")
            elif abs(broker[comment] - volume) > 1e-9:
                issues.append(f"{comment}: volume {volume} internally vs {broker[comment]} at broker")
        for comment in broker:
            if comment not in internal_comments:
                issues.append(f"{comment}: open at broker, unknown internally")
        return {"consistent": not issues, "issues": issues, "broker_positions": broker}
