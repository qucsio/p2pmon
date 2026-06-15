from decimal import Decimal

from apps.common.decimal_utils import d, q_usdt
from apps.exchange.models import FeeRule
from apps.orders.models import P2POrder


def resolve_fee(order: P2POrder, list_payload: dict, detail_payload: dict) -> tuple[Decimal, str, str]:
    """
    Priority: manual > API detail > API list > FeeRule > zero
    Returns (fee_amount, fee_currency, fee_source)
    """
    if order.manual_fee_amount is not None:
        return (
            q_usdt(order.manual_fee_amount),
            order.manual_fee_currency or "USDT",
            P2POrder.FEE_SOURCE_MANUAL,
        )

    detail = detail_payload.get("result") or detail_payload
    fee = _parse_fee(detail.get("fee"))
    if fee > 0:
        currency = detail.get("tradingFeeTokenId") or detail.get("gasFeeTokenId") or "USDT"
        return fee, currency, P2POrder.FEE_SOURCE_API

    list_fee = _parse_fee(list_payload.get("fee"))
    if list_fee > 0:
        return list_fee, "USDT", P2POrder.FEE_SOURCE_API

    rule_fee = _fee_from_rule(order)
    if rule_fee > 0:
        return rule_fee, "USDT", P2POrder.FEE_SOURCE_CALCULATED

    return Decimal("0"), "", P2POrder.FEE_SOURCE_NONE


def _parse_fee(value) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return q_usdt(d(value))
    except Exception:
        return Decimal("0")


def _fee_from_rule(order: P2POrder) -> Decimal:
    side = order.side.lower()
    rules = FeeRule.objects.filter(
        exchange_account=order.exchange_account,
        is_active=True,
        effective_from__lte=order.created_at_moscow,
    ).filter(
        models_Q_effective_to(order.created_at_moscow)
    )
    for rule in rules:
        if rule.side not in (FeeRule.SIDE_BOTH, side):
            continue
        base = order.quantity_gross if rule.fee_currency.upper() == "USDT" else order.amount_rub
        return q_usdt(base * rule.fee_rate)
    return Decimal("0")


def models_Q_effective_to(dt):
    from django.db.models import Q
    return Q(effective_to__isnull=True) | Q(effective_to__gte=dt)
