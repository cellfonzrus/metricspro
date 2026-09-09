"""SALES FROM EVENTS — the three reports, and the honesty rules that hold them together.

OWNER DIRECTIVE 2026-09-09 (verbatim): *"i need a seaprate reporting menu for only rsk activations
done per store and their retention , the report will be called sales from events and in marketing
menu, for boost it will eb coming from teh rsk events tender and the others not sure yet but
provision will be made - so 2 reports - 1 total sales with all available fields n that report and the
second is the retention , the third will be roi from te event, that will include teh cost to set up
teh event and teh total commssion received for the lines activated on that day via teh rsk , if teh
event was not loaded previously it will still run a report with the roi and ask the user to input teh
cost details or link it to the event created in the system if the user inputs teh details it will
create the event in the system with the minimal information which is required to compute the cost ,
cost of event , payroll paid , the number of phones activated and their cosrt willcome from teh sales
report and the sku report, it is an unlocked phones given away the the systtem will ask while
gatehring this information how much is teh cost of teh phone, for boost check the register of rsk"*

═══════════════════════════════════════════════════════════════════════════════════════════════════
ONE CORRECTION THE OWNER SHOULD SEE — RSK IS A REGISTER, NOT A TENDER
═══════════════════════════════════════════════════════════════════════════════════════════════════
The directive says "the rsk events tender". In the live data RSK is the value of
`raw_sales.register`; `tender_type` on those very same rows reads Cash / Credit Card / Debit Card /
Externel Credit Card. Filtering on the tender would answer "how did the customer pay" and would match
nothing. The reports read the REGISTER — the same reading `commcalc/flags.py`'s RSK_ACTIVATIONS flag
has always used, now shared through `commcalc/sales_register.py` so the two can never diverge. The
owner's own last line says it: *"for boost check the register of rsk"*.

═══════════════════════════════════════════════════════════════════════════════════════════════════
WHAT IS REUSED (CLAUDE.md duplicate-check build gate)
═══════════════════════════════════════════════════════════════════════════════════════════════════
Nothing here re-derives a number another surface already owns:

  • WHAT AN ACTIVATION IS      → `commcalc/calculator.classify_contract_type` — THE one classifier
                                 behind the Sales Report, Exec MTD and Daily Targets. This module
                                 calls it; it does not know a contract-type label.
  • WHAT A VOID / RETURN IS    → `commcalc/gp_report.is_voided` + the shared `trans_type == 'Return'`
                                 skip, reached through `countable_sale_skip_reason`.
  • COUNTING (distinct txn)    → `commcalc/router._compute_feed_actuals_py`, called with the RSK rows
                                 as its `rows=` argument. That parameter exists precisely so a caller
                                 can hand the shared pass a pre-filtered row set (`_fetch_actuals`
                                 already does it with the feed∪raw_sales union). The register is a ROW
                                 FILTER applied BEFORE the shared pass, so every classification,
                                 skip rule, distinct-`trans_id` rule and canonical store-code
                                 resolution is the shared pass's, unchanged.
  • SALE LINE → SUBSCRIBER     → `commcalc/sale_installment_engine._mi_index` / `_match_mi` — THE
                                 paid-gate's line-matching key (MDN first, then device serial). The
                                 retention report is a second READER of that key, not a second key.
  • COMMISSION RECEIVED        → `commcalc/router.commission_received_breakout` (which is itself the
                                 `commission_received.build_breakout` pure pass over the ePay / MA /
                                 MI feeds), honouring `ma_store_pnl.commission_received_lines()`'s
                                 rule that a REBATE IS NOT COMMISSION (owner 2026-09-08). This module
                                 READS that figure; computing commission is the commission agent's.
  • PAYROLL HOURS              → `storeops/salary_expense.day_measurement` / `hours_for_shift` — the
                                 three-state hours contract (measured-nonzero / measured-zero /
                                 not-measured). No third payroll derivation is invented here.
  • THE EVENT ENTITY           → `core.marketing_event` (mig 986) and `POST /marketing/events`. There
                                 is no second event table, no second event creator and no second
                                 store-attribution path.

═══════════════════════════════════════════════════════════════════════════════════════════════════
THE THREE HONESTY RULES
═══════════════════════════════════════════════════════════════════════════════════════════════════
1. RETENTION HAS THREE STATES, NOT TWO. A line that matched and is active; a line that matched and is
   not; and a line that COULD NOT BE MATCHED at all. The third is NOT churn. It never enters the
   denominator, it is reported with its own count and a stated reason (no MDN on the sale row / the
   subscriber feed for that month was never loaded / the MDN is absent from a feed that WAS loaded),
   and a retention percentage is `None` rather than 0% when the denominator is empty.

2. AN UNKNOWN COST IS NEVER $0.00. Every ROI cost component carries a `basis`: `derived` (we computed
   it and can say from what), `entered` (a human typed it, and we say who and when) or
   `prompt_required` (we do not know). A single `prompt_required` component makes the ROI itself
   `None` and puts the component in `missing` — because an ROI computed from a missing cost is wrong
   in the flattering direction, which is the worst direction to be wrong in.

3. THE COMMISSION FIGURE STATES ITS OWN GRAIN. Commission received is not expressible per DAY or per
   REGISTER from any feed the platform holds (see `commission_basis` below). The report says so, in
   the payload, every time.
"""
from datetime import date as _date, datetime, timedelta, timezone

from app.modules.commcalc import sales_register as REG

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 1. CONFIG (RULE TWO) — house defaults here, per-org rows on core.marketing_config (migration 995)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# NOTHING below is a carrier or tenant branch. `event_sales_registers` holds a POS register VALUE
# supplied as data (the house default is the one `flags.py` has always used); the activation classes
# are the SHARED CLASSIFIER'S OWN output vocabulary ('premium' / 'byod' / 'upgrade'), which is why
# adding a carrier means adding a config row and never a code path. That is the owner's "the others
# not sure yet but provision will be made".
DEFAULT_EVENT_SALES_CONFIG = {
    "event_sales_registers": list(REG.HOUSE_EVENT_REGISTERS),
    # Which of the shared classifier's buckets the HEADLINE "activations" number counts. Upgrades are
    # excluded by default because an upgrade is an EXISTING line — it has nothing to retain that the
    # event created — but the upgrade count is reported beside the headline either way, never hidden.
    "event_sales_activation_classes": ["premium", "byod"],
    "event_retention_windows_days": [30, 60, 90],
    # Whether the ROI report may read a phone's cost from the SKU catalog before asking a human.
    "event_roi_phone_cost_from_catalog": True,
}

