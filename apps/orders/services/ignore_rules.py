from dataclasses import dataclass

from django.utils import timezone

from apps.exchange.models import ExchangeAccount
from apps.orders.models import IgnoredOrderRule, P2POrder

LEDGER_START_REASON = "Before ledger start date"


@dataclass
class ApplyIgnoreRulesResult:
    ledger_start_marked: int = 0
    id_rules_applied: int = 0
    id_rules_pending: int = 0


def apply_ignore_rules(exchange_account: ExchangeAccount) -> ApplyIgnoreRulesResult:
    account = ExchangeAccount.objects.get(pk=exchange_account.pk)
    result = ApplyIgnoreRulesResult()
    now = timezone.now()

    if account.ledger_start_at:
        qs = P2POrder.objects.filter(exchange_account=account)
        if account.ledger_start_inclusive:
            qs = qs.filter(created_at_moscow__lte=account.ledger_start_at)
        else:
            qs = qs.filter(created_at_moscow__lt=account.ledger_start_at)
        result.ledger_start_marked = qs.update(
            include_in_ledger=False,
            show_in_orders=False,
            show_in_export=False,
            ignore_reason=LEDGER_START_REASON,
            ignored_at=now,
        )

    for rule in IgnoredOrderRule.objects.filter(exchange_account=account):
        order = P2POrder.objects.filter(
            exchange_account=account,
            bybit_order_id=rule.bybit_order_id,
        ).first()
        if order is None:
            result.id_rules_pending += 1
            continue

        order.include_in_ledger = rule.include_in_ledger
        order.show_in_orders = rule.show_in_orders
        order.show_in_export = rule.show_in_export
        order.ignore_reason = rule.reason
        order.ignored_at = now
        order.save(
            update_fields=[
                "include_in_ledger",
                "show_in_orders",
                "show_in_export",
                "ignore_reason",
                "ignored_at",
                "updated_at",
            ]
        )
        if rule.applied_at is None:
            rule.applied_at = now
            rule.save(update_fields=["applied_at", "updated_at"])
        result.id_rules_applied += 1

    return result


def upsert_ignored_order_rules(
    exchange_account: ExchangeAccount,
    order_ids: list[str],
    reason: str,
) -> tuple[int, int, int]:
    """
    Create/update IgnoredOrderRule rows and apply to existing P2POrder rows.
    Returns (found, applied, pending).
    """
    found = 0
    applied = 0
    pending = 0
    now = timezone.now()

    for order_id in order_ids:
        order_id = order_id.strip()
        if not order_id:
            continue
        found += 1
        rule, _ = IgnoredOrderRule.objects.update_or_create(
            exchange_account=exchange_account,
            bybit_order_id=order_id,
            defaults={
                "reason": reason,
                "include_in_ledger": False,
                "show_in_orders": False,
                "show_in_export": False,
            },
        )
        order = P2POrder.objects.filter(
            exchange_account=exchange_account,
            bybit_order_id=order_id,
        ).first()
        if order is None:
            pending += 1
            continue

        order.include_in_ledger = rule.include_in_ledger
        order.show_in_orders = rule.show_in_orders
        order.show_in_export = rule.show_in_export
        order.ignore_reason = rule.reason
        order.ignored_at = now
        order.save(
            update_fields=[
                "include_in_ledger",
                "show_in_orders",
                "show_in_export",
                "ignore_reason",
                "ignored_at",
                "updated_at",
            ]
        )
        rule.applied_at = now
        rule.save(update_fields=["applied_at", "updated_at"])
        applied += 1

    return found, applied, pending
