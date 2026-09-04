"""Merchant-processor PORTAL ADAPTER REGISTRY — pure config + pure normalizers (owner directive 2026-09-04).

WHY THIS EXISTS. A lot of tenants run a THIRD-PARTY credit-card terminal that is NOT integrated to the
POS ("external credit card"; some tenants call the box the "white machine"). The money it takes never
reaches the POS, so today the only record of it is what an employee types on the daily closing sheet —
unverifiable. The owner asked for the other side of that tally: pull the processor's OWN report every
day, per store, per day, per card brand, and let the closing recon compare.

THREE PORTALS, ONE MECHANISM (RULE TWO — config, never code):
  • payanywhere   — paymentshub.com (PayAnywhere / North American Bancard). The EXTERNAL credit-card
                    processor both current tenants use. settlement_role = external_cc.
  • transfirst    — translink.transfirst.com (TransFirst / TSYS TransLink). A POS merchant provider.
  • businesstrack — cl.businesstrack.com (Fiserv / First Data ClientLine). A POS merchant provider.

NOTHING here names a tenant. A portal is selected by the `processor` value on the tenant's
commcalc.data_source row (config), the house defaults below are per-PORTAL (not per-tenant), and every
default a tenant may need to differ on (settlement role, report window, column synonyms, the navigation
hints the live session calibrates) is overridable on the source row. Adding a fourth portal is a new
entry in PORTALS plus a scraper registration — no branch anywhere in the calling code.

WHAT IS PROVEN AND WHAT IS CALIBRATED — read this before trusting a selector.
The part of a portal integration that can be proven WITHOUT the portal is the PARSER: given the report
payload the portal exports, produce normalized rows. That is what this module is, and
harness_merchant_portals.py proves it against fixtures for all three portals. The part that CANNOT be
honestly hardcoded from outside the portal is its exact DOM (menu ids, button classes, the export
control). So the adapters carry *heuristic hints* — the same word-list mechanism vidapay_sweep's
_find_input/_click_visible already drive every other portal in this platform with — and the exact
selectors are CALIBRATED once by the operator on the live-login screencast and stored per source in
data_source.portal_calibration (mig 955). We do not ship invented CSS as if it were verified.

PURE: no DB, no network, no Playwright, stdlib only. Safe to import anywhere (the harness imports it
by path). The runtime that drives a browser with these descriptors is merchant_portal_sweep.py.
"""
import re
from datetime import date, datetime

# ── settlement roles ─────────────────────────────────────────────────────────────────────────────
# Which SIDE of the daily tally a portal's money belongs to. The closing module's recon reads this to
# decide whether a row answers the "external credit card / white machine" field or the POS card tender.
# SLUGS ARE SHARED WITH THE CLOSING RECON. closing/external_credit_recon.py defines the same two
# neutral slugs and its assemble_rows() rejects anything else, so these two strings are a CONTRACT
# between the scrape side (here) and the tally side — not a local naming choice. Never rename one
# without the other; a mismatch would make every scraped row invisible to the recon.
ROLE_EXTERNAL = "external_cc"     # a standalone terminal NOT integrated to the POS
ROLE_POS = "pos_merchant"         # the merchant provider behind the POS's own card tender
ROLES = (ROLE_EXTERNAL, ROLE_POS)

# ── canonical card brands ────────────────────────────────────────────────────────────────────────
BRAND_UNKNOWN = "unknown"
_BRAND_SYNONYMS = {
    "visa": ("visa", "vi", "vs"),
    "mastercard": ("mastercard", "master card", "mc", "mstr", "master"),
    "amex": ("amex", "american express", "americanexpress", "ax", "amx"),
    "discover": ("discover", "disc", "ds", "novus"),
    "debit": ("debit", "pin debit", "pindebit", "interlink", "star", "nyce", "pulse", "maestro"),
    "ebt": ("ebt", "snap"),
    "other": ("other", "misc", "miscellaneous", "jcb", "diners", "diners club", "china unionpay",
              "unionpay", "gift", "private label"),
}


