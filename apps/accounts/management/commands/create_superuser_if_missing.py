from decouple import config
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create the first superuser from DJANGO_SUPERUSER_* env vars if none exists"

    def handle(self, *args, **options):
        User = get_user_model()
        if User.objects.filter(is_superuser=True).exists():
            self.stdout.write("Superuser already exists")
            return
        # No built-in password: this repository is public, so a default here is a
        # default for everyone who reads it.
        password = config("DJANGO_SUPERUSER_PASSWORD", default="")
        if not password:
            self.stdout.write(
                self.style.WARNING(
                    "No superuser and DJANGO_SUPERUSER_PASSWORD is not set: skipped. "
                    "Run `python manage.py createsuperuser`."
                )
            )
            return
        username = config("DJANGO_SUPERUSER_USERNAME", default="admin")
        User.objects.create_superuser(username=username, email="", password=password)
        self.stdout.write(self.style.SUCCESS(f"Created superuser {username}"))
