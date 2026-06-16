from django.db import migrations, models


def reclassify(apps, schema_editor):
    ProfitAllocation = apps.get_model("investors", "ProfitAllocation")
    claim_modes = {"split_from_investor", "fixed_pct"}
    for a in ProfitAllocation.objects.filter(status="unpaid").select_related("investor"):
        a.status = "unpaid_claim" if a.investor.profit_share_mode in claim_modes else "retained_in_capital"
        a.save(update_fields=["status"])


def back(apps, schema_editor):
    ProfitAllocation = apps.get_model("investors", "ProfitAllocation")
    ProfitAllocation.objects.filter(
        status__in=["unpaid_claim", "retained_in_capital"]
    ).update(status="unpaid")


class Migration(migrations.Migration):

    dependencies = [
        ("investors", "0004_split_and_position_snapshot"),
    ]

    operations = [
        migrations.AlterField(
            model_name="profitallocation",
            name="status",
            field=models.CharField(
                choices=[
                    ("retained_in_capital", "В капитале (NAV)"),
                    ("unpaid_claim", "Невыплаченное требование"),
                    ("paid_out", "Выплачено"),
                    ("reinvested", "Реинвестировано"),
                    ("unpaid", "Не выплачено (устар.)"),
                ],
                default="retained_in_capital",
                max_length=20,
            ),
        ),
        migrations.RunPython(reclassify, back),
    ]