#: The classifier buckets that exist at all — the whitelist a configured list is checked against, so
#: a typo cannot invent an activation bucket that silently counts nothing.
ACTIVATION_CLASSES = ("premium", "byod", "upgrade")

CLASS_LABELS = {
    "premium": "Activation",
    "byod": "BYOD activation",
    "upgrade": "Upgrade",
}


def _as_list(v):
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return [x for x in v]
    s = str(v).strip()
    if not s:
        return []
    return [p.strip() for p in s.strip("{}").split(",") if p.strip()]


def _int_list(v, default):
    raw = _as_list(v)
    if raw is None:
        return list(default)
    out = []
    for x in raw:
        try:
            n = int(float(x))
        except (TypeError, ValueError):
            continue
        if n > 0 and n not in out:
            out.append(n)
    return sorted(out) or list(default)


def resolve_event_sales_config(row):
    """A `core.marketing_config` row (or None, or a pre-995 row) → the full effective config.

    ADAPTIVE by design: a database on which migration 995 has not been applied hands back a row with
    none of these columns, and every one of them falls back to the house default. The reports work on
    the old schema; they simply cannot be re-pointed until the migration lands.
    """
    cfg = {k: (list(v) if isinstance(v, list) else v)
           for k, v in DEFAULT_EVENT_SALES_CONFIG.items()}
    if not isinstance(row, dict):
        return cfg

    regs = REG.normalize_registers(_as_list(row.get("event_sales_registers")) or [])
    if regs:
        cfg["event_sales_registers"] = list(regs)

    classes = _as_list(row.get("event_sales_activation_classes"))
    if classes:
        picked = [c for c in (str(x).strip().lower() for x in classes) if c in ACTIVATION_CLASSES]
        if picked:
            cfg["event_sales_activation_classes"] = picked

    cfg["event_retention_windows_days"] = _int_list(
        row.get("event_retention_windows_days"),
        DEFAULT_EVENT_SALES_CONFIG["event_retention_windows_days"])

    if row.get("event_roi_phone_cost_from_catalog") is not None:
        cfg["event_roi_phone_cost_from_catalog"] = bool(row["event_roi_phone_cost_from_catalog"])
    return cfg


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 2. THE SHARED CLASSIFIERS, REACHED LAZILY
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Imported inside the functions for the reason `actuals.py` documents: a marketing page must not pay
# commcalc's import cost, and this module must stay importable (and provable) where commcalc's
# dependencies are absent. Both helpers accept an INJECTED classifier so the harness can prove the
# logic without importing anything, and the injected default IS the shared one in production.
def _shared_classify():
    from app.modules.commcalc.calculator import classify_contract_type
    return classify_contract_type


def _shared_is_voided():
    from app.modules.commcalc.gp_report import is_voided
    return is_voided


def line_class(row, classify=None):
    """'premium' | 'byod' | 'upgrade' | None for one sale row — the SHARED classifier's answer.

    None means "not a phone-activation line". On the live RSK rows that is 362 of 602: device set-up
    charges, bill payments, SIM cards and services — the accompanying lines of the same transactions,
    NOT unreported activations (measured 2026-09-09; every one of the 125 distinct MDNs on the RSK
    rows sits on a line whose contract type is non-blank). The blanks are correctly not activations.
    """
    fn = classify or _shared_classify()
    return fn(str((row or {}).get("contract_type") or ""))


def is_countable(row, is_voided=None):
    """The SHARED skip rules: a void token, or a Return, is not a countable sale.

    Identical to the pair `_sales_cell_agg` applies (`gp_report.is_voided` + `trans_type == 'Return'`).
    The rep skip (`'admin'`/blank) is deliberately NOT applied here: the shared pass drops those rows
    for a REP-grain report, and dropping a store's event sale because the till was signed in as admin
    would understate the store. The counts this module publishes as "activations" come from the shared
    pass itself, which applies its own rule; this predicate governs only the row-level LISTING.
    """
    fn = is_voided or _shared_is_voided()
    if fn((row or {}).get("voided")):
        return False
    return str((row or {}).get("trans_type") or "").strip() != "Return"


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 3. REPORT 1 — TOTAL SALES ("all available fields")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
#: Every column the live RSK rows carry, in the order a human reads a receipt. The report renders and
#: exports ALL of them (the owner: "1 total sales with all available fields n that report"). Adding a
#: column to `raw_sales` adds it here without a code change — see `detail_fields`.
BASE_FIELDS = ("trans_date", "trans_ts", "trans_id", "store", "register", "salesperson",
               "user_login", "contract_type", "department", "category", "product_desc",
               "product_id", "sku", "serial_1", "mdn", "customer", "customer_no", "email",
               "ext_price", "gp", "tax", "tender_type", "trans_type", "voided", "period")

#: Columns that must NEVER be exported from this report even though `raw_sales` carries them.
#: `customer` / `customer_no` / `email` ARE shown — a store manager reconciling an event needs to see
#: who was signed up — but they are named here so the decision is visible rather than accidental.
SENSITIVE_FIELDS = ("email", "customer_no")


def detail_fields(rows, base=BASE_FIELDS):
    """The field list for the detail table: the canonical order first, then any column the rows
    actually carry that the canonical list does not name.

    This is what makes "all available fields" survive the next migration that adds a column to
    `raw_sales`: the new column appears in the report the day it appears in the data, rather than the
    day somebody remembers to add it to a list.
    """
    seen, out = set(), []
    for f in base:
        out.append(f)
        seen.add(f)
    extra = set()
    for r in (rows or []):
        for k in (r or {}):
            if k not in seen and not str(k).startswith("_") and k not in ("id", "org_id"):
                extra.add(k)
    return out + sorted(extra)


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _txn(row):
    return str((row or {}).get("trans_id") or "").replace(".0", "").strip()


