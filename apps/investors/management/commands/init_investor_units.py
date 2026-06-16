from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.common.helpers import get_active_account
from apps.investors.services import emergency_seed_units_from_shares


class Command(BaseCommand):
    help = (
        "EMERGENCY ONLY: seed investor units from legacy share_percent at CURRENT "
        "equity. This ignores real entry dates — prefer historical contribution "
        "linking in the Investors → История взносов UI. Requires --i-understand."
    )

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, required=True)
        parser.add_argument("--force", action="store_true")
        parser.add_argument(
            "--i-understand",
            action="store_true",
            help="Confirm you understand this produces historically incorrect shares.",
        )

    def handle(self, *args, **options):
        if not options["i_understand"]:
            self.stderr.write(self.style.ERROR(
                "Refusing: this is emergency-only and produces historically incorrect "
                "shares. Use the contribution-linking flow instead, or pass --i-understand."
            ))
            return
        user = get_user_model().objects.get(pk=options["user_id"])
        account = get_active_account(user)
        created = emergency_seed_units_from_shares(user, account, force=options["force"])
        self.stdout.write(self.style.WARNING(f"[emergency] Seeded units for {created} investor(s)."))
