"""THE FLAG REGISTRY — one home for "what kind of finding is this, how bad is it, and who reviews it".

OWNER ASK 2026-10-05, verbatim: *"Which five modules write findings and why do they write 3 different
ways"* — asked while commissioning the Management Watchdog.

THE ANSWER, AND THE DEFECT IT NAMES
───────────────────────────────────
`commcalc.flags` was created in migration 002 as `flag_type TEXT` with no registry, no enum and no
check constraint. Six modules were then given a queue on it, months apart, and each author picked the
spelling that looked right:

    commcalc/flags.py + portout_flags.py   'DUPLICATE_IMEI'                  SCREAMING_SNAKE
    commcalc/sales_recon.py                'sales_leak'                      lower_snake
    asset/router.py + asset/invoice_due.py "Hotsheet Underpayment"           a human sentence
    account/recon.py                       "Distributor credit-memo recon …" a human sentence
    payables/engine.py                     "Equipment Rebate Not Received"   a human sentence
    closing/ops_chargebacks.py             "Missed Daily Closing"            a human sentence

Nobody was wrong. There was no shared home to be right about. That is the duplicate defect the index
rules forbid, in its quietest form: not two code paths, but six private vocabularies for one column.

**The part that is not cosmetic: `severity` has three incompatible vocabularies too.**
`commcalc/flags.py` writes `HIGH` / `MEDIUM` / `LOW`, `portout_flags.py` adds `CRITICAL`, and asset /
account / payables write `critical` / `warning`. So "show me everything critical first" is physically
impossible across the table — half the rows use words the other half never writes. A management
dashboard cannot be built on that, which is why this module ships before any new detector.

WHAT THIS MODULE IS
───────────────────
PURE, stdlib only, no I/O, no framework import, so `harness_flag_registry_lock.py` proves it DB-free
and every writer can import it without dragging a router in.

  TYPES          every flag type the platform emits → its canonical key, label, AREA and severity.
  AREAS          the review areas the Management Watchdog is organised by. The dashboard's page list
                 is DERIVED from this, so a new detector appears on the board by registering here and
                 cannot drift from the rules.
  canon_type()   any stored spelling → its registered key (legacy sentences included).
  canon_sev()    any of the three stored severity vocabularies → ONE four-level scale.
  severity_for() what a WRITER should stamp, so new rows are canonical at birth.

WHAT IT DELIBERATELY DOES NOT DO
────────────────────────────────
  · **It does not rewrite stored rows.** No migration re-spells `flag_type` or `severity` on history.
    A finding is an accusation against a person with a manager's ruling attached (see
    `flag_persist.py`); re-writing it to tidy a vocabulary is exactly the erasure that module exists
    to prevent. Legacy spellings are canonicalised ON READ instead, and new writes are canonical.
  · **It does not store the area on the flag row.** The area is a property of the TYPE, not of the
    finding, so it lives here and the read path dereferences it. A column would be a second copy and
    a future divergence (CLAUDE.md, "one fact, one home, dereferenced — never copied").
  · **It invents no detection.** Every type below is one somebody already emits, plus the two new
    watchdogs commissioned in the same PR.

RULE TWO: no carrier, tenant, store or product name appears here. Per-org enable and thresholds are
config rows (`commcalc.watchdog_rule`, mig 1056) read through `rule_params()`; the house defaults
below are the fallback when no row exists.

Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §53.
"""
from __future__ import annotations

# ── Severity: ONE scale, four levels, worst first. ───────────────────────────────────────────────
#: The canonical vocabulary. Nothing may invent a fifth level.
#:
#: UPPER-CASE, and that is a deliberate choice between two live spellings rather than a preference.
#: `commcalc/flags.py` + `portout_flags.py` — by far the largest writer, and the one the Flags page
#: was built for — already write `CRITICAL` / `HIGH` / `MEDIUM` / `LOW`, and that page's colour map
#: (`SEVERITY_COLORS`) is keyed on exactly those four strings. So asset / account / payables rows,
#: written as `critical` / `warning`, render GREY there today: the divergence is already visible on
#: screen. Canonicalising UP therefore fixes those rows' colour with no frontend change and no
#: regression, where canonicalising DOWN would grey out every flag the page currently colours.
CRITICAL = "CRITICAL"
HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"
SEVERITIES = (CRITICAL, HIGH, MEDIUM, LOW)

