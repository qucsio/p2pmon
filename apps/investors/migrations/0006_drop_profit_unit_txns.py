from django.db import migrations


def drop_profit_txns(apps, schema_editor):
    # Profit must never create units. Remove any legacy profit_reinvest /
    # profit_payout capital transactions so units reflect only real capital events.
    Txn = apps.get_model("investors", "InvestorCapitalTransaction")
    Txn.objects.filter(type__in=["profit_reinvest", "profit_payout"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("investors", "0005_allocation_claim_status"),
    ]

    operations = [
        migrations.RunPython(drop_profit_txns, migrations.RunPython.noop),
    ]
