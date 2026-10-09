"""ePAY-PAID vs DISTRIBUTOR-CLAIMED DEVICE REIMBURSEMENT — one reconciliation, per store per month.

OWNER REPORT 2026-10-08, verbatim:
    "i just checked the commission details for boost, the commission is over stated as the device
     reimbursement is being added in the commision and also in device reimbursement
     example is 103 fulton street
       Commission (promo) $7,583.96
       Device-financing reimbursements (Distributor) $7,999.93
     the report which pays us is same as what is reported in commision 7583.96"
and, asked what the difference between the two means:
    "distributors payments are not in additon to the reimbursement they are the same payments but
     the discrepancy nbetween them shows that the distributor claims it was paid bunt epay never
     paid it"

SO THE TWO SIDES ARE **ONE** FLOW OF MONEY, AND THEY ARE NOT SYMMETRIC:

  · the CARRIER STATEMENT (what ePay actually paid us) is the MONEY RECEIVED;
  · the DISTRIBUTOR LEDGER's reimbursement column is a CLAIM about that same money — never a
    second payment.

Therefore **claimed above paid** is the exception the owner wants to see: the distributor says it
was reimbursed and the carrier never paid it. **Paid above claimed** is a DIFFERENT fact (money
received that the distributor's ledger never claimed) and is reported as its own bucket with its own
total — never folded into one signed number, because a signed total lets one store's overpayment
cancel another's shortfall and shows neither.

WHAT THIS MODULE IS
───────────────────
PURE. stdlib only, no DB, no clock, no framework import, no carrier / tenant / store / product /
quarter / compensation-type name anywhere (RULE TWO) — every one of those strings is DATA the caller
hands in from the org's own config rows. The lock `backend/harness_device_reimb_recon.py` proves it.

WHAT IT DELIBERATELY DOES NOT DO
────────────────────────────────
  · **It does not classify a carrier dollar.** "Is this carrier dollar commission or device-financing
    reimbursement" has ONE home — `commcalc.carrier_category_map`, read through
    `commcalc/carrier_map.py` (`load_rules` / `classify`) and already dereferenced by the P&L
    (`account/coa.py`). This module takes a `classify` callable and a CONFIGURED LIST of the
    classification keys that are the device-financing side. A second classifier here would be the
    duplicate defect CLAUDE.md forbids.
  · **It books, unbooks and pays nothing.** It computes a reconciliation and the flag rows that
    describe it. No amount, rate, tier, plan, schedule or paid/earned column is reachable from here.
  · **It does not test feed coverage itself.** "Does this period's statement cover every day it is
    owed, and what is the shortfall worth" has ONE home — `pay_data_quality.statement_month_coverage`
    (§19.54, which knows a CLOSED month is owed its whole calendar and prices the missing days from
    the per-line feed), over `day_coverage_gap`'s two-feed answer (§19.48). The caller hands that
    verdict in and `coverage_verdict` below is the one place this module reads it.

AN ABSENCE IS NEVER A ZERO (the §19.48 / §19.49 / §19.50 / §19.51 house shape, fourth precedent
dereferenced rather than a fifth vocabulary invented)
─────────────────────────────────────────────────────────────────────────────────────────────────
A store-month is `measured` only when BOTH sides arrived and the carrier feed's coverage for that
month is complete. Otherwise it is `not_measured` WITH ITS REASON, carrying both dollar figures as
evidence. A clean $0.00 difference is never reported for a side that did not arrive.

**THE COVERAGE RULE IS DIRECTION-ASYMMETRIC, AND THAT IS THE WHOLE POINT.** A month whose statement
arrived short a day makes the carrier side a FLOOR, not an unknown. A floor can only ever prove the
direction it points:

  · `paid_above_claim` on a short month is PROVEN (`measured_floor`) — more arrived than was claimed,
    and the missing days can only add to it.
  · `claimed_not_paid` on a short month is NOT PROVEN — the shortfall may BE the missing day. It is
    reported `not_measured` with reason `carrier_coverage_incomplete`, the missing days named and the
    store's own per-day run-rate over the days that DID arrive given as context.

That asymmetry is the false-accusation guard. "The distributor claims it was paid and ePay never paid
it" is a theft-shaped accusation about a real person's store; emitting it because a statement pull
was end-date-exclusive would be a defect far worse than a missing flag. Measured live 2026-10-08, the
house org's statement feed is missing the FINAL DAY OF EVERY CLOSED MONTH (§19.48) and four of
October's six days, so TODAY that guard withholds every claimed-above-paid finding and says why.

GRAIN, STATED RATHER THAN IMPLIED
─────────────────────────────────
The distributor side is device-keyed (one ledger row per unit: its identifier, model, acquired date,
reimbursement date), so a finding carries the devices that make up the claim as its evidence. The
carrier side is a STATEMENT TOTAL per store-month with no device grain at all — there is nothing to
match device-for-device against, and this module never pretends otherwise. `carrier_has_device_grain`
is False on every row for exactly that reason, so a reader is never invited to click through to a
device on the paid side.

Registered in docs/SYSTEM_DATA_FLOW_INDEX.md §19.52 + §16-18.

💰 MOVES NO MONEY.
"""
from __future__ import annotations

# ── Verdicts. ONE vocabulary, dereferenced from the four shipped precedents (§19.48-§19.51). ─────
#: Both sides arrived and the carrier month's coverage is complete — the difference is a fact.
VERDICT_MEASURED = "measured"
#: The carrier side is a FLOOR (its month arrived short) and the difference points the way a floor
#: can prove: more was paid than was claimed.
VERDICT_MEASURED_FLOOR = "measured_floor"
#: Nothing was measured. `reason` says what is missing. NEVER a $0.00 difference.
VERDICT_NOT_MEASURED = "not_measured"
VERDICTS = (VERDICT_MEASURED, VERDICT_MEASURED_FLOOR, VERDICT_NOT_MEASURED)

# ── Reasons a store-month could not be measured. Each names the ABSENCE, never guesses a cause. ──
#: The org has declared no classification key as its device-financing carrier side at all.
REASON_NO_RULE = "no_device_financing_rule_configured"
#: The carrier statement has no rows for this store-month (e.g. a month that predates the feed).
REASON_CARRIER_ABSENT = "carrier_statement_absent"
#: The statement HAS rows for this store-month, but not one dollar of them is classified into a
#: configured device-financing key — so the paid side of this comparison is unknown, not zero.
REASON_CARRIER_UNCLASSIFIED = "carrier_side_not_classified"
#: The distributor ledger claims nothing for this store-month.
REASON_DISTRIBUTOR_ABSENT = "distributor_claim_absent"
#: The statement for this month arrived missing days, so the paid side is a floor and a
#: claimed-above-paid shortfall cannot be told from the days that never arrived.
REASON_COVERAGE_INCOMPLETE = "carrier_coverage_incomplete"
REASONS = (REASON_NO_RULE, REASON_CARRIER_ABSENT, REASON_CARRIER_UNCLASSIFIED,
           REASON_DISTRIBUTOR_ABSENT, REASON_COVERAGE_INCOMPLETE)

