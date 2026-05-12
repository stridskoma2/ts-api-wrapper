from __future__ import annotations

from enum import Enum


class TradeStationOrderStatus(str, Enum):
    RECEIVED = "ACK"
    OPTION_ASSIGNMENT = "ASS"
    BRACKET_CANCELED = "BRC"
    BRACKET_FILLED = "BRF"
    BROKEN = "BRO"
    CANCELED = "CAN"
    CHANGE = "CHG"
    CONDITION_MET = "CND"
    FILL_CORRECTED = "COR"
    CANCEL_SENT_DEPRECATED = "CSN"
    DISPATCHED = "DIS"
    DEAD = "DOA"
    QUEUED = "DON"
    EXPIRATION_CANCEL_REQUEST = "ECN"
    EXPIRED = "EXP"
    OPTION_EXERCISE = "EXE"
    FILLED = "FLL"
    PARTIAL_FILL_DONE = "FLP"
    PARTIAL_FILL_ALIVE = "FPR"
    TOO_LATE_TO_CANCEL = "LAT"
    SENT = "OPN"
    OSO_ORDER = "OSO"
    OTHER = "OTHER"
    UR_OUT = "OUT"
    SENDING = "PLA"
    BIG_BROTHER_RECALL_REQUEST = "REC"
    REJECTED = "REJ"
    CANCEL_REJECTED = "RJC"
    CHANGE_REQUEST_REJECTED = "RJR"
    REPLACE_PENDING = "RPD"
    REPLACE_SENT = "RSN"
    BIG_BROTHER_RECALL = "SCN"
    STOP_HIT = "STP"
    STATUS_MESSAGE = "STT"
    SUSPENDED = "SUS"
    TRADE_SERVER_CANCELED = "TSC"
    REPLACED = "UCH"
    CANCEL_SENT = "UCN"
    UNKNOWN = "UNKNOWN"


DONE_STATUSES = frozenset(
    {
        TradeStationOrderStatus.BROKEN,
        TradeStationOrderStatus.BRACKET_CANCELED,
        TradeStationOrderStatus.BRACKET_FILLED,
        TradeStationOrderStatus.CANCELED,
        TradeStationOrderStatus.CHANGE_REQUEST_REJECTED,
        TradeStationOrderStatus.DEAD,
        TradeStationOrderStatus.EXPIRED,
        TradeStationOrderStatus.FILLED,
        TradeStationOrderStatus.FILL_CORRECTED,
        TradeStationOrderStatus.OPTION_ASSIGNMENT,
        TradeStationOrderStatus.OPTION_EXERCISE,
        TradeStationOrderStatus.PARTIAL_FILL_DONE,
        TradeStationOrderStatus.REJECTED,
        TradeStationOrderStatus.REPLACED,
        TradeStationOrderStatus.BIG_BROTHER_RECALL,
        TradeStationOrderStatus.TRADE_SERVER_CANCELED,
        TradeStationOrderStatus.UR_OUT,
    }
)
CANCEL_PENDING_STATUSES = frozenset(
    {
        TradeStationOrderStatus.CANCEL_SENT,
        TradeStationOrderStatus.CANCEL_SENT_DEPRECATED,
        TradeStationOrderStatus.EXPIRATION_CANCEL_REQUEST,
        TradeStationOrderStatus.BIG_BROTHER_RECALL_REQUEST,
    }
)
REPLACE_PENDING_STATUSES = frozenset(
    {
        TradeStationOrderStatus.REPLACE_PENDING,
        TradeStationOrderStatus.REPLACE_SENT,
    }
)
WAITING_STATUSES = frozenset(
    {
        TradeStationOrderStatus.RECEIVED,
        TradeStationOrderStatus.CONDITION_MET,
        TradeStationOrderStatus.QUEUED,
        TradeStationOrderStatus.OSO_ORDER,
    }
)
WORKING_STATUSES = frozenset(
    {
        TradeStationOrderStatus.CHANGE,
        TradeStationOrderStatus.DISPATCHED,
        TradeStationOrderStatus.SENT,
        TradeStationOrderStatus.PARTIAL_FILL_ALIVE,
        TradeStationOrderStatus.TOO_LATE_TO_CANCEL,
        TradeStationOrderStatus.CANCEL_REJECTED,
        TradeStationOrderStatus.SENDING,
        TradeStationOrderStatus.STOP_HIT,
        TradeStationOrderStatus.SUSPENDED,
    }
)
ACTIVE_STATUSES = WAITING_STATUSES | WORKING_STATUSES | CANCEL_PENDING_STATUSES | REPLACE_PENDING_STATUSES
NON_CANCELABLE_WORKING_STATUSES = frozenset(
    {
        TradeStationOrderStatus.CANCEL_REJECTED,
        TradeStationOrderStatus.TOO_LATE_TO_CANCEL,
    }
)
CANCELABLE_STATUSES = (
    WAITING_STATUSES | (WORKING_STATUSES - NON_CANCELABLE_WORKING_STATUSES) | REPLACE_PENDING_STATUSES
)
REPLACEABLE_STATUSES = WAITING_STATUSES | (WORKING_STATUSES - NON_CANCELABLE_WORKING_STATUSES)


def normalize_order_status(status: str | None) -> TradeStationOrderStatus | None:
    if status is None:
        return None
    normalized = status.strip().upper()
    if not normalized:
        return None
    try:
        return TradeStationOrderStatus(normalized)
    except ValueError:
        return TradeStationOrderStatus.UNKNOWN


def order_status_is_done(status: TradeStationOrderStatus | None) -> bool:
    return status in DONE_STATUSES


def order_status_is_active(status: TradeStationOrderStatus | None) -> bool:
    return status in ACTIVE_STATUSES


def order_status_is_working(status: TradeStationOrderStatus | None) -> bool:
    return status in WORKING_STATUSES


def order_status_can_cancel(status: TradeStationOrderStatus | None) -> bool:
    return status in CANCELABLE_STATUSES


def order_status_can_replace(status: TradeStationOrderStatus | None) -> bool:
    return status in REPLACEABLE_STATUSES
