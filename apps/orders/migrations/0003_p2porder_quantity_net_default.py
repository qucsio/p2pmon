from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0002_rename_orders_p2po_created_6a8f0d_idx_orders_p2po_created_61eb67_idx_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="p2porder",
            name="quantity_net",
            field=models.DecimalField(decimal_places=8, default=Decimal("0"), max_digits=24),
        ),
    ]
