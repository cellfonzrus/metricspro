"""DEVICE PAYABLE AS AT A DATE — of the devices the distributor billed us, which were still unpaid
on a given day, by COMPANY and by STORE.

OWNER, verbatim (2026-09-11): *"I need the payable at the end of the year accounts. Payable on
12/31/2025 company wise"*, then *"we need to check which of the imei a billed in 2025 got paid in
2025 and which ones were paid in 2026"*.

That second sentence is the whole specification. The question is not "what is the open balance
today" — the platform already answers that (`GET /account/liabilities-due`, and the balance-sheet
distributor payable, index §4/§23n). It is a BACKDATED question: standing on 31 December, which
units that had already been billed had not yet been paid for? Only a per-unit PAYMENT DATE can
answer that, and nothing in this platform was reading one.

═══════════════ THE MECHANISM: TWO DATES PER SERIALISED UNIT ══════════════════════════════════════
`commcalc.asset_ledger` carries, per unit, the date that unit was actually paid for (`payg_date`)
alongside what was owed on it (`owed_to_vip`). `commcalc.vip_invoice_devices` carries, per unit, the
invoice it arrived on and when. Join them and every device has two dates — BILLED on X, PAID on Y:

    payable as at D  =  units invoiced on or before D whose payment date is after D, or absent.

WHY `payg_date` IS ACCEPTED AS THE PAYMENT DATE — measured, not assumed. Summing `owed_to_vip`
grouped by `payg_date` year and comparing against the distributor's own settled payment batches
(`commcalc.vip_paygo_payments`) agrees to within 2.7% in both fully-covered years (house org,
measured 2026-09-11: ledger $6,891,830.12 vs batches $6,712,367.33 for 2025; $4,441,540.54 vs
$4,326,473.83 for 2026). Two independently-sourced numbers landing that close is what licenses the
column. That comparison is pinned as a CONTRACT in `harness_device_payable.py` §E, so if the feed
ever changes what `payg_date` means, the proof breaks before the report lies.

═══════════════ THE JOIN KEY: THE COLUMN NAMES LIE. DO NOT "FIX" THIS BACK ════════════════════════
`vip_invoice_devices.imei` DOES NOT HOLD THE IMEI. It holds the SIM/ICCID — 18 characters, and on
this feed 43,357 of 49,196 rows are exactly 18 long. The 15-digit handset IMEI is in the column
named `serial` (49,164 of 49,196 rows are exactly 15 long), and `asset_ledger.esn_imei` is that same
15-digit IMEI (32,705 of 32,709 distinct keys are 15 long).

Measured consequence on the house org's 2025 devices: joining on the column CALLED `imei` matches
**4 rows of 19,571**. Joining on `serial` matches **19,352**. A reader who "corrects" the join to the
obviously-named column silently deletes 99.98% of this report, and it will still render a confident
number. That is why this paragraph, and the comment on the join itself, exist.

Both sides are normalised (non-alphanumerics stripped, upper-cased) before comparison, and a ledger
key with NO DIGIT IN IT is not a serial at all — the ledger carries per-invoice fee rows under
non-serial labels. That test is a SHAPE test on the data, never a list of label spellings, so it
keeps working for the next feed (RULE TWO).

═══════════════ COVERAGE IS LIMITED, AND THE REPORT SAYS SO INSTEAD OF GUESSING ═══════════════════
`asset_ledger` is a wipe-and-reinsert CURRENT snapshot and it has been PRUNED of old rows. Measured
house org: 72 rows acquired in 2023 and 1,391 in 2024, against 1,504 and 16,195 units actually
invoiced in those years. A payable computed for a 2024 date would therefore come out small,
confident and WRONG.

So the coverage boundary is DERIVED FROM THE DATA, not written down: month by month, ledger rows are
counted against units invoiced, and coverage begins after the last month where that ratio collapses
(`coverage_scan`). On the house org that lands on 2025-01 — 0.58 in December 2024, 0.99 in January
2025 — but no year is hard-coded, and an org whose ledger is complete from 2022 gets 2022.

An as-at date outside that window is **NOT MEASURED**: `payable_amount` comes back `None` with a
reason and the derived window, and the page renders that instead of a figure. Three states, never a
bare $0.00 — measured / genuinely zero / not measured — which is the house silent-zero rule, and the
only reason this report can be trusted at all.

═══════════════ WHAT THIS METHOD CANNOT SEE, STATED IN THE PAYLOAD ════════════════════════════════
Only SERIALISED units are in `asset_ledger`. Chargebacks, loans/exchanges, managed services, SIM
packs and activation fees are not devices, have no serial, and are invisible to the join. They are
not small: a $159,056.76 Return Item Chargeback plus a $50 NSF fee, invoiced 2025-12-29, sat right on
the year-end date this report was asked about.

They are therefore reported as a SEPARATE, DIFFERENTLY-BASED section — never folded into the device
payable, never omitted. Their basis is BILLED, not OUTSTANDING: there is no per-line payment date for
a non-device item anywhere in the feed, so whether each was settled by the as-at date is honestly
`not_measured` rather than assumed either way. The payload says so and the page prints it.

Classification reuses `device_purchases.device_product_names` / `.classify_line` verbatim — the same
data-driven "a line is a device when that product actually arrived serialised" rule the Device
Purchases report (§23y) already proves. No second definition of what a device is.

═══════════════ A VOIDED INVOICE IS NOT A PAYABLE ═════════════════════════════════════════════════
An invoice whose header `status` says VOIDED was cancelled: we do not owe it. Its units stay in the
feed, and until 2026-09-11 both this report and the Device Purchases report counted them. Neither
does now, and they were changed TOGETHER — two finance reports disagreeing about voided invoices is
a defect that survives until someone reconciles them, and then costs both their credibility.

There is exactly ONE voided rule in the platform: `device_purchases.VOID_STATUSES` and
`device_purchases.voided_invoice_set`, imported here rather than redefined. The status comes from
`commcalc.vip_invoices` — the HEADER is what a status means.

Measured house org, as at 2025-12-31: the payable falls from $489,136.63 to **$484,696.79** (16
units, $4,439.84). A further 10 units on voided invoices were already paid ($4,359.90) and 10 more
are not in the unit ledger at all; all of them leave every figure together. Nothing is silently
dropped — `totals.excluded_voided_*` reports exactly what left, so the earlier number is explainable
rather than merely gone.

═══════════════ DUPLICATE CHECK (CLAUDE.md build gate) ════════════════════════════════════════════
Searched `docs/SYSTEM_DATA_FLOW_INDEX.md` before building. Nothing backdates a per-unit payable:

  · `GET /account/liabilities-due` (§4) — CURRENT state. Its `date` parameter shifts the
    due-THIS-WEEK window; it does not move the as-of of the balance. It cannot answer 31 December.
  · §23n `owed_vip` / balance-sheet `vip_ap` (`balance_sheet.asset_ledger_open_bookings`, mig 954)
    — sums `owed_to_vip` on rows whose STATUS is open, as-of today. Status is a snapshot: a unit paid
    in 2026 reads "paid" when you ask about 2025. Status cannot be backdated; a payment DATE can.
  · §13/§13a `device_cogs` — cost of units SOLD, recognised at sale. A different question entirely.
  · §23y `device_purchases` — what was BILLED in a window. This report is its natural sibling and
    SHARES it rather than copying it: the device vocabulary and line classification, the
    codeless-store_mapping-row rule, and the store/company placement all come from that module.
  · The `payables` module (`/payables`, `device_payable_ledger`, mig 095) — a per-IMEI FORECASTING
    ledger, rebuilt by `POST /payables/rebuild`, whose `/owed-by-date` reads DUE dates going forward.
    It is a forward-looking purchasing tool over a rebuilt table, not an as-at historical statement,
    and it holds no distributor payment date. Nothing here writes to it or duplicates it.

Store and company come from `coa.store_resolver` (§13/§13a) and `coa.build_company_matcher` (§13b),
called EXACTLY as they are. `account/coa.py` is not touched, so no booked figure can move.

READ-ONLY. Books nothing, writes nothing, no migration.
PURE except `compute()`. Proof: backend/harness_device_payable.py.
"""
from app.modules.commcalc.calculator import safe_float
from app.modules.account import device_purchases as dp

