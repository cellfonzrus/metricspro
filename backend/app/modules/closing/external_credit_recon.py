"""CARD-SETTLEMENT TALLY — declared closing figures vs the processors' scraped totals. PURE logic
(owner directive 2026-09-04; migs 960/961).

The owner's words, verbatim: "a lot of tenants will be using 3rd party credit card processor which
is not integrated to the pos, which is recorded as external credit card, we need to pull in data
from the merchants from both pos merchant provider and the external credit card provider … need to
scrape the reports on a daily basis and tally with our platform as entered by the employees … need
to add another field on daily closing as external credit machine".

WHAT THIS MODULE IS
-------------------
The TALLY half. Per (store, day, processor role): what the employee DECLARED at closing vs what the
processor actually SETTLED, with the variance and a short/over/match verdict. The scraping half
(portal logins, 2FA sessions, the daily pull) is a separate module; this one only consumes whatever
that pull stored.

WHAT ALREADY EXISTED AND IS REUSED HERE, NOT REBUILT (duplicate-check gate)
--------------------------------------------------------------------------
  • THE FIELD.  `commcalc.daily_closing.t_ext_cc` — "External Credit Card (separate terminal)" —
    since mig `103`. Written by the closing submit form / POST /closing/row, summed by
    `/closing/summary` + `/closing/submissions`, already inside the card base of the mig-939
    coverage recon and the mig-944 3-way recon. No new column was created for this work.
  • THE SHORT/OVER TRUTH TABLE.  `closing/envelope_report.count_fields` (mig 936) — the platform's
    ONE counted-vs-expected classifier, already reused by `closing/pickup_actual.py` (mig 949).
    `recon_row` below calls it; there is no second classifier anywhere in this file.
  • THE LABEL.  The mig-945/953 carrier label PRESET machinery on `commcalc.ui_label_override`
    (`report_labels.LABELABLE_COLUMNS` key `closing_t_ext_cc`; mig 960 seeds the house carrier
    presets as DATA, and an org with no preset renders the built-in default wording). No second
    labelling mechanism, and no tenant/carrier/brand name appears anywhere in this module.
  • MERCHANT ID → STORE.  `storeops.store_merchant_id` (mig `902`, free-form `processor` key) —
    the same resolution the ePay/VidaPay feeds use. `settlement_cells` takes that map as a
    parameter; it never derives its own.
  • WHERE THE SCRAPED ROWS LIVE.  `commcalc.report_pull_map` (mig `207`: report_key →
    `target_table` + `column_map`, org row overriding the house row). The caller resolves the feed
    through it and hands the rows here already read — so NO table name and NO column name is
    hardcoded in this module or in the endpoint. Mig `955` (the portal-scrape side) seeds the house
    row `SETTLEMENT_REPORT_KEY` pointing at `commcalc.merchant_settlement_day`, whose
    `settlement_role` column carries the SAME two role slugs defined below. A portal that renames a
    column is a config edit on that one row — no change on either side.
  • THE DM's CORRECTION.  `closing/verified_overlay` + `closing/verification_audit` (migs 935/961).

PROCESSOR ROLES ARE NEUTRAL SLUGS, NEVER BRANDS (RULE TWO)
----------------------------------------------------------
`external_cc` = the standalone third-party credit machine the POS does not integrate;
`pos_merchant` = the POS-integrated card processor. WHICH vendor sits behind a role is data —
`commcalc.data_source.processor`, `storeops.store_merchant_id.processor`, `report_pull_map` — and
what a tenant CALLS the field is the mig-960 label preset. Neither ever appears in code.

THE DM-CORRECTED DAY (the honest-gap rule that matters most here)
-----------------------------------------------------------------
On a store-day the DM verified WITH a card correction, `dm_store_cc` is one COMBINED card total.
Pre-961 the split was destroyed (`t_ext_cc` zeroed), so a naive tally would have called the whole
external figure SHORT — a fabricated variance. Mig 961 lets the DM state `dm_ext_cc` (the external
portion OF that same total, so the total never moves). When the DM corrected the card total and did
NOT state the split, this module reports basis `dm_merged` and status `dm_merged` — never a number
it cannot evidence. Same posture as `no_pos_data` / `no_processor_data` elsewhere in closing.

Everything here is PURE (rows in, dicts out — stdlib only). Proof:
backend/harness_external_credit_recon.py.
"""

