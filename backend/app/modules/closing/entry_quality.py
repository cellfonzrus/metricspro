"""Closing entry-quality coaching — PURE detection logic (owner directive 2026-09-02, item 3;
mig 937).

The owner's words, verbatim: "Need a training walkthru for an employee if their data is not
entered correctly for a second day in a row to tell them that they are not entering the data
correctly or clearly whatever the case is and guiding them how to correct it."

WHAT "NOT ENTERED CORRECTLY" MEANS (config-selectable signals, both on by default):
  • 'dm_corrected'    — the store-day the employee submitted a closing row on was DM-verified
                        WITH a correction (daily_closing_verification dm_* set — the DM had to
                        change the store's figures; the strongest 'the entry was wrong' signal
                        the platform records).
  • 'sent_to_review'  — the employee's own row was flagged by the close gate: `auto_accepted`
                        (3 tries, still mismatched against B2B) or `mgmt_flag`.

DETECTION: an employee whose entry was incorrect on N CONSECUTIVE days (threshold_days, house
default 2 — "a second day in a row") gets the walkthrough nudge. Delivery:
  • in-app: GET /closing/entry-quality/me → the submit page shows the guidance banner + a
    "Walk me through" that launches the org's configured training tour (default: the existing
    'closing-submit' Training Center tour — data, not code; a tenant can point at its own);
  • notify: POST /closing/entry-quality/run-due emails/WhatsApps the employee when the org's
    config enables a channel (house default: none — in-app only), idempotent per
    (employee, streak-end day) via commcalc.closing_entry_coaching (mig 937).

RULE TWO: thresholds, signals, message template and tour slug are per-org config
(commcalc.closing_entry_quality_config) with house defaults — no tenant name in code.
Everything here is PURE — proof: backend/harness_closing_entry_quality.py.
"""
from datetime import date, timedelta

DEFAULT_SIGNALS = ("dm_corrected", "sent_to_review")
DEFAULT_THRESHOLD_DAYS = 2
DEFAULT_TOUR_SLUG = "closing-submit"
DEFAULT_MESSAGE = (
    "Hi {name} — your daily closing entry needed correction on {days} "
    "({reasons}). That usually means a number was entered in the wrong field or didn't match "
    "what was actually collected. Please take the quick closing walkthrough so tonight's entry "
    "goes in right — and ask your manager if anything on the form is unclear.")

_DM_FIELDS = ("dm_store_cash", "dm_store_cc", "dm_epay_cash", "dm_epay_cc", "dm_acc_sale", "dm_other")

_REASON_LABEL = {
    "dm_corrected": "your manager had to correct the figures",
    "sent_to_review": "the entry didn't match the day's sales and went to review",
}


def default_config():
    return {"enabled": True, "threshold_days": DEFAULT_THRESHOLD_DAYS,
            "signals": list(DEFAULT_SIGNALS), "notify_channel": "none",
            "message_template": DEFAULT_MESSAGE, "tour_slug": DEFAULT_TOUR_SLUG}


def resolve_config(row):
    """PURE: a commcalc.closing_entry_quality_config row (or None) → the effective config, house
    defaults filling every blank. Garbage values degrade to the default, never raise."""
    cfg = default_config()
    r = row or {}
    if r.get("enabled") is not None:
        cfg["enabled"] = bool(r.get("enabled"))
    try:
        t = int(r.get("threshold_days"))
        if 1 <= t <= 30:
            cfg["threshold_days"] = t
    except (TypeError, ValueError):
        pass
    sigs = r.get("signals")
    if isinstance(sigs, list):
        picked = [s for s in (str(x).strip() for x in sigs) if s in DEFAULT_SIGNALS]
        if picked:
            cfg["signals"] = picked
    ch = str(r.get("notify_channel") or "").strip().lower()
    if ch in ("none", "email", "whatsapp", "both"):
        cfg["notify_channel"] = ch
    if str(r.get("message_template") or "").strip():
        cfg["message_template"] = str(r.get("message_template")).strip()
    if str(r.get("tour_slug") or "").strip():
        cfg["tour_slug"] = str(r.get("tour_slug")).strip()
    return cfg


def _day(v):
    return str(v or "")[:10]


