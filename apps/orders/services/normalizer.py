from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from apps.common.decimal_utils import d, q_price, q_rub, q_usdt
from apps.exchange.models import ExchangeAccount
from apps.orders.models import P2POrder, RawP2POrder
from apps.orders.services.fees import resolve_fee

MOSCOW = ZoneInfo("Europe/Moscow")
COMPLETED_STATUS = 50


def _compute_net_fields(
    side: str,
    quantity_gross: Decimal,
    amount_gross: Decimal,
    fee_amount: Decimal,
    fee_currency: str,
) -> tuple[Decimal, Decimal, Decimal]:
    currency = (fee_currency or "").upper()
    if currency == "USDT" and fee_amount > 0:
        if side == P2POrder.SIDE_BUY:
            return q_usdt(quantity_gross - fee_amount), amount_gross, amount_gross
        return quantity_gross, amount_gross, amount_gross
    if currency == "RUB" and fee_amount > 0:
        if side == P2POrder.SIDE_SELL:
            amount_net = q_rub(amount_gross - fee_amount)
            return quantity_gross, amount_net, amount_net
        amount_net = q_rub(amount_gross + fee_amount)
        return quantity_gross, amount_net, amount_net
    return quantity_gross, amount_gross, amount_gross


def normalize_raw_order(raw: RawP2POrder) -> P2POrder:
    list_payload = raw.raw_list_payload
    detail_payload = raw.raw_detail_payload or {}
    detail = detail_payload.get("result") or detail_payload

    bybit_side = int(list_payload.get("side", detail.get("side", 0)))
    side = P2POrder.SIDE_BUY if bybit_side == 0 else P2POrder.SIDE_SELL

    ts_ms = int(list_payload.get("createDate") or detail.get("createDate", 0))
    created_at_utc = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    created_at_moscow = created_at_utc.astimezone(MOSCOW)

    completed_at_utc = None
    completed_at_moscow = None
    transfer_date = detail.get("transferDate") or list_payload.get("transferDate")
    if transfer_date and str(transfer_date) not in ("0", ""):
        try:
            completed_at_utc = datetime.fromtimestamp(int(transfer_date) / 1000, tz=timezone.utc)
            completed_at_moscow = completed_at_utc.astimezone(MOSCOW)
        except (ValueError, TypeError):
            pass

    quantity_gross = q_usdt(d(
        detail.get("quantity")
        or detail.get("notifyTokenQuantity")
        or list_payload.get("notifyTokenQuantity")
        or list_payload.get("quantity")
        or 0
    ))
    amount_gross = q_rub(d(list_payload.get("amount") or detail.get("amount") or 0))
    price = q_price(d(list_payload.get("price") or detail.get("price") or 0))

    if side == P2POrder.SIDE_BUY:
        counterparty_name = list_payload.get("sellerRealName") or detail.get("sellerRealName") or ""
    else:
        counterparty_name = list_payload.get("buyerRealName") or detail.get("buyerRealName") or ""

    existing = P2POrder.objects.filter(
        exchange_account=raw.exchange_account,
        bybit_order_id=raw.bybit_order_id,
    ).first()
    fee_stub = existing or P2POrder(
        exchange_account=raw.exchange_account,
        bybit_order_id=raw.bybit_order_id,
        side=side,
        quantity_gross=quantity_gross,
        amount_rub=amount_gross,
        created_at_moscow=created_at_moscow,
    )
    fee_amount, fee_currency, fee_source = resolve_fee(fee_stub, list_payload, detail_payload)
    quantity_net, amount_net, amount_rub = _compute_net_fields(
        side, quantity_gross, amount_gross, fee_amount, fee_currency
    )

    defaults = {
        "bybit_side": bybit_side,
        "side": side,
        "order_type": detail.get("orderType") or list_payload.get("orderType") or "",
        "status": int(list_payload.get("status") or detail.get("status") or COMPLETED_STATUS),
        "token_id": list_payload.get("tokenId") or detail.get("tokenId") or "USDT",
        "currency_id": list_payload.get("currencyId") or detail.get("currencyId") or "RUB",
        "price": price,
        "quantity_gross": quantity_gross,
        "quantity_net": quantity_net,
        "amount_gross": amount_gross,
        "amount_net": amount_net,
        "amount_rub": amount_rub,
        "fee_amount": fee_amount,
        "fee_currency": fee_currency,
        "fee_source": fee_source,
        "maker_fee": q_usdt(d(detail.get("makerFee") or 0)),
        "taker_fee": q_usdt(d(detail.get("takerFee") or 0)),
        "counterparty_name": counterparty_name or "",
        "counterparty_nickname": list_payload.get("targetNickName") or detail.get("targetNickName") or "",
        "counterparty_user_id": str(list_payload.get("targetUserId") or detail.get("targetUserId") or ""),
        "created_at_bybit_raw_ms": ts_ms,
        "created_at_utc": created_at_utc,
        "created_at_moscow": created_at_moscow,
        "completed_at_bybit_utc": completed_at_utc,
        "completed_at_moscow": completed_at_moscow,
    }

    order = P2POrder.objects.filter(
        exchange_account=raw.exchange_account,
        bybit_order_id=raw.bybit_order_id,
    ).first()
    if order is None:
        order = P2POrder(
            exchange_account=raw.exchange_account,
            bybit_order_id=raw.bybit_order_id,
            raw_order=raw,
        )

    for field, value in defaults.items():
        setattr(order, field, value)
    order.raw_order = raw
    if order.quantity_net is None:
        order.quantity_net = quantity_gross
    if order.amount_net is None:
        order.amount_net = amount_gross
    order.save()
    return order


def normalize_account_orders(exchange_account: ExchangeAccount):
    for raw in RawP2POrder.objects.filter(exchange_account=exchange_account):
        normalize_raw_order(raw)