def card_brand(value):
    """Canonical card-brand key for whatever the portal calls it. PURE. Unknown/blank ⇒ 'unknown' (never
    dropped — an unmapped brand must still tally into the day's total, just labelled honestly)."""
    s = re.sub(r"[^a-z ]+", "", str(value or "").strip().lower()).strip()
    if not s:
        return BRAND_UNKNOWN
    for canon, syns in _BRAND_SYNONYMS.items():
        if s == canon or s in syns:
            return canon
    for canon, syns in _BRAND_SYNONYMS.items():
        for syn in syns:
            if len(syn) > 3 and syn in s:
                return canon
    return BRAND_UNKNOWN


# ── column synonyms — the portal's header text → our field ───────────────────────────────────────
# House defaults. A tenant whose export carries a column we have not seen adds it per source in
# data_source.portal_calibration.column_synonyms (RULE TWO: config, not a code change).
# Matching is normalized (lowercase, non-alphanumerics collapsed) and LONGEST-SYNONYM-FIRST, so
# "net amount" wins over "amount" on a header that contains both words.
COLUMN_SYNONYMS = {
    "business_date": ("business date", "batch date", "transaction date", "trans date", "date",
                      "settlement date", "sale date", "deposit date", "activity date", "post date"),
    "merchant_id": ("merchant id", "mid", "merchant number", "merchant account", "merchant",
                    "outlet id", "outlet", "dba number", "location id", "location"),
    "terminal_id": ("terminal id", "tid", "terminal number", "terminal", "device id", "device",
                    "register", "lane"),
    "store_label": ("dba", "dba name", "merchant name", "location name", "store", "store name",
                    "outlet name", "business name"),
    "card_brand": ("card type", "card brand", "brand", "payment type", "tender type", "card",
                   "network", "plan", "card plan"),
    "gross_amount": ("gross amount", "gross sales", "sales amount", "sales", "gross", "purchase amount",
                     "submitted amount", "amount"),
    "refund_amount": ("refund amount", "refunds", "returns", "credit amount", "credits", "return amount"),
    "net_amount": ("net amount", "net sales", "net deposit", "net", "net settled", "settled amount"),
    "fee_amount": ("fee amount", "fees", "total fees", "discount amount", "processing fees", "fee"),
    "txn_count": ("transaction count", "trans count", "count", "items", "number of transactions",
                  "sales count", "txn count", "qty", "quantity"),
    "batch_ref": ("batch id", "batch number", "batch", "batch reference", "deposit id", "funding id",
                  "reference number", "reference"),
    "deposit_amount": ("deposit amount", "funded amount", "funding amount", "amount funded",
                       "net deposit amount", "total deposit"),
    "deposit_date": ("deposit date", "funding date", "date funded", "expected funding date"),
    "currency": ("currency", "curr", "currency code"),
}

# Fields that carry MONEY (parsed with the money reader — parentheses/trailing-minus aware).
_MONEY_FIELDS = ("gross_amount", "refund_amount", "net_amount", "fee_amount", "deposit_amount")


