"""Fund-unit capital accounting and profit allocation for investors.

Capital ownership is tracked as fund units. Each investor's capital value is
``units * unit_price`` where ``unit_price = portfolio_equity / total_units``.
Deposits/withdrawals issue/redeem units at the current price (so existing
investors are not diluted), and they post a matching LedgerAdjustment so the
portfolio cash actually moves. Profit allocation is a separate concept driven
by each investor's ``profit_share_mode``.
"""
from datetime import datetime, time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.common.decimal_utils import q_rub
from apps.investors.models import (
    Investor,
    InvestorCapitalTransaction,
    InvestorPositionSnapshot,
    ProfitAllocation,
)
from apps.ledger.models import DailySnapshot, LedgerAdjustment

HUNDRED = Decimal("100")
EPS = Decimal("0.01")

# Only genuine capital movements may be linked as investor contributions.
# Tax payments, fee corrections and operational corrections are excluded.
LINKABLE_ADJUSTMENT_TYPES = {
    LedgerAdjustment.TYPE_DEPOSIT,
    LedgerAdjustment.TYPE_WITHDRAWAL,
    LedgerAdjustment.TYPE_INVESTOR_DEPOSIT,
    LedgerAdjustment.TYPE_INVESTOR_WITHDRAWAL,
}


# --------------------------------------------------------------------------- #
# Capital / units
# --------------------------------------------------------------------------- #
def portfolio_equity(account) -> Decimal:
    if not account:
        return Decimal("0")
    snap = (
        DailySnapshot.objects.filter(exchange_account=account)
        .order_by("-day")
        .first()
    )
    return snap.total_equity if snap else Decimal("0")


def total_units(user) -> Decimal:
    return (
        InvestorCapitalTransaction.objects.filter(investor__user=user)
        .aggregate(t=Sum("units_delta"))["t"]
        or Decimal("0")
    )


def units_as_of(user, when) -> Decimal:
    return (
        InvestorCapitalTransaction.objects.filter(
            investor__user=user, effective_at__lte=when
        ).aggregate(t=Sum("units_delta"))["t"]
        or Decimal("0")
    )


def investor_units_as_of(investor, when) -> Decimal:
    return (
        investor.capital_transactions.filter(effective_at__lte=when)
        .aggregate(t=Sum("units_delta"))["t"]
        or Decimal("0")
    )


def equity_as_of(account, day) -> Decimal:
    """Latest snapshot equity on or before `day` (a date)."""
    if not account:
        return Decimal("0")
    snap = (
        DailySnapshot.objects.filter(exchange_account=account, day__lte=day)
        .order_by("-day")
        .first()
    )
    return snap.total_equity if snap else Decimal("0")


def current_unit_price(user, account) -> Decimal:
    """Price of one unit in RUB. Never returns zero once units exist."""
    tu = total_units(user)
    if tu <= 0:
        return Decimal("1")
    equity = portfolio_equity(account)
    if equity <= 0:
        # No equity snapshot yet — fall back to par so unit price is never zero.
        return Decimal("1")
    return equity / tu


def contribution_unit_price(user, account, when, units_before=None) -> Decimal:
    """Unit price used to value a contribution at `when` — equity at the start of
    that day (prior snapshot) divided by units already issued. Par for genesis."""
    if units_before is None:
        units_before = units_as_of(user, when) - Decimal("0")  # excludes nothing
    if units_before <= 0:
        return Decimal("1")
    from datetime import timedelta

    prev_day = (when.date() if hasattr(when, "date") else when) - timedelta(days=1)
    equity = equity_as_of(account, prev_day)
    if equity <= 0:
        return Decimal("1")
    return equity / units_before


def capital_share_pct(investor, user=None, when=None) -> Decimal:
    user = user or investor.user
    if when is not None:
        tu = units_as_of(user, when)
        iu = investor_units_as_of(investor, when)
    else:
        tu = total_units(user)
        iu = investor.units
    if tu <= 0:
        return Decimal("0")
    return (iu / tu) * HUNDRED


