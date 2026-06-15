from apps.common.encryption import generate_encryption_key
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Generate a Fernet encryption key for FIELD_ENCRYPTION_KEY"

    def handle(self, *args, **options):
        key = generate_encryption_key()
        self.stdout.write(self.style.SUCCESS(f"FIELD_ENCRYPTION_KEY={key}"))
