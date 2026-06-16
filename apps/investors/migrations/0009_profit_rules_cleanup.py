from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("investors", "0008_residual_investor"),
    ]

    operations = [
        migrations.RemoveField(model_name="investor", name="share_percent"),
        migrations.AlterField(
            model_name="investorcapitaltransaction",
            name="type",
            field=models.CharField(
                choices=[
                    ("deposit", "Депозит"),
                    ("withdrawal", "Вывод"),
                    ("correction", "Корректировка"),
                ],
                max_length=20,
            ),
        ),
        migrations.DeleteModel(name="ProfitAllocation"),
        migrations.DeleteModel(name="InvestorPositionSnapshot"),
        migrations.CreateModel(
            name="InvestorProfitRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("mode", models.CharField(choices=[
                    ("same_as_capital", "Как доля капитала"),
                    ("multiplier", "Множитель от доли капитала"),
                    ("fixed_pct", "Фиксированный процент"),
                    ("none", "Без прибыли"),
                    ("split_from_investor", "Доля от прибыли другого инвестора"),
                ], default="same_as_capital", max_length=20)),
                ("profit_share_multiplier", models.DecimalField(decimal_places=6, default=Decimal("1"), max_digits=10)),
                ("profit_share_fixed_pct", models.DecimalField(blank=True, decimal_places=6, max_digits=10, null=True)),
                ("split_percent", models.DecimalField(blank=True, decimal_places=6, max_digits=10, null=True)),
                ("effective_from", models.DateField()),
                ("effective_to", models.DateField(blank=True, null=True)),
                ("comment", models.TextField(blank=True)),
                ("investor", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="profit_rules", to="investors.investor")),
                ("source_investor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="rule_profit_sources", to="investors.investor")),
                ("residual_investor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="rule_residual_targets", to="investors.investor")),
            ],
            options={"ordering": ["investor", "effective_from"]},
        ),
    ]