def _day(row):
    return str((row or {}).get("trans_date") or "")[:10]


def annotate_rows(rows, cfg, classify=None, is_voided=None):
    """The detail rows, each stamped with the SHARED classifier's verdict and the countable flag.

    Nothing is dropped: a voided or returned line stays in the listing marked `countable: false`, so
    the report shows what actually happened at the till instead of quietly editing history.
    """
    counted = set(cfg.get("event_sales_activation_classes") or ())
    fn_c, fn_v = classify or _shared_classify(), is_voided or _shared_is_voided()
    out = []
    for r in (rows or []):
        cls = line_class(r, fn_c)
        ok = is_countable(r, fn_v)
        d = dict(r)
        d.pop("org_id", None)
        d["line_class"] = cls
        d["line_class_label"] = CLASS_LABELS.get(cls or "", "")
        d["countable"] = ok
        d["is_activation"] = bool(ok and cls in counted)
        out.append(d)
    out.sort(key=lambda x: (_day(x), str(x.get("store") or ""), _txn(x),
                            str(x.get("product_desc") or "")))
    return out


def sales_summary(rows, cfg, classify=None, is_voided=None):
    """Totals for report 1 — and the (store, date) EVENT KEYS the other two reports hang off.

    "An event is effectively a (store, date) pair" is not an assumption this module makes up: it is
    what the live data says (602 RSK rows over 8 stores and 13 distinct trans_dates, measured
    2026-09-09). Where a `marketing_event` row exists the reports use it; where none does — which is
    every row today, `core.marketing_event` being empty — the (store, date) pair is the event, and
    the payload says which of the two it used.
    """
    ann = annotate_rows(rows, cfg, classify, is_voided)
    counted = set(cfg.get("event_sales_activation_classes") or ())

    keys, by_store, by_date, by_ct, by_class = {}, {}, {}, {}, {}
    money = {"ext_price": 0.0, "gp": 0.0, "tax": 0.0}
    txns, mdns, uncountable = set(), set(), 0
    for r in ann:
        store, day = str(r.get("store") or ""), _day(r)
        k = (store, day)
        slot = keys.setdefault(k, {"store": store, "trans_date": day, "rows": 0, "transactions": set(),
                                   "activations": 0, "by_class": {}, "ext_price": 0.0, "gp": 0.0,
                                   "tax": 0.0, "mdns": set()})
        slot["rows"] += 1
        by_store[store] = by_store.get(store, 0) + 1
        by_date[day] = by_date.get(day, 0) + 1
        ct = str(r.get("contract_type") or "").strip() or "(blank)"
        by_ct[ct] = by_ct.get(ct, 0) + 1
        if not r["countable"]:
            uncountable += 1
            continue
        t = _txn(r)
        if t:
            txns.add((store, day, t))
            slot["transactions"].add(t)
        for f in money:
            v = _f(r.get(f))
            money[f] += v
            slot[f] += v
        m = _norm_key(r.get("mdn"))
        if m:
            mdns.add((store, day, m))
            slot["mdns"].add(m)
        cls = r.get("line_class")
        if cls:
            by_class[cls] = by_class.get(cls, 0) + 1
            slot["by_class"][cls] = slot["by_class"].get(cls, 0) + 1
            if cls in counted:
                slot["activations"] += 1

    out_keys = []
    for k in sorted(keys):
        s = keys[k]
        out_keys.append({"store": s["store"], "trans_date": s["trans_date"], "rows": s["rows"],
                         "transactions": len(s["transactions"]), "lines": s["activations"],
                         "by_class": dict(s["by_class"]), "distinct_mdns": len(s["mdns"]),
                         "ext_price": round(s["ext_price"], 2), "gp": round(s["gp"], 2),
                         "tax": round(s["tax"], 2)})
    return {
        "rows": len(ann),
        "uncountable_rows": uncountable,
        "transactions": len(txns),
        "distinct_mdns": len(mdns),
        "ext_price": round(money["ext_price"], 2),
        "gp": round(money["gp"], 2),
        "tax": round(money["tax"], 2),
        "by_contract_type": dict(sorted(by_ct.items(), key=lambda x: (-x[1], x[0]))),
        "by_class": {c: by_class.get(c, 0) for c in ACTIVATION_CLASSES},
        "activation_classes_counted": list(cfg.get("event_sales_activation_classes") or ()),
        "by_store": dict(sorted(by_store.items(), key=lambda x: (-x[1], x[0]))),
        "by_date": dict(sorted(by_date.items())),
        "event_keys": out_keys,
        "fields": detail_fields(rows),
        "sensitive_fields": list(SENSITIVE_FIELDS),
    }


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 4. REPORT 2 — RETENTION OF THE LINES ACTIVATED AT THE EVENT
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# ⚠ NAMING — this is SUBSCRIBER retention. `GET /marketing/checkin-retention` already exists and is
# GDPR DATA retention (the purge date on staff check-in GPS rows). The two share nothing but the
# English word. Everything here says `line_retention` / `subscriber` in its name, its endpoint and its
# payload so a reader who arrives at one can never mistake it for the other.
STATE_ACTIVE = "active"
STATE_CHURNED = "churned"
STATE_UNMATCHED = "unmatched"

#: WHY a line could not be matched. Each is a different real-world thing and a different fix.
REASON_NO_MDN = "no_mdn_on_sale_row"
REASON_FEED_NOT_LOADED = "subscriber_feed_not_loaded"
REASON_ABSENT = "absent_from_loaded_feed"
REASON_DROPPED = "dropped_out_of_the_feed"

