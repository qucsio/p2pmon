from django.core.management.base import BaseCommand

from apps.investors.models import Investor


class Command(BaseCommand):
    help = "Seed a single 100% investor for a user"

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, required=True)
        parser.add_argument("--name", type=str, default="Owner")

    def handle(self, *args, **options):
        from django.contrib.auth import get_user_model
        from decimal import Decimal

        user = get_user_model().objects.get(pk=options["user_id"])
        Investor.objects.get_or_create(
            user=user,
            name=options["name"],
            defaults={"share_percent": Decimal("100"), "is_active": True},
        )
        self.stdout.write(self.style.SUCCESS("Investor seeded at 100%"))
