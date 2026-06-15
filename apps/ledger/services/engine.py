from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.common.decimal_utils import d, q_price, q_rub, q_usdt
from apps.exchange.models import ExchangeAccount
from apps.investors.models import TaxSetting
from apps.ledger.models import DailySnapshot, LedgerEvent, WeeklySnapshot
from apps.ledger.services.events import build_events


@dataclass
class LedgerState:
    bank: Decimal = field(default_factory=lambda: Decimal("0"))
    exchange: Decimal = field(default_factory=lambda: Decimal("0"))
    wac_qty: Decimal = field(default_factory=lambda: Decimal("0"))
    wac_cost: Decimal = field(default_factory=lambda: Decimal("0"))
    wac_realized_cum: Decimal = field(default_factory=lambda: Decimal("0"))
    fees_cum: Decimal = field(default_factory=lambda: Decimal("0"))
    last_price: Decimal = field(default_factory=lambda: Decimal("0"))

    def wac_price(self) -> Decimal:
        if self.wac_qty > 0:
            return q_price(self.wac_cost / self.wac_qty)
        return Decimal("0")

    def normalize(self):
        self.bank = q_rub(self.bank)
        self.exchange = q_usdt(self.exchange)
        self.wac_qty = q_usdt(self.wac_qty)
        self.wac_cost = q_rub(self.wac_cost)


@dataclass
class DayAccumulator:
    wac_realized: Decimal = field(default_factory=lambda: Decimal("0"))
    fees: Decimal = field(default_factory=lambda: Decimal("0"))
    volume_usdt: Decimal = field(default_factory=lambda: Decimal("0"))
    volume_rub: Decimal = field(default_factory=lambda: Decimal("0"))
    last_price: Decimal | None = None
    last_price_eod: Decimal | None = None
    bank_eod: Decimal | None = None
    exchange_eod: Decimal | None = None
    wac_eod: Decimal | None = None
    wac_qty_eod: Decimal | None = None
    wac_cost_eod: Decimal | None = None
    wac_realized_cum_eod: Decimal | None = None
    bank_adj_rub: Decimal = field(default_factory=lambda: Decimal("0"))
    exchange_adj_usdt: Decimal = field(default_factory=lambda: Decimal("0"))


def get_tax_rate(user, day: date) -> Decimal:
    setting = (
        TaxSetting.objects.filter(user=user, is_active=True, effective_from__lte=day)
        .filter(models_Q_tax_to(day))
        .order_by("-effective_from")
        .first()
    )
    if setting:
        return setting.tax_rate
    return Decimal("0")


def models_Q_tax_to(day):
    from django.db.models import Q
    return Q(effective_to__isnull=True) | Q(effective_to__gte=day)


