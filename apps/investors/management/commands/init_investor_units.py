from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.common.helpers import get_active_account
from apps.investors.services import initialize_units


class Command(BaseCommand):
    help = "Initialize investor capital units from legacy share_percent using current equity."

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, required=True)
        parser.add_argument(
            "--force",
            action="store_true",
            help="Delete existing capital transactions and re-initialize.",
        )

    def handle(self, *args, **options):
        user = get_user_model().objects.get(pk=options["user_id"])
        account = get_active_account(user)
        created = initialize_units(user, account, force=options["force"])
        self.stdout.write(self.style.SUCCESS(f"Initialized units for {created} investor(s)."))