def day_open_unit_price(account, day, units_open) -> Decimal:
    """Unit price at the START of `day` — equity at end of the prior day divided
    by units already issued before that day. All transactions on the same day use
    this single price, so same-day deposits never get different prices."""
    if units_open <= 0:
        return Decimal("1")
    from datetime import timedelta

    equity_prev = equity_as_of(account, day - timedelta(days=1))
    if equity_prev <= 0:
        return Decimal("1")
    return equity_prev / units_open


@transaction.atomic
def recompute_units(user, account):
    """Recompute unit_price/units_delta of every capital transaction in
    chronological order. Amount stays fixed. All transactions on the same day
    share one opening unit price (fairness), and ordering within a day cannot
    skew prices."""
    txns = list(
        InvestorCapitalTransaction.objects.filter(investor__user=user)
        .order_by("effective_at", "id")
    )
    running = Decimal("0")
    cur_day = None
    day_open_units = Decimal("0")
    for t in txns:
        d = t.effective_at.date()
        if d != cur_day:
            cur_day = d
            day_open_units = running  # frozen for the whole day
        price = day_open_unit_price(account, d, day_open_units)
        if t.type == InvestorCapitalTransaction.TYPE_PROFIT_PAYOUT:
            t.units_delta = Decimal("0")
            t.unit_price = price
            t.save(update_fields=["units_delta", "unit_price"])
            continue
        if t.type == InvestorCapitalTransaction.TYPE_CORRECTION:
            t.unit_price = price or t.unit_price
            t.save(update_fields=["unit_price"])
            running += t.units_delta  # legacy/manual corrections keep their units
            continue
        sign = Decimal("-1") if t.type == InvestorCapitalTransaction.TYPE_WITHDRAWAL else Decimal("1")
        t.unit_price = price
        t.units_delta = sign * (t.amount_rub / price)
        t.save(update_fields=["units_delta", "unit_price"])
        running += t.units_delta


@transaction.atomic
def link_contribution(investor, adjustment, user, account, recompute=True):
    """Attach an existing bank LedgerAdjustment to an investor as a capital
    contribution (no new cash is created)."""
    if adjustment.investor_transactions.exists():
        raise ValidationError("Эта корректировка уже привязана к инвестору.")
    if adjustment.type not in LINKABLE_ADJUSTMENT_TYPES:
        raise ValidationError(
            "Этот тип корректировки нельзя привязывать как капитал инвестора "
            "(налоги и операционные корректировки исключены)."
        )
    if adjustment.account != LedgerAdjustment.ACCOUNT_BANK:
        raise ValidationError("Привязывать можно только банковские (рублёвые) корректировки.")
    signed = adjustment.signed_amount_rub()
    if signed == 0:
        raise ValidationError("Корректировка не меняет банковский баланс в рублях.")
    is_deposit = signed > 0
    txn = InvestorCapitalTransaction.objects.create(
        investor=investor,
        type=(InvestorCapitalTransaction.TYPE_DEPOSIT if is_deposit
              else InvestorCapitalTransaction.TYPE_WITHDRAWAL),
        amount_rub=abs(signed),
        units_delta=Decimal("0"),  # filled by recompute
        unit_price=Decimal("0"),
        effective_at=adjustment.effective_at,
        linked_ledger_adjustment=adjustment,
        comment=f"Привязка истории: {adjustment.get_type_display()}",
    )
    if recompute:
        recompute_units(user, account)
    return txn


# --------------------------------------------------------------------------- #
# Deposits / withdrawals
# --------------------------------------------------------------------------- #
@transaction.atomic
def deposit(investor, amount_rub, effective_at, account, user, comment=""):
    amount_rub = Decimal(amount_rub)
    if amount_rub <= 0:
        raise ValidationError("Сумма депозита должна быть положительной.")
    if not account:
        raise ValidationError("Нет активного аккаунта биржи для проводки депозита.")

    price = current_unit_price(user, account)
    units = amount_rub / price

    adj = LedgerAdjustment.objects.create(
        exchange_account=account,
        account=LedgerAdjustment.ACCOUNT_BANK,
        type=LedgerAdjustment.TYPE_INVESTOR_DEPOSIT,
        currency="RUB",
        amount_rub=amount_rub,
        effective_at=effective_at,
        comment=comment or f"Депозит инвестора: {investor.name}",
        include_in_ledger=True,
        created_by=user,
    )
    txn = InvestorCapitalTransaction.objects.create(
        investor=investor,
        type=InvestorCapitalTransaction.TYPE_DEPOSIT,
        amount_rub=amount_rub,
        units_delta=units,
        unit_price=price,
        effective_at=effective_at,
        linked_ledger_adjustment=adj,
        comment=comment,
    )
    recompute_units(user, account)
    return txn