class LedgerEngine:
    def __init__(self, exchange_account: ExchangeAccount):
        self.account = exchange_account

    @transaction.atomic
    def rebuild(self):
        LedgerEvent.objects.filter(exchange_account=self.account).delete()
        DailySnapshot.objects.filter(exchange_account=self.account).delete()
        WeeklySnapshot.objects.filter(exchange_account=self.account).delete()

        events = build_events(self.account)
        state = LedgerState()
        daily_acc: dict[date, DayAccumulator] = defaultdict(DayAccumulator)
        prev_equity: Decimal | None = None
        prev_day: date | None = None

        for ev in events:
            self._persist_event(ev)
            day = ev["occurred_at_moscow"].date()
            wac_realized, fee_rub = self._apply_event(state, ev)

            acc = daily_acc[day]
            acc.wac_realized += wac_realized
            acc.fees += fee_rub
            if ev["event_type"] in (LedgerEvent.EVENT_BUY, LedgerEvent.EVENT_SELL):
                acc.volume_usdt += abs(d(ev["amount_usdt"]))
                acc.volume_rub += abs(d(ev["amount_rub"]))
                acc.last_price = d(ev["price"])
            elif ev["event_type"] in (
                LedgerEvent.EVENT_BANK_DEPOSIT,
                LedgerEvent.EVENT_BANK_WITHDRAWAL,
                LedgerEvent.EVENT_BANK_CORRECTION,
                LedgerEvent.EVENT_TAX_PAYMENT,
                LedgerEvent.EVENT_INVESTOR_DEPOSIT,
                LedgerEvent.EVENT_INVESTOR_WITHDRAWAL,
            ):
                acc.bank_adj_rub += d(ev["amount_rub"])
            elif ev["event_type"] in (
                LedgerEvent.EVENT_EXCHANGE_DEPOSIT,
                LedgerEvent.EVENT_EXCHANGE_WITHDRAWAL,
                LedgerEvent.EVENT_EXCHANGE_CORRECTION,
            ):
                acc.exchange_adj_usdt += d(ev["amount_usdt"])

            state.normalize()
            acc.bank_eod = state.bank
            acc.exchange_eod = state.exchange
            acc.wac_eod = state.wac_price()
            acc.wac_qty_eod = state.wac_qty
            acc.wac_cost_eod = state.wac_cost
            acc.wac_realized_cum_eod = state.wac_realized_cum
            if acc.last_price is not None:
                acc.last_price_eod = acc.last_price
            elif state.last_price > 0:
                acc.last_price_eod = state.last_price

        all_days = sorted(daily_acc.keys())

        for day in all_days:
            acc = daily_acc[day]
            last_price = acc.last_price_eod or acc.last_price or Decimal("0")

            bank = acc.bank_eod or Decimal("0")
            exchange = acc.exchange_eod or Decimal("0")
            wac_price_val = acc.wac_eod or Decimal("0")
            wac_qty = acc.wac_qty_eod or Decimal("0")
            wac_cost = acc.wac_cost_eod or Decimal("0")
            wac_realized_cum = acc.wac_realized_cum_eod or Decimal("0")

            equity = q_rub(bank + q_rub(exchange * last_price))
            adj_effect = q_rub(acc.bank_adj_rub + q_rub(acc.exchange_adj_usdt * last_price))

            if prev_equity is not None and prev_day is not None:
                daily_equity_pnl = q_rub(equity - prev_equity - adj_effect)
            else:
                daily_equity_pnl = Decimal("0")

            wac_unrealized = q_rub((last_price - wac_price_val) * wac_qty) if wac_qty > 0 else Decimal("0")
            running_wac_pnl = q_rub(wac_realized_cum + wac_unrealized)

            gross_realized = acc.wac_realized
            fees = acc.fees
            net_before_tax = q_rub(gross_realized - fees)
            tax_rate = get_tax_rate(self.account.user, day)
            tax_accrual = q_rub(max(net_before_tax, Decimal("0")) * tax_rate)
            net_after_tax = q_rub(net_before_tax - tax_accrual)

            DailySnapshot.objects.create(
                exchange_account=self.account,
                day=day,
                bank_balance=bank,
                exchange_balance=exchange,
                total_equity=equity,
                daily_total_equity_pnl=daily_equity_pnl,
                daily_wac_realized_pnl=acc.wac_realized,
                daily_wac_unrealized_pnl=wac_unrealized,
                running_wac_pnl=running_wac_pnl,
                gross_realized_pnl=gross_realized,
                fees=fees,
                net_profit_before_tax=net_before_tax,
                tax_accrual=tax_accrual,
                net_profit_after_tax=net_after_tax,
                volume_usdt=acc.volume_usdt,
                volume_rub=acc.volume_rub,
                last_price=last_price,
                wac_price=wac_price_val,
                wac_qty=wac_qty,
                wac_cost=wac_cost,
            )

            prev_equity = equity
            prev_day = day

        self._build_weekly_snapshots()

    def _persist_event(self, ev: dict):
        LedgerEvent.objects.create(
            exchange_account=self.account,
            event_type=ev["event_type"],
            source_type=ev["source_type"],
            source_id=ev["source_id"],
            occurred_at_utc=ev["occurred_at_utc"],
            occurred_at_moscow=ev["occurred_at_moscow"],
            currency=ev["currency"],
            amount_rub=ev["amount_rub"],
            amount_usdt=ev["amount_usdt"],
            price=ev["price"],
            fee_amount=ev["fee_amount"],
            fee_currency=ev["fee_currency"],
            include_in_ledger=ev["include_in_ledger"],
            metadata=ev["metadata"],
        )

    def _apply_event(self, state: LedgerState, ev: dict) -> tuple[Decimal, Decimal]:
        event_type = ev["event_type"]
        wac_realized = Decimal("0")
        fee_rub = Decimal("0")

        if event_type == LedgerEvent.EVENT_BUY:
            rub = abs(d(ev["amount_rub"]))
            qty = abs(d(ev["amount_usdt"]))
            fee = d(ev["fee_amount"])
            fee_currency = (ev["fee_currency"] or "").upper()

            if fee_currency == "RUB" and fee > 0:
                rub = q_rub(rub + fee)
                fee_rub = q_rub(fee)
            elif fee_currency == "USDT" and fee > 0:
                fee_rub = q_rub(fee * d(ev["price"]))

            state.bank = q_rub(state.bank - rub)
            state.exchange = q_usdt(state.exchange + qty)
            state.wac_qty = q_usdt(state.wac_qty + qty)
            state.wac_cost = q_rub(state.wac_cost + rub)
            state.fees_cum = q_rub(state.fees_cum + fee_rub)

        elif event_type == LedgerEvent.EVENT_SELL:
            rub = abs(d(ev["amount_rub"]))
            qty_gross = abs(d(ev["amount_usdt"]))
            fee = d(ev["fee_amount"])
            fee_currency = (ev["fee_currency"] or "").upper()
            wac_before = state.wac_price()

            if fee_currency == "USDT" and fee > 0:
                qty_out = q_usdt(qty_gross + fee)
                wac_realized = q_rub(rub - wac_before * qty_out)
                fee_rub = q_rub(fee * d(ev["price"]))
            elif fee_currency == "RUB" and fee > 0:
                net_rub = q_rub(rub - fee)
                qty_out = qty_gross
                wac_realized = q_rub(net_rub - wac_before * qty_out)
                fee_rub = q_rub(fee)
                rub = net_rub
            else:
                qty_out = qty_gross
                wac_realized = q_rub(rub - wac_before * qty_out)

            state.bank = q_rub(state.bank + rub)
            state.exchange = q_usdt(state.exchange - qty_out)
            qty_from_pos = qty_out if qty_out <= state.wac_qty else state.wac_qty
            state.wac_qty = q_usdt(state.wac_qty - qty_from_pos)
            state.wac_cost = q_rub(state.wac_cost - wac_before * qty_from_pos)
            state.wac_realized_cum = q_rub(state.wac_realized_cum + wac_realized)
            state.fees_cum = q_rub(state.fees_cum + fee_rub)

        elif event_type in (
            LedgerEvent.EVENT_BANK_DEPOSIT,
            LedgerEvent.EVENT_BANK_WITHDRAWAL,
            LedgerEvent.EVENT_BANK_CORRECTION,
            LedgerEvent.EVENT_TAX_PAYMENT,
            LedgerEvent.EVENT_INVESTOR_DEPOSIT,
            LedgerEvent.EVENT_INVESTOR_WITHDRAWAL,
        ):
            state.bank = q_rub(state.bank + d(ev["amount_rub"]))

        elif event_type in (
            LedgerEvent.EVENT_EXCHANGE_DEPOSIT,
            LedgerEvent.EVENT_EXCHANGE_WITHDRAWAL,
            LedgerEvent.EVENT_EXCHANGE_CORRECTION,
        ):
            delta = d(ev["amount_usdt"])
            state.exchange = q_usdt(state.exchange + delta)
            state.wac_qty = q_usdt(state.wac_qty + delta)

        state.normalize()
        return wac_realized, fee_rub

    def _build_weekly_snapshots(self):
        daily = DailySnapshot.objects.filter(exchange_account=self.account).order_by("day")
        weekly_data: dict[str, list] = defaultdict(list)

        for snap in daily:
            iso = snap.day.isocalendar()
            week_key = f"{iso.year}-W{iso.week:02d}"
            weekly_data[week_key].append(snap)

        for week, snaps in weekly_data.items():
            last = snaps[-1]
            WeeklySnapshot.objects.create(
                exchange_account=self.account,
                week=week,
                bank_balance=last.bank_balance,
                exchange_balance=last.exchange_balance,
                total_equity=last.total_equity,
                daily_total_equity_pnl=sum(s.daily_total_equity_pnl for s in snaps),
                daily_wac_realized_pnl=sum(s.daily_wac_realized_pnl for s in snaps),
                daily_wac_unrealized_pnl=last.daily_wac_unrealized_pnl,
                running_wac_pnl=last.running_wac_pnl,
                gross_realized_pnl=sum(s.gross_realized_pnl for s in snaps),
                fees=sum(s.fees for s in snaps),
                net_profit_before_tax=sum(s.net_profit_before_tax for s in snaps),
                tax_accrual=sum(s.tax_accrual for s in snaps),
                net_profit_after_tax=sum(s.net_profit_after_tax for s in snaps),
                volume_usdt=sum(s.volume_usdt for s in snaps),
                volume_rub=sum(s.volume_rub for s in snaps),
                last_price=last.last_price,
                wac_price=last.wac_price,
                wac_qty=last.wac_qty,
                wac_cost=last.wac_cost,
            )

    def get_current_state(self) -> LedgerState:
        state = LedgerState()
        events = build_events(self.account)
        for ev in events:
            self._apply_event(state, ev)
        return state