from . import envelope_report as _envelope_report

# ── Processor ROLES (neutral slugs — see the module docstring) ────────────────────────────────────
EXTERNAL_CC = "external_cc"
POS_MERCHANT = "pos_merchant"
ROLES = (EXTERNAL_CC, POS_MERCHANT)

ROLE_TITLES = {
    # Neutral fallback wording only. The tenant-facing name of the external field comes from the
    # mig-960 label preset (`report_labels` key `closing_t_ext_cc`), resolved by the caller.
    EXTERNAL_CC: "External credit machine",
    POS_MERCHANT: "POS merchant processor",
}

# Standard tender_key → physical daily_closing column. A standard tender reads its t_* column; a
# custom tender (mig 111 config) reads the `tenders` JSONB instead — so the two never double-count.
# THE one map: `closing/router._TCOL` is this constant (re-pointed 2026-09-04, not copied).
TENDER_COLUMN = {"cash": "t_cash", "credit": "t_credit", "ext_cc": "t_ext_cc", "gift": "t_gift",
                 "store_acct": "t_store_acct", "zelle": "t_zelle", "acima": "t_acima"}

# HOUSE DEFAULT tender_key → processor role. Per-org overrides live in
# `commcalc.closing_tender_def.processor_key` (mig 960); an org with no tender-def rows (the common
# case — the built-in 7 tenders) tallies correctly with zero configuration.
DEFAULT_TENDER_PROCESSOR = {"ext_cc": EXTERNAL_CC, "credit": POS_MERCHANT}

# Statuses this report emits. The first three are `envelope_report.count_fields`' own verdicts,
# reused verbatim; the rest are HONEST GAPS — never a fake zero or a fake mismatch.
STATUSES = ("short", "over", "match", "no_processor_data", "no_declared_data", "dm_merged")
GAP_STATUSES = ("no_processor_data", "no_declared_data", "dm_merged")

# ── The SETTLEMENT ADAPTER CONTRACT ───────────────────────────────────────────────────────────────
# The scraped-totals table is owned by the portal-scrape side and resolved through
# `commcalc.report_pull_map` (mig 207), so its column spelling is CONFIG, not an assumption here.
# `normalize_settlement_rows` maps whatever that config names onto this canonical shape:
#
#   store_code   TEXT   our store code, when the feed already resolved one (optional)
#   merchant_id  TEXT   the store's id at that processor — resolved through
#                       storeops.store_merchant_id (mig 902) when store_code is absent
#   day          DATE   the settlement/business day (ISO 'YYYY-MM-DD')
#   amount       NUMERIC the day's settled total for that store at that processor
#   role         TEXT   optional; defaults to the role the caller asked for
#
# DEFAULT_SETTLEMENT_FIELDS is the fallback spelling used when the report_pull_map row names no
# column_map — it is a DEFAULT, never a requirement, and a feed that spells things differently is
# accommodated by config alone.
SETTLEMENT_FIELDS = ("store_code", "merchant_id", "day", "amount", "role")
DEFAULT_SETTLEMENT_FIELDS = {
    "store_code": ("store_code",),
    "merchant_id": ("merchant_id", "terminal_id"),
    "day": ("business_date", "settlement_date", "day", "close_date", "batch_date", "transaction_date"),
    "amount": ("net_amount", "amount", "total", "gross_amount"),
    "role": ("settlement_role", "role", "processor_role"),
}

# The mig-207 `report_pull_map.report_key` under which the portal-scrape side registers the daily
# settlement feed (seeded by mig 955). ONE key for BOTH roles — a row states its own role in the
# column the registry's `column_map` names, so a tenant running one portal in the other role changes
# a config row, never code.
SETTLEMENT_REPORT_KEY = "merchant_settlement"


def _f(v):
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _norm_store(code):
    """Store-code normalization — the SAME rule verified_overlay._norm applies, so a declared cell
    and a settlement cell for the same store always land on the same key."""
    return str(code or "").strip().upper()


def _day(v):
    return str(v or "")[:10]


