from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.exchange.models import ExchangeAccount
from apps.investors import services
from apps.investors.models import Investor, InvestorCapitalTransaction
from apps.ledger.models import DailySnapshot, LedgerAdjustment


def aware(d):
    return timezone.make_aware(timezone.datetime(d.year, d.month, d.day, 12, 0))


class Base(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("u", password="x")
        self.account = ExchangeAccount.objects.create(user=self.user, name="acc")

    def snap(self, d, equity, pnl="0"):
        return DailySnapshot.objects.create(
            exchange_account=self.account, day=d,
            total_equity=Decimal(equity), daily_total_equity_pnl=Decimal(pnl))

    def mk(self, name, **kw):
        return Investor.objects.create(user=self.user, name=name, **kw)

    def link(self, investor, amount, d, type_=LedgerAdjustment.TYPE_INVESTOR_DEPOSIT):
        a = LedgerAdjustment.objects.create(
            exchange_account=self.account, account=LedgerAdjustment.ACCOUNT_BANK,
            type=type_, currency="RUB", amount_rub=Decimal(amount),
            effective_at=aware(d), include_in_ledger=True)
        return services.link_contribution(investor, a, self.user, self.account)

    def rep(self):
        r = services.investor_report(self.user, self.account)
        return r, {x["investor"].id: x for x in r["rows"]}


class UnitsOnlyOnCapitalEvents(Base):
    def test_profit_creates_no_units(self):
        self.snap(date(2026, 1, 1), "100000")
        a = self.mk("A")
        self.link(a, "100000", date(2026, 1, 1))
        self.snap(date(2026, 1, 2), "150000", "50000")
        before = a.units
        services.investor_report(self.user, self.account)
        self.assertEqual(a.units, before)
        self.assertFalse(
            InvestorCapitalTransaction.objects.exclude(
                type__in=services.CAPITAL_EVENT_TYPES).exists())


class SameAsCapitalTests(Base):
    def test_economic_equals_raw_for_same_as_capital(self):
        self.snap(date(2026, 1, 1), "100000")
        a = self.mk("A")  # same_as_capital, alone
        self.link(a, "100000", date(2026, 1, 1))
        self.snap(date(2026, 1, 2), "137686", "37686")
        r, by = self.rep()
        row = by[a.id]
        self.assertEqual(row["economic_capital"], row["raw_exposure_value"])
        self.assertAlmostEqual(row["economic_capital"], Decimal("137686"), delta=Decimal("1"))


class MultiplierTests(Base):
    def setUp(self):
        super().setUp()
        self.snap(date(2026, 1, 1), "100000")
        self.m = self.mk("Михаил")          # same_as_capital, residual owner
        self.link(self.m, "100000", date(2026, 1, 1))
        self.d = self.mk("Денискин", profit_share_mode=Investor.PROFIT_MULTIPLIER,
                         profit_share_multiplier=Decimal("0.5"), residual_investor=self.m)
        self.link(self.d, "100000", date(2026, 1, 2))
        # day with profit while both in (≈5261 raw to Денискин on his half of equity)
        # Build so Денискин raw gross ≈ 5261: total equity 200000, profit 10522 split 50/50.
        self.snap(date(2026, 1, 2), "210522", "10522")

    def test_multiplier_assigned_half_of_gross(self):
        r, by = self.rep()
        dn = by[self.d.id]
        # gross ≈ 5261 (half of 10522), assigned = 0.5 × gross ≈ 2630
        self.assertAlmostEqual(dn["gross_by_weight"], Decimal("5261"), delta=Decimal("1"))
        self.assertAlmostEqual(dn["assigned_profit"], Decimal("2630.5"), delta=Decimal("1"))

    def test_multiplier_economic_capital_not_raw(self):
        r, by = self.rep()
        dn = by[self.d.id]
        # economic ≈ 100000 + 2630 = 102630, NOT 105261
        self.assertAlmostEqual(dn["economic_capital"], Decimal("102630"), delta=Decimal("2"))
        self.assertGreater(dn["raw_exposure_value"], dn["economic_capital"])

    def test_residual_goes_to_owner(self):
        r, by = self.rep()
        self.assertAlmostEqual(by[self.d.id]["residual_out"], Decimal("2630.5"), delta=Decimal("1"))
        self.assertAlmostEqual(by[self.m.id]["residual_in"], Decimal("2630.5"), delta=Decimal("1"))
        # Михаил economic = external 100000 + own gross 5261 + residual 2630 ≈ 107891
        self.assertAlmostEqual(by[self.m.id]["economic_capital"], Decimal("107891"), delta=Decimal("2"))

    def test_residual_unassigned_when_no_owner(self):
        self.d.residual_investor = None
        self.d.save()
        r, by = self.rep()
        self.assertGreater(r["unassigned_residual"], Decimal("0"))
        self.assertIn("Денискин", r["warnings"])


class SplitTests(Base):
    def setUp(self):
        super().setUp()
        self.snap(date(2026, 1, 1), "100000")
        self.m = self.mk("Михаил")
        self.link(self.m, "100000", date(2026, 1, 1))
        self.danya = self.mk("Даня", profit_share_mode=Investor.PROFIT_SPLIT,
                             source_investor=self.m, split_percent=Decimal("50"))
        self.snap(date(2026, 1, 2), "110000", "10000")

    def test_split_displayed_but_zero_economic_capital(self):
        r, by = self.rep()
        dn = by[self.danya.id]
        self.assertEqual(self.danya.units, Decimal("0"))
        self.assertEqual(dn["economic_capital"], Decimal("0"))
        self.assertEqual(dn["displayed_net"], Decimal("5000.00"))

    def test_split_does_not_reduce_source_economic(self):
        r, by = self.rep()
        # Михаил economic keeps full profit (split is display-only)
        self.assertEqual(by[self.m.id]["economic_capital"], Decimal("110000.00"))
        self.assertEqual(by[self.m.id]["displayed_net"], Decimal("5000.00"))


class IntervalTests(Base):
    def test_late_investor_no_old_profit_and_entry_value(self):
        self.snap(date(2025, 9, 1), "100000", "0")
        self.snap(date(2026, 1, 5), "150000", "50000")
        m = self.mk("Михаил")
        self.link(m, "100000", date(2025, 9, 1))
        d = self.mk("Денискин")
        self.link(d, "100000", date(2026, 1, 6))
        self.snap(date(2026, 1, 6), "250000", "0")
        r, by = self.rep()
        self.assertEqual(by[d.id]["assigned_profit"], Decimal("0.00"))
        self.assertEqual(by[m.id]["assigned_profit"], Decimal("50000.00"))
        # Денискин enters at unit price 1.5 → economic ≈ external 100000
        self.assertAlmostEqual(by[d.id]["economic_capital"], Decimal("100000"), delta=Decimal("1"))


class ExternalFlowTests(Base):
    def test_unassigned_flow_detected(self):
        self.snap(date(2026, 1, 1), "100000")
        LedgerAdjustment.objects.create(
            exchange_account=self.account, account=LedgerAdjustment.ACCOUNT_BANK,
            type=LedgerAdjustment.TYPE_WITHDRAWAL, currency="RUB",
            amount_rub=Decimal("8000"), effective_at=aware(date(2026, 1, 2)),
            include_in_ledger=True)
        flows = services.unassigned_external_flows(self.account)
        self.assertEqual(len(flows), 1)
        self.assertEqual(flows[0].signed_amount_rub(), Decimal("-8000"))

    def test_tax_not_linkable(self):
        a = self.mk("A")
        tax = LedgerAdjustment.objects.create(
            exchange_account=self.account, account=LedgerAdjustment.ACCOUNT_BANK,
            type=LedgerAdjustment.TYPE_TAX_PAYMENT, currency="RUB",
            amount_rub=Decimal("5000"), effective_at=aware(date(2026, 1, 1)),
            include_in_ledger=True)
        with self.assertRaises(ValidationError):
            services.link_contribution(a, tax, self.user, self.account)