# Labelled buckets — an unplaced row is a ROW WITH A LABEL, never a row that vanished.
STORE_NOT_MAPPED = dp.STORE_NOT_MAPPED
COMPANY_NOT_MAPPED = dp.COMPANY_NOT_MAPPED
NOT_A_RETAIL_LOCATION = "(not a retail location)"

# Per-unit payment state at the as-at date. THREE states on purpose:
#   paid     the unit carries a payment date on or before the as-at date;
#   unpaid   it carries one AFTER the as-at date — it was still owed on that day;
#   unknown  the ledger row exists but carries NO payment date. It is counted INTO the payable
#            (we cannot evidence that it was paid) and reported separately, so a reader can see
#            exactly how much of the figure rests on an absent date rather than a later one.
PAYMENT_STATES = ("paid", "unpaid", "unknown")

# THE PLATFORM'S ONE VOIDED RULE, imported rather than redefined (see the docstring). Re-exported
# under this module's name so a reader of either report finds the same vocabulary in the same place.
VOID_STATUSES = dp.VOID_STATUSES

# COVERAGE. A month counts as covered when the ledger holds at least this share of the units
# invoiced that month. Measured house org: the collapse is not subtle — 0.02-0.09 through 2024,
# 0.58 in the transition month, then 0.99-1.08 every month after. Any threshold in that gap gives
# the same boundary, which is what makes the derivation safe rather than tuned.
COVERAGE_RATIO = 0.70
# Months with fewer invoiced units than this are not evidence either way and are not judged — a
# three-unit month would otherwise flip a boundary on noise.
COVERAGE_MIN_DEVICES = 20