#: WHICH absences earn a flag, and the two that deliberately do not.
#:
#: A flag asks a named person to go and do something, so an absence only earns one when there is a
#: CLAIM it is withholding judgement on:
#:   · `no_device_financing_rule_configured` is an ORG-level fact, not a store's. Flagging it per
#:     store would write one identical finding per store every period and say nothing new on any of
#:     them; the report carries `configured: false` instead, which is where a configuration fact
#:     belongs.
#:   · `distributor_claim_absent` means nobody claimed anything. There is no finding in it.
FLAGGED_REASONS = (REASON_CARRIER_ABSENT, REASON_CARRIER_UNCLASSIFIED, REASON_COVERAGE_INCOMPLETE)

#: What a human reads. No table, column or hosting name reaches a screen (index §19.38).
REASON_LABELS = {
    REASON_NO_RULE: ("No device-financing reimbursement rule is configured, so there is nothing to "
                     "compare the distributor's claim against."),
    REASON_CARRIER_ABSENT: ("No carrier statement was received for this store in this month, so "
                            "what the carrier paid is unknown - not zero."),
    REASON_CARRIER_UNCLASSIFIED: ("The carrier statement for this store-month carries money, but "
                                  "none of it is classified as device-financing reimbursement, so "
                                  "the paid side is unknown - not zero."),
    REASON_DISTRIBUTOR_ABSENT: "The distributor's ledger claims no reimbursement for this store-month.",
    REASON_COVERAGE_INCOMPLETE: ("The carrier statement for this month arrived missing days, so what "
                                 "was paid is a floor: a shortfall against the distributor's claim "
                                 "cannot be told apart from the days that never arrived."),
}

# ── Directions. Asymmetric on purpose (owner 2026-10-08) — never one signed total. ───────────────
#: The distributor claims more than the carrier paid: it says it was reimbursed and ePay never paid.
DIRECTION_CLAIMED_NOT_PAID = "claimed_not_paid"
#: More arrived than the distributor's ledger claims. A different fact, its own bucket, its own total.
DIRECTION_PAID_ABOVE_CLAIM = "paid_above_claim"
#: The two sides agree within tolerance.
DIRECTION_AGREED = "agreed"
DIRECTIONS = (DIRECTION_CLAIMED_NOT_PAID, DIRECTION_PAID_ABOVE_CLAIM, DIRECTION_AGREED)

# ── Flag types. Registered in commcalc/flag_registry.py — nothing here invents a vocabulary. ─────
FLAG_CLAIMED_NOT_PAID = "DEVICE_REIMB_CLAIMED_NOT_PAID"
FLAG_NOT_MEASURED = "DEVICE_REIMB_NOT_MEASURED"
FLAG_SOURCE = "device_reimb_recon"

# ── CONFIG (RULE TWO). Every carrier / product / category string a human would tune lives here as
#    DATA, per org, with these house defaults. Stored at
#    `commcalc.commission_org_config.device_reimb_recon_config` (migration 1063); the loader degrades
#    to this dict when the column, row or table is absent, so every surface works unmigrated.
CODE_DEFAULT = {
    "enabled": True,
    # cent-level tolerance: below this the two sides AGREE.
    "tolerance": 0.01,
    # WHICH CARRIER DOLLARS ARE THE DEVICE-FINANCING SIDE. Each entry is a classification key as the
    # ONE classification home (`commcalc.carrier_category_map` via `carrier_map.classify`) produces
    # it: {"component": "...", "subtype": "..." | null}. A null/absent subtype matches ANY subtype of
    # that component. EMPTY (the default) means the org has declared nothing, and every store-month
    # reports `no_device_financing_rule_configured` rather than a $0.00 paid side — the absence is
    # the finding. This is deliberately NOT pre-filled with a component guess: the house's own
    # statement carries subsidy and trade-in reimbursement money that is NOT device financing, so a
    # guess would compare the wrong dollars and call it a reconciliation.
    "carrier_sources": [],
    # WHICH DISTRIBUTOR LEDGER ROWS ARE A REIMBURSEMENT CLAIM. Empty list = any row carrying a
    # non-zero reimbursement amount, which is the honest superset: a tenant narrows it by naming its
    # own category / status spellings. Matching is case-insensitive and whitespace-tolerant.
    "distributor_categories": [],
    "distributor_statuses": [],
    # Column names are SCHEMA, and a tenant whose feed lands them elsewhere re-points them here
    # rather than in a branch (the `payable_source_map` precedent, mig 095).
    "columns": {
        "carrier_store": "business_address",
        "carrier_amount": "payment_amount",
        "carrier_category": "compensation_type",
        "carrier_period": "period",
        "carrier_day": "begin_date",
        "distributor_store": "store",
        "distributor_amount": "reimbursement",
        "distributor_date": "reimbursement_date",
        "distributor_category": "category",
        "distributor_status": "status",
        "distributor_device_id": "esn_imei",
        "distributor_device_model": "device_model",
        "distributor_acquired": "acquired_date",
        # ── THE DEVICE GRAIN (owner report 2026-10-09, store 652). The statement total has no
        #    device on it; the carrier's PER-LINE payment feed does (`imei` is populated on 99.7%
        #    of the house org's lines), and the distributor ledger carries the device's COST and
        #    the price the store sold it at on the SAME row as the claim. These names are SCHEMA,
        #    re-pointable per org exactly like the eight above (mig 095 `payable_source_map`).
        "line_store": "business_address",
        "line_amount": "amount",
        "line_category": "payment_type",
        "line_device_id": "imei",
        "line_date": "payment_date",
        # WHO SOLD THE PHONE (owner ask 2026-10-09: *"add who sold the phone to the report"*). The
        # carrier's own per-line feed names the rep it paid the line against, which is the only
        # authority this report has for "who sold it" — the distributor ledger carries no person at
        # all. A line with no rep on it reports NO rep, never a guess from the store's roster.
        "line_rep": "rep_username",
        "distributor_cost": "owed_to_vip",
        "distributor_sale": "selling_price",
        "distributor_sold": "date_sold",
    },
    # A finding's severity by the dollars at stake. A manager's queue order is a tuning decision.
    "severity_high_at": 500.0,
    "severity_critical_at": 2500.0,
    # THE REIMBURSEMENT LAG (device grain only). A device-financing payment rarely lands in the
    # month the distributor claims it: the claim is made when the distributor settles, the carrier
    # pays on its own statement cycle. §27 `imei_rebate_report` already owns "how long either side
    # of the event may this money arrive" (`period_window`) and the device layer DEREFERENCES that
    # enumeration rather than holding a second lag rule — the caller reads it SYMMETRICALLY, because
    # this layer anchors on the CLAIM date and a claim routinely follows the payment, where §27
    # anchors on the activation, which the money can only follow.
    #
    # WHY 1 AND NOT §27's 6, MEASURED RATHER THAN ASSUMED. House org, September 2026, the same live
    # rows read at four widths: +-0 leaves 139 devices ($40,119.62) looking unpaid; **+-1 resolves
    # every one of them** and finds 140 devices ($43,568.83) paid to a store other than the one
    # carrying the cost; +-2 and +-3 return those figures UNCHANGED to the cent. The answer
    # saturates at one month either side, so a wider window costs three to eight times the feed
    # read (53,762 lines vs 162,600) and buys nothing. A tenant whose distributor settles on a
    # longer cycle widens it with ONE config row.
    "lag_months": 1,
    # How many devices travel with one finding as its evidence. The COUNT and the TOTAL are always
    # complete; this caps only the listed sample so a flag description stays readable.
    "max_devices_in_evidence": 20,
}