UNMATCHED_REASONS = {
    REASON_NO_MDN: ("The sale line carries no mobile number, so there is nothing to look up. "
                    "Accessory, SIM, bill-payment and fee lines legitimately have none."),
    REASON_FEED_NOT_LOADED: ("The subscriber feed for that month has not been loaded, so this line "
                             "could not be looked up at all. This is an ingest gap, not a churn."),
    REASON_ABSENT: ("The month's subscriber feed WAS loaded and this number is not in it — the line "
                    "is not carried by this feed (a different product or carrier), so its status is "
                    "unknown rather than lost."),
    REASON_DROPPED: ("This number was in an earlier month's subscriber feed and is not in the target "
                     "month's. That is consistent with the line going away, but the feed does not "
                     "SAY so, and this report will not call an absence a cancellation."),
}


def _norm_key(v):
    """The line key. Byte-identical to `commission_engine._norm_mdn` (which
    `sale_installment_engine._mi_index` / `_match_mi` build their index with), so a number keyed here
    and a number keyed by the paid gate are the same string."""
    return ("" if v is None else str(v)).replace(".0", "").strip()


def is_active_status(status):
    """ACTIVE vs not. The SAME test the installment paid gate applies
    (`str(...).strip().lower().startswith('activ')`), so "still a subscriber" means one thing on both
    surfaces. Live values on the matched RSK lines: ACTIVE, INVOLUNTARY-SUSPENDED, INACTIVE,
    PORTED-OUT — note that INACTIVE does NOT start with 'activ' and is correctly not active.
    """
    return str(status or "").strip().lower().startswith("activ")


def activation_lines(rows, cfg, classify=None, is_voided=None):
    """One entry per LINE the event activated: (store, event date, MDN), earliest sale row wins.

    De-duplicated on the number because one activation rings several sale lines (device, SIM, set-up
    fee) and counting a line three times would inflate both the numerator and the denominator of a
    retention rate.
    """
    counted = set(cfg.get("event_sales_activation_classes") or ())
    fn_c, fn_v = classify or _shared_classify(), is_voided or _shared_is_voided()
    seen, out = {}, []
    for r in (rows or []):
        if not is_countable(r, fn_v):
            continue
        cls = line_class(r, fn_c)
        if cls not in counted:
            continue
        mdn = _norm_key(r.get("mdn"))
        serial = _norm_key(r.get("serial_1"))
        store, day = str(r.get("store") or ""), _day(r)
        key = (store, day, mdn or ("txn:%s:%s" % (_txn(r), serial)))
        if key in seen:
            if serial and not seen[key]["serial_1"]:
                seen[key]["serial_1"] = serial
            continue
        line = {"store": store, "trans_date": day, "mdn": mdn, "serial_1": serial,
                "trans_id": _txn(r), "line_class": cls,
                "contract_type": str(r.get("contract_type") or "").strip(),
                "salesperson": str(r.get("salesperson") or "").strip(),
                "product_desc": str(r.get("product_desc") or "").strip()}
        seen[key] = line
        out.append(line)
    out.sort(key=lambda x: (x["trans_date"], x["store"], x["mdn"] or x["trans_id"]))
    return out


def target_date(trans_date, window_days):
    """The calendar date a retention window lands on. `window_days=None` means "now"."""
    if window_days is None:
        return None
    try:
        base = _date.fromisoformat(str(trans_date)[:10])
    except (TypeError, ValueError):
        return None
    return (base + timedelta(days=int(window_days))).isoformat()


def period_label_for(day, period_labels):
    """The subscriber-feed period label covering a calendar day, from the labels the feed HAS.

    `period_labels` is `{'YYYY-MM': 'August 2026'}` — built from the periods actually present, never
    from a generated month name, so a label spelling this tenant does not use can never be queried.
    """
    if not day:
        return None
    return (period_labels or {}).get(str(day)[:7])


def evaluate_line(line, window_days, snapshots, period_labels, latest_key=None, earlier_keys=()):
    """ONE line, ONE window → its retention state. PURE.

    `snapshots` is `{period_key: {'index': <_mi_index output>, 'loaded': bool}}`.

    THE RULE THAT MATTERS: a line this function cannot look up is `unmatched` with a reason. It is
    never `churned`, it never enters a rate's denominator, and it is never rendered as 0% retained.
    """
    mdn = _norm_key(line.get("mdn"))
    if not mdn and not _norm_key(line.get("serial_1")):
        return {"state": STATE_UNMATCHED, "reason": REASON_NO_MDN, "status": None,
                "target_date": None, "period": None}

    if window_days is None:
        key = latest_key
        tgt = None
    else:
        tgt = target_date(line.get("trans_date"), window_days)
        key = str(tgt)[:7] if tgt else None
        # A window that has not elapsed yet is not a churn either — it is simply not due.
        if tgt and latest_key and key > latest_key:
            return {"state": STATE_UNMATCHED, "reason": REASON_FEED_NOT_LOADED, "status": None,
                    "target_date": tgt, "period": period_label_for(tgt, period_labels)}

    snap = (snapshots or {}).get(key or "")
    if not snap or not snap.get("loaded"):
        return {"state": STATE_UNMATCHED, "reason": REASON_FEED_NOT_LOADED, "status": None,
                "target_date": tgt, "period": period_label_for(tgt or (key or ""), period_labels)}

    row = _match(line, snap.get("index") or {})
    if row is None:
        reason = REASON_ABSENT
        for ek in (earlier_keys or ()):
            if ek >= (key or ""):
                continue
            s = (snapshots or {}).get(ek) or {}
            if s.get("loaded") and _match(line, s.get("index") or {}) is not None:
                reason = REASON_DROPPED
                break
        return {"state": STATE_UNMATCHED, "reason": reason, "status": None,
                "target_date": tgt, "period": period_label_for(tgt or (key or ""), period_labels)}

    status = str(row.get("subscriber_status") or "").strip()
    return {"state": (STATE_ACTIVE if is_active_status(status) else STATE_CHURNED),
            "reason": None, "status": status or None, "target_date": tgt,
            "period": period_label_for(tgt or (key or ""), period_labels),
            "mi_activation_date": row.get("mi_activation_date"),
            "mi_deactivation_date": row.get("mi_deactivation_date")}