# ── PURE: config resolution (house default < per-org tender_def rows) ─────────────────────────────
def tender_processor_map(tender_defs=None):
    """{tender_key: processor role} for one org: the house default map, overlaid with any
    `closing_tender_def` row that names a `processor_key`. An inactive tender def is ignored; an
    unknown role slug is ignored (a junk config row can never invent a phantom leg)."""
    out = dict(DEFAULT_TENDER_PROCESSOR)
    for d in tender_defs or []:
        d = d or {}
        key = str(d.get("tender_key") or "").strip()
        role = str(d.get("processor_key") or "").strip()
        if not key or d.get("is_active") is False:
            continue
        if role in ROLES:
            out[key] = role
        elif role:                      # explicit blank/unknown → the tender opts OUT of the tally
            out.pop(key, None)
    return out


def role_columns(tender_defs=None):
    """{processor role: [daily_closing column, …]} — which declared money column(s) feed each leg.
    A CUSTOM tender (mig 111, no physical t_* column) is returned as its JSONB key so
    `declared_amount` can read the `tenders` blob; standard tenders return their t_* column."""
    out = {r: [] for r in ROLES}
    for key, role in sorted(tender_processor_map(tender_defs).items()):
        if role in out:
            out[role].append(TENDER_COLUMN.get(key) or key)
    return out


def declared_amount(row, column):
    """One closing row's declared money for one resolved column: a physical `t_*` column, else the
    mig-111 `tenders` JSONB key of the same name. Mirrors `closing/router._closing_amt`."""
    r = row or {}
    if column in TENDER_COLUMN.values():
        return _f(r.get(column))
    j = r.get("tenders")
    return _f(j.get(column)) if isinstance(j, dict) else 0.0


# ── PURE: the DECLARED leg ────────────────────────────────────────────────────────────────────────
def declared_cells(closing_rows, cols_by_role=None):
    """{(STORE, day): {role: {'amount': x, 'basis': 'rep'}}} — the employees' entered figures summed
    to store-day grain (the grain a DM verification and a processor settlement both live at).
    `cols_by_role` defaults to the house map."""
    cols = cols_by_role or role_columns()
    cells = {}
    for r in closing_rows or []:
        r = r or {}
        code, day = _norm_store(r.get("store_code")), _day(r.get("close_date"))
        if not day:
            continue
        slot = cells.setdefault((code, day), {})
        for role, columns in cols.items():
            amt = round(sum(declared_amount(r, c) for c in columns), 2)
            leg = slot.setdefault(role, {"amount": 0.0, "basis": "rep"})
            leg["amount"] = round(leg["amount"] + amt, 2)
    return cells


def apply_dm_split(cells, dm_by_store_day):
    """Overlay the DM's VERIFIED card correction onto the declared cells, in place, and stamp each
    leg's BASIS. Mirrors `verified_overlay.apply_overlay`'s mig-961 rule exactly — one derivation,
    stated in two shapes:

        dm_store_cc set, dm_ext_cc set   → external = dm_ext_cc
                                           pos_merchant = dm_store_cc − dm_ext_cc   basis 'dm'
        dm_store_cc set, dm_ext_cc None  → the split is UNKNOWN                      basis 'dm_merged'
                                           (amount left as the reps' figure; the row reports the
                                            gap status `dm_merged` and NEVER a variance)
        dm_store_cc None                 → the reps' figures stand                   basis 'rep'

    In the first branch the two legs still sum to `dm_store_cc` to the cent — the card TOTAL the
    rest of the platform books never moves (mig 961 invariant)."""
    for (code, day), slot in (cells or {}).items():
        dm = (dm_by_store_day or {}).get((code, day))
        if not dm or dm.get("dm_store_cc") is None:
            continue
        cc_total = _f(dm.get("dm_store_cc"))
        if dm.get("dm_ext_cc") is None:
            for role in (EXTERNAL_CC, POS_MERCHANT):
                if role in slot:
                    slot[role]["basis"] = "dm_merged"
            continue
        ext = _f(dm.get("dm_ext_cc"))
        if EXTERNAL_CC in slot:
            slot[EXTERNAL_CC] = {"amount": ext, "basis": "dm"}
        if POS_MERCHANT in slot:
            slot[POS_MERCHANT] = {"amount": round(cc_total - ext, 2), "basis": "dm"}
    return cells