#: Sort rank, so a board can order by severity without re-deciding what "worse" means.
SEVERITY_RANK = {CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3}

#: Every spelling found in the table on 2026-10-05 → its canonical level.
#:
#: `warning` is the only judgement call in here. asset / account / payables use `critical` vs
#: `warning` as a TWO-level scale where critical is the worse one, so `warning` maps to MEDIUM: that
#: keeps `critical` distinct and never promotes an advisory finding above a rule that explicitly
#: asked for HIGH. Stated rather than silently chosen, because it decides board order.
_SEVERITY_ALIASES = {
    "critical": CRITICAL, "crit": CRITICAL, "severe": CRITICAL,
    "high": HIGH,
    "warning": MEDIUM, "warn": MEDIUM, "medium": MEDIUM, "med": MEDIUM,
    "low": LOW, "info": LOW, "notice": LOW,
}


def canon_sev(raw, default=MEDIUM) -> str:
    """PURE. Any stored severity spelling → one of SEVERITIES.

    An unrecognised value returns `default` rather than raising: a finding with an odd severity must
    still reach the board. Case and surrounding space are ignored.
    """
    return _SEVERITY_ALIASES.get(str(raw or "").strip().lower(), default)


def sev_rank(raw) -> int:
    """PURE. Sort key for any stored severity — lower is worse. Use for board ordering."""
    return SEVERITY_RANK[canon_sev(raw)]


# ── Areas: how management reviews findings. ──────────────────────────────────────────────────────
# ONE row per area, in board order. `blurb` reaches the screen, so it carries no table, column or
# hosting name (index §19.38).
AREAS: tuple[tuple[str, str, str], ...] = (
    ("cash", "Cash & Closing",
     "Drawer and credit variances on store-days that were accepted, auto-accepted while still "
     "mismatched, or submitted and never corrected."),
    ("voids", "Voids, Returns & Waived Fees",
     "Transactions that were reversed or had a fee waived — who reversed them, how often, and "
     "whether the same device was sold and voided."),
    ("commission", "Sales vs Commission",
     "Sales the carrier never paid for, and sales paid at an amount that does not match the sale."),
    ("fraud", "Fraud & Identity",
     "One device or identity used where it should appear once, and activation patterns that do not "
     "look like retail selling."),
    ("churn", "Port-outs & Churn",
     "Subscribers that left, were transferred out, or were suspended, by how soon after the sale."),
    ("inventory", "Inventory & Assets",
     "Units the ledger and the shelf disagree about, and device costs or reimbursements that came "
     "back short."),
    ("distributor", "Distributor & Payables",
     "Invoices, credit memos and rebates where what we were billed, received or promised do not "
     "agree."),
    ("feed", "Feed & Data Integrity",
     "A store or payment type the platform received data for but cannot place — a gap that makes "
     "every other number for that store wrong."),
)

#: Area keys in board order.
AREA_KEYS = tuple(k for k, _l, _b in AREAS)
AREA_LABELS = {k: l for k, l, _b in AREAS}
AREA_BLURBS = {k: b for k, _l, b in AREAS}

#: Where a finding goes when its type is not registered. It is COUNTED AND NAMED, never dropped —
#: the §19.26 rule ("missing beats wrong") applied to the board: a manager must be able to tell
#: "nothing open" from "something open that nobody classified".
UNASSIGNED = "unassigned"
UNASSIGNED_LABEL = "Unclassified"


