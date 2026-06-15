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
            name="LedgerAdjustment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("account", models.CharField(choices=[("bank", "Bank"), ("exchange", "Exchange")], max_length=16)),
                ("type", models.CharField(choices=[("deposit", "Deposit"), ("withdrawal", "Withdrawal"), ("correction", "Correction"), ("tax_payment", "Tax Payment"), ("fee_correction", "Fee Correction"), ("investor_deposit", "Investor Deposit"), ("investor_withdrawal", "Investor Withdrawal")], max_length=32)),
                ("currency", models.CharField(max_length=16)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=20)),
                ("effective_at", models.DateTimeField()),
                ("comment", models.TextField(blank=True)),
                ("include_in_ledger", models.BooleanField(default=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="adjustments", to=settings.AUTH_USER_MODEL)),
                ("deleted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="deleted_adjustments", to=settings.AUTH_USER_MODEL)),
                ("exchange_account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="adjustments", to="exchange.exchangeaccount")),
            ],
            options={"ordering": ["effective_at", "id"]},
        ),
        migrations.CreateModel(
            name="LedgerEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_type", models.CharField(choices=[("buy", "Buy"), ("sell", "Sell"), ("bank_deposit", "Bank Deposit"), ("bank_withdrawal", "Bank Withdrawal"), ("bank_correction", "Bank Correction"), ("exchange_deposit", "Exchange Deposit"), ("exchange_withdrawal", "Exchange Withdrawal"), ("exchange_correction", "Exchange Correction"), ("fee", "Fee"), ("tax_payment", "Tax Payment"), ("investor_deposit", "Investor Deposit"), ("investor_withdrawal", "Investor Withdrawal")], max_length=32)),
                ("source_type", models.CharField(choices=[("order", "Order"), ("adjustment", "Adjustment")], max_length=16)),
                ("source_id", models.BigIntegerField()),
                ("occurred_at_utc", models.DateTimeField()),
                ("occurred_at_moscow", models.DateTimeField()),
                ("currency", models.CharField(blank=True, max_length=16)),
                ("amount_rub", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("amount_usdt", models.DecimalField(decimal_places=8, default=0, max_digits=24)),
                ("price", models.DecimalField(decimal_places=6, default=0, max_digits=20)),
                ("fee_amount", models.DecimalField(decimal_places=8, default=0, max_digits=24)),
                ("fee_currency", models.CharField(blank=True, max_length=16)),
                ("include_in_ledger", models.BooleanField(default=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("exchange_account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ledger_events", to="exchange.exchangeaccount")),
            ],
            options={"ordering": ["occurred_at_moscow", "event_type", "source_id"]},
        ),
        migrations.CreateModel(
            name="DailySnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("day", models.DateField()),
                ("bank_balance", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("exchange_balance", models.DecimalField(decimal_places=8, default=0, max_digits=24)),
                ("total_equity", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("daily_total_equity_pnl", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("daily_wac_realized_pnl", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("daily_wac_unrealized_pnl", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("running_wac_pnl", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("gross_realized_pnl", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("fees", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("net_profit_before_tax", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("tax_accrual", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("net_profit_after_tax", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("volume_usdt", models.DecimalField(decimal_places=8, default=0, max_digits=24)),
                ("volume_rub", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("last_price", models.DecimalField(decimal_places=6, default=0, max_digits=20)),
                ("wac_price", models.DecimalField(decimal_places=6, default=0, max_digits=20)),
                ("wac_qty", models.DecimalField(decimal_places=8, default=0, max_digits=24)),
                ("wac_cost", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("exchange_account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="daily_snapshots", to="exchange.exchangeaccount")),
            ],
            options={"ordering": ["day"]},
        ),
        migrations.CreateModel(
            name="WeeklySnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("week", models.CharField(max_length=16)),
                ("bank_balance", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("exchange_balance", models.DecimalField(decimal_places=8, default=0, max_digits=24)),
                ("total_equity", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("daily_total_equity_pnl", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("daily_wac_realized_pnl", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("daily_wac_unrealized_pnl", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("running_wac_pnl", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("gross_realized_pnl", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("fees", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("net_profit_before_tax", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("tax_accrual", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("net_profit_after_tax", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("volume_usdt", models.DecimalField(decimal_places=8, default=0, max_digits=24)),
                ("volume_rub", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("last_price", models.DecimalField(decimal_places=6, default=0, max_digits=20)),
                ("wac_price", models.DecimalField(decimal_places=6, default=0, max_digits=20)),
                ("wac_qty", models.DecimalField(decimal_places=8, default=0, max_digits=24)),
                ("wac_cost", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("exchange_account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="weekly_snapshots", to="exchange.exchangeaccount")),
            ],
            options={"ordering": ["week"]},
        ),
        migrations.AddConstraint(
            model_name="dailysnapshot",
            constraint=models.UniqueConstraint(fields=("exchange_account", "day"), name="unique_daily_snapshot"),
        ),
        migrations.AddConstraint(
            model_name="weeklysnapshot",
            constraint=models.UniqueConstraint(fields=("exchange_account", "week"), name="unique_weekly_snapshot"),
        ),
        migrations.AddIndex(
            model_name="ledgerevent",
            index=models.Index(fields=["exchange_account", "occurred_at_moscow"], name="ledger_ledg_exchang_7g8h9i_idx"),
        ),
    ]
