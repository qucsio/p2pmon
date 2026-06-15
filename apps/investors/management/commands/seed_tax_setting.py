from django.core.management.base import BaseCommand

from apps.investors.models import TaxSetting


class Command(BaseCommand):
    help = "Seed default tax setting for a user"

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, required=True)
        parser.add_argument("--rate", type=str, default="0.06")

    def handle(self, *args, **options):
        from django.contrib.auth import get_user_model
        from datetime import date
        from decimal import Decimal

        user = get_user_model().objects.get(pk=options["user_id"])
        TaxSetting.objects.get_or_create(
            user=user,
            name="Default tax",
            defaults={
                "tax_rate": Decimal(options["rate"]),
                "effective_from": date(2020, 1, 1),
                "is_active": True,
            },
        )
        self.stdout.write(self.style.SUCCESS("Tax setting seeded"))