def _t(v):
    return str(v or "").strip()


def as_date(v):
    """PURE: the ISO date prefix of a date/timestamp string ('2025-12-29T00:00:00+00:00' →
    '2025-12-29'); '' when there is nothing readable. ISO dates compare correctly as strings, which
    is why every comparison below is a string comparison and no timezone is ever guessed."""
    s = _t(v)
    if len(s) < 10 or s[4] != "-" or s[7] != "-":
        return ""
    d = s[:10]
    return d if d[:4].isdigit() and d[5:7].isdigit() and d[8:10].isdigit() else ""


def month_of(v):
    """PURE: 'YYYY-MM' of a date/timestamp, or ''."""
    d = as_date(v)
    return d[:7] if d else ""


def month_start(m):
    """PURE: the first day of 'YYYY-MM' as an ISO date."""
    return m + "-01"


# ── THE JOIN (pure) ───────────────────────────────────────────────────────────────────────────────
def norm_key(v):
    """PURE: the join key — non-alphanumerics stripped, upper-cased. Applied to BOTH sides so a
    dash, a space or a lower-case letter can never split a match."""
    return "".join(ch for ch in str(v or "") if ch.isalnum()).upper()


def is_serial_key(k):
    """PURE: could this key be a serialised unit's identifier at all?

    A serial contains DIGITS. The asset ledger also carries per-invoice fee rows whose identifier
    column holds a label instead of a serial (house org: two such labels, 1,316 rows between them) —
    they have no digit in them, so this shape test excludes them without naming one of them in code.
    Naming them would be a vocabulary in code, and the next feed would spell them differently."""
    return bool(k) and any(ch.isdigit() for ch in k)


def ledger_index(ledger_rows):
    """PURE: {normalised serial -> the ledger row}, plus what was skipped and why.

    ══ THE COLUMN NAMES LIE — READ THIS BEFORE CHANGING THE KEY ══
    The unit key on the INVOICE side is `vip_invoice_devices.serial`, NOT the column named `imei`:
    that one holds the SIM/ICCID (18 chars on this feed), while `serial` holds the 15-digit handset
    IMEI, which is what `asset_ledger.esn_imei` holds. Measured on the house org's 19,571 units
    invoiced in 2025: joining on `serial` matches 19,352; joining on the column CALLED `imei`
    matches 4. If you "correct" this to the obviously-named column the report still renders — it
    just renders a number that is 99.98% missing. Leave the key on `serial`.

    First row wins on a repeated key; `duplicate_keys` counts the collisions so a ledger that starts
    carrying a serial twice is visible rather than silently arbitrary."""
    index, dup, skipped, blank = {}, 0, 0, 0
    for r in (ledger_rows or []):
        k = norm_key(r.get("esn_imei"))
        if not k:
            blank += 1
            continue
        if not is_serial_key(k):
            skipped += 1
            continue
        if k in index:
            dup += 1
            continue
        index[k] = r
    return index, {"keys": len(index), "duplicate_keys": dup,
                   "non_serial_rows": skipped, "blank_key_rows": blank}


def payment_state(ledger_row, as_at):
    """PURE: 'paid' | 'unpaid' | 'unknown' for one matched unit at the as-at date."""
    pd = as_date((ledger_row or {}).get("payg_date"))
    if not pd:
        return "unknown"
    return "paid" if pd <= as_at else "unpaid"