@transaction.atomic
def withdraw(investor, amount_rub, effective_at, account, user, comment=""):
    amount_rub = Decimal(amount_rub)
    if amount_rub <= 0:
        raise ValidationError("Сумма вывода должна быть положительной.")
    if not account:
        raise ValidationError("Нет активного аккаунта биржи для проводки вывода.")

    price = current_unit_price(user, account)
    units = amount_rub / price
    if units > investor.units + EPS:
        raise ValidationError(
            f"Нельзя вывести больше, чем у инвестора есть капитала "
            f"(доступно ~{(investor.units * price):.2f} ₽)."
        )

    adj = LedgerAdjustment.objects.create(
        exchange_account=account,
        account=LedgerAdjustment.ACCOUNT_BANK,
        type=LedgerAdjustment.TYPE_INVESTOR_WITHDRAWAL,
        currency="RUB",
        amount_rub=amount_rub,
        effective_at=effective_at,
        comment=comment or f"Вывод инвестора: {investor.name}",
        include_in_ledger=True,
        created_by=user,
    )
    txn = InvestorCapitalTransaction.objects.create(
        investor=investor,
        type=InvestorCapitalTransaction.TYPE_WITHDRAWAL,
        amount_rub=amount_rub,
        units_delta=-units,
        unit_price=price,
        effective_at=effective_at,
        linked_ledger_adjustment=adj,
        comment=comment,
    )
    recompute_units(user, account)
    return txn


# --------------------------------------------------------------------------- #
# Profit allocation
# --------------------------------------------------------------------------- #
def _day_shares(active, units, total_open):
    """Per-day profit-share percentage by mode. fixed/multiplier take their cut,
    same_as_capital splits the remainder by capital, none/split start at 0."""
    pct = {}
    fixed_total = Decimal("0")
    mult_total = Decimal("0")
    same = []
    cap = {}
    for inv in active:
        cap[inv.id] = (units[inv.id] / total_open * HUNDRED) if total_open > 0 else Decimal("0")
    for inv in active:
        m = inv.profit_share_mode
        if m == Investor.PROFIT_FIXED_PCT:
            p = inv.profit_share_fixed_pct or Decimal("0")
            pct[inv.id] = p
            fixed_total += p
        elif m == Investor.PROFIT_MULTIPLIER:
            p = cap[inv.id] * (inv.profit_share_multiplier or Decimal("0"))
            pct[inv.id] = p
            mult_total += p
        elif m in (Investor.PROFIT_NONE, Investor.PROFIT_SPLIT):
            pct[inv.id] = Decimal("0")
        else:
            same.append(inv)
    remainder = HUNDRED - fixed_total - mult_total
    same_cap = sum((cap[i.id] for i in same), Decimal("0"))
    for inv in same:
        pct[inv.id] = (remainder * cap[inv.id] / same_cap) if same_cap > 0 else Decimal("0")
    return pct, cap


