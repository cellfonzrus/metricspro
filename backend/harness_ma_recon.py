"""HARNESS — B2B ↔ MA Commission / MA TX reconciliation → discrepancy report (Phase C, mig 312).

Owner spec 2026-09-01: an activation rung out in B2B but not paid in MA Commission / MA TX falls
into the discrepancy report for that month, attributed through the uploadable business rules — and
when no rule exists it STILL appears, with the literal reason 'no business rule configured'.

Everything proven here is PURE — no database, no pandas, no network (stdlib + the pure module only).

  python3 backend/harness_ma_recon.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.commcalc import ma_recon as mr                          # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}   {extra}")


CFG = {"gate_source": "ma_tx", "ma_tx_activation_order_type": "Activation Order",
       "ma_max_month": 16, "ma_min_amount": 0.01, "ma_payout_sign": -1,
       "ma_month_field_prefix": "spiff_m", "ma_month1_extra_fields": ["rebate", "device_margin"]}
PERIOD = "August 2026"

print("── A. sold-side basis: contract_type semantics over the display sales union ──")
SALES = [
    # A — paid via ma_commission spiff (Excel-float serial must normalize onto the MA join key)
    {"serial_1": "355163568356973.0", "contract_type": "Activation", "trans_id": "T-A",
     "trans_date": "2026-08-02", "store": "S1", "salesperson": "alice",
     "product_desc": "Samsung A15", "_source_table": "raw_sales"},
    # A again, later ring — dedupe must keep the EARLIER row
    {"serial_1": "355163568356973", "contract_type": "Activation", "trans_id": "T-A2",
     "trans_date": "2026-08-09", "store": "S1", "salesperson": "alice",
     "product_desc": "Samsung A15 re-ring", "_source_table": "daily_sales_feed"},
    # B — paid via the MA TX activation-order row only (no spiff money)
    {"serial_1": "990001112223334", "contract_type": "Eligible Port-In Activation", "trans_id": "T-B",
     "trans_date": "2026-08-05", "store": "S2", "salesperson": "bob",
     "product_desc": "Moto G", "_source_table": "daily_sales_feed"},
    # C — sold, NO MA record anywhere → the owner's headline case
    {"serial_1": "111112222233333", "contract_type": "Activation", "trans_id": "T-C",
     "trans_date": "2026-08-07", "store": "S3", "salesperson": "cara",
     "product_desc": "iPhone 13", "_source_table": "raw_sales"},
    # D — unpaid BYOD SIM kit: the business-rule case
    {"serial_1": "444445555566666", "contract_type": "BYOD", "trans_id": "T-D",
     "trans_date": "2026-08-11", "store": "S1", "salesperson": "alice",
     "product_desc": "BYOD Sim Kit Deluxe", "_source_table": "raw_sales"},
    # E — MA row exists but net spiff is a CHARGE (positive under sign -1): NOT paid
    {"serial_1": "777778888899999", "contract_type": "Activation", "trans_id": "T-E",
     "trans_date": "2026-08-13", "store": "S2", "salesperson": "bob",
     "product_desc": "Pixel 8", "_source_table": "raw_sales"},
    # excluded lines: swap, voided, blank contract_type, and an activation with no serial
    {"serial_1": "123123123123123", "contract_type": "Swap", "trans_id": "T-X1",
     "trans_date": "2026-08-03"},
    {"serial_1": "321321321321321", "contract_type": "BYOD Swap", "trans_id": "T-X2",
     "trans_date": "2026-08-03"},
    {"serial_1": "999888777666555", "contract_type": "Activation", "voided": "TRUE",
     "trans_id": "T-X3", "trans_date": "2026-08-03"},
    {"serial_1": "555666777888999", "contract_type": "", "trans_id": "T-X4",
     "trans_date": "2026-08-03"},
    {"serial_1": "", "contract_type": "Activation", "trans_id": "T-X5", "trans_date": "2026-08-03"},
]
sold, no_serial = mr.build_sold_index(SALES)
check("activation lines only: swap / BYOD Swap / voided / blank contract_type are excluded",
      set(sold) == {"355163568356973", "990001112223334", "111112222233333",
                    "444445555566666", "777778888899999"}, sorted(sold))
check("Excel-float serial digit-normalizes; duplicate rings dedupe to the EARLIEST row",
      sold["355163568356973"]["trans_id"] == "T-A", sold["355163568356973"].get("trans_id"))
check("an activation with no normalizable serial is COUNTED (sold_without_serial), never dropped silently",
      no_serial == 1, no_serial)
check("builder never raises on junk", mr.build_sold_index(None) == ({}, 0)
      and mr.build_sold_index([{}]) == ({}, 0))

print("── B. paid-side: reuse of the mig-308 two-hop primitives (never re-implemented) ──")
MA_ROWS = [
    # A: base + adjustment pair — spiff_m1 nets to -15 (a payout under sign -1)
    {"imei": "355163568356973.0", "sim": "", "activation_order": "AO-1", "spiff_m1": -20.0},
    {"imei": "355163568356973", "activation_order": "AO-1", "spiff_m1": 5.0},
    # B: links to AO-2 but carries NO spiff money — MA TX must prove it instead
    {"imei": "990001112223334", "activation_order": "AO-2"},
    # E: net POSITIVE spiff_m1 = a CHARGE under sign -1 → must NOT read as paid
    {"imei": "777778888899999", "activation_order": "AO-5", "spiff_m1": 12.0},
]
TX_ROWS = [
    {"order_number": "AO-2", "order_type": "Activation Order",
     "product_name": "Total 5G Plan $50", "retail_cost": 50.0, "account_id": "ACCT7"},
    {"order_number": "AO-9", "order_type": "Activation Order",   # unlinked order — must gate nobody
     "product_name": "Total 5G Plan $60", "retail_cost": 60.0},
]
paid = mr.build_paid_index(MA_ROWS, TX_ROWS, CFG)
check("paid index carries the three reused indexes (spiff / link / tx)",
      set(paid) == {"spiff", "link", "tx"})
check("hop 1: serial → activation_order (imei|sim, digit-normalized, netted rows union orders)",
      paid["link"].get("355163568356973") == ["AO-1"]
      and paid["link"].get("990001112223334") == ["AO-2"])
check("hop 2: order_number → activation row (order_type from CONFIG, not a literal)",
      paid["tx"]["AO-2"]["activation"]["retail_cost"] == 50.0)
check("build_paid_index never raises on junk", mr.build_paid_index(None, None, None)
      == {"spiff": {}, "link": {}, "tx": {}})

print("── C. reconciliation statuses — evidence-first, never guessed ──")
rows, summary = mr.reconcile_ma_activations(sold, paid, [], PERIOD, CFG)
by = {r["imei"]: r for r in rows}
check("A: paid via ma_commission spiff (netted base+adjustment) → 'ok'",
      by["355163568356973"]["status"] == "ok", by["355163568356973"])
check("B: paid via the MA TX activation-order row (two-hop) → 'ok'",
      by["990001112223334"]["status"] == "ok", by["990001112223334"])
check("C: sold, unpaid, NO rules → 'open' with the LITERAL reason",
      by["111112222233333"]["status"] == "open"
      and by["111112222233333"]["notes"] == "no business rule configured")
check("D: sold, unpaid, no rules loaded → also 'open' + the literal reason",
      by["444445555566666"]["status"] == "open"
      and by["444445555566666"]["notes"] == mr.NO_RULE_REASON)
check("E: net spiff CHARGE (positive under sign -1) does NOT read as paid — direction-aware",
      by["777778888899999"]["status"] == "open"
      and by["777778888899999"]["evidence"]["gate_reason"] == "net_clawback",
      by["777778888899999"]["evidence"])
check("summary counts: 2 ok / 3 open / 0 explained",
      (summary["paid_ok"], summary["open_no_rule"], summary["explained_info"],
       summary["explained_lagged"]) == (2, 3, 0, 0), summary)

print("── D. the two-hop EVIDENCE dict names exactly which source had / lacked the activation ──")
evC = by["111112222233333"]["evidence"]
check("C.b2b names the sold transaction (trans_id + date + source table)",
      evC["b2b"] == {"trans_id": "T-C", "trans_date": "2026-08-07", "source_table": "raw_sales"},
      evC["b2b"])
check("C.ma_commission says NOT matched (imei named, no activation_orders)",
      evC["ma_commission"]["matched"] is False
      and evC["ma_commission"]["imei"] == "111112222233333"
      and evC["ma_commission"]["activation_orders"] == [], evC["ma_commission"])
check("C.ma_tx says NOT matched, no order_number",
      evC["ma_tx"]["matched"] is False and evC["ma_tx"]["order_number"] is None, evC["ma_tx"])
evB = by["990001112223334"]["evidence"]
check("B.ma_commission matched with its activation_order named (hop 1)",
      evB["ma_commission"]["matched"] is True
      and evB["ma_commission"]["activation_orders"] == ["AO-2"], evB["ma_commission"])
check("B.ma_tx matched with the exact order_number + activation-order sighting (hop 2)",
      evB["ma_tx"]["matched"] is True and evB["ma_tx"]["order_number"] == "AO-2"
      and evB["ma_tx"]["activation_order_seen"] is True, evB["ma_tx"])
check("B row carries order_number as a top-level column (mig 312 provenance)",
      by["990001112223334"]["order_number"] == "AO-2")
check("A (spiff-paid, no TX rows): ma_tx honestly reports unmatched-in-tx, order_number None",
      by["355163568356973"]["evidence"]["ma_tx"]["matched"] is False
      and by["355163568356973"]["order_number"] is None,
      by["355163568356973"]["evidence"]["ma_tx"])

print("── E. business rules: first-match by priority; case/trim-insensitive; windows; regex guard ──")
RULES = [
    # bad regex at TOP priority — must be SKIPPED (guarded), never crash the recon
    {"id": "r-bad", "rule_key": "broken-regex", "description": "broken", "match_field": "product_desc",
     "match_op": "regex", "match_value": "([", "expected_outcome": "not_paid", "priority": 1,
     "is_active": True},
    # the real BYOD rule (note the un-trimmed, differently-cased value → case/trim-insensitive)
    {"id": "r-byod", "rule_key": "byod-sim-kit", "description": "BYOD SIM kits carry no MA payout",
     "match_field": "product_desc", "match_op": "contains", "match_value": "  byod sim kit  ",
     "expected_outcome": "not_paid", "priority": 10, "is_active": True},
    # a lower-priority rule that ALSO matches D — must NOT win (first match by priority)
    {"id": "r-generic", "rule_key": "generic-byod", "description": "generic BYOD (should not win)",
     "match_field": "contract_type", "match_op": "equals", "match_value": "BYOD",
     "expected_outcome": "not_paid", "priority": 50, "is_active": True},
    # window rule: effective only from Sep 2026 — must not touch an August sale
    {"id": "r-window", "rule_key": "future-window", "description": "future promo",
     "match_field": "product_desc", "match_op": "contains", "match_value": "iphone",
     "expected_outcome": "not_paid", "priority": 5, "is_active": True,
     "effective_from": "2026-09-01"},
    # paid_late outcome for the Pixel → the report's 'lagged' tab
    {"id": "r-late", "rule_key": "pixel-late", "description": "Pixel promo pays one statement late",
     "match_field": "product_desc", "match_op": "prefix", "match_value": "pixel",
     "expected_outcome": "paid_late", "priority": 20, "is_active": True},
    # inactive rule that would otherwise beat everything
    {"id": "r-off", "rule_key": "disabled", "description": "disabled", "match_field": "product_desc",
     "match_op": "contains", "match_value": "a", "expected_outcome": "not_paid", "priority": 0,
     "is_active": False},
]
rows2, summary2 = mr.reconcile_ma_activations(sold, paid, RULES, PERIOD, CFG)
by2 = {r["imei"]: r for r in rows2}
d = by2["444445555566666"]
check("D: rule matched → 'info' with rule_key + description attached",
      d["status"] == "info" and d["rule_key"] == "byod-sim-kit"
      and d["rule_reason"] == "BYOD SIM kits carry no MA payout"
      and d["notes"] == "BYOD SIM kits carry no MA payout" and d["rule_id"] == "r-byod", d)
check("priority FIRST-match wins (byod-sim-kit @10 beats generic-byod @50)",
      d["rule_key"] != "generic-byod")
check("bad regex rule is skipped (never crashes; never matches)",
      all(r.get("rule_key") != "broken-regex" for r in rows2))
check("inactive rule never matches even at priority 0",
      all(r.get("rule_key") != "disabled" for r in rows2))
check("effective_from window respected: future-window rule does NOT explain the August iPhone",
      by2["111112222233333"]["status"] == "open"
      and by2["111112222233333"]["notes"] == mr.NO_RULE_REASON, by2["111112222233333"])
check("expected_outcome='paid_late' → status 'lagged' (the report's awaiting-later-statement tab)",
      by2["777778888899999"]["status"] == "lagged"
      and by2["777778888899999"]["rule_key"] == "pixel-late", by2["777778888899999"])
check("summary reflects the attribution split (2 ok / 1 open / 1 info / 1 lagged)",
      (summary2["paid_ok"], summary2["open_no_rule"], summary2["explained_info"],
       summary2["explained_lagged"]) == (2, 1, 1, 1), summary2)
check("effective_to window: a rule ending before the sale does not match",
      mr.match_rules({"product_desc": "BYOD Sim Kit", "trans_date": "2026-08-11"},
                     [dict(RULES[1], effective_to="2026-07-31")]) is None)
check("windowed rule with an unparseable trans_date does NOT match (needs a date to prove it applies)",
      mr.match_rules({"product_desc": "BYOD Sim Kit", "trans_date": ""},
                     [dict(RULES[1], effective_from="2026-01-01")]) is None)
check("match ops: equals + prefix are case/trim-insensitive too",
      mr._rule_matches_value("equals", " BYOD ", "byod")
      and mr._rule_matches_value("prefix", "PIX", "  pixel 8  ") is True)

print("── F. persist payload: org/period scoping, ok rows dropped, adaptive narrow insert ──")
wide = mr.persist_payload(rows2, "org-123", wide=True)
check("'ok' rows are NOT persisted (the report holds discrepancies, mirrors the Boost engine)",
      len(wide) == 3 and all(r["status"] != "ok" for r in wide), [r["status"] for r in wide])
check("every persisted row is stamped with the org and the run's period (tenant + month scoping)",
      all(r["org_id"] == "org-123" and r["period"] == PERIOD for r in wide))
check("every persisted row is marked source='ma' + comp_type='MA_ACTIVATION' (the delete-scope keys "
      "that keep Boost rows untouched)",
      all(r["source"] == "ma" and r["comp_type"] == mr.MA_COMP_TYPE for r in wide))
narrow = mr.persist_payload(rows2, "org-123", wide=False)
check("ADAPTIVE narrow payload drops exactly the mig-312 attribution columns (pre-312 DB insert)",
      all(not any(c in r for c in mr.ATTRIBUTION_COLUMNS) for r in narrow)
      and len(narrow) == len(wide))
check("narrow payload keeps the legacy column set (status/notes/imei/store/period …)",
      all({"org_id", "period", "imei", "store", "status", "notes", "comp_type"} <= set(r)
          for r in narrow))
check("payload helper never raises on junk", mr.persist_payload(None, "o") == [])

print("── G. period canonicalization — the parse_period lenient-January trap is closed ──")
check("'2026-08' → 'August 2026' (POST /discrepancy/run sends YYYY-MM; the shared helpers would "
      "otherwise read JANUARY's MA statements)",
      mr.canonical_period("2026-08") == "August 2026", mr.canonical_period("2026-08"))
check("'august 2026' (any case) → 'August 2026'; canonical label passes through unchanged",
      mr.canonical_period("august 2026") == "August 2026"
      and mr.canonical_period("August 2026") == "August 2026")
check("unparseable input passes through UNCHANGED — never guesses a month",
      mr.canonical_period("Q3 FY26") == "Q3 FY26" and mr.canonical_period("") == ""
      and mr.canonical_period("2026-13") == "2026-13")

print("── H. module contracts the router relies on ──")
check("NO_RULE_REASON is the exact owner-spec literal",
      mr.NO_RULE_REASON == "no business rule configured")
check("OUTCOME_STATUS maps not_paid/partial → info and paid_late → lagged",
      mr.OUTCOME_STATUS == {"not_paid": "info", "paid_late": "lagged", "partial": "info"})
check("RULE_MATCH_FIELDS mirror the mig-312 CHECK",
      mr.RULE_MATCH_FIELDS == ("product_desc", "department", "category", "contract_type",
                               "sku", "plan"))
check("the join/evidence primitives are the mig-308 engine's own functions (reused, not re-implemented)",
      mr.build_ma_link_index.__module__ == "app.modules.commcalc.sale_installment_engine"
      and mr.build_ma_tx_index.__module__ == "app.modules.commcalc.sale_installment_engine"
      and mr._gate_met_ma_tx.__module__ == "app.modules.commcalc.sale_installment_engine"
      and mr._norm_imei.__module__ == "app.modules.commcalc.sale_installment_engine")

print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILURES'}: {PASS} passed, {FAIL} failed")
sys.exit(0 if FAIL == 0 else 1)