def _has_dm_correction(ver_row):
    v = ver_row or {}
    return bool(v.get("verified")) and any(v.get(k) is not None for k in _DM_FIELDS)


def incorrect_days(closing_rows, ver_by_store_day, signals=DEFAULT_SIGNALS):
    """PURE: {employee_name (as submitted): {day: sorted [reasons]}} over the given daily_closing
    rows. A day is 'incorrect' for an employee when any enabled signal fired for a row they
    submitted. `ver_by_store_day` = {(store_code, day): verification row}."""
    sigs = set(signals or DEFAULT_SIGNALS)
    out = {}
    for r in closing_rows or []:
        r = r or {}
        emp = (r.get("employee_name") or "").strip()
        d = _day(r.get("close_date"))
        if not emp or not d:
            continue
        reasons = []
        if "dm_corrected" in sigs and _has_dm_correction(
                ver_by_store_day.get((r.get("store_code"), d))):
            reasons.append("dm_corrected")
        if "sent_to_review" in sigs and (r.get("auto_accepted") or r.get("mgmt_flag")):
            reasons.append("sent_to_review")
        if reasons:
            slot = out.setdefault(emp, {})
            slot[d] = sorted(set(slot.get(d, []) + reasons))
    return out


def _to_date(s):
    try:
        y, m, d = (int(p) for p in str(s)[:10].split("-"))
        return date(y, m, d)
    except Exception:
        return None


def streaks(days):
    """PURE: sorted day strings → [(start, end, length)] of CONSECUTIVE calendar-day runs.
    Unparseable dates are skipped (never crash a coaching sweep on one bad row)."""
    ds = sorted({d for d in ((_to_date(x)) for x in (days or [])) if d})
    out, start, prev = [], None, None
    for d in ds:
        if start is None:
            start, prev = d, d
            continue
        if (d - prev).days == 1:
            prev = d
            continue
        out.append((start.isoformat(), prev.isoformat(), (prev - start).days + 1))
        start, prev = d, d
    if start is not None:
        out.append((start.isoformat(), prev.isoformat(), (prev - start).days + 1))
    return out


def needs_walkthrough(days_by_emp, threshold_days=DEFAULT_THRESHOLD_DAYS, recent_within=None,
                      as_of=None):
    """PURE: the employees whose incorrect-entry streak reached the threshold. Returns
    [{employee_name, streak, streak_start, streak_end, days: {day: reasons}}], one entry per
    employee for their LONGEST qualifying streak (most recent on a tie). `recent_within` (days,
    optional) keeps only streaks whose END is within that many days of `as_of` — the run-due
    sweep passes 1 so an old resolved streak never re-notifies forever."""
    out = []
    ref = _to_date(as_of) if as_of else None
    for emp, dmap in (days_by_emp or {}).items():
        best = None
        for (s, e, n) in streaks(dmap.keys()):
            if n < max(int(threshold_days or DEFAULT_THRESHOLD_DAYS), 1):
                continue
            if recent_within is not None and ref is not None:
                ed = _to_date(e)
                if ed is None or (ref - ed).days > recent_within:
                    continue
            if best is None or (n, e) > (best[2], best[1]):
                best = (s, e, n)
        if best:
            s, e, n = best
            run_days = {d: dmap[d] for d in dmap if s <= _day(d) <= e}
            out.append({"employee_name": emp, "streak": n, "streak_start": s, "streak_end": e,
                        "days": run_days})
    out.sort(key=lambda x: (-x["streak"], x["employee_name"].casefold()))
    return out


def guidance_message(template, name, days_map):
    """PURE: render the per-org guidance template. Placeholders: {name}, {days} (the incorrect
    days, comma-joined), {reasons} (plain-English union of the fired signals). A template missing
    a placeholder still renders (format_map with a default dict — a tenant's shortened message
    never crashes the banner)."""
    days = ", ".join(sorted(days_map or {}))
    reason_keys = sorted({r for rs in (days_map or {}).values() for r in rs})
    reasons = "; ".join(_REASON_LABEL.get(r, r) for r in reason_keys) or "entry issues"

    class _Safe(dict):
        def __missing__(self, k):
            return "{" + k + "}"

    return str(template or DEFAULT_MESSAGE).format_map(
        _Safe(name=name or "there", days=days or "recent days", reasons=reasons))
