from decimal import Decimal

from django.db import migrations
from django.utils import timezone


def init_units(apps, schema_editor):
    Investor = apps.get_model("investors", "Investor")
    Txn = apps.get_model("investors", "InvestorCapitalTransaction")
    ExchangeAccount = apps.get_model("exchange", "ExchangeAccount")
    DailySnapshot = apps.get_model("ledger", "DailySnapshot")

    if Txn.objects.exists():
        return  # already initialized

    now = timezone.now()
    user_ids = Investor.objects.values_list("user_id", flat=True).distinct()

    for user_id in user_ids:
        investors = list(Investor.objects.filter(user_id=user_id))
        account = ExchangeAccount.objects.filter(user_id=user_id, is_active=True).first()
        equity = Decimal("0")
        if account:
            snap = (
                DailySnapshot.objects.filter(exchange_account=account)
                .order_by("-day")
                .first()
            )
            if snap:
                equity = snap.total_equity

        share_total = sum(
            (i.share_percent for i in investors if i.is_active), Decimal("0")
        )

        for inv in investors:
            if equity > 0 and share_total > 0:
                units = equity * (inv.share_percent / share_total)
            else:
                units = inv.share_percent
            if units <= 0:
                continue
            Txn.objects.create(
                investor=inv,
                type="correction",
                amount_rub=round(units, 2),
                units_delta=units,
                unit_price=Decimal("1"),
                effective_at=now,
                comment="Инициализация капитала из доли (миграция)",
            )


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
