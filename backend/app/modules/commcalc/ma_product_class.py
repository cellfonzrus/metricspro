"""PRODUCT-NAME CLASSIFICATION for the MA Daily Tx file — what KIND of money each line is.

WHY THIS EXISTS (owner directive, in chat 2026-07-31): "the product_name column mixes MANY payment
types — if I assign them commission they could be residual for some or spiff or bill payment revenue;
we need to create another file based on the product name in that column, it could be billpayment or
rebate or commission". One `commcalc.raw_ma_daily_tx` export carries, side by side:

    'TBV MONTH 3 New Activation Commission'  -37.50   a commission installment
    'TBV MONTH 5 New Activation SPF'         -37.50   a spiff installment
    'Trac Autopay Residual '                  -3.40   a residual  (note the TRAILING SPACE)
    'Total MAX 5G Plan $55'                   55.00   a customer PLAN PURCHASE (bill payment revenue)
    'Apple iPhone 16e 128GB Black TO'         599.99  a DEVICE SALE
    'Invoice Fee'                              0.40   a dealer FEE
    'Credit Debit Memo'                       99.99   an ADJUSTMENT

Treating that column as one thing — "commission" — is the mistake this module makes impossible.

──────────────────────────────────────────────────────────────────────────────────────────────────
THIS PACKAGE MOVES $0, BY CONSTRUCTION.
Nothing here is read by `calculator.py`, `commission_engine.py`, `_run_calculation`, `rep_commissions`,
`commission_ledger.build_row`, `ledger_ma_sync` or `whatif._ma_carrier_income`. The classification is
CONFIG + a READ-ONLY preview. Wiring a class into a payout/ledger/P&L number is a separate, owner-gated
change (see the OPEN follow-up in docs/handoffs/commission.md).
──────────────────────────────────────────────────────────────────────────────────────────────────

WHY A SIBLING TABLE AND NOT `commission_category_map` (the config-home decision, evidence in the handoff):
`commcalc.commission_category_map` (mig 071) is a RULE table — (pattern, match_op, priority),
first-match-wins — whose rows are read by `commission_ledger.load_rules()` and booked into the five
canonical payout buckets that now feed What-If's carrier income. Three concrete facts made extending it
the wrong call:
  1. `load_rules` selects `.in_("source_report", [source_report, "*"])`, so a row filed under the wrong
     namespace (or '*') silently reclassifies live ledger money. This package must move $0; the safe
     structure is the one where a data-entry slip CANNOT move money.
  2. `commission_ledger.build_row` books `row[category]` only when `category in CATEGORIES`, and
     `summarize()` counts only the five buckets + 'other' + 'charge'. A row carrying `device_sale`
     would set `payout_total` yet vanish from every roll-up — dollars lost silently.
  3. `list_templates()` enumerates DISTINCT `source_report` from that table and offers each as a ledger
     TEMPLATE; a namespace hack would put a non-template in the ledger's picker, and the existing
     editor's category dropdown (five buckets) would silently rewrite any row an operator touched.
The shape needed here is the one `commcalc.gp_category_map` (mig 069) already established in this
module: ONE ROW PER OBSERVED VALUE of a raw column, exact key, own vocabulary, own admin page. So this
follows an existing in-module pattern rather than inventing a parallel rule engine — and it leaves the
money classifier untouched.

MATCHING IS EXACT — DELIBERATELY (RULE: no keyword/substring matchers).
`match(name)` is byte equality after `strip()` and NOTHING else. No lowercasing, no `contains`, no
regex. Two reasons, both live bug classes in this codebase:
  · 'TW EDGE SPF Month 1' is the Total Wireless EDGE **financing tender**, not a Motorola Edge handset
    (memory: edge-is-financing-not-device-model). A `contains 'edge'` rule bills a phone as a spiff.
  · 'Total ALL ACCESS Plan $65' (a $65 plan purchase) and 'Total ALL ACCESS Plan $65 New Activation
    Commission' (a $0 commission line) differ ONLY by suffix. Any prefix/contains rule collapses them.
The single normalization is `strip()`, which exists for exactly one observed reason: the export ships
'Trac Autopay Residual ' with a TRAILING SPACE. Case is significant — a case variant lands UNMAPPED
(loud), which is the safe direction.

UNMAPPED IS LOUD, NEVER A MONEY BUCKET. A name with no confirmed mapping classifies as 'unmapped' and
is reported with its own line count and dollar total, plus the offending names. It is a reserved class:
the write path refuses to assign it.

PROPOSED vs CONFIRMED. The seed below is the owner's own sample, filed as status='proposed'. The
preview computes BOTH readings — 'confirmed' (only owner-confirmed rows count) and 'proposed'
(confirmed + proposed) — so the owner sees exactly what CONFIRMING would do before confirming it. A
money decision stays the owner's.

PURE + DB-FREE: no client, no network, no I/O. The DB orchestration lives in router.py.
"""