# ── PURE: the SETTLEMENT leg (the scraped side) ───────────────────────────────────────────────────
def normalize_settlement_rows(rows, column_map=None, role=None):
    """Map raw scraped rows onto the canonical adapter shape (see SETTLEMENT_FIELDS).

    `column_map` is the `report_pull_map.column_map` for the feed, in either direction —
    {canonical: source_header} or the ingest-facing {source_header: {'col': canonical}} /
    {source_header: canonical}. Absent → DEFAULT_SETTLEMENT_FIELDS' candidate spellings. Rows with
    no day, or no store identity at all, are returned with those fields blank so the caller can
    report them as unmapped rather than dropping money silently."""
    resolved = {}
    cm = column_map or {}
    if isinstance(cm, dict) and cm:
        for k, v in cm.items():
            if k in SETTLEMENT_FIELDS and isinstance(v, str):
                resolved[k] = [v]                                   # {canonical: source_header}
            else:
                dest = v.get("col") if isinstance(v, dict) else v   # {source_header: canonical}
                if isinstance(dest, str) and dest in SETTLEMENT_FIELDS:
                    resolved.setdefault(dest, []).append(k)
    out = []
    for r in rows or []:
        r = r or {}

        def pick(field):
            for cand in resolved.get(field) or DEFAULT_SETTLEMENT_FIELDS.get(field, ()):
                if r.get(cand) not in (None, ""):
                    return r.get(cand)
            return None

        out.append({
            "store_code": pick("store_code"),
            "merchant_id": str(pick("merchant_id") or "").strip() or None,
            "day": _day(pick("day")),
            "amount": _f(pick("amount")),
            "role": str(pick("role") or role or "").strip() or role,
        })
    return out


def settlement_cells(rows, store_by_merchant=None, role=None):
    """({(STORE, day): {role: amount}}, unmapped_rows) from normalized settlement rows.

    A row's store is its own `store_code` when the feed resolved one, else its `merchant_id`
    through `store_by_merchant` — `storeops.merchant_ids.resolve_map(org, processor)`, mig 902,
    passed in by the caller. A row whose merchant id maps to nothing is NOT silently dropped: it is
    returned in `unmapped` so the screen can say which terminal needs a store, exactly as the
    mig-944 account-resolution defect taught."""
    store_by_merchant = store_by_merchant or {}
    cells, unmapped = {}, []
    for r in rows or []:
        r = r or {}
        day = _day(r.get("day"))
        code = _norm_store(r.get("store_code")) or _norm_store(store_by_merchant.get(r.get("merchant_id")))
        rl = r.get("role") or role
        if not day or not code or rl not in ROLES:
            unmapped.append(r)
            continue
        slot = cells.setdefault((code, day), {})
        slot[rl] = round(_f(slot.get(rl)) + _f(r.get("amount")), 2)
    return cells, unmapped


# ── PURE: the tally ───────────────────────────────────────────────────────────────────────────────
def recon_row(store_code, day, role, declared_leg, settled, tolerance=0.0, meta=None):
    """ONE tally line for (store, day, processor role).

    THE VERDICT IS `envelope_report.count_fields` — the mig-936 truth table, reused, not re-written:
    expected = what the platform DECLARED, counted = what the processor SETTLED, so
    variance = settled − declared and a NEGATIVE variance is SHORT (the processor settled less than
    the store recorded). |variance| ≤ tolerance ⇒ match.

    Honest gaps win over any verdict, and each carries `variance = None` so no screen, export or sum
    can mistake an absent feed for a zero:
      dm_merged           the DM corrected the card total without stating the external split (mig 961)
      no_declared_data    no closing row for this store-day
      no_processor_data   the settlement feed reported nothing for this store-day
    """
    leg = declared_leg or {}
    dec = leg.get("amount")
    basis = leg.get("basis") or "rep"
    row = {
        "store_code": store_code,
        "close_date": day,
        "processor_role": role,
        "role_title": ROLE_TITLES.get(role, role),
        "declared_amount": _f(dec) if dec is not None else None,
        "declared_basis": basis,
        "settled_amount": _f(settled) if settled is not None else None,
        "variance": None,
        "status": None,
        **(meta or {}),
    }
    if basis == "dm_merged":
        row["status"] = "dm_merged"
    elif dec is None:
        row["status"] = "no_declared_data"
    elif settled is None:
        row["status"] = "no_processor_data"
    else:
        cf = _envelope_report.count_fields(_f(dec), _f(settled), tolerance)
        row["variance"] = cf["variance"]
        row["status"] = cf["status"]
    return row