# ── The types. ───────────────────────────────────────────────────────────────────────────────────
# key      the canonical flag_type a writer stamps.
# label    what a manager reads. No table/column names (index §19.38).
# area     which Management Watchdog page it is reviewed on.
# sev      the severity a writer stamps when it has no sharper judgement of its own. A detector that
#          grades its own findings (asset's critical-vs-warning, port-out's day bands) passes an
#          override to `severity_for` and this is only the fallback.
# writer   the module that emits it — so a reader of the board can find the rule.
# grain    'transaction' (names one sale/line/unit), 'store_day', 'rep_period' or 'store_period'.
#          The grain is what tells a manager whether they can click through to a single transaction;
#          it is also the honest answer to "why can't I see which sale this was".
# legacy   other spellings the same finding has been stored under. Read-path only.
# prefix   True when the stored value is this key plus a trailing detail (asset's dynamic types).
TYPES: dict[str, dict] = {
    # ── Cash & Closing ──────────────────────────────────────────────────────────────────────────
    # New in this PR. The signals all come from `commcalc.closing_attempt`, which has carried them
    # since migration 103 with nothing watching them.
    "CASH_OVER": {
        "label": "Drawer over the point-of-sale figure",
        "area": "cash", "sev": HIGH, "writer": "closing/cash_watchdog.py", "grain": "store_day",
    },
    "CASH_SHORT": {
        "label": "Drawer short of the point-of-sale figure",
        "area": "cash", "sev": CRITICAL, "writer": "closing/cash_watchdog.py", "grain": "store_day",
    },
    "CREDIT_VARIANCE": {
        "label": "Card total does not match the point-of-sale figure",
        "area": "cash", "sev": HIGH, "writer": "closing/cash_watchdog.py", "grain": "store_day",
    },
    "CASH_AUTO_ACCEPTED": {
        "label": "Closing accepted on the final try while still mismatched",
        "area": "cash", "sev": CRITICAL, "writer": "closing/cash_watchdog.py", "grain": "store_day",
    },
    "CASH_AWAITING_CORRECTION": {
        "label": "Cash entered, sent back to recount, never returned",
        "area": "cash", "sev": CRITICAL, "writer": "closing/cash_watchdog.py", "grain": "store_day",
    },
    "CASH_REPEAT_VARIANCE": {
        "label": "Same person present on repeated variance days",
        "area": "cash", "sev": HIGH, "writer": "closing/cash_watchdog.py", "grain": "rep_period",
    },
    "Missed Daily Closing": {
        "label": "Daily closing never submitted",
        "area": "cash", "sev": MEDIUM, "writer": "closing/ops_chargebacks.py", "grain": "store_day",
    },

    # ── Voids, Returns & Waived Fees ────────────────────────────────────────────────────────────
    # New in this PR, except SETUP_FEE_MISSING which already existed and belongs with them: a waived
    # fee and a reversed sale are the same family of "the transaction did not stand as written".
    "VOID_RATE_HIGH": {
        "label": "Void rate above the allowed share of lines",
        "area": "voids", "sev": HIGH, "writer": "commcalc/void_watchdog.py", "grain": "rep_period",
    },
    "RETURN_RATE_HIGH": {
        "label": "Return rate above the allowed share of lines",
        "area": "voids", "sev": MEDIUM, "writer": "commcalc/void_watchdog.py", "grain": "rep_period",
    },
    "VOID_AFTER_SALE": {
        "label": "Same device both sold and voided",
        "area": "voids", "sev": CRITICAL, "writer": "commcalc/void_watchdog.py",
        "grain": "transaction",
    },
    "VOID_UNATTRIBUTED": {
        "label": "Voided lines with nobody named on them",
        "area": "voids", "sev": HIGH, "writer": "commcalc/void_watchdog.py", "grain": "store_period",
    },
    "SETUP_FEE_MISSING": {
        "label": "Activation with no setup fee charged",
        "area": "voids", "sev": MEDIUM, "writer": "commcalc/flags.py", "grain": "rep_period",
    },

    # ── Sales vs Commission ─────────────────────────────────────────────────────────────────────
    "sales_leak": {
        "label": "Sale the commission feed never carried",
        "area": "commission", "sev": HIGH, "writer": "commcalc/sales_recon.py",
        "grain": "transaction",
    },
    "sales_amount_mismatch": {
        "label": "Sale paid at a different amount than it was sold for",
        "area": "commission", "sev": HIGH, "writer": "commcalc/sales_recon.py",
        "grain": "transaction",
    },
    "CHARGEBACK": {
        "label": "Commission clawed back",
        "area": "commission", "sev": HIGH, "writer": "commcalc/flags.py", "grain": "transaction",
    },
    "INSTALLMENT_WITHHELD_UNPAID": {
        "label": "Installment withheld because the line stopped paying",
        "area": "commission", "sev": MEDIUM, "writer": "commcalc/sale_installment_engine.py",
        "grain": "transaction",
    },
    "SOLD_LINE_NOT_PAYING": {
        "label": "Sold line not paying, so the rep's commission is gated",
        "area": "commission", "sev": MEDIUM, "writer": "commcalc/sale_installment_engine.py",
        "grain": "transaction",
    },

    # ── Fraud & Identity ────────────────────────────────────────────────────────────────────────
    "DUPLICATE_IMEI": {
        "label": "One device used on several lines",
        "area": "fraud", "sev": HIGH, "writer": "commcalc/flags.py", "grain": "transaction",
    },
    "RSK_ACTIVATIONS": {
        "label": "Activation pattern flagged as risky",
        "area": "fraud", "sev": HIGH, "writer": "commcalc/flags.py", "grain": "rep_period",
    },

    # ── Port-outs & Churn ───────────────────────────────────────────────────────────────────────
    "PORT_OUT_30DAY": {
        "label": "Ported out within 30 days of the sale",
        "area": "churn", "sev": CRITICAL, "writer": "commcalc/portout_flags.py",
        "grain": "transaction",
    },
    "PORT_OUT_60DAY": {
        "label": "Ported out within 60 days of the sale",
        "area": "churn", "sev": HIGH, "writer": "commcalc/portout_flags.py", "grain": "transaction",
    },
    "PORT_OUT_90PLUS": {
        "label": "Ported out after 90 days",
        "area": "churn", "sev": LOW, "writer": "commcalc/portout_flags.py", "grain": "transaction",
    },
    "PORT_OUT_NODATE": {
        "label": "Ported out, sale date unknown",
        "area": "churn", "sev": LOW, "writer": "commcalc/portout_flags.py", "grain": "transaction",
    },
    "RESIDUAL_TRANSFER_OUT": {
        "label": "Subscriber transferred to another dealer",
        "area": "churn", "sev": MEDIUM, "writer": "commcalc/portout_flags.py",
        "grain": "transaction",
    },
    "INVOLUNTARY_SUSPENDED": {
        "label": "Subscriber suspended for non-payment",
        "area": "churn", "sev": MEDIUM, "writer": "commcalc/portout_flags.py",
        "grain": "transaction",
    },
    "HIGH_PORT_OUT_RATE": {
        "label": "Store port-out rate above the allowed share",
        "area": "churn", "sev": HIGH, "writer": "commcalc/flags.py", "grain": "store_period",
    },

    # ── Inventory & Assets ──────────────────────────────────────────────────────────────────────
    # `Inventory mismatch — <kind>` is written as an f-string, so its stored value is unbounded.
    # `prefix` is how a registry covers a type whose author chose to spell the detail into the name;
    # the base key is what the board groups on.
    "Inventory mismatch": {
        "label": "Ledger and shelf disagree",
        "area": "inventory", "sev": MEDIUM, "writer": "asset/router.py", "grain": "transaction",
        "prefix": True,
    },
    "Hotsheet Underpayment": {
        "label": "Device reimbursed below the published rate",
        "area": "inventory", "sev": MEDIUM, "writer": "asset/router.py", "grain": "transaction",
    },
    "Asset Appeal / Denied Payment": {
        "label": "Device payment denied on appeal",
        "area": "inventory", "sev": CRITICAL, "writer": "asset/router.py", "grain": "transaction",
    },
    "RMA Reimbursement Gap": {
        "label": "Returned device never reimbursed",
        "area": "inventory", "sev": MEDIUM, "writer": "asset/router.py", "grain": "transaction",
    },
    "Device Undercharge": {
        "label": "Device sold below its cost",
        "area": "inventory", "sev": MEDIUM, "writer": "asset/router.py", "grain": "transaction",
    },

    # ── Distributor & Payables ──────────────────────────────────────────────────────────────────
    "Upcoming VIP Invoice Due": {
        "label": "Distributor invoice due soon",
        "area": "distributor", "sev": MEDIUM, "writer": "asset/invoice_due.py", "grain": "transaction",
    },
    "VIP Invoice Overdue": {
        "label": "Distributor invoice overdue",
        "area": "distributor", "sev": CRITICAL, "writer": "asset/invoice_due.py",
        "grain": "transaction",
    },
    "Distributor credit-memo recon (company-wide)": {
        "label": "Credit memos do not reconcile, company-wide",
        "area": "distributor", "sev": MEDIUM, "writer": "account/recon.py", "grain": "store_period",
    },
    "Distributor credit-memo recon (store)": {
        "label": "Credit memos do not reconcile for a store",
        "area": "distributor", "sev": MEDIUM, "writer": "account/recon.py", "grain": "store_period",
    },
    "Equipment Rebate Not Received": {
        "label": "Rebate earned but never received",
        "area": "distributor", "sev": MEDIUM, "writer": "payables/engine.py", "grain": "transaction",
    },

    # ── Feed & Data Integrity ───────────────────────────────────────────────────────────────────
    "MISSING_STORE_SALES": {
        "label": "Store in the sales feed that cannot be placed",
        "area": "feed", "sev": MEDIUM, "writer": "commcalc/flags.py", "grain": "store_period",
    },
    "MISSING_STORE_PAYMENT": {
        "label": "Store in the payment feed that cannot be placed",
        "area": "feed", "sev": MEDIUM, "writer": "commcalc/flags.py", "grain": "store_period",
    },
    "UNMAPPED_PAYMENT_TYPE": {
        "label": "Payment type the platform does not recognise",
        "area": "feed", "sev": LOW, "writer": "commcalc/flags.py", "grain": "store_period",
    },
}

