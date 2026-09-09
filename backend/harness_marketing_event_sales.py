"""HARNESS — SALES FROM EVENTS: the three reports (mig 995, owner directive 2026-09-09).

OWNER SPEC (verbatim): "i need a seaprate reporting menu for only rsk activations done per store and
their retention , the report will be called sales from events and in marketing menu, for boost it
will eb coming from teh rsk events tender and the others not sure yet but provision will be made - so
2 reports - 1 total sales with all available fields n that report and the second is the retention ,
the third will be roi from te event, that will include teh cost to set up teh event and teh total
commssion received for the lines activated on that day via teh rsk , if teh event was not loaded
previously it will still run a report with the roi and ask the user to input teh cost details or link
it to the event created in the system if the user inputs teh details it will create the event in the
system with the minimal information which is required to compute the cost , cost of event , payroll
paid , the number of phones activated and their cosrt willcome from teh sales report and the sku
report, it is an unlocked phones given away the the systtem will ask while gatehring this information
how much is teh cost of teh phone, for boost check the register of rsk"

WHAT THIS PROVES (stdlib + the platform's own PURE modules — no DB, no network, no supabase call):

  A. THE REGISTER IS ONE PREDICATE — `sales_register` reproduces the expression `flags.py` used
     inline, byte-for-byte, on the REAL RSK row shape; flags.py now calls it; an empty configured
     list matches NOTHING (never "everything"); and a static scan proves nobody re-wrote the
     predicate. Includes the register-is-not-a-tender regression.
  B. RULE TWO — no register value, contract-type label or carrier word is a literal in the new
     module's executable code; the config resolver is ADAPTIVE (a pre-995 row yields the house
     defaults); a typo cannot be stored as an activation class.
  C. REPORT 1 ON THE REAL SHAPE — 602 rows, 8 stores, 13 dates, 128 transactions, 362 blank contract
     types. The blanks are NOT activations and the report says why; "all available fields" survives a
     new raw_sales column; a void/return is listed but not counted.
  D. THE ACTIVATION DEFINITION — it IS the shared classifier's, on the live contract-type strings.
     The regression: a naive "blank means we do not know" filter would report the 362 fee/bill-pay/
     SIM lines as activations, and does not.
  E. RETENTION — THREE STATES. Active, churned, and the unmatched line with its reason. The
     unmatched line is never churn, never in a denominator, and an all-unmatched window reports
     `retention_pct: None` rather than 0%. Reproduces the real 125-line / 77-matched / 48-unmatched
     measurement, including the 19 lines that dropped OUT of a loaded feed.
  F. RETENTION USES THE PAID GATE'S KEY — `_mi_index` / `_match_mi`, matched on MDN then serial.
  G. ROI — an unknown cost is never $0.00; one prompt withholds the whole ROI; the phone cost comes
     from the catalog where the SKU resolves and is PROMPTED where it does not; a measured zero
     (no handset went out) is a real zero and is labelled as one.
  H. THE EVENT-NOT-LOADED FLOW — the normal path today. No event ⇒ the report still runs, states it,
     and offers the MINIMAL create payload; the payload targets the EXISTING creator's contract.
  I. THE GIVEAWAY BOUNDARY — `marketing_event_giveaway.unit_cost` is NOT read by any money path in
     the new code, and migration 995 says so in as many words.
  J. ORG-SCOPING — a static guard over every new query in the module.
  K. MIGRATION SANITY — 995 is additive, idempotent, carries REVERT notes, seeds no money, creates no
     second event table and no second store-attribution path.
  L. ARMED — a negative control proving these assertions can actually fail.

Run:  cd backend && python3 harness_marketing_event_sales.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import sales_register as R          # noqa: E402
from app.modules.commcalc.calculator import classify_contract_type  # noqa: E402
from app.modules.commcalc.gp_report import is_voided          # noqa: E402
from app.modules.marketing import event_sales as ES           # noqa: E402

PASS, FAIL = [], []
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def check(label, got, want=True):
    if got == want:
        PASS.append(label)
    else:
        FAIL.append("%s: got %r, want %r" % (label, got, want))


def section(t):
    print("\n" + "=" * 98)
    print(t)
    print("=" * 98)


def count(prefix):
    print("  %d checks" % len([p for p in PASS if p.startswith(prefix)]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE REAL SHAPE — measured against the live house org (00000000-…-0001) on 2026-09-09.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 602 RSK rows · 8 stores · 13 distinct trans_dates · 128 distinct trans_ids · every row trans_type
# 'Sale' with a blank `voided` · contract types: 362 blank, BYOD 141, Activation 41, Activation Add A
# Line 31, Upgrade 12, Eligible Port-In Activation 8, BYOD Add A Line 5, Eligible Port-In Add A Line
# 2 · 125 DISTINCT MDNs, every one of them on a line whose contract type is NON-blank · 150 rows
# carry a device serial · the 362 blanks are Device Setup Charge (119), Boost RTR bill payments
# (117), SIM cards (102) and services (14) · Luxelink has ZERO RSK rows (its register is uniformly
# '1'). The fixtures below reproduce that shape at 1:1 proportions where the count matters.
STORES = [("3565 Broadway", 182), ("652 Communipaw Avenue", 102),
          ("196 Martin Luther King Jr Dr", 87), ("5135 BERGENLINE", 82),
          ("6011 Bergenline Ave", 73), ("2509 Bergenline Ave Ste A", 43),
          ("559 BROADWAY", 32), ("3 Palisade Ave Yonkers", 1)]
DATES = [("2026-07-31", 220), ("2026-07-03", 164), ("2026-05-02", 81), ("2026-07-10", 29),
         ("2026-06-27", 23), ("2026-06-25", 20), ("2026-07-07", 19), ("2026-06-06", 17),
         ("2026-08-08", 15), ("2026-04-18", 9), ("2026-03-28", 3), ("2026-07-19", 1),
         ("2026-03-19", 1)]
CTS = [("", 362), ("BYOD", 141), ("Activation", 41), ("Activation Add A Line", 31),
       ("Upgrade", 12), ("Eligible Port-In Activation", 8), ("BYOD Add A Line", 5),
       ("Eligible Port-In Add A Line", 2)]
BLANK_LINES = [("Dev. Charges or Fees", "Device Setup Charge", 119),
               ("Bill Payments", "Boost RTR", 117),
               ("Sim Cards", "Sim Card", 102),
               ("Miscellaneous", "Service", 14),
               ("BYOD", "Services", 7),
               ("Ondigo", "Screen Protectors", 1),
               ("C2 Wireless", "Accessories", 1),
               ("Dev. Charges or Fees", "Other Charge", 1)]


def _expand(pairs):
    out = []
    for v, n in pairs:
        out += [v] * n
    return out


def build_rsk_rows():
    """602 rows reproducing the live shape on every axis an assertion below depends on:
    8 stores / 13 dates at their real row counts, 128 distinct transactions, the real contract-type
    spread (362 blank), 125 distinct mobile numbers — every one of them on a NON-blank contract-type
    line, exactly as the live rows have it — and 150 rows carrying a device serial."""
    stores, dates, cts = _expand(STORES), _expand(DATES), _expand(CTS)
    blanks = _expand([(d + "|" + c, n) for d, c, n in BLANK_LINES])
    assert len(stores) == len(dates) == len(cts) == 602
    assert len(blanks) == 362

    # (store, date) blocks, in order — the natural event key.
    groups, order = {}, []
    for i in range(602):
        k = (stores[i], dates[i])
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(i)

    # 128 transactions spread across the blocks, largest-remainder, at least one per block.
    quota = {k: 1 for k in order}
    left = 128 - len(order)
    rem = sorted(order, key=lambda k: -len(groups[k]))
    j = 0
    while left > 0:
        k = rem[j % len(rem)]
        if quota[k] < len(groups[k]):
            quota[k] += 1
            left -= 1
        j += 1
        if j > 4000:
            break
    txn_of, txns, t = {}, [], 0
    for k in order:
        chunks = quota[k]
        rows_here = groups[k]
        per = max(1, len(rows_here) // chunks)
        for c in range(chunks):
            lo = c * per
            hi = (len(rows_here) if c == chunks - 1 else min(len(rows_here), (c + 1) * per))
            ids = rows_here[lo:hi]
            if not ids and c:
                continue
            tid = "T%04d" % t
            t += 1
            txns.append((tid, k, ids))
            for i in ids:
                txn_of[i] = tid
    assert t == 128, t

    # ACTIVATION transactions: one from every block first (so all 8 stores carry a line, the way the
    # live data does — the single-row Yonkers store included), then filled to 125.
    first_of_block, rest = [], []
    seen_blocks = set()
    for tid, k, ids in txns:
        (first_of_block if k not in seen_blocks else rest).append((tid, ids))
        seen_blocks.add(k)
    act_txns = (first_of_block + rest)[:125]

    # The non-blank contract types land INSIDE those 125 transactions. premium/byod go first so every
    # activation transaction's first line is one, and the 12 upgrades land as extra lines — which is
    # what makes the de-duplicated line count exactly 125.
    nonblank = _expand([(v, n) for v, n in CTS if v])
    nonblank.sort(key=lambda c: ("upgrade" in c.lower(), c))
    assert len(nonblank) == 240
    ct_at, mdn_at, pos, cursor = {}, {}, 0, 0
    while pos < len(nonblank):
        tid, ids = act_txns[cursor % len(act_txns)]
        depth = cursor // len(act_txns)
        cursor += 1
        if depth >= len(ids):
            continue
        i = ids[depth]
        ct_at[i] = nonblank[pos]
        pos += 1
    for n, (tid, ids) in enumerate(act_txns):
        for i in ids:
            if i in ct_at:
                mdn_at[i] = "86200%05d" % n

    rows, blank_i, serial_i = [], 0, 0
    for i in range(602):
        ct = ct_at.get(i, "")
        row = {"org_id": "00000000-0000-0000-0000-000000000001",
               "register": "RSK", "trans_type": "Sale", "voided": "",
               "store": stores[i], "trans_date": dates[i], "trans_id": txn_of[i],
               "salesperson": "REP%d" % (i % 7), "user_login": "u%d" % (i % 7),
               "contract_type": ct, "ext_price": 6.24, "gp": 1.08, "tax": 0.0,
               "tender_type": ("Cash" if i % 3 else "Credit Card"),
               "period": "%s %s" % (("March", "April", "May", "June", "July", "August")
                                    [int(dates[i][5:7]) - 3], dates[i][:4]),
               "product_desc": "", "product_id": "", "sku": "", "serial_1": "",
               "mdn": mdn_at.get(i, ""), "department": "", "category": "",
               "customer": "CUST %d" % i, "customer_no": "%06d" % i,
               "email": "c%d@example.test" % i}
        if ct:
            if serial_i < 150:
                row["serial_1"] = "3500699%08d" % serial_i
                row["product_desc"] = ("APPLE IPHONE 15" if serial_i % 2 else "SAMSUNG A15")
                row["product_id"] = "P%03d" % (serial_i % 4)
                row["sku"] = "SKU%03d" % (serial_i % 4)
                row["department"] = ("IPHONE - XP" if serial_i % 2 else "Android - XP")
                serial_i += 1
        else:
            dep, cat = blanks[blank_i].split("|")
            blank_i += 1
            row["department"], row["category"] = dep, cat
            row["product_desc"] = cat
        rows.append(row)
    assert serial_i == 150, serial_i
    return rows


RSK = build_rsk_rows()
CFG = dict(ES.DEFAULT_EVENT_SALES_CONFIG)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. THE REGISTER IS ONE PREDICATE — extracted from flags.py, not copied")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def flags_expression(rows):
    """The expression `commcalc/flags.py` carried INLINE before the extraction, reproduced verbatim
    here. Every row of the live shape must classify identically under both, or the extraction moved a
    commission flag — which is the one thing it was not allowed to do."""
    return [r for r in rows if str(r.get("register", "") or "").strip().upper() == "RSK"]


mixed = RSK + [dict(r, register=v) for r, v in zip(RSK[:40], ["1"] * 20 + ["", "rsk", " RSK ",
                                                              "Rsk", None] * 4)]
check("A1 the shared predicate matches the flag's old inline expression, row for row, on 602 real rows",
      [id(x) for x in R.filter_by_register(RSK, R.HOUSE_EVENT_REGISTERS)],
      [id(x) for x in flags_expression(RSK)])
check("A2 …and on a mixed register set including case and whitespace drift",
      len(R.filter_by_register(mixed, R.HOUSE_EVENT_REGISTERS)), len(flags_expression(mixed)))
check("A3 all 602 live rows ARE event-register rows", len(R.filter_by_register(RSK)), 602)
check("A4 a Luxelink-shaped row (register '1') is not an event sale",
      R.is_event_register({"register": "1"}), False)
check("A5 a blank register is not an event sale", R.is_event_register({"register": ""}), False)
check("A6 a missing register key is not an event sale", R.is_event_register({}), False)
check("A7 case and whitespace drift still matches", R.is_event_register({"register": " rsk "}), True)
# THE REGRESSION THE OWNER'S WORDING WOULD HAVE CAUSED: reading `tender_type` matches nothing.
check("A8 REGRESSION: RSK is a REGISTER — the same rows' tender_type never contains it",
      sorted({str(r.get("tender_type")) for r in RSK}), ["Cash", "Credit Card"])
check("A9 an EMPTY configured register list matches NOTHING (never 'everything')",
      R.filter_by_register(RSK, []), [])
check("A10 …and is_event_register agrees", R.is_event_register({"register": "RSK"}, []), False)
check("A11 registers_present names what a period DOES carry, blank included",
      R.registers_present([{"register": "RSK"}, {"register": "1"}, {}]),
      {"RSK": 1, "1": 1, "": 1})
check("A12 normalize_registers de-duplicates and preserves the org's own order",
      R.normalize_registers(["rsk", " RSK ", "EV2", "rsk"]), ("RSK", "EV2"))

_FLAGS = open(os.path.join(HERE, "app", "modules", "commcalc", "flags.py"), encoding="utf-8").read()
check("A13 flags.py now CALLS the shared predicate", "filter_by_register" in _FLAGS)
check("A14 …and no longer carries its own inline copy",
      "str(r.get('register', '') or '').strip().upper() == 'RSK'" in _FLAGS, False)
check("A15 the flag deliberately passes the HOUSE default, not per-org reporting config",
      "HOUSE_EVENT_REGISTERS as _HOUSE_REGISTERS" in _FLAGS)
count("A")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. RULE TWO — the vocabulary is DATA, and the resolver is adaptive")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_NEW_FILES = [os.path.join(HERE, "app", "modules", "marketing", "event_sales.py")]


def code_without_comments(path):
    src = open(path, encoding="utf-8").read()
    src = re.sub(r'"""(?:.|\n)*?"""', '""', src)
    src = re.sub(r"'''(?:.|\n)*?'''", "''", src)
    return re.sub(r"(?m)#.*$", "", src)


BODY = "\n".join(code_without_comments(p) for p in _NEW_FILES)
# The register VALUE, the carrier names and every contract-type label the live data carries are
# forbidden as literals in the report module's executable code: they are data, and the house default
# lives in ONE place (sales_register.HOUSE_EVENT_REGISTERS, which this module imports).
for lit in ('"RSK"', "'RSK'", "boost", "Boost", "vidapay", "VidaPay", "luxelink",
            "Eligible Port-In", "Activation Add A Line", "Boost RTR", "Device Setup Charge"):
    check("B1 no hard-coded %r in the report module's executable code" % lit, lit in BODY, False)
check("B2 the house register default comes from the ONE shared module",
      ES.DEFAULT_EVENT_SALES_CONFIG["event_sales_registers"], list(R.HOUSE_EVENT_REGISTERS))
check("B3 a pre-995 config row (no new columns) yields the house defaults",
      ES.resolve_event_sales_config({"approval_required": False}),
      dict(ES.DEFAULT_EVENT_SALES_CONFIG))
check("B4 no config row at all yields the house defaults",
      ES.resolve_event_sales_config(None), dict(ES.DEFAULT_EVENT_SALES_CONFIG))
check("B5 a tenant re-points the register with a row, not a deploy",
      ES.resolve_event_sales_config({"event_sales_registers": ["ev2", "kiosk"]})
      ["event_sales_registers"], ["EV2", "KIOSK"])
check("B6 a Postgres array literal from PostgREST is accepted too",
      ES.resolve_event_sales_config({"event_sales_registers": "{RSK,EV2}"})
      ["event_sales_registers"], ["RSK", "EV2"])
check("B7 a typo'd activation class is IGNORED, not stored to count nothing",
      ES.resolve_event_sales_config({"event_sales_activation_classes": ["premim", "byod"]})
      ["event_sales_activation_classes"], ["byod"])
check("B8 …and an entirely invalid list falls back to the house default",
      ES.resolve_event_sales_config({"event_sales_activation_classes": ["nonsense"]})
      ["event_sales_activation_classes"], ["premium", "byod"])
check("B9 retention windows are config", ES.resolve_event_sales_config(
    {"event_retention_windows_days": [7, 14, 7]})["event_retention_windows_days"], [7, 14])
check("B10 …and a garbage window list falls back to 30/60/90",
      ES.resolve_event_sales_config({"event_retention_windows_days": ["x", "-3", "0"]})
      ["event_retention_windows_days"], [30, 60, 90])
count("B")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. REPORT 1 — total sales, all available fields, on the real shape")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
S = ES.sales_summary(RSK, CFG, classify_contract_type, is_voided)
check("C1 every one of the 602 live rows is listed", S["rows"], 602)
check("C2 the 8 live stores are all present", len(S["by_store"]), 8)
check("C3 …with the live per-store row counts", S["by_store"], dict(STORES))
check("C4 the 13 live event dates are all present", len(S["by_date"]), 13)
check("C5 128 distinct transactions, not 602 lines", S["transactions"], 128)
check("C6 125 distinct mobile numbers", S["distinct_mdns"], 125)
check("C7 the contract-type spread is the live one", S["by_contract_type"]["(blank)"], 362)
check("C8 an event is a (store, date) pair and the report keys on it",
      len(S["event_keys"]) > 0 and all(k["store"] and k["trans_date"] for k in S["event_keys"]))
check("C9 every event key carries its own money and counts",
      all({"ext_price", "gp", "tax", "transactions", "lines"} <= set(k) for k in S["event_keys"]))
FIELDS = ES.detail_fields(RSK)
for f in ("trans_date", "store", "register", "contract_type", "mdn", "serial_1", "sku",
          "product_id", "ext_price", "gp", "tax", "tender_type", "customer", "salesperson"):
    check("C10 the detail carries the %r field" % f, f in FIELDS)
check("C11 'all available fields' survives a NEW raw_sales column without a code change",
      "promo_code" in ES.detail_fields(RSK + [dict(RSK[0], promo_code="X")]))
check("C12 …and never exports org_id or the row id",
      any(x in ES.detail_fields(RSK + [dict(RSK[0], id="i")]) for x in ("id", "org_id")), False)
check("C13 the annotated rows drop org_id",
      "org_id" in ES.annotate_rows(RSK[:1], CFG, classify_contract_type, is_voided)[0], False)

VOIDED = RSK[:5] + [dict(RSK[5], voided="true"), dict(RSK[6], trans_type="Return")]
SV = ES.sales_summary(VOIDED, CFG, classify_contract_type, is_voided)
check("C14 a voided line and a return are LISTED but not counted", SV["uncountable_rows"], 2)
check("C15 …and the listing still shows all 7 rows (history is not edited)", SV["rows"], 7)
count("C")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. THE ACTIVATION DEFINITION — it IS the shared classifier's, on the live labels")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
LIVE_CT = {"": None, "BYOD": "byod", "Activation": "premium", "Activation Add A Line": "premium",
           "Upgrade": "upgrade", "Eligible Port-In Activation": "premium",
           "BYOD Add A Line": "byod", "Eligible Port-In Add A Line": "premium"}
for ct, want in LIVE_CT.items():
    check("D1 the shared classifier decides %r" % (ct or "(blank)"),
          ES.line_class({"contract_type": ct}, classify_contract_type), want)
check("D2 the headline counts the CONFIGURED buckets, not a hard-coded list",
      S["activation_classes_counted"], ["premium", "byod"])
check("D3 upgrades are reported BESIDE the headline, never hidden", S["by_class"]["upgrade"], 12)
check("D4 the live activation buckets total the live label counts",
      (S["by_class"]["premium"], S["by_class"]["byod"]), (41 + 31 + 8 + 2, 141 + 5))
# THE REGRESSION: 362 blank contract types. A filter that treated "blank" as "unknown, count it"
# would report the store's set-up fees, bill payments and SIM cards as activations.
BLANKS = [r for r in RSK if not r["contract_type"]]
check("D5 REGRESSION: none of the 362 blank-contract-type lines is an activation",
      sum(1 for r in BLANKS
          if ES.line_class(r, classify_contract_type) in CFG["event_sales_activation_classes"]), 0)
check("D6 …and those blanks are the fee / bill-pay / SIM lines of the same transactions",
      sorted({r["department"] for r in BLANKS})[:3],
      ["BYOD", "Bill Payments", "C2 Wireless"])
check("D7 …and no distinct mobile number is lost by excluding them: every MDN in the live rows "
      "sits on a NON-blank contract-type line",
      len({r["mdn"] for r in RSK if r["mdn"]}),
      len({r["mdn"] for r in RSK if r["mdn"] and r["contract_type"]}))
check("D8 a tenant that wants upgrades counted says so in config, not in code",
      ES.sales_summary(RSK, {**CFG, "event_sales_activation_classes":
                             ["premium", "byod", "upgrade"]},
                       classify_contract_type, is_voided)["activation_classes_counted"],
      ["premium", "byod", "upgrade"])
count("D")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. REPORT 2 — retention has THREE states, and the third is never churn")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
LINES = ES.activation_lines(RSK, CFG, classify_contract_type, is_voided)
check("E1 one entry per activated LINE, de-duplicated on the number (not per sale line)",
      len(LINES), 125)
check("E2 every line carries the store and the event date it was rung on",
      all(x["store"] and x["trans_date"] for x in LINES))

# The live measurement, reproduced: 125 lines · 77 matched in the latest loaded month · of those
# 57 ACTIVE, 18 INVOLUNTARY-SUSPENDED, 1 INACTIVE, 1 PORTED-OUT · 48 unmatched, of which 19 WERE in
# an earlier loaded month (dropped out of the feed) and 29 are in no loaded month at all.
STATUSES = (["ACTIVE"] * 57 + ["INVOLUNTARY-SUSPENDED"] * 18 + ["INACTIVE"] + ["PORTED-OUT"])
AUG_ROWS, EARLIER_ROWS = [], []
for i, ln in enumerate(LINES):
    if i < 77:
        AUG_ROWS.append({"phone_number": ln["mdn"], "subscriber_status": STATUSES[i],
                         "mi_activation_date": "2026-07-31", "mi_deactivation_date": None})
    elif i < 96:                       # 19 lines present EARLIER and gone by the latest month
        EARLIER_ROWS.append({"phone_number": ln["mdn"], "subscriber_status": "ACTIVE",
                             "mi_activation_date": "2026-05-02", "mi_deactivation_date": None})


def index_of(rows):
    by_mdn, by_serial = {}, {}
    for r in rows:
        m = ES._norm_key(r.get("phone_number"))
        if m:
            by_mdn.setdefault(m, r)
        s = ES._norm_key(r.get("device_serial"))
        if s:
            by_serial.setdefault(s, r)
    return {"mdn": by_mdn, "serial": by_serial}


LABELS = {"2026-03": "March 2026", "2026-04": "April 2026", "2026-05": "May 2026",
          "2026-06": "June 2026", "2026-07": "July 2026", "2026-08": "August 2026",
          "2026-09": "September 2026", "2026-10": "October 2026", "2026-11": "November 2026"}
SNAP = {k: {"loaded": True, "index": index_of([])} for k in LABELS}
SNAP["2026-08"] = {"loaded": True, "index": index_of(AUG_ROWS)}
SNAP["2026-05"] = {"loaded": True, "index": index_of(EARLIER_ROWS)}
SNAP["2026-09"] = {"loaded": False, "index": index_of([])}
SNAP["2026-10"] = {"loaded": False, "index": index_of([])}
SNAP["2026-11"] = {"loaded": False, "index": index_of([])}

REP = ES.retention_report(LINES, SNAP, LABELS, [30, 60, 90], latest_key="2026-08")
NOW = [w for w in REP["windows"] if w["window"] == "now"][0]
check("E3 the live match rate is reproduced: 77 of 125 lines matched",
      (NOW["active"] + NOW["churned"]), 77)
check("E4 …57 active", NOW["active"], 57)
check("E5 …20 matched but not active (18 suspended, 1 inactive, 1 ported out)", NOW["churned"], 20)
check("E6 …and 48 UNMATCHED — which is neither of the above", NOW["unmatched"], 48)
check("E7 the retention percentage's denominator is the MATCHED lines only",
      NOW["retention_pct"], round(100.0 * 57 / 77, 1))
check("E8 …which is NOT the flattering-or-damning 57/125", NOW["retention_pct"] == 45.6, False)
check("E9 the 19 lines that dropped OUT of a loaded feed say so, and are still not churn",
      NOW["by_reason"].get(ES.REASON_DROPPED), 19)
check("E10 the 29 never seen in any loaded feed say THAT instead",
      NOW["by_reason"].get(ES.REASON_ABSENT), 29)
check("E11 the two reasons account for every unmatched line",
      sum(NOW["by_reason"].values()), 48)
check("E12 every unmatched reason is explained in words the payload carries",
      all(r in REP["unmatched_reasons"] for r in NOW["by_reason"]))
check("E13 the live statuses are reported individually, not folded into 'churn'",
      NOW["by_status"], {"ACTIVE": 57, "INVOLUNTARY-SUSPENDED": 18, "INACTIVE": 1, "PORTED-OUT": 1})

# THE DEFECT THIS REPORT EXISTS TO AVOID: a denominator that silently swallows unmatchable lines.
ALL_UNMATCHED = ES.retention_report(LINES[:10], {"2026-08": {"loaded": False, "index": {}}},
                                    LABELS, [], latest_key=None)
UW = ALL_UNMATCHED["windows"][0]
check("E14 REGRESSION: an all-unmatched window reports retention_pct None, never 0%",
      UW["retention_pct"], None)
check("E15 …and reports the feed-not-loaded reason rather than 10 cancellations",
      (UW["unmatched"], UW["churned"], UW["by_reason"].get(ES.REASON_FEED_NOT_LOADED)),
      (10, 0, 10))
check("E16 a line with no mobile number is unmatched for THAT reason, not absent-from-feed",
      ES.evaluate_line({"mdn": "", "serial_1": "", "trans_date": "2026-07-31"}, None, SNAP,
                       LABELS, latest_key="2026-08")["reason"], ES.REASON_NO_MDN)
# The most recent event day is 2026-08-08 and the latest loaded feed month is August, so its 90-day
# window lands in November — a month that cannot have happened yet, let alone been loaded.
LATEST_LINE = max(LINES, key=lambda x: x["trans_date"])
check("E17 a 90-day window whose month has not been loaded is NOT DUE, never a loss",
      ES.evaluate_line(LATEST_LINE, 90, SNAP, LABELS, latest_key="2026-08")["state"],
      ES.STATE_UNMATCHED)
check("E18 …with the not-loaded reason",
      ES.evaluate_line(LATEST_LINE, 90, SNAP, LABELS, latest_key="2026-08")["reason"],
      ES.REASON_FEED_NOT_LOADED)
check("E18b …and that line's 30-day window, whose month IS loaded, answers normally",
      ES.evaluate_line(LATEST_LINE, 30, SNAP, LABELS, latest_key="2026-08")["period"],
      "September 2026")
check("E19 'INACTIVE' does not start with 'activ' and is correctly not active",
      ES.is_active_status("INACTIVE"), False)
check("E20 'ACTIVE' is", ES.is_active_status("ACTIVE"), True)
check("E21 the per-store roll-up covers all 8 live stores", len(REP["by_store"]), 8)
check("E22 the per-(store,date) roll-up is keyed on the event key",
      all("store" in b and "trans_date" in b for b in REP["by_event_key"]))
check("E23 the report NAMES the surface it must never be confused with",
      "checkin-retention" in REP["basis"]["not_to_be_confused_with"])
count("E")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. RETENTION MATCHES ON THE COMMISSION PAID GATE'S OWN KEY")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
from app.modules.commcalc.sale_installment_engine import _mi_index, _match_mi   # noqa: E402
from app.modules.commcalc.commission_engine import _norm_mdn                    # noqa: E402

check("F1 the retention key IS commission_engine._norm_mdn, byte for byte",
      [ES._norm_key(v) for v in ("8622707122", " 8622707122 ", "8622707122.0", None, 12345)],
      [_norm_mdn(v) for v in ("8622707122", " 8622707122 ", "8622707122.0", None, 12345)])
IDX = _mi_index([{"phone_number": "8622707122", "subscriber_status": "ACTIVE"},
                 {"device_serial": "350069900000001", "subscriber_status": "ACTIVE"}])
check("F2 the paid gate's index matches by MDN first",
      ES._match({"mdn": "8622707122"}, IDX)["subscriber_status"], "ACTIVE")
check("F3 …then falls back to the device serial",
      ES._match({"mdn": "", "serial_1": "350069900000001"}, IDX)["subscriber_status"], "ACTIVE")
check("F4 …and returns None (never a guess) when neither matches",
      ES._match({"mdn": "9999999999", "serial_1": "x"}, IDX), None)
check("F5 the module's fallback agrees with the paid gate's own function",
      _match_mi({"mdn": "8622707122"}, IDX) is ES._match({"mdn": "8622707122"}, IDX))
_ES_SRC = open(_NEW_FILES[0], encoding="utf-8").read()
check("F6 the module does NOT build a second subscriber index of its own",
      "_mi_index" in _ES_SRC and "def _mi_index" not in _ES_SRC)
count("F")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. REPORT 3 — ROI: an unknown cost is never $0.00")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
DAY_ROWS = [r for r in RSK if r["store"] == "3565 Broadway" and r["trans_date"] == "2026-07-31"]
PHONES = ES.phone_lines(DAY_ROWS, CFG, classify_contract_type, is_voided)
check("G1 phones are the activation lines carrying a device serial", bool(PHONES))
check("G2 …and nothing without a serial is counted as a phone",
      all(p["serial_1"] for p in PHONES))
CAT = ES.catalog_index([{"product_id": "P000", "sku": "SKU000", "cost": 129.99},
                        {"product_id": "P001", "sku": "SKU001", "cost": 89.50}])
PRICED = ES.price_phones(PHONES, CAT)
check("G3 a SKU the catalog knows is priced from the catalog",
      all(p["cost_source"].startswith("catalog") for p in PRICED["products"]
          if p["product_id"] in ("P000", "P001")))
check("G4 a SKU the catalog does NOT know is PROMPTED, not priced at zero",
      all(p["unit_cost"] is None for p in PRICED["unpriced"]))
check("G5 …and the prompt list is not empty for this day (P002/P003 are uncatalogued)",
      bool(PRICED["unpriced"]))
check("G6 the question is asked once per MODEL, not once per handset",
      len(PRICED["products"]) <= 4)
ENTERED = ES.price_phones(PHONES, CAT, entered_unit_costs={"P002": 40, "P003": 55})
check("G7 the owner's typed cost prices the rest", ENTERED["unpriced"], [])
check("G8 …and the total is the sum of unit x qty",
      ENTERED["total"], round(sum(p["unit_cost"] * p["qty"] for p in ENTERED["products"]), 2))

PAYROLL = ES.payroll_from_hours(
    [{"employee_id": "E1", "hours": 8, "state": "measured", "day": "2026-07-31"},
     {"employee_id": "E2", "hours": 6, "state": "scheduled", "day": "2026-07-31"}],
    {"E1": 20.0, "E2": 18.0})
check("G9 payroll = the existing three-state hours x the rate", PAYROLL["total"], 8 * 20 + 6 * 18)
check("G10 …and the measured/scheduled split is reported, never averaged away",
      (PAYROLL["hours_measured"], PAYROLL["hours_scheduled"]), (8.0, 6.0))
SALARIED = ES.payroll_from_hours([{"employee_id": "E3", "hours": 8, "state": "measured"}], {})
check("G11 an employee with hours and no hourly rate is UNPRICED, not costed at zero",
      len(SALARIED["unpriced"]), 1)

EVENT = {"id": "ev1", "planned_spend": 500.0}
COSTS = ES.build_costs(EVENT, {}, PAYROLL, ENTERED)
check("G12 the event's own planned spend is DERIVED",
      [c["basis"] for c in COSTS if c["kind"] == ES.COST_EVENT_SPEND], [ES.BASIS_DERIVED])
ROI = ES.roi_compute(1200.0, COSTS, ES.COMMISSION_BASIS_ALLOCATED, False)
check("G13 a complete ROI computes", ROI["complete"], True)
check("G14 …net = commission − every cost", ROI["net"],
      round(1200.0 - (500.0 + PAYROLL["total"] + ENTERED["total"]), 2))
check("G15 …and the percentage is net over cost",
      ROI["roi_pct"], round(100.0 * ROI["net"] / ROI["cost_total"], 1))

NO_EVENT_COSTS = ES.build_costs(None, {}, None, PRICED)
ROI2 = ES.roi_compute(1200.0, NO_EVENT_COSTS, ES.COMMISSION_BASIS_ALLOCATED, False)
check("G16 REGRESSION: one unknown cost withholds the ROI entirely", ROI2["roi_pct"], None)
check("G17 …and the net too", ROI2["net"], None)
check("G18 …and nothing is rendered as 0.00 to fill the gap",
      [c["amount"] for c in NO_EVENT_COSTS if c["basis"] == ES.BASIS_PROMPT],
      [None, None, None])
check("G19 …and the report NAMES what it needs", sorted(ROI2["missing_costs"]),
      sorted([ES.COST_EVENT_SPEND, ES.COST_PAYROLL, ES.COST_PHONES]))
check("G20 …in a sentence a human can act on", "enter the figure" in (ROI2["reason"] or "").lower())
NO_PHONES = ES.build_costs(EVENT, {}, PAYROLL, ES.price_phones([], CAT))
ph = [c for c in NO_PHONES if c["kind"] == ES.COST_PHONES][0]
check("G21 a day where no handset went out is a MEASURED zero, and says so",
      (ph["amount"], ph["basis"]), (0.0, ES.BASIS_DERIVED))
check("G22 …and it is distinguishable from an unknown cost in the payload",
      "MEASURED zero" in ph["note"])
check("G23 a typed figure OVERRIDES the plan's own spend",
      [c["basis"] for c in ES.build_costs(EVENT, {ES.COST_EVENT_SPEND: 640}, PAYROLL, ENTERED)
       if c["kind"] == ES.COST_EVENT_SPEND], [ES.BASIS_ENTERED])
check("G24 the commission figure is labelled an ALLOCATION, never exact",
      ROI["commission_exact"], False)
check("G25 …and the method is stated on the response",
      "ALLOCATION, NOT A MEASUREMENT" in ROI["commission_note"])
check("G26 the exact basis exists and is described as a floor, not the whole",
      "FLOOR" in ES.COMMISSION_BASIS_NOTES[ES.COMMISSION_BASIS_EXACT])
count("G")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("H. THE EVENT-NOT-LOADED FLOW — the NORMAL path (marketing_event holds zero rows)")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("H1 no event matches when there are none", ES.match_event([], [], "B-3565", "2026-07-31"), None)
EV = {"id": "e1", "event_start": "2026-07-31T10:00:00+00:00",
      "event_end": "2026-07-31T18:00:00+00:00", "primary_store_code": "B-3565"}
check("H2 an event matches on its own calendar day + its store",
      (ES.match_event([EV], [], "B-3565", "2026-07-31") or {}).get("id"), "e1")
check("H3 …not on a different day",
      ES.match_event([EV], [], "B-3565", "2026-07-30"), None)
check("H4 …not for a different store",
      ES.match_event([EV], [], "B-OTHER", "2026-07-31"), None)
check("H5 store membership goes through the EXISTING many-to-many, not a new path",
      (ES.match_event([EV], [{"event_id": "e1", "store_code": "B-652"}], "B-652", "2026-07-31")
       or {}).get("id"), "e1")
check("H6 the raw POS store spelling is accepted as an alias",
      (ES.match_event([EV], [], "3565 Broadway", "2026-07-31",
                      store_aliases=["B-3565"]) or {}).get("id"), "e1")
MP = ES.minimal_event_payload("B-3565", "2026-07-31", planned_spend=500)
check("H7 the minimal create payload carries a title", bool(MP["title"]))
check("H8 …one store, set BOTH ways the creator understands",
      (MP["primary_store_code"], MP["store_codes"]), ("B-3565", ["B-3565"]))
check("H9 …the single calendar day as a start and an end",
      (MP["event_start"][:10], MP["event_end"][:10]), ("2026-07-31", "2026-07-31"))
check("H10 …and the spend that makes it able to carry a cost", MP["planned_spend"], 500)
check("H11 every key it sets is a real column on the EXISTING creator's body",
      sorted(set(MP) - {"title", "market", "primary_store_code", "store_codes", "event_start",
                        "event_end", "planned_spend", "description"}), [])
_R_SRC = open(os.path.join(HERE, "app", "modules", "marketing", "router.py"),
              encoding="utf-8").read()
check("H12 the ROI screen creates through the module's ONE existing creator",
      "result = create_event(EventIn(**payload)" in _R_SRC)
check("H13 …and no second insert into the event table exists",
      _R_SRC.count('table(EVENT_TABLE).insert('), 1)
count("H")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("I. THE GIVEAWAY BOUNDARY — informational money stays informational")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
MIG995 = open(os.path.join(REPO, "database", "migrations", "995_marketing_event_sales.sql"),
              encoding="utf-8").read()


def sql_without_comments(text):
    return re.sub(r"(?m)^\s*--.*$", "", text)


check("I1 the ROI code never reads marketing_event_giveaway.unit_cost",
      "unit_cost" in BODY and "giveaway" in BODY.lower(), False)
# The new endpoints' block, isolated from the rest of the router (which legitimately serves the
# giveaway collection through its generic child CRUD — that is the module's existing surface, not a
# money path this work added).
NEW_BLOCK = _R_SRC.split("# SALES FROM EVENTS — the three reports")[-1] \
                  .split("# The generic child CRUD handlers")[0]
# Comments and docstrings come out first: the block DOCUMENTS the boundary at length (it must, or the
# next reader re-crosses it), and a naive substring search over the raw text finds the very words the
# documentation promises are absent. The assertion is about EXECUTABLE code.
NEW_EXEC = re.sub(r"(?m)#.*$", "", re.sub(r'"""(?:.|\n)*?"""', '""', NEW_BLOCK))
check("I2 …and no query the new reports issue touches the giveaway table",
      "marketing_event_giveaway" in NEW_EXEC, False)
check("I2b …and no new executable line reads a giveaway unit cost",
      "unit_cost" in NEW_EXEC and "giveaway" in NEW_EXEC.lower(), False)
check("I2c …while the boundary IS documented where it would have been crossed",
      "marketing_event_giveaway.unit_cost" in NEW_BLOCK)
check("I3 migration 995 creates the ROI's OWN cost row instead",
      "CREATE TABLE IF NOT EXISTS core.marketing_event_cost" in MIG995)
check("I4 …and states the boundary decision in the migration, not only in a commit message",
      "INFORMATIONAL money" in MIG995 and "NOT promoted" in MIG995)
check("I5 the decision is also stated at the code that would have crossed it",
      "GIVEAWAY BOUNDARY" in _R_SRC)
check("I6 995 seeds no money at all",
      any(t in sql_without_comments(MIG995) for t in ("INSERT INTO core.marketing_event_cost",
                                                      "INSERT INTO core.marketing_event_giveaway")),
      False)
count("I")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("J. ORG-SCOPING — every new query, statically")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def classify_chain(chain):
    if "org-guard-ok" in chain:
        return "optout"
    if "org_id" in chain:
        return "scoped"
    if (".insert(" in chain or ".upsert(" in chain) and not (".update(" in chain
                                                             or ".delete(" in chain):
        return "payload"
    return "violation"


check("J0a the classifier discriminates: an unscoped read is a violation",
      classify_chain('.table("raw_sales").select("*").execute()'), "violation")
check("J0b …and an org-filtered read is scoped",
      classify_chain('.table("raw_sales").select("*").eq("org_id", org_id).execute()'), "scoped")

violations, scanned = [], 0
for m in re.finditer(r"\.table\(\s*[\"']([A-Za-z0-9_]+)[\"']\s*\)", NEW_BLOCK):
    seg = NEW_BLOCK[m.start(): m.start() + 1600]
    end = seg.find(".execute(")
    chain = seg[: end + 9] if end != -1 else seg
    scanned += 1
    if classify_chain(chain) == "violation":
        violations.append("%s — %s" % (m.group(1), chain[:100].replace("\n", " ")))
check("J1 every query the new reports issue is org-scoped", violations, [])
check("J2 the guard scanned a meaningful number of queries", scanned >= 8)
check("J3 the new cost table is written with org_id in the payload",
      '"org_id": org_id, "event_id": event_id, "cost_kind"' in _R_SRC)
check("J4 the new endpoints ride the module's existing entitlement gate (one router, one gate)",
      'require_module("marketing")' in _R_SRC)
check("J5 the write endpoint requires a manager, like every other write in the module",
      '_require_manager(caller, "record what an event cost")' in _R_SRC)
count("J")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("K. MIGRATION 995 — additive, idempotent, reversible, and it forks nothing")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("K1 additive column adds are idempotent",
      MIG995.count("ADD COLUMN IF NOT EXISTS") >= 4)
check("K2 the table create is idempotent", "CREATE TABLE IF NOT EXISTS" in MIG995)
check("K3 REVERT notes are present", "-- REVERT:" in MIG995)
check("K4 RLS is turned on for the new table", "ENABLE ROW LEVEL SECURITY" in MIG995)
check("K5 …and nothing is granted to anon/authenticated",
      "REVOKE ALL ON core.%I FROM anon, authenticated" in MIG995)
check("K6 NO second event table is created",
      "CREATE TABLE IF NOT EXISTS core.marketing_event " in MIG995, False)
check("K7 NO second event-store attribution table is created",
      "marketing_event_store (" in sql_without_comments(MIG995), False)
check("K8 the register default matches the code's house default",
      "DEFAULT '{RSK}'" in MIG995 and R.HOUSE_EVENT_REGISTERS == ("RSK",))
check("K9 the activation-class default matches the code's",
      "DEFAULT '{premium,byod}'" in MIG995
      and ES.DEFAULT_EVENT_SALES_CONFIG["event_sales_activation_classes"] == ["premium", "byod"])
check("K10 the retention-window default matches the code's",
      "DEFAULT '{30,60,90}'" in MIG995
      and ES.DEFAULT_EVENT_SALES_CONFIG["event_retention_windows_days"] == [30, 60, 90])
check("K11 the migration records the duplicate check it was gated on",
      "duplicate-check build gate" in MIG995)
check("K12 the index registers the new reports",
      "23s. SALES FROM EVENTS" in open(os.path.join(REPO, "docs", "SYSTEM_DATA_FLOW_INDEX.md"),
                                       encoding="utf-8").read())
check("K13 the migration number is the one assigned (995, after 994)",
      os.path.exists(os.path.join(REPO, "database", "migrations",
                                  "995_marketing_event_sales.sql")))
count("K")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("L. ARMED — the negative control")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
armed = []
if ES.retention_report(LINES[:5], {"2026-08": {"loaded": False, "index": {}}}, LABELS, [],
                       latest_key=None)["windows"][0]["retention_pct"] == 0:
    armed.append("an all-unmatched window reported 0% retention")
if ES.roi_compute(100.0, ES.build_costs(None, {}, None, ES.price_phones([], {})),
                  ES.COMMISSION_BASIS_ALLOCATED, False)["roi_pct"] is not None:
    armed.append("an ROI was computed from a missing cost")
if R.filter_by_register(RSK, []):
    armed.append("an empty register config matched every sale")
if any(ES.line_class(r, classify_contract_type) in CFG["event_sales_activation_classes"]
       for r in RSK if not r["contract_type"]):
    armed.append("a blank contract type was counted as an activation")
if ES.cost_component("x", None, ES.BASIS_PROMPT, "s")["amount"] == 0.0:
    armed.append("an unknown cost was rendered as 0.00")
check("L1 the negative control found no regression (and CAN fail — see the source)", armed, [])

_before = len(FAIL)
check("L2 self-test: a deliberately wrong assertion is recorded as a FAILURE", 1 + 1, 3)
_worked = len(FAIL) == _before + 1
if _worked:
    FAIL.pop()
    PASS.append("L2 self-test: a deliberately wrong assertion is recorded as a FAILURE")
check("L3 …and the harness's failure path is therefore wired up", _worked, True)
count("L")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 98)
for f in FAIL:
    print("  FAIL  " + f)
print("RESULT: %d passed, %d failed" % (len(PASS), len(FAIL)))
print("=" * 98)
sys.exit(1 if FAIL else 0)
