"""Offline proof for the ePay (Boost) Daily Transaction Detail ingest (commcalc/epay_ingest.py).
Pure-function coverage: fee classification, UserName->store suggestion, parse, terminal resolution, and
the per-store-day PAYMENT vs FEE aggregation the recon consumes. No DB, no file.

Run: `python3 harness_epay_ingest.py` from backend/.
"""
import sys
sys.path.insert(0, ".")

import app.modules.commcalc.epay_ingest as E  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


# ── fee classification ─────────────────────────────────────────────────────────────────────────────
check("fee line: '… FEE' title is a fee", E.is_fee_line("Boost RTR $5 - $300 FEE") is True)
check("payment line: RTR PayGo is not a fee", E.is_fee_line("Boost RTR PayGo $5-$300*") is False)
check("payment line: New Account Replen is not a fee", E.is_fee_line("Boost New Account Replenishment $5-$300") is False)

# ── UserName -> store suggestion (best-effort hint) ─────────────────────────────────────────────────
check("suggest: 418Uniondale -> 418", E.suggest_store_from_username("418Uniondale") == "418")
check("suggest: Epay652 -> 652 (strips the Epay label)", E.suggest_store_from_username("Epay652") == "652")
check("suggest: 6149-epay -> 6149", E.suggest_store_from_username("6149-epay") == "6149")
check("suggest: 2509bl -> 2509", E.suggest_store_from_username("2509bl") == "2509")
check("suggest: 117Burnside -> 117", E.suggest_store_from_username("117Burnside") == "117")
check("suggest: 3PL (address-style) -> '' (no clean number)", E.suggest_store_from_username("3PL") == "")
check("suggest: 1s60th-epay (address-style) -> ''", E.suggest_store_from_username("1s60th-epay") == "")

# ── parse (real sample shape) ────────────────────────────────────────────────────────────────────
records = [
    {"TransactionID": "1004470333", "TransactionSourceID": "1", "InvoiceID": "67020639",
     "SettlementDate": "2026-08-18 00:00:00", "TerminalID": "633423", "UserName": "418Uniondale",
     "Product": "BSTRTRSR2", "ProductTitle": "Boost RTR PayGo $5-$300*", "Type": "Sold", "Retail": "95"},
    {"TransactionID": "1004470333", "TransactionSourceID": "12", "InvoiceID": "67020639",
     "SettlementDate": "2026-08-18 00:00:00", "TerminalID": "633423", "UserName": "418Uniondale",
     "Product": "BSTRTRFEE4", "ProductTitle": "Boost RTR $5 - $300 FEE", "Type": "Sold", "Retail": "4"},
    {"TransactionID": "1004470391", "TransactionSourceID": "1", "SettlementDate": "2026-08-19 00:00:00",
     "TerminalID": "648757", "UserName": "117Burnside", "ProductTitle": "Boost New Account Replen",
     "Type": "Sold", "Retail": "34.06"},
    {"TransactionID": "", "TerminalID": "633423", "Retail": "999"},   # no TransactionID -> skipped
]
rows = E.parse_records(records, source_batch="b1")
check("parse: skips a row with no TransactionID", len(rows) == 3, len(rows))
check("parse: SettlementDate normalized to YYYY-MM-DD", rows[0]["settlement_date"] == "2026-08-18", rows[0])
check("parse: retail coerced to float", rows[0]["retail"] == 95.0 and rows[2]["retail"] == 34.06)
check("parse: fee line flagged is_fee", rows[1]["is_fee"] is True and rows[0]["is_fee"] is False)
check("parse: source_batch carried", rows[0]["source_batch"] == "b1")

# ── terminal resolution via the merchant map ─────────────────────────────────────────────────────
tmap = {"633423": "418"}   # 648757 intentionally unmapped
unresolved = E.resolve_stores(rows, tmap)
check("resolve: mapped terminal gets its store", rows[0]["store_code"] == "418")
check("resolve: unmapped terminal has no store", rows[2]["store_code"] is None)
check("resolve: unresolved set names the unmapped terminal", unresolved == {"648757"}, unresolved)

# ── per-store-day aggregation: PAYMENT excludes fee, FEE is fee lines only ─────────────────────────
agg = E.aggregate_store_day(rows)   # 648757 rows excluded (no store)
check("aggregate: only resolved store-days appear", list(agg.keys()) == [("418", "2026-08-18")], list(agg.keys()))
sd = agg[("418", "2026-08-18")]
check("aggregate: payment = main line only (95), fee excluded from payment", sd["payment"] == 95.0, sd)
check("aggregate: fee = the fee line only (4)", sd["fee"] == 4.0, sd)
check("aggregate: line count includes both", sd["lines"] == 2, sd)

# a fuller check: two payment lines + one fee at one store-day
rows2 = E.parse_records([
    {"TransactionID": "A", "TransactionSourceID": "1", "SettlementDate": "2026-08-18", "TerminalID": "T",
     "ProductTitle": "Boost RTR PayGo", "Retail": "100"},
    {"TransactionID": "B", "TransactionSourceID": "1", "SettlementDate": "2026-08-18", "TerminalID": "T",
     "ProductTitle": "Boost New Account Replen", "Retail": "50"},
    {"TransactionID": "A", "TransactionSourceID": "12", "SettlementDate": "2026-08-18", "TerminalID": "T",
     "ProductTitle": "Boost RTR FEE", "Retail": "4"},
])
E.resolve_stores(rows2, {"T": "S1"})
sd2 = E.aggregate_store_day(rows2)[("S1", "2026-08-18")]
check("aggregate: payments sum (100+50=150)", sd2["payment"] == 150.0, sd2)
check("aggregate: fee stays separate (4)", sd2["fee"] == 4.0, sd2)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED: " + f)
    sys.exit(1)
