"""Offline proof for the ePay auto-sweep → Daily Transaction Detail (DTD) leg (epay_sweep.py).

Proves, with NO live portal and NO real DB:
  1. label-based report-id resolution picks the right Commissions-menu row from a fake [{id,label}] set
     (case-insensitive, substring), and returns None when nothing matches.
  2. the REPORTS registry wires DTD as report_id=None + label_match + an epay_ingest `ingest` hook, and
     _expand_jobs expands it into a trailing day-range job (refresh_days).
  3. the sweep routes a downloaded DTD *workbook* through epay_ingest — the payment-vs-fee split, the
     TerminalID→store resolution, and the idempotent (org_id, transaction_id, transaction_source_id)
     upsert — reusing epay_ingest whole (no DTD parse reimplemented in the sweep).
  4. an empty DTD workbook is reported as mode 'no_data', never an error.

Playwright/Chromium are NOT imported or required (the sweep guards the import; this harness only drives
the pure resolver + the ingest hook, both of which are Playwright-free).

Run: `python3 harness_epay_sweep_dtd.py` from backend/.
"""
import os
import sys
import tempfile
import types

sys.path.insert(0, ".")

import pandas as pd  # noqa: E402

import app.modules.commcalc.epay_sweep as S  # noqa: E402
import app.modules.storeops.merchant_ids as MIDS  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


# ── 1. label-based report-id resolution ─────────────────────────────────────────────────────────────
MENU = [
    {"id": "102817", "label": "Monthly Incentive & ATU Subscriber Details"},
    {"id": "50273", "label": "Commission Payment Detail"},
    {"id": "100614", "label": "Comprehensive Compensation Report"},
    {"id": "778899", "label": "Daily Transaction Detail"},
]
check("resolve: exact-ish label picks the DTD menu id",
      S._resolve_report_id("daily transaction detail", MENU) == "778899",
      S._resolve_report_id("daily transaction detail", MENU))
check("resolve: match is case-insensitive",
      S._resolve_report_id("DAILY Transaction DETAIL", MENU) == "778899")
check("resolve: substring match tolerates surrounding menu text",
      S._resolve_report_id("daily transaction detail",
                           [{"id": "5", "label": "  Daily Transaction Detail (Boost)  "}]) == "5")
check("resolve: no match -> None", S._resolve_report_id("nonexistent report", MENU) is None)
check("resolve: empty label_match -> None", S._resolve_report_id("", MENU) is None)
check("resolve: does NOT mismatch a different report",
      S._resolve_report_id("daily transaction detail",
                           [{"id": "9", "label": "Commission Payment Detail"}]) is None)

# ── 2. registry wiring + job expansion ──────────────────────────────────────────────────────────────
spec = S.REPORTS["epay_daily_tx"]
check("registry: report_id is None (resolved at run time)", spec["report_id"] is None)
check("registry: label_match set", spec["label_match"] == "daily transaction detail")
check("registry: table is raw_epay_daily_tx", spec["table"] == "commcalc.raw_epay_daily_tx")
check("registry: grain day / filter daily_range", spec["grain"] == "day" and spec["filter"] == "daily_range")
check("registry: ingest hook wired to ingest_daily_tx", spec.get("ingest") is S.ingest_daily_tx)
check("registry: empty DTD window is allowed", spec.get("empty_ok") is True)

# default refresh_days=1 -> a single-day range ending today
jobs = S._expand_jobs(["epay_daily_tx"], report_cfg=None)
check("expand: one day-range job for DTD", len(jobs) == 1 and jobs[0][0] == "epay_daily_tx", jobs)
tgt = jobs[0][1]
check("expand: default window is today only (begin==end)",
      tgt.get("kind") == "day_range" and tgt.get("begin") == tgt.get("end"), tgt)
# a wider registry window widens the range in ONE run
jobs3 = S._expand_jobs(["epay_daily_tx"], report_cfg={"epay_daily_tx": {"refresh_days": 3}})
t3 = jobs3[0][1]
check("expand: refresh_days=3 -> a 3-day range in one job",
      t3["kind"] == "day_range" and len(t3["days"]) == 3 and t3["begin"] != t3["end"], t3)


# ── 3. the sweep routes a DTD WORKBOOK through epay_ingest ───────────────────────────────────────────
class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, store):
        self.store = store

    def upsert(self, chunk, on_conflict=None):
        self._chunk = chunk
        self._keys = [c.strip() for c in (on_conflict or "").split(",") if c.strip()]
        return self

    def execute(self):
        for row in self._chunk:
            key = tuple(row.get(c) for c in self._keys) if self._keys else id(row)
            self.store[key] = row       # conflict key overwrites -> idempotent, mirrors the real upsert
        return _FakeResult(self._chunk)


class _FakeSchema:
    def __init__(self, store):
        self.store = store

    def table(self, _name):
        return _FakeTable(self.store)