_MONTHS = ("january", "february", "march", "april", "may", "june",
           "july", "august", "september", "october", "november", "december")
_MONTH_NO = {m: i + 1 for i, m in enumerate(_MONTHS)}
_MONTH_LABEL = {i + 1: m.title() for i, m in enumerate(_MONTHS)}


# ── pure helpers ─────────────────────────────────────────────────────────────────────────────────
def _f(v) -> float:
    try:
        s = str(v if v is not None else "").strip().replace(",", "").replace("$", "")
        if s.startswith("(") and s.endswith(")"):
            s = "-" + s[1:-1]
        return float(s) if s else 0.0
    except Exception:
        return 0.0


def _s(v) -> str:
    return str(v if v is not None else "").strip()


def _fold(v) -> str:
    return _s(v).lower()


def month_key(value) -> str | None:
    """PURE. Any stored month spelling → 'YYYY-MM', or None when it carries no month.

    Accepts the period label the statement stores ('September 2026'), the key form ('2026-09'), and
    the two date spellings the ledger feeds use ('2026-09-14...', '09/14/2026', '9/14/26'). None is a
    real answer and the caller keeps it: a row with no month cannot be placed in a store-MONTH and is
    counted, never bucketed into a guess.
    """
    s = _s(value)
    if not s:
        return None
    low = s.lower()
    for name, no in _MONTH_NO.items():                      # 'September 2026'
        if low.startswith(name):
            tail = "".join(ch for ch in low[len(name):] if ch.isdigit())
            if len(tail) >= 4:
                return f"{int(tail[:4]):04d}-{no:02d}"
    if len(s) >= 7 and s[:4].isdigit() and s[4] == "-":     # '2026-09' / '2026-09-14...'
        try:
            m = int(s[5:7])
            return f"{int(s[:4]):04d}-{m:02d}" if 1 <= m <= 12 else None
        except Exception:
            return None
    if "/" in s:                                            # '09/14/2026' / '9/14/26'
        parts = (s[:10].split("/") + ["", "", ""])[:3]
        mm, yy = parts[0], "".join(ch for ch in parts[2] if ch.isdigit())
        if mm.isdigit() and yy:
            y = int(yy)
            y += 2000 if y < 100 else 0
            m = int(mm)
            return f"{y:04d}-{m:02d}" if 1 <= m <= 12 else None
    return None


def month_label(key) -> str:
    """PURE. 'YYYY-MM' → 'September 2026' for a human-facing sentence. Unparseable passes through."""
    s = _s(key)
    try:
        return f"{_MONTH_LABEL[int(s[5:7])]} {int(s[:4])}"
    except Exception:
        return s


def day_key(value) -> str | None:
    """PURE. A date spelling → 'YYYY-MM-DD', or None. Used only to count the days a feed covers."""
    s = _s(value)
    if len(s) >= 10 and s[:4].isdigit() and s[4] == "-" and s[7] == "-":
        return s[:10]
    if "/" in s:
        a, b, c = (s[:10].split("/") + ["", "", ""])[:3]
        cc = "".join(ch for ch in c if ch.isdigit())
        if a.isdigit() and b.isdigit() and cc:
            y = int(cc)
            y += 2000 if y < 100 else 0
            return f"{y:04d}-{int(a):02d}-{int(b):02d}"
    return None


def coverage_verdict(entry):
    """PURE. (complete, missing_days, missing_amount) from the COVERAGE HOME's own answer.

    The home is `commcalc/pay_data_quality.statement_month_coverage` (§19.54) — "does this period's
    statement cover every day it is owed, and what is the shortfall worth". It knows a CLOSED month is
    owed its whole calendar, which a two-feed day gap cannot know, and it PRICES the missing days from
    the per-line feed. This module reads that answer and never re-derives it.

    `ok` is authoritative when present: True means complete, False means short, and **None means
    nothing was measured, which is NOT complete** (§19.49 — an absence is never a pass). An entry from
    the older two-feed gap (`complete` / `missing_from_statement`) is still read, so a caller that has
    only that answer is not forced to fabricate the richer one.

    `missing_amount` is None when the shortfall could not be priced — reported, never valued at $0.00.
    """
    e = entry if isinstance(entry, dict) else {}
    if "ok" in e:
        complete = e.get("ok") is True
    else:
        complete = bool(e.get("complete"))
    missing = list(e.get("missing_days") or e.get("missing_from_statement") or ())
    if missing:
        complete = False                      # a named missing day outranks any flag claiming complete
    amt = e.get("missing_amount")
    try:
        amt = float(amt) if amt is not None else None
    except Exception:
        amt = None
    return complete, missing, amt


def normalize_config(raw, base=None) -> dict:
    """PURE. Coerce a stored config blob into a complete, in-range dict. Never raises.

    Every clamp is one-directional-safe: a typo can only make this report LESS — it can never widen
    the carrier side to dollars the org did not declare, and it can never turn an unconfigured org
    into one that flags stores.
    """
    cfg = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
           for k, v in (base or CODE_DEFAULT).items()}
    if not isinstance(raw, dict):
        return cfg
    if "enabled" in raw:
        cfg["enabled"] = raw.get("enabled") is not False
    for num in ("tolerance", "severity_high_at", "severity_critical_at"):
        if num in raw:
            v = _f(raw.get(num))
            if v > 0:
                cfg[num] = v
    if "lag_months" in raw:
        try:
            cfg["lag_months"] = max(0, min(24, int(raw.get("lag_months"))))
        except Exception:
            pass
    if "max_devices_in_evidence" in raw:
        try:
            cfg["max_devices_in_evidence"] = max(0, min(500, int(raw.get("max_devices_in_evidence"))))
        except Exception:
            pass
    src = raw.get("carrier_sources")
    if isinstance(src, (list, tuple)):
        keys = []
        for e in src:
            if isinstance(e, dict):
                comp, sub = _s(e.get("component")).upper(), _s(e.get("subtype"))
            else:
                comp, sub = _s(e).upper(), ""
            if comp:
                keys.append({"component": comp, "subtype": sub or None})
        cfg["carrier_sources"] = keys
    for lst in ("distributor_categories", "distributor_statuses"):
        v = raw.get(lst)
        if isinstance(v, (list, tuple)):
            cfg[lst] = sorted({_fold(x) for x in v if _s(x)})
    cols = raw.get("columns")
    if isinstance(cols, dict):
        for k, v in cols.items():
            if k in cfg["columns"] and _s(v):
                cfg["columns"][k] = _s(v)
    return cfg