def payment_evidence(ledger_rows, settled_rows):
    """PURE: the measurement that LICENSES `payg_date` as the payment date, re-run every time the
    report runs instead of being asserted once in a docstring.

    Two independently-sourced numbers, per calendar year: what the per-unit ledger says was paid
    that year (Σ `owed_to_vip` grouped by `payg_date` year) against what the distributor's own
    settled payment batches say was paid that year (Σ `vip_paygo_payments.amount`). The batches are
    a different feed, swept separately, and know nothing about units.

    If those two land close, `payg_date` means what this report needs it to mean. House org measured
    2026-09-11: 2025 $6,891,830.12 vs $6,712,367.33 (2.7%), 2026 $4,441,540.54 vs $4,326,473.83
    (2.7%). The day that agreement breaks, the report is lying and this is where it shows.

    Reported, never enforced: a year with a genuine timing difference must not blank the page. The
    numbers are put in front of the reader, which is the honest form of a validation the report
    cannot itself adjudicate."""
    led, batch = {}, {}
    for r in (ledger_rows or []):
        y = as_date(r.get("payg_date"))[:4]
        if y:
            led[y] = led.get(y, 0.0) + safe_float(r.get("owed_to_vip"))
    for p in (settled_rows or []):
        y = _t(p.get("period_year")) or as_date(p.get("created_on"))[:4]
        if y:
            batch[y] = batch.get(y, 0.0) + safe_float(p.get("amount"))
    out = []
    for y in sorted(set(led) | set(batch)):
        a, b = round(led.get(y, 0.0), 2), round(batch.get(y, 0.0), 2)
        out.append({"year": y, "ledger_paid": a, "settled_batches": b,
                    "difference": round(a - b, 2),
                    # None, not 0 — an agreement percentage against a year with no batches is not a
                    # measurement, and printing 0.0% would say the two agree perfectly.
                    "variance_pct": (round(abs(a - b) / b * 100, 2) if b else None)})
    return out


# ── COVERAGE (pure) ───────────────────────────────────────────────────────────────────────────────
def coverage_scan(ledger_rows, device_rows, ratio=COVERAGE_RATIO, min_devices=COVERAGE_MIN_DEVICES):
    """PURE: where does this org's ledger actually have the units, month by month?

    The ledger is a wipe-and-reinsert CURRENT snapshot that has been pruned of history, so it holds
    almost nothing for older months while the invoice feed holds everything. Comparing the two per
    month makes the prune visible as an arithmetic collapse, and the boundary falls out of the data:

      covered month   = ledger rows acquired that month / units invoiced that month >= `ratio`,
                        judged only on months with at least `min_devices` invoiced units;
      start_month     = the first judged, COVERED month after the last collapse — the first month
                        the ledger can actually evidence. Deliberately not "the month after the
                        gap": that month may hold no rows at all, and a window opening on a month
                        the ledger never reached would claim coverage it does not have;
      end_month       = the last month the ledger holds any row for. Beyond it the snapshot simply
                        has not caught up yet — that is FRESHNESS, not pruning, so it is reported as
                        a stale tail rather than refusing the whole answer.

    No year, no tenant and no carrier appears here: an org whose ledger is complete from 2022 gets
    2022, and one with no ledger at all gets `start_month=None`, which is "not measured" everywhere.
    """
    led, inv = {}, {}
    for r in (ledger_rows or []):
        m = month_of(r.get("acquired_date"))
        if m:
            led[m] = led.get(m, 0) + 1
    for d in (device_rows or []):
        m = month_of(d.get("created_on"))
        if m:
            inv[m] = inv.get(m, 0) + 1

    end_month = max(led) if led else None
    months = sorted(set(led) | set(inv))
    rows, last_gap = [], None
    for m in months:
        lc, ic = led.get(m, 0), inv.get(m, 0)
        judged = ic >= min_devices and (end_month is None or m <= end_month)
        r = lc / ic if ic else None
        covered = (r is not None and r >= ratio) if judged else None
        rows.append({"month": m, "ledger_rows": lc, "invoiced_devices": ic,
                     "ratio": (round(r, 4) if r is not None else None),
                     "judged": judged, "covered": covered})
        if judged and covered is False:
            last_gap = m                        # the LAST month the ledger demonstrably lacks

    # Coverage starts at the first month we can actually EVIDENCE — the first judged, covered month
    # after the last collapse. Not merely "the month after the gap": that month may hold no data at
    # all, and a window opening on a month the ledger never reached would claim coverage it has not
    # got. When every judged month collapsed, nothing is measurable and `start_month` stays None,
    # which is "not measured" everywhere downstream rather than a $0.00 payable.
    start = None
    for r in rows:
        if r["judged"] and r["covered"] and (last_gap is None or r["month"] > last_gap):
            start = r["month"]
            break
    return {"start_month": start, "end_month": end_month, "months": rows,
            "ratio_threshold": ratio, "min_devices_judged": min_devices}


def coverage_state(cov, as_at):
    """PURE: can this as-at date be answered at all, and if not, exactly why?

    Returns (state, reason, stale_tail). `state` is 'measured' or 'not_measured' — the two of the
    three silent-zero states that this decision can produce; a measured-and-genuinely-zero payable
    is a real 0.00 and is not this function's business."""
    start, end = cov.get("start_month"), cov.get("end_month")
    if not start:
        return ("not_measured",
                "the per-unit ledger holds no month with enough coverage to evidence a payable",
                False)
    m = month_of(as_at + "-01") if len(as_at) == 7 else as_at[:7]
    if m < start:
        return ("not_measured",
                "the per-unit ledger is a current snapshot that has been pruned of rows older than "
                "%s, so a payable at this date would be confidently wrong rather than merely "
                "uncertain" % start,
                False)
    return ("measured", None, bool(end and m > end))