class _FakeClient:
    def __init__(self):
        self.store = {}

    def schema(self, _name):
        return _FakeSchema(self.store)


def _write_workbook(records):
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    pd.DataFrame(records).to_excel(path, index=False)
    return path


# One transaction with a PAYMENT line (source 1) and a FEE line (source 12) on a MAPPED terminal, plus
# a payment on an UNMAPPED terminal, plus a junk row with no TransactionID.
DTD_RECORDS = [
    {"TransactionID": "1004470333", "TransactionSourceID": "1", "InvoiceID": "67020639",
     "SettlementDate": "2026-08-18 00:00:00", "TerminalID": "633423", "UserName": "418Uniondale",
     "Product": "BSTRTRSR2", "ProductTitle": "Boost RTR PayGo $5-$300*", "Type": "Sold", "Retail": "95"},
    {"TransactionID": "1004470333", "TransactionSourceID": "12", "InvoiceID": "67020639",
     "SettlementDate": "2026-08-18 00:00:00", "TerminalID": "633423", "UserName": "418Uniondale",
     "Product": "BSTRTRFEE4", "ProductTitle": "Boost RTR $5 - $300 FEE", "Type": "Sold", "Retail": "4"},
    {"TransactionID": "1004470391", "TransactionSourceID": "1", "InvoiceID": "67020640",
     "SettlementDate": "2026-08-19 00:00:00", "TerminalID": "648757", "UserName": "117Burnside",
     "Product": "BSTNEW", "ProductTitle": "Boost New Account Replen", "Type": "Sold", "Retail": "34.06"},
    {"TransactionID": "", "TransactionSourceID": "1", "TerminalID": "633423", "Retail": "999"},
]

# Fake the merchant registry so 633423 -> store 418; 648757 stays unmapped.
_orig_resolve_map = MIDS.resolve_map
MIDS.resolve_map = lambda org_id, processor: {"633423": "418"}
try:
    wb = _write_workbook(DTD_RECORDS)
    client = _FakeClient()
    res = S.ingest_daily_tx(client, "org-1", wb, source_batch="epay-sweep epay_daily_tx [2026-08-18]")

    check("ingest: report tagged epay_daily_tx", res["report"] == "epay_daily_tx", res)
    check("ingest: mode upsert", res["mode"] == "upsert", res)
    check("ingest: 3 rows parsed+saved (junk row with no TransactionID dropped)",
          res["rows"] == 3 and res.get("parsed") == 3, res)

    # what actually landed in the (fake) table, keyed by the real conflict tuple
    stored = list(client.store.values())
    check("ingest: upsert keyed on org_id+transaction_id+transaction_source_id (3 distinct rows)",
          len(client.store) == 3, sorted(client.store.keys()))
    by_src = {(r["transaction_id"], r["transaction_source_id"]): r for r in stored}
    pay = by_src[("1004470333", "1")]
    fee = by_src[("1004470333", "12")]
    check("ingest: payment line is NOT a fee, store resolved to 418",
          pay["is_fee"] is False and pay["store_code"] == "418", pay)
    check("ingest: fee line IS a fee ('…FEE' title), store resolved to 418",
          fee["is_fee"] is True and fee["store_code"] == "418", fee)
    check("ingest: settlement_date normalized to YYYY-MM-DD", pay["settlement_date"] == "2026-08-18", pay)
    check("ingest: unmapped terminal 648757 has no store_code",
          by_src[("1004470391", "1")]["store_code"] is None, by_src[("1004470391", "1")])
    check("ingest: unresolved terminal surfaced for the confirm queue",
          [t["terminal_id"] for t in res["unresolved_terminals"]] == ["648757"],
          res["unresolved_terminals"])

    # idempotent RE-PULL: same workbook, same client -> the table does not grow (conflict overwrites)
    before = len(client.store)
    S.ingest_daily_tx(client, "org-1", wb, source_batch="epay-sweep epay_daily_tx [2026-08-18] rerun")
    check("ingest: hourly re-pull is idempotent (row count unchanged)",
          len(client.store) == before == 3, (before, len(client.store)))
    os.unlink(wb)

    # ── 4. empty (header-only) DTD workbook -> no_data, not an error ───────────────────────────────
    import app.modules.commcalc.epay_ingest as _EI
    fd, empty_wb = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    pd.DataFrame(columns=list(_EI.COLUMNS)).to_excel(empty_wb, index=False)
    try:
        empty_client = _FakeClient()
        eres = S.ingest_daily_tx(empty_client, "org-1", empty_wb, source_batch="b")
        check("ingest: empty workbook -> mode no_data (not an error)",
              eres["mode"] == "no_data" and eres["rows"] == 0, eres)
        check("ingest: empty workbook writes nothing", len(empty_client.store) == 0, empty_client.store)
    finally:
        os.unlink(empty_wb)
finally:
    MIDS.resolve_map = _orig_resolve_map


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED: " + f)
    sys.exit(1)
