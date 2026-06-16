from django.db import migrations


def clear(apps, schema_editor):
    # ProfitAllocation / InvestorPositionSnapshot are no longer the source of truth
    # for lifetime earned profit (it's computed live). Clear stale/legacy rows so
    # nothing incorrect is displayed; the tables remain for optional future export.
    apps.get_model("investors", "ProfitAllocation").objects.all().delete()
    apps.get_model("investors", "InvestorPositionSnapshot").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("investors", "0006_drop_profit_unit_txns"),
    ]

    operations = [
        migrations.RunPython(clear, migrations.RunPython.noop),
    ]
