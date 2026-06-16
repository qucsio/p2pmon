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
    ProfitAllocation,
)
from apps.ledger.models import DailySnapshot, LedgerAdjustment

HUNDRED = Decimal("100")
EPS = Decimal("0.01")


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


@transaction.atomic
def recompute_units(user, account):
    """Recompute unit_price and units_delta of every capital transaction in
    chronological order. Keeps amount_rub fixed; makes results order-independent
    so history can be entered/linked in any order."""
    txns = list(
        InvestorCapitalTransaction.objects.filter(investor__user=user)
        .order_by("effective_at", "id")
    )
    running = Decimal("0")
    for t in txns:
        if t.type == InvestorCapitalTransaction.TYPE_PROFIT_PAYOUT:
            t.units_delta = Decimal("0")
            t.unit_price = (
                contribution_unit_price(user, account, t.effective_at, running)
            )
            t.save(update_fields=["units_delta", "unit_price"])
            continue
        if t.type == InvestorCapitalTransaction.TYPE_CORRECTION:
            running += t.units_delta  # legacy/manual corrections kept as-is
            continue
        price = contribution_unit_price(user, account, t.effective_at, running)
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
def compute_allocation(user, period_from, period_to, account):
    """Return a preview of the profit split for the period (not persisted)."""
    snaps = DailySnapshot.objects.filter(
        exchange_account=account, day__gte=period_from, day__lte=period_to
    )
    gross = snaps.aggregate(t=Sum("gross_realized_pnl"))["t"] or Decimal("0")
    fees = snaps.aggregate(t=Sum("fees"))["t"] or Decimal("0")
    tax = snaps.aggregate(t=Sum("tax_accrual"))["t"] or Decimal("0")
    net = snaps.aggregate(t=Sum("net_profit_after_tax"))["t"] or Decimal("0")

    investors = list(Investor.objects.filter(user=user, is_active=True))
    # Capital share as of the END of the period — so historical periods use the
    # ownership that actually applied then, not today's.
    period_end_dt = timezone.make_aware(datetime.combine(period_to, time.max))
    caps = {inv.id: capital_share_pct(inv, user, when=period_end_dt) for inv in investors}

    pct = {}
    fixed_total = Decimal("0")
    mult_total = Decimal("0")
    same_group = []

    for inv in investors:
        if inv.profit_share_mode == Investor.PROFIT_FIXED_PCT:
            p = inv.profit_share_fixed_pct or Decimal("0")
            pct[inv.id] = p
            fixed_total += p
        elif inv.profit_share_mode == Investor.PROFIT_MULTIPLIER:
            p = caps[inv.id] * (inv.profit_share_multiplier or Decimal("0"))
            pct[inv.id] = p
            mult_total += p
        elif inv.profit_share_mode == Investor.PROFIT_NONE:
            pct[inv.id] = Decimal("0")
        else:  # same_as_capital — resolved after the fixed/multiplier pools
            same_group.append(inv)

    if fixed_total + mult_total > HUNDRED + EPS:
        raise ValidationError(
            f"Фиксированные и множительные доли превышают 100% "
            f"({(fixed_total + mult_total):.2f}%). Уменьшите их."
        )

    remainder = HUNDRED - fixed_total - mult_total
    same_cap_total = sum((caps[i.id] for i in same_group), Decimal("0"))
    for inv in same_group:
        pct[inv.id] = (
            remainder * caps[inv.id] / same_cap_total
            if same_cap_total > 0
            else Decimal("0")
        )

    rows = []
    allocated_pct = Decimal("0")
    for inv in investors:
        p = pct[inv.id]
        allocated_pct += p
        frac = p / HUNDRED
        rows.append({
            "investor": inv,
            "capital_share_pct": caps[inv.id],
            "profit_share_pct": p,
            "gross_profit": q_rub(gross * frac),
            "fees_part": q_rub(fees * frac),
            "tax_part": q_rub(tax * frac),
            "net_profit": q_rub(net * frac),
        })

    return {
        "gross": gross, "fees": fees, "tax": tax, "net": net,
        "rows": rows,
        "allocated_pct": allocated_pct,
        "leftover_pct": HUNDRED - allocated_pct,
    }


@transaction.atomic
def save_allocation(user, period_from, period_to, preview):
    """Persist a profit-allocation snapshot. Replaces only unsettled rows for
    the period, leaving already paid/reinvested allocations frozen."""
    ProfitAllocation.objects.filter(
        investor__user=user,
        period_from=period_from,
        period_to=period_to,
        status=ProfitAllocation.STATUS_UNPAID,
    ).delete()

    # Investors already settled for this period stay frozen — don't duplicate them.
    settled_ids = set(
        ProfitAllocation.objects.filter(
            investor__user=user, period_from=period_from, period_to=period_to
        )
        .exclude(status=ProfitAllocation.STATUS_UNPAID)
        .values_list("investor_id", flat=True)
    )

    created = []
    for r in preview["rows"]:
        if r["investor"].id in settled_ids:
            continue
        created.append(ProfitAllocation.objects.create(
            period_from=period_from,
            period_to=period_to,
            investor=r["investor"],
            share_percent=r["profit_share_pct"],
            capital_share_pct=r["capital_share_pct"],
            profit_share_pct=r["profit_share_pct"],
            gross_profit=r["gross_profit"],
            fees_part=r["fees_part"],
            tax_part=r["tax_part"],
            net_profit=r["net_profit"],
        ))
    return created


@transaction.atomic
def settle_allocation(allocation, status, account, user, effective_at=None):
    """Mark an allocation as paid out or reinvested, posting the side effects."""
    if allocation.status != ProfitAllocation.STATUS_UNPAID:
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
    recompute_units(user, account)
    return allocation


# --------------------------------------------------------------------------- #
# Initialization (units from legacy share_percent)
# --------------------------------------------------------------------------- #
@transaction.atomic
def initialize_units(user, account, force=False):
    """Create initial capital units from legacy ``share_percent`` using current
    portfolio equity as the baseline. Idempotent unless ``force``."""
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