ORG_HOUSE = "00000000-0000-0000-0000-000000000001"

# Table names are MA-prefixed on purpose: `agency.py` already uses the bare token "product_class" as a
# holdback SCOPE key meaning the ACCESSORY taxonomy (agency._accessory_classes). Two different
# vocabularies under one name is exactly how a future wiring change books the wrong thing.
CLASS_TABLE = "ma_product_class"
MAP_TABLE = "ma_product_class_map"
SOURCE_TABLE = "ma_product_class_source"

# The reserved sentinel: "no confirmed mapping". Never stored, never assignable, always surfaced.
UNMAPPED = "unmapped"
STATUSES = ("proposed", "confirmed")

# ── the source registry (defaults; per-tenant overrides live in commcalc.ma_product_class_source) ──
# Only `raw_ma_daily_tx` is registered today — the file the directive is about. The registry exists so a
# second raw source (another processor's daily file) is onboarded by adding a ROW, not a code branch.
#   name_column    the text column being classified
#   amount_column  the SIGNED money column summed for the preview (see the identifier guard below)
#   date_column    fallback month when `period` is blank or differently spelled
DEFAULT_SOURCES = {
    "ma_daily_tx": {
        "source_report": "ma_daily_tx",
        "source_table": "raw_ma_daily_tx",
        "name_column": "product_name",
        "amount_column": "retail_cost",
        "date_column": "tx_date",
        "period_column": "period",
        "store_column": "account_name",
        "rep_column": "user_name",
        "label": "MA Daily Tx (VidaPay / Total) — Product Name",
    },
}

# Money columns on raw_ma_daily_tx, in offer order. `merchant_invoice` is DELIBERATELY absent: mig 083
# typed it NUMERIC but it is the Merchant Invoice NUMBER (ma_upload.FIELD_LABELS role='key'), and summing
# it as dollars is the live bug that reported -$492,946,277,716 of "residual" (whatif.py header). The
# guard below refuses it whatever a caller asks for.
MONEY_COLUMNS = {
    "ma_daily_tx": ("retail_cost", "merchant_discount"),
}


def is_money_column(source_report, column):
    """True only for a column this module may sum as dollars. Cross-checked against whatif's identifier
    guard (the same ma_upload.FIELD_LABELS catalogue) in the proof — an identifier can never be money."""
    col = (column or "").strip()
    allowed = MONEY_COLUMNS.get(source_report) or MONEY_COLUMNS["ma_daily_tx"]
    return bool(col) and col in allowed


def source_def(source_report, override=None):
    """The effective source definition: the built-in default, overlaid with a tenant's saved row. An
    override that names a non-money amount column is REFUSED (the default stands) and reported."""
    base = dict(DEFAULT_SOURCES.get(source_report) or DEFAULT_SOURCES["ma_daily_tx"])
    base["source_report"] = source_report or base["source_report"]
    refused = None
    for k, v in (override or {}).items():
        if k not in base or v in (None, ""):
            continue
        if k == "amount_column" and not is_money_column(base["source_report"], v):
            refused = v
            continue
        base[k] = v
    base["amount_refused"] = refused
    base["money_columns"] = list(MONEY_COLUMNS.get(base["source_report"]) or ())
    return base


