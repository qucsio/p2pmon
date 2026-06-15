from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create default superuser if none exists"

    def handle(self, *args, **options):
        User = get_user_model()
        if User.objects.filter(is_superuser=True).exists():
            self.stdout.write("Superuser already exists")
            return
        User.objects.create_superuser(
            username="admin",
            email="admin@localhost",
            password="admin",
        )
        self.stdout.write(self.style.SUCCESS("Created superuser admin/admin"))
