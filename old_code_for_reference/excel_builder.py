import sqlite3
import pandas as pd
from collections import deque, defaultdict
from dataclasses import dataclass
from decimal import Decimal, getcontext, ROUND_HALF_UP, InvalidOperation
from typing import Dict, List, Any, Optional, Tuple

# ================= DECIMAL SETUP =================
getcontext().prec = 50

RUB = Decimal("0.01")
USDT = Decimal("0.000001")
PRICE = Decimal("0.00001")

def D(x) -> Decimal:
    return Decimal(str(x))

def q(x: Decimal, step: Decimal) -> Decimal:
    return x.quantize(step, rounding=ROUND_HALF_UP)

def dec_from_sql(v, step: Decimal) -> Decimal:
    """
    SQLite REAL/NUMERIC may be float (DB Browser rounds in UI).
    Convert to Decimal safely, then quantize to our canonical step.
    """
    if v is None:
        return Decimal("0")
    s = str(v).strip()
    if not s:
        return Decimal("0")
    try:
        return q(Decimal(s), step)
    except (InvalidOperation, ValueError):
        return q(D(v), step)

def to_float(x: Decimal, step: Decimal) -> float:
    """
    Excel stores numbers as IEEE float anyway; we ensure rounding BEFORE converting.
    This makes display/sums consistent with your accounting precision.
    """
    return float(q(x, step))

# ================= FIFO LOT =================
@dataclass
class Lot:
    qty: Decimal
    price: Decimal  # RUB per USDT


