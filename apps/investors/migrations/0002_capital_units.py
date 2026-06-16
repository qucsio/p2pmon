from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("investors", "0001_initial"),
        ("ledger", "0002_split_adjustment_amounts"),
    ]

    operations = [
        migrations.AlterField(
            model_name="investor",
            name="share_percent",
            field=models.DecimalField(decimal_places=6, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name="investor",
            name="profit_share_mode",
            field=models.CharField(
                choices=[
                    ("same_as_capital", "Как доля капитала"),
                    ("multiplier", "Множитель от доли капитала"),
                    ("fixed_pct", "Фиксированный процент"),
                    ("none", "Без прибыли"),
                ],
                default="same_as_capital",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="investor",
            name="profit_share_multiplier",
            field=models.DecimalField(decimal_places=6, default=Decimal("1"), max_digits=10),
        ),
        migrations.AddField(
            model_name="investor",
            name="profit_share_fixed_pct",
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=10, null=True),
        ),
        migrations.CreateModel(
            name="InvestorCapitalTransaction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("type", models.CharField(choices=[("deposit", "Депозит"), ("withdrawal", "Вывод"), ("profit_reinvest", "Реинвест прибыли"), ("profit_payout", "Выплата прибыли"), ("correction", "Корректировка")], max_length=20)),
                ("amount_rub", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("units_delta", models.DecimalField(decimal_places=8, default=0, max_digits=30)),
                ("unit_price", models.DecimalField(decimal_places=8, default=0, max_digits=30)),
                ("effective_at", models.DateTimeField()),
                ("comment", models.TextField(blank=True)),
                ("investor", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="capital_transactions", to="investors.investor")),
                ("linked_ledger_adjustment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="investor_transactions", to="ledger.ledgeradjustment")),
            ],
            options={"ordering": ["effective_at", "id"]},
        ),
        migrations.AlterField(
            model_name="profitallocation",
            name="share_percent",
            field=models.DecimalField(decimal_places=6, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name="profitallocation",
            name="capital_share_pct",
            field=models.DecimalField(decimal_places=6, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name="profitallocation",
            name="profit_share_pct",
            field=models.DecimalField(decimal_places=6, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name="profitallocation",
            name="status",
            field=models.CharField(
                choices=[("unpaid", "Не выплачено"), ("paid_out", "Выплачено"), ("reinvested", "Реинвестировано")],
                default="unpaid",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="profitallocation",
            name="settled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="profitallocation",
            name="settlement_txn",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="settled_allocation", to="investors.investorcapitaltransaction"),
        ),
    ]