def carrier_source_matcher(cfg):
    """PURE. fn(classification) -> bool: is this classified carrier dollar the device-financing side?

    `classification` is whatever the ONE classification home returns — a dict carrying `component`
    and `subtype`. A configured entry with no subtype matches any subtype of its component. With
    nothing configured NOTHING matches, which is what makes `no_device_financing_rule_configured` an
    honest verdict instead of a $0.00 paid side.
    """
    want = cfg.get("carrier_sources") or []
    pairs = {(_s(e.get("component")).upper(), _fold(e.get("subtype")) or None) for e in want}
    comps = {c for c, s in pairs if s is None}

    def matches(classification) -> bool:
        if not pairs:
            return False
        cls = classification if isinstance(classification, dict) else {}
        comp = _s(cls.get("component")).upper()
        if not comp:
            return False
        return comp in comps or (comp, _fold(cls.get("subtype")) or None) in pairs

    return matches


def distributor_claim_matcher(cfg):
    """PURE. fn(row) -> bool: is this distributor ledger row a reimbursement CLAIM?

    An empty category/status config matches any row carrying a non-zero reimbursement amount — the
    honest superset. A tenant narrows it by naming its own spellings in config, never in a branch.
    """
    cols = cfg["columns"]
    cats = set(cfg.get("distributor_categories") or [])
    stats = set(cfg.get("distributor_statuses") or [])

    def claims(row) -> bool:
        r = row or {}
        if not _f(r.get(cols["distributor_amount"])):
            return False
        if cats and _fold(r.get(cols["distributor_category"])) not in cats:
            return False
        if stats and _fold(r.get(cols["distributor_status"])) not in stats:
            return False
        return True

    return claims


# ── THE TWO SIDES ────────────────────────────────────────────────────────────────────────────────
def carrier_side(rows, cfg, classify, resolve_store=None):
    """PURE. The carrier statement, per (store, month): what was PAID, and what was NOT claimed.

    `classify(raw_category) -> {"component":…, "subtype":…}` is the ONE classification home, injected
    (`commcalc/carrier_map.classify` bound to the org's own rules). `resolve_store(raw) -> canonical`
    is the platform's ONE store canonicalization (`account/coa.store_resolver`), injected for the same
    reason — this module never invents either.

    `other` is the money at that store-month that the configured device-financing keys do NOT claim,
    broken down by classification key. That breakdown is the EVIDENCE a reader needs when the verdict
    is `carrier_side_not_classified`: it shows exactly which carrier dollars are sitting outside the
    comparison and under which classification, so the config can be corrected from the report.
    """
    cols = cfg["columns"]
    is_source = carrier_source_matcher(cfg)
    rs = resolve_store or (lambda v: _s(v) or None)
    out, unplaced = {}, {"rows": 0, "amount": 0.0}
    for r in rows or ():
        mk = month_key((r or {}).get(cols["carrier_period"])) or \
            month_key((r or {}).get(cols["carrier_day"]))
        st = rs((r or {}).get(cols["carrier_store"]))
        amt = _f((r or {}).get(cols["carrier_amount"]))
        if not mk or not st:
            unplaced["rows"] += 1
            unplaced["amount"] = round(unplaced["amount"] + amt, 2)
            continue
        cell = out.setdefault((st, mk), {"paid": 0.0, "rows": 0, "other": {}, "other_total": 0.0,
                                         "days": set(), "paid_days": set()})
        cell["rows"] += 1
        dk = day_key((r or {}).get(cols["carrier_day"]))
        if dk:
            cell["days"].add(dk)
        cat = (r or {}).get(cols["carrier_category"])
        cls = classify(cat) if classify else {}
        if is_source(cls):
            cell["paid"] = round(cell["paid"] + amt, 2)
            if dk:
                cell["paid_days"].add(dk)
        else:
            key = f"{_s((cls or {}).get('component')) or 'unclassified'}" \
                  f"/{_s((cls or {}).get('subtype')) or '-'}"
            cell["other"][key] = round(cell["other"].get(key, 0.0) + amt, 2)
            cell["other_total"] = round(cell["other_total"] + amt, 2)
    return {"cells": out, "unplaced": unplaced}


def distributor_side(rows, cfg, resolve_store=None):
    """PURE. The distributor ledger, per (store, month): what it CLAIMS was reimbursed, device by device.

    The month is the reimbursement date's month — the month the claim is made about, which is the
    month the P&L recognises it in. Devices are carried so a finding can point at what was claimed;
    the sample is capped by config while the count and total stay complete.
    """
    cols = cfg["columns"]
    claims = distributor_claim_matcher(cfg)
    cap = int(cfg.get("max_devices_in_evidence") or 0)
    rs = resolve_store or (lambda v: _s(v) or None)
    out, unplaced = {}, {"rows": 0, "amount": 0.0}
    for r in rows or ():
        if not claims(r):
            continue
        amt = _f((r or {}).get(cols["distributor_amount"]))
        mk = month_key((r or {}).get(cols["distributor_date"]))
        st = rs((r or {}).get(cols["distributor_store"]))
        if not mk or not st:
            unplaced["rows"] += 1
            unplaced["amount"] = round(unplaced["amount"] + amt, 2)
            continue
        cell = out.setdefault((st, mk), {"claimed": 0.0, "devices": 0, "sample": []})
        cell["claimed"] = round(cell["claimed"] + amt, 2)
        cell["devices"] += 1
        if len(cell["sample"]) < cap:
            cell["sample"].append({
                "device_id": _s((r or {}).get(cols["distributor_device_id"])),
                "model": _s((r or {}).get(cols["distributor_device_model"])),
                "acquired": _s((r or {}).get(cols["distributor_acquired"]))[:10],
                "reimbursement_date": _s((r or {}).get(cols["distributor_date"]))[:10],
                "amount": amt,
            })
    return {"cells": out, "unplaced": unplaced}