# ── the class vocabulary (seeded into commcalc.ma_product_class; owner-editable there) ─────────────
# `reserved` = a sentinel the UI shows but the write path refuses to assign.
DEFAULT_CLASSES = [
    ("commission", "Commission", "Activation / upgrade commission earned by the dealer.", 10, False),
    ("spiff", "Spiff", "Promotional or behaviour bonus (SPF), often paid across M1..M6.", 20, False),
    ("residual", "Residual", "Recurring monthly / autopay residual on an active subscriber.", 30, False),
    ("billpayment", "Bill payment / airtime", "Customer plan purchase or RTR airtime top-up sold at the "
     "counter — retail revenue, not a carrier payout to the dealer.", 40, False),
    ("device_sale", "Device sale", "Handset / tablet / router sold to the customer.", 50, False),
    ("protection", "Protection / insurance", "Device-protection or insurance plan line.", 60, False),
    ("financing", "Financing", "Financing credit or financing-tender line (e.g. the TW EDGE tender).", 70, False),
    ("subsidy", "Subsidy", "Carrier device subsidy / equipment rebate.", 80, False),
    ("fee", "Fee", "Fee charged to the dealer (invoice, processing).", 90, False),
    ("wallet", "Wallet funding", "Funding of the dealer's RTR wallet.", 100, False),
    ("sim_kit", "SIM kit", "SIM card / SIM-kit line.", 110, False),
    ("adjustment_memo", "Adjustment / memo", "Credit or debit memo — a correction, not an earning.", 120, False),
    (UNMAPPED, "Unmapped", "RESERVED — no confirmed class yet. Never assignable; always surfaced with "
     "its own line count and dollar total.", 999, True),
]
CLASS_KEYS = tuple(c[0] for c in DEFAULT_CLASSES)
ASSIGNABLE_CLASSES = tuple(c[0] for c in DEFAULT_CLASSES if not c[4])
CLASS_LABELS = {c[0]: c[1] for c in DEFAULT_CLASSES}


