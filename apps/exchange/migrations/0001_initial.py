# Generated manually for MVP

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ExchangeAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=128)),
                ("exchange", models.CharField(choices=[("bybit", "Bybit")], default="bybit", max_length=32)),
                ("api_key_encrypted", models.BinaryField(blank=True, default=b"")),
                ("api_secret_encrypted", models.BinaryField(blank=True, default=b"")),
                ("is_active", models.BooleanField(default=True)),
                ("last_successful_sync_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="exchange_accounts", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="SyncLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("status", models.CharField(choices=[("running", "Running"), ("success", "Success"), ("failed", "Failed"), ("partial", "Partial"), ("skipped", "Skipped")], default="running", max_length=16)),
                ("mode", models.CharField(choices=[("hourly", "Hourly"), ("manual", "Manual"), ("backfill", "Backfill"), ("period_resync", "Period Resync")], max_length=16)),
                ("period_from", models.DateTimeField(blank=True, null=True)),
                ("period_to", models.DateTimeField(blank=True, null=True)),
                ("orders_fetched", models.PositiveIntegerField(default=0)),
                ("orders_created", models.PositiveIntegerField(default=0)),
                ("orders_updated", models.PositiveIntegerField(default=0)),
                ("orders_ignored", models.PositiveIntegerField(default=0)),
                ("details_fetched", models.PositiveIntegerField(default=0)),
                ("errors_count", models.PositiveIntegerField(default=0)),
                ("warnings_count", models.PositiveIntegerField(default=0)),
                ("message", models.TextField(blank=True)),
                ("raw_error", models.TextField(blank=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sync_logs", to=settings.AUTH_USER_MODEL)),
                ("exchange_account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sync_logs", to="exchange.exchangeaccount")),
            ],
            options={"ordering": ["-started_at"]},
        ),
        migrations.CreateModel(
            name="FeeRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=128)),
                ("side", models.CharField(choices=[("buy", "Buy"), ("sell", "Sell"), ("both", "Both")], default="both", max_length=8)),
                ("ad_type", models.CharField(blank=True, max_length=32)),
                ("role", models.CharField(choices=[("maker", "Maker"), ("taker", "Taker"), ("both", "Both")], default="maker", max_length=8)),
                ("fee_rate", models.DecimalField(decimal_places=6, max_digits=10)),
                ("fee_currency", models.CharField(default="USDT", max_length=16)),
                ("effective_from", models.DateTimeField()),
                ("effective_to", models.DateTimeField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("comment", models.TextField(blank=True)),
                ("exchange_account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="fee_rules", to="exchange.exchangeaccount")),
            ],
            options={"ordering": ["-effective_from"]},
        ),
    ]
