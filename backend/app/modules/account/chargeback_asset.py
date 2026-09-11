"""CAPITALISED CHARGEBACK — a distributor chargeback carried as a balance-sheet ASSET and expensed
as it amortises.

OWNER, verbatim (2026-09-11): *"9663.75 is actually being mortised towards the chargeback - ttoal
chargeback is 159106.76 +9663.75 x6 should be in the balance sheet as chargeback"*.

Two owner decisions, settled, implemented exactly:
  1. CAPITALISE the gross as a balance-sheet ASSET and expense it as it amortises. Each instalment
     books to the EXISTING `chargebacks` opex line as it is billed — that line is what it is for.
  2. DERIVE it from the feed, never freeze it. There is no March 2026 instalment (Feb 23 → Apr 3),
     so the schedule is roughly-monthly rather than strictly monthly, and a hard-coded "× 6" is
     wrong the moment a seventh lands. Nothing below counts instalments; it sums what the feed has.

═══════════════ WHAT IS MEASURED, AND WHAT IT IS WORTH KNOWING ════════════════════════════════════
Live, house org, `commcalc.vip_invoice_lines`, measured 2026-09-11:

    Return Item Chargeback   inv 1604147  2025-12-29   $159,056.76
    NSF Fee                  inv 1604147  2025-12-29   $     50.00
    Dealer Chargeback  × 6   2026-02-23, 04-03, 05-02, 06-02, 07-02, 08-02   $ 57,982.50
    ─────────────────────────────────────────────────────────────────────────────────
    gross $217,089.26 · amortised to date $57,982.50 · carrying value $159,106.76

**THIS MONEY IS BOOKED NOWHERE TODAY.** `coa.build_inputs` reads the invoice HEADERS only —
`shipping + other_cost` into `vip_fees`, and unpaid `grand_total` into the `vip_ap` liability. All
seven of these invoices carry shipping $0.00, other_cost $0.00 and status 'Paid In Full', so they
contribute exactly $0.00 to the P&L and $0.00 to the Balance Sheet as things stand. Capitalising
them is therefore RECOGNITION OF MONEY THAT WAS NEVER ON THE BOOKS, not a reclassification of money
that was — which is why the change is material and why nothing existing moves when it lands.

═══════════════ THE TRAP A NAIVE NAME MATCH FALLS INTO ════════════════════════════════════════════
Summing every chargeback-NAMED line on this feed gives **$217,460.31**, not $217,089.26. The extra
$371.05 is other people's money and belongs nowhere near this asset:

    Return Item Chargeback  $30.00 × 3   2025-12-29, at three RETAIL STORES (not the account)
    Early Life Churn Chargeback  × 19    $306.05, 2023, at retail stores
    Handset Returns Commission Chargeback × 1   $25.00, 2023

They share a date and a word with the event and have nothing to do with it. So the derivation is
scoped on BOTH the line-name vocabulary AND the account LOCATION, and both are per-org config.

═══════════════ RULE TWO ══════════════════════════════════════════════════════════════════════════
No location, vendor, product or line name appears in this file. Every one of them is a per-org
config value, and **the house default is EMPTY** — which means this module derives NOTHING and books
NOTHING for any org, including the house org, until an owner explicitly configures it. That is the
same posture `service_fee_products`, `payroll_expense_names` and `overhead_config` already take in
`coa._account_config`: an empty vocabulary leaves every tenant byte-identical.

═══════════════ TWO BASES, BECAUSE THE OWNER'S WORDING IMPLIES SOMETHING THEY MAY NOT INTEND ══════
`basis='event_plus_instalments'` is the owner's stated model: the gross is the original chargeback
PLUS every instalment, and each instalment is amortisation. It reproduces their three numbers
exactly. It also has a consequence worth seeing before it ships: because each new instalment adds
the SAME amount to gross and to amortised, **the carrying value never changes** — it is $159,106.76
today and $159,106.76 after the seventh, the tenth and the fortieth instalment. An asset that
amortises forever without ever declining is unusual, and an accountant will ask about it.

`basis='event_only'` is the other reading: the instalments pay DOWN the original $159,106.76,
carrying value $101,124.26 today, reaching zero after about 16 more.

Both are computed on every run and BOTH are published. The configured basis drives the booking; the
alternative is reported beside it, so the choice is made on numbers rather than on wording, and
changing it later is a config edit rather than a code change.

═══════════════ DUPLICATE CHECK (CLAUDE.md build gate) ════════════════════════════════════════════
Searched `docs/SYSTEM_DATA_FLOW_INDEX.md` first. The house already has chargeback machinery and this
module REUSES rather than siblings it:
  · `chargebacks` ("Chargebacks / clawbacks", opex, store) — the amortisation expense books HERE.
    That is exactly what the line means and no new expense line is created.
  · `chargeback_res` ("Chargeback reserve", liability, store) — DELIBERATELY NOT REUSED. It means
    EXPECTED, UNDEDUCTED chargebacks (`coa.build_inputs` books it from `chargeback_items` rows with
    no `decided_at`). These have been decided, deducted AND paid in full; parking them in a reserve
    would state the opposite of what happened and would corrupt a line the P&L already uses.
  · `commcalc.chargeback_items` / `ops_chargeback` (migs 036/037/504) — INTERNAL, store-and-employee
    chargebacks with their own decide/settle lifecycle. This is a DISTRIBUTOR chargeback billed on a
    distributor invoice; it is not an ops chargeback and must not be written into that lifecycle.
  · `vip_fees` / `vip_ap` — read the invoice HEADERS, and these headers carry nothing (see above).

PURE. Every function here is stdlib-only math over rows handed to it: no client, no I/O, no writes.
Proof: backend/harness_chargeback_asset.py.
"""
from app.modules.commcalc.calculator import safe_float