# ── the seed: the owner's own sample of real distinct product_name values, as PROPOSALS ─────────────
# (product_name, product_class, note). Every row lands status='proposed' — the owner confirms in the UI.
# The names are stored TRIMMED; the export's 'Trac Autopay Residual ' matches after strip().
# Written as a literal table on purpose: no keyword rule generated these, and none is used to apply them.
DEFAULT_PROPOSALS = [
    # ── commission (activation commission, incl. the month-indexed installments) ──
    ("TBV MONTH 2 New Activation Commission", "commission", ""),
    ("TBV MONTH 3 New Activation Commission", "commission", ""),
    ("New Activation Commission - M1 Proration", "commission", "Partial-month M1 commission."),
    ("Total MAX 5G BYO Plan $30 New Activation Commission", "commission",
     "Suffix-only difference from the plan-purchase line 'Total MAX 5G BYO Plan $30' — the reason "
     "matching is exact."),
    ("Total STARTER Plan $40 New Activation Commission", "commission", ""),
    ("Total MAX 5G Plan $55 New Activation Commission", "commission", ""),
    ("Total ALL ACCESS 2 Month Plan $130 New Activation Commission", "commission", ""),
    ("Total ALL ACCESS Plan $65 New Activation Commission", "commission",
     "Sampled at $0.00 — a commission line that happened to pay nothing, NOT a plan purchase."),
    ("Total Wireless Base Unlimited Tablet 3-Month Plan $30 New Activation Commission", "commission", ""),
    # ── spiff ──
    ("TBV MONTH 4 New Activation SPF", "spiff", ""),
    ("TBV MONTH 5 New Activation SPF", "spiff", ""),
    ("TBV MONTH 6 New Activation SPF", "spiff", ""),
    ("BYO Activation SPF Month 1", "spiff", ""),
    ("BYO Activation SPF Month 2", "spiff", ""),
    ("TW EDGE SPF Month 1", "spiff",
     "EDGE here is the Total Wireless FINANCING TENDER, not a Motorola Edge handset — a 'contains edge' "
     "rule would misclassify phones. Exact match only."),
    # ── residual ──
    ("Residual", "residual", ""),
    ("Trac Autopay Residual", "residual",
     "The export ships this with a TRAILING SPACE ('Trac Autopay Residual '); it matches after trim()."),
    # ── bill payment / airtime (customer plan purchases + RTR top-ups) ──
    ("Total MAX 5G BYO Plan $30", "billpayment", ""),
    ("Total MAX 5G BYO Plan $30 RTR", "billpayment", ""),
    ("Total MAX 5G Plan $55", "billpayment", ""),
    ("Total MAX 5G Plan $55 RTR", "billpayment", ""),
    ("Total STARTER Plan $40", "billpayment", ""),
    ("Total STARTER Plan $40 RTR", "billpayment", ""),
    ("Total ALL ACCESS Plan $65", "billpayment", ""),
    ("Total ALL ACCESS Plan $65 RTR", "billpayment", ""),
    ("Total ALL ACCESS 2 Month Plan $130", "billpayment", ""),
    ("Total Wireless 5G Unlimited RTR $55", "billpayment",
     "Sampled at $30.00 against a $55 label — a partial top-up, not a mismatch."),
    ("Total Wireless 5G+ Unlimited RTR $65", "billpayment", ""),
    ("Total Wireless Base 5G Unlimited RTR $40", "billpayment", ""),
    ("Total Wireless Base Unlimited Tablet Plan $50", "billpayment", ""),
    ("Total Wireless Base Unlimited Tablet Plan RTR $50", "billpayment", ""),
    ("Total Wireless Base Unlimited Tablet 3-Month Plan $30", "billpayment", ""),
    ("Total Wireless Base Unlimited Tablet 6-Month Plan $60", "billpayment", ""),
    ("Total Wireless 5G Unlimited Tablet Plan RTR $60", "billpayment", ""),
    ("Total Wireless $50 Data Plan 100GB", "billpayment", ""),
    ("Total Wireless Home Internet", "billpayment", ""),
    ("Total Wireless Home Internet RTR", "billpayment", ""),
    ("Verizon Postpaid Payment", "billpayment", "Bill payment taken at the counter for another brand."),
    ("Simple Mobile RTR $60", "billpayment", "Other-brand airtime sold at the counter."),
    ("Total Wireless Device Upgrade", "billpayment",
     "AMBIGUOUS — sampled at $0.00 with no ' TO' device suffix and no price. Proposed as the upgrade "
     "TRANSACTION line rather than a device sale; please verify before confirming."),
    # ── device sale (the ' TO' order lines carry the handset price) ──
    ("Apple iPhone 16e 128GB Black TO", "device_sale", ""),
    ("Apple iPhone 17e 256GB Black TO", "device_sale", ""),
    ("Samsung Galaxy A16 5G TO", "device_sale", ""),
    ("Samsung Galaxy A17 5G TO", "device_sale", ""),
    ("Samsung Galaxy A26 5G TO", "device_sale", ""),
    ("Samsung Galaxy A36 TO", "device_sale", ""),
    ("Samsung Galaxy A37 5G TO", "device_sale", ""),
    ("Samsung Galaxy S25 FE TO", "device_sale", ""),
    ("Samsung Galaxy Tab A11+ 5G TO", "device_sale", ""),
    ("Motorola Moto G 5G 2026 TO", "device_sale", ""),
    ("Motorola Moto G Power 5G 2026 TO", "device_sale", ""),
    ("Motorola Moto G Stylus 5G 2025 TO", "device_sale", ""),
    ("Motorola Razr 2025 Blue TO", "device_sale", ""),
    ("Motorola Razr 2025 FIFA TO", "device_sale", ""),
    ("Motorola Razr 2025 Teal TO", "device_sale", ""),
    ("Google Pixel 10a TO", "device_sale", ""),
    ("TCL Tab 8 NXTPAPER 5G TO", "device_sale", ""),
    ("TCL Tab 10 NXTPAPER 5G TO", "device_sale", ""),
    ("Home Internet Router TO", "device_sale",
     "Sampled at $0.00 — a bundled router still ships as a device line."),
    # ── protection ──
    ("Total Wireless Protection", "protection", ""),
    ("Total Wireless Protection RTR", "protection", ""),
    ("Total Wireless Protect+", "protection", ""),
    ("Total Wireless Protect+ RTR", "protection", ""),
    # ── financing / subsidy / fee / wallet / sim kit / memo ──
    ("Financing Credit", "financing", ""),
    ("Subsidy", "subsidy", ""),
    ("Invoice Fee", "fee", ""),
    ("Total Wireless RTR Wallet", "wallet", ""),
    ("Total by Verizon SIM Kit", "sim_kit", ""),
    ("Credit Debit Memo", "adjustment_memo",
     "AMBIGUOUS in direction — a memo can be a credit or a debit; the sign on the line decides, and "
     "this module never touches signs."),
]


