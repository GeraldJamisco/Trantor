"""Explicit broker-metadata verification for XAUUSD-P.

Nothing is treated as verified because it exists in configuration. Each fact
moves through these states:

``unavailable``   the broker did not provide it (or the API cannot expose it);
``observed``      read live from the broker and internally consistent, but not
                  yet confirmed by the owner against the broker's published
                  contract specification;
``verified``      observed live, internally consistent (including the broker's
                  own ``order_calc_profit`` / ``order_calc_margin``), and equal
                  to the owner-confirmed expectation file;
``mismatch``      live value disagrees with the expectation or with an
                  independent consistency check;
``failed``        the check itself could not run.

Monetary sizing requires every economics field to be ``verified``.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

__all__ = [
    "BrokerVerificationReport",
    "CheckStatus",
    "ECONOMICS_FIELDS",
    "VerifiedEconomics",
    "load_expectations",
    "proposed_expectations",
    "verify_broker",
]

UTC = timezone.utc

ECONOMICS_FIELDS = (
    "trade_contract_size",
    "trade_tick_size",
    "trade_tick_value",
    "volume_min",
    "volume_max",
    "volume_step",
    "digits",
    "point",
    "currency_profit",
    "currency_margin",
    "account_currency",
    "trade_calc_mode",
)
_SYMBOL_TRADE_MODES = {0: "disabled", 1: "long_only", 2: "short_only", 3: "close_only", 4: "full"}
_CALC_MODES = {
    0: "forex",
    1: "futures",
    2: "cfd",
    3: "cfd_index",
    4: "cfd_leverage",
    5: "forex_no_leverage",
    32: "exchange_stocks",
    33: "exchange_futures",
}
_EXECUTION_MODES = {0: "request", 1: "instant", 2: "market", 3: "exchange"}
_FILLING_FLAGS = {1: "fok", 2: "ioc", 4: "boc"}
_SWAP_MODES = {
    0: "disabled",
    1: "points",
    2: "currency_symbol",
    3: "currency_margin",
    4: "currency_deposit",
    5: "interest_current",
    6: "interest_open",
    7: "reopen_current",
    8: "reopen_bid",
    9: "currency_profit",
}


class CheckStatus(StrEnum):
    VERIFIED = "verified"
    OBSERVED = "observed"
    MISMATCH = "mismatch"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    status: CheckStatus
    observed: Any
    expected: Any = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "observed": self.observed,
            "expected": self.expected,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class VerifiedEconomics:
    """Contract economics that passed every verification step."""

    contract_size: float
    tick_size: float
    tick_value: float
    volume_min: float
    volume_max: float
    volume_step: float
    account_currency: str
    digits: int
    stops_level_points: int
    freeze_level_points: int
    point: float

    @property
    def value_per_price_unit_per_lot(self) -> float:
        return self.tick_value / self.tick_size


@dataclass
class BrokerVerificationReport:
    generated_at: datetime
    symbol: str
    checks: list[Check] = field(default_factory=list)
    observed: dict[str, Any] = field(default_factory=dict)
    expectations_source: str | None = None

    def add(self, check: Check) -> None:
        self.checks.append(check)

    def check(self, name: str) -> Check | None:
        for item in self.checks:
            if item.name == name:
                return item
        return None

    @property
    def economics_verified(self) -> bool:
        statuses = [self.check(f"economics.{name}") for name in ECONOMICS_FIELDS]
        consistency = [
            self.check("consistency.tick_value"),
            self.check("consistency.order_calc_profit"),
        ]
        return all(item is not None and item.status is CheckStatus.VERIFIED for item in statuses) and all(
            item is not None and item.status in (CheckStatus.VERIFIED, CheckStatus.OBSERVED)
            for item in consistency
        )

    @property
    def identity_ok(self) -> bool:
        item = self.check("identity.symbol")
        return item is not None and item.status in (CheckStatus.VERIFIED, CheckStatus.OBSERVED)

    @property
    def overall(self) -> str:
        if any(item.status is CheckStatus.MISMATCH for item in self.checks):
            return "mismatch"
        if not self.identity_ok:
            return "identity_unavailable"
        if self.economics_verified:
            return "economics_verified"
        return "observed_unconfirmed"

    def economics(self) -> VerifiedEconomics | None:
        if not self.economics_verified:
            return None
        o = self.observed
        return VerifiedEconomics(
            contract_size=float(o["trade_contract_size"]),
            tick_size=float(o["trade_tick_size"]),
            tick_value=float(o["trade_tick_value"]),
            volume_min=float(o["volume_min"]),
            volume_max=float(o["volume_max"]),
            volume_step=float(o["volume_step"]),
            account_currency=str(o["account_currency"]),
            digits=int(o["digits"]),
            stops_level_points=int(o.get("trade_stops_level") or 0),
            freeze_level_points=int(o.get("trade_freeze_level") or 0),
            point=float(o["point"]),
        )

    @property
    def report_id(self) -> str:
        payload = json.dumps(
            [item.as_dict() for item in self.checks], sort_keys=True, default=str
        ).encode()
        return hashlib.sha256(payload).hexdigest()[:24]

    def as_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "generated_at": self.generated_at.isoformat(),
            "symbol": self.symbol,
            "overall": self.overall,
            "economics_verified": self.economics_verified,
            "monetary_sizing": "available" if self.economics_verified else "blocked",
            "expectations_source": self.expectations_source,
            "checks": [item.as_dict() for item in self.checks],
            "observed": self.observed,
        }


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _close(a: float, b: float, rel: float = 1e-6) -> bool:
    return math.isclose(a, b, rel_tol=rel, abs_tol=1e-12)


def load_expectations(path: str | Path) -> dict[str, Any] | None:
    """Load the owner-confirmed expectation file; None if absent.

    The file must declare ``confirmed_by_owner: true`` and a ``source`` that
    names the broker document the values were checked against.
    """

    file = Path(path)
    if not file.exists():
        return None
    data = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("expectations file must be a JSON object")
    if data.get("confirmed_by_owner") is not True:
        return None
    if not isinstance(data.get("source"), str) or not data["source"].strip():
        raise ValueError("expectations file must name its source document")
    values = data.get("values")
    if not isinstance(values, dict):
        raise ValueError("expectations file must contain a 'values' object")
    return data


def proposed_expectations(report: BrokerVerificationReport) -> dict[str, Any]:
    """A template the owner can review against the broker specification.

    It is written with ``confirmed_by_owner: false`` and therefore verifies
    nothing until the owner edits and confirms it.
    """

    return {
        "confirmed_by_owner": False,
        "source": "",
        "instructions": (
            "Compare every value with Fortune Prime's published XAUUSD-P contract "
            "specification and your account statement. Correct anything that "
            "differs, fill in 'source', then set confirmed_by_owner to true."
        ),
        "values": {name: report.observed.get(name) for name in ECONOMICS_FIELDS},
        "generated_from_report": report.report_id,
    }


def verify_broker(
    *,
    symbol: str,
    symbol_info: Mapping[str, Any] | None,
    account: Mapping[str, Any] | None,
    tick: Mapping[str, Any] | None,
    calc_profit: Any = None,
    calc_margin: Any = None,
    expectations: Mapping[str, Any] | None = None,
    forming_m15_close: float | None = None,
    commissions: list[Mapping[str, Any]] | None = None,
    time_basis: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> BrokerVerificationReport:
    """Run every verification check from already-fetched broker data."""

    report = BrokerVerificationReport(generated_at=now or datetime.now(UTC), symbol=symbol)
    expected_values: Mapping[str, Any] = {}
    if expectations is not None:
        expected_values = expectations.get("values", {})
        report.expectations_source = str(expectations.get("source"))

    if not symbol_info:
        report.add(Check("identity.symbol", CheckStatus.UNAVAILABLE, None, symbol, "symbol_info returned nothing"))
        return report
    info = dict(symbol_info)
    acct = dict(account or {})
    observed = {
        "name": info.get("name"),
        "description": info.get("description"),
        "path": info.get("path"),
        "custom": info.get("custom"),
        "currency_base": info.get("currency_base"),
        "currency_profit": info.get("currency_profit"),
        "currency_margin": info.get("currency_margin"),
        "trade_contract_size": _num(info.get("trade_contract_size")),
        "trade_tick_size": _num(info.get("trade_tick_size")),
        "trade_tick_value": _num(info.get("trade_tick_value")),
        "trade_tick_value_profit": _num(info.get("trade_tick_value_profit")),
        "trade_tick_value_loss": _num(info.get("trade_tick_value_loss")),
        "point": _num(info.get("point")),
        "digits": info.get("digits"),
        "volume_min": _num(info.get("volume_min")),
        "volume_max": _num(info.get("volume_max")),
        "volume_step": _num(info.get("volume_step")),
        "volume_limit": _num(info.get("volume_limit")),
        "trade_mode": _SYMBOL_TRADE_MODES.get(info.get("trade_mode"), info.get("trade_mode")),
        "trade_calc_mode": _CALC_MODES.get(info.get("trade_calc_mode"), info.get("trade_calc_mode")),
        "trade_exemode": _EXECUTION_MODES.get(info.get("trade_exemode"), info.get("trade_exemode")),
        "filling_modes": [
            label for flag, label in _FILLING_FLAGS.items() if int(info.get("filling_mode") or 0) & flag
        ],
        "trade_stops_level": info.get("trade_stops_level"),
        "trade_freeze_level": info.get("trade_freeze_level"),
        "swap_mode": _SWAP_MODES.get(info.get("swap_mode"), info.get("swap_mode")),
        "swap_long": _num(info.get("swap_long")),
        "swap_short": _num(info.get("swap_short")),
        "swap_rollover3days": info.get("swap_rollover3days"),
        "margin_initial": _num(info.get("margin_initial")),
        "margin_maintenance": _num(info.get("margin_maintenance")),
        "spread_points": info.get("spread"),
        "spread_float": info.get("spread_float"),
        "account_currency": acct.get("currency"),
        "account_trade_mode": acct.get("trade_mode"),
        "account_leverage": acct.get("leverage"),
        "account_margin_mode": acct.get("margin_mode"),
    }
    report.observed = observed

    # Identity ---------------------------------------------------------------
    name_ok = observed["name"] == symbol and observed["custom"] in (False, None, 0)
    report.add(
        Check(
            "identity.symbol",
            CheckStatus.VERIFIED if name_ok else CheckStatus.MISMATCH,
            {"name": observed["name"], "custom": observed["custom"]},
            symbol,
            "exact broker symbol, not a custom symbol",
        )
    )
    # Some brokers report a CFD's base currency as the quote currency. Gold
    # identity is then taken from the broker's own description and symbol path.
    described_gold = "gold" in str(observed["description"] or "").lower() and "metal" in str(
        observed["path"] or ""
    ).lower()
    base_ok = observed["currency_base"] == "XAU" or described_gold
    gold = base_ok and observed["currency_profit"] == "USD"
    report.add(
        Check(
            "identity.currencies",
            CheckStatus.OBSERVED if gold else CheckStatus.MISMATCH,
            {
                "base": observed["currency_base"],
                "profit": observed["currency_profit"],
                "margin": observed["currency_margin"],
                "description": observed["description"],
                "path": observed["path"],
            },
            {"base": "XAU (or a gold description under a metals path)", "profit": "USD"},
            (
                "gold quoted in USD"
                if observed["currency_base"] == "XAU"
                else "broker reports base currency "
                f"{observed['currency_base']!r}; gold identity taken from description and path"
            ),
        )
    )
    report.add(
        Check(
            "identity.product_type",
            CheckStatus.OBSERVED,
            observed["trade_calc_mode"],
            "cfd/cfd_leverage/forex",
            "MT5 margin calculation mode; not a COMEX future",
        )
    )

    # Economics vs owner expectations ---------------------------------------
    for name in ECONOMICS_FIELDS:
        value = observed.get(name)
        if value is None:
            report.add(Check(f"economics.{name}", CheckStatus.UNAVAILABLE, None, expected_values.get(name)))
            continue
        if name not in expected_values:
            report.add(
                Check(
                    f"economics.{name}",
                    CheckStatus.OBSERVED,
                    value,
                    None,
                    "live value; no owner-confirmed expectation",
                )
            )
            continue
        expected = expected_values[name]
        if isinstance(value, float) and _num(expected) is not None:
            same = _close(value, float(expected))
        else:
            same = value == expected
        report.add(
            Check(
                f"economics.{name}",
                CheckStatus.VERIFIED if same else CheckStatus.MISMATCH,
                value,
                expected,
                "matches owner-confirmed value" if same else "differs from owner-confirmed value",
            )
        )

    # Internal consistency ----------------------------------------------------
    point = observed["point"]
    digits = observed["digits"]
    if point is not None and isinstance(digits, int):
        ok = _close(point, 10.0 ** (-digits))
        report.add(Check("consistency.point_digits", CheckStatus.OBSERVED if ok else CheckStatus.MISMATCH, {"point": point, "digits": digits}))
    tick_size = observed["trade_tick_size"]
    if tick_size is not None:
        strategy_tick = 0.01
        ratio = strategy_tick / tick_size
        ok = tick_size <= strategy_tick + 1e-12 and abs(ratio - round(ratio)) < 1e-6
        report.add(
            Check(
                "consistency.strategy_tick_grid",
                CheckStatus.OBSERVED if ok else CheckStatus.MISMATCH,
                tick_size,
                "0.01 must be a multiple of the broker tick",
                "strategy rounds stops/targets to 0.01",
            )
        )
    contract = observed["trade_contract_size"]
    tick_value = observed["trade_tick_value"]
    if contract and tick_size and tick_value and observed["account_currency"] == "USD" and observed["currency_profit"] == "USD":
        expected_tv = contract * tick_size
        ok = _close(tick_value, expected_tv, rel=1e-4)
        status = CheckStatus.VERIFIED if ok and report.check("economics.trade_tick_value").status is CheckStatus.VERIFIED else (
            CheckStatus.OBSERVED if ok else CheckStatus.MISMATCH
        )
        report.add(
            Check(
                "consistency.tick_value",
                status,
                tick_value,
                expected_tv,
                "USD account: tick value = contract size x tick size",
            )
        )
    else:
        report.add(
            Check(
                "consistency.tick_value",
                CheckStatus.UNAVAILABLE,
                tick_value,
                None,
                "requires USD account, USD profit currency, contract size and tick size",
            )
        )

    quote = dict(tick or {})
    bid = _num(quote.get("bid"))
    ask = _num(quote.get("ask"))
    vmin = observed["volume_min"]
    if calc_profit is not None and ask and vmin and contract:
        try:
            profit = calc_profit(True, vmin, ask, ask + 1.0)
        except Exception as exc:  # broker call failure
            report.add(Check("consistency.order_calc_profit", CheckStatus.FAILED, None, None, str(exc)))
        else:
            expected_profit = vmin * contract * 1.0 if observed["account_currency"] == "USD" else None
            if profit is None:
                report.add(Check("consistency.order_calc_profit", CheckStatus.UNAVAILABLE, None, expected_profit))
            elif expected_profit is None:
                report.add(Check("consistency.order_calc_profit", CheckStatus.OBSERVED, profit, None, "non-USD account; conversion not checked"))
            else:
                ok = _close(profit, expected_profit, rel=1e-3)
                report.add(
                    Check(
                        "consistency.order_calc_profit",
                        CheckStatus.OBSERVED if ok else CheckStatus.MISMATCH,
                        profit,
                        expected_profit,
                        f"broker profit for {vmin} lot over a 1.00 price move",
                    )
                )
    else:
        report.add(Check("consistency.order_calc_profit", CheckStatus.UNAVAILABLE, None, None, "no live quote or calculator"))

    if calc_margin is not None and ask:
        try:
            margin = calc_margin(True, 1.0, ask)
        except Exception as exc:
            report.add(Check("margin.per_lot", CheckStatus.FAILED, None, None, str(exc)))
        else:
            leverage = _num(observed["account_leverage"])
            implied = None if not (margin and contract) else contract * ask / margin
            report.add(
                Check(
                    "margin.per_lot",
                    CheckStatus.OBSERVED if margin else CheckStatus.UNAVAILABLE,
                    margin,
                    None,
                    (
                        f"broker margin for 1 lot at {ask}; implied leverage "
                        f"{None if implied is None else round(implied, 2)} vs account leverage {leverage}"
                    ),
                )
            )
    else:
        report.add(Check("margin.per_lot", CheckStatus.UNAVAILABLE, None))

    # Volume and partial-exit feasibility -----------------------------------
    step = observed["volume_step"]
    vmax = observed["volume_max"]
    if vmin and step and vmax:
        ratio = vmin / step
        ok = abs(ratio - round(ratio)) < 1e-6 and vmax >= vmin
        report.add(
            Check(
                "volume.rules",
                CheckStatus.OBSERVED if ok else CheckStatus.MISMATCH,
                {"min": vmin, "max": vmax, "step": step},
                None,
                f"50/50 partial exit needs at least {max(vmin * 2, 2 * step)} lots",
            )
        )
    else:
        report.add(Check("volume.rules", CheckStatus.UNAVAILABLE, {"min": vmin, "max": vmax, "step": step}))

    # Permissions -----------------------------------------------------------
    report.add(
        Check(
            "permissions.symbol_trade_mode",
            CheckStatus.OBSERVED if observed["trade_mode"] == "full" else CheckStatus.MISMATCH,
            observed["trade_mode"],
            "full",
        )
    )
    report.add(Check("permissions.account_trade_allowed", CheckStatus.OBSERVED, acct.get("trade_allowed")))
    report.add(Check("account.trade_mode", CheckStatus.OBSERVED if acct else CheckStatus.UNAVAILABLE, acct.get("trade_mode"), "demo for any demo stage"))
    report.add(Check("execution.mode", CheckStatus.OBSERVED, observed["trade_exemode"]))
    report.add(Check("execution.filling_modes", CheckStatus.OBSERVED, observed["filling_modes"]))
    if point is not None:
        stops = observed["trade_stops_level"]
        freeze = observed["trade_freeze_level"]
        report.add(
            Check(
                "execution.stop_and_freeze_levels",
                CheckStatus.OBSERVED,
                {
                    "stops_level_points": stops,
                    "stops_level_price": None if stops is None else stops * point,
                    "freeze_level_points": freeze,
                    "freeze_level_price": None if freeze is None else freeze * point,
                },
            )
        )

    # Costs ------------------------------------------------------------------
    report.add(
        Check(
            "costs.swap",
            CheckStatus.OBSERVED if observed["swap_mode"] is not None else CheckStatus.UNAVAILABLE,
            {"mode": observed["swap_mode"], "long": observed["swap_long"], "short": observed["swap_short"], "triple_day": observed["swap_rollover3days"]},
            None,
            "financing terms as configured on the symbol; not yet reconciled with a statement",
        )
    )
    if commissions:
        per_lot = [
            abs(float(item["commission"])) / float(item["volume"])
            for item in commissions
            if _num(item.get("commission")) is not None and _num(item.get("volume"))
        ]
        report.add(
            Check(
                "costs.commission",
                CheckStatus.OBSERVED,
                {"deals": len(commissions), "per_lot_per_side": sorted(set(round(v, 4) for v in per_lot))[:5]},
                None,
                "derived from historical deals on this account",
            )
        )
    else:
        report.add(
            Check(
                "costs.commission",
                CheckStatus.UNAVAILABLE,
                None,
                None,
                "MT5 symbol data has no commission field and no historical deals were found",
            )
        )

    # Spread convention -------------------------------------------------------
    spread_points = observed["spread_points"]
    if bid and ask and point and spread_points is not None:
        quoted = (ask - bid) / point
        ok = abs(quoted - float(spread_points)) <= 1.0 + 1e-9
        report.add(
            Check(
                "spread.convention",
                CheckStatus.OBSERVED if ok else CheckStatus.MISMATCH,
                {"ask_minus_bid_points": round(quoted, 3), "symbol_spread_points": spread_points},
                None,
                "symbol spread is (ask - bid) / point" if ok else "symbol spread does not equal ask-bid",
            )
        )
    else:
        report.add(Check("spread.convention", CheckStatus.UNAVAILABLE, None))
    if forming_m15_close is not None and bid and ask:
        side = "bid" if abs(forming_m15_close - bid) <= abs(forming_m15_close - ask) else "ask"
        report.add(
            Check(
                "spread.bar_quote_side",
                CheckStatus.OBSERVED,
                {"forming_close": forming_m15_close, "bid": bid, "ask": ask, "closest": side},
                "bid",
                "single-sample comparison; repeat across sessions before relying on it",
            )
        )

    # Sessions and time -------------------------------------------------------
    report.add(
        Check(
            "sessions.broker_calendar",
            CheckStatus.UNAVAILABLE,
            None,
            None,
            "the MetaTrader5 Python API does not expose symbol session times; broker calendar unverified",
        )
    )
    if time_basis is not None:
        report.add(
            Check(
                "time.server_offset",
                CheckStatus.OBSERVED if time_basis.get("verified") else CheckStatus.UNAVAILABLE,
                time_basis.get("offset_hours"),
                None,
                "measured from fresh ticks against the host clock",
            )
        )
    return report


def commission_window(now: datetime) -> tuple[datetime, datetime]:
    return now - timedelta(days=180), now
