from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.common.helpers import get_active_account
from apps.investors.services import recompute_units


class Command(BaseCommand):
    help = (
        "Recompute investor unit prices/units chronologically (same-day consistent). "
        "Run after migrations or bulk data changes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, required=True)

    def handle(self, *args, **options):
        user = get_user_model().objects.get(pk=options["user_id"])
        account = get_active_account(user)
        recompute_units(user, account)
        self.stdout.write(self.style.SUCCESS("Units recomputed."))