def _sf(v):
    from app.modules.commcalc.calculator import safe_float
    return safe_float(v)


# ── normalization: TRIM AND NOTHING ELSE ────────────────────────────────────────────────────────────
def normalize(name):
    """The ONLY normalization applied to a product name: strip leading/trailing whitespace. Case is
    preserved and significant. See the module header for why nothing else is allowed."""
    return str(name if name is not None else "").strip()


def classes_from(rows):
    """Effective class vocabulary: the tenant's saved rows, else the built-in default. Each entry is
    {class_key, label, description, sort_order, is_reserved, is_active, source}."""
    out = []
    for r in (rows or []):
        key = normalize(r.get("class_key"))
        if not key or r.get("is_active") is False:
            continue
        out.append({"class_key": key, "label": r.get("label") or key,
                    "description": r.get("description") or "",
                    "sort_order": int(r.get("sort_order") or 500),
                    "is_reserved": bool(r.get("is_reserved")),
                    "is_active": True, "source": "config"})
    if not out:
        out = [{"class_key": k, "label": lab, "description": desc, "sort_order": so,
                "is_reserved": res, "is_active": True, "source": "default"}
               for (k, lab, desc, so, res) in DEFAULT_CLASSES]
    seen, dedup = set(), []
    for c in sorted(out, key=lambda x: (x["sort_order"], x["class_key"])):
        if c["class_key"] in seen:
            continue
        seen.add(c["class_key"])
        dedup.append(c)
    if UNMAPPED not in seen:            # the sentinel is never optional
        dedup.append({"class_key": UNMAPPED, "label": CLASS_LABELS[UNMAPPED],
                      "description": DEFAULT_CLASSES[-1][2], "sort_order": 999,
                      "is_reserved": True, "is_active": True, "source": "reserved"})
    return dedup


def assignable(class_rows):
    return [c["class_key"] for c in class_rows if not c["is_reserved"]]


# ── the index: product_name -> mapping. EXACT after trim; confirmed beats proposed. ─────────────────
def build_index(map_rows, default_proposals=True):
    """{normalized product_name: {product_class, status, note, id, source}}.

    `default_proposals` seeds the built-in proposals for names the tenant has no row for, so the feature
    works the moment the code deploys and BEFORE migration 254 runs (the same fallback discipline
    `commission_ledger.DEFAULT_RULES` uses). Built-ins are always status='proposed', never 'confirmed' —
    only the owner's own confirmation can promote a name, and a saved row always wins over a built-in."""
    idx = {}
    if default_proposals:
        for (name, cls, note) in DEFAULT_PROPOSALS:
            idx[normalize(name)] = {"product_class": cls, "status": "proposed", "note": note,
                                    "id": None, "source": "builtin"}
    for r in (map_rows or []):
        name = normalize(r.get("product_name"))
        if not name:
            continue
        cls = normalize(r.get("product_class"))
        if not cls or cls == UNMAPPED:      # a reserved/blank class is not a mapping
            continue
        status = normalize(r.get("status")).lower() or "proposed"
        if status not in STATUSES:
            status = "proposed"
        prev = idx.get(name)
        # a saved row always beats a built-in; between two saved rows, confirmed beats proposed
        if prev and prev.get("source") == "config" and prev.get("status") == "confirmed" and status != "confirmed":
            continue
        idx[name] = {"product_class": cls, "status": status, "note": r.get("note") or "",
                     "id": r.get("id"), "source": "config",
                     "confirmed_by": r.get("confirmed_by"), "confirmed_at": r.get("confirmed_at")}
    return idx