def _match(line, index):
    """MDN first, then device serial — the paid gate's key, reached through its own function when
    commcalc is importable and reproduced from the SAME index shape when it is not (the harness runs
    without commcalc; production always has it)."""
    try:
        from app.modules.commcalc.sale_installment_engine import _match_mi
        return _match_mi(line, index)
    except Exception:
        m = _norm_key(line.get("mdn"))
        if m and m in (index.get("mdn") or {}):
            return index["mdn"][m]
        s = _norm_key(line.get("serial_1"))
        if s and s in (index.get("serial") or {}):
            return index["serial"][s]
        return None


def retention_report(lines, snapshots, period_labels, windows, latest_key=None):
    """Report 2, whole. Per line × window, then rolled up per window, per store and per event key.

    `retention_pct` is `None` — never 0 — when a window has no matched line, because "we could not
    look any of them up" and "none of them stayed" are different facts and only one of them is bad
    news about the event.
    """
    win = list(windows or []) + [None]           # None = "still active now"
    keys = sorted(k for k, v in (snapshots or {}).items() if v.get("loaded"))
    per_line, buckets, by_store, by_key = [], {}, {}, {}

    for line in (lines or []):
        entry = dict(line)
        entry["windows"] = {}
        for w in win:
            wk = _window_key(w)
            v = evaluate_line(line, w, snapshots, period_labels, latest_key=latest_key,
                              earlier_keys=keys)
            entry["windows"][wk] = v
            b = buckets.setdefault(wk, _blank_bucket(w))
            _tally(b, v)
            if w is None:
                s = by_store.setdefault(line.get("store") or "", _blank_bucket(None))
                _tally(s, v)
                ek = "%s|%s" % (line.get("store") or "", line.get("trans_date") or "")
                k = by_key.setdefault(ek, {**_blank_bucket(None), "store": line.get("store") or "",
                                           "trans_date": line.get("trans_date") or ""})
                _tally(k, v)
        per_line.append(entry)

    return {
        "lines": per_line,
        "windows": [buckets[_window_key(w)] for w in win if _window_key(w) in buckets],
        "by_store": [{"store": s, **b} for s, b in sorted(by_store.items())],
        "by_event_key": [b for _k, b in sorted(by_key.items())],
        "unmatched_reasons": dict(UNMATCHED_REASONS),
        "basis": RETENTION_BASIS,
    }


RETENTION_BASIS = {
    "headline": "Subscriber retention of the lines rung on the event register — NOT data retention",
    "detail": (
        "Each line activated on the event register is matched into the carrier subscriber feed by "
        "mobile number, falling back to device serial — the same key the commission paid-gate uses. "
        "A matched line is reported ACTIVE or NOT ACTIVE from the feed's own subscriber status. A "
        "line that cannot be matched is reported separately with the reason, is NEVER counted as a "
        "cancellation, and is NEVER in the denominator of a retention percentage."),
    "grain_note": (
        "The subscriber feed is a MONTHLY snapshot, so a 30/60/90-day window is answered by the "
        "snapshot for the month that day falls in — not by the day itself. A window whose month has "
        "not been loaded yet is reported as not-yet-answerable, never as a loss."),
    "not_to_be_confused_with": (
        "GET /marketing/checkin-retention — which is the GDPR purge schedule for staff check-in GPS "
        "rows and has nothing to do with subscribers."),
}


def _window_key(w):
    return "now" if w is None else ("d%d" % int(w))


def _blank_bucket(w):
    return {"window": _window_key(w), "window_days": w,
            "label": ("Still active now" if w is None else "Active at %d days" % int(w)),
            "lines": 0, "active": 0, "churned": 0, "unmatched": 0,
            "by_reason": {}, "by_status": {}, "retention_pct": None}


def _tally(bucket, v):
    bucket["lines"] += 1
    st = v["state"]
    bucket[st] = bucket.get(st, 0) + 1
    if st == STATE_UNMATCHED:
        r = v.get("reason") or REASON_ABSENT
        bucket["by_reason"][r] = bucket["by_reason"].get(r, 0) + 1
    elif v.get("status"):
        bucket["by_status"][v["status"]] = bucket["by_status"].get(v["status"], 0) + 1
    den = bucket["active"] + bucket["churned"]
    bucket["matched"] = den
    bucket["retention_pct"] = (round(100.0 * bucket["active"] / den, 1) if den else None)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 5. REPORT 3 — ROI
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# ROI = commission received for the lines activated that day on the event register  −  event cost.
#
# ═══ THE COMMISSION GRAIN, STATED RATHER THAN GLOSSED ═════════════════════════════════════════════
# The owner asked for "the total commssion received for the lines activated on that day via teh rsk".
# That is NOT exactly expressible from any feed the platform holds, and pretending otherwise would be
# the flattering kind of wrong:
#   • ePay Commission Payment Detail carries a store address and a payment TYPE naming the month of
#     life. It carries no transaction id, no MDN and no register — a dollar cannot be traced to a
#     sale line, let alone to a till.
#   • VidaPay / master-agent commission carries no store at all (the breakout endpoint EXCLUDES it
#     while a store filter is active, and says so).
#   • MI/ATU residual is the ONE exception: `raw_mi` is per SUBSCRIBER, so residual IS exactly
#     attributable to the individual lines this event activated.
# So the report offers two bases and labels both:
#   `matched_line_residual` — EXACT. The residual actually paid on the very lines the event
#                             activated, summed from the subscriber rows they matched to. A floor,
#                             not the whole commission.
#   `allocated_store_month` — an ALLOCATION, `exact: false`. The store's commission received for the
#                             event's month, times the event's share of that store's countable
#                             activations that month. Both counts come from the SAME shared sales
#                             pass, so the ratio is internally consistent; it is still an allocation
#                             and the payload says so on every response.
COMMISSION_BASIS_EXACT = "matched_line_residual"
COMMISSION_BASIS_ALLOCATED = "allocated_store_month"