# ── THE RECONCILIATION ───────────────────────────────────────────────────────────────────────────
def reconcile(carrier, distributor, cfg, coverage=None):
    """PURE. One row per store-month, each carrying its verdict, its evidence and nothing invented.

    `coverage` maps 'YYYY-MM' → the verdict of the ONE coverage home
    (`pay_data_quality.day_coverage_gap`): `complete`, `missing_from_statement`. A month absent from
    the map is treated as UNKNOWN coverage, which is handled exactly like incomplete — an untested
    feed is not a tested one (§19.49: an absence is never a pass).

    Returns {"rows": [...], "totals": {...}}. Every dollar at stake lands in exactly one bucket of
    `totals`, and the buckets are never netted against each other.
    """
    tol = _f(cfg.get("tolerance")) or 0.01
    configured = bool(cfg.get("carrier_sources"))
    ccells, dcells = carrier.get("cells") or {}, distributor.get("cells") or {}
    cov = coverage or {}
    rows = []
    for (st, mk) in sorted(set(ccells) | set(dcells), key=lambda k: (k[1], k[0])):
        c = ccells.get((st, mk)) or {}
        d = dcells.get((st, mk)) or {}
        paid, claimed = _f(c.get("paid")), _f(d.get("claimed"))
        complete, missing_days, missing_amount = coverage_verdict(cov.get(mk))
        paid_days = sorted(c.get("paid_days") or ())
        row = {
            "store": st, "month": mk, "month_label": month_label(mk),
            "carrier_paid": round(paid, 2), "distributor_claimed": round(claimed, 2),
            "carrier_rows": int(c.get("rows") or 0),
            "carrier_not_claimed_total": round(_f(c.get("other_total")), 2),
            "carrier_not_claimed_by_class": dict(c.get("other") or {}),
            "carrier_days_present": len(c.get("days") or ()),
            "carrier_paid_days": len(paid_days),
            "carrier_per_day": round(paid / len(paid_days), 2) if paid_days else None,
            "carrier_coverage_complete": complete,
            "carrier_missing_days": missing_days,
            # What the missing days are WORTH in the per-line feed, measured by the coverage home —
            # None when nothing can price them, never 0.00.
            "carrier_missing_amount": missing_amount,
            "carrier_has_device_grain": False,
            "devices_claimed": int(d.get("devices") or 0),
            "device_sample": list(d.get("sample") or ()),
            "difference": None, "direction": None,
            "verdict": VERDICT_NOT_MEASURED, "reason": None, "reason_label": None,
        }
        # ── the absences, most specific first. None of them produces a dollar difference. ────────
        if not configured:
            row["reason"] = REASON_NO_RULE
        elif claimed <= tol:
            # No claim is being made, so there is nothing to reconcile. This takes precedence over
            # every carrier-side absence: reporting "the paid side is unknown" for a store-month
            # nobody claimed anything about would be a queue of findings with no finding in them.
            row["reason"] = REASON_DISTRIBUTOR_ABSENT
        elif not c.get("rows"):
            row["reason"] = REASON_CARRIER_ABSENT
        elif paid <= tol:
            row["reason"] = REASON_CARRIER_UNCLASSIFIED
        else:
            diff = round(claimed - paid, 2)
            if abs(diff) <= tol:
                row.update({"difference": 0.0, "direction": DIRECTION_AGREED,
                            "verdict": VERDICT_MEASURED if complete else VERDICT_MEASURED_FLOOR})
            elif diff < 0:
                # paid above claimed: a FLOOR above the claim proves itself, short month or not.
                row.update({"difference": round(-diff, 2), "direction": DIRECTION_PAID_ABOVE_CLAIM,
                            "verdict": VERDICT_MEASURED if complete else VERDICT_MEASURED_FLOOR})
            elif complete:
                row.update({"difference": diff, "direction": DIRECTION_CLAIMED_NOT_PAID,
                            "verdict": VERDICT_MEASURED})
            else:
                # claimed above a FLOOR proves nothing — the shortfall may be the missing days.
                row.update({"difference": diff, "direction": DIRECTION_CLAIMED_NOT_PAID,
                            "reason": REASON_COVERAGE_INCOMPLETE})
        if row["reason"]:
            row["reason_label"] = REASON_LABELS.get(row["reason"])
        rows.append(row)

    totals = {
        "store_months": len(rows),
        "measured": 0, "measured_floor": 0, "not_measured": 0,
        # the owner's exception, and ONLY the confirmed part of it
        "claimed_not_paid_store_months": 0, "claimed_not_paid_total": 0.0,
        # its own bucket, never netted into the line above
        "paid_above_claim_store_months": 0, "paid_above_claim_total": 0.0,
        "agreed_store_months": 0,
        # withheld, with the dollars so the size of what is NOT being said is visible
        "unconfirmed_claimed_not_paid_store_months": 0, "unconfirmed_claimed_not_paid_total": 0.0,
        "not_measured_by_reason": {}, "not_measured_claim_total": 0.0,
        "carrier_unplaced": dict(carrier.get("unplaced") or {}),
        "distributor_unplaced": dict(distributor.get("unplaced") or {}),
    }
    for r in rows:
        totals[r["verdict"]] += 1
        if r["verdict"] == VERDICT_NOT_MEASURED:
            totals["not_measured_by_reason"][r["reason"]] = \
                totals["not_measured_by_reason"].get(r["reason"], 0) + 1
            totals["not_measured_claim_total"] = round(
                totals["not_measured_claim_total"] + r["distributor_claimed"], 2)
            if r["reason"] == REASON_COVERAGE_INCOMPLETE:
                totals["unconfirmed_claimed_not_paid_store_months"] += 1
                totals["unconfirmed_claimed_not_paid_total"] = round(
                    totals["unconfirmed_claimed_not_paid_total"] + _f(r["difference"]), 2)
        elif r["direction"] == DIRECTION_CLAIMED_NOT_PAID:
            totals["claimed_not_paid_store_months"] += 1
            totals["claimed_not_paid_total"] = round(
                totals["claimed_not_paid_total"] + _f(r["difference"]), 2)
        elif r["direction"] == DIRECTION_PAID_ABOVE_CLAIM:
            totals["paid_above_claim_store_months"] += 1
            totals["paid_above_claim_total"] = round(
                totals["paid_above_claim_total"] + _f(r["difference"]), 2)
        else:
            totals["agreed_store_months"] += 1
    return {"rows": rows, "totals": totals, "configured": configured}


def severity_for(amount, cfg) -> str:
    """PURE. A finding's severity from the dollars at stake — thresholds are config, not constants.

    Returns the registry's own vocabulary spelling; `flag_registry.canon_sev` is still the one scale.
    """
    a = abs(_f(amount))
    if a >= _f(cfg.get("severity_critical_at")):
        return "CRITICAL"
    if a >= _f(cfg.get("severity_high_at")):
        return "HIGH"
    return "MEDIUM"


