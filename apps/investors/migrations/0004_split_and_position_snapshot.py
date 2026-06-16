import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("investors", "0003_initialize_units"),
    ]

    operations = [
        migrations.AlterField(
            model_name="investor",
            name="profit_share_mode",
            field=models.CharField(
                choices=[
                    ("same_as_capital", "Как доля капитала"),
                    ("multiplier", "Множитель от доли капитала"),
                    ("fixed_pct", "Фиксированный процент"),
                    ("none", "Без прибыли"),
                    ("split_from_investor", "Доля от прибыли другого инвестора"),
                ],
                default="same_as_capital",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="investor",
            name="source_investor",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="profit_recipients", to="investors.investor",
            ),
        ),
        migrations.AddField(
            model_name="investor",
            name="split_percent",
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=10, null=True),
        ),
        migrations.CreateModel(
            name="InvestorPositionSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("period_from", models.DateField()),
                ("period_to", models.DateField()),
                ("opening_units", models.DecimalField(decimal_places=8, default=0, max_digits=30)),
                ("closing_units", models.DecimalField(decimal_places=8, default=0, max_digits=30)),
                ("unit_price", models.DecimalField(decimal_places=8, default=0, max_digits=30)),
                ("capital_value_rub", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("capital_share_pct", models.DecimalField(decimal_places=6, default=0, max_digits=10)),
                ("profit_share_pct", models.DecimalField(decimal_places=6, default=0, max_digits=10)),
                ("earned_profit_rub", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("cumulative_earned_profit_rub", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("paid_out_profit_rub", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("reinvested_profit_rub", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("unpaid_profit_rub", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("investor", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="position_snapshots", to="investors.investor")),
            ],
            options={"ordering": ["-period_to", "investor__name"]},
        ),
        migrations.AddConstraint(
            model_name="investorpositionsnapshot",
            constraint=models.UniqueConstraint(
                fields=["investor", "period_from", "period_to"],
                name="unique_position_snapshot_period",
            ),
        ),
    ]
