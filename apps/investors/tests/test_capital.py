from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.exchange.models import ExchangeAccount
from apps.investors import services
from apps.investors.models import (
    Investor,
    InvestorCapitalTransaction,
    ProfitAllocation,
)
from apps.ledger.models import DailySnapshot, LedgerAdjustment

WIDE_FROM = date(2000, 1, 1)
WIDE_TO = date(2100, 1, 1)


def aware(d):
    return timezone.make_aware(timezone.datetime(d.year, d.month, d.day, 12, 0))


class Base(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("u", password="x")
        self.account = ExchangeAccount.objects.create(user=self.user, name="acc")

    def snap(self, d, equity, pnl=Decimal("0")):
        return DailySnapshot.objects.create(
            exchange_account=self.account, day=d,
            total_equity=Decimal(equity), daily_total_equity_pnl=Decimal(pnl),
        )

    def mk(self, name, **kwargs):
        return Investor.objects.create(user=self.user, name=name, **kwargs)

    def adj(self, type_, amount, d):
        return LedgerAdjustment.objects.create(
            exchange_account=self.account, account=LedgerAdjustment.ACCOUNT_BANK,
            type=type_, currency="RUB", amount_rub=Decimal(amount), effective_at=aware(d),
            include_in_ledger=True,
        )

    def alloc(self):
        preview = services.compute_allocation(self.user, WIDE_FROM, WIDE_TO, self.account)
        return preview, {r["investor"].id: r for r in preview["rows"]}


class MigrationSurvivalTests(Base):
    def test_existing_investors_survive(self):
        # Existing investor records (with legacy share_percent) remain usable.
        a = self.mk("A", share_percent=Decimal("50"))
        self.assertEqual(Investor.objects.filter(pk=a.pk).count(), 1)
        self.assertEqual(a.units, Decimal("0"))  # no units until linked


class LinkingTests(Base):
    def test_link_deposit_no_duplicate_cash(self):
        self.snap(date(2026, 1, 1), "0")
        a = self.mk("A")
        dep = self.adj(LedgerAdjustment.TYPE_INVESTOR_DEPOSIT, "100000", date(2026, 1, 1))
        n_before = LedgerAdjustment.objects.count()
        services.link_contribution(a, dep, self.user, self.account)
        # no new ledger cash created — only a capital transaction linked to it
        self.assertEqual(LedgerAdjustment.objects.count(), n_before)
        txn = InvestorCapitalTransaction.objects.get(investor=a)
        self.assertEqual(txn.linked_ledger_adjustment_id, dep.id)
        self.assertGreater(a.units, Decimal("0"))

    def test_tax_payment_cannot_be_linked(self):
        a = self.mk("A")
        tax = self.adj(LedgerAdjustment.TYPE_TAX_PAYMENT, "5000", date(2026, 1, 1))
        with self.assertRaises(ValidationError):
            services.link_contribution(a, tax, self.user, self.account)

    def test_correction_cannot_be_linked(self):
        a = self.mk("A")
        corr = self.adj(LedgerAdjustment.TYPE_CORRECTION, "5000", date(2026, 1, 1))
        with self.assertRaises(ValidationError):
            services.link_contribution(a, corr, self.user, self.account)


class HistoricalFairnessTests(Base):
    def setUp(self):
        super().setUp()
        # Day 1: A deposits 100k (equity baseline). Profit earned days 2-3.
        self.snap(date(2026, 1, 1), "100000", "0")
        self.snap(date(2026, 1, 2), "110000", "10000")   # +10k while only A in
        self.a = self.mk("A")
        dep_a = self.adj(LedgerAdjustment.TYPE_INVESTOR_DEPOSIT, "100000", date(2026, 1, 1))
        services.link_contribution(self.a, dep_a, self.user, self.account)

    def test_late_investor_gets_no_old_profit(self):
        # B enters day 3; profit on day 2 must stay fully with A.
        self.snap(date(2026, 1, 3), "210000", "0")  # B deposits 100k, no trading pnl
        b = self.mk("B")
        dep_b = self.adj(LedgerAdjustment.TYPE_INVESTOR_DEPOSIT, "100000", date(2026, 1, 3))
        services.link_contribution(b, dep_b, self.user, self.account)
        _, rows = self.alloc()
        self.assertEqual(rows[b.id]["net_profit"], Decimal("0.00"))
        self.assertEqual(rows[self.a.id]["net_profit"], Decimal("10000.00"))

    def test_late_investor_units_at_current_price(self):
        # On day 3 the unit price reflects equity grown to 110k over A's 100k units.
        self.snap(date(2026, 1, 3), "210000", "0")
        b = self.mk("B")
        dep_b = self.adj(LedgerAdjustment.TYPE_INVESTOR_DEPOSIT, "110000", date(2026, 1, 3))
        services.link_contribution(b, dep_b, self.user, self.account)
        txn = InvestorCapitalTransaction.objects.get(investor=b)
        # price ~ 110000/100000 = 1.1 → 110000/1.1 = 100000 units
        self.assertAlmostEqual(txn.unit_price, Decimal("1.1"), places=4)
        self.assertAlmostEqual(txn.units_delta, Decimal("100000"), places=2)


class SameDayTests(Base):
    def test_same_day_consistent_price(self):
        self.snap(date(2026, 1, 1), "100000", "0")
        self.snap(date(2026, 1, 2), "120000", "20000")
        a = self.mk("A"); b = self.mk("B")
        da = self.adj(LedgerAdjustment.TYPE_INVESTOR_DEPOSIT, "100000", date(2026, 1, 1))
        services.link_contribution(a, da, self.user, self.account)
        # Two same-day deposits on day 3 — must get identical unit price.
        self.snap(date(2026, 1, 3), "320000", "0")
        d1 = self.adj(LedgerAdjustment.TYPE_INVESTOR_DEPOSIT, "60000", date(2026, 1, 3))
        d2 = self.adj(LedgerAdjustment.TYPE_INVESTOR_DEPOSIT, "60000", date(2026, 1, 3))
        services.link_contribution(b, d1, self.user, self.account)
        c = self.mk("C")
        services.link_contribution(c, d2, self.user, self.account)
        t1 = InvestorCapitalTransaction.objects.get(linked_ledger_adjustment=d1)
        t2 = InvestorCapitalTransaction.objects.get(linked_ledger_adjustment=d2)
        self.assertEqual(t1.unit_price, t2.unit_price)


class ProfitModeTests(Base):
    def _setup_capital(self):
        self.snap(date(2026, 1, 1), "100000", "0")
        self.a = self.mk("A")
        da = self.adj(LedgerAdjustment.TYPE_INVESTOR_DEPOSIT, "100000", date(2026, 1, 1))
        services.link_contribution(self.a, da, self.user, self.account)

    def test_multiplier_half(self):
        self._setup_capital()
        # B has capital share 50%, multiplier 0.5 → profit share 25%.
        b = self.mk("B", profit_share_mode=Investor.PROFIT_MULTIPLIER,
                    profit_share_multiplier=Decimal("0.5"))
        db = self.adj(LedgerAdjustment.TYPE_INVESTOR_DEPOSIT, "100000", date(2026, 1, 1))
        services.link_contribution(b, db, self.user, self.account)
        self.snap(date(2026, 1, 2), "210000", "10000")
        _, rows = self.alloc()
        self.assertAlmostEqual(rows[b.id]["profit_share_pct"], Decimal("25"), places=2)
        self.assertEqual(rows[b.id]["net_profit"], Decimal("2500.00"))

    def test_split_from_investor(self):
        self._setup_capital()
        partner = self.mk("Partner", profit_share_mode=Investor.PROFIT_SPLIT,
                          source_investor=self.a, split_percent=Decimal("50"))
        self.snap(date(2026, 1, 2), "110000", "10000")
        _, rows = self.alloc()
        # A earns 10000 from capital, partner takes 50% → 5000 each.
        self.assertEqual(rows[partner.id]["net_profit"], Decimal("5000.00"))
        self.assertEqual(rows[self.a.id]["net_profit"], Decimal("5000.00"))

    def test_split_partner_keeps_zero_units(self):
        self._setup_capital()
        partner = self.mk("Partner", profit_share_mode=Investor.PROFIT_SPLIT,
                          source_investor=self.a, split_percent=Decimal("50"))
        self.snap(date(2026, 1, 2), "110000", "10000")
        self.assertEqual(partner.units, Decimal("0"))
        self.assertEqual(services.capital_share_pct(partner, self.user), Decimal("0"))


class SettlementTests(Base):
    def _alloc_saved(self):
        self.snap(date(2026, 1, 1), "100000", "0")
        self.a = self.mk("A")
        da = self.adj(LedgerAdjustment.TYPE_INVESTOR_DEPOSIT, "100000", date(2026, 1, 1))
        services.link_contribution(self.a, da, self.user, self.account)
        self.snap(date(2026, 1, 2), "110000", "10000")
        preview = services.compute_allocation(self.user, WIDE_FROM, WIDE_TO, self.account)
        services.save_allocation(self.user, WIDE_FROM, WIDE_TO, preview)

    def test_paid_out_does_not_increase_units(self):
        self._alloc_saved()
        before = self.a.units
        services.settle_period(self.user, self.account, WIDE_FROM, WIDE_TO,
                               ProfitAllocation.STATUS_PAID_OUT)
        self.assertEqual(self.a.units, before)

    def test_reinvest_creates_units(self):
        self._alloc_saved()
        before = self.a.units
        services.settle_period(self.user, self.account, WIDE_FROM, WIDE_TO,
                               ProfitAllocation.STATUS_REINVESTED)
        self.assertGreater(self.a.units, before)

    def test_saved_allocation_frozen_after_deposit(self):
        self._alloc_saved()
        alloc = self.a.allocations.first()
        frozen = alloc.net_profit
        # later deposit must not change already-saved allocation amounts
        self.snap(date(2026, 2, 1), "300000", "0")
        dep = self.adj(LedgerAdjustment.TYPE_INVESTOR_DEPOSIT, "200000", date(2026, 2, 1))
        b = self.mk("B")
        services.link_contribution(b, dep, self.user, self.account)
        alloc.refresh_from_db()
        self.assertEqual(alloc.net_profit, frozen)
