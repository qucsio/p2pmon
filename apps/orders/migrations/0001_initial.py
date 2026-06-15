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
            name="RawP2POrder",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("bybit_order_id", models.CharField(max_length=64)),
                ("raw_list_payload", models.JSONField(default=dict)),
                ("raw_detail_payload", models.JSONField(blank=True, default=dict)),
                ("fetched_at", models.DateTimeField(auto_now_add=True)),
                ("detail_fetched_at", models.DateTimeField(blank=True, null=True)),
                ("exchange_account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="raw_orders", to="exchange.exchangeaccount")),
            ],
            options={"ordering": ["-fetched_at"]},
        ),
        migrations.CreateModel(
            name="P2POrder",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("bybit_order_id", models.CharField(max_length=64)),
                ("side", models.CharField(choices=[("BUY", "Buy"), ("SELL", "Sell")], max_length=4)),
                ("bybit_side", models.IntegerField()),
                ("order_type", models.CharField(blank=True, max_length=32)),
                ("status", models.IntegerField()),
                ("token_id", models.CharField(default="USDT", max_length=16)),
                ("currency_id", models.CharField(default="RUB", max_length=16)),
                ("price", models.DecimalField(decimal_places=6, max_digits=20)),
                ("quantity_gross", models.DecimalField(decimal_places=8, max_digits=24)),
                ("quantity_net", models.DecimalField(decimal_places=8, max_digits=24)),
                ("amount_gross", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("amount_net", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("amount_rub", models.DecimalField(decimal_places=2, max_digits=20)),
                ("fee_amount", models.DecimalField(decimal_places=8, default=0, max_digits=24)),
                ("fee_currency", models.CharField(blank=True, max_length=16)),
                ("maker_fee", models.DecimalField(decimal_places=8, default=0, max_digits=24)),
                ("taker_fee", models.DecimalField(decimal_places=8, default=0, max_digits=24)),
                ("fee_source", models.CharField(choices=[("api", "API"), ("calculated", "Calculated"), ("manual", "Manual"), ("none", "None")], default="none", max_length=16)),
                ("manual_fee_amount", models.DecimalField(blank=True, decimal_places=8, max_digits=24, null=True)),
                ("manual_fee_currency", models.CharField(blank=True, max_length=16)),
                ("manual_fee_comment", models.TextField(blank=True)),
                ("manual_fee_updated_at", models.DateTimeField(blank=True, null=True)),
                ("counterparty_name", models.CharField(blank=True, max_length=256)),
                ("counterparty_nickname", models.CharField(blank=True, max_length=128)),
                ("counterparty_user_id", models.CharField(blank=True, max_length=64)),
                ("created_at_bybit_raw_ms", models.BigIntegerField()),
                ("created_at_utc", models.DateTimeField()),
                ("created_at_moscow", models.DateTimeField()),
                ("completed_at_bybit_utc", models.DateTimeField(blank=True, null=True)),
                ("completed_at_moscow", models.DateTimeField(blank=True, null=True)),
                ("include_in_ledger", models.BooleanField(default=True)),
                ("show_in_orders", models.BooleanField(default=True)),
                ("show_in_export", models.BooleanField(default=True)),
                ("ignore_reason", models.TextField(blank=True)),
                ("ignored_at", models.DateTimeField(blank=True, null=True)),
                ("exchange_account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="p2p_orders", to="exchange.exchangeaccount")),
                ("ignored_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="ignored_orders", to=settings.AUTH_USER_MODEL)),
                ("manual_fee_updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="fee_overrides", to=settings.AUTH_USER_MODEL)),
                ("raw_order", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="normalized_order", to="orders.rawp2porder")),
            ],
            options={"ordering": ["-created_at_moscow"]},
        ),
        migrations.AddConstraint(
            model_name="rawp2porder",
            constraint=models.UniqueConstraint(fields=("exchange_account", "bybit_order_id"), name="unique_raw_order_per_account"),
        ),
        migrations.AddConstraint(
            model_name="p2porder",
            constraint=models.UniqueConstraint(fields=("exchange_account", "bybit_order_id"), name="unique_p2p_order_per_account"),
        ),
        migrations.AddIndex(
            model_name="p2porder",
            index=models.Index(fields=["created_at_moscow"], name="orders_p2po_created_6a8f0d_idx"),
        ),
        migrations.AddIndex(
            model_name="p2porder",
            index=models.Index(fields=["side"], name="orders_p2po_side_0a1b2c_idx"),
        ),
        migrations.AddIndex(
            model_name="p2porder",
            index=models.Index(fields=["include_in_ledger"], name="orders_p2po_includ_3d4e5f_idx"),
        ),
    ]
