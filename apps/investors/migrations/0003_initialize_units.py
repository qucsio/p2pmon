from decimal import Decimal

from django.db import migrations
from django.utils import timezone


def init_units(apps, schema_editor):
    # Intentionally a no-op. Capital units are now established by *linking*
    # existing investor money already present in the ledger (see the
    # "История взносов" flow / services.link_contribution), not by seeding
    # everyone on the migration date — that would ignore different join dates.
    return


def noop_reverse(apps, schema_editor):
    Txn = apps.get_model("investors", "InvestorCapitalTransaction")
    Txn.objects.filter(comment="Инициализация капитала из доли (миграция)").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("investors", "0002_capital_units"),
        ("exchange", "0001_initial"),
        ("ledger", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(init_units, noop_reverse),
    ]
