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
            total_equity=Decimal(equity), daily_total_equity_pnl=Decimal(pnl),
        )

    def mk(self, name, **kw):
        return Investor.objects.create(user=self.user, name=name, **kw)

    def adj(self, type_, amount, d):
        return LedgerAdjustment.objects.create(
            exchange_account=self.account, account=LedgerAdjustment.ACCOUNT_BANK,
            type=type_, currency="RUB", amount_rub=Decimal(amount),
            effective_at=aware(d), include_in_ledger=True,
        )

    def link(self, investor, type_, amount, d):
        a = self.adj(type_, amount, d)
        return services.link_contribution(investor, a, self.user, self.account)

    def report(self):
        rep = services.profit_report(self.user, self.account)
        return rep, {r["investor"].id: r for r in rep["rows"]}


class UnitsTests(Base):
    def test_units_change_only_on_capital_events(self):
        self.snap(date(2026, 1, 1), "100000")
        a = self.mk("A")
        self.link(a, LedgerAdjustment.TYPE_INVESTOR_DEPOSIT, "100000", date(2026, 1, 1))
        self.assertEqual(a.units, Decimal("100000"))
        # trading profit on day 2 must not change units
        self.snap(date(2026, 1, 2), "150000", "50000")
        services.profit_report(self.user, self.account)
        self.assertEqual(a.units, Decimal("100000"))

    def test_profit_report_creates_no_units(self):
        self.snap(date(2026, 1, 1), "100000")
        a = self.mk("A")
        self.link(a, LedgerAdjustment.TYPE_INVESTOR_DEPOSIT, "100000", date(2026, 1, 1))
        self.snap(date(2026, 1, 2), "150000", "50000")
        before = InvestorCapitalTransaction.objects.count()
        services.profit_report(self.user, self.account)
        self.assertEqual(InvestorCapitalTransaction.objects.count(), before)
        self.assertFalse(
            InvestorCapitalTransaction.objects.exclude(
                type__in=services.CAPITAL_EVENT_TYPES).exists()
        )


class IntervalTests(Base):
    def setUp(self):
        super().setUp()
        # Михаил deposits over time; Денискин enters last.
        self.snap(date(2025, 9, 1), "100000", "0")
        self.snap(date(2026, 1, 5), "150000", "50000")   # +50k all under Михаил
        self.m = self.mk("Михаил")
        self.link(self.m, LedgerAdjustment.TYPE_INVESTOR_DEPOSIT, "100000", date(2025, 9, 1))
        self.d = self.mk("Денискин")

    def test_no_manual_period_required(self):
        # Just call the live report — no manual allocation rows needed.
        rep, by = self.report()
        self.assertEqual(by[self.m.id]["displayed_net"], Decimal("50000.00"))

    def test_deniskin_zero_share_before_entry(self):
        # Денискин deposits 2026-01-06; before that his share is 0 and he earns nothing
        # on the +50k that happened on 2026-01-05.
        self.link(self.d, LedgerAdjustment.TYPE_INVESTOR_DEPOSIT, "100000", date(2026, 1, 6))
        self.snap(date(2026, 1, 6), "250000", "0")
        rep, by = self.report()
        self.assertEqual(by[self.d.id]["displayed_net"], Decimal("0.00"))
        self.assertEqual(by[self.m.id]["displayed_net"], Decimal("50000.00"))

    def test_deniskin_capital_value_at_entry(self):
        # Enters at unit price 1.5 (equity 150k / 100k units) → 100k buys ~66666 units,
        # capital value right after = 100000.
        self.link(self.d, LedgerAdjustment.TYPE_INVESTOR_DEPOSIT, "100000", date(2026, 1, 6))
        self.snap(date(2026, 1, 6), "250000", "0")
        cap = services.capital_summary(self.user, self.account)
        drow = next(r for r in cap["rows"] if r["investor"].id == self.d.id)
        self.assertAlmostEqual(drow["capital_value"], Decimal("100000"), delta=Decimal("1"))


