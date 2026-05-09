from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import Enum

from tradestation_api_wrapper.models import OrderSnapshot, TradeAction, UnknownOrderFingerprint


class ReconciliationOutcome(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"
    NO_MATCH = "NO_MATCH"
    MULTIPLE_MATCHES = "MULTIPLE_MATCHES"


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    outcome: ReconciliationOutcome
    matches: tuple[OrderSnapshot, ...]

    @property
    def order(self) -> OrderSnapshot | None:
        if self.outcome is ReconciliationOutcome.EXACT_MATCH:
            return self.matches[0]
        return None


def match_unknown_order(
    fingerprint: UnknownOrderFingerprint,
    snapshots: tuple[OrderSnapshot, ...],
    *,
    time_window: timedelta = timedelta(minutes=5),
) -> ReconciliationResult:
    matches = tuple(
        snapshot
        for snapshot in snapshots
        if _matches_fingerprint(fingerprint, snapshot, time_window)
    )
    if len(matches) == 1:
        return ReconciliationResult(ReconciliationOutcome.EXACT_MATCH, matches)
    if len(matches) > 1:
        return ReconciliationResult(ReconciliationOutcome.MULTIPLE_MATCHES, matches)
    return ReconciliationResult(ReconciliationOutcome.NO_MATCH, ())


def _matches_fingerprint(
    fingerprint: UnknownOrderFingerprint,
    snapshot: OrderSnapshot,
    time_window: timedelta,
) -> bool:
    if snapshot.account_id is not None and snapshot.account_id != fingerprint.account_id:
        return False
    if snapshot.primary_symbol() != fingerprint.symbol:
        return False
    if _normalize_broker_action(snapshot.primary_action()) != fingerprint.trade_action:
        return False
    if snapshot.order_type != fingerprint.order_type:
        return False
    snapshot_quantity = snapshot.primary_quantity()
    if snapshot_quantity is None or snapshot_quantity != fingerprint.quantity:
        return False
    if not _decimal_equal(snapshot.limit_price, fingerprint.limit_price):
        return False
    if not _decimal_equal(snapshot.stop_price, fingerprint.stop_price):
        return False
    if snapshot.opened_at is None:
        return True
    return abs(snapshot.opened_at - fingerprint.submitted_at) <= time_window


def _normalize_broker_action(value: str | None) -> TradeAction | None:
    if value is None:
        return None
    normalized = value.replace(" ", "").replace("_", "").upper()
    mapping = {
        "BUY": TradeAction.BUY,
        "SELL": TradeAction.SELL,
        "BUYTOCOVER": TradeAction.BUY_TO_COVER,
        "SELLSHORT": TradeAction.SELL_SHORT,
        "BUYTOOPEN": TradeAction.BUY_TO_OPEN,
        "BUYTOCLOSE": TradeAction.BUY_TO_CLOSE,
        "SELLTOOPEN": TradeAction.SELL_TO_OPEN,
        "SELLTOCLOSE": TradeAction.SELL_TO_CLOSE,
    }
    return mapping.get(normalized)


def _decimal_equal(left: Decimal | None, right: Decimal | None) -> bool:
    if left is None or right is None:
        return left is right
    return left == right

