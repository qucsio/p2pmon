from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("exchange", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="exchangeaccount",
            name="ledger_start_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Orders before this Moscow-time boundary are excluded from ledger/UI/export.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="exchangeaccount",
            name="ledger_start_inclusive",
            field=models.BooleanField(
                default=False,
                help_text="If True, orders at ledger_start_at are also ignored (<= boundary).",
            ),
        ),
    ]
