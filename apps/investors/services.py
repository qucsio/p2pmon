"""Investor capital & profit accounting — economic-capital model (no units/NAV).

An investor's real balance is:

    economic_capital = external_capital + assigned_profit
    external_capital = deposits - withdrawals + corrections
    assigned_profit  = Σ over days of (period_pnl × participation_share × rule)

Participation share for a day is each investor's economic capital BEFORE that
day's PnL divided by the total economic capital. There are no fund units, no
unit price and no raw exposure in the accounting. ``split_from_investor`` is
display-only and never changes economic capital. Profit agreements are
versioned via ``InvestorProfitRule`` so changing them does not rewrite history.

``investor_report`` is the single source of truth.
"""
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.common.decimal_utils import q_rub
from apps.investors.models import (
    Investor,
    InvestorCapitalTransaction,
    InvestorProfitRule,
)
from apps.ledger.models import DailySnapshot, LedgerAdjustment

HUNDRED = Decimal("100")
EPS = Decimal("0.01")

# Capital events — the ONLY things that change external capital.
CAPITAL_EVENT_TYPES = (
    InvestorCapitalTransaction.TYPE_DEPOSIT,
    InvestorCapitalTransaction.TYPE_WITHDRAWAL,
    InvestorCapitalTransaction.TYPE_CORRECTION,
)

# Only genuine capital movements may be linked as investor contributions.
LINKABLE_ADJUSTMENT_TYPES = {
    LedgerAdjustment.TYPE_DEPOSIT,
    LedgerAdjustment.TYPE_WITHDRAWAL,
    LedgerAdjustment.TYPE_INVESTOR_DEPOSIT,
    LedgerAdjustment.TYPE_INVESTOR_WITHDRAWAL,
}


# --------------------------------------------------------------------------- #
# Equity / external capital
# --------------------------------------------------------------------------- #
def portfolio_equity(account) -> Decimal:
    if not account:
        return Decimal("0")
    snap = DailySnapshot.objects.filter(exchange_account=account).order_by("-day").first()
    return snap.total_equity if snap else Decimal("0")


def equity_as_of(account, day) -> Decimal:
    if not account:
        return Decimal("0")
    snap = (
        DailySnapshot.objects.filter(exchange_account=account, day__lte=day)
        .order_by("-day").first()
    )
    return snap.total_equity if snap else Decimal("0")


def _signed_capital_amount(txn) -> Decimal:
    if txn.type == InvestorCapitalTransaction.TYPE_DEPOSIT:
        return txn.amount_rub
    if txn.type == InvestorCapitalTransaction.TYPE_WITHDRAWAL:
        return -txn.amount_rub
    if txn.type == InvestorCapitalTransaction.TYPE_CORRECTION:
        return txn.amount_rub  # corrections store a signed amount
    return Decimal("0")


def net_external_capital(investor, when=None) -> Decimal:
    qs = investor.capital_transactions.filter(type__in=CAPITAL_EVENT_TYPES)
    if when is not None:
        qs = qs.filter(effective_at__date__lte=when)
    return sum((_signed_capital_amount(t) for t in qs), Decimal("0"))


# --------------------------------------------------------------------------- #
# Profit rules (versioned)
# --------------------------------------------------------------------------- #
def _rules_for_user(user):
    out = defaultdict(list)
    for r in InvestorProfitRule.objects.filter(investor__user=user).order_by("effective_from"):
        out[r.investor_id].append(r)
    return out


def _resolved_rule(inv, rules_map, day):
    """The agreement applicable to `inv` on `day` (latest matching rule, else the
    investor's current default fields)."""
    best = None
    for r in rules_map.get(inv.id, []):
        if r.effective_from <= day and (r.effective_to is None or day <= r.effective_to):
            if best is None or r.effective_from > best.effective_from:
                best = r
    if best is not None:
        return {
            "mode": best.mode, "mult": best.profit_share_multiplier,
            "fixed": best.profit_share_fixed_pct, "source_id": best.source_investor_id,
            "split": best.split_percent, "residual_id": best.residual_investor_id,
        }
    return {
        "mode": inv.profit_share_mode, "mult": inv.profit_share_multiplier,
        "fixed": inv.profit_share_fixed_pct, "source_id": inv.source_investor_id,
        "split": inv.split_percent, "residual_id": inv.residual_investor_id,
    }