#: Types `commcalc/flags.py`'s docstring names that the code does NOT emit: MRC_IMEI_MISMATCH,
#: ACCESSORY_LOSS, RSK_NON_PAYMENT, HIGH_CHURN_RATE. They are left OUT of TYPES on purpose. The
#: registry is a record of what the platform actually writes, and registering a type nobody emits
#: would put a permanently-empty row on the management board — the fake-zero this house forbids. The
#: stale docstring is reported, not papered over (see the PR body).
DOCUMENTED_NOT_EMITTED = ("MRC_IMEI_MISMATCH", "ACCESSORY_LOSS", "RSK_NON_PAYMENT",
                          "HIGH_CHURN_RATE")

#: Prefix types, longest first, so `canon_type` matches the most specific one.
_PREFIX_KEYS = tuple(sorted((k for k, m in TYPES.items() if m.get("prefix")),
                            key=len, reverse=True))

#: Legacy spelling → canonical key. Built from each type's own `legacy` list so there is exactly one
#: place a second spelling is ever declared.
_LEGACY: dict[str, str] = {}
for _k, _m in TYPES.items():
    for _alias in _m.get("legacy") or ():
        _LEGACY[str(_alias).strip().lower()] = _k

#: Canonical key, case-folded → key. A writer that lower-cased its own type still resolves.
_FOLDED = {k.strip().lower(): k for k in TYPES}


