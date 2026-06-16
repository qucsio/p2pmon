from django.core.management.base import BaseCommand, CommandError

from apps.exchange.models import ExchangeAccount
from apps.ledger.services.engine import LedgerEngine
from apps.orders.models import P2POrder
from apps.orders.services.ignore_rules import apply_ignore_rules


class Command(BaseCommand):
    help = "Set ledger_start_at from an existing normalized P2P order boundary"

    def add_arguments(self, parser):
        parser.add_argument("--account-id", type=int, required=True)
        parser.add_argument("--order-id", type=str, required=True, help="Bybit order id or P2POrder pk")
        parser.add_argument(
            "--include-boundary",
            action="store_true",
            default=True,
            help="If set, boundary order stays in ledger (ignore only older). Default: True.",
        )
        parser.add_argument(
            "--exclude-boundary",
            action="store_true",
            help="Ignore boundary order and older (<= boundary).",
        )

    def handle(self, *args, **options):
        account = ExchangeAccount.objects.get(pk=options["account_id"])
        order = self._find_order(account, options["order_id"])
        include_boundary = options["include_boundary"] and not options["exclude_boundary"]

        account.ledger_start_at = order.created_at_moscow
        account.ledger_start_inclusive = not include_boundary
        account.save(update_fields=["ledger_start_at", "ledger_start_inclusive", "updated_at"])

        result = apply_ignore_rules(account)
        LedgerEngine(account).rebuild()

        mode = "older only" if include_boundary else "boundary and older"
        self.stdout.write(
            self.style.SUCCESS(
                f"ledger_start_at={account.ledger_start_at.isoformat()} ({mode}); "
                f"ledger_start_marked={result.ledger_start_marked}"
            )
        )

    def _find_order(self, account: ExchangeAccount, order_id: str) -> P2POrder:
        order = P2POrder.objects.filter(
            exchange_account=account,
            bybit_order_id=order_id,
        ).first()
        if order:
            return order
        if order_id.isdigit():
            order = P2POrder.objects.filter(exchange_account=account, pk=int(order_id)).first()
            if order:
                return order
        raise CommandError(f"P2POrder not found for id={order_id!r}")