def _coeff_display(rule):
    m = rule["mode"]
    if m == Investor.PROFIT_MULTIPLIER:
        return rule["mult"] or Decimal("0")
    if m == Investor.PROFIT_FIXED_PCT:
        return rule["fixed"] or Decimal("0")
    if m == Investor.PROFIT_SAME_AS_CAPITAL:
        return Decimal("1")
    return Decimal("0")


# --------------------------------------------------------------------------- #
# The engine — single source of truth
# --------------------------------------------------------------------------- #
def investor_report(user, account, period_from=None, period_to=None):
    snaps_qs = DailySnapshot.objects.filter(exchange_account=account)
    if period_from:
        snaps_qs = snaps_qs.filter(day__gte=period_from)
    if period_to:
        snaps_qs = snaps_qs.filter(day__lte=period_to)
    snaps = list(snaps_qs.order_by("day"))

    investors = list(Investor.objects.filter(user=user))
    active = [i for i in investors if i.is_active]
    by_id = {i.id: i for i in investors}
    rules_map = _rules_for_user(user)
    eff_to = period_to or (snaps[-1].day if snaps else timezone.localdate())

    txns = list(
        InvestorCapitalTransaction.objects.filter(
            investor__user=user, type__in=CAPITAL_EVENT_TYPES,
            effective_at__date__lte=eff_to,
        ).order_by("effective_at", "id")
    )

    ext = defaultdict(lambda: Decimal("0"))
    assigned = defaultdict(lambda: Decimal("0"))
    gross_total = defaultdict(lambda: Decimal("0"))
    residual_out = defaultdict(lambda: Decimal("0"))
    residual_in = defaultdict(lambda: Decimal("0"))
    displayed = defaultdict(lambda: Decimal("0"))
    unassigned_residual = Decimal("0")
    warnings = set()
    total_profit = Decimal("0")
    series_labels, econ_series, disp_series = [], defaultdict(list), defaultdict(list)
    ti = 0

    for snap in snaps:
        day = snap.day
        # Capital events effective on/before this day count toward participation.
        while ti < len(txns) and txns[ti].effective_at.date() <= day:
            t = txns[ti]
            ext[t.investor_id] += _signed_capital_amount(t)
            ti += 1

        P = snap.daily_total_equity_pnl
        total_profit += P
        resolved = {inv.id: _resolved_rule(inv, rules_map, day) for inv in active}

        # Fixed-pct claims are funded off the top, reducing the weight pool.
        F = Decimal("0")
        weight = []
        for inv in active:
            r = resolved[inv.id]
            if r["mode"] == Investor.PROFIT_FIXED_PCT:
                F += P * (r["fixed"] or Decimal("0")) / HUNDRED
            elif r["mode"] in (Investor.PROFIT_SAME_AS_CAPITAL,
                               Investor.PROFIT_MULTIPLIER, Investor.PROFIT_NONE):
                weight.append(inv)
        if F > P + EPS:
            warnings.add("fixed_pct превышает прибыль периода")
        R = P - F
        tw = sum((ext[inv.id] + assigned[inv.id] for inv in weight), Decimal("0"))

        gday = {inv.id: Decimal("0") for inv in active}
        for inv in weight:
            econ_before = ext[inv.id] + assigned[inv.id]
            share = (econ_before / tw) if tw > 0 else Decimal("0")
            gday[inv.id] = R * share
        for inv in active:
            if resolved[inv.id]["mode"] == Investor.PROFIT_FIXED_PCT:
                gday[inv.id] = P * (resolved[inv.id]["fixed"] or Decimal("0")) / HUNDRED

        aday = defaultdict(lambda: Decimal("0"))
        for inv in active:
            r = resolved[inv.id]
            m = r["mode"]
            g = gday[inv.id]
            gross_total[inv.id] += g
            if m == Investor.PROFIT_SAME_AS_CAPITAL:
                aday[inv.id] += g
            elif m == Investor.PROFIT_FIXED_PCT:
                aday[inv.id] += g
            elif m == Investor.PROFIT_MULTIPLIER:
                a = g * (r["mult"] or Decimal("0"))
                aday[inv.id] += a
                res = g - a
                _route_residual(res, r["residual_id"], inv, by_id, aday,
                                residual_out, residual_in, warnings)
            elif m == Investor.PROFIT_NONE:
                _route_residual(g, r["residual_id"], inv, by_id, aday,
                                residual_out, residual_in, warnings)
            # split → no economic assignment

        # If nobody can take the weight pool, it's unassigned (not silently lost).
        if tw <= 0 and R != 0:
            unassigned_residual += R

        for k, v in aday.items():
            assigned[k] += v

        dday = dict(aday)
        for inv in active:
            r = resolved[inv.id]
            if r["mode"] == Investor.PROFIT_SPLIT and r["source_id"] in by_id:
                base = aday.get(r["source_id"], Decimal("0"))
                tr = base * (r["split"] or Decimal("0")) / HUNDRED
                dday[inv.id] = dday.get(inv.id, Decimal("0")) + tr
                dday[r["source_id"]] = dday.get(r["source_id"], Decimal("0")) - tr
        for k, v in dday.items():
            displayed[k] += v

        series_labels.append(day.strftime("%d.%m.%Y"))
        for inv in active:
            econ_series[inv.id].append(round(float(ext[inv.id] + assigned[inv.id]), 2))
            disp_series[inv.id].append(round(float(displayed[inv.id]), 2))

    # Drain any capital events after the last snapshot (as-of completeness).
    while ti < len(txns):
        t = txns[ti]
        ext[t.investor_id] += _signed_capital_amount(t)
        ti += 1

    equity = snaps[-1].total_equity if snaps else portfolio_equity(account)
    rows = []
    for inv in active:
        rule_now = _resolved_rule(inv, rules_map, eff_to)
        rows.append({
            "investor": inv,
            "net_external_capital": q_rub(ext[inv.id]),
            "assigned_profit": q_rub(assigned[inv.id]),
            "economic_capital": q_rub(ext[inv.id] + assigned[inv.id]),
            "economic_pnl": q_rub(assigned[inv.id]),
            "gross_by_participation": q_rub(gross_total[inv.id]),
            "displayed_net": q_rub(displayed[inv.id]),
            "residual_out": q_rub(residual_out[inv.id]),
            "residual_in": q_rub(residual_in[inv.id]),
            "rule_mode": dict(Investor.PROFIT_MODE_CHOICES).get(rule_now["mode"], rule_now["mode"]),
            "profit_coeff": _coeff_display(rule_now),
            "source_investor": by_id.get(rule_now["source_id"]),
            "residual_investor": by_id.get(rule_now["residual_id"]),
        })

    return {
        "equity": equity, "profit": total_profit, "rows": rows,
        "unassigned_residual": q_rub(unassigned_residual), "warnings": sorted(warnings),
        "series_labels": series_labels,
        "econ_series": dict(econ_series), "disp_series": dict(disp_series),
        "period_from": snaps[0].day if snaps else period_from,
        "period_to": snaps[-1].day if snaps else period_to,
    }