def canon_type(raw) -> str | None:
    """PURE. Any stored `flag_type` → its registered key, or None when nothing covers it.

    Exact match, then case-folded, then a declared legacy spelling, then a prefix type. None is a
    real answer and the caller must keep it (see UNASSIGNED) — guessing an area for an unknown
    finding would put it under a heading its rule never meant.
    """
    s = str(raw or "").strip()
    if not s:
        return None
    if s in TYPES:
        return s
    low = s.lower()
    if low in _FOLDED:
        return _FOLDED[low]
    if low in _LEGACY:
        return _LEGACY[low]
    for p in _PREFIX_KEYS:
        if low.startswith(p.strip().lower()):
            return p
    return None


def area_of(raw) -> str:
    """PURE. Which Management Watchdog area a stored `flag_type` is reviewed on.

    UNASSIGNED when the type is not registered — counted and named on the board, never dropped.
    """
    k = canon_type(raw)
    return TYPES[k]["area"] if k else UNASSIGNED


def label_of(raw) -> str:
    """PURE. What a manager reads for a stored `flag_type`. Falls back to the stored value itself,
    so an unregistered finding shows the words its author wrote rather than a blank."""
    k = canon_type(raw)
    return TYPES[k]["label"] if k else str(raw or "").strip()


def grain_of(raw) -> str | None:
    """PURE. 'transaction' | 'store_day' | 'rep_period' | 'store_period' for a stored type, else None.

    This is the honest answer to "why can't I click through to the sale": a `rep_period` finding
    counts transactions it did not keep, so there is nothing to click.
    """
    k = canon_type(raw)
    return TYPES[k]["grain"] if k else None