COMMISSION_BASIS_NOTES = {
    COMMISSION_BASIS_EXACT: (
        "EXACT for what it covers. The subscriber feed is per line, so the residual paid on the very "
        "numbers this event activated is attributable to them with no allocation. It is only the "
        "residual leg, so it is a FLOOR on what the event earned, not the whole of it."),
    COMMISSION_BASIS_ALLOCATED: (
        "AN ALLOCATION, NOT A MEASUREMENT. No commission feed carries a register or a transaction, so "
        "commission cannot be traced to one till on one day. This takes the store's commission "
        "received for the event's month and multiplies it by the event's share of that store's "
        "countable activations for the month. Both counts come from the one shared sales pass. Read "
        "it as an estimate with a stated method."),
}

#: The cost components the owner named, in the order the ROI screen collects them.
COST_EVENT_SPEND = "event_spend"
COST_PAYROLL = "payroll"
COST_PHONES = "phones_given"

COST_LABELS = {
    COST_EVENT_SPEND: "Cost to set up the event",
    COST_PAYROLL: "Payroll paid for the event",
    COST_PHONES: "Phones given away",
}

BASIS_DERIVED = "derived"
BASIS_ENTERED = "entered"
BASIS_PROMPT = "prompt_required"


def cost_component(kind, amount, basis, source, note=None, detail=None):
    """ONE cost line. `amount` is None whenever `basis` is `prompt_required` — the type system of
    this report: an unknown cost is a None that propagates, never a 0.0 that adds up."""
    return {"kind": kind, "label": COST_LABELS.get(kind, kind),
            "amount": (None if basis == BASIS_PROMPT else round(float(amount or 0.0), 2)),
            "basis": basis, "source": source, "note": note, "detail": detail or {}}


def phone_lines(rows, cfg, classify=None, is_voided=None):
    """The handset lines rung on the event register — the "number of phones activated" and the SKUs
    whose cost the catalog is asked for.

    A phone line is a countable activation line that carries a DEVICE SERIAL. That is the property
    the data actually has (a handset is the line with an IMEI on it), not a product-name guess and
    not a department literal — RULE TWO. On the live rows 150 of 602 carry a serial.
    """
    counted = set(cfg.get("event_sales_activation_classes") or ())
    fn_c, fn_v = classify or _shared_classify(), is_voided or _shared_is_voided()
    out = []
    for r in (rows or []):
        if not is_countable(r, fn_v):
            continue
        if line_class(r, fn_c) not in counted:
            continue
        serial = _norm_key(r.get("serial_1"))
        if not serial:
            continue
        out.append({"serial_1": serial, "mdn": _norm_key(r.get("mdn")),
                    "product_id": _norm_key(r.get("product_id")),
                    "sku": _norm_key(r.get("sku")),
                    "product_desc": str(r.get("product_desc") or "").strip(),
                    "store": str(r.get("store") or ""), "trans_date": _day(r),
                    "ext_price": round(_f(r.get("ext_price")), 2)})
    out.sort(key=lambda x: (x["trans_date"], x["store"], x["product_desc"]))
    return out


def catalog_index(catalog_rows):
    """{key: cost} from `commcalc.raw_catalog`, keyed by product_id AND by sku, so a sale line that
    carries either one resolves. The catalog IS "the sku report" the owner named."""
    by_pid, by_sku = {}, {}
    for r in (catalog_rows or []):
        cost = r.get("cost")
        if cost is None:
            continue
        pid, sku = _norm_key(r.get("product_id")), _norm_key(r.get("sku")).upper()
        if pid:
            by_pid.setdefault(pid, _f(cost))
        if sku:
            by_sku.setdefault(sku, _f(cost))
    return {"product_id": by_pid, "sku": by_sku}


def price_phones(lines, cat, entered_unit_costs=None, use_catalog=True):
    """Phone cost. Catalog where the SKU resolves; the human's typed figure where it does not; and a
    PROMPT where neither exists — the owner's *"the system will ask ... how much is the cost of the
    phone"*.

    Deliberately per DISTINCT PRODUCT, not per line: the screen asks one question per phone model,
    not one per handset, and the answer is reused for every unit of that model.
    """
    entered = {str(k).strip().upper(): _f(v) for k, v in (entered_unit_costs or {}).items()
               if v is not None and str(v) != ""}
    products, total, unpriced = {}, 0.0, []
    for ln in (lines or []):
        pid, sku = ln.get("product_id") or "", (ln.get("sku") or "").upper()
        key = pid or sku or (ln.get("product_desc") or "").upper()
        p = products.setdefault(key, {"key": key, "product_id": pid, "sku": ln.get("sku") or "",
                                      "product_desc": ln.get("product_desc") or "", "qty": 0,
                                      "unit_cost": None, "cost_source": None})
        p["qty"] += 1
    for p in products.values():
        k = str(p["key"]).upper()
        if k in entered:
            p["unit_cost"], p["cost_source"] = round(entered[k], 2), BASIS_ENTERED
        elif use_catalog and p["product_id"] and p["product_id"] in cat.get("product_id", {}):
            p["unit_cost"] = round(cat["product_id"][p["product_id"]], 2)
            p["cost_source"] = "catalog_product_id"
        elif use_catalog and str(p["sku"]).upper() in cat.get("sku", {}):
            p["unit_cost"] = round(cat["sku"][str(p["sku"]).upper()], 2)
            p["cost_source"] = "catalog_sku"
        else:
            p["cost_source"] = BASIS_PROMPT
            unpriced.append(p)
            continue
        total += p["unit_cost"] * p["qty"]
    rows = sorted(products.values(), key=lambda x: (-x["qty"], x["product_desc"]))
    return {"products": rows, "units": sum(p["qty"] for p in rows),
            "priced_units": sum(p["qty"] for p in rows if p["cost_source"] != BASIS_PROMPT),
            "total": round(total, 2), "unpriced": unpriced}