def recon_flags(result, cfg, period_label=None):
    """PURE. The flag rows this reconciliation justifies — and only those.

    TWO types, because they ask a manager for two different actions:
      · DEVICE_REIMB_CLAIMED_NOT_PAID — a CONFIRMED shortfall: the distributor claims reimbursement
        the carrier never paid. Go to the distributor.
      · DEVICE_REIMB_NOT_MEASURED — this store-month could not be reconciled, with the reason. Go to
        the configuration or the feed. A withheld claimed-above-paid shortfall lands HERE rather than
        as a shortfall finding, which is the false-accusation guard in one line of code.

    `paid_above_claim` is deliberately NOT flagged. It is money we received that the distributor's
    ledger never claimed — reported on the report with its own total, but it asks nobody to act, and
    a queue that cannot be worked through is a queue nobody works through.

    Rows carry no `org_id` and no `flag_key`: `flag_persist.sync` owns identity, and the caller stamps
    the org. `source_ref` is the store-month, which is this finding's grain — the description carries
    dollars that move between runs, so it can never be part of the identity (flag_persist §IDENTITY).
    """
    if not (cfg.get("enabled", True)):
        return []
    flags = []
    for r in result.get("rows") or ():
        mk, st = r["month"], r["store"]
        ml = r["month_label"]
        ref = f"{mk}|{st}"
        if r["verdict"] != VERDICT_NOT_MEASURED and r["direction"] == DIRECTION_CLAIMED_NOT_PAID:
            dev = (f" The claim is made up of {r['devices_claimed']} device(s)."
                   if r["devices_claimed"] else "")
            flags.append({
                "period": period_label or ml, "flag_type": FLAG_CLAIMED_NOT_PAID,
                "source": FLAG_SOURCE, "severity": severity_for(r["difference"], cfg),
                "store_address": st, "source_ref": ref, "amount": r["difference"],
                "description": (
                    f"The distributor's ledger claims ${r['distributor_claimed']:,.2f} of device "
                    f"reimbursement for {ml} that the carrier statement shows only "
                    f"${r['carrier_paid']:,.2f} of - ${r['difference']:,.2f} claimed as reimbursed "
                    f"and never received.{dev} These are the same money, not two payments."),
            })
        elif r["verdict"] == VERDICT_NOT_MEASURED and r["reason"] in FLAGGED_REASONS \
                and r["distributor_claimed"] > 0:
            extra = ""
            if r["reason"] == REASON_COVERAGE_INCOMPLETE:
                rate = (f" over the {r['carrier_paid_days']} day(s) that did arrive the statement "
                        f"averages ${r['carrier_per_day']:,.2f} a day for this store, so a "
                        f"${_f(r['difference']):,.2f} shortfall cannot be separated from the missing "
                        f"day(s) until the statement is re-pulled") if r["carrier_per_day"] else ""
                worth = (f", worth ${r['carrier_missing_amount']:,.2f} across the company in the "
                         f"per-line feed" if r["carrier_missing_amount"] is not None else
                         ", and nothing can price what they held")
                extra = (f" The statement for {ml} is missing "
                         f"{len(r['carrier_missing_days'])} day(s)"
                         f" ({', '.join(r['carrier_missing_days'][:5])}){worth};{rate}.")
            elif r["reason"] == REASON_CARRIER_UNCLASSIFIED and r["carrier_not_claimed_total"]:
                extra = (f" The statement carries ${r['carrier_not_claimed_total']:,.2f} for this "
                         f"store-month under other classifications "
                         f"({', '.join(sorted(r['carrier_not_claimed_by_class']))}).")
            flags.append({
                "period": period_label or ml, "flag_type": FLAG_NOT_MEASURED,
                "source": FLAG_SOURCE, "severity": "MEDIUM",
                "store_address": st, "source_ref": ref,
                "amount": r["distributor_claimed"],
                "description": (
                    f"Device reimbursement for {ml} could not be reconciled: "
                    f"{REASON_LABELS.get(r['reason'], r['reason'])} The distributor claims "
                    f"${r['distributor_claimed']:,.2f}; the carrier statement shows "
                    f"${r['carrier_paid']:,.2f} of device-financing reimbursement.{extra} "
                    f"This is reported as NOT MEASURED, not as a difference of $0.00."),
            })
    return flags


def config_from_rows(tenant_blob, house_blob=None) -> dict:
    """PURE. This org's config: house defaults ← the HOUSE org's stored blob ← the tenant's own.

    The two-level inheritance is the platform's standing config shape (`report_pull_map`, mig 207):
    a house row every tenant inherits, a tenant row that overrides it key by key. The caller does the
    two reads; keeping the MERGE here means one statement of precedence that a lock can pin.
    """
    return normalize_config(tenant_blob, base=normalize_config(house_blob))


# ════════════════════════════════════════════════════════════════════════════════════════════════
# THE DEVICE GRAIN — one row per DEVICE, not per store-month
# ════════════════════════════════════════════════════════════════════════════════════════════════
# OWNER REPORT 2026-10-09, verbatim: *"i was checking the p&l for 652 , the equipment rebate is
# almost 5000 less than the equipment reimbursement, we need to check what is going on and also
# create another report for the equipment reimbursement per line , cost per line, and device payment
# charged in the store to asses which line items dod not get paid"*.
#
# WHY THIS LIVES HERE AND NOT IN A NEW MODULE. "ePay paid vs the distributor claimed" already has
# ONE home — this file — with one verdict vocabulary, one set of reasons, one coverage rule and one
# config row. A sibling module for the same question at a finer grain is exactly the duplicate
# defect CLAUDE.md forbids: the two would drift the first time a reason or a tolerance changed. So
# the grain is ADDED to the existing home and everything above is reused unchanged.
#
# WHAT MAKES THE DEVICE GRAIN POSSIBLE AT ALL, stated because the store-month layer says it is not.
# `carrier_side` reads the STATEMENT (one total per store-month, no device on it) and therefore sets
# `carrier_has_device_grain` False — correctly. The carrier's PER-LINE payment feed is a different
# read of the same money and DOES carry the device identifier on every line. So the device grain is
# not a re-derivation of the statement total; it is the other, finer read, and the caller injects it.
#
# THE FINDING THE OWNER IS LOOKING AT, and the class behind it. Measured live, read-only, house org,
# store '652 Communipaw Avenue', September 2026: the distributor ledger claims $15,274.91 across 47
# devices; the carrier's per-line feed pays $4,999.98 of that, on 15 of those devices, TO A
# DIFFERENT STORE (four of them), and $0.00 of it is unpaid anywhere. The instance is 15 iPhones and
# Motorolas. The class is:
#
#     **"which store does a device-financing dollar belong to?" is answered by two authorities that
#       nothing reconciles per device — the distributor ledger books the claim to the store the
#       device was STOCKED to, the carrier statement pays the store it was ACTIVATED at.**
#
# At store-month grain the two are just two totals and the difference is unexplained. At device
# grain it is a list of transferred handsets, which is a thing a human can act on. That is the whole
# reason this layer exists, and it is why `paid_to_other_store` is its own status and never folded
# into "not paid": telling a store it was never paid for a device another store was paid for is the
# same false accusation §D of the lock exists to prevent.
#
# 💰 MOVES NO MONEY. Reads three feeds, books nothing, pays nobody, writes nothing.

#: The carrier's per-line feed pays this device, at THIS store.
LINE_PAID = "paid"
#: The carrier paid this device — to a DIFFERENT store than the one the ledger claims it for.
LINE_PAID_OTHER_STORE = "paid_to_other_store"
#: The carrier's per-line feed carries no device-financing dollar for this device, anywhere, and the
#: month's feed is COMPLETE — so the absence is a fact.
LINE_NOT_PAID = "not_paid"
#: Nothing could be said. `reason` names the absence. NEVER rendered as "not paid".
LINE_NOT_MEASURED = "not_measured"
LINE_STATUSES = (LINE_PAID, LINE_PAID_OTHER_STORE, LINE_NOT_PAID, LINE_NOT_MEASURED)

#: What a human reads for each status. No table, column or hosting name reaches a screen (§19.38).
LINE_STATUS_LABELS = {
    LINE_PAID: "Paid to this store.",
    LINE_PAID_OTHER_STORE: ("The carrier paid this device's reimbursement to a different store. "
                            "This store carries the cost; the other store received the money."),
    LINE_NOT_PAID: ("No device-financing reimbursement was paid for this device at any store, and "
                    "the month's feed is complete — so this one really was not paid."),
    LINE_NOT_MEASURED: "Not measured. The reason says what is missing.",
}