def _route_residual(res, residual_id, inv, by_id, aday, residual_out, residual_in, warnings):
    if res == 0:
        return
    residual_out[inv.id] += res
    owner = by_id.get(residual_id)
    if owner and owner.is_active:
        aday[owner.id] += res
        residual_in[owner.id] += res
    else:
        warnings.add(f"нет получателя остатка: {inv.name}")
        # left unassigned at report level via reconcile (not credited to anyone)


def reconcile(user, account, period_from=None, period_to=None):
    """Sum of economic capital + unassigned residual should reconcile to equity.
    Remaining diff points to unlinked external flows / data issues."""
    rep = investor_report(user, account, period_from, period_to)
    sum_econ = sum((r["economic_capital"] for r in rep["rows"]), Decimal("0"))
    # residual routed to a missing owner was added to residual_out but not to anyone
    routed_lost = sum((r["residual_out"] for r in rep["rows"]), Decimal("0")) \
        - sum((r["residual_in"] for r in rep["rows"]), Decimal("0"))
    unassigned = rep["unassigned_residual"] + q_rub(routed_lost)
    ext_unlinked = sum((a.signed_amount_rub() for a in unassigned_external_flows(account)), Decimal("0"))
    diff = rep["equity"] - sum_econ - unassigned
    return {
        "equity": rep["equity"], "sum_economic": q_rub(sum_econ),
        "unassigned_residual": q_rub(unassigned),
        "external_unlinked": q_rub(ext_unlinked), "diff": q_rub(diff),
    }