# ── THE AGGREGATION (pure) ────────────────────────────────────────────────────────────────────────
def _bucket(store, company, company_id, market, how):
    return {"company": company, "company_id": company_id, "store": store, "market": market,
            "resolved_by": how, "payable_amount": 0.0, "payable_devices": 0,
            "paid_amount": 0.0, "paid_devices": 0, "unknown_devices": 0, "unmatched_devices": 0}


def aggregate(device_rows, line_rows, ledger_rows, as_at, place_store, company_of,
              company_names=None, market_of=None, settled_rows=None, invoice_rows=None,
              void_statuses=VOID_STATUSES):
    """PURE: the whole report from rows + the two SHARED resolvers, passed in as functions.

      device_rows  — commcalc.vip_invoice_devices (serial, imei, location, created_on, product_name)
      line_rows    — commcalc.vip_invoice_lines   (name, total, location, created_on, invoice_number)
      ledger_rows  — commcalc.asset_ledger        (esn_imei, payg_date, owed_to_vip, acquired_date)
      settled_rows — the distributor's own settled payment batches (amount, period_year) — the
                     SECOND, independent feed that `payment_evidence` measures `payg_date`
                     against, so the licence for the whole method is re-checked on every run
      invoice_rows — commcalc.vip_invoices headers (invoice_number, status) — used ONLY to
                     DECLARE what sits on a voided invoice, never to drop it (see the docstring)
      as_at        — 'YYYY-MM-DD'; the day we are standing on
      place_store  — dp.store_placer(coa.store_resolver(...), known) : location -> (store, how)
      company_of   — coa.build_company_matcher(rows, None)           : key -> company_id or None
    """
    names = company_names or {}
    mk = market_of or (lambda _s: "")
    index, ledger_stats = ledger_index(ledger_rows)
    cov = coverage_scan(ledger_rows, device_rows)
    state, reason, stale = coverage_state(cov, as_at)
    win_from = month_start(cov["start_month"]) if cov.get("start_month") else None

    def place(raw_location):
        """Store, company and market for one raw invoice location, from the SHARED resolvers only.

        A location that is not one of our retail stores still gets its COMPANY asked of the same
        shared matcher, on the raw string. That is how the distributor's own master/dealer ACCOUNT
        stays visible as its own named row — it is a legal entity we really do owe, it carries a
        company assignment, and it must not be absorbed into a retail store (its street number would
        otherwise be matched against one) nor quietly dropped. Its STORE stays labelled as not a
        retail location, because it is not one."""
        addr, how = place_store(raw_location)
        key = addr if addr is not None else _t(raw_location)
        cid = company_of(key) if key else None
        company = (names.get(cid) or names.get(str(cid)) or str(cid)) if cid else COMPANY_NOT_MAPPED
        if addr is None:
            return (NOT_A_RETAIL_LOCATION, company, cid, "", how)
        return (addr, company, cid, mk(addr) or "", how)

    # A VOIDED INVOICE IS NOT A PAYABLE. The set is built by the SHARED rule — one definition for
    # this report and Device Purchases, so the two can never drift on what "voided" means.
    void_set = dp.voided_invoice_set(invoice_rows, void_statuses)
    voided = {"payable_devices": 0, "payable_amount": 0.0, "paid_devices": 0, "paid_amount": 0.0,
              "unmatched_devices": 0, "invoices": set()}

    cells, unmatched_units, unplaced = {}, [], {}
    tot = {"payable_amount": 0.0, "payable_devices": 0, "paid_amount": 0.0, "paid_devices": 0,
           "unknown_devices": 0, "unknown_amount": 0.0, "unmatched_devices": 0,
           "invoiced_devices": 0, "before_coverage_devices": 0}
    seen_payable, repeat = set(), {"rows": 0, "amount": 0.0}

    if state == "measured":
        for d in (device_rows or []):
            inv_date = as_date(d.get("created_on"))
            if not inv_date or inv_date > as_at:
                continue
            if win_from and inv_date < win_from:
                tot["before_coverage_devices"] += 1     # billed, but outside what the ledger can
                continue                                # evidence — declared, never counted as paid
            inv_no = _t(d.get("invoice_number"))
            if inv_no and inv_no in void_set:
                # Excluded from every figure — and KEPT, with its money, so the exclusion is
                # auditable rather than a number that quietly went missing between two releases.
                voided["invoices"].add(inv_no)
                row_v = index.get(norm_key(d.get("serial")))
                if row_v is None:
                    voided["unmatched_devices"] += 1
                else:
                    amt_v = safe_float(row_v.get("owed_to_vip"))
                    if payment_state(row_v, as_at) == "paid":
                        voided["paid_devices"] += 1
                        voided["paid_amount"] += amt_v
                    else:
                        voided["payable_devices"] += 1
                        voided["payable_amount"] += amt_v
                continue
            tot["invoiced_devices"] += 1
            raw = _t(d.get("location"))
            store, company, cid, market, how = place(raw)
            key = (company, store)
            c = cells.setdefault(key, _bucket(store, company, cid, market, how))
            if store == NOT_A_RETAIL_LOCATION and raw:
                u = unplaced.setdefault(raw, {"location": raw, "company": company,
                                              "payable_amount": 0.0, "payable_devices": 0,
                                              "devices": 0})
                u["devices"] += 1
            # ── THE JOIN. `serial` IS the IMEI here; the column named `imei` is the SIM/ICCID. ──
            unit_key = norm_key(d.get("serial"))
            row = index.get(unit_key)
            if row is None:
                tot["unmatched_devices"] += 1
                c["unmatched_devices"] += 1
                if len(unmatched_units) < 500:
                    unmatched_units.append({"serial": _t(d.get("serial")), "location": raw,
                                            "invoiced": inv_date,
                                            "product": _t(d.get("product_name"))})
                continue
            st = payment_state(row, as_at)
            amt = safe_float(row.get("owed_to_vip"))
            if st == "paid":
                tot["paid_amount"] += amt
                tot["paid_devices"] += 1
                c["paid_amount"] += amt
                c["paid_devices"] += 1
                continue
            # unpaid OR unknown — both are payable at this date; unknown is also counted apart
            if st == "unknown":
                tot["unknown_devices"] += 1
                tot["unknown_amount"] += amt
                c["unknown_devices"] += 1
            if unit_key in seen_payable:      # the same unit billed on a second invoice in-window:
                repeat["rows"] += 1           # the ledger owes for it ONCE, so the extra row is
                repeat["amount"] += amt       # declared rather than silently doubling the figure
            seen_payable.add(unit_key)
            tot["payable_amount"] += amt
            tot["payable_devices"] += 1
            c["payable_amount"] += amt
            c["payable_devices"] += 1
            if store == NOT_A_RETAIL_LOCATION and raw:
                unplaced[raw]["payable_amount"] += amt
                unplaced[raw]["payable_devices"] += 1

    # ── NON-DEVICE ITEMS — a DIFFERENT BASIS, reported apart and never merged ────────────────────
    # Chargebacks, loans/exchanges, managed services, SIM packs and activation fees are not
    # serialised, so the per-unit ledger cannot see them at all. What IS knowable is what was billed
    # on or before the as-at date. Whether each was settled by then is honestly not measured: no
    # per-line payment date exists anywhere in this feed, and the invoice STATUS column is a current
    # snapshot that cannot be backdated. Classification is device_purchases' own, reused verbatim.
    exact_names, ci_names = dp.device_product_names(device_rows)
    nd_items, nd_cells = {}, {}
    nd_tot = {"amount": 0.0, "lines": 0}
    invoices_with_units = {_t(d.get("invoice_number")) for d in (device_rows or [])
                           if _t(d.get("invoice_number"))}
    orphan = {"amount": 0.0, "lines": 0}
    for l in (line_rows or []):
        d0 = as_date(l.get("created_on"))
        if not d0 or d0 > as_at:
            continue
        if win_from and d0 < win_from:
            continue
        if _t(l.get("invoice_number")) in void_set:
            continue                       # the same rule, applied to the non-device basis too
        amt = safe_float(l.get("total"))
        nm = _t(l.get("name"))
        if dp.classify_line(nm, exact_names, ci_names) != "non_device":
            # a DEVICE-named line on an invoice that carried no serialised unit: real money that the
            # per-unit method has no serial for. Counted nowhere above, so it is declared here.
            if _t(l.get("invoice_number")) not in invoices_with_units:
                orphan["amount"] += amt
                orphan["lines"] += 1
            continue
        nd_tot["amount"] += amt
        nd_tot["lines"] += 1
        it = nd_items.setdefault(nm or "(unnamed line)",
                                 {"name": nm or "(unnamed line)", "amount": 0.0, "lines": 0,
                                  "latest": ""})
        it["amount"] += amt
        it["lines"] += 1
        it["latest"] = max(it["latest"], d0)
        store, company, cid, market, _how = place(l.get("location"))
        b = nd_cells.setdefault((company, store), {"company": company, "company_id": cid,
                                                  "store": store, "market": market,
                                                  "amount": 0.0, "lines": 0})
        b["amount"] += amt
        b["lines"] += 1

    r2 = lambda v: round(v + 0.0, 2)                                            # noqa: E731
    rows = sorted(cells.values(), key=lambda x: (-x["payable_amount"], x["company"], x["store"]))
    by_company = {}
    for c in rows:
        b = by_company.setdefault(c["company"], {
            "company": c["company"], "company_id": c["company_id"], "stores": 0,
            "payable_amount": 0.0, "payable_devices": 0, "paid_amount": 0.0, "paid_devices": 0,
            "unknown_devices": 0, "unmatched_devices": 0})
        for k in ("payable_amount", "payable_devices", "paid_amount", "paid_devices",
                  "unknown_devices", "unmatched_devices"):
            b[k] += c[k]
        b["stores"] += 1
    for d in (list(rows) + list(by_company.values()) + list(nd_items.values())
              + list(nd_cells.values()) + list(unplaced.values())):
        for k in ("payable_amount", "paid_amount", "amount"):
            if k in d:
                d[k] = r2(d[k])

    measured = state == "measured"
    matched = tot["invoiced_devices"] - tot["unmatched_devices"]
    out = {
        "as_at": as_at,
        "basis": "device_payable_as_at",
        "coverage": {
            "state": state,
            "reason": reason,
            "start_month": cov["start_month"],
            "end_month": cov["end_month"],
            "stale_tail": stale,
            "window": ({"from": win_from, "to": as_at} if (measured and win_from) else None),
            "ratio_threshold": cov["ratio_threshold"],
            "min_devices_judged": cov["min_devices_judged"],
            "months": cov["months"],
        },
        # NOT-MEASURED IS `None`, NEVER 0.00. A reader must never be shown a figure this report
        # cannot evidence, and a rendered zero is a figure.
        "totals": {
            "payable_amount": r2(tot["payable_amount"]) if measured else None,
            "payable_devices": tot["payable_devices"] if measured else None,
            "paid_amount": r2(tot["paid_amount"]) if measured else None,
            "paid_devices": tot["paid_devices"] if measured else None,
            "invoiced_devices": tot["invoiced_devices"] if measured else None,
            "matched_devices": matched if measured else None,
            "unmatched_devices": tot["unmatched_devices"] if measured else None,
            "match_rate": (round(matched / tot["invoiced_devices"], 4)
                           if measured and tot["invoiced_devices"] else None),
            "payment_date_unknown_devices": tot["unknown_devices"] if measured else None,
            "payment_date_unknown_amount": r2(tot["unknown_amount"]) if measured else None,
            "before_coverage_devices": tot["before_coverage_devices"] if measured else None,
            "repeat_serial_rows": repeat["rows"] if measured else None,
            "repeat_serial_amount": r2(repeat["amount"]) if measured else None,
            "distinct_device_payable_amount": (r2(tot["payable_amount"] - repeat["amount"])
                                               if measured else None),
            "distinct_device_payable_devices": (tot["payable_devices"] - repeat["rows"]
                                                if measured else None),
            # EXCLUDED, NOT MISSING. What voided invoices took out of every figure above, so the
            # number this report used to print stays explainable. The sibling Device Purchases
            # report excludes them under the SAME shared rule, changed in the same release.
            "excluded_voided_invoices": len(voided["invoices"]) if measured else None,
            "excluded_voided_payable_devices": voided["payable_devices"] if measured else None,
            "excluded_voided_payable_amount": r2(voided["payable_amount"]) if measured else None,
            "excluded_voided_paid_amount": r2(voided["paid_amount"]) if measured else None,
            "excluded_voided_unmatched_devices": voided["unmatched_devices"] if measured else None,
            "payable_including_voided": (r2(tot["payable_amount"] + voided["payable_amount"])
                                         if measured else None),
        },
        "by_company": sorted(by_company.values(),
                             key=lambda x: (-x["payable_amount"], x["company"])) if measured else [],
        "by_store": rows if measured else [],
        "unmatched_sample": unmatched_units,
        "not_a_retail_location": sorted(unplaced.values(),
                                        key=lambda x: (-x["payable_amount"], x["location"])),
        # A SEPARATE SECTION WITH A SEPARATE BASIS, and the payload says which.
        "non_device": {
            "basis": "billed",
            "settlement_state": "not_measured",
            "settlement_reason": ("these items carry no serial and no per-line payment date, so "
                                  "whether each was settled by the as-at date cannot be evidenced "
                                  "from this feed; the invoice status column is a current snapshot "
                                  "and cannot be backdated"),
            "amount": r2(nd_tot["amount"]) if measured else None,
            "lines": nd_tot["lines"] if measured else None,
            "by_item": sorted(nd_items.values(), key=lambda x: (-abs(x["amount"]), x["name"])),
            "by_store": sorted(nd_cells.values(), key=lambda x: (-abs(x["amount"]), x["company"])),
            "device_lines_without_serial": {"amount": r2(orphan["amount"]), "lines": orphan["lines"]},
        },
        "meta": {
            "ledger": ledger_stats,
            "payment_date_evidence": payment_evidence(ledger_rows, settled_rows),
            "device_rows_seen": len(device_rows or []),
            "payment_states": list(PAYMENT_STATES),
        },
    }
    return out


