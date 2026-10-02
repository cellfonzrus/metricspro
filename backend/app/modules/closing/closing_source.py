"""THE ONE HOME for the fact "where does this store's daily closing come from".

OWNER (2026-10-02): *"the admin should be able to check a box to input daily closing by sales reps for
all stores or pull b2b data from directly into daily closing in case the tenant does not want to have
people submit daily closing, so it is derived via the permission selected at the time of setting up the
store - it could be changed later at any time by the tenant admin, all other features like cash pick up
etc will stay as they are a following action / reports after the data gets populated."*

THE CLASS, NOT THE INSTANCE (CLAUDE.md, "A fix is a DESIGN fix"). The general fact that was missing is
not "this tenant does not want reps submitting" — it is that **the platform had exactly one answer to
"who produces a `commcalc.daily_closing` row", hardcoded as "a rep, by hand"**, and four different
callers each assumed it independently:

  · the submit endpoint accepts any rep's submission for any store;
  · the missing-closing alert nags every store that has no row by the deadline;
  · the `closing_stale_stores` attention provider calls a store with sales and no row
    "selling but not submitting";
  · the closing form offers every store in the picker.

So this module is the ONE registry those callers dereference. It holds no I/O: the resolver takes the
config rows the caller read and returns the source, and `derive_row` builds the `daily_closing` body
from the SAME B2B day aggregate the money recon and the close gate already use (`router._b2b_day`) —
never a second derivation of the day's money or counts.

ONE FACT, ONE HOME, DEREFERENCED. A derived closing is a REAL `commcalc.daily_closing` row carrying
`source='b2b_derived'`, which is why "all other features like cash pick up etc stay as they are":
every downstream consumer (cash position, pickups, envelope report, deposit accountability, the
five-stage chain, DM verify, the P&L bookings) reads `daily_closing` and keeps working with no change
at all. Nothing downstream learns a new vocabulary; the row simply exists without a person typing it.

RULE TWO. No carrier, tenant or product name appears here. The source is a per-org config row with a
per-store override and a house default (`HOUSE_DEFAULT` below) — and the house default is REP ENTRY, so
a tenant that never touches this screen behaves exactly as it does today.
"""
from __future__ import annotations

# ── The vocabulary. Two values, and nothing may invent a third (`normalize` folds anything else to
#    the house default rather than letting an unknown string decide how a store closes its books).
SOURCE_REP_ENTRY = "rep_entry"
SOURCE_B2B_DERIVED = "b2b_derived"
SOURCES = (SOURCE_REP_ENTRY, SOURCE_B2B_DERIVED)

#: The house default — existing manual sales-rep entry, so every store today behaves unchanged.
HOUSE_DEFAULT = SOURCE_REP_ENTRY

#: `daily_closing.source` written by a derived row. The rep path writes 'manual' (unchanged); the
#: sheet-upload path writes 'sheet_upload' (unchanged). Three producers, three distinguishable marks.
DERIVED_ROW_SOURCE = "b2b_derived"

LABELS = {
    SOURCE_REP_ENTRY: "Sales reps submit it",
    SOURCE_B2B_DERIVED: "Derived from the sales feed",
}


def normalize(value) -> str:
    """Any stored / posted value → one of `SOURCES`. Unknown, blank or None → `HOUSE_DEFAULT`."""
    v = str(value or "").strip().lower()
    return v if v in SOURCES else HOUSE_DEFAULT


def resolve(cfg_rows, store_code=None) -> str:
    """THE RESOLVER. `cfg_rows` are the org's `commcalc.closing_source_config` rows (the caller does the
    read); the row with a blank `store_code` is the org default, a row matching `store_code`
    (case-insensitively) is the per-store override.

    Precedence: per-store override → org default → `HOUSE_DEFAULT`. A table or rows that do not exist
    yet give `[]` here, so a pre-migration tenant resolves to rep entry everywhere — byte-identical to
    the behaviour before this feature existed.
    """
    rows = list(cfg_rows or [])
    out = HOUSE_DEFAULT
    for r in rows:
        if not str((r or {}).get("store_code") or "").strip():
            out = normalize((r or {}).get("source"))
            break
    code = str(store_code or "").strip()
    if code:
        for r in rows:
            if str((r or {}).get("store_code") or "").strip().upper() == code.upper():
                out = normalize((r or {}).get("source"))
                break
    return out


def source_map(cfg_rows, store_codes) -> dict:
    """`{store_code: source}` for every code given — the batch form of `resolve`, so a sweep over a
    day's stores resolves from ONE config read instead of one per store."""
    return {c: resolve(cfg_rows, c) for c in (store_codes or []) if str(c or "").strip()}


def expects_rep_submission(source) -> bool:
    """True when a human is supposed to type this store's closing — the question the submit endpoint,
    the missing-closing alert and the stale-store attention provider are each really asking."""
    return normalize(source) == SOURCE_REP_ENTRY


def is_derived(source) -> bool:
    """True when this store's closing is produced from the sales feed."""
    return normalize(source) == SOURCE_B2B_DERIVED


def refusal_message(store_code) -> str:
    """What a rep is told if they somehow reach the submit endpoint for a derived store. It names the
    reason and who changes it — never "forbidden"."""
    return (f"{store_code or 'This store'} is set to take its daily closing from the sales feed, so "
            f"there is nothing to submit here. A tenant admin can switch it back to rep entry in "
            f"Store Setup → Daily closing source.")


