from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.exchange.models import ExchangeAccount
from apps.investors import services
from apps.investors.models import Investor, InvestorCapitalTransaction, ProfitAllocation
from apps.ledger.models import DailySnapshot


class CapitalTestBase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("u", password="x")
        self.account = ExchangeAccount.objects.create(user=self.user, name="acc")
        self.snap = DailySnapshot.objects.create(
            exchange_account=self.account,
            day=date(2026, 1, 1),
            total_equity=Decimal("100000"),
        )

    def mk(self, name, share=Decimal("0"), **kwargs):
        return Investor.objects.create(user=self.user, name=name, share_percent=share, **kwargs)

    def init_5050(self):
        self.a = self.mk("A", Decimal("50"))
        self.b = self.mk("B", Decimal("50"))
        services.initialize_units(self.user, self.account)
        self.a.refresh_from_db()
        self.b.refresh_from_db()


class InitializationTests(CapitalTestBase):
    def test_units_initialized_from_shares(self):
        self.init_5050()
        # equity 100000 split 50/50 → 50000 units each, price 1
        self.assertEqual(self.a.units, Decimal("50000"))
        self.assertEqual(self.b.units, Decimal("50000"))
        self.assertEqual(services.total_units(self.user), Decimal("100000"))
        self.assertEqual(services.current_unit_price(self.user, self.account), Decimal("1"))

    def test_existing_investors_survive_and_shares_match(self):
        self.init_5050()
        self.assertEqual(Investor.objects.count(), 2)
        self.assertAlmostEqual(services.capital_share_pct(self.a), Decimal("50"))

    def test_idempotent(self):
        self.init_5050()
        services.initialize_units(self.user, self.account)  # no-op
        self.assertEqual(InvestorCapitalTransaction.objects.count(), 2)

    def test_unit_price_never_zero(self):
        self.assertEqual(services.current_unit_price(self.user, self.account), Decimal("1"))


class DepositWithdrawTests(CapitalTestBase):
    def test_deposit_changes_capital_share(self):
        self.init_5050()
        before = services.capital_share_pct(self.a)
        services.deposit(self.a, Decimal("50000"), timezone.now(), self.account, self.user)
        self.assertGreater(services.capital_share_pct(self.a), before)
        # A now 100000 / 150000 ≈ 66.67%
        self.assertAlmostEqual(services.capital_share_pct(self.a), Decimal("66.666667"), places=3)

    def test_withdrawal_changes_capital_share(self):
        self.init_5050()
        before = services.capital_share_pct(self.a)
        services.withdraw(self.a, Decimal("25000"), timezone.now(), self.account, self.user)
        self.assertLess(services.capital_share_pct(self.a), before)

    def test_withdrawal_cannot_exceed_units(self):
        self.init_5050()
        with self.assertRaises(ValidationError):
            services.withdraw(self.a, Decimal("999999"), timezone.now(), self.account, self.user)


class AllocationTests(CapitalTestBase):
    def _period_net(self, net):
        self.snap.net_profit_after_tax = Decimal(net)
        self.snap.save()

    def test_fixed_pct_with_zero_capital_receives_profit(self):
        self.init_5050()
        c = self.mk("C", profit_share_mode=Investor.PROFIT_FIXED_PCT,
                    profit_share_fixed_pct=Decimal("10"))
        self._period_net("10000")
        res = services.compute_allocation(self.user, date(2000, 1, 1), date(2100, 1, 1), self.account)
        row = next(r for r in res["rows"] if r["investor"].id == c.id)
        self.assertEqual(row["capital_share_pct"], Decimal("0"))
        self.assertEqual(row["profit_share_pct"], Decimal("10"))
        self.assertEqual(row["net_profit"], Decimal("1000.00"))

    def test_multiplier_half_receives_half_of_capital_share(self):
        self.a = self.mk("A", Decimal("50"), profit_share_mode=Investor.PROFIT_MULTIPLIER,
                         profit_share_multiplier=Decimal("0.5"))
        self.b = self.mk("B", Decimal("50"))
        services.initialize_units(self.user, self.account)
        self._period_net("10000")
        res = services.compute_allocation(self.user, date(2000, 1, 1), date(2100, 1, 1), self.account)
        row = next(r for r in res["rows"] if r["investor"].id == self.a.id)
        # capital share 50% * 0.5 = 25%
        self.assertAlmostEqual(row["profit_share_pct"], Decimal("25"))
        self.assertEqual(row["net_profit"], Decimal("2500.00"))

    def test_same_as_capital_absorbs_remainder(self):
        self.init_5050()
        self._period_net("10000")
        res = services.compute_allocation(self.user, date(2000, 1, 1), date(2100, 1, 1), self.account)
        # both same_as_capital, equal capital → 50/50, fully allocated
        self.assertAlmostEqual(res["allocated_pct"], Decimal("100"))
        for r in res["rows"]:
            self.assertEqual(r["net_profit"], Decimal("5000.00"))


class SettlementTests(CapitalTestBase):
    def _alloc(self, net="10000"):
        self.init_5050()
        self.snap.net_profit_after_tax = Decimal(net)
        self.snap.save()
        preview = services.compute_allocation(self.user, date(2000, 1, 1), date(2100, 1, 1), self.account)
        return services.save_allocation(self.user, date(2000, 1, 1), date(2100, 1, 1), preview)

    def test_reinvest_increases_capital(self):
        self._alloc()
        alloc = self.a.allocations.first()
        before = self.a.units
        services.settle_allocation(alloc, ProfitAllocation.STATUS_REINVESTED, self.account, self.user)
        self.assertGreater(self.a.units, before)

    def test_payout_does_not_increase_capital(self):
        self._alloc()
        alloc = self.a.allocations.first()
        before = self.a.units
        services.settle_allocation(alloc, ProfitAllocation.STATUS_PAID_OUT, self.account, self.user)
        self.assertEqual(self.a.units, before)

    def test_allocation_frozen_after_settlement(self):
        self._alloc()
        alloc = self.a.allocations.first()
        services.settle_allocation(alloc, ProfitAllocation.STATUS_PAID_OUT, self.account, self.user)
        frozen_amount = alloc.net_profit
        # re-saving the same period must not touch settled rows
        preview = services.compute_allocation(self.user, date(2000, 1, 1), date(2100, 1, 1), self.account)
        services.save_allocation(self.user, date(2000, 1, 1), date(2100, 1, 1), preview)
        alloc.refresh_from_db()
        self.assertEqual(alloc.status, ProfitAllocation.STATUS_PAID_OUT)
        self.assertEqual(alloc.net_profit, frozen_amount)

    def test_cannot_settle_twice(self):
        self._alloc()
        alloc = self.a.allocations.first()
        services.settle_allocation(alloc, ProfitAllocation.STATUS_PAID_OUT, self.account, self.user)
        with self.assertRaises(ValidationError):
            services.settle_allocation(alloc, ProfitAllocation.STATUS_REINVESTED, self.account, self.user)
