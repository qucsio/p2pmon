from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.exchange.models import ExchangeAccount
from apps.orders.services.ignore_rules import upsert_ignored_order_rules


class Command(BaseCommand):
    help = "Import legacy Bybit order IDs into IgnoredOrderRule blacklist"

    def add_arguments(self, parser):
        parser.add_argument("--account-id", type=int, required=True)
        parser.add_argument("--file", type=str, help="Path to file with one order id per line")
        parser.add_argument("--ids", type=str, help="Comma-separated Bybit order ids")
        parser.add_argument(
            "--reason",
            type=str,
            default="Legacy orders from previous system",
        )

    def handle(self, *args, **options):
        if not options.get("file") and not options.get("ids"):
            raise CommandError("Provide --file or --ids")

        account = ExchangeAccount.objects.get(pk=options["account_id"])
        order_ids = self._load_ids(options)
        found, applied, pending = upsert_ignored_order_rules(
            account,
            order_ids,
            reason=options["reason"],
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Legacy blacklist import: found={found} applied={applied} pending={pending}"
            )
        )

    def _load_ids(self, options) -> list[str]:
        ids: list[str] = []
        if options.get("ids"):
            ids.extend(options["ids"].split(","))
        if options.get("file"):
            path = Path(options["file"])
            if not path.exists():
                raise CommandError(f"File not found: {path}")
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    ids.append(line)
        return ids
