from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Investor",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=128)),
                ("share_percent", models.DecimalField(decimal_places=6, max_digits=10)),
                ("is_active", models.BooleanField(default=True)),
                ("comment", models.TextField(blank=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="investors", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="TaxSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=128)),
                ("tax_rate", models.DecimalField(decimal_places=6, max_digits=10)),
                ("effective_from", models.DateField()),
                ("effective_to", models.DateField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("comment", models.TextField(blank=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tax_settings", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-effective_from"]},
        ),
        migrations.CreateModel(
            name="ProfitAllocation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("period_from", models.DateField()),
                ("period_to", models.DateField()),
                ("share_percent", models.DecimalField(decimal_places=6, max_digits=10)),
                ("gross_profit", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("fees_part", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("tax_part", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("net_profit", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("investor", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="allocations", to="investors.investor")),
            ],
            options={"ordering": ["-period_from", "investor__name"]},
        ),
    ]
