from django.core.management.base import BaseCommand

from apps.exchange.models import ExchangeAccount, SyncLog
from apps.exchange.services.sync import SyncService


class Command(BaseCommand):
    help = "Run full backfill sync for an exchange account"

    def add_arguments(self, parser):
        parser.add_argument("--account-id", type=int, required=True)

    def handle(self, *args, **options):
        account = ExchangeAccount.objects.get(pk=options["account_id"])
        log = SyncService(account, SyncLog.MODE_BACKFILL).run()
        self.stdout.write(self.style.SUCCESS(f"Backfill finished: {log.status} - {log.message}"))
