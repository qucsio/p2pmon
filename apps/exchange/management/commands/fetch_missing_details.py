from django.core.management.base import BaseCommand

from apps.exchange.models import ExchangeAccount
from apps.exchange.services.sync import fetch_all_missing_details


class Command(BaseCommand):
    help = "Fetch Bybit order details for all raw orders missing detail payload"

    def add_arguments(self, parser):
        parser.add_argument("--account-id", type=int, required=True)

    def handle(self, *args, **options):
        account = ExchangeAccount.objects.get(pk=options["account_id"])
        count = fetch_all_missing_details(account)
        self.stdout.write(self.style.SUCCESS(f"Fetched {count} order details"))