class LinkingTests(Base):
    def test_tax_not_linkable(self):
        a = self.mk("A")
        tax = self.adj(LedgerAdjustment.TYPE_TAX_PAYMENT, "5000", date(2026, 1, 1))
        with self.assertRaises(ValidationError):
            services.link_contribution(a, tax, self.user, self.account)

    def test_link_no_duplicate_cash(self):
        self.snap(date(2026, 1, 1), "0")
        a = self.mk("A")
        dep = self.adj(LedgerAdjustment.TYPE_INVESTOR_DEPOSIT, "100000", date(2026, 1, 1))
        n = LedgerAdjustment.objects.count()
        services.link_contribution(a, dep, self.user, self.account)
        self.assertEqual(LedgerAdjustment.objects.count(), n)


class DisplayedProfitTests(Base):
    def setUp(self):
        super().setUp()
        self.snap(date(2026, 1, 1), "100000", "0")
        self.m = self.mk("Михаил")
        self.link(self.m, LedgerAdjustment.TYPE_INVESTOR_DEPOSIT, "100000", date(2026, 1, 1))
        self.danya = self.mk("Даня", profit_share_mode=Investor.PROFIT_SPLIT,
                             source_investor=self.m, split_percent=Decimal("50"))
        self.snap(date(2026, 1, 2), "110000", "10000")

    def test_danya_displayed_profit_zero_capital(self):
        rep, by = self.report()
        self.assertEqual(by[self.danya.id]["displayed_net"], Decimal("5000.00"))
        self.assertEqual(self.danya.units, Decimal("0"))
        cap = services.capital_summary(self.user, self.account)
        drow = next(r for r in cap["rows"] if r["investor"].id == self.danya.id)
        self.assertEqual(drow["capital_value"], Decimal("0"))

    def test_mikhail_capital_value_unchanged_by_split(self):
        # Split reduces Михаил's DISPLAYED profit, not his capital value.
        rep, by = self.report()
        self.assertEqual(by[self.m.id]["gross_by_capital"], Decimal("10000.00"))
        self.assertEqual(by[self.m.id]["displayed_net"], Decimal("5000.00"))
        cap = services.capital_summary(self.user, self.account)
        mrow = next(r for r in cap["rows"] if r["investor"].id == self.m.id)
        # capital value = full equity (only Михаил has units), not reduced by 5000
        self.assertEqual(mrow["capital_value"], Decimal("110000.00"))


class LifetimeReconcileTests(Base):
    def test_lifetime_uses_all_intervals(self):
        self.snap(date(2026, 1, 1), "100000", "0")
        m = self.mk("Михаил")
        self.link(m, LedgerAdjustment.TYPE_INVESTOR_DEPOSIT, "100000", date(2026, 1, 1))
        self.snap(date(2026, 1, 2), "110000", "10000")
        self.snap(date(2026, 1, 3), "115000", "5000")
        rep, by = self.report()
        # 10000 + 5000 across the whole history, not from any saved rows
        self.assertEqual(by[m.id]["displayed_net"], Decimal("15000.00"))

    def test_capital_pnl_reconciles_to_equity_minus_deposits(self):
        self.snap(date(2026, 1, 1), "100000", "0")
        m = self.mk("Михаил")
        self.link(m, LedgerAdjustment.TYPE_INVESTOR_DEPOSIT, "100000", date(2026, 1, 1))
        self.snap(date(2026, 1, 2), "137686", "37686")
        cap = services.capital_summary(self.user, self.account)
        mrow = next(r for r in cap["rows"] if r["investor"].id == m.id)
        # equity 137686 - deposits 100000 ≈ 37686
        self.assertAlmostEqual(mrow["capital_pnl"], Decimal("37686"), delta=Decimal("1"))


class ExternalFlowTests(Base):
    def test_unassigned_external_flow_detected(self):
        self.snap(date(2026, 1, 1), "100000")
        # an 8000 outflow not linked to any investor
        self.adj(LedgerAdjustment.TYPE_WITHDRAWAL, "8000", date(2026, 1, 2))
        flows = services.unassigned_external_flows(self.account)
        self.assertEqual(len(flows), 1)
        self.assertEqual(flows[0].signed_amount_rub(), Decimal("-8000"))