def types_in_area(area) -> tuple[str, ...]:
    """PURE. The canonical keys reviewed on one area's page, in registry order."""
    a = str(area or "").strip()
    return tuple(k for k, m in TYPES.items() if m["area"] == a)


def severity_for(flag_type, override=None) -> str:
    """PURE. The severity a WRITER stamps, so new rows are canonical at birth.

    `override` is for a detector that grades its own findings — asset's critical-vs-warning, the
    port-out day bands — and is canonicalised through the same scale, so a writer cannot introduce a
    fifth level by passing one. With no override the registered default is used; an unregistered type
    falls back to MEDIUM rather than raising, because refusing to write a finding is worse than
    writing it at the middle level.
    """
    if override not in (None, ""):
        return canon_sev(override)
    k = canon_type(flag_type)
    return TYPES[k]["sev"] if k else MEDIUM


def stamp(rows):
    """THE WRITE-SIDE DEREFERENCE. Canonicalise `severity` on every finding, IN PLACE, and return it.

    This is the one line each of the six writers calls before handing its rows on, and it is what
    makes the registry a dereference rather than a document. A writer keeps its own judgement — the
    value it already wrote is passed through as the `override`, so asset's critical-vs-warning and
    the port-out day bands survive — but it comes out on the ONE scale, so the board can order by it.

    `harness_flag_registry_lock.py` fails the build if a writer of `commcalc.flags` does not call
    this, which is what stops the seventh module inventing a fifth vocabulary.

    A row with no `flag_type` is left alone: stamping a severity onto a finding with no kind would be
    inventing a judgement about something we cannot classify.
    """
    for r in rows or ():
        if not isinstance(r, dict):
            continue
        ft = r.get("flag_type")
        if not str(ft or "").strip():
            continue
        r["severity"] = severity_for(ft, r.get("severity"))
    return rows


def unregistered_types(rows) -> list[str]:
    """PURE. The distinct `flag_type` values in `rows` that this registry does not cover, sorted.

    Used by the board (to NAME its unclassified bucket) and by the guard (to report a type somebody
    shipped without registering). Returns the values as STORED, because that is what somebody has to
    search the code for.
    """
    out = set()
    for r in rows or ():
        ft = str((r or {}).get("flag_type") or "").strip()
        if ft and canon_type(ft) is None:
            out.add(ft)
    return sorted(out)


# ── Per-org rules (RULE TWO). ────────────────────────────────────────────────────────────────────
# A watchdog's thresholds are CONFIG, never code. The house defaults live here; a tenant overrides
# them with a `commcalc.watchdog_rule` row (mig 1056), which `rule_params` merges over the default.
# No tenant, carrier or store name appears in either.
#
# Every default was chosen to be deliberately quiet on arrival: a watchdog that flags a third of
# store-days on day one is the alert noise §19.17 removed, and a manager who mutes a board never
# un-mutes it. The thresholds are the tenant's to tighten.
DEFAULT_PARAMS: dict[str, dict] = {
    # Dollars of variance before a drawer day is a finding. Coin-level rounding is not a watchdog.
    "CASH_OVER": {"tolerance": 20.0},
    "CASH_SHORT": {"tolerance": 20.0},
    "CREDIT_VARIANCE": {"tolerance": 20.0},
    # Size-independent: being accepted while still mismatched IS the finding.
    "CASH_AUTO_ACCEPTED": {"tolerance": 0.0},
    "CASH_AWAITING_CORRECTION": {},
    # How many variance days by one person, inside the window, before the pattern is the finding.
    "CASH_REPEAT_VARIANCE": {"min_days": 3},
    # Share of a person's lines, and the floor below which a share means nothing.
    "VOID_RATE_HIGH": {"max_share": 0.05, "min_lines": 20},
    "RETURN_RATE_HIGH": {"max_share": 0.08, "min_lines": 20},
    "VOID_AFTER_SALE": {},
    "VOID_UNATTRIBUTED": {"min_lines": 1},
}