#: The device-level absence the per-line feed can have of its own: the device carries no identifier
#: on the distributor's row, so there is nothing to look it up by. Reported, never guessed.
REASON_DEVICE_UNIDENTIFIED = "device_not_identified_on_claim"
#: Two store records claim one of the two addresses, so "a DIFFERENT store was paid" cannot be told
#: from "the same store spelled twice". Measured live (house org, September 2026): 23 of the 135
#: devices the carrier paid elsewhere, worth $6,164.95, touch one of the three addresses §13d reports
#: as claimed by two codes — and calling those a transfer would move cost between two records of one
#: physical store. The ambiguous SET is injected from the one home (`account/store_identity
#: .store_identity_index` → `.ambiguous_identities`, §64) so this module states no identity rule.
REASON_STORE_AMBIGUOUS = "store_identity_ambiguous"
#: What a human reads for the two device-level absences, in the same voice as REASON_LABELS.
LINE_REASON_LABELS = {
    REASON_DEVICE_UNIDENTIFIED: ("The distributor's claim carries no device identifier, so there is "
                                 "nothing to look this device up by on the carrier's feed."),
    REASON_STORE_AMBIGUOUS: ("Two store records claim one of these two addresses, so whether the "
                             "carrier paid a DIFFERENT store or the same store under its second "
                             "spelling cannot be told. Reported rather than called a transfer."),
}


def carrier_line_side(rows, cfg, is_device_dollar, resolve_store=None):
    """PURE. The carrier's PER-LINE feed, indexed by device: who was paid for it, and how much.

    `is_device_dollar(raw_category) -> bool` is INJECTED and is the only thing that decides whether a
    line is device-financing money. It is built by the caller from the org's OWN declaration (the
    §58 classification home read through the org's `carrier_component_lines` routing — the same fact
    the P&L's reimbursement line is booked on), so this module adds no second classifier and cannot
    disagree with the statement the owner is reading. When the org has declared no routing the
    caller passes None and every row reports `no_device_financing_rule_configured`.

    `resolve_store` is the platform's ONE store canonicalization (`account/coa.store_resolver`),
    injected for the same reason.

    Returns {"devices": {device_id: {"total", "by_store": {store: amount}, "lines": n}},
             "unidentified": {"rows", "amount"}}  — a device-financing line with no device
    identifier on it is COUNTED, not dropped, so the report can say how much of the paid side it
    could not place on a device.
    """
    cols = cfg["columns"]
    rs = resolve_store or (lambda v: _s(v) or None)
    out = {}
    unidentified = {"rows": 0, "amount": 0.0}
    for r in rows or ():
        if is_device_dollar is None:
            break
        if not is_device_dollar((r or {}).get(cols["line_category"])):
            continue
        amt = _f((r or {}).get(cols["line_amount"]))
        dev = _s((r or {}).get(cols["line_device_id"]))
        if not dev:
            unidentified["rows"] += 1
            unidentified["amount"] = round(unidentified["amount"] + amt, 2)
            continue
        st = rs((r or {}).get(cols["line_store"])) or ""
        cell = out.setdefault(dev, {"total": 0.0, "by_store": {}, "by_store_month": {},
                                    "lines": 0, "reps": {}})
        cell["total"] = round(cell["total"] + amt, 2)
        cell["lines"] += 1
        cell["by_store"][st] = round(cell["by_store"].get(st, 0.0) + amt, 2)
        # WHEN it arrived, per store, so the device row can say "paid, two months later" instead of
        # folding a lagged payment into a bare "paid" and losing the one fact a chaser needs.
        bm = cell["by_store_month"].setdefault(st, {})
        _lmk = month_key((r or {}).get(cols["line_date"])) or ""
        bm[_lmk] = round(bm.get(_lmk, 0.0) + amt, 2)
        # The rep the carrier paid this line against, with the store it was paid at, so a device the
        # carrier paid ANOTHER store for names the person who actually sold it there.
        _rep = _s((r or {}).get(cols["line_rep"]))
        if _rep:
            cell["reps"].setdefault((_rep, st), 0.0)
            cell["reps"][(_rep, st)] = round(cell["reps"][(_rep, st)] + amt, 2)
    return {"devices": out, "unidentified": unidentified}


