import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("investors", "0007_clear_stale_allocations"),
    ]

    operations = [
        migrations.AddField(
            model_name="investor",
            name="residual_investor",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="residual_recipients", to="investors.investor",
            ),
        ),
    ]