class TradingLedger:
    """
    SQLite -> Excel exporter with:
      - Orders_Display
      - Orders_Processed
      - Daily
      - Weekly
      - Adjustments (expanded)

    Adjustments:
      applied by DATE (YYYY-MM-DD) at START of each day,
      regardless of whether there are orders that day.

    UX:
      Daily/Weekly includes ONLY "active" trading days (days with at least 1 order).
      BUT: adjustments that fall on non-trade days are SHOWN on the NEXT trade day
      (they are still applied on their own day to balances).

    Money source (Variant A):
      RUB cashflow uses DB `amount` as truth (no qty*price reconstruction).
    """

    def __init__(self, db_path: str, adjustments: Optional[dict] = None):
        self.db_path = db_path
        self.adjustments = adjustments or {"bank": [], "exchange": []}
        self.adjustments_by_day = self._normalize_adjustments(self.adjustments)

        # state
        self.bank = Decimal("0")
        self.exchange = Decimal("0")

        # WAC
        self.wac_qty = Decimal("0")
        self.wac_cost = Decimal("0")  # RUB basis

        # FIFO
        self.fifo_lots = deque()

    # ---------- helpers ----------
    def _normalize_state(self):
        self.bank = q(self.bank, RUB)
        self.exchange = q(self.exchange, USDT)
        self.wac_qty = q(self.wac_qty, USDT)
        self.wac_cost = q(self.wac_cost, RUB)
        for lot in self.fifo_lots:
            lot.qty = q(lot.qty, USDT)
            lot.price = q(lot.price, PRICE)

    # ---------- adjustments ----------
    def _normalize_adjustments(self, adjustments: dict):
        by_day = defaultdict(list)
        for acc, items in adjustments.items():
            acc = str(acc).lower().strip()
            if acc not in ("bank", "exchange"):
                raise ValueError(f"Unknown adjustment account: {acc}")

            for a in items:
                if "date" not in a or "amount" not in a:
                    raise ValueError(f"Adjustment must include date+amount: {a}")

                day = str(a["date"])[:10]
                by_day[day].append({
                    "day": day,
                    "account": acc,  # 'bank' | 'exchange'
                    "amount": D(a["amount"]),
                    "comment": a.get("comment", "")
                })
        return by_day

    def _adjustment_sums_for_day(self, day: str) -> Tuple[Decimal, Decimal]:
        bank_sum = Decimal("0")
        exch_sum = Decimal("0")
        for a in self.adjustments_by_day.get(day, []):
            if a["account"] == "bank":
                bank_sum += a["amount"]
            else:
                exch_sum += a["amount"]
        return q(bank_sum, RUB), q(exch_sum, USDT)

    def apply_adjustments_for_day(self, day: str):
        """
        Apply all adjustments for this day at the START of the day.
        BANK amounts in RUB, EXCHANGE amounts in USDT.
        EXCHANGE adjustment affects holdings; WAC cost basis cost NOT changed (external basis).
        """
        for a in self.adjustments_by_day.get(day, []):
            amt = a["amount"]
            if a["account"] == "bank":
                self.bank = self.bank + amt
            else:  # exchange
                self.exchange = self.exchange + amt
                self.wac_qty = self.wac_qty + amt
                # wac_cost unchanged (external)
        self._normalize_state()

    # ---------- DB ----------
    def load_orders(self) -> pd.DataFrame:
        conn = sqlite3.connect(self.db_path)

        df = pd.read_sql("""
            SELECT
                id,
                dt_msk AS dt,
                side,
                quantity,
                price,
                amount,
                counterparty
            FROM orders
            ORDER BY dt_msk
        """, conn)

        conn.close()

        # datetime
        df["dt"] = pd.to_datetime(
            df["dt"].astype(str).str.strip(),
            format="mixed",
            errors="raise"
        )

        df["day"] = df["dt"].dt.strftime("%Y-%m-%d")
        iso = df["dt"].dt.isocalendar()
        df["week"] = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)

        df["side"] = df["side"].astype(str).str.upper().str.strip()

        # Canonical quantization at input
        df["quantity"] = df["quantity"].apply(lambda v: dec_from_sql(v, USDT))
        df["price"] = df["price"].apply(lambda v: dec_from_sql(v, PRICE))
        df["amount"] = df["amount"].apply(lambda v: dec_from_sql(v, RUB))

        return df

    # ---------- FIFO ----------
    def _fifo_sell(self, sell_qty: Decimal, sell_price: Decimal) -> Tuple[Decimal, Decimal]:
        """
        Returns (fifo_realized_rub, external_qty_usdt).
        external_qty: amount sold beyond available FIFO lots (does NOT crash).
        """
        realized = Decimal("0")
        remaining = sell_qty

        while remaining > 0 and self.fifo_lots:
            lot = self.fifo_lots[0]
            take = lot.qty if lot.qty <= remaining else remaining

            realized += take * (sell_price - lot.price)

            lot.qty = q(lot.qty - take, USDT)
            remaining = q(remaining - take, USDT)

            if lot.qty == 0:
                self.fifo_lots.popleft()

        external_qty = remaining if remaining > 0 else Decimal("0")
        return q(realized, RUB), q(external_qty, USDT)

    def _wac_price(self) -> Decimal:
        if self.wac_qty > 0:
            return q(self.wac_cost / self.wac_qty, PRICE)
        return Decimal("0")

    # ---------- main ----------
    def run(self):
        df = self.load_orders()

        # group orders by day
        orders_by_day = defaultdict(list)
        for row in df.itertuples(index=False):
            orders_by_day[row.day].append({
                "id": row.id,
                "dt": row.dt,
                "day": row.day,
                "week": row.week,
                "side": row.side,
                "quantity": row.quantity,
                "price": row.price,
                "amount": row.amount,          # IMPORTANT: used for cashflow
                "counterparty": row.counterparty,
            })

        # ACTIVE days for engine = days with orders OR adjustments
        active_days = sorted(set(orders_by_day.keys()) | set(self.adjustments_by_day.keys()))
        if not active_days:
            raise RuntimeError("No orders and no adjustments. Nothing to export.")

        # expanded adjustments sheet
        adj_rows = []
        for day in sorted(self.adjustments_by_day.keys()):
            for a in self.adjustments_by_day[day]:
                adj_rows.append({
                    "Day": a["day"],
                    "Account": a["account"].upper(),
                    "Amount": to_float(a["amount"], RUB if a["account"] == "bank" else USDT),
                    "Comment": a["comment"],
                })
        adjustments_df = pd.DataFrame(adj_rows)

        display_rows: List[Dict[str, Any]] = []
        processed_rows: List[Dict[str, Any]] = []

        # daily accumulator + EOD snapshot
        daily_acc = defaultdict(lambda: {
            "fifo_real": Decimal("0"),
            "wac_real": Decimal("0"),
            "vol_usdt": Decimal("0"),
            "vol_rub": Decimal("0"),
            "last_price": None,         # Decimal
            "bank_eod": None,           # Decimal
            "exch_eod": None,           # Decimal
            "wac_eod": None,            # Decimal
            "wac_qty_eod": None,        # Decimal
            "fifo_lots_copy": None,     # list[(qty, price)]
            "bank_adj_show": Decimal("0"),
            "exch_adj_show": Decimal("0"),
        })

        # pending adjustments to DISPLAY (if adjustment happens on a non-trade day)
        pending_bank_adj = Decimal("0")
        pending_exch_adj = Decimal("0")

        # running pnl state (cumulative realized)
        fifo_real_cum = Decimal("0")
        wac_real_cum = Decimal("0")

        for day in active_days:
            # capture adjustment sums for DISPLAY logic
            bank_adj_today, exch_adj_today = self._adjustment_sums_for_day(day)

            # START OF DAY: apply adjustments even if no orders
            self.apply_adjustments_for_day(day)

            # process all orders for this day
            for r in orders_by_day.get(day, []):
                side = r["side"]
                qty = q(r["quantity"], USDT)
                price = q(r["price"], PRICE)
                rub = q(r["amount"], RUB)   # VARIANT A: RUB truth from DB

                fifo_real = Decimal("0")
                wac_real = Decimal("0")
                external_qty = Decimal("0")

                bank_delta = Decimal("0")
                exch_delta = Decimal("0")

                if side == "BUY":
                    bank_delta = q(-rub, RUB)
                    exch_delta = qty

                    self.bank = self.bank + bank_delta
                    self.exchange = self.exchange + exch_delta

                    self.fifo_lots.append(Lot(qty=qty, price=price))

                    self.wac_qty = self.wac_qty + qty
                    self.wac_cost = self.wac_cost + rub

                elif side == "SELL":
                    bank_delta = q(rub, RUB)
                    exch_delta = q(-qty, USDT)

                    self.bank = self.bank + bank_delta
                    self.exchange = self.exchange + exch_delta

                    fifo_real, external_qty = self._fifo_sell(qty, price)

                    # WAC realized should follow RUB cashflow (amount), not idealized price*qty
                    wac_before = self._wac_price()
                    qty_from_pos = qty if qty <= self.wac_qty else self.wac_qty

                    if qty > 0 and qty_from_pos > 0:
                        rub_from_pos = q(rub * (qty_from_pos / qty), RUB)
                    else:
                        rub_from_pos = Decimal("0")

                    wac_real = q(rub_from_pos - (wac_before * qty_from_pos), RUB)

                    # reduce basis for covered portion
                    self.wac_qty = self.wac_qty - qty_from_pos
                    self.wac_cost = self.wac_cost - (wac_before * qty_from_pos)

                else:
                    raise ValueError(f"Unknown side={side}")

                self._normalize_state()
                wac_now = self._wac_price()

                # accumulate for day
                daily_acc[day]["fifo_real"] += fifo_real
                daily_acc[day]["wac_real"] += wac_real
                daily_acc[day]["vol_usdt"] += qty
                daily_acc[day]["vol_rub"] += rub
                daily_acc[day]["last_price"] = price

                # Orders_Display (numbers, not strings)
                display_rows.append({
                    "Order ID": r["id"],
                    "Date": r["dt"],
                    "Side": side,
                    "Quantity (USD)": to_float(qty, USDT),
                    "Price": to_float(price, PRICE),
                    "RUB": to_float(rub, RUB),
                    "Counterparty": r["counterparty"],
                })

                # Orders_Processed (numbers)
                processed_rows.append({
                    "id": r["id"],
                    "dt": r["dt"],
                    "day": day,
                    "week": r["week"],
                    "side": side,
                    "qty": to_float(qty, USDT),
                    "price": to_float(price, PRICE),
                    "rub": to_float(rub, RUB),
                    "bank_balance": to_float(self.bank, RUB),
                    "bank_delta": to_float(bank_delta, RUB),
                    "exchange_balance": to_float(self.exchange, USDT),
                    "exchange_delta": to_float(exch_delta, USDT),
                    "fifo_realized": to_float(fifo_real, RUB),
                    "wac_realized": to_float(wac_real, RUB),
                    # "external_qty": to_float(external_qty, USDT),
                    "wac": to_float(wac_now, PRICE),
                })

            # END OF DAY snapshot (always)
            self._normalize_state()
            daily_acc[day]["bank_eod"] = self.bank
            daily_acc[day]["exch_eod"] = self.exchange
            daily_acc[day]["wac_eod"] = q(self._wac_price(), PRICE)
            daily_acc[day]["wac_qty_eod"] = self.wac_qty
            daily_acc[day]["fifo_lots_copy"] = [(lot.qty, lot.price) for lot in self.fifo_lots]

            # DISPLAY adjustments logic:
            # If no trades today (last_price None) -> carry to pending for next trade day display.
            if daily_acc[day]["last_price"] is None:
                pending_bank_adj = q(pending_bank_adj + bank_adj_today, RUB)
                pending_exch_adj = q(pending_exch_adj + exch_adj_today, USDT)
            else:
                # trade day: show pending + today's adjustments
                daily_acc[day]["bank_adj_show"] = q(pending_bank_adj + bank_adj_today, RUB)
                daily_acc[day]["exch_adj_show"] = q(pending_exch_adj + exch_adj_today, USDT)
                pending_bank_adj = Decimal("0")
                pending_exch_adj = Decimal("0")

        orders_display_df = pd.DataFrame(display_rows)
        orders_processed_df = pd.DataFrame(processed_rows)

        # -------- Daily (only days with a real trade price) --------
        daily_rows = []
        for day in sorted(daily_acc.keys()):
            v = daily_acc[day]
            if v["last_price"] is None:
                continue

            last_price = q(v["last_price"], PRICE)

            # unrealized FIFO
            fifo_unreal = Decimal("0")
            for lot_qty, lot_price in (v["fifo_lots_copy"] or []):
                fifo_unreal += lot_qty * (last_price - lot_price)
            fifo_unreal = q(fifo_unreal, RUB)

            # unrealized WAC
            wac_unreal = q((last_price - q(v["wac_eod"], PRICE)) * q(v["wac_qty_eod"], USDT), RUB)

            # running pnl (cum realized + current unrealized)
            fifo_real_cum = q(fifo_real_cum + q(v["fifo_real"], RUB), RUB)
            wac_real_cum = q(wac_real_cum + q(v["wac_real"], RUB), RUB)

            running_fifo_pnl = q(fifo_real_cum + fifo_unreal, RUB)
            running_wac_pnl = q(wac_real_cum + wac_unreal, RUB)

            bank = q(v["bank_eod"], RUB)
            exch = q(v["exch_eod"], USDT)
            equity = q(bank + q(exch * last_price, RUB), RUB)

            daily_rows.append({
                "Day": day,
                "Bank Balance": to_float(bank, RUB),
                "Exchange Balance": to_float(exch, USDT),
                "Total Equity": to_float(equity, RUB),
                "FIFO Realized": to_float(q(v["fifo_real"], RUB), RUB),
                "FIFO Unrealized": to_float(fifo_unreal, RUB),
                "WAC Realized": to_float(q(v["wac_real"], RUB), RUB),
                "WAC Unrealized": to_float(wac_unreal, RUB),
                "Volume USDT": to_float(q(v["vol_usdt"], USDT), USDT),
                "Volume RUB": to_float(q(v["vol_rub"], RUB), RUB),
                "Running FIFO PnL": to_float(running_fifo_pnl, RUB),
                "Running WAC PnL": to_float(running_wac_pnl, RUB),
                "Bank Adjustment": to_float(q(v["bank_adj_show"], RUB), RUB),
                "Exchange Adjustment": to_float(q(v["exch_adj_show"], USDT), USDT),
                "Last Price": to_float(last_price, PRICE),
                "WAC": to_float(q(v["wac_eod"], PRICE), PRICE),
            })

        daily_df = pd.DataFrame(daily_rows)

        # считаем Daily PnL
        daily_adj_rub = (
            daily_df["Bank Adjustment"]
            + daily_df["Exchange Adjustment"] * daily_df["Last Price"]
        ).round(2)   # <-- КЛЮЧЕВО

        print(daily_adj_rub)
        print(daily_df["Exchange Adjustment"] * daily_df["Last Price"])

        daily_pnl = (
            daily_df["Total Equity"].round(2)
            - daily_df["Total Equity"].shift(1).round(2)
            - daily_adj_rub
        )

        daily_pnl = daily_pnl.round(2)
        daily_pnl.iloc[0] = 0.0

        pos = daily_df.columns.get_loc("Total Equity") + 1
        daily_df.insert(pos, "Daily PnL", daily_pnl)


        # -------- Weekly (aggregate from Daily rows, take EOW snapshot) --------
        weekly_bucket = defaultdict(lambda: {
            "fifo_real": Decimal("0"),
            "wac_real": Decimal("0"),
            "vol_usdt": Decimal("0"),
            "vol_rub": Decimal("0"),
            "bank_adj": Decimal("0"),
            "exch_adj": Decimal("0"),
            "bank_eow": None,
            "exch_eow": None,
            "wac_eow": None,
            "wac_qty_eow": None,
            "fifo_lots_copy": None,
            "last_price_eow": None,
            "running_fifo_pnl_eow": None,
            "running_wac_pnl_eow": None,
        })

        # Use daily_rows (already trade-only) to avoid phantom weeks
        for row in daily_rows:
            day = row["Day"]
            dt = pd.to_datetime(day)
            iso = dt.isocalendar()
            week = f"{iso.year}-W{int(iso.week):02d}"

            weekly_bucket[week]["fifo_real"] += D(row["FIFO Realized"])
            weekly_bucket[week]["wac_real"] += D(row["WAC Realized"])
            weekly_bucket[week]["vol_usdt"] += D(row["Volume USDT"])
            weekly_bucket[week]["vol_rub"] += D(row["Volume RUB"])
            weekly_bucket[week]["bank_adj"] += D(row["Bank Adjustment"])
            weekly_bucket[week]["exch_adj"] += D(row["Exchange Adjustment"])

            # EOW snapshot from internal state snapshot for that day
            snap = daily_acc[day]
            weekly_bucket[week]["bank_eow"] = snap["bank_eod"]
            weekly_bucket[week]["exch_eow"] = snap["exch_eod"]
            weekly_bucket[week]["wac_eow"] = snap["wac_eod"]
            weekly_bucket[week]["wac_qty_eow"] = snap["wac_qty_eod"]
            weekly_bucket[week]["fifo_lots_copy"] = snap["fifo_lots_copy"]
            weekly_bucket[week]["last_price_eow"] = snap["last_price"]

            # running pnl at end of week = last daily row’s running pnl
            weekly_bucket[week]["running_fifo_pnl_eow"] = D(row["Running FIFO PnL"])
            weekly_bucket[week]["running_wac_pnl_eow"] = D(row["Running WAC PnL"])

        weekly_rows = []
        for week in sorted(weekly_bucket.keys()):
            wb = weekly_bucket[week]
            if wb["last_price_eow"] is None:
                continue

            last_price = q(wb["last_price_eow"], PRICE)

            fifo_unreal = Decimal("0")
            for lot_qty, lot_price in (wb["fifo_lots_copy"] or []):
                fifo_unreal += lot_qty * (last_price - lot_price)
            fifo_unreal = q(fifo_unreal, RUB)

            wac_unreal = q((last_price - q(wb["wac_eow"], PRICE)) * q(wb["wac_qty_eow"], USDT), RUB)

            bank = q(wb["bank_eow"], RUB)
            exch = q(wb["exch_eow"], USDT)
            equity = q(bank + q(exch * last_price, RUB), RUB)

            weekly_rows.append({
                "Week": week,
                "Bank Balance": to_float(bank, RUB),
                "Exchange Balance": to_float(exch, USDT),
                "Total Equity": to_float(equity, RUB),
                "FIFO Realized": float(q(Decimal(str(wb["fifo_real"])), RUB)),
                "FIFO Unrealized": to_float(fifo_unreal, RUB),
                "WAC Realized": float(q(Decimal(str(wb["wac_real"])), RUB)),
                "WAC Unrealized": to_float(wac_unreal, RUB),
                "Volume USDT": float(q(Decimal(str(wb["vol_usdt"])), USDT)),
                "Volume RUB": float(q(Decimal(str(wb["vol_rub"])), RUB)),
                "Running FIFO PnL": float(q(Decimal(str(wb["running_fifo_pnl_eow"])), RUB)),
                "Running WAC PnL": float(q(Decimal(str(wb["running_wac_pnl_eow"])), RUB)),
                "Bank Adjustment": float(q(wb["bank_adj"], RUB)),
                "Exchange Adjustment": float(q(wb["exch_adj"], USDT)),
                "Last Price": to_float(last_price, PRICE),
                "WAC": to_float(q(wb["wac_eow"], PRICE), PRICE),
            })

        weekly_df = pd.DataFrame(weekly_rows)

        weekly_adj_rub = (
            weekly_df["Bank Adjustment"]
            + weekly_df["Exchange Adjustment"] * weekly_df["Last Price"]
        ).round(2)

        weekly_pnl = (
            weekly_df["Total Equity"].round(2)
            - weekly_df["Total Equity"].shift(1).round(2)
            - weekly_adj_rub
        )

        weekly_pnl = weekly_pnl.round(2)
        weekly_pnl.iloc[0] = 0.0

        pos = weekly_df.columns.get_loc("Total Equity") + 1
        weekly_df.insert(pos, "Weekly PnL", weekly_pnl)



        return (
            orders_display_df,
            orders_processed_df,
            daily_df,
            weekly_df,
            adjustments_df
        )
    def export_excel(self, path: str = "ledger.xlsx"):
        orders_display, orders_processed, daily, weekly, adj = self.run()

        with pd.ExcelWriter(path, engine="xlsxwriter", datetime_format="yyyy-mm-dd hh:mm:ss") as w:
            wb = w.book

            # --- FORMATS (РОВНО КАК ТЫ ПРОСИЛ) ---
            fmt_rub = wb.add_format({"num_format": "0.00"})
            fmt_usdt = wb.add_format({"num_format": "0.0000"})
            fmt_price = wb.add_format({"num_format": "0.00"})
            fmt_dt = wb.add_format({"num_format": "yyyy-mm-dd hh:mm:ss"})

            def calc_width(series: pd.Series, header: str) -> int:
                """
                Excel-accurate auto width:
                - header (bold + filter icon)
                - data (as displayed)
                """
                # header: bold + filter icon ≈ +4 chars
                header_len = len(str(header)) + 4

                def cell_len(x):
                    if pd.isna(x):
                        return 0

                    # numbers: as Excel shows them
                    if isinstance(x, (int, float)):
                        return len(f"{x:,.2f}")

                    return len(str(x))

                data_len = series.map(cell_len).max()

                # Excel columns are wider than char count → +2 safety
                return min(max(header_len, data_len) + 2, 60)




            def write_table(sheet_name: str, df: pd.DataFrame, style: str):
                # создаём лист сами (чтобы контролировать порядок)
                ws = wb.add_worksheet(sheet_name)
                w.sheets[sheet_name] = ws

                rows, cols = df.shape

                # ---------- HEADER (СТРОКА 0) ----------
                for c, name in enumerate(df.columns):
                    ws.write(0, c, name)

                # ---------- DATA (С 1-Й СТРОКИ) ----------
                df.to_excel(
                    w,
                    sheet_name=sheet_name,
                    index=False,
                    header=False,
                    startrow=1
                )

                # ---------- AUTO WIDTH ----------
                for i, col in enumerate(df.columns):
                    width = calc_width(df[col], col)
                    ws.set_column(i, i, width)

                # ---------- COLUMN FORMATS ----------
                col_idx = {name: i for i, name in enumerate(df.columns)}

                for name in ("date", "dt"):
                    if name in col_idx:
                        ws.set_column(col_idx[name], col_idx[name], None, fmt_dt)

                for name in (
                    "rub", "bank_delta", "fifo_realized", "wac_realized",
                    "bank_balance",
                    "FIFO Realized", "WAC Realized",
                    "Volume RUB",
                    "FIFO Unrealized", "WAC Unrealized",
                    "Running FIFO PnL", "Running WAC PnL",
                    "Total Equity",
                    "Bank Balance", "Bank Adjustment",
                ):
                    if name in col_idx:
                        ws.set_column(col_idx[name], col_idx[name], None, fmt_rub)

                for name in (
                    "qty", "exchange_delta", "external_qty",
                    "Exchange Balance", "Volume USDT", "Exchange Adjustment",
                ):
                    if name in col_idx:
                        ws.set_column(col_idx[name], col_idx[name], None, fmt_usdt)

                for name in ("price", "wac", "WAC", "Last Price"):
                    if name in col_idx:
                        ws.set_column(col_idx[name], col_idx[name], None, fmt_price)

                # ---------- EXCEL TABLE ----------
                # диапазон: header + все строки данных
                ws.add_table(
                    0, 0,
                    rows, cols - 1,
                    {
                        "style": style,
                        "columns": [{"header": c} for c in df.columns],
                    }
                )

                ws.freeze_panes(1, 0)

            # ---------- SHEETS ----------
            write_table("Orders_Display", orders_display, "Table Style Medium 2")
            write_table("Orders_Processed", orders_processed, "Table Style Medium 2")
            write_table("Daily", daily, "Table Style Medium 2")
            write_table("Weekly", weekly, "Table Style Medium 2")
            write_table("Adjustments", adj, "Table Style Medium 2")

        print(f"OK: {path} created")



# ================= Example usage =================
if __name__ == "__main__":
    adjustments = {
        "bank": [
            {"date": "2025-09-01", "amount": 100000, "comment": "initial deposit"},
            {"date": "2025-12-15", "amount": -16544.55, "comment": "ИП взносы"},
        ],
        "exchange": []
    }

    xlx = TradingLedger("orders.db", adjustments=adjustments)
    xlx.export_excel("ledger.xlsx")
    print("OK: ledger.xlsx created")
