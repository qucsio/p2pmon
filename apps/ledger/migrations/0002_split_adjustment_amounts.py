from decimal import Decimal

from django.db import migrations, models


def migrate_amounts_forward(apps, schema_editor):
    LedgerAdjustment = apps.get_model("ledger", "LedgerAdjustment")
    for adj in LedgerAdjustment.objects.all():
        amount = getattr(adj, "amount", None)
        if amount is None:
            continue
        if adj.account == "bank":
            adj.amount_rub = amount
            adj.amount_usdt = Decimal("0")
        else:
            adj.amount_usdt = amount
            adj.amount_rub = Decimal("0")
        adj.save(update_fields=["amount_rub", "amount_usdt"])


def migrate_amounts_backward(apps, schema_editor):
    LedgerAdjustment = apps.get_model("ledger", "LedgerAdjustment")
    for adj in LedgerAdjustment.objects.all():
        if adj.account == "bank":
            adj.amount = adj.amount_rub
        else:
            adj.amount = adj.amount_usdt
        adj.save(update_fields=["amount"])


class Migration(migrations.Migration):

    dependencies = [
        ("ledger", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="ledgeradjustment",
            name="amount_rub",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=20),
        ),
        migrations.AddField(
            model_name="ledgeradjustment",
            name="amount_usdt",
            field=models.DecimalField(decimal_places=8, default=0, max_digits=24),
        ),
        migrations.RunPython(migrate_amounts_forward, migrate_amounts_backward),
        migrations.RemoveField(
            model_name="ledgeradjustment",
            name="amount",
        ),
    ]
