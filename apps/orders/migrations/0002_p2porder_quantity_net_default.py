from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="p2porder",
            name="quantity_net",
            field=models.DecimalField(decimal_places=8, default=Decimal("0"), max_digits=24),
        ),
    ]
