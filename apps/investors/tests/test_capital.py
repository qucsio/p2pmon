from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.exchange.models import ExchangeAccount
from apps.investors import services
from apps.investors.models import Investor, InvestorProfitRule
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

    def deposit(self, inv, amount, d):
        a = LedgerAdjustment.objects.create(
            exchange_account=self.account, account=LedgerAdjustment.ACCOUNT_BANK,
            type=LedgerAdjustment.TYPE_INVESTOR_DEPOSIT, currency="RUB",
            amount_rub=Decimal(amount), effective_at=aware(d), include_in_ledger=True)
        return services.link_contribution(inv, a, self.user, self.account)

    def rep(self):
        r = services.investor_report(self.user, self.account)
        return r, {x["investor"].id: x for x in r["rows"]}


class EconomicCapitalTests(Base):
    def test_economic_capital_formula(self):
        self.snap(date(2026, 1, 1), "100000")
        a = self.mk("A")
        self.deposit(a, "100000", date(2026, 1, 1))
        self.snap(date(2026, 1, 2), "120000", "20000")
        r, by = self.rep()
        # deposits - withdrawals + assigned (same_as_capital → full 20000)
        self.assertEqual(by[a.id]["net_external_capital"], Decimal("100000.00"))
        self.assertEqual(by[a.id]["assigned_profit"], Decimal("20000.00"))
        self.assertEqual(by[a.id]["economic_capital"], Decimal("120000.00"))

    def test_no_units_fields_in_report(self):
        self.snap(date(2026, 1, 1), "100000")
        a = self.mk("A")
        self.deposit(a, "100000", date(2026, 1, 1))
        r, by = self.rep()
        row = by[a.id]
        for k in ("units", "unit_price", "raw_exposure_value", "technical_share"):
            self.assertNotIn(k, row)
        self.assertNotIn("total_units", r)
        self.assertNotIn("unit_price", r)


class MultiplierTests(Base):
    def setUp(self):
        super().setUp()
        self.snap(date(2026, 1, 1), "100000")
        self.m = self.mk("Михаил")
        self.deposit(self.m, "100000", date(2026, 1, 1))
        self.d = self.mk("Денискин", profit_share_mode=Investor.PROFIT_MULTIPLIER,
                         profit_share_multiplier=Decimal("0.5"), residual_investor=self.m)
        self.deposit(self.d, "100000", date(2026, 1, 2))
        # both have equal economic capital 100000 going into day 2; profit 10522
        self.snap(date(2026, 1, 2), "210522", "10522")

    def test_same_as_capital_gets_full_gross(self):
        r, by = self.rep()
        # Михаил gross by participation = 5261, assigned = full
        self.assertAlmostEqual(by[self.m.id]["gross_by_participation"], Decimal("5261"), delta=Decimal("1"))

    def test_multiplier_half(self):
        r, by = self.rep()
        dn = by[self.d.id]
        self.assertAlmostEqual(dn["gross_by_participation"], Decimal("5261"), delta=Decimal("1"))
        self.assertAlmostEqual(dn["assigned_profit"], Decimal("2630.5"), delta=Decimal("1"))
        self.assertAlmostEqual(dn["economic_capital"], Decimal("102630"), delta=Decimal("2"))

    def test_residual_to_owner(self):
        r, by = self.rep()
        self.assertAlmostEqual(by[self.d.id]["residual_out"], Decimal("2630.5"), delta=Decimal("1"))
        self.assertAlmostEqual(by[self.m.id]["residual_in"], Decimal("2630.5"), delta=Decimal("1"))

    def test_residual_unassigned_when_no_owner(self):
        self.d.residual_investor = None
        self.d.save()
        r, by = self.rep()
        self.assertTrue(any("остатка" in w for w in r["warnings"]))

    def test_cannot_withdraw_more_than_economic(self):
        with self.assertRaises(ValidationError):
            services.withdraw(self.d, Decimal("105261"), aware(date(2026, 1, 3)),
                              self.account, self.user)

    def test_can_withdraw_up_to_economic(self):
        # ~102630 available → 102000 allowed
        services.withdraw(self.d, Decimal("102000"), aware(date(2026, 1, 3)),
                          self.account, self.user)
        # a later snapshot reflects the outflow (pnl excludes the flow)
        self.snap(date(2026, 1, 3), "108522", "0")
        r, by = self.rep()
        self.assertLess(by[self.d.id]["economic_capital"], Decimal("1000"))