def device_lines(asset_rows, carrier_lines, cfg, resolve_store=None, coverage=None,
                 configured=True, ambiguous_stores=None):
    """PURE. One row per DEVICE the distributor ledger claims a reimbursement for.

    Each row carries, side by side and never netted:

      · what the distributor CLAIMS was reimbursed for this device,
      · what the device COST (the distributor's charge on the same ledger row),
      · what the store CHARGED for it — and `null` with `store_payment_recorded: False` when the
        ledger carries no sale price, because a missing price is not a sale for $0.00,
      · what the carrier's per-line feed paid for it at THIS store, and at any OTHER store, with
        those other stores named.

    `coverage` maps 'YYYY-MM' → the ONE coverage home's verdict (`pay_data_quality
    .statement_month_coverage`, §19.54), consulted for exactly one purpose: a month whose feed
    arrived short cannot support "this device was never paid", so such a row is NOT_MEASURED with
    the coverage reason instead. Same direction-asymmetric guard as `reconcile` — a floor only ever
    proves the direction it points, and "you were never paid for this handset" is a theft-shaped
    claim about a real person's store.

    `configured` is False when the org has declared no device-financing routing at all; then every
    row is NOT_MEASURED with `no_device_financing_rule_configured` and no dollar is called unpaid.

    `ambiguous_stores` is the set of canonical store keys that TWO `store_mapping` records claim,
    read from the ONE identity home (§64, `account/store_identity.ambiguous_identities`) and never
    derived here. A device the carrier paid at another store is NOT_MEASURED when either side is one
    of those keys: the two keys may be one physical store spelled twice, and a transfer called on
    that evidence would move real cost between two records of the same store.
    """
    cols = cfg["columns"]
    claims = distributor_claim_matcher(cfg)
    rs = resolve_store or (lambda v: _s(v) or None)
    devices = (carrier_lines or {}).get("devices") or {}
    rows, unplaced = [], {"rows": 0, "amount": 0.0}
    amb = {str(k).strip() for k in (ambiguous_stores or ()) if str(k).strip()}
    for r in asset_rows or ():
        if not claims(r):
            continue
        claimed = _f((r or {}).get(cols["distributor_amount"]))
        mk = month_key((r or {}).get(cols["distributor_date"]))
        st = rs((r or {}).get(cols["distributor_store"]))
        if not mk or not st:
            unplaced["rows"] += 1
            unplaced["amount"] = round(unplaced["amount"] + claimed, 2)
            continue
        dev = _s((r or {}).get(cols["distributor_device_id"]))
        paid = devices.get(dev) or {}
        by_store = dict(paid.get("by_store") or {})
        here = round(by_store.pop(st, 0.0), 2)
        other = [{"store": k, "amount": v} for k, v in sorted(by_store.items(), key=lambda kv: -kv[1])]
        other_total = round(float(sum(o["amount"] for o in other)), 2)
        # WHEN this store was paid. The caller reads the per-line feed over the lag WINDOW, so a
        # payment can legitimately sit in a later month than the claim; saying so is the difference
        # between "paid late" and the bare "paid" that hides the lag.
        _here_months = sorted(m for m, v in ((paid.get("by_store_month") or {}).get(st) or {}).items()
                              if v and m)
        paid_month = _here_months[0] if _here_months else None
        # WHO SOLD IT, from the carrier's own line. Biggest-paid first, each with the store the
        # carrier paid it at, so a transferred device names the rep AND where they were selling.
        # Empty when no line carried a rep — reported as unknown, never filled from a roster.
        _reps = [{"rep": k[0], "store": k[1], "amount": v}
                 for k, v in sorted((paid.get("reps") or {}).items(), key=lambda kv: -kv[1])]
        _sold_by = [x for x in _reps if x["store"] == st] or _reps
        cov_complete, cov_missing, cov_missing_amount = \
            coverage_verdict((coverage or {}).get(mk))

        if not configured:
            status, reason = LINE_NOT_MEASURED, REASON_NO_RULE
        elif not dev:
            status, reason = LINE_NOT_MEASURED, REASON_DEVICE_UNIDENTIFIED
        elif here:
            status, reason = LINE_PAID, None
        elif other_total and (st in amb or any(o["store"] in amb for o in other)):
            # One of the two addresses is claimed by TWO store records (§13d / §64), so "a different
            # store was paid" and "the same store under its second spelling" are indistinguishable.
            # The money stays on the row, named; only the VERDICT is withheld. A transfer called here
            # would move real cost between two records of one physical store.
            status, reason = LINE_NOT_MEASURED, REASON_STORE_AMBIGUOUS
        elif other_total:
            # PROVEN whatever the month's coverage is: money that DID arrive, at a named store, can
            # only be added to by the days that did not. This is the one finding a short month
            # cannot withhold, and the asymmetry is deliberate (same rule as VERDICT_MEASURED_FLOOR).
            status, reason = LINE_PAID_OTHER_STORE, None
        elif not cov_complete:
            status, reason = LINE_NOT_MEASURED, REASON_COVERAGE_INCOMPLETE
        else:
            status, reason = LINE_NOT_PAID, None

        sale_raw = (r or {}).get(cols["distributor_sale"])
        sale_present = _s(sale_raw) != ""
        rows.append({
            "store": st,
            "month": mk,
            "month_label": month_label(mk),
            "device_id": dev,
            "model": _s((r or {}).get(cols["distributor_device_model"])),
            "acquired": _s((r or {}).get(cols["distributor_acquired"]))[:10],
            "sold": _s((r or {}).get(cols["distributor_sold"]))[:10],
            "reimbursement_date": _s((r or {}).get(cols["distributor_date"]))[:10],
            "distributor_claimed": claimed,
            "device_cost": _f((r or {}).get(cols["distributor_cost"])),
            # An absence is never a zero: no recorded sale price reads as "not recorded".
            "store_device_payment": _f(sale_raw) if sale_present else None,
            "store_payment_recorded": sale_present,
            # The rep the carrier paid for this device, and every rep on it when more than one line
            # was paid. `sold_by` prefers a rep paid AT this store and otherwise names whoever the
            # carrier paid elsewhere — which is the person who actually sold a transferred phone.
            "sold_by": _sold_by[0]["rep"] if _sold_by else None,
            "sold_by_store": _sold_by[0]["store"] if _sold_by else None,
            "reps": _reps,
            "carrier_paid_here": here,
            "carrier_paid_here_month": paid_month,
            "carrier_paid_here_month_label": month_label(paid_month) if paid_month else None,
            # True only when the money arrived in a LATER month than the claim — the lag made
            # visible rather than averaged away. Never True for an unpaid device.
            "carrier_paid_after_claim_month": bool(paid_month and paid_month > mk),
            "carrier_paid_other_stores": other,
            "carrier_paid_other_total": other_total,
            "carrier_paid_lines": int(paid.get("lines") or 0),
            "status": status,
            "status_label": LINE_STATUS_LABELS[status],
            "reason": reason,
            "reason_label": (LINE_REASON_LABELS.get(reason) or REASON_LABELS.get(reason)) if reason else None,
            # The evidence behind a withheld "never paid": WHICH days of this month's feed never
            # arrived, and what they were worth. Carried only on the row the coverage rule decided,
            # so no other row invites a reader to discount it.
            "coverage_missing_days": (cov_missing if reason == REASON_COVERAGE_INCOMPLETE else []),
            "coverage_missing_amount": (cov_missing_amount
                                        if reason == REASON_COVERAGE_INCOMPLETE else None),
        })

    rows.sort(key=lambda r: (r["store"], -r["distributor_claimed"], r["device_id"]))
    return {
        "rows": rows,
        "by_store": _device_store_totals(rows),
        "totals": _device_totals(rows, carrier_lines),
        "configured": bool(configured),
        "unplaced": unplaced,
        # Stated on the payload rather than assumed by a reader: this layer's paid side IS device-
        # keyed, unlike `reconcile`'s, whose `carrier_has_device_grain` is False on every row.
        "carrier_has_device_grain": True,
    }


def _device_blank_totals() -> dict:
    return {
        "devices": 0,
        "distributor_claimed": 0.0,
        "device_cost": 0.0,
        "store_device_payment": 0.0,
        "store_payment_not_recorded": 0,
        "carrier_paid_here": 0.0,
        "carrier_paid_other_total": 0.0,
        "not_paid_total": 0.0,
        "not_measured_total": 0.0,
        "by_status": {s: 0 for s in LINE_STATUSES},
    }


def _device_accumulate(t: dict, r: dict) -> None:
    t["devices"] += 1
    t["by_status"][r["status"]] += 1
    for k in ("distributor_claimed", "device_cost", "carrier_paid_here", "carrier_paid_other_total"):
        t[k] = round(t[k] + r[k], 2)
    if r["store_payment_recorded"]:
        t["store_device_payment"] = round(t["store_device_payment"] + (r["store_device_payment"] or 0.0), 2)
    else:
        t["store_payment_not_recorded"] += 1
    # The two unpaid buckets are kept APART and never netted against what was paid — the same rule
    # `reconcile`'s totals hold for the store-month layer.
    if r["status"] == LINE_NOT_PAID:
        t["not_paid_total"] = round(t["not_paid_total"] + r["distributor_claimed"], 2)
    elif r["status"] == LINE_NOT_MEASURED:
        t["not_measured_total"] = round(t["not_measured_total"] + r["distributor_claimed"], 2)


def _device_store_totals(rows) -> list:
    """PURE. Per store, the same buckets as the report total, so a store row and the header can
    never state different numbers (one accumulation, applied twice)."""
    out = {}
    for r in rows or ():
        t = out.setdefault(r["store"], dict(_device_blank_totals(), store=r["store"]))
        _device_accumulate(t, r)
    return sorted(out.values(), key=lambda t: -t["carrier_paid_other_total"])


def _device_totals(rows, carrier_lines=None) -> dict:
    t = _device_blank_totals()
    for r in rows or ():
        _device_accumulate(t, r)
    u = (carrier_lines or {}).get("unidentified") or {}
    # Device-financing money the carrier paid that carries NO device identifier. Reported on the
    # total so the paid side is never quietly understated by what could not be placed on a device.
    t["carrier_paid_unidentified"] = round(_f(u.get("amount")), 2)
    t["carrier_paid_unidentified_rows"] = int(u.get("rows") or 0)
    return t