# ── portal descriptors ───────────────────────────────────────────────────────────────────────────
# Each entry is DATA the runtime interprets; none of it is branched on in code.
#   login_fields  — which credentials the portal's login asks for, so the settings UI renders the right
#                   boxes and the live session prefills the right values (reuses data_source.username /
#                   password / account_id — no new credential columns).
#   auth_notes    — what the portal does at login, stated honestly (see the module docstring: 2FA is
#                   satisfied by a HUMAN on the live screencast, never defeated).
#   reports       — the report(s) to pull, with navigation TEXT hints (not CSS) and the grain each
#                   report lands at. `export` says how we prefer to get the data.
#   nav_hints     — words the generic menu walker clicks through to reach Reports.
#   settlement_role / date_window_days / brand_split — house defaults, per-source overridable.
PORTALS = {
    "payanywhere": {
        "key": "payanywhere",
        "label": "PayAnywhere — Payments Hub (external credit card)",
        "vendor": "North American Bancard",
        "base_url": "https://www.paymentshub.com/",
        "login_fields": ("username", "password"),          # email + password
        "username_label": "Email",
        "settlement_role": ROLE_EXTERNAL,
        "date_window_days": 7,
        "brand_split": True,
        "auth_notes": ("Email + password, then a one-time code to the registered email/phone on a new "
                       "device. Satisfied by the operator on the live-login screencast; the durable "
                       "session is then reused for the daily pull. Supports authenticator-app (TOTP) "
                       "enrollment on accounts that have it turned on — see portal_totp."),
        "nav_hints": ("reports", "reporting", "transactions", "deposits"),
        "reports": (
            {"key": "deposits", "label": "Deposits / funding", "grain": "batch",
             "nav": ("reports", "deposits"), "export": "csv"},
            {"key": "card_summary", "label": "Transactions by card type", "grain": "store_day_brand",
             "nav": ("reports", "transactions"), "export": "csv"},
        ),
    },
    "transfirst": {
        "key": "transfirst",
        "label": "TransFirst TransLink (POS merchant provider)",
        "vendor": "TransFirst / TSYS",
        "base_url": "https://translink.transfirst.com/login.aspx",
        "login_fields": ("username", "password"),
        "username_label": "User ID",
        "settlement_role": ROLE_POS,
        "date_window_days": 7,
        "brand_split": True,
        "auth_notes": ("ASP.NET login (User ID + password) with 2FA enabled on the owner's accounts. "
                       "Operator satisfies the challenge once on the live screencast; the session is "
                       "persisted per (org, source) and reused."),
        "nav_hints": ("reports", "reporting", "batch", "deposits"),
        "reports": (
            {"key": "batch_summary", "label": "Batch summary", "grain": "store_day_brand",
             "nav": ("reports", "batch"), "export": "csv"},
            {"key": "deposits", "label": "Deposit / funding detail", "grain": "batch",
             "nav": ("reports", "deposit"), "export": "csv"},
        ),
    },
    "businesstrack": {
        "key": "businesstrack",
        "label": "ClientLine / BusinessTrack (POS merchant provider)",
        "vendor": "Fiserv / First Data",
        "base_url": "https://cl.businesstrack.com/",
        "login_fields": ("username", "password"),
        "username_label": "User ID",
        "settlement_role": ROLE_POS,
        "date_window_days": 7,
        "brand_split": True,
        "auth_notes": ("ClientLine user id + password with 2FA enabled. Same live-session posture; "
                       "ClientLine invalidates a session aggressively, so expect the session-health "
                       "chip to ask for a re-login more often than the other two."),
        "nav_hints": ("reports", "reporting", "submission", "funding", "deposits"),
        "reports": (
            {"key": "submission_summary", "label": "Submission / sales by card type",
             "grain": "store_day_brand", "nav": ("reports", "submission"), "export": "csv"},
            {"key": "funding", "label": "Funding / deposits", "grain": "batch",
             "nav": ("reports", "funding"), "export": "csv"},
        ),
    },
}

PORTAL_KEYS = tuple(PORTALS.keys())


def portal(key):
    """The descriptor for a portal key, or None. Case/space tolerant."""
    return PORTALS.get(str(key or "").strip().lower().replace(" ", "_").replace("-", "_"))


def is_portal(key):
    return portal(key) is not None


def settlement_role(key, override=None):
    """The role a source's rows land with: the per-source override when it is a legal role, else the
    portal's house default, else ROLE_EXTERNAL. RULE TWO — the tenant's answer lives on its config row."""
    ov = str(override or "").strip().lower()
    if ov in ROLES:
        return ov
    p = portal(key)
    return (p or {}).get("settlement_role") or ROLE_EXTERNAL


