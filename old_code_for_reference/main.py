from excel_builder import TradingLedger
from parser import P2POrderSync
from bybit_p2p import P2P

api = P2P(
    testnet=False,
    api_key="JpmQHTXqX4X1pxGdqK",
    api_secret="JJS64e7eDvINRtLfPEBjQZpwd6gikxJqgsUM"
)
# print(api.get_current_balance(accountType="FUND"))


syncer = P2POrderSync(api)

added = syncer.sync()
print("Added:", added)
adjustments = {
    "bank": [
        {"date": "2025-09-01", "amount": 100000, "comment": "initial deposit"},
        {"date": "2025-12-15", "amount": -16544.55, "comment": "ИП взносы"},
        {"date": "2026-01-06", "amount": 40000, "comment": "Депозит"},
        {"date": "2026-02-26", "amount": 30000, "comment": "Депозит"},
        {"date": "2026-04-16", "amount": 65000, "comment": "Депозит"},
        {"date": "2026-04-27", "amount": -6533, "comment": "НДФЛ"},
    ],
    "exchange": [
        {"date": "2025-10-31", "amount": 23.54, "comment": "+correction"},
    ]
}
xlx = TradingLedger("orders.db", adjustments)


xlx.export_excel()
