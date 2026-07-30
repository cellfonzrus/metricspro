"""DIFFERENTIAL proof for the Commission-Ledger MA refresh (owner directive 2026-07-30).

THE CLAIM UNDER TEST: refreshing the canonical ledger from the raw MA tables produces the SAME ledger rows
a hand-uploaded carrier FILE would have produced. Not "similar", not "close" — byte-identical on every
money field, bucket, sign, month and context field.

HOW IT IS PROVEN. Two paths are run over the SAME fixture data:

  A) FILE path   — exactly what POST /commission-ledger/import does per row:
                     column_mapping.apply_mapping(file_row, hdr_rules, base) -> commission_ledger.build_row
  B) MA-SYNC path — ledger_ma_sync.derive(raw_rows, ...) over the same facts spelled as raw_ma_* columns

Then every produced row is compared key-by-key (provenance keys, which only the sync sets, are compared
separately and must be the ONLY difference), and the summarize() rollups are compared as whole objects.

Also proved here (pure, no DB):
  • the composed mapping resolves EVERY ledger field of MA Daily Tx at exact-header confidence
  • the amount column is `retail_cost` — and `merchant_invoice` (NUMERIC, but an invoice NUMBER) is REFUSED
    with a named reason, in both the row shape and as a component
  • the per-line sanity ceiling excludes + counts + reports, never imports silently
  • the component expansion of one MA Commission row == a hand-flattened file of the same components
  • a month-indexed component lands in its own payment_month column of the by_month matrix
  • sign convention: negative raw = payout booked positive in its bucket; positive raw = a dealer charge
    that never reaches a payout bucket (identical on both paths)

Run: `python3 scratchpad/ledger_ma_sync_differential.py` from the backend dir.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.modules.commcalc import column_mapping as CM
from app.modules.commcalc import commission_ledger as CL
from app.modules.commcalc import ledger_ma_sync as L

_pass = _fail = 0


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}{(' — ' + str(extra)) if extra else ''}")


# ── shared config: the DEFAULTS, i.e. a tenant that configured nothing ───────────────────────────
HDR = [{"target_field": d["target_field"], "source_header": d["source_header"],
        "transform": d["transform"]} for d in CM.default_mapping("commission_ledger")]
FIELD_DEFS = CM.target_fields("commission_ledger")
CAT_RULES = CL.load_rules(None, None, "ma_daily_tx") if False else [
    {"match_field": mf, "match_op": op, "pattern": pat, "category": cat, "sign_rule": sr, "priority": pr}
    for (mf, op, pat, cat, sr, pr) in CL.DEFAULT_RULES]
ORG = "00000000-0000-0000-0000-000000000009"          # a NON-house tenant on purpose
PERIOD = "June 2026"
BASE_FILE = {"org_id": ORG, "source_report": "ma_daily_tx", "period": PERIOD}
BASE_SYNC = dict(BASE_FILE, origin=L.ORIGIN_SYNC)

PROVENANCE_KEYS = ("origin", "source_table", "source_row_id", "synced_at")


def file_rows(records):
    """The file-import path, verbatim (the same two calls + the same usable-row guard)."""
    out = []
    for r in records:
        src = CM.apply_mapping(r, HDR, {})
        if not (src.get("product_name") or src.get("raw_amount") or src.get("order_type")):
            continue
        out.append(CL.build_row(src, BASE_FILE, CAT_RULES))
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 1. the composed mapping (ledger header rules ∘ MA report column map) ──")
COL_MAP_TX = L.ma_column_map("ma_daily_tx")
SDEF_TX = L.source_def("ma_daily_tx")
RES_TX, UNRES_TX = L.resolve_field_sources(HDR, FIELD_DEFS, COL_MAP_TX, SDEF_TX.get("field_hints"))

check("every ledger field resolves for MA Daily Tx", not UNRES_TX, UNRES_TX)
check("all ten resolve at EXACT header confidence (no guessing)",
      len(RES_TX) == 10 and all(v["confidence"] == "mapped" for v in RES_TX.values()),
      {k: v["confidence"] for k, v in RES_TX.items()})
check("amount column composes to retail_cost", RES_TX["raw_amount"]["col"] == "retail_cost",
      RES_TX["raw_amount"])
check("rep composes to user_name", RES_TX["rep_user"]["col"] == "user_name")
check("date composes to tx_date", RES_TX["trans_date"]["col"] == "tx_date")
check("no ledger field composes to merchant_invoice",
      all(v["col"] != "merchant_invoice" for v in RES_TX.values()))

print("\n── 2. the ID-column guard (merchant_invoice is NUMERIC but is an invoice NUMBER) ──")
check("merchant_invoice is REFUSED as an amount", bool(L.blocked_amount_reason("merchant_invoice")))
check("...and the reason names the registered role",
      "role 'key'" in (L.blocked_amount_reason("merchant_invoice") or ""),
      L.blocked_amount_reason("merchant_invoice"))
check("retail_cost is allowed", L.blocked_amount_reason("retail_cost") is None)
check("merchant_discount is allowed", L.blocked_amount_reason("merchant_discount") is None)
for c in ("order_number", "account_id", "tx_date", "imei", "sim", "tspid", "sku", "platform_tx_id",
          "external_ref", "activation_order", "ban", "bin"):
    check(f"identifier '{c}' can never be an amount", bool(L.blocked_amount_reason(c)))
for c in ("device_margin", "consumer_margin", "consumer_financing", "rebate", "wallet_funding",
          "fees_margin", "spiff_m1", "spiff_m6", "amount_paid", "payout_total"):
    check(f"money column '{c}' is allowed", L.blocked_amount_reason(c) is None,
          L.blocked_amount_reason(c))

# a source whose amount column IS an identifier must refuse the WHOLE source and read nothing
bad_res = dict(RES_TX)
bad_res["raw_amount"] = {"header": "Retail Cost", "col": "merchant_invoice", "confidence": "mapped",
                         "label": "Amount"}
bad_rows, bad_diag = L.derive([{"merchant_invoice": 4211987, "product_name": "x"}], kind="row",
                              resolved=bad_res, hdr_rules=HDR, cat_rules=CAT_RULES, base=BASE_SYNC,
                              source_table="raw_ma_daily_tx", report_key="ma_daily_tx")
check("an ID amount column refuses the source (zero rows)", bad_rows == [])
check("...and says why", "refused" in (bad_diag.get("refused") or "").lower() or
      "identifier" in (bad_diag.get("refused") or ""), bad_diag.get("refused"))
check("...and names the blocked column", bad_diag.get("blocked_amount_col") == "merchant_invoice")

no_amt_rows, no_amt_diag = L.derive([{"retail_cost": -5}], kind="row",
                                    resolved={k: v for k, v in RES_TX.items() if k != "raw_amount"},
                                    hdr_rules=HDR, cat_rules=CAT_RULES, base=BASE_SYNC,
                                    source_table="raw_ma_daily_tx", report_key="ma_daily_tx")
check("no amount column at all also refuses (never books 0)", no_amt_rows == [] and no_amt_diag["refused"])

print("\n── 3. DIFFERENTIAL: MA Daily Tx raw rows vs the same facts as a FILE ──")
# One fixture, spelled twice. Every canonical bucket + a charge + an unmapped payout is represented.
FIXTURE = [
    # (product, order_type, amount, user, order#, date, due, acct_id, acct_name, direct_ma)
    ("TBV MONTH 1 New Activation Commission", "Postpaid Order", -25.00, "amir", "SO-1", "2026-06-03", "2026-07-01", "A100", "509 Nostrand", "CellFonz"),
    ("TBV MONTH 4 SPF", "Postpaid Order", -10.00, "amir", "SO-2", "2026-06-04", "2026-07-01", "A100", "509 Nostrand", "CellFonz"),
    ("Trac Autopay Residual", "Residual Order", -2.50, "sara", "SO-3", "2026-06-05", "", "A101", "1800 Great Neck", "CellFonz"),
    ("Postpaid Residual", "Residual Order", -7.25, "sara", "SO-4", "2026-06-06", "", "A101", "1800 Great Neck", "CellFonz"),
    ("Device Subsidy", "Promo Order", -55.00, "amir", "SO-5", "2026-06-07", "", "A100", "509 Nostrand", "CellFonz"),
    ("Airtime Top-Up $30", "Airtime Order", 30.00, "amir", "SO-6", "2026-06-08", "", "A100", "509 Nostrand", "CellFonz"),
    ("Mystery Bonus", "Other Order", -3.00, "sara", "SO-7", "2026-06-09", "", "A101", "1800 Great Neck", "CellFonz"),
]
raw_tx, file_tx = [], []
for i, (prod, otype, amt, user, onum, d, due, aid, aname, ma) in enumerate(FIXTURE):
    raw_tx.append({"id": f"raw-{i}", "org_id": ORG, "period": PERIOD, "account_id": aid,
                   "account_name": aname, "direct_ma_name": ma, "order_number": onum, "tx_date": d,
                   "due_date": due, "user_name": user, "order_type": otype, "product_name": prod,
                   "retail_cost": amt, "merchant_discount": 1.11, "merchant_invoice": 4211987 + i})
    file_tx.append({"Account ID": aid, "Account Name": aname, "Direct MA Name": ma,
                    "Order Number": onum, "Date of Transaction": d, "Date Due": due, "User": user,
                    "Order Type": otype, "Product Name": prod, "Retail Cost": amt,
                    "Merchant Discount": 1.11, "Merchant Invoice": 4211987 + i})

A = file_rows(file_tx)
B, diag_tx = L.derive(raw_tx, kind="row", resolved=RES_TX, hdr_rules=HDR, cat_rules=CAT_RULES,
                      base=BASE_SYNC, source_table="raw_ma_daily_tx", synced_at="2026-07-30T02:00:00Z",
                      report_key="ma_daily_tx")

check("both paths produce the same number of rows", len(A) == len(B), (len(A), len(B)))
diffs = []
for a, b in zip(A, B):
    for k in set(a) | set(b):
        if k in PROVENANCE_KEYS:
            continue
        if a.get(k) != b.get(k):
            diffs.append((a.get("product_name"), k, a.get(k), b.get(k)))
check("PER-ROW DIFFS ON EVERY NON-PROVENANCE KEY: NONE", not diffs, diffs[:8])
check("the sync adds ONLY provenance keys",
      set(B[0]) - set(A[0]) == set(PROVENANCE_KEYS), set(B[0]) - set(A[0]))
check("summarize() is identical between the two paths", CL.summarize(A) == CL.summarize(B))

sa = CL.summarize(A)
check("commission bucket = 25.00", sa["categories"]["commission"]["total"] == 25.0, sa["categories"])
check("spiff bucket = 10.00 (the M4 'SPF' line)", sa["categories"]["spiff"]["total"] == 10.0)
check("autopay_residual = 2.50", sa["categories"]["autopay_residual"]["total"] == 2.5)
check("residual_monthly = 7.25", sa["categories"]["residual_monthly"]["total"] == 7.25)
check("equipment_rebate = 55.00 (Subsidy)", sa["categories"]["equipment_rebate"]["total"] == 55.0)
check("the POSITIVE airtime line is a charge, in NO payout bucket",
      sa["charge_total"] == 30.0 and sa["payout_total"] == 25.0 + 10.0 + 2.5 + 7.25 + 55.0 + 3.0)
check("the unmapped payout is surfaced as 'other', not dropped",
      sa["other_count"] == 1 and sa["other_total"] == 3.0)
check("payment months parse identically (M1 commission, M4 spiff)",
      sa["by_month"].get("commission|1") == 25.0 and sa["by_month"].get("spiff|4") == 10.0,
      sa["by_month"])
check("provenance is stamped on every synced row",
      all(r["origin"] == "ma_sync" and r["source_table"] == "raw_ma_daily_tx" and
          r["synced_at"] == "2026-07-30T02:00:00Z" for r in B))
check("line-level lineage back to the raw row is kept",
      [r["source_row_id"] for r in B] == [f"raw-{i}" for i in range(len(FIXTURE))])
check("org_id is stamped on every synced row (never a constant)",
      all(r["org_id"] == ORG for r in B))
check("the diag counts what it read and wrote",
      diag_tx["rows_in"] == 7 and diag_tx["lines_out"] == 7 and diag_tx["excluded_ceiling"] == 0, diag_tx)

print("\n── 4. the per-line sanity CEILING (an id-shaped magnitude can never be booked) ──")
raw_big = list(raw_tx) + [{"id": "raw-big", "org_id": ORG, "period": PERIOD, "product_name": "Weird Line",
                           "order_type": "Postpaid Order", "retail_cost": -4211987.0, "user_name": "amir",
                           "tx_date": "2026-06-10"}]
C, diag_big = L.derive(raw_big, kind="row", resolved=RES_TX, hdr_rules=HDR, cat_rules=CAT_RULES,
                       base=BASE_SYNC, ceiling=25000, source_table="raw_ma_daily_tx",
                       report_key="ma_daily_tx")
check("the over-ceiling line is NOT written", len(C) == len(B))
check("...it is COUNTED", diag_big["excluded_ceiling"] == 1)
check("...its dollars are reported", diag_big["excluded_ceiling_total"] == 4211987.0)
check("...with an example naming the column", diag_big["excluded_examples"] and
      diag_big["excluded_examples"][0]["column"] == "retail_cost", diag_big["excluded_examples"])
check("...and the surviving totals are untouched", CL.summarize(C) == CL.summarize(B))
C2, diag_c2 = L.derive(raw_big, kind="row", resolved=RES_TX, hdr_rules=HDR, cat_rules=CAT_RULES,
                       base=BASE_SYNC, ceiling=5000000, source_table="raw_ma_daily_tx",
                       report_key="ma_daily_tx")
check("the ceiling is CONFIGURABLE (raise it and the line comes back)",
      len(C2) == len(B) + 1 and diag_c2["excluded_ceiling"] == 0)

print("\n── 5. DIFFERENTIAL: MA Commission component expansion vs a hand-flattened file ──")
COL_MAP_MC = L.ma_column_map("ma_commission")
SDEF_MC = L.source_def("ma_commission")
RES_MC, UNRES_MC = L.resolve_field_sources(HDR, FIELD_DEFS, COL_MAP_MC, SDEF_MC.get("field_hints"))
COMPS = L.components_for("ma_commission", None, COL_MAP_MC)
check("component set mirrors the residual/P&L payable columns (12)", len(COMPS) == 12, len(COMPS))
try:
    from app.modules.account.residual_subs import _MA_COMPONENTS as RS
    check("...and is EXACTLY that list, in order", [c["col"] for c in COMPS] == list(RS),
          ([c["col"] for c in COMPS], list(RS)))
except Exception as e:                                                    # pragma: no cover
    check("...and is EXACTLY that list, in order", False, f"could not import residual_subs: {e}")
check("mrc_net_discount is NOT a component (a plan price, not a dealer payment)",
      "mrc_net_discount" not in [c["col"] for c in COMPS])
check("component labels are the REPORT's own headers",
      [c["label"] for c in COMPS][:2] == ["Device Margin", "Consumer Margin"], [c["label"] for c in COMPS])
check("spiff components carry their payment month",
      [c["payment_month"] for c in COMPS if c["col"].startswith("spiff_")] == [1, 2, 3, 4, 5, 6])
check("the amount/product fields are NOT resolved from the raw table (they are synthesized)",
      "raw_amount" not in RES_MC and "product_name" not in RES_MC)
check("a field_hint can never fill the amount or the label",
      L.source_def("ma_commission", {"field_hints": {"raw_amount": "ban",
                                                     "product_name": "sku"}})["field_hints"].keys()
      .isdisjoint({"raw_amount", "product_name"}))

MC_RAW = [{"id": "mc-1", "org_id": ORG, "period": PERIOD, "tx_date": "2026-06-11",
           "activation_order": "ACT-9", "merchant_account_id": "M-77", "ban": "BAN-5",
           "activation_type": "New", "user_name": "amir", "imei": "355163568356973",
           "device_margin": -30.0, "consumer_margin": 0, "consumer_financing": None, "rebate": -20.0,
           "wallet_funding": 0, "fees_margin": -1.5, "spiff_m1": -5.0, "spiff_m2": -5.0,
           "spiff_m3": 0, "spiff_m4": -4.0, "spiff_m5": 0, "spiff_m6": 0,
           "mrc_net_discount": -45.0, "merchant_invoice": 987654321}]
D, diag_mc = L.derive(MC_RAW, kind="component", resolved=RES_MC, hdr_rules=HDR, cat_rules=CAT_RULES,
                      base=dict(BASE_SYNC, source_report="ma_commission"), components=COMPS,
                      source_table="raw_ma_commission", synced_at="2026-07-30T02:00:00Z",
                      report_key="ma_commission")
check("only the NON-ZERO components become lines", len(D) == 6, [r["product_name"] for r in D])
check("zero/empty components are counted, not written", diag_mc["skipped_empty_amount"] == 6, diag_mc)
check("mrc_net_discount never appears as a line",
      all("MRC" not in (r["product_name"] or "") for r in D))
check("the invoice number never appears as an amount",
      all(abs(r["raw_amount"]) != 987654321 for r in D))

# the same six lines as a HAND-FLATTENED file (what a human would upload)
MC_FILE = [{"Product Name": lab, "Retail Cost": amt, "User": "amir", "Date of Transaction": "2026-06-11",
            "Order Number": "ACT-9", "Order Type": "New", "Account ID": "BAN-5",
            "Direct MA Name": "M-77"}
           for lab, amt in [("Device Margin", -30.0), ("Rebate", -20.0), ("Fees Margin", -1.5),
                            ("1st Month Spiff", -5.0), ("2nd Month Spiff", -5.0), ("4th Month Spiff", -4.0)]]
E = []
for r in MC_FILE:
    src = CM.apply_mapping(r, HDR, {})
    E.append(CL.build_row(src, dict(BASE_FILE, source_report="ma_commission"), CAT_RULES))
mdiffs = []
for a, b in zip(E, D):
    for k in set(a) | set(b):
        if k in PROVENANCE_KEYS or k == "payment_month":
            continue
        if a.get(k) != b.get(k):
            mdiffs.append((a.get("product_name"), k, a.get(k), b.get(k)))
check("component expansion == the hand-flattened file (money keys identical)", not mdiffs, mdiffs[:8])
check("the spiff months the FILE could not state are stated by the component config",
      [r["payment_month"] for r in D] == [None, None, None, 1, 2, 4],
      [(r["product_name"], r["payment_month"]) for r in D])
sd = CL.summarize(D)
check("spiff bucket = 14.00 across M1/M2/M4", sd["categories"]["spiff"]["total"] == 14.0, sd["categories"])
check("by_month splits the spiffs into M1/M2/M4",
      (sd["by_month"].get("spiff|1"), sd["by_month"].get("spiff|2"), sd["by_month"].get("spiff|4"))
      == (5.0, 5.0, 4.0), sd["by_month"])
check("the unmapped MA-Commission labels are SURFACED as 'other', never guessed",
      sd["other_count"] == 3 and sd["other_total"] == 51.5, (sd["other_count"], sd["other_total"]))
check("context fields come from the hinted columns (order/store/account)",
      D[0]["order_number"] == "ACT-9" and D[0]["store"] == "M-77" and D[0]["account_id"] == "BAN-5",
      {k: D[0][k] for k in ("order_number", "store", "account_id")})
check("a positive component would be a charge, not a payout (sign convention preserved)",
      L.derive([dict(MC_RAW[0], device_margin=+30.0, consumer_margin=0, consumer_financing=0, rebate=0,
                     wallet_funding=0, fees_margin=0, spiff_m1=0, spiff_m2=0, spiff_m3=0, spiff_m4=0,
                     spiff_m5=0, spiff_m6=0)],
               kind="component", resolved=RES_MC, hdr_rules=HDR, cat_rules=CAT_RULES,
               base=BASE_SYNC, components=COMPS, source_table="raw_ma_commission",
               report_key="ma_commission")[0][0]["is_payout"] is False)

print("\n── 6. config-driven-ness (RULE TWO): no behaviour is reachable only from code ──")
comps_off = L.components_for("ma_commission", {"device_margin": {"enabled": False}}, COL_MAP_MC)
check("a component can be disabled by config", "device_margin" not in [c["col"] for c in comps_off])
comps_re = L.components_for("ma_commission", {"spiff_m4": {"label": "Month 4 Spiff", "payment_month": 9}},
                            COL_MAP_MC)
m4 = next(c for c in comps_re if c["col"] == "spiff_m4")
check("a component's label + month are config-overridable",
      m4["label"] == "Month 4 Spiff" and m4["payment_month"] == 9, m4)
comps_new = L.components_for("ma_commission", {"future_bonus": {"label": "Future Bonus"}}, COL_MAP_MC)
check("a NEW carrier payout column is a config row, not a code change",
      any(c["col"] == "future_bonus" for c in comps_new))
check("an unknown report_key still yields a usable source definition",
      L.source_def("ma_whatever")["source_table"] == "raw_ma_whatever")
check("template -> source(s) is config-overridable",
      L.template_sources("ma_daily_tx", [{"source_report": "ma_daily_tx", "report_key": "ma_commission",
                                          "enabled": True}]) == ["ma_commission"])
check("a DISABLED config row removes the source", L.template_sources(
    "ma_daily_tx", [{"source_report": "ma_daily_tx", "report_key": "ma_commission", "enabled": False}]) == [])
check("built-in defaults apply when nothing is configured",
      L.template_sources("ma_commission", []) == ["ma_commission"])

print("\n── 7. a tenant's SAVED column-map override is honoured (not the hard-coded default) ──")
saved = {"Order Number": "order_number", "Product Name": "product_name",
         "My Net Payout": {"col": "merchant_discount", "type": "num"}, "User": "user_name",
         "Date of Transaction": {"col": "tx_date", "type": "date"}}
cm_saved = L.ma_column_map("ma_daily_tx", saved)
hdr_saved = [dict(r, source_header=("My Net Payout" if r["target_field"] == "raw_amount"
                                    else r["source_header"])) for r in HDR]
res_saved, _u = L.resolve_field_sources(hdr_saved, FIELD_DEFS, cm_saved)
check("the override re-points the amount to merchant_discount",
      res_saved["raw_amount"]["col"] == "merchant_discount", res_saved.get("raw_amount"))
F, _d = L.derive([{"id": "r1", "merchant_discount": -9.99, "product_name": "TBV MONTH 2 Commission",
                   "order_number": "SO-9", "user_name": "amir", "tx_date": "2026-06-12"}],
                 kind="row", resolved=res_saved, hdr_rules=hdr_saved, cat_rules=CAT_RULES,
                 base=BASE_SYNC, source_table="raw_ma_daily_tx", report_key="ma_daily_tx")
check("...and the payout is booked from THAT column",
      len(F) == 1 and F[0]["commission"] == 9.99 and F[0]["payment_month"] == 2, F)

print("\n── 8. overlap is described, never silently merged ──")
check("file rows present + a sync pending => an explicit double-count warning",
      "counted TOGETHER" in (L.overlap_note({"file": {"lines": 1230}}, 400) or ""),
      L.overlap_note({"file": {"lines": 1230}}, 400))
check("...and it promises nothing file-imported is deleted",
      "Nothing file-imported is deleted" in (L.overlap_note({"file": {"lines": 1230}}, 400) or ""))
check("a re-sync says it REPLACES only the synced rows",
      "File-imported rows are untouched" in (L.overlap_note({"ma_sync": {"lines": 400}}, 400) or ""))
check("an empty period warns about nothing", L.overlap_note({}, 400) is None)
check("writing zero rows warns about nothing", L.overlap_note({"file": {"lines": 10}}, 0) is None)

print(f"\n===== ledger_ma_sync_differential: {_pass} passed, {_fail} failed =====")
sys.exit(1 if _fail else 0)