def classify(product_name, index, include_proposed=True):
    """The class for ONE product name. Returns {product_class, status, note, matched}.

    include_proposed=False is the CONFIRMED-ONLY reading: an unconfirmed proposal counts as unmapped,
    so the preview can show what confirming would change. Unmatched -> 'unmapped' (never a money bucket)."""
    key = normalize(product_name)
    ent = index.get(key)
    if not ent:
        return {"product_class": UNMAPPED, "status": UNMAPPED, "note": "", "matched": False}
    if not include_proposed and ent.get("status") != "confirmed":
        return {"product_class": UNMAPPED, "status": "proposed", "note": ent.get("note") or "",
                "matched": False, "proposed_class": ent.get("product_class")}
    return {"product_class": ent.get("product_class"), "status": ent.get("status"),
            "note": ent.get("note") or "", "matched": True}


# ── month keys (the period-spelling bug class: 'June 2026' vs '2026-06') ────────────────────────────
_MONTHS = {"January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6, "July": 7,
           "August": 8, "September": 9, "October": 10, "November": 11, "December": 12}
_MONTH_NAMES = {v: k for k, v in _MONTHS.items()}
NO_MONTH = "(no period)"


def month_key(period, date_value=None):
    """Canonical 'YYYY-MM' for a row, from either period spelling, falling back to the row's own date
    column. Returns NO_MONTH when neither is usable — surfaced, never silently folded into a real month."""
    p = normalize(period)
    parts = p.split()
    if len(parts) == 2 and parts[0] in _MONTHS and parts[1].isdigit():
        return "%s-%02d" % (parts[1], _MONTHS[parts[0]])
    if len(p) >= 7 and p[:4].isdigit() and p[4] == "-" and p[5:7].isdigit():
        return p[:7]
    d = normalize(date_value)
    if len(d) >= 7 and d[:4].isdigit() and d[4] == "-" and d[5:7].isdigit():
        return d[:7]
    return NO_MONTH


def month_label(key):
    """'2026-06' -> 'June 2026'. Unparseable keys pass through unchanged."""
    k = normalize(key)
    if len(k) == 7 and k[:4].isdigit() and k[4] == "-" and k[5:7].isdigit():
        m = int(k[5:7])
        if 1 <= m <= 12:
            return "%s %s" % (_MONTH_NAMES[m], k[:4])
    return k


# ── observed distinct names (what the admin grid lists) ─────────────────────────────────────────────
def observed(rows, index, amount_column="retail_cost", date_column="tx_date", period_column="period",
             name_column="product_name"):
    """Aggregate raw source rows into one entry per DISTINCT (trimmed) product name.

    Amounts are summed EXACTLY AS STORED — signed, untouched. The export's convention is that a negative
    is money paid TO the dealer; normalizing that is `whatif._ma_commission_sign`'s job, not this
    module's, and doing it here would double-normalize downstream. `sign` reports what was seen."""
    agg = {}
    for r in (rows or []):
        name = normalize(r.get(name_column))
        amt = _sf(r.get(amount_column))
        a = agg.get(name)
        if a is None:
            a = agg[name] = {"product_name": name, "lines": 0, "total": 0.0,
                             "negatives": 0, "positives": 0, "zeros": 0,
                             "min": None, "max": None, "months": set(), "raw_variants": set(),
                             "first_seen": None, "last_seen": None}
        a["lines"] += 1
        a["total"] += amt
        a["negatives" if amt < 0 else ("positives" if amt > 0 else "zeros")] += 1
        a["min"] = amt if a["min"] is None else min(a["min"], amt)
        a["max"] = amt if a["max"] is None else max(a["max"], amt)
        a["months"].add(month_key(r.get(period_column), r.get(date_column)))
        raw = r.get(name_column)
        if raw is not None and str(raw) != name:
            a["raw_variants"].add(str(raw))
        d = normalize(r.get(date_column))
        if d:
            a["first_seen"] = d if not a["first_seen"] else min(a["first_seen"], d)
            a["last_seen"] = d if not a["last_seen"] else max(a["last_seen"], d)
    out = []
    for name, a in agg.items():
        c = classify(name, index, include_proposed=True)
        neg, pos = a["negatives"], a["positives"]
        sign = "negative" if (neg and not pos) else ("positive" if (pos and not neg) else
                                                    ("mixed" if (neg and pos) else "zero"))
        out.append({"product_name": name,
                    "product_class": c["product_class"], "status": c["status"],
                    "note": c["note"], "matched": c["matched"],
                    "lines": a["lines"], "total": round(a["total"], 2),
                    "min": round(a["min"], 2) if a["min"] is not None else None,
                    "max": round(a["max"], 2) if a["max"] is not None else None,
                    "sign": sign, "negatives": neg, "positives": pos, "zeros": a["zeros"],
                    "months": sorted(m for m in a["months"] if m != NO_MONTH),
                    "month_count": len(a["months"]),
                    "raw_variants": sorted(a["raw_variants"]),
                    "first_seen": a["first_seen"], "last_seen": a["last_seen"]})
    out.sort(key=lambda x: (0 if x["product_class"] == UNMAPPED else 1,
                            -abs(x["total"]), x["product_name"]))
    return out


