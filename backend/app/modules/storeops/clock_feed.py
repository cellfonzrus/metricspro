"""THE ONE HOME for "what did the B2B clock in / clock out report actually say?"

OWNER ASK 2026-10-10: *"need to import the clock in clock out report from b2b … this will be picked
from the emails for each store like other reports."* The file arrives by email every morning at
08:00 for the PREVIOUS day.

THE FORMAT, MEASURED — not guessed (the real export, one sheet, `A1:M47`, 45 punch rows for
2026-10-09, house org; `/mnt/project-files/payroll-clocked-hours/`):

    Store | Salesperson | Clock In | Clock Out | Duration | Duration Decimal | Type |
    Clock In Note | Clock Out Note | Division | Region | District | User Login

WHAT THIS MODULE IS. The column→field mapping is the platform's existing one
(`commcalc/column_mapping` layout `clock_punch` → `storeops.raw_clock_punch`, landed by
`_ingest_mapped_df` like every other feed), so a tenant whose export spells a header differently
maps it on the Column Mapping page and no code changes. What the generic mapper cannot do is the
eleven SHAPE facts below, each of which silently corrupts the import or the comparison if it is
ignored. They live here, once, and `router._ingest_mapped_df` calls `normalize` as this report's
feed-shape post-step — beside the footer-row, row-period and blank-key post-steps that are already
there for exactly this reason.

THE ELEVEN TRAPS, AND WHAT IS DONE ABOUT EACH
---------------------------------------------
 1. **ROW 47 IS A TRAILER AND IT IS A CHECKSUM.** Blank except `Duration = '249:10'`, which equals
    the 249.17h sum of `Duration Decimal` to the minute. The existing `column_mapping
    .drop_footer_rows` already removes a blank-identity totals row (that post-step exists because a
    POS footer once doubled every sum), so the row is not ingested — and `trailer_tie_out` then uses
    it the way the carrier statement work ties to the penny: **a file that does not tie is REJECTED,
    naming both numbers.** Free integrity on a feed that is about to decide pay.
 2. **`Store` IS AN ADDRESS, NOT A CODE.** All 28 distinct spellings in the sample resolve 28/28
    through `account/store_identity.build_store_resolver` over live `commcalc.store_mapping` +
    `store_aliases` — the ONE home (§13d/§64). This module writes NO matcher; the resolver is
    INJECTED and a spelling that binds nowhere is REPORTED, never guessed onto a store.
 3. **`Salesperson` IS `"Last, First"` AND SOMETIMES `"Last, First, M"`** (`Caba, David, A` — a
    middle initial as a third comma segment), **and it carries accents** (`Martínez, LESLIEE`).
    `str.isalnum()` is True for `í`, so the platform's identity folds kept it and the row matched
    nothing; that was fixed in the fold itself (`store_identity.fold_accents`, for every caller),
    and `payroll_identity.person_key` / `name_tokens` are the one home for the person spelling.
    Nothing here splits a name on the comma and assumes two parts.
 4. **CLOCK IN / OUT ARE LOCAL WALL CLOCK WITH NO TIMEZONE**, and `storeops.timelog` is UTC (the
    offset on the sample is 4h, EDT). `to_utc` takes the ZoneInfo the caller resolved through
    `storeops/router._biz_tz_for_store` / `_biz_tz_for` — never a literal offset — so a DST
    boundary and a multi-zone tenant are both correct. A naive stamp with no zone handed in is
    REPORTED, not assumed to be UTC.
 5. **`Duration Decimal` IS TRUNCATED, NOT ROUNDED** (`8:28` → `8.466666`). Hours are computed from
    the TIMESTAMPS and the decimal is a CROSS-CHECK only (it agrees to <0.005h on every sample row);
    a disagreement beyond `duration_tolerance_hours` is reported per row.
 6. **`Type` IS AN ALLOWLIST, NOT A FIELD WE HAVE SEEN THE VOCABULARY OF.** `Work` is the only value
    in this file. A type nobody has ruled on — a break, a lunch, a holiday, a training code — reads
    as NOT PAYABLE and is reported (the `carrier_dollar_class.component_is_evidence` pattern, index
    §66). Defaulting the other way would silently pay an unpaid break the first time one appears.
 7. **THE BACK-OFFICE LOGIN IS NOT A STORE SHIFT.** 14 of the 45 rows are one back-office login,
    12.48h in bursts of 0:00–5:18 across twelve stores as somebody moves between them in the back
    office. Those must not become paid hours — and RULE TWO forbids the login being named in code,
    so `non_shift_logins` is a per-org CONFIG ARRAY (seeded for the house org by mig 1070) and the
    excluded hours are SURFACED in `excluded`, never silently dropped.
 8. **THE KEY IS (employee, date, PUNCH), NEVER (employee, date).** One person has two rows at two
    stores on the sample day. Hours attribute to each punch's OWN store.
 9. **A ZERO-DURATION ROW IS A MEASURED ZERO, NOT A MISSING MEASUREMENT** (5 rows in the sample,
    clock in == clock out). That distinction already exists — `payroll_hours_basis.MEASURED_ZERO` /
    `salary_expense`'s three states — and is reused rather than re-invented.
10. **`Division` / `Region` / `District` ARE B2B'S OWN ORG, NOT OURS.** `District` reads `PA` / `NJ`
    / `Default District`; MetricsPro's districts are `storeops.org_units`. They are kept as feed
    METADATA and may never resolve a recipient, a market or a store — `clock_variance.ask_plan`
    resolves people through `storeops.org_chain` and nothing else.
11. **THE BUSINESS DATE COMES FROM `Clock In`, NEVER FROM THE FILENAME OR THE UPLOAD.** The filename
    does carry a range (`My_Employee_Time_Sheet_10092026-10092026.Xlsx`) and `filename_claim` reads
    it — but only to ASSERT which day the file claims to cover. Stamping rows from it is the exact
    `feed-row-month-from-upload` defect that once read carrier commission 28× high, so
    `claim_agrees` reports a mismatch instead and the rows keep their own dates.

An OVERNIGHT punch (clock out earlier in the day than clock in) rolls to the next day; there is no
such row in the sample, so the harness pins a synthetic one.

PURE. No DB, no network, no clock, no app imports except the two one-homes it dereferences for
folding and the basis vocabulary. Proof: `backend/harness_clock_feed.py`.
Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §14z.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from app.modules.account import store_identity as _sid
from app.modules.storeops import payroll_hours_basis as _basis

#: The layout key this feed is mapped under (`column_mapping.TABLE_MAP`), and its landing table.
REPORT_KEY = "clock_punch"
TABLE = "raw_clock_punch"

# ── CONFIG (RULE TWO — house defaults; the per-org row mig 1070 adds) ────────────────────────────
DEFAULT_CONFIG = {
    # THE ALLOWLIST (trap 6). Folded case/space-insensitively. An unlisted type is NOT payable.
    "payable_punch_types": ("work",),
    # Logins whose punches are not a store shift (trap 7). EMPTY in code: the real value is a config
    # row, because a login is a tenant's data and naming one here would be per-tenant code.
    "non_shift_logins": (),
    # How far the file's own `Duration Decimal` may sit from the timestamps before it is reported.
    "duration_tolerance_hours": 0.02,
    # How far the file's trailer total may sit from the sum of its rows before the FILE IS REJECTED.
    "trailer_tolerance_hours": 0.05,
    # Reject a file whose trailer does not tie? True — the trailer is free integrity on a pay feed.
    "reject_on_trailer_mismatch": True,
}
_CONFIG_FIELDS = tuple(DEFAULT_CONFIG.keys())


def resolve_config(org_row=None):
    """Merge a (possibly partial / absent) per-org row over the house defaults."""
    eff = dict(DEFAULT_CONFIG)
    for f in _CONFIG_FIELDS:
        v = (org_row or {}).get(f)
        if v is not None:
            eff[f] = v
    eff["payable_punch_types"] = tuple(_fold(t) for t in (eff["payable_punch_types"] or ()) if _fold(t))
    eff["non_shift_logins"] = tuple(_fold(t) for t in (eff["non_shift_logins"] or ()) if _fold(t))
    for f in ("duration_tolerance_hours", "trailer_tolerance_hours"):
        try:
            eff[f] = max(0.0, float(eff[f]))
        except (TypeError, ValueError):
            eff[f] = DEFAULT_CONFIG[f]
    eff["reject_on_trailer_mismatch"] = bool(eff["reject_on_trailer_mismatch"])
    return eff


def _fold(v):
    """The comparison form for a type word or a login: accent-folded (the ONE fold), lower-cased,
    non-alphanumerics dropped. `'M.saul'` and `'msaul'` are the same login; `'Work '` and `'work'`
    the same type."""
    return "".join(ch for ch in _sid.fold_accents(v).lower() if ch.isalnum())


# ── WHY A ROW IS NOT A PAYABLE PUNCH (the vocabulary; part of every exclusion) ───────────────────
EXCL_NON_SHIFT_LOGIN = "non_shift_login"
EXCL_TYPE_NOT_PAYABLE = "type_not_payable"
EXCL_NO_DATE = "no_business_date"
EXCL_NO_TIMES = "no_clock_times"
EXCL_NO_ZONE = "no_timezone_supplied"
EXCL_STORE_UNBOUND = "store_unbound"

EXCLUSION_REASONS = {
    EXCL_NON_SHIFT_LOGIN:
        "This login is on this org's `non_shift_logins` list — a back-office session moving between "
        "stores, not a store shift. Its hours are reported here and excluded from pay.",
    EXCL_TYPE_NOT_PAYABLE:
        "This row's Type is not on this org's `payable_punch_types` allowlist. An unruled type is "
        "never paid on the assumption that it is work; declare it to change that.",
    EXCL_NO_DATE:
        "The row carries no readable clock-in, so it cannot be placed on a business day. A day we "
        "cannot name cannot be compared against anything.",
    EXCL_NO_TIMES:
        "The row carries no usable clock-in / clock-out pair, so no span can be measured from it.",
    EXCL_NO_ZONE:
        "The clock times are local wall clock with no timezone and the caller supplied none for this "
        "store, so converting them would mean inventing an offset.",
    EXCL_STORE_UNBOUND:
        "This store spelling binds to no store in this org through the platform's store resolver. "
        "Reported rather than guessed onto a store — it is a store-setup gap.",
}


def exclusion_reason(kind):
    """PURE: the sentence shown beside an excluded row. An unruled kind says so rather than blank."""
    return EXCLUSION_REASONS.get(kind, f"This exclusion kind ({kind!r}) has no ruling in clock_feed.")


def type_is_payable(punch_type, cfg):
    """PURE: is this `Type` a payable punch? ALLOWLIST — an unknown or missing type is NOT payable.

    THE QUESTION, NOT THE POLICY. A blank type is treated as unknown, deliberately: the sample file
    carries `Work` on every row, so a blank would be a shape change, and a shape change on a pay feed
    must surface rather than default to paid.
    """
    cfg = cfg if isinstance(cfg, dict) else resolve_config(None)
    return _fold(punch_type) in cfg["payable_punch_types"]


def login_is_store_shift(login, cfg):
    """PURE: does this login's punch represent a store shift? Config-driven (trap 7); no login is
    named in code."""
    cfg = cfg if isinstance(cfg, dict) else resolve_config(None)
    key = _fold(login)
    if not key:
        # A row with no login at all is not evidence of a back-office session; it is a row whose
        # login column is blank. It stays a candidate punch and its other tests still apply.
        return True
    return key not in cfg["non_shift_logins"]


# ── DURATION ─────────────────────────────────────────────────────────────────────────────────────
_HHMM = re.compile(r"^\s*(\d{1,5}):([0-5]\d)(?::([0-5]\d))?\s*$")


def parse_duration(v):
    """PURE: `'8:28'` → 8.466666…, `'249:10'` → 249.1666…, `'0:00'` → 0.0, anything else → None.

    HOURS MAY EXCEED 24 — the trailer total is `249:10` — so this is NOT a time-of-day parse and
    must never be fed through one. A number is returned as itself (the `Duration Decimal` column),
    so one function reads both of the file's two duration spellings.
    """
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = _HHMM.match(str(v or ""))
    if not m:
        return None
    h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    return h + mi / 60.0 + se / 3600.0


def _naive(v):
    """PURE: a datetime with no tzinfo, or None. Accepts a datetime object (what the sheet reader
    hands back) and the ISO-ish strings a CSV export of the same report would carry."""
    if isinstance(v, datetime):
        return v.replace(tzinfo=None) if v.tzinfo is None else v
    s = str(v or "").strip()
    if not s:
        return None
    s = s.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M",
                "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def span_hours(clock_in, clock_out):
    """PURE: `(hours, rolled_overnight)` from two LOCAL naive stamps.

    An out EARLIER than the in is an OVERNIGHT shift and rolls to the next day — a negative span is
    never returned, and the roll is reported so a reader can see it happened. There is no overnight
    row in the sample file, which is exactly why the harness pins a synthetic one: an unexercised
    branch on a pay feed is an untested branch.
    """
    a, b = _naive(clock_in), _naive(clock_out)
    if a is None or b is None:
        return None, False
    rolled = False
    if b < a:
        b = b + timedelta(days=1)
        rolled = True
    return (b - a).total_seconds() / 3600.0, rolled


def to_utc(local_stamp, tz):
    """PURE given its inputs: a LOCAL naive stamp + the store's ZoneInfo → an aware UTC datetime.

    `tz` is resolved by the CALLER through `storeops/router._biz_tz_for_store` (the store's own zone,
    else the tenant's, else the house default) — this module spells no zone and no offset, so a
    multi-zone tenant and a DST boundary are both the platform's existing answer rather than a second
    one. `tz=None` returns None: inventing an offset is the one thing that must not happen quietly.
    """
    a = _naive(local_stamp)
    if a is None or tz is None:
        return None
    try:
        from datetime import timezone
        return a.replace(tzinfo=tz).astimezone(timezone.utc)
    except Exception:
        return None


# ── THE TRAILER (trap 1) ─────────────────────────────────────────────────────────────────────────
def trailer_tie_out(row_hours, trailer_value, cfg):
    """PURE: does the file tie to its own trailer?

    `row_hours` is the per-row hours this module measured; `trailer_value` is the trailer row's
    `Duration` (`'249:10'`) or its decimal. Returns `{ties, rows_total, trailer_total, delta,
    tolerance, reject, reason}`.

    THE POINT. The trailer is a checksum the exporter computed over the same rows, so a file whose
    rows do not sum to it is TRUNCATED, FILTERED or DOUBLED — and this feed is about to decide pay.
    The carrier-statement work ties to the penny for the same reason; a feed that hands us its own
    total and is not checked against it is free integrity thrown away. A file with NO trailer is not
    a failure (`ties` True, `trailer_total` None): absence of a checksum is not a failed checksum.
    """
    cfg = cfg if isinstance(cfg, dict) else resolve_config(None)
    total = round(sum(float(h or 0) for h in (row_hours or ())), 4)
    claimed = parse_duration(trailer_value)
    tol = float(cfg["trailer_tolerance_hours"])
    if claimed is None:
        return {"ties": True, "rows_total": round(total, 2), "trailer_total": None,
                "delta": None, "tolerance": tol, "reject": False,
                "reason": "This file carries no trailer total, so there is nothing to tie to. "
                          "Absence of a checksum is not a failed checksum."}
    delta = round(total - claimed, 4)
    ties = abs(delta) <= tol
    return {"ties": ties, "rows_total": round(total, 2), "trailer_total": round(claimed, 2),
            "delta": round(delta, 2), "tolerance": tol,
            "reject": (not ties) and cfg["reject_on_trailer_mismatch"],
            "reason": ("The file's rows sum to its own trailer total."
                       if ties else
                       f"The file's rows sum to {total:.2f}h but its own trailer claims "
                       f"{claimed:.2f}h — a gap of {delta:+.2f}h, beyond the {tol:.2f}h this org "
                       f"allows. The export is truncated, filtered or doubled; nothing was landed, "
                       f"because a clock feed that does not tie to its own total must not decide "
                       f"anybody's pay.")}


# ── THE FILENAME'S CLAIM (trap 11) ───────────────────────────────────────────────────────────────
_FNAME_RANGE = re.compile(r"(\d{2})(\d{2})(\d{4})\s*-\s*(\d{2})(\d{2})(\d{4})")
_FNAME_ONE = re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{4})(?!\d)")


def filename_claim(filename):
    """PURE: `(first_iso, last_iso)` the FILENAME claims to cover, or `(None, None)`.

    `My_Employee_Time_Sheet_10092026-10092026.Xlsx` → `('2026-10-09', '2026-10-09')`; a single
    `…_10092026.xlsx` → that one day twice. MMDDYYYY, which is what the exporter writes.

    ⚠ THIS IS USED ONLY TO ASSERT, NEVER TO STAMP. Every row's business date comes from its own
    `Clock In` (`row_business_date`). Stamping rows from the filename or the upload is the defect
    that once read carrier commission 28× high.
    """
    s = str(filename or "")
    m = _FNAME_RANGE.search(s)
    if m:
        a = f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
        b = f"{m.group(6)}-{m.group(4)}-{m.group(5)}"
        return a, b
    m = _FNAME_ONE.search(s)
    if m:
        d = f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
        return d, d
    return None, None


def claim_agrees(filename, dates):
    """PURE: do the rows' own business dates fall inside what the filename claims?

    Returns `{claimed, dates, agrees, outside, reason}`. A mismatch is REPORTED and the rows keep
    their own dates — the file is not rejected for it, because the filename is the weaker evidence
    of the two and a renamed attachment must not throw away real punches.
    """
    lo, hi = filename_claim(filename)
    ds = sorted({str(d)[:10] for d in (dates or ()) if str(d or "")[:10]})
    if lo is None:
        return {"claimed": None, "dates": ds, "agrees": True, "outside": [],
                "reason": "The filename names no date range, so there is nothing to assert against."}
    if hi < lo:
        lo, hi = hi, lo
    outside = [d for d in ds if d < lo or d > hi]
    return {"claimed": [lo, hi], "dates": ds, "agrees": not outside, "outside": outside,
            "reason": ("Every row's own business date falls inside the range the filename claims."
                       if not outside else
                       f"{len(outside)} business date(s) in this file fall outside the "
                       f"{lo}..{hi} range its filename claims ({', '.join(outside[:5])}). The rows "
                       f"keep THEIR OWN dates — a filename is never allowed to stamp a punch — but "
                       f"the file is probably not the day it says it is.")}


def row_business_date(clock_in):
    """PURE: the business date of one row, from its CLOCK IN and nothing else (trap 11)."""
    a = _naive(clock_in)
    return a.date().isoformat() if a is not None else None


# ── THE NORMALISATION ────────────────────────────────────────────────────────────────────────────
def normalize(records, cfg, *, resolve_store=None, tz_for_store=None, filename=None,
              trailer_value=None):
    """PURE given its injected resolvers: the mapped rows of ONE clock report → what it says.

    `records`       the rows AFTER `column_mapping` has mapped the headers onto the canonical fields
                    (`store_raw`, `employee_raw`, `clock_in`, `clock_out`, `hours`, `type`,
                    `feed_login`, …) and AFTER `drop_footer_rows` has removed the trailer.
    `resolve_store` INJECTED: `account/store_identity.build_store_resolver` over the org's own
                    `store_mapping` + `store_aliases` (28/28 on the sample). Never written here.
    `tz_for_store`  INJECTED: `store_code -> ZoneInfo`, the platform's own
                    `storeops/router._biz_tz_for_store`. Never a literal offset.
    `trailer_value` the trailer row's `Duration`, kept aside by the caller for the tie-out.

    Returns `{punches, excluded, rejected, tie_out, claim, totals}`.
      `punches`  one entry per PAYABLE punch, keyed (store_code, employee_raw, business_date,
                 clock_in) — the punch grain, never (employee, date) (trap 8).
      `excluded` every row that is not a payable punch, WITH its reason and its hours, so the
                 back-office total and any unruled type are visible rather than missing.
      `rejected` set when the file does not tie to its own trailer: NOTHING should be landed.
    """
    cfg = cfg if isinstance(cfg, dict) else resolve_config(None)
    punches, excluded = [], []
    measured_hours = []

    for r in records or ():
        r = r or {}
        store_raw = str(r.get("store_raw") or "").strip()
        emp_raw = str(r.get("employee_raw") or "").strip()
        login = str(r.get("feed_login") or "").strip()
        ptype = r.get("type")
        day = row_business_date(r.get("clock_in"))
        hours, rolled = span_hours(r.get("clock_in"), r.get("clock_out"))
        claimed_decimal = parse_duration(r.get("hours"))
        row = {"store_raw": store_raw, "employee_raw": emp_raw, "feed_login": login,
               "type": ptype, "business_date": day,
               "clock_in_local": str(r.get("clock_in") or ""), "clock_out_local": str(r.get("clock_out") or ""),
               "hours": None if hours is None else round(hours, 4),
               "feed_hours_claimed": None if claimed_decimal is None else round(claimed_decimal, 4),
               "overnight": bool(rolled),
               "feed_division": r.get("division"), "feed_region": r.get("region"),
               # ⚠ B2B's OWN district (trap 10). Metadata. It never resolves a recipient, a market
               # or a store — `clock_variance.ask_plan` walks `storeops.org_chain` and nothing else.
               "feed_district": r.get("district"),
               "clock_in_note": r.get("clock_in_note"), "clock_out_note": r.get("clock_out_note")}

        def drop(kind):
            excluded.append({**row, "excluded": kind, "reason": exclusion_reason(kind)})

        if not login_is_store_shift(login, cfg):
            drop(EXCL_NON_SHIFT_LOGIN)
            continue
        if not type_is_payable(ptype, cfg):
            drop(EXCL_TYPE_NOT_PAYABLE)
            continue
        if not day:
            drop(EXCL_NO_DATE)
            continue
        if hours is None:
            drop(EXCL_NO_TIMES)
            continue

        store_code = store_raw
        if resolve_store is not None:
            try:
                store_code = str(resolve_store(store_raw) or "").strip()
            except Exception:
                store_code = ""
        if not store_code:
            drop(EXCL_STORE_UNBOUND)
            continue
        row["store_code"] = store_code

        tz = None
        if tz_for_store is not None:
            try:
                tz = tz_for_store(store_code)
            except Exception:
                tz = None
        if tz is None:
            drop(EXCL_NO_ZONE)
            continue
        ci = to_utc(r.get("clock_in"), tz)
        co = to_utc(r.get("clock_out"), tz)
        if co is not None and ci is not None and co < ci:
            co = co + timedelta(days=1)
        row["clock_in"] = ci.isoformat() if ci else None
        row["clock_out"] = co.isoformat() if co else None

        # Trap 5: the file's own decimal is a CROSS-CHECK, never the source of the number.
        if claimed_decimal is not None and abs(claimed_decimal - hours) > cfg["duration_tolerance_hours"]:
            row["duration_disagrees_by"] = round(claimed_decimal - hours, 4)
        # Trap 9: zero IS a measurement here. The row is kept, with the basis the platform already
        # uses for it, so a measured zero can never be mistaken for a missing measurement.
        row["basis"] = _basis.MEASURED_ZERO if round(hours, 4) == 0 else _basis.CLOCKED
        punches.append(row)
        measured_hours.append(hours)

    # THE TIE-OUT IS OVER EVERY ROW THE FILE CARRIED, payable or not — the trailer is the exporter's
    # total of its own sheet, so excluding the back-office rows from the comparison would make a
    # correct file read as not tying. A row with no measurable span falls back to its own declared
    # decimal, so an unparseable timestamp cannot fake a shortfall.
    tie_hours = [p["hours"] for p in punches]
    for e in excluded:
        h = e.get("hours")
        tie_hours.append(float(h) if h is not None else float(e.get("feed_hours_claimed") or 0))
    tie = trailer_tie_out(tie_hours, trailer_value, cfg)
    claim = claim_agrees(filename, [p["business_date"] for p in punches]
                                    + [e.get("business_date") for e in excluded])
    excl_h = round(sum(float(e.get("hours") if e.get("hours") is not None
                             else (e.get("feed_hours_claimed") or 0)) for e in excluded), 2)
    return {
        "punches": punches,
        "excluded": excluded,
        "rejected": tie["reject"],
        "tie_out": tie,
        "claim": claim,
        "totals": {
            "rows": len(punches) + len(excluded),
            "punch_rows": len(punches),
            "punch_hours": round(sum(measured_hours), 2),
            "excluded_rows": len(excluded),
            "excluded_hours": excl_h,
            "excluded_by_reason": _tally(excluded, "excluded"),
            "zero_duration_rows": sum(1 for p in punches if p["basis"] == _basis.MEASURED_ZERO),
            "overnight_rows": sum(1 for p in punches if p["overnight"]),
            "duration_disagreements": sum(1 for p in punches if "duration_disagrees_by" in p),
            "stores": len({p["store_code"] for p in punches}),
            "business_dates": sorted({p["business_date"] for p in punches}),
        },
    }


def _tally(rows, key):
    out = {}
    for r in rows or ():
        k = (r or {}).get(key)
        cell = out.setdefault(k, {"rows": 0, "hours": 0.0})
        cell["rows"] += 1
        cell["hours"] = round(cell["hours"] + float((r or {}).get("hours") or 0), 2)
    return out


def landing_rows(org_id, result, *, source_file=None, period=None):
    """The `storeops.raw_clock_punch` rows for ONE normalised file — the CLAIM, as it was received.

    Every row the file carried is landed, PAYABLE OR NOT, with `excluded` naming why it is not a
    payable punch. A landing that silently dropped the back-office rows could never explain where
    12.48 hours went, and the whole posture of this feed is that it is evidence we keep, not a number
    we trust.

    It is `storeops.raw_clock_punch` and NEVER `storeops.timelog` / `storeops.shifts.actual_hours`:
    our punches are a MEASUREMENT, this file is somebody else's CLAIM about the same day, and
    `clock_variance` is the one home for the difference.
    """
    rows = []
    for r in (result or {}).get("punches", []) + (result or {}).get("excluded", []):
        rows.append({
            "org_id": org_id, "period": period,
            "store_raw": r.get("store_raw") or None, "store_code": r.get("store_code") or None,
            "employee_raw": r.get("employee_raw") or None, "feed_login": r.get("feed_login") or None,
            "business_date": r.get("business_date"),
            "clock_in": r.get("clock_in"), "clock_out": r.get("clock_out"),
            "clock_in_local": r.get("clock_in_local") or None,
            "clock_out_local": r.get("clock_out_local") or None,
            "hours": r.get("hours"), "feed_hours_claimed": r.get("feed_hours_claimed"),
            "punch_type": (str(r.get("type")) if r.get("type") is not None else None),
            "excluded": r.get("excluded"),
            "overnight": bool(r.get("overnight")),
            "feed_division": r.get("feed_division") or None,
            "feed_region": r.get("feed_region") or None,
            "feed_district": r.get("feed_district") or None,
            "source_file": source_file,
        })
    return rows


# ── THE ONE IMPURE FUNCTION (the `carrier_dollar_class.load_config` convention) ──────────────────
#: The feed's shape knobs live in the SAME per-org row the hours basis and the variance gate read
#: (mig 1070) — one row, one read, no third config table for one feature.
CONFIG_TABLE = _basis.CONFIG_TABLE
_CONFIG_CACHE = {}


def load_config(client, org_id, *, use_cache=True):
    """The per-org row merged over the house defaults. Every failure mode — no row, no table, an
    unreachable database — returns the house defaults, under which the back-office exclusion list is
    EMPTY and only `work` is payable. A config read that failed must never widen what gets paid."""
    key = str(org_id)
    if use_cache and key in _CONFIG_CACHE:
        return _CONFIG_CACHE[key]
    row = None
    try:
        rows = (client.table(CONFIG_TABLE).select("*").eq("org_id", org_id).limit(1).execute().data) or []
        row = rows[0] if rows else None
    except Exception:
        row = None
    cfg = resolve_config(row)
    cfg["config_row_present"] = row is not None
    if use_cache:
        _CONFIG_CACHE[key] = cfg
    return cfg


def invalidate_config(org_id=None):
    if org_id is None:
        _CONFIG_CACHE.clear()
    else:
        _CONFIG_CACHE.pop(str(org_id), None)