# ── I/O (the only impure function in the module) ──────────────────────────────────────────────────
def compute(client, org_id, as_at):
    """The report. Org-scoped on every read; the resolvers are the platform's shared ones."""
    from app.modules.account import coa
    # The device and line feeds are read WHOLE, not windowed: coverage is derived by comparing every
    # month of the ledger against every month of the invoice feed, so a windowed read would make the
    # coverage boundary depend on the question being asked. `dp._page` is device_purchases' own
    # deterministic org-scoped pager, reused rather than re-written.
    devices = dp._page(client, "vip_invoice_devices",
                       "id,invoice_number,location,serial,imei,product_name,created_on,"
                       "period_year,period_month", org_id)
    lines = dp._page(client, "vip_invoice_lines",
                     "id,invoice_number,location,status,name,quantity,total,created_on,"
                     "period_year,period_month", org_id)
    ledger = dp._page(client, "asset_ledger",
                      "id,esn_imei,payg_date,owed_to_vip,acquired_date,store,status,device_model",
                      org_id)
    # invoice HEADERS — the authoritative status. Read ONLY so the report can DECLARE what sits on a
    # voided invoice; nothing is dropped on it (see the module docstring).
    invoices = dp._page(client, "vip_invoices", "id,invoice_number,status,period_year", org_id)
    # the SECOND, independent payment feed — small (hundreds of rows), read whole, org-scoped. It is
    # never used to compute a payable; it exists only so `payment_evidence` can re-prove the licence.
    settled = dp._page(client, "vip_paygo_payments", "id,amount,period_year,created_on,status",
                       org_id)

    known = dp.store_addresses(coa._fetch_all(client, "store_mapping", "store_address,store_code",
                                              {"org_id": org_id}))
    place_store = dp.store_placer(coa.store_resolver(client, org_id), known)
    _mp, _default_id, companies = coa.store_company_map(client, org_id)
    try:
        assign_rows = coa._fetch_all(client, "store_companies", "store_address,company_id",
                                     {"org_id": org_id})
    except Exception:
        assign_rows = []                          # same fail-closed posture as company_assignment
    # `default_id=None` — so "no assignment row matched" stays distinguishable from "it fell back to
    # the default company". Printing the booking fallback would state a fact we do not have.
    company_of = coa.build_company_matcher(assign_rows, None)
    names = {}
    for c in (companies or []):
        names[c.get("id")] = c.get("name") or ""
        names[str(c.get("id"))] = c.get("name") or ""

    market_of = None
    try:                                     # §13a canonical store→market; never fatal to a report
        from app.core.scope import store_market_resolver
        market_of, _markets = store_market_resolver(client, org_id)
    except Exception as e:                                       # pragma: no cover - I/O guard
        print(f"WARN device_payable market resolution unavailable: {e}")

    out = aggregate(devices, lines, ledger, as_at, place_store, company_of, names, market_of,
                    settled_rows=settled, invoice_rows=invoices)
    out["org_id"] = org_id
    # The distributor's NAME is never written in code or page copy (RULE TWO) — it resolves through
    # the mig-953 `report_term` vocabulary, tenant override > house carrier preset > the neutral
    # noun, exactly as device_purchases resolves it.
    try:
        from app.modules.commcalc.report_labels import carrier_term
        label, source = carrier_term(client, org_id, "distributor")
    except Exception as e:                                       # pragma: no cover - I/O guard
        print(f"WARN device_payable distributor term unavailable: {e}")
        label, source = "distributor", "neutral_default"
    out["distributor_label"] = label
    out["distributor_label_source"] = source
    out["source"] = {"devices_table": "commcalc.vip_invoice_devices",
                     "ledger_table": "commcalc.asset_ledger",
                     "lines_table": "commcalc.vip_invoice_lines",
                     "settled_batches_table": "commcalc.vip_paygo_payments",
                     "invoice_headers_table": "commcalc.vip_invoices",
                     "invoice_headers_read": len(invoices),
                     "devices_read": len(devices), "ledger_read": len(ledger),
                     "lines_read": len(lines), "settled_batches_read": len(settled)}
    return out