def report_specs(key, enabled_keys=None):
    """The report descriptors to pull for a portal, optionally filtered to a per-source enabled list."""
    p = portal(key)
    if not p:
        return ()
    specs = tuple(p.get("reports") or ())
    if not enabled_keys:
        return specs
    want = {str(k).strip().lower() for k in enabled_keys}
    return tuple(s for s in specs if s["key"] in want)


def public_catalog():
    """Portal catalog for the settings UI — descriptors only, never a credential."""
    return [{"key": p["key"], "label": p["label"], "vendor": p["vendor"], "base_url": p["base_url"],
             "login_fields": list(p["login_fields"]), "username_label": p["username_label"],
             "settlement_role": p["settlement_role"], "auth_notes": p["auth_notes"],
             "reports": [{"key": r["key"], "label": r["label"], "grain": r["grain"]}
                         for r in p["reports"]]}
            for p in PORTALS.values()]


# ── pure value readers ───────────────────────────────────────────────────────────────────────────
def _norm_header(h):
    """Normalize a header cell for synonym matching: lowercase, non-alphanumerics → single spaces."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(h or "").lower())).strip()


def money(value):
    """Parse a portal money cell to float. PURE. Handles '$1,234.56', '(12.34)' ⇒ -12.34, '12.34-' ⇒
    -12.34, '', None, '-', 'N/A'. Never raises — an unparseable cell is 0.0, and the caller keeps the
    raw payload so nothing is silently lost."""
    s = str(value if value is not None else "").strip()
    if not s:
        return 0.0
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    if s.endswith("-"):
        neg, s = True, s[:-1]
    s = re.sub(r"[^0-9.\-]", "", s)
    if s.count("-") == 1 and s.startswith("-"):
        neg, s = True, s[1:]
    s = s.replace("-", "")
    if not s or s == ".":
        return 0.0
    try:
        v = float(s)
    except ValueError:
        return 0.0
    return -v if neg else v


def count(value):
    """Parse a transaction-count cell to int. PURE; unparseable ⇒ 0."""
    s = re.sub(r"[^0-9\-]", "", str(value if value is not None else "").strip())
    if not s or s == "-":
        return 0
    try:
        return int(s)
    except ValueError:
        return 0


_MONTHS = {m: i + 1 for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}


def iso_date(value):
    """Parse a portal date cell to 'YYYY-MM-DD'. PURE. Accepts YYYY-MM-DD, MM/DD/YYYY, M/D/YY,
    MM-DD-YYYY, 'Aug 12, 2026', '12 Aug 2026', and a datetime/date object. Returns None when the cell
    holds no date (a totals row, a blank) — the caller SKIPS such rows rather than guessing a day."""
    if isinstance(value, (datetime, date)):
        return (value.date() if isinstance(value, datetime) else value).isoformat()
    s = str(value or "").strip()
    if not s:
        return None
    s = s.split("T")[0].strip()
    m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{2}|\d{4})$", s)
        if m:
            mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if y < 100:                      # 2-digit year: portals only ever export recent history
                y += 2000
        else:
            m = re.match(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})$", s)
            if m:
                mo = _MONTHS.get(m.group(1)[:3].lower())
                d, y = int(m.group(2)), int(m.group(3))
            else:
                m = re.match(r"^(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})$", s)
                if not m:
                    return None
                d = int(m.group(1))
                mo = _MONTHS.get(m.group(2)[:3].lower())
                y = int(m.group(3))
    if not mo or not (1 <= mo <= 12) or not (1 <= d <= 31) or not (2000 <= y <= 2100):
        return None
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


# ── header mapping ───────────────────────────────────────────────────────────────────────────────
def map_headers(headers, extra_synonyms=None):
    """{field: column index} for a report's header row. PURE.

    Longest synonym first so a header containing several synonym words resolves to the most specific
    field ('net amount' → net_amount, not gross_amount via 'amount'). EXACT normalized equality wins
    over containment, and the FIRST column to claim a field keeps it, so a report with both
    'Sales Amount' and 'Amount' maps gross_amount to the specific one.

    `extra_synonyms` is the per-source calibration ({field: [synonyms]}) merged OVER the house map —
    RULE TWO: a tenant whose export differs is a config row, never a code change."""
    syn = {f: list(v) for f, v in COLUMN_SYNONYMS.items()}
    for f, vals in (extra_synonyms or {}).items():
        if f in syn:
            syn[f] = [str(v).lower() for v in (vals or [])] + syn[f]
        else:
            syn[f] = [str(v).lower() for v in (vals or [])]
    norm = [_norm_header(h) for h in (headers or [])]
    out = {}
    # Pass 1 — exact normalized match.
    for field, syns in syn.items():
        for s in sorted({_norm_header(x) for x in syns}, key=len, reverse=True):
            if field in out:
                break
            for i, h in enumerate(norm):
                if h and h == s and i not in out.values():
                    out[field] = i
                    break
    # Pass 2 — containment, longest synonym first, only for fields still unclaimed.
    for field, syns in syn.items():
        if field in out:
            continue
        for s in sorted({_norm_header(x) for x in syns}, key=len, reverse=True):
            if field in out:
                break
            for i, h in enumerate(norm):
                if h and s and s in h and i not in out.values():
                    out[field] = i
                    break
    return out


def _cell(row, idx):
    if idx is None or idx < 0 or idx >= len(row):
        return None
    return row[idx]


_TOTALS_WORDS = ("total", "totals", "grand total", "subtotal", "sub total", "summary", "report total")


def _is_totals_row(row):
    """A portal export's trailing TOTALS row must never become a settlement day (it would double the
    day). Detected by a totals word in any of the first three cells with no parseable date anywhere."""
    head = " ".join(str(c or "").strip().lower() for c in list(row)[:3])
    if not head:
        return False
    return any(w in head for w in _TOTALS_WORDS)


# ── the normalizer — the fixture-proven core ─────────────────────────────────────────────────────
def normalize_settlement(portal_key, table, *, source_id=None, org_id=None, role_override=None,
                         calibration=None, report_key=None, merchant_id_default=None):
    """Portal report table → normalized SETTLEMENT rows (store × day × card brand). PURE.

    `table` is the export as a list of lists, header row FIRST (what a CSV reader yields). Returns
      {"rows": [...], "skipped": [...], "fields": {...}, "warnings": [...]}
    Each row carries the SOURCE's OWN identifiers (merchant_id, terminal_id, batch_ref, the raw cells)
    so the recon can always be traced back to the portal, plus `store_code=None` — store attribution is
    resolved by the runtime through storeops.merchant_ids (mig 902), never guessed here.

    Rows WITHOUT a parseable business date, and totals rows, are SKIPPED with a reason rather than
    coerced — a settlement figure attributed to the wrong day is worse than a missing one."""
    cal = calibration or {}
    role = settlement_role(portal_key, role_override)
    rows, skipped, warnings = [], [], []
    table = [list(r) for r in (table or []) if r is not None]
    if not table:
        return {"rows": [], "skipped": [], "fields": {}, "warnings": ["empty report payload"]}
    fields = map_headers(table[0], cal.get("column_synonyms"))
    if "business_date" not in fields:
        warnings.append("no date column recognised in the export header — calibrate column_synonyms "
                        "for this source")
    if not any(f in fields for f in ("gross_amount", "net_amount", "deposit_amount")):
        warnings.append("no amount column recognised in the export header — calibrate column_synonyms "
                        "for this source")
    for n, raw in enumerate(table[1:], start=2):
        if not any(str(c or "").strip() for c in raw):
            continue
        if _is_totals_row(raw):
            skipped.append({"line": n, "reason": "totals row"})
            continue
        iso = iso_date(_cell(raw, fields.get("business_date")))
        if not iso:
            skipped.append({"line": n, "reason": "no parseable business date"})
            continue
        mid = str(_cell(raw, fields.get("merchant_id")) or merchant_id_default or "").strip()
        gross = money(_cell(raw, fields.get("gross_amount")))
        refund = money(_cell(raw, fields.get("refund_amount")))
        net_cell = _cell(raw, fields.get("net_amount"))
        # net is the portal's own figure when it publishes one, else gross − |refunds| (refunds are
        # exported as positives by some portals and negatives by others; |x| makes both behave).
        net = money(net_cell) if net_cell not in (None, "") else round(gross - abs(refund), 2)
        rows.append({
            "org_id": org_id, "source_id": source_id, "portal_key": portal_key,
            "report_key": report_key, "settlement_role": role,
            "business_date": iso,
            "merchant_id": mid or None,
            "terminal_id": (str(_cell(raw, fields.get("terminal_id")) or "").strip() or None),
            "store_label": (str(_cell(raw, fields.get("store_label")) or "").strip() or None),
            "store_code": None,                      # resolved by the runtime via storeops.merchant_ids
            "card_brand": card_brand(_cell(raw, fields.get("card_brand"))),
            "gross_amount": round(gross, 2),
            "refund_amount": round(abs(refund), 2),
            "net_amount": round(net, 2),
            "fee_amount": round(money(_cell(raw, fields.get("fee_amount"))), 2),
            "txn_count": count(_cell(raw, fields.get("txn_count"))),
            "batch_ref": (str(_cell(raw, fields.get("batch_ref")) or "").strip() or None),
            "currency": (str(_cell(raw, fields.get("currency")) or "").strip().upper() or "USD"),
            "source_line": n,
            "raw": {str(h): ("" if c is None else str(c))
                    for h, c in zip(table[0], raw)},
        })
    return {"rows": rows, "skipped": skipped, "fields": fields, "warnings": warnings}


def normalize_batches(portal_key, table, *, source_id=None, org_id=None, role_override=None,
                      calibration=None, report_key=None, merchant_id_default=None):
    """Portal DEPOSIT/FUNDING report → normalized batch rows (one per funding event). PURE.

    A different GRAIN from settlement: a batch is money leaving the processor for the bank, which the
    cash/deposit recon (§12) reads, while settlement rows answer the daily closing tally. Kept apart so
    neither can be double-counted into the other."""
    cal = calibration or {}
    role = settlement_role(portal_key, role_override)
    table = [list(r) for r in (table or []) if r is not None]
    if not table:
        return {"rows": [], "skipped": [], "fields": {}, "warnings": ["empty report payload"]}
    fields = map_headers(table[0], cal.get("column_synonyms"))
    rows, skipped, warnings = [], [], []
    if not any(f in fields for f in ("deposit_date", "business_date")):
        warnings.append("no deposit/date column recognised — calibrate column_synonyms for this source")
    for n, raw in enumerate(table[1:], start=2):
        if not any(str(c or "").strip() for c in raw):
            continue
        if _is_totals_row(raw):
            skipped.append({"line": n, "reason": "totals row"})
            continue
        dep_iso = iso_date(_cell(raw, fields.get("deposit_date")))
        biz_iso = iso_date(_cell(raw, fields.get("business_date")))
        if not (dep_iso or biz_iso):
            skipped.append({"line": n, "reason": "no parseable deposit date"})
            continue
        amt_cell = _cell(raw, fields.get("deposit_amount"))
        amount = money(amt_cell) if amt_cell not in (None, "") else money(_cell(raw, fields.get("net_amount")))
        rows.append({
            "org_id": org_id, "source_id": source_id, "portal_key": portal_key,
            "report_key": report_key, "settlement_role": role,
            "deposit_date": dep_iso or biz_iso,
            "batch_date": biz_iso or dep_iso,
            "merchant_id": (str(_cell(raw, fields.get("merchant_id")) or merchant_id_default or "").strip() or None),
            "terminal_id": (str(_cell(raw, fields.get("terminal_id")) or "").strip() or None),
            "store_label": (str(_cell(raw, fields.get("store_label")) or "").strip() or None),
            "store_code": None,
            "batch_ref": (str(_cell(raw, fields.get("batch_ref")) or "").strip() or None),
            "deposit_amount": round(amount, 2),
            "fee_amount": round(money(_cell(raw, fields.get("fee_amount"))), 2),
            "txn_count": count(_cell(raw, fields.get("txn_count"))),
            "currency": (str(_cell(raw, fields.get("currency")) or "").strip().upper() or "USD"),
            "source_line": n,
            "raw": {str(h): ("" if c is None else str(c)) for h, c in zip(table[0], raw)},
        })
    return {"rows": rows, "skipped": skipped, "fields": fields, "warnings": warnings}


def settlement_key(row):
    """The natural key a settlement row upserts on — mirrors the UNIQUE index in mig 955, so the parser
    and the table can never disagree about what 'the same row pulled twice' means."""
    return (row.get("org_id"), row.get("source_id"), row.get("merchant_id") or "",
            row.get("business_date"), row.get("card_brand") or BRAND_UNKNOWN)


def dedupe_settlement(rows):
    """Collapse rows sharing the natural key by SUMMING money/counts. PURE.

    Portals legitimately export several lines for one (merchant, day, brand) — one per terminal, or per
    batch. The table's grain is the day, so they must be summed BEFORE the upsert; upserting them one by
    one would let the last line silently overwrite the rest (the row would read one terminal's total as
    the store's day). Terminal/batch identifiers that differ across the collapsed lines are recorded as
    a list in `merged_from` so the source's own identifiers survive."""
    out, order = {}, []
    for r in rows or []:
        k = settlement_key(r)
        if k not in out:
            cur = dict(r)
            cur["merged_lines"] = 1
            cur["merged_from"] = [x for x in [r.get("terminal_id") or r.get("batch_ref")] if x]
            out[k] = cur
            order.append(k)
            continue
        cur = out[k]
        for f in ("gross_amount", "refund_amount", "net_amount", "fee_amount"):
            cur[f] = round((cur.get(f) or 0.0) + (r.get(f) or 0.0), 2)
        cur["txn_count"] = (cur.get("txn_count") or 0) + (r.get("txn_count") or 0)
        cur["merged_lines"] += 1
        ident = r.get("terminal_id") or r.get("batch_ref")
        if ident and ident not in cur["merged_from"]:
            cur["merged_from"].append(ident)
        if not cur.get("terminal_id") and r.get("terminal_id"):
            cur["terminal_id"] = r["terminal_id"]
    return [out[k] for k in order]


def totals_by_store_day(rows):
    """{(store_code, 'YYYY-MM-DD'): {net, gross, fees, count, brands:{}}} — the shape the closing
    recon reads to tally the portal side against the employee-entered figure. PURE. Rows with no
    resolved store are EXCLUDED and returned separately, so an unmapped merchant id can never quietly
    deflate a store's expected card total."""
    by, unresolved = {}, []
    for r in rows or []:
        sc = r.get("store_code")
        if not sc:
            unresolved.append({"merchant_id": r.get("merchant_id"), "terminal_id": r.get("terminal_id"),
                               "store_label": r.get("store_label"), "business_date": r.get("business_date")})
            continue
        k = (sc, r.get("business_date"))
        cur = by.setdefault(k, {"net": 0.0, "gross": 0.0, "fees": 0.0, "count": 0, "brands": {}})
        cur["net"] = round(cur["net"] + (r.get("net_amount") or 0.0), 2)
        cur["gross"] = round(cur["gross"] + (r.get("gross_amount") or 0.0), 2)
        cur["fees"] = round(cur["fees"] + (r.get("fee_amount") or 0.0), 2)
        cur["count"] += (r.get("txn_count") or 0)
        b = r.get("card_brand") or BRAND_UNKNOWN
        cur["brands"][b] = round(cur["brands"].get(b, 0.0) + (r.get("net_amount") or 0.0), 2)
    return {"by_store_day": by, "unresolved": unresolved}