# The two readings of the owner's wording. Config, not a branch on a tenant.
BASIS_EVENT_PLUS_INSTALMENTS = "event_plus_instalments"
BASIS_EVENT_ONLY = "event_only"
BASES = (BASIS_EVENT_PLUS_INSTALMENTS, BASIS_EVENT_ONLY)

# HOUSE DEFAULTS — deliberately EMPTY. No org derives or books anything until an owner configures
# it, so every tenant is byte-identical the day this ships (RULE TWO, and the same posture
# `service_fee_products` / `payroll_expense_names` already take).
DEFAULT_CONFIG = {
    "locations": [],            # invoice `location` values whose chargebacks are capitalised
    "event_names": [],          # line names forming the capitalised EVENT
    "amortisation_names": [],   # line names that AMORTISE it
    "basis": BASIS_EVENT_PLUS_INSTALMENTS,
}


def _t(v):
    return str(v or "").strip()


def _fold(v):
    return _t(v).casefold()


def as_date(v):
    """PURE: the ISO date prefix of a date/timestamp, '' when unreadable. ISO dates compare
    correctly as strings, so every comparison below is a string comparison."""
    s = _t(v)
    if len(s) < 10 or s[4] != "-" or s[7] != "-":
        return ""
    d = s[:10]
    return d if d[:4].isdigit() and d[5:7].isdigit() and d[8:10].isdigit() else ""


def normalise_config(cfg):
    """PURE: a raw config dict (or None) → the resolved vocabulary, case-folded for matching.

    A missing key, a null, a string where a list belongs, or a basis nobody recognises all degrade
    to the house default rather than raising — the same never-raises posture `coa._account_config`
    takes, because a config typo must not take the Balance Sheet down."""
    c = cfg if isinstance(cfg, dict) else {}

    def _list(key):
        v = c.get(key)
        if isinstance(v, str):
            v = [v]
        return [_t(x) for x in v if _t(x)] if isinstance(v, list) else []

    basis = _t(c.get("basis")) or BASIS_EVENT_PLUS_INSTALMENTS
    if basis not in BASES:
        basis = BASIS_EVENT_PLUS_INSTALMENTS
    locations, events, amorts = _list("locations"), _list("event_names"), _list("amortisation_names")
    return {
        "locations": locations, "event_names": events, "amortisation_names": amorts,
        "basis": basis,
        "_loc": {_fold(x) for x in locations},
        "_event": {_fold(x) for x in events},
        "_amort": {_fold(x) for x in amorts},
        # CONFIGURED means: an account scope AND at least one thing to find in it. Without both, the
        # derivation would either match nothing or match every store's chargebacks — and matching
        # every store's is the $371.05 trap this module exists to avoid.
        "configured": bool(locations) and bool(events or amorts),
    }


def classify_line(row, cfg):
    """PURE: 'event' | 'amortisation' | None for one invoice line, under a normalised config.

    Scoped on BOTH the account LOCATION and the line-name vocabulary. Location alone would sweep in
    every fee the account is ever billed; the name alone sweeps in three retail stores' $30
    chargebacks that share a date and a word with this event (see the module docstring)."""
    if not cfg.get("configured"):
        return None
    if _fold(row.get("location")) not in cfg["_loc"]:
        return None
    name = _fold(row.get("name"))
    if name in cfg["_event"]:
        return "event"
    if name in cfg["_amort"]:
        return "amortisation"
    return None