def unassigned_external_flows(account):
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
# Capital events (no units)
# --------------------------------------------------------------------------- #
@transaction.atomic
def deposit(investor, amount_rub, effective_at, account, user, comment=""):
    amount_rub = Decimal(amount_rub)
    if amount_rub <= 0:
        raise ValidationError("Сумма депозита должна быть положительной.")
    if not account:
        raise ValidationError("Нет активного аккаунта биржи для проводки депозита.")
    adj = LedgerAdjustment.objects.create(
        exchange_account=account, account=LedgerAdjustment.ACCOUNT_BANK,
        type=LedgerAdjustment.TYPE_INVESTOR_DEPOSIT, currency="RUB",
        amount_rub=amount_rub, effective_at=effective_at,
        comment=comment or f"Депозит инвестора: {investor.name}",
        include_in_ledger=True, created_by=user)
    return InvestorCapitalTransaction.objects.create(
        investor=investor, type=InvestorCapitalTransaction.TYPE_DEPOSIT,
        amount_rub=amount_rub, effective_at=effective_at,
        linked_ledger_adjustment=adj, comment=comment)


@transaction.atomic
def withdraw(investor, amount_rub, effective_at, account, user, comment=""):
    amount_rub = Decimal(amount_rub)
    if amount_rub <= 0:
        raise ValidationError("Сумма вывода должна быть положительной.")
    if not account:
        raise ValidationError("Нет активного аккаунта биржи для проводки вывода.")
    rep = investor_report(user, account)
    row = next((r for r in rep["rows"] if r["investor"].id == investor.id), None)
    econ = row["economic_capital"] if row else Decimal("0")
    if econ <= 0:
        raise ValidationError("У инвестора нет экономического капитала для вывода.")
    if amount_rub > econ + EPS:
        raise ValidationError(
            f"Нельзя вывести больше экономического капитала (доступно ~{econ:.2f} ₽).")
    adj = LedgerAdjustment.objects.create(
        exchange_account=account, account=LedgerAdjustment.ACCOUNT_BANK,
        type=LedgerAdjustment.TYPE_INVESTOR_WITHDRAWAL, currency="RUB",
        amount_rub=amount_rub, effective_at=effective_at,
        comment=comment or f"Вывод инвестора: {investor.name}",
        include_in_ledger=True, created_by=user)
    return InvestorCapitalTransaction.objects.create(
        investor=investor, type=InvestorCapitalTransaction.TYPE_WITHDRAWAL,
        amount_rub=amount_rub, effective_at=effective_at,
        linked_ledger_adjustment=adj, comment=comment)


@transaction.atomic
def link_contribution(investor, adjustment, user, account):
    """Attach an existing bank LedgerAdjustment to an investor as a historical
    capital event (no new cash is created)."""
    if adjustment.investor_transactions.exists():
        raise ValidationError("Эта корректировка уже привязана к инвестору.")
    if adjustment.type not in LINKABLE_ADJUSTMENT_TYPES:
        raise ValidationError(
            "Этот тип корректировки нельзя привязывать как капитал инвестора "
            "(налоги и операционные корректировки исключены).")
    if adjustment.account != LedgerAdjustment.ACCOUNT_BANK:
        raise ValidationError("Привязывать можно только банковские (рублёвые) корректировки.")
    signed = adjustment.signed_amount_rub()
    if signed == 0:
        raise ValidationError("Корректировка не меняет банковский баланс в рублях.")
    is_deposit = signed > 0
    return InvestorCapitalTransaction.objects.create(
        investor=investor,
        type=(InvestorCapitalTransaction.TYPE_DEPOSIT if is_deposit
              else InvestorCapitalTransaction.TYPE_WITHDRAWAL),
        amount_rub=abs(signed), effective_at=adjustment.effective_at,
        linked_ledger_adjustment=adjustment,
        comment=f"Привязка истории: {adjustment.get_type_display()}")
