from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("exchange", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BalanceSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("snapshot_at", models.DateTimeField()),
                ("bank_balance_fact", models.DecimalField(decimal_places=2, max_digits=20)),
                ("exchange_balance_fact", models.DecimalField(decimal_places=8, max_digits=24)),
                ("bank_balance_calculated", models.DecimalField(decimal_places=2, max_digits=20)),
                ("exchange_balance_calculated", models.DecimalField(decimal_places=8, max_digits=24)),
                ("bank_diff", models.DecimalField(decimal_places=2, max_digits=20)),
                ("exchange_diff", models.DecimalField(decimal_places=8, max_digits=24)),
                ("comment", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("exchange_account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="balance_snapshots", to="exchange.exchangeaccount")),
            ],
            options={"ordering": ["-snapshot_at"]},
        ),
    ]
