"""Investor capital & profit accounting.

Two separate concepts:

* TECHNICAL participation — fund units. ``units`` change ONLY on real capital
  events (deposit/withdrawal/correction); profit never creates units.
  ``raw_exposure_value = units / total_units * equity`` is technical only and is
  NOT the investor's money for non-``same_as_capital`` modes.

* ECONOMIC capital — what the investor actually owns:
  ``economic_capital = net_external_capital + cumulative_assigned_profit``.
  Assigned profit applies the investor's profit coefficient to their gross
  (weight-based) profit per automatic interval; the un-assigned residual is
  routed to ``residual_investor`` (or flagged as unassigned).

``split_from_investor`` is reporting-only: it moves *displayed* profit between
investors without touching units, NAV, or economic capital. See
``investor_report`` for the single source of truth.
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

# Units (capital ownership) change ONLY on these events. Profit never creates units.
CAPITAL_EVENT_TYPES = (
    InvestorCapitalTransaction.TYPE_DEPOSIT,
    InvestorCapitalTransaction.TYPE_WITHDRAWAL,
    InvestorCapitalTransaction.TYPE_CORRECTION,
)

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
        InvestorCapitalTransaction.objects.filter(
            investor__user=user, type__in=CAPITAL_EVENT_TYPES
        ).aggregate(t=Sum("units_delta"))["t"]
        or Decimal("0")
    )


def units_as_of(user, when) -> Decimal:
    return (
        InvestorCapitalTransaction.objects.filter(
            investor__user=user, type__in=CAPITAL_EVENT_TYPES, effective_at__lte=when
        ).aggregate(t=Sum("units_delta"))["t"]
        or Decimal("0")
    )


def investor_units_as_of(investor, when) -> Decimal:
    return (
        investor.capital_transactions.filter(
            type__in=CAPITAL_EVENT_TYPES, effective_at__lte=when
        ).aggregate(t=Sum("units_delta"))["t"]
        or Decimal("0")
    )


def _signed_capital_amount(txn) -> Decimal:
    """External cash effect of a capital transaction (deposit +, withdrawal -,
    correction signed by its units)."""
    if txn.type == InvestorCapitalTransaction.TYPE_DEPOSIT:
        return txn.amount_rub
    if txn.type == InvestorCapitalTransaction.TYPE_WITHDRAWAL:
        return -txn.amount_rub
    if txn.type == InvestorCapitalTransaction.TYPE_CORRECTION:
        return txn.amount_rub if txn.units_delta >= 0 else -txn.amount_rub
    return Decimal("0")


def net_external_capital(investor) -> Decimal:
    return sum(
        (_signed_capital_amount(t)
         for t in investor.capital_transactions.filter(type__in=CAPITAL_EVENT_TYPES)),
        Decimal("0"),
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
        # Profit transactions (legacy reinvest/payout) never affect units.
        if t.type not in CAPITAL_EVENT_TYPES:
            if t.units_delta != 0:
                t.units_delta = Decimal("0")
                t.save(update_fields=["units_delta"])
            continue
        d = t.effective_at.date()
        if d != cur_day:
            cur_day = d
            day_open_units = running  # frozen for the whole day
        price = day_open_unit_price(account, d, day_open_units)
        if t.type == InvestorCapitalTransaction.TYPE_CORRECTION:
            t.unit_price = price or t.unit_price
            t.save(update_fields=["unit_price"])
            running += t.units_delta  # corrections keep their explicit units
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
def _profit_coeff_display(inv):
    if inv.profit_share_mode == Investor.PROFIT_MULTIPLIER:
        return inv.profit_share_multiplier or Decimal("0")
    if inv.profit_share_mode == Investor.PROFIT_FIXED_PCT:
        return inv.profit_share_fixed_pct or Decimal("0")
    if inv.profit_share_mode == Investor.PROFIT_SAME_AS_CAPITAL:
        return Decimal("1")
    return Decimal("0")


def investor_report(user, account, period_from=None, period_to=None):
    """Single source of truth. Walks daily snapshots over automatic intervals
    (capital events) and computes, per investor:

      * TECHNICAL: units, technical_share, raw_exposure_value (= units/total*equity),
        gross_by_weight — raw participation, NOT money the investor owns.
      * ECONOMIC: assigned_profit (gross adjusted by profit coefficient, with the
        residual routed to residual_investor), economic_capital =
        net_external_capital + assigned_profit, economic_pnl = assigned_profit.
      * DISPLAYED: displayed_net — economic assigned adjusted by reporting-only
        split_from_investor transfers (does not affect economic capital).

    Profit never creates units; units change only on capital events.
    """
    from collections import defaultdict

    snaps_qs = DailySnapshot.objects.filter(exchange_account=account)
    if period_from:
        snaps_qs = snaps_qs.filter(day__gte=period_from)
    if period_to:
        snaps_qs = snaps_qs.filter(day__lte=period_to)
    snaps = list(snaps_qs.order_by("day"))

    investors = list(Investor.objects.filter(user=user))
    active = [i for i in investors if i.is_active]
    by_id = {i.id: i for i in investors}

    eff_to = period_to or (snaps[-1].day if snaps else None)
    txns = []
    if eff_to is not None:
        txns = list(
            InvestorCapitalTransaction.objects.filter(
                investor__user=user, type__in=CAPITAL_EVENT_TYPES,
                effective_at__date__lte=eff_to,
            ).order_by("effective_at", "id")
        )

    units = defaultdict(lambda: Decimal("0"))
    ext = defaultdict(lambda: Decimal("0"))          # net external capital as-of
    gross = defaultdict(lambda: Decimal("0"))
    assigned = defaultdict(lambda: Decimal("0"))     # economic
    residual_out = defaultdict(lambda: Decimal("0"))
    residual_in = defaultdict(lambda: Decimal("0"))
    displayed = defaultdict(lambda: Decimal("0"))
    unassigned_residual = Decimal("0")
    warnings = set()
    total_profit = Decimal("0")
    series_labels = []
    econ_series = defaultdict(list)
    disp_series = defaultdict(list)
    ti = 0

    for snap in snaps:
        day = snap.day
        while ti < len(txns) and txns[ti].effective_at.date() <= day:
            t = txns[ti]
            units[t.investor_id] += t.units_delta
            ext[t.investor_id] += _signed_capital_amount(t)
            ti += 1
        total = sum(units.values(), Decimal("0"))
        P = snap.daily_total_equity_pnl
        total_profit += P

        gday = {}
        for inv in active:
            w = (units[inv.id] / total) if total > 0 else Decimal("0")
            gday[inv.id] = P * w
            gross[inv.id] += gday[inv.id]

        aday = defaultdict(lambda: Decimal("0"))
        for inv in active:
            g = gday[inv.id]
            m = inv.profit_share_mode
            if m == Investor.PROFIT_SAME_AS_CAPITAL:
                aday[inv.id] += g
            elif m == Investor.PROFIT_MULTIPLIER:
                a = g * (inv.profit_share_multiplier or Decimal("0"))
                aday[inv.id] += a
                res = g - a
                if res != 0:
                    owner = inv.residual_investor
                    if owner and owner.is_active:
                        aday[owner.id] += res
                        residual_in[owner.id] += res
                    else:
                        unassigned_residual += res
                        warnings.add(inv.name)
                    residual_out[inv.id] += res
            elif m == Investor.PROFIT_NONE:
                res = g
                if res != 0:
                    owner = inv.residual_investor
                    if owner and owner.is_active:
                        aday[owner.id] += res
                        residual_in[owner.id] += res
                    else:
                        unassigned_residual += res
                        warnings.add(inv.name)
                    residual_out[inv.id] += res
            elif m == Investor.PROFIT_FIXED_PCT:
                aday[inv.id] += P * (inv.profit_share_fixed_pct or Decimal("0")) / HUNDRED
            # split_from_investor → no economic assignment (display only)

        for k, v in aday.items():
            assigned[k] += v

        dday = dict(aday)
        for inv in active:
            if inv.profit_share_mode == Investor.PROFIT_SPLIT and inv.source_investor_id in by_id:
                base = aday.get(inv.source_investor_id, Decimal("0"))
                tr = base * (inv.split_percent or Decimal("0")) / HUNDRED
                dday[inv.id] = dday.get(inv.id, Decimal("0")) + tr
                dday[inv.source_investor_id] = dday.get(inv.source_investor_id, Decimal("0")) - tr
        for k, v in dday.items():
            displayed[k] += v

        series_labels.append(day.strftime("%d.%m.%Y"))
        for inv in active:
            econ_series[inv.id].append(round(float(ext[inv.id] + assigned[inv.id]), 2))
            disp_series[inv.id].append(round(float(displayed[inv.id]), 2))

    equity = snaps[-1].total_equity if snaps else portfolio_equity(account)
    tu = sum(units.values(), Decimal("0"))
    unit_price = (equity / tu) if tu > 0 else Decimal("1")

    rows = []
    for inv in active:
        u = units[inv.id]
        raw = (u / tu * equity) if tu > 0 else Decimal("0")
        rows.append({
            "investor": inv,
            "units": u,
            "technical_share": (u / tu * HUNDRED) if tu > 0 else Decimal("0"),
            "raw_exposure_value": q_rub(raw),
            "gross_by_weight": q_rub(gross[inv.id]),
            "profit_coeff": _profit_coeff_display(inv),
            "assigned_profit": q_rub(assigned[inv.id]),
            "residual_out": q_rub(residual_out[inv.id]),
            "residual_in": q_rub(residual_in[inv.id]),
            "displayed_net": q_rub(displayed[inv.id]),
            "net_external_capital": q_rub(ext[inv.id]),
            "economic_capital": q_rub(ext[inv.id] + assigned[inv.id]),
            "economic_pnl": q_rub(assigned[inv.id]),
            "source_investor": inv.source_investor if inv.profit_share_mode == Investor.PROFIT_SPLIT else None,
        })

    return {
        "equity": equity, "total_units": tu, "unit_price": unit_price,
        "profit": total_profit, "rows": rows,
        "unassigned_residual": q_rub(unassigned_residual),
        "warnings": sorted(warnings),
        "series_labels": series_labels,
        "econ_series": dict(econ_series),
        "disp_series": dict(disp_series),
        "period_from": snaps[0].day if snaps else period_from,
        "period_to": snaps[-1].day if snaps else period_to,
    }


def unassigned_external_flows(account):
    """External capital/expense movements in the ledger not linked to any investor
    (e.g. an unexplained equity drop). Tax payments are excluded."""
    out = []
    for adj in (
        account.adjustments.filter(is_deleted=False)
        .exclude(type=LedgerAdjustment.TYPE_TAX_PAYMENT)
        .prefetch_related("investor_transactions")
        .order_by("-effective_at")
    ):
        if adj.investor_transactions.exists():
            continue
        if adj.signed_amount_rub() == 0 and adj.signed_amount_usdt() == 0:
            continue
        out.append(adj)
    return out


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
