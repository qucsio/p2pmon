from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0001_initial"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="p2porder",
            new_name="orders_p2po_created_61eb67_idx",
            old_name="orders_p2po_created_6a8f0d_idx",
        ),
        migrations.RenameIndex(
            model_name="p2porder",
            new_name="orders_p2po_side_2caf21_idx",
            old_name="orders_p2po_side_0a1b2c_idx",
        ),
        migrations.RenameIndex(
            model_name="p2porder",
            new_name="orders_p2po_include_7a0c41_idx",
            old_name="orders_p2po_includ_3d4e5f_idx",
        ),
    ]