def unmapped_summary(observed_rows):
    """The LOUD unmapped block: how many lines, how many dollars, and which names."""
    names = [o for o in observed_rows if o["product_class"] == UNMAPPED]
    return {"names": len(names), "lines": sum(o["lines"] for o in names),
            "total": round(sum(o["total"] for o in names), 2),
            "detail": [{"product_name": o["product_name"], "lines": o["lines"],
                        "total": o["total"], "sign": o["sign"]} for o in names[:200]],
            "truncated": max(0, len(names) - 200)}


# ── the IMPACT PREVIEW (read-only: what the classification WOULD reclassify) ────────────────────────
def preview(rows, index, amount_column="retail_cost", date_column="tx_date", period_column="period",
            name_column="product_name"):
    """Per class per month: line count + signed total, computed TWICE.

      · 'confirmed' — only owner-CONFIRMED mappings count; everything else is 'unmapped'.
      · 'proposed'  — confirmed + proposed count. This is what the file would look like if the owner
                      confirmed every proposal as-is.

    The pair IS the impact preview: the delta between the two modes is exactly what confirming buys.
    NOTHING here is written, and no consumer of money reads this — see the module header."""
    modes = {"confirmed": False, "proposed": True}
    out = {}
    months = set()
    for mode, include in modes.items():
        by_month, by_class = {}, {}
        lines = 0
        total = 0.0
        for r in (rows or []):
            name = r.get(name_column)
            amt = _sf(r.get(amount_column))
            mk = month_key(r.get(period_column), r.get(date_column))
            months.add(mk)
            cls = classify(name, index, include_proposed=include)["product_class"]
            m = by_month.setdefault(mk, {})
            c = m.setdefault(cls, {"lines": 0, "total": 0.0})
            c["lines"] += 1
            c["total"] += amt
            g = by_class.setdefault(cls, {"lines": 0, "total": 0.0})
            g["lines"] += 1
            g["total"] += amt
            lines += 1
            total += amt
        for m in by_month.values():
            for c in m.values():
                c["total"] = round(c["total"], 2)
        for g in by_class.values():
            g["total"] = round(g["total"], 2)
        out[mode] = {"by_month": by_month, "by_class": by_class,
                     "line_count": lines, "total": round(total, 2),
                     "unmapped_lines": by_class.get(UNMAPPED, {}).get("lines", 0),
                     "unmapped_total": by_class.get(UNMAPPED, {}).get("total", 0.0)}
    ordered = sorted(months, key=lambda m: (m == NO_MONTH, m))
    out["months"] = [{"key": m, "label": month_label(m)} for m in ordered]
    out["classes_present"] = sorted(set(out["confirmed"]["by_class"]) | set(out["proposed"]["by_class"]))
    out["delta"] = {
        "lines_newly_classified": (out["confirmed"]["unmapped_lines"] - out["proposed"]["unmapped_lines"]),
        "dollars_newly_classified": round(out["confirmed"]["unmapped_total"]
                                          - out["proposed"]["unmapped_total"], 2),
    }
    return out


def seed_rows(org_id, source_report="ma_daily_tx", existing_names=()):
    """The built-in proposals as INSERTABLE rows for one tenant, skipping names already mapped.
    org_id is an explicit argument and is stamped on every row (RULE ONE: config rows carry the tenant)."""
    have = {normalize(n) for n in (existing_names or ())}
    return [{"org_id": org_id, "source_report": source_report, "product_name": normalize(name),
             "product_class": cls, "status": "proposed", "note": note or None}
            for (name, cls, note) in DEFAULT_PROPOSALS if normalize(name) not in have]