def payroll_from_hours(staff_hours, rates):
    """Event payroll from the EXISTING three-state hours contract.

    `staff_hours` is what `storeops/salary_expense.day_measurement` + `hours_for_shift` already
    decide for an (employee, day): measured hours where a closed punch or a manual correction exists,
    scheduled hours only where nothing was measured, and a MEASURED ZERO stays zero. This function
    does not re-decide any of that — it multiplies the hours that contract produced by the employee's
    rate and reports the provenance mix.

    An employee with hours and NO hourly rate (a salaried person, whose pay is allocated per MONTH by
    `payroll_salary.py` and cannot be split onto one day without inventing an allocation) is reported
    as UNPRICED. The component then needs a human figure rather than quietly costing zero.
    """
    total, measured, scheduled, rows, unpriced = 0.0, 0.0, 0.0, [], []
    for h in (staff_hours or []):
        emp = str(h.get("employee_id") or "")
        hours = _f(h.get("hours"))
        state = h.get("state") or "not_measured"
        if state == "measured":
            measured += hours
        else:
            scheduled += hours
        rate = (rates or {}).get(emp)
        row = {"employee_id": emp, "employee_name": h.get("employee_name") or emp,
               "day": h.get("day"), "hours": round(hours, 2), "hours_state": state,
               "pay_rate": (None if rate is None else round(_f(rate), 2)),
               "amount": None}
        if rate is None or _f(rate) <= 0:
            row["reason"] = ("No hourly rate on file for this person (a salaried employee's pay is "
                             "allocated by month and cannot be split onto one day without inventing "
                             "an allocation).")
            unpriced.append(row)
        else:
            row["amount"] = round(hours * _f(rate), 2)
            total += row["amount"]
        rows.append(row)
    return {"rows": rows, "total": round(total, 2), "hours_measured": round(measured, 2),
            "hours_scheduled": round(scheduled, 2), "unpriced": unpriced}


def build_costs(event, entered, payroll, phones):
    """The three cost components, each with its basis. A component nobody can derive and nobody has
    typed is `prompt_required` with `amount: None`."""
    out = []

    spend = (entered or {}).get(COST_EVENT_SPEND)
    if spend is not None and str(spend) != "":
        out.append(cost_component(COST_EVENT_SPEND, spend, BASIS_ENTERED,
                                  "entered on the ROI screen"))
    elif isinstance(event, dict) and event.get("planned_spend") is not None:
        out.append(cost_component(
            COST_EVENT_SPEND, event.get("planned_spend"), BASIS_DERIVED,
            "core.marketing_event.planned_spend",
            note=("The plan's own spend figure for this event. It is a PLANNED number — if the day "
                  "cost more or less, correct it here and the correction is what the ROI uses.")))
    else:
        out.append(cost_component(
            COST_EVENT_SPEND, None, BASIS_PROMPT, "not derivable",
            note=("No event record covers this store and date, so nothing states what the day cost "
                  "to set up. Enter it (or link this day to an event) and the ROI completes.")))

    p_entered = (entered or {}).get(COST_PAYROLL)
    if p_entered is not None and str(p_entered) != "":
        out.append(cost_component(COST_PAYROLL, p_entered, BASIS_ENTERED,
                                  "entered on the ROI screen"))
    elif payroll and payroll.get("rows") and not payroll.get("unpriced"):
        out.append(cost_component(
            COST_PAYROLL, payroll["total"], BASIS_DERIVED,
            "storeops hours (salary_expense three-state contract) x employee pay rate",
            note=("%.2f measured hours and %.2f scheduled-fallback hours across %d shift(s)."
                  % (payroll["hours_measured"], payroll["hours_scheduled"], len(payroll["rows"]))),
            detail={"rows": payroll["rows"]}))
    elif payroll and payroll.get("unpriced"):
        out.append(cost_component(
            COST_PAYROLL, None, BASIS_PROMPT, "hours known, rate not",
            note=("Hours were found for %d person-day(s) but %d of them have no hourly rate on "
                  "file, so the payroll for the day cannot be completed without a figure."
                  % (len(payroll["rows"]), len(payroll["unpriced"]))),
            detail={"unpriced": payroll["unpriced"], "rows": payroll["rows"]}))
    else:
        out.append(cost_component(
            COST_PAYROLL, None, BASIS_PROMPT, "no staffing recorded",
            note=("Nobody is recorded as having worked this event, so there are no hours to cost. "
                  "Enter what payroll the day cost, or staff the event record.")))

    if phones and phones.get("units"):
        if phones.get("unpriced"):
            out.append(cost_component(
                COST_PHONES, None, BASIS_PROMPT, "sales report x SKU catalog",
                note=("%d phone(s) went out on this event register and %d of the model(s) have no "
                      "cost in the SKU catalog. Enter the cost of the phone and the ROI completes."
                      % (phones["units"], len(phones["unpriced"]))),
                detail={"products": phones["products"], "unpriced": phones["unpriced"]}))
        else:
            out.append(cost_component(
                COST_PHONES, phones["total"], BASIS_DERIVED,
                "sales report (count) x commcalc.raw_catalog (cost)",
                note="%d phone(s), costed from the SKU catalog." % phones["units"],
                detail={"products": phones["products"]}))
    else:
        out.append(cost_component(
            COST_PHONES, 0.0, BASIS_DERIVED, "sales report",
            note=("No handset line was rung on the event register for this store and date, so no "
                  "phone cost arises. This zero is a MEASURED zero, not a missing figure.")))
    return out


def roi_compute(commission_amount, costs, commission_basis, commission_exact):
    """The ROI figure — or an honest refusal to produce one.

    Refuses when ANY cost component is `prompt_required`. There is no partial ROI here: a return on
    investment that leaves out part of the investment is not a smaller number, it is a wrong one, and
    it is wrong in the direction that makes the event look good.
    """
    missing = [c["kind"] for c in (costs or []) if c["basis"] == BASIS_PROMPT]
    known = sum(float(c["amount"] or 0.0) for c in (costs or []) if c["basis"] != BASIS_PROMPT)
    comm = None if commission_amount is None else round(float(commission_amount), 2)
    out = {
        "commission_received": comm,
        "commission_basis": commission_basis,
        "commission_exact": bool(commission_exact),
        "commission_note": COMMISSION_BASIS_NOTES.get(commission_basis),
        "cost_total_known": round(known, 2),
        "cost_components": list(costs or []),
        "missing_costs": missing,
        "complete": (not missing) and comm is not None,
    }
    if missing or comm is None:
        out.update({"cost_total": None, "net": None, "roi_pct": None,
                    "reason": _incomplete_reason(missing, comm)})
        return out
    net = round(comm - known, 2)
    out.update({"cost_total": round(known, 2), "net": net,
                "roi_pct": (round(100.0 * net / known, 1) if known else None),
                "reason": None})
    if not known:
        out["reason"] = ("Every cost component came back as a measured zero, so there is no "
                         "investment to return on and the percentage is not defined.")
    return out


