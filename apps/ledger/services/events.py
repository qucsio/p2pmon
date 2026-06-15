from decimal import Decimal

from apps.common.decimal_utils import d, q_price, q_rub, q_usdt
from apps.ledger.models import LedgerAdjustment, LedgerEvent
from apps.orders.models import P2POrder

EVENT_PRIORITY = {
    LedgerEvent.EVENT_BANK_DEPOSIT: 10,
    LedgerEvent.EVENT_BANK_WITHDRAWAL: 10,
    LedgerEvent.EVENT_BANK_CORRECTION: 10,
    LedgerEvent.EVENT_EXCHANGE_DEPOSIT: 10,
    LedgerEvent.EVENT_EXCHANGE_WITHDRAWAL: 10,
    LedgerEvent.EVENT_EXCHANGE_CORRECTION: 10,
    LedgerEvent.EVENT_TAX_PAYMENT: 10,
    LedgerEvent.EVENT_INVESTOR_DEPOSIT: 10,
    LedgerEvent.EVENT_INVESTOR_WITHDRAWAL: 10,
    LedgerEvent.EVENT_BUY: 20,
    LedgerEvent.EVENT_SELL: 20,
    LedgerEvent.EVENT_FEE: 25,
}

ADJUSTMENT_TYPE_MAP = {
    (LedgerAdjustment.ACCOUNT_BANK, LedgerAdjustment.TYPE_DEPOSIT): (
        LedgerEvent.EVENT_BANK_DEPOSIT, 1
    ),
    (LedgerAdjustment.ACCOUNT_BANK, LedgerAdjustment.TYPE_WITHDRAWAL): (
        LedgerEvent.EVENT_BANK_WITHDRAWAL, -1
    ),
    (LedgerAdjustment.ACCOUNT_BANK, LedgerAdjustment.TYPE_CORRECTION): (
        LedgerEvent.EVENT_BANK_CORRECTION, None
    ),
    (LedgerAdjustment.ACCOUNT_BANK, LedgerAdjustment.TYPE_TAX_PAYMENT): (
        LedgerEvent.EVENT_TAX_PAYMENT, -1
    ),
    (LedgerAdjustment.ACCOUNT_BANK, LedgerAdjustment.TYPE_INVESTOR_DEPOSIT): (
        LedgerEvent.EVENT_INVESTOR_DEPOSIT, 1
    ),
    (LedgerAdjustment.ACCOUNT_BANK, LedgerAdjustment.TYPE_INVESTOR_WITHDRAWAL): (
        LedgerEvent.EVENT_INVESTOR_WITHDRAWAL, -1
    ),
    (LedgerAdjustment.ACCOUNT_EXCHANGE, LedgerAdjustment.TYPE_DEPOSIT): (
        LedgerEvent.EVENT_EXCHANGE_DEPOSIT, 1
    ),
    (LedgerAdjustment.ACCOUNT_EXCHANGE, LedgerAdjustment.TYPE_WITHDRAWAL): (
        LedgerEvent.EVENT_EXCHANGE_WITHDRAWAL, -1
    ),
    (LedgerAdjustment.ACCOUNT_EXCHANGE, LedgerAdjustment.TYPE_CORRECTION): (
        LedgerEvent.EVENT_EXCHANGE_CORRECTION, None
    ),
    (LedgerAdjustment.ACCOUNT_EXCHANGE, LedgerAdjustment.TYPE_FEE_CORRECTION): (
        LedgerEvent.EVENT_EXCHANGE_CORRECTION, None
    ),
}


def build_events(exchange_account) -> list[dict]:
    events = []

    for order in P2POrder.objects.filter(
        exchange_account=exchange_account,
        include_in_ledger=True,
        status=50,
    ):
        event_type = LedgerEvent.EVENT_BUY if order.side == P2POrder.SIDE_BUY else LedgerEvent.EVENT_SELL
        events.append({
            "event_type": event_type,
            "source_type": LedgerEvent.SOURCE_ORDER,
            "source_id": order.id,
            "occurred_at_utc": order.created_at_utc,
            "occurred_at_moscow": order.created_at_moscow,
            "currency": order.token_id,
            "amount_rub": order.amount_net or order.amount_rub,
            "amount_usdt": order.quantity_net,
            "price": order.price,
            "fee_amount": order.fee_amount,
            "fee_currency": order.fee_currency,
            "include_in_ledger": True,
            "metadata": {"bybit_order_id": order.bybit_order_id, "side": order.side},
            "priority": EVENT_PRIORITY[event_type],
        })

    for adj in LedgerAdjustment.objects.filter(
        exchange_account=exchange_account,
        include_in_ledger=True,
        is_deleted=False,
    ):
        key = (adj.account, adj.type)
        if key not in ADJUSTMENT_TYPE_MAP:
            continue
        event_type, sign = ADJUSTMENT_TYPE_MAP[key]
        amount = adj.amount
        if sign == -1:
            amount = -abs(amount)
        elif sign == 1:
            amount = abs(amount)

        is_bank = adj.account == LedgerAdjustment.ACCOUNT_BANK
        events.append({
            "event_type": event_type,
            "source_type": LedgerEvent.SOURCE_ADJUSTMENT,
            "source_id": adj.id,
            "occurred_at_utc": adj.effective_at,
            "occurred_at_moscow": adj.effective_at,
            "currency": adj.currency,
            "amount_rub": amount if is_bank else Decimal("0"),
            "amount_usdt": amount if not is_bank else Decimal("0"),
            "price": Decimal("0"),
            "fee_amount": Decimal("0"),
            "fee_currency": "",
            "include_in_ledger": True,
            "metadata": {"comment": adj.comment, "type": adj.type},
            "priority": EVENT_PRIORITY[event_type],
        })

    events.sort(key=lambda e: (e["occurred_at_moscow"], e["priority"], e["source_id"]))
    return events
