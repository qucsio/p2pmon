from django.core.management.base import BaseCommand

from apps.exchange.models import ExchangeAccount
from apps.ledger.services.engine import LedgerEngine


class Command(BaseCommand):
    help = "Rebuild ledger for an exchange account"

    def add_arguments(self, parser):
        parser.add_argument("--account-id", type=int, required=True)

    def handle(self, *args, **options):
        account = ExchangeAccount.objects.get(pk=options["account_id"])
        LedgerEngine(account).rebuild()
        self.stdout.write(self.style.SUCCESS("Ledger rebuilt"))