def _rows_of(line_rows, cfg, kind, as_of):
    out = []
    for r in (line_rows or []):
        if classify_line(r, cfg) != kind:
            continue
        d = as_date(r.get("created_on"))
        if as_of and d and d > as_of:
            continue
        out.append({"date": d, "invoice_number": _t(r.get("invoice_number")),
                    "name": _t(r.get("name")), "location": _t(r.get("location")),
                    "amount": safe_float(r.get("total")), "status": _t(r.get("status"))})
    return sorted(out, key=lambda x: (x["date"], x["invoice_number"], x["name"]))


def derive(line_rows, cfg, as_of=""):
    """PURE: the whole capitalised-chargeback position as at `as_of` ('' ⇒ everything the feed has).

    Returns THREE STATES, never a bare 0.00:
      · not_configured — no org vocabulary, so nothing is measured and every figure is None;
      · measured with real numbers;
      · measured and genuinely zero — the vocabulary is set but the feed carries no such line yet,
        which is a true $0.00 and is reported as one.
    """
    c = normalise_config(cfg)
    if not c["configured"]:
        return {"state": "not_configured",
                "reason": ("no capitalised-chargeback vocabulary is configured for this "
                           "organisation, so nothing is derived and nothing is booked"),
                "basis": c["basis"], "as_of": as_of or None,
                "gross": None, "amortised_to_date": None, "carrying_value": None,
                "alternative": None, "event_lines": [], "amortisation_lines": [],
                "config": {k: c[k] for k in ("locations", "event_names", "amortisation_names")}}

    events = _rows_of(line_rows, c, "event", as_of)
    amorts = _rows_of(line_rows, c, "amortisation", as_of)
    r2 = lambda v: round(v + 0.0, 2)                                            # noqa: E731
    event_total = r2(sum(x["amount"] for x in events))
    amort_total = r2(sum(x["amount"] for x in amorts))

    # The owner's stated model: the gross is the event PLUS every instalment, and each instalment is
    # amortisation of it. Its consequence — the carrying value never declines — is computed here
    # rather than discovered later, and published under `carrying_value_moves`.
    gross_both = r2(event_total + amort_total)
    both = {"basis": BASIS_EVENT_PLUS_INSTALMENTS, "gross": gross_both,
            "amortised_to_date": amort_total, "carrying_value": r2(gross_both - amort_total),
            "carrying_value_moves": False}
    only = {"basis": BASIS_EVENT_ONLY, "gross": event_total,
            "amortised_to_date": amort_total, "carrying_value": r2(event_total - amort_total),
            "carrying_value_moves": True}
    active, other = (both, only) if c["basis"] == BASIS_EVENT_PLUS_INSTALMENTS else (only, both)

    return {
        "state": "measured",
        "reason": None,
        "basis": c["basis"],
        "as_of": as_of or None,
        "gross": active["gross"],
        "amortised_to_date": active["amortised_to_date"],
        "carrying_value": active["carrying_value"],
        # THE CONSEQUENCE, stated. Under the owner's basis each new instalment raises the gross and
        # the amortisation by the same amount, so the carrying value is frozen. Publishing the flag
        # means nobody has to notice it from the numbers a year from now.
        "carrying_value_moves": active["carrying_value_moves"],
        "alternative": other,
        "event_total": event_total,
        "amortisation_total": amort_total,
        "instalments": len(amorts),
        "latest_instalment": (amorts[-1]["date"] if amorts else None),
        "event_lines": events,
        "amortisation_lines": amorts,
        "config": {k: c[k] for k in ("locations", "event_names", "amortisation_names")},
    }


def amortisation_in_period(line_rows, cfg, period_pred):
    """PURE: the instalment money billed INSIDE one P&L period, per invoice `location`.

    `period_pred(row) -> bool` is supplied by the caller (the P&L already owns period matching and
    this module must not grow a second one). Returns {location: amount} — the caller maps location
    to its own store/company grain through the platform's shared resolvers, so no attribution rule
    is re-derived here either."""
    c = normalise_config(cfg)
    out = {}
    if not c["configured"]:
        return out
    for r in (line_rows or []):
        if classify_line(r, c) != "amortisation" or not period_pred(r):
            continue
        loc = _t(r.get("location"))
        out[loc] = round(out.get(loc, 0.0) + safe_float(r.get("total")), 2)
    return out