def compute_allocation(user, period_from, period_to, account):
    """Day-by-day historical allocation. Each day's distributable profit
    (``DailySnapshot.daily_total_equity_pnl`` = equity Δ minus capital flows) is
    split using the ownership/state that existed *that day*, so a late investor
    never receives profit earned before their deposit. ``split_from_investor``
    transfers a share of the source's daily profit to the recipient."""
    from collections import defaultdict

    investors = list(Investor.objects.filter(user=user))
    active = [i for i in investors if i.is_active]

    snaps = list(
        DailySnapshot.objects.filter(
            exchange_account=account, day__gte=period_from, day__lte=period_to
        ).order_by("day")
    )
    txns = list(
        InvestorCapitalTransaction.objects.filter(
            investor__user=user, effective_at__date__lte=period_to
        ).order_by("effective_at", "id")
    )

    earned = defaultdict(lambda: Decimal("0"))
    total_profit = Decimal("0")
    units = defaultdict(lambda: Decimal("0"))
    ti = 0

    for snap in snaps:
        day = snap.day
        while ti < len(txns) and txns[ti].effective_at.date() < day:
            units[txns[ti].investor_id] += txns[ti].units_delta
            ti += 1
        total_open = sum(units.values(), Decimal("0"))
        day_profit = snap.daily_total_equity_pnl
        total_profit += day_profit

        pct, _cap = _day_shares(active, units, total_open)
        base = {inv.id: day_profit * pct[inv.id] / HUNDRED for inv in active}
        # split_from_investor: move a share of the source's daily profit
        for inv in active:
            if inv.profit_share_mode == Investor.PROFIT_SPLIT and inv.source_investor_id in base:
                transfer = base[inv.source_investor_id] * (inv.split_percent or Decimal("0")) / HUNDRED
                base[inv.id] += transfer
                base[inv.source_investor_id] -= transfer
        for k, v in base.items():
            earned[k] += v

    # Closing units / capital shares at period end.
    while ti < len(txns):
        units[txns[ti].investor_id] += txns[ti].units_delta
        ti += 1
    total_close = sum(units.values(), Decimal("0"))
    price_end = (equity_as_of(account, period_to) / total_close) if total_close > 0 else Decimal("1")

    rows = []
    for inv in active:
        e = q_rub(earned[inv.id])
        cap_pct = (units[inv.id] / total_close * HUNDRED) if total_close > 0 else Decimal("0")
        rows.append({
            "investor": inv,
            "closing_units": units[inv.id],
            "unit_price": price_end,
            "capital_value": q_rub(units[inv.id] * price_end),
            "capital_share_pct": cap_pct,
            "profit_share_pct": (e / total_profit * HUNDRED) if total_profit else Decimal("0"),
            "net_profit": e,
        })

    return {
        "profit": total_profit,
        "rows": rows,
        "allocated": sum((r["net_profit"] for r in rows), Decimal("0")),
        "period_from": period_from,
        "period_to": period_to,
    }


@transaction.atomic
def save_allocation(user, period_from, period_to, preview):
    """Persist & freeze a profit-allocation snapshot. Replaces only unsettled
    rows for the period; already paid/reinvested allocations stay frozen."""
    from datetime import timedelta

    SETTLED = (ProfitAllocation.STATUS_PAID_OUT, ProfitAllocation.STATUS_REINVESTED)
    # Re-savable rows are those not yet settled (retained / claim / legacy unpaid).
    ProfitAllocation.objects.filter(
        investor__user=user, period_from=period_from, period_to=period_to,
    ).exclude(status__in=SETTLED).delete()
    settled_ids = set(
        ProfitAllocation.objects.filter(
            investor__user=user, period_from=period_from, period_to=period_to,
            status__in=SETTLED,
        ).values_list("investor_id", flat=True)
    )

    created = []
    for r in preview["rows"]:
        inv = r["investor"]
        if inv.id in settled_ids:
            continue
        # Capital investors' profit is already in NAV → retained, not payable.
        # Split/fixed participants' profit is a real claim.
        status = (ProfitAllocation.STATUS_UNPAID_CLAIM if inv.profit_is_claim
                  else ProfitAllocation.STATUS_RETAINED)
        created.append(ProfitAllocation.objects.create(
            period_from=period_from, period_to=period_to, investor=inv,
            share_percent=r["profit_share_pct"],
            capital_share_pct=r["capital_share_pct"],
            profit_share_pct=r["profit_share_pct"],
            net_profit=r["net_profit"],
            status=status,
        ))

        opening = (
            inv.capital_transactions.filter(effective_at__date__lt=period_from)
            .aggregate(t=Sum("units_delta"))["t"] or Decimal("0")
        )
        cumulative = (
            inv.allocations.filter(period_to__lte=period_to)
            .aggregate(t=Sum("net_profit"))["t"] or Decimal("0")
        )
        InvestorPositionSnapshot.objects.update_or_create(
            investor=inv, period_from=period_from, period_to=period_to,
            defaults={
                "opening_units": opening,
                "closing_units": r["closing_units"],
                "unit_price": r["unit_price"],
                "capital_value_rub": r["capital_value"],
                "capital_share_pct": r["capital_share_pct"],
                "profit_share_pct": r["profit_share_pct"],
                "earned_profit_rub": r["net_profit"],
                "cumulative_earned_profit_rub": cumulative,
                # Only a payable claim is "unpaid"; retained NAV profit is not.
                "unpaid_profit_rub": r["net_profit"] if inv.profit_is_claim else Decimal("0"),
                "paid_out_profit_rub": Decimal("0"),
                "reinvested_profit_rub": Decimal("0"),
            },
        )
    return created


