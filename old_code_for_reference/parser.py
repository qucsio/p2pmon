import sqlite3
from typing import Dict, List
import pandas as pd
import time
from datetime import datetime, timezone, timedelta


class P2POrderSync:
    additional = [{
            "id": "1978054220950458368",
            "side": "BUY",
            "amount": 49000.00,
            "price": 87.15,
            "quantity": 562.2489,
            "counterparty": "АЛЕКСЕЙ ВАСИЛЬЕВИЧ БЕДНИЧЕНКО",
            "ts_utc_ms": 1760450652000,
            "dt_msk": "2025-10-14 14:04:12"
        },
        # {
        #     "id": "2015714553195487232",
        #     "side": "SELL",
        #     "amount": 1000.00,
        #     "price": 81.39,
        #     "quantity": 12.2865,
        #     "counterparty": "HAROON AMIRI",
        #     "ts_utc_ms": 1769418775000,
        #     "dt_msk": "2026-01-26 12:12:55"
        # },
        # {
        #     "id": "2019344463927861248",
        #     "side": "SELL",
        #     "amount": 13500.00,
        #     "price": 82.58,
        #     "quantity": 163.4778,
        #     "counterparty": "ИРИНА НИКОЛАЕВНА ФРОЛОВА",
        #     "ts_utc_ms": 1770284213000,
        #     "dt_msk": "2026-02-05 12:36:53"
        # },
        ]

    def __init__(self, api, db_path: str = "orders.db"):
        self.api = api
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    # ================= DB =================

    def _init_db(self):
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            row_num INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT UNIQUE,
            side TEXT,
            amount REAL,
            price REAL,
            quantity REAL,
            counterparty TEXT,
            ts_utc_ms INTEGER,
            dt_msk TEXT
        )
        """)
        self.conn.commit()

    def db_count(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM orders"
        ).fetchone()[0]

    def has_id(self, order_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM orders WHERE id = ? LIMIT 1",
            (order_id,)
        ).fetchone() is not None

    def insert_order(self, o: Dict):
        self.conn.execute("""
        INSERT OR IGNORE INTO orders
        (id, side, amount, price, quantity, counterparty, ts_utc_ms, dt_msk)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            o["id"],
            o["side"],
            o["amount"],
            o["price"],
            o["quantity"],
            o["counterparty"],
            o["ts_utc_ms"],
            o["dt_msk"]
        ))
        self.conn.commit()

    # ================= PARSE =================

    def is_blacklisted(self, order):
        blacklist = ['2020269267843825664','1998471725041479680', '1970542931125129216', '1998471204625453056', '1998478331601448960', '1993746475777691648', '1998816289102204928', '1982843176577970176', '1996956449636954112', '1998471243288547328', '1996951519849897984', '1970545616818606080', '1998472024485052416', '1993864328876560384', '1970612247143227392', '1998471300293857280', '1973526384608407552', '1971590274079121408', '1970543749279023104', '1977565679565955072']
        if order["id"] in blacklist:
            # print(f"\nSKIPPING:{order=}")
            return True
        return False


    @staticmethod
    def _to_msk(ts_ms: int) -> tuple[int, str]:
        utc_dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        msk_dt = utc_dt# + timedelta(hours=3)
        return ts_ms, msk_dt.strftime("%Y-%m-%d %H:%M:%S")

    @classmethod
    def parse(cls, raw: Dict) -> Dict:
        side = "BUY" if raw["side"] == 0 else "SELL"

        counterparty = (
            raw.get("sellerRealName")
            if raw["side"] == 0
            else raw.get("buyerRealName")
        )

        ts_ms = int(raw["createDate"])
        ts_utc_ms, dt_msk = cls._to_msk(ts_ms)

        return {
            "id": raw["id"],
            "side": side,
            "amount": float(raw["amount"]),
            "price": float(raw["price"]),
            "quantity": float(raw["notifyTokenQuantity"]),
            "counterparty": counterparty,
            "ts_utc_ms": ts_utc_ms,
            "dt_msk": dt_msk
        }

    # ================= SYNC =================

    def sync(self, page_size: int = 30, status: int = 50) -> int:
        api_count = self._api_count(status)
        db_count_before = self.db_count()

        page = 1
        added = 0
        blacklisted = 0
        time.sleep(1)
        print("SYNCING...")
        while True:
            print(f"PARSING PAGE {page=}, {added=}")
            resp = self.api.get_orders(
                page=page,
                size=page_size,
                status=status
            )

            items = resp["result"]["items"]
            if not items:
                print(1)
                break

            for raw in items:
                if self.has_id(raw["id"]):
                    print(2)
                    return added, blacklisted

                if not self.is_blacklisted(raw):
                    print(raw["id"])
                    parsed = self.parse(raw)
                    self.insert_order(parsed)
                    added += 1
                else:
                    blacklisted += 1

            if len(items) < page_size:
                print(3)
                break


            page += 1
            time.sleep(1)

        for o in self.additional:
            self.insert_order(o)
        # safety check

        db_count_after = self.db_count()
        if db_count_after + blacklisted - len(self.additional) != api_count:
            print(
                "[WARN] Possible missing orders:",
                "api_count =", api_count,
                "db_count =", db_count_after
            )

        return added

    # ================= API =================

    def _api_count(self, status: int) -> int:
        resp = self.api.get_orders(page=0, size=1, status=status)
        return resp["result"]["count"]

    # ================= EXPORT =================

 