# ── The derivation ───────────────────────────────────────────────────────────────────────────────
#  `b2b_store` is one store's entry out of `router._b2b_day()["by_store"]`:
#     {"cash": float, "card": float, "other": float, "acc_gross": float, "total": float,
#      "tenders_available": bool}
#  `b2b_counts` is that store's entry out of `["counts"]`: {"activations": int, "upgrades": int}
#  Both are computed ONCE per day by the existing aggregate — this module re-derives neither.

def derivable(b2b_store) -> tuple:
    """(ok, reason). A day is derivable for a store only when the feed actually carried that store's
    money with a tender split. The two refusals are REPORTED, never papered over with zeros:

      · `no_feed`      — the feed has no rows for this store that day (a store that was shut, or a
                         feed that has not landed). Writing a $0 closing here would manufacture a
                         clean close for a day nobody has data for.
      · `no_tender_split` — the feed carried sales but every line landed in 'other', so cash vs card
                         is unknown. `_b2b_day` already flags this (`tenders_available`) for the close
                         gate; a derived row would otherwise declare $0 cash against real sales and
                         every cash recon downstream would read a phantom shortage.
    """
    s = b2b_store or {}
    total = float(s.get("total") or 0.0)
    if total <= 0:
        return (False, "no_feed")
    if not s.get("tenders_available", True):
        return (False, "no_tender_split")
    return (True, "")


def derive_row(org_id, store_code, close_date, b2b_store, b2b_counts=None, store_meta=None,
               now_iso=None) -> dict:
    """PURE: the `commcalc.daily_closing` body for one derived store-day.

    The tender columns mirror exactly what the REP path writes for the same money
    (`router.submit_closing`), so no downstream consumer can tell a derived row apart except by
    `source` — which is the point. In particular:

      · `t_cash` / `t_credit` are the feed's cash / card; the feed carries no gift / store-account /
        Zelle / ACIMA split, so those stay 0 and the feed's 'other' bucket is reported on the row
        (`derived_other`) rather than silently folded into a tender it is not.
      · the legacy mirror columns (`store_cash`, `store_cc`, `epay_*`) are populated by the SAME
        formulas the rep path uses, because the legacy dashboards read them.
      · `employee_name` is NULL, not a placeholder: nobody submitted this, and inventing a name would
        put a fabricated person on the envelope report and the entry-quality coaching.
      · `attempts` / `auto_accepted` / `mgmt_flag` are untouched — the 3-try close gate exists to
        reconcile a human's count against the feed, and there is no human count here.
    """
    s = b2b_store or {}
    counts = b2b_counts or {}
    meta = store_meta or {}
    cash = round(float(s.get("cash") or 0.0), 2)
    card = round(float(s.get("card") or 0.0), 2)
    other = round(float(s.get("other") or 0.0), 2)
    body = {
        "org_id": org_id,
        "period": str(close_date)[:7],
        "close_date": str(close_date),
        "store_code": store_code,
        "sfid": meta.get("sfid") or None,
        "store_name": meta.get("store_name") or None,
        "store_address": meta.get("store_address") or None,
        "employee_name": None,
        "source": DERIVED_ROW_SOURCE,
        "submitted_at": now_iso,
        "acc_sale": round(float(s.get("acc_gross") or 0.0), 2),
        "t_cash": cash, "t_credit": card,
        "t_ext_cc": 0.0, "t_gift": 0.0, "t_store_acct": 0.0, "t_zelle": 0.0, "t_acima": 0.0,
        # legacy mirrors — the rep path's own formulas, so legacy dashboards reconcile unchanged
        "store_cash": cash, "epay_cash": 0.0,
        "store_cc": card, "epay_cc": 0.0,
        "other_account": 0.0,
        "epay_on_cash": 0.0, "epay_on_credit": 0.0, "epay_on_acima": 0.0,
        "upgrade_count": int(counts.get("upgrades") or 0),
        "new_line_count": int(counts.get("activations") or 0),
        "postpaid_count": 0,
        "expense_amount": 0.0, "expense_description": None, "expense_approved": False,
        "remarks": None,
        "derived_other": other,
        "derived_at": now_iso,
    }
    return body


def changed_fields(existing, derived) -> list:
    """Which of the derived money/count fields differ from the row already stored (2-dp money
    compare). A re-run with an unchanged feed returns `[]`, which is how the sweep stays idempotent
    without blind-updating every row every night."""
    out = []
    for k, v in (derived or {}).items():
        if k in ("org_id", "period", "close_date", "store_code", "source", "submitted_at",
                 "derived_at", "employee_name"):
            continue
        old = (existing or {}).get(k)
        if isinstance(v, float) or isinstance(old, float):
            try:
                if round(float(old or 0.0), 2) != round(float(v or 0.0), 2):
                    out.append(k)
            except (TypeError, ValueError):
                out.append(k)
        elif (old if old is not None else None) != (v if v is not None else None):
            out.append(k)
    return sorted(out)


def plan_day(cfg_rows, store_codes, b2b_by_store, b2b_counts) -> dict:
    """PURE: what a sweep for one day will do, before it touches the database.

    Returns `{"derive": [codes], "skipped": [{"store_code", "reason"}], "rep_submits": [codes]}`.
    `reason` is `derivable`'s, so a day that produces nothing says WHY per store — the owner's
    standing rule that a data defect is reported, never hidden behind a written zero.
    """
    smap = source_map(cfg_rows, store_codes)
    derive, skipped, rep = [], [], []
    for code in (store_codes or []):
        src = smap.get(code, HOUSE_DEFAULT)
        if not is_derived(src):
            rep.append(code)
            continue
        ok, reason = derivable((b2b_by_store or {}).get(code))
        if ok:
            derive.append(code)
        else:
            skipped.append({"store_code": code, "reason": reason})
    return {"derive": derive, "skipped": skipped, "rep_submits": rep}