def _incomplete_reason(missing, comm):
    parts = []
    if missing:
        parts.append("The ROI is not computed because %s %s not known. Nothing here is shown as "
                     "$0.00 to fill the gap — enter the figure(s) and the ROI completes."
                     % (" and ".join(COST_LABELS.get(m, m).lower() for m in missing),
                        "is" if len(missing) == 1 else "are"))
    if comm is None:
        parts.append("No commission figure could be read for this store and month.")
    return " ".join(parts)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 6. LINKING A (STORE, DATE) TO AN EXISTING EVENT — or standing in for one
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# `core.marketing_event` holds ZERO rows today, so "the event was not loaded previously" is the
# NORMAL path, not an edge case. These functions make the un-linked day a first-class citizen: the
# report runs, states that no event covers it, and offers the minimum the owner named to create one.
def match_event(events, event_stores, store_code, day, store_aliases=()):
    """The `marketing_event` covering this (store, date), or None.

    An event matches when the day falls inside its calendar window AND the store is one of its
    stores. Store membership goes through `core.marketing_event_store` (the many-to-many the module
    already has — one table event worked by two stores) with `primary_store_code` as the fallback for
    an event that never had its store set added. NO second store-attribution path is created here.
    """
    want = {str(s).strip().upper() for s in ([store_code] + list(store_aliases or []))
            if str(s or "").strip()}
    by_event = {}
    for r in (event_stores or []):
        by_event.setdefault(str(r.get("event_id") or ""), set()).add(
            str(r.get("store_code") or "").strip().upper())
    best = None
    for e in (events or []):
        codes = by_event.get(str(e.get("id") or ""), set())
        if not codes and e.get("primary_store_code"):
            codes = {str(e["primary_store_code"]).strip().upper()}
        if not (codes & want):
            continue
        days = _event_days(e)
        if day not in days:
            continue
        if best is None or str(e.get("event_start") or "") < str(best.get("event_start") or ""):
            best = e
    return best


def _event_days(event):
    from app.modules.marketing import event_logic as L
    return set(L.event_dates(event))


def minimal_event_payload(store_code, day, title=None, market=None, planned_spend=None):
    """The MINIMUM `POST /marketing/events` body that makes an event able to carry a cost — the
    owner's *"create the event in the system with the minimal information which is required to
    compute the cost"*.

    Title, one store, one calendar day, and the spend. Everything else the event schema offers is
    left NULL: this is a cost carrier the owner is creating from a report, not a planning exercise
    they are being made to fill in.
    """
    d = str(day)[:10]
    return {
        "title": title or ("Event — %s, %s" % (store_code, d)),
        "market": market or None,
        "primary_store_code": store_code,
        "store_codes": [store_code],
        "event_start": "%sT00:00:00+00:00" % d,
        "event_end": "%sT23:59:59+00:00" % d,
        "planned_spend": planned_spend,
        "description": ("Created from the Sales from Events ROI report to carry the day's cost. "
                        "Only the fields needed to compute cost were set."),
    }


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 7. THE ATTRIBUTION BLOCK — carried by every response, exactly as `actuals.py` does
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def attribution(cfg, registers_seen=None, source_note=None):
    """What these reports claim, and what they refuse to claim. Rendered as a visible caption."""
    return {
        "headline": ("Sales rung on the event register — not every sale the event caused, and not "
                     "every sale rung at the store"),
        "detail": (
            "These reports read ONLY the sale lines whose POS register is one of the org's "
            "configured event registers. That is a fact about the till, and it is the closest thing "
            "in the data to 'this was sold at the event'. It is not the same as event-caused "
            "business: a conversation started at a table often rings up at the store days later and "
            "will not appear here, and a line rung on the event register inside the store will. The "
            "store-wide view of an event window, with a same-weekday baseline, is the separate "
            "GET /marketing/events/{id}/actuals report — this one is deliberately narrower."),
        "register_note": (
            "RSK is the value of raw_sales.register, NOT a tender type — the tender on those same "
            "rows reads Cash / Credit Card / Debit Card. The register reading is the one "
            "commcalc/flags.py's RSK_ACTIVATIONS flag has always used and is now shared with it."),
        "activation_note": (
            "An activation is whatever the ONE shared contract-type classifier says it is "
            "(commcalc.calculator.classify_contract_type — the same classifier behind the Sales "
            "Report, Executive MTD and Daily Targets). A blank contract type is not an activation: "
            "on the live event-register rows the blanks are device set-up charges, bill payments, "
            "SIM cards and services, and every distinct mobile number on those rows sits on a line "
            "whose contract type is non-blank."),
        "registers": list(cfg.get("event_sales_registers") or ()),
        "activation_classes_counted": list(cfg.get("event_sales_activation_classes") or ()),
        "registers_seen": dict(registers_seen or {}),
        "source": "commcalc.raw_sales, filtered by register before the shared sales pass runs.",
        "source_note": source_note,
    }


def no_register_note(cfg, registers_seen):
    """The empty-state that says WHY, instead of an empty table. Returns None when there is nothing
    to explain."""
    want = list(cfg.get("event_sales_registers") or ())
    if not want:
        return ("No event register is configured for this org, so no sale can be an event sale. Set "
                "one in Marketing Settings — the reports will not guess.")
    seen = [k for k in (registers_seen or {}) if k]
    if seen:
        return ("No rows were rung on register %s for this period. The registers that DO appear are: "
                "%s." % (", ".join(want), ", ".join(sorted(seen))))
    return None


def now_utc():
    return datetime.now(timezone.utc)
