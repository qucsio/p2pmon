from datetime import datetime
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand

from apps.exchange.models import ExchangeAccount
from apps.ledger.services.engine import LedgerEngine
from apps.orders.services.ignore_rules import apply_ignore_rules


class Command(BaseCommand):
    help = "Set ledger_start_at from an explicit Moscow datetime"

    def add_arguments(self, parser):
        parser.add_argument("--account-id", type=int, required=True)
        parser.add_argument("--date", type=str, required=True, help='Datetime "YYYY-MM-DD HH:MM"')
        parser.add_argument("--timezone", type=str, default="Europe/Moscow")
        parser.add_argument(
            "--inclusive",
            action="store_true",
            help="Also ignore orders exactly at ledger_start_at (<= boundary).",
        )

    def handle(self, *args, **options):
        account = ExchangeAccount.objects.get(pk=options["account_id"])
        tz = ZoneInfo(options["timezone"])
        ledger_start_at = datetime.strptime(options["date"], "%Y-%m-%d %H:%M").replace(tzinfo=tz)

        account.ledger_start_at = ledger_start_at
        account.ledger_start_inclusive = options["inclusive"]
        account.save(update_fields=["ledger_start_at", "ledger_start_inclusive", "updated_at"])

        result = apply_ignore_rules(account)
        LedgerEngine(account).rebuild()

        self.stdout.write(
            self.style.SUCCESS(
                f"ledger_start_at={ledger_start_at.isoformat()}; "
                f"ledger_start_marked={result.ledger_start_marked}"
            )
        )