def default_params(flag_type) -> dict:
    """PURE. The house thresholds for a type — a COPY, so a caller cannot mutate the defaults."""
    k = canon_type(flag_type) or str(flag_type or "").strip()
    return dict(DEFAULT_PARAMS.get(k) or {})


def rule_params(flag_type, rows=None) -> dict:
    """PURE. The thresholds in force for a type: the house defaults with a tenant's row merged over.

    `rows` is what the caller read from `commcalc.watchdog_rule` for ITS OWN org — this module does
    no I/O and never sees an org id, so it cannot leak one tenant's config into another's run. A row
    whose `params` is not an object is ignored rather than trusted, and a key the house does not
    declare is ignored too: a typo in config must not silently disable a threshold.
    """
    out = default_params(flag_type)
    k = canon_type(flag_type) or str(flag_type or "").strip()
    for r in rows or ():
        if str((r or {}).get("flag_type") or "").strip() != k:
            continue
        p = (r or {}).get("params")
        if isinstance(p, dict):
            for pk, pv in p.items():
                if pk in out:
                    out[pk] = pv
    return out


def is_enabled(flag_type, rows=None, default=True) -> bool:
    """PURE. Whether a watchdog runs for this org. Absent row = the house default (on).

    A tenant switches a watchdog off with `enabled = false`; `enabled` NULL on the row means "no
    opinion", which is the house default, not off — an operator clearing a cell must not silently
    stop a cash check.
    """
    k = canon_type(flag_type) or str(flag_type or "").strip()
    for r in rows or ():
        if str((r or {}).get("flag_type") or "").strip() != k:
            continue
        v = (r or {}).get("enabled")
        if v is None:
            return default
        return bool(v)
    return default


def area_summary(flag_rows) -> list[dict]:
    """PURE. The Management Watchdog board: one row per area, with its open findings counted.

    `flag_rows` are the caller's already-org-scoped `commcalc.flags` rows. Every area in AREAS is
    returned even at zero, so the board is a complete statement of what is watched rather than a list
    of today's bad news. UNASSIGNED is appended ONLY when something landed in it.
    """
    counts: dict[str, int] = {k: 0 for k in AREA_KEYS}
    worst: dict[str, int] = {}
    unassigned = 0
    unassigned_worst = None
    for r in flag_rows or ():
        ft = (r or {}).get("flag_type")
        a = area_of(ft)
        rank = sev_rank((r or {}).get("severity"))
        if a == UNASSIGNED:
            unassigned += 1
            unassigned_worst = rank if unassigned_worst is None else min(unassigned_worst, rank)
            continue
        counts[a] = counts.get(a, 0) + 1
        worst[a] = rank if a not in worst else min(worst[a], rank)
    out = [{"area": k, "label": AREA_LABELS[k], "blurb": AREA_BLURBS[k],
            "open_count": counts.get(k, 0),
            "worst_severity": SEVERITIES[worst[k]] if k in worst else None,
            "types": list(types_in_area(k))}
           for k in AREA_KEYS]
    if unassigned:
        out.append({"area": UNASSIGNED, "label": UNASSIGNED_LABEL,
                    "blurb": "Findings whose kind is not registered yet. Counted here so the board "
                             "is never read as complete when it is not.",
                    "open_count": unassigned,
                    "worst_severity": SEVERITIES[unassigned_worst]
                    if unassigned_worst is not None else None,
                    "types": []})
    return out


if __name__ == "__main__":  # pragma: no cover - smoke
    assert canon_sev("HIGH") == HIGH and canon_sev("warning") == MEDIUM
    assert area_of("Inventory mismatch — on-hand") == "inventory"
    assert area_of("nonsense") == UNASSIGNED
    assert severity_for("CASH_SHORT") == CRITICAL
    print("flag_registry self-test OK —", len(TYPES), "types in", len(AREAS), "areas")