@transaction.atomic
def settle_allocation(allocation, status, account, user, effective_at=None):
    """Mark a CLAIM allocation as paid out or reinvested, posting side effects.
    Capital investors' retained (in-NAV) profit cannot be settled — it would mint
    fake units / pay out value already reflected in their unit price."""
    if allocation.status == ProfitAllocation.STATUS_RETAINED:
        raise ValidationError(
            "Это удержанная в капитале прибыль (уже в стоимости юнитов) — "
            "её нельзя выплатить или реинвестировать отдельно."
        )
    if allocation.status not in ProfitAllocation.CLAIM_STATUSES:
        raise ValidationError("Эта аллокация уже закрыта и заморожена.")
    effective_at = effective_at or timezone.now()
    amount = allocation.net_profit
    investor = allocation.investor
    txn = None

    if status == ProfitAllocation.STATUS_REINVESTED:
        price = current_unit_price(user, account)
        units = amount / price if price else Decimal("0")
        txn = InvestorCapitalTransaction.objects.create(
            investor=investor,
            type=InvestorCapitalTransaction.TYPE_PROFIT_REINVEST,
            amount_rub=amount,
            units_delta=units,
            unit_price=price,
            effective_at=effective_at,
            comment=f"Реинвест прибыли {allocation.period_from}–{allocation.period_to}",
        )
    elif status == ProfitAllocation.STATUS_PAID_OUT:
        adj = None
        if account:
            adj = LedgerAdjustment.objects.create(
                exchange_account=account,
                account=LedgerAdjustment.ACCOUNT_BANK,
                type=LedgerAdjustment.TYPE_INVESTOR_WITHDRAWAL,
                currency="RUB",
                amount_rub=amount,
                effective_at=effective_at,
                comment=f"Выплата прибыли: {investor.name}",
                include_in_ledger=True,
                created_by=user,
            )
        price = current_unit_price(user, account)
        txn = InvestorCapitalTransaction.objects.create(
            investor=investor,
            type=InvestorCapitalTransaction.TYPE_PROFIT_PAYOUT,
            amount_rub=amount,
            units_delta=Decimal("0"),  # payout does not change capital
            unit_price=price,
            effective_at=effective_at,
            linked_ledger_adjustment=adj,
            comment=f"Выплата прибыли {allocation.period_from}–{allocation.period_to}",
        )
    else:
        raise ValidationError("Недопустимый статус выплаты.")

    allocation.status = status
    allocation.settled_at = effective_at
    allocation.settlement_txn = txn
    allocation.save(update_fields=["status", "settled_at", "settlement_txn"])
    _sync_snapshot_settlement(allocation)
    recompute_units(user, account)
    return allocation


def _sync_snapshot_settlement(a):
    snap = InvestorPositionSnapshot.objects.filter(
        investor=a.investor, period_from=a.period_from, period_to=a.period_to
    ).first()
    if not snap:
        return
    paid = reinv = unpaid = Decimal("0")
    if a.status == ProfitAllocation.STATUS_PAID_OUT:
        paid = a.net_profit
    elif a.status == ProfitAllocation.STATUS_REINVESTED:
        reinv = a.net_profit
    else:
        unpaid = a.net_profit
    snap.paid_out_profit_rub = paid
    snap.reinvested_profit_rub = reinv
    snap.unpaid_profit_rub = unpaid
    snap.save(update_fields=["paid_out_profit_rub", "reinvested_profit_rub", "unpaid_profit_rub"])


