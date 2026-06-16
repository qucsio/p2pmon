import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("exchange", "0002_exchangeaccount_ledger_start"),
        ("orders", "0003_p2porder_quantity_net_default"),
    ]

    operations = [
        migrations.CreateModel(
            name="IgnoredOrderRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("bybit_order_id", models.CharField(max_length=64)),
                ("reason", models.TextField()),
                ("include_in_ledger", models.BooleanField(default=False)),
                ("show_in_orders", models.BooleanField(default=False)),
                ("show_in_export", models.BooleanField(default=False)),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                (
                    "exchange_account",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ignored_order_rules",
                        to="exchange.exchangeaccount",
                    ),
                ),
            ],
            options={
                "ordering": ["bybit_order_id"],
            },
        ),
        migrations.AddConstraint(
            model_name="ignoredorderrule",
            constraint=models.UniqueConstraint(
                fields=("exchange_account", "bybit_order_id"),
                name="unique_ignored_order_rule_per_account",
            ),
        ),
    ]