class SplitTests(Base):
    def setUp(self):
        super().setUp()
        self.snap(date(2026, 1, 1), "100000")
        self.m = self.mk("Михаил")
        self.deposit(self.m, "100000", date(2026, 1, 1))

    def test_split_display_only_zero_economic(self):
        danya = self.mk("Даня", profit_share_mode=Investor.PROFIT_SPLIT,
                        source_investor=self.m, split_percent=Decimal("50"))
        self.snap(date(2026, 1, 2), "110000", "10000")
        r, by = self.rep()
        self.assertEqual(by[danya.id]["economic_capital"], Decimal("0.00"))
        self.assertEqual(by[danya.id]["displayed_net"], Decimal("5000.00"))
        # Михаил economic keeps full profit
        self.assertEqual(by[self.m.id]["economic_capital"], Decimal("110000.00"))


class HistoricalRuleTests(Base):
    def test_rule_effective_from_does_not_affect_earlier(self):
        self.snap(date(2026, 1, 1), "100000")
        m = self.mk("Михаил")
        self.deposit(m, "100000", date(2026, 1, 1))
        danya = self.mk("Даня")  # default same_as_capital, no capital
        # Даня becomes split beneficiary only from 2026-02-01
        InvestorProfitRule.objects.create(
            investor=danya, mode=Investor.PROFIT_SPLIT, source_investor=m,
            split_percent=Decimal("50"), effective_from=date(2026, 2, 1))
        self.snap(date(2026, 1, 15), "110000", "10000")   # before rule
        self.snap(date(2026, 2, 15), "120000", "10000")   # after rule
        r, by = self.rep()
        # Before 2026-02-01 Даня gets nothing; after, 50% of Михаил's day profit (5000)
        self.assertEqual(by[danya.id]["economic_capital"], Decimal("0.00"))
        self.assertEqual(by[danya.id]["displayed_net"], Decimal("5000.00"))

    def test_changing_current_rule_does_not_rewrite_history(self):
        self.snap(date(2026, 1, 1), "100000")
        m = self.mk("Михаил")
        self.deposit(m, "100000", date(2026, 1, 1))
        self.snap(date(2026, 1, 2), "110000", "10000")
        r1, by1 = self.rep()
        before = by1[m.id]["assigned_profit"]
        # add a future-only rule; past assignment must not change
        InvestorProfitRule.objects.create(
            investor=m, mode=Investor.PROFIT_MULTIPLIER,
            profit_share_multiplier=Decimal("0.5"), residual_investor=m,
            effective_from=date(2030, 1, 1))
        r2, by2 = self.rep()
        self.assertEqual(by2[m.id]["assigned_profit"], before)


class FixedPctTests(Base):
    def test_fixed_pct_does_not_create_extra_money(self):
        self.snap(date(2026, 1, 1), "100000")
        m = self.mk("Михаил")
        self.deposit(m, "100000", date(2026, 1, 1))
        mgr = self.mk("Управляющий", profit_share_mode=Investor.PROFIT_FIXED_PCT,
                      profit_share_fixed_pct=Decimal("10"))
        self.snap(date(2026, 1, 2), "110000", "10000")
        r, by = self.rep()
        total_assigned = sum(x["assigned_profit"] for x in r["rows"])
        # fixed funded off the top → total assigned == period profit, no extra money
        self.assertAlmostEqual(total_assigned, Decimal("10000"), delta=Decimal("1"))
        self.assertAlmostEqual(by[mgr.id]["assigned_profit"], Decimal("1000"), delta=Decimal("1"))


class ReconcileTests(Base):
    def test_reconcile_to_equity(self):
        self.snap(date(2026, 1, 1), "100000")
        m = self.mk("Михаил")
        self.deposit(m, "100000", date(2026, 1, 1))
        self.snap(date(2026, 1, 2), "137686", "37686")
        rec = services.reconcile(self.user, self.account)
        self.assertAlmostEqual(rec["diff"], Decimal("0"), delta=Decimal("1"))