@transaction.atomic
def settle_period(user, account, period_from, period_to, status, effective_at=None):
    """Batch-settle every unpaid allocation in a period at one consistent
    (same-day) unit price. Supports paid_out and reinvested."""
    if status not in (ProfitAllocation.STATUS_PAID_OUT, ProfitAllocation.STATUS_REINVESTED):
        raise ValidationError("Пакетно можно отметить только «выплачено» или «реинвестировано».")
    effective_at = effective_at or timezone.now()
    allocs = list(
        ProfitAllocation.objects.filter(
            investor__user=user, period_from=period_from, period_to=period_to,
            status__in=ProfitAllocation.CLAIM_STATUSES,
        ).select_related("investor")
    )
    if not allocs:
        raise ValidationError(
            "Нет невыплаченных требований за этот период "
            "(прибыль обычных инвесторов удержана в капитале и не выплачивается отдельно)."
        )

    price = current_unit_price(user, account)  # recompute re-derives day-open price
    for a in allocs:
        amount = a.net_profit
        txn = None
        if amount != 0 and status == ProfitAllocation.STATUS_REINVESTED:
            txn = InvestorCapitalTransaction.objects.create(
                investor=a.investor,
                type=InvestorCapitalTransaction.TYPE_PROFIT_REINVEST,
                amount_rub=amount, units_delta=(amount / price if price else Decimal("0")),
                unit_price=price, effective_at=effective_at,
                comment=f"Реинвест прибыли {period_from}–{period_to}",
            )
        elif amount != 0 and status == ProfitAllocation.STATUS_PAID_OUT:
            adj = None
            if account:
                adj = LedgerAdjustment.objects.create(
                    exchange_account=account, account=LedgerAdjustment.ACCOUNT_BANK,
                    type=LedgerAdjustment.TYPE_INVESTOR_WITHDRAWAL, currency="RUB",
                    amount_rub=amount, effective_at=effective_at,
                    comment=f"Выплата прибыли: {a.investor.name}",
                    include_in_ledger=True, created_by=user,
                )
            txn = InvestorCapitalTransaction.objects.create(
                investor=a.investor,
                type=InvestorCapitalTransaction.TYPE_PROFIT_PAYOUT,
                amount_rub=amount, units_delta=Decimal("0"), unit_price=price,
                effective_at=effective_at, linked_ledger_adjustment=adj,
                comment=f"Выплата прибыли {period_from}–{period_to}",
            )
        a.status = status
        a.settled_at = effective_at
        a.settlement_txn = txn
        a.save(update_fields=["status", "settled_at", "settlement_txn"])
        _sync_snapshot_settlement(a)

    recompute_units(user, account)
    return len(allocs)


# --------------------------------------------------------------------------- #
# EMERGENCY ONLY — legacy current-state seeding.
# --------------------------------------------------------------------------- #
@transaction.atomic
def emergency_seed_units_from_shares(user, account, force=False):
    """⚠️ EMERGENCY / TEST ONLY. Seeds units from the *current* equity and legacy
    ``share_percent``, dating everyone "now". This IGNORES real entry dates and
    will wrongly grant late investors a share of earlier profit. The correct flow
    is historical contribution linking (see `link_contribution`). Do not use in
    normal operation."""
    investors = list(Investor.objects.filter(user=user))
    if not investors:
        return 0

    existing = InvestorCapitalTransaction.objects.filter(investor__user=user)
    if existing.exists():
        if not force:
            return 0
        existing.delete()

    equity = portfolio_equity(account)
    share_total = sum((i.share_percent for i in investors if i.is_active), Decimal("0"))
    now = timezone.now()
    created = 0

    for inv in investors:
        if equity > 0 and share_total > 0:
            units = equity * (inv.share_percent / share_total)
            price = Decimal("1")
        else:
            units = inv.share_percent  # par fallback, keeps shares proportional
            price = Decimal("1")
        if units <= 0:
            continue
        InvestorCapitalTransaction.objects.create(
            investor=inv,
            type=InvestorCapitalTransaction.TYPE_CORRECTION,
            amount_rub=q_rub(units * price),
            units_delta=units,
            unit_price=price,
            effective_at=now,
            comment="Инициализация капитала из доли (миграция)",
        )
        created += 1
    return created