def assemble_rows(declared, settled, tolerance=0.0, roles=ROLES, meta_by_store=None,
                  feed_days=None):
    """Every tally line for the window: the UNION of declared and settled (store, day) keys × roles,
    sorted newest day first then store then role.

    `feed_days`: the days the settlement feed actually covers, per role — {role: set(days)}. A day
    OUTSIDE that set is `no_processor_data` (the scrape has not landed); a day INSIDE it with no row
    for this store is an honest 0.00 settled — the same present-but-silent vs absent distinction the
    mig-944 3-way recon makes. `feed_days` None ⇒ every missing cell is `no_processor_data`."""
    meta_by_store = meta_by_store or {}
    keys = set(declared or {}) | set(settled or {})
    out = []
    for (code, day) in sorted(keys, key=lambda k: (k[1], k[0]), reverse=True):
        dslot = (declared or {}).get((code, day)) or {}
        sslot = (settled or {}).get((code, day)) or {}
        for role in roles:
            leg = dslot.get(role)
            if role in sslot:
                got = sslot[role]
            elif feed_days is not None and day in ((feed_days or {}).get(role) or set()):
                got = 0.0                       # feed covers the day and is silent for this store
            else:
                got = None                      # feed has not landed for this day
            if leg is None and got is None:
                continue
            out.append(recon_row(code, day, role, leg, got, tolerance,
                                 meta=dict(meta_by_store.get(code) or {})))
    return out


def status_filter(rows, status=""):
    """Filter the tally by status. Extra buckets, mirroring envelope_report.status_filter's shape:
    'variance' = short ∪ over; 'gap' = every honest-gap status."""
    s = str(status or "").strip().lower()
    if not s or s == "all":
        return list(rows or [])
    if s == "variance":
        return [r for r in rows or [] if r.get("status") in ("short", "over")]
    if s == "gap":
        return [r for r in rows or [] if r.get("status") in GAP_STATUSES]
    return [r for r in rows or [] if r.get("status") == s]


def totals(rows):
    """Report totals. Gap rows contribute to their COUNT only — never to a dollar sum, so a missing
    scrape can never read as a balanced day."""
    out = {"cells": 0, "declared_total": 0.0, "settled_total": 0.0, "variance_total": 0.0,
           "short": 0, "short_total": 0.0, "over": 0, "over_total": 0.0, "match": 0,
           "no_processor_data": 0, "no_declared_data": 0, "dm_merged": 0,
           "by_role": {}}
    for r in rows or []:
        st = r.get("status")
        out["cells"] += 1
        role = r.get("processor_role") or "?"
        rslot = out["by_role"].setdefault(role, {"cells": 0, "declared_total": 0.0,
                                                 "settled_total": 0.0, "variance_total": 0.0,
                                                 "short": 0, "over": 0, "match": 0, "gaps": 0})
        rslot["cells"] += 1
        if st in GAP_STATUSES:
            out[st] = out.get(st, 0) + 1
            rslot["gaps"] += 1
            continue
        dec, got, var = _f(r.get("declared_amount")), _f(r.get("settled_amount")), _f(r.get("variance"))
        out["declared_total"] = round(out["declared_total"] + dec, 2)
        out["settled_total"] = round(out["settled_total"] + got, 2)
        out["variance_total"] = round(out["variance_total"] + var, 2)
        rslot["declared_total"] = round(rslot["declared_total"] + dec, 2)
        rslot["settled_total"] = round(rslot["settled_total"] + got, 2)
        rslot["variance_total"] = round(rslot["variance_total"] + var, 2)
        if st == "short":
            out["short"] += 1
            rslot["short"] += 1
            out["short_total"] = round(out["short_total"] + abs(var), 2)
        elif st == "over":
            out["over"] += 1
            rslot["over"] += 1
            out["over_total"] = round(out["over_total"] + var, 2)
        elif st == "match":
            out["match"] += 1
            rslot["match"] += 1
    return out
