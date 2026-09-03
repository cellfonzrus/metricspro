"""Flags & Compliance dashboard — the category registry + pure count assembly
(owner directive 2026-09-03: "Flags and Compliance should be a separate Dashboard and every flag
and compliance issue should be under that").

WHAT THIS IS: ONE thin count-aggregator over the platform's EXISTING flag/exception/compliance
surfaces — every count in the summary is produced by (or by the same query as) an existing page's
own endpoint; nothing here derives a new number (duplicate-check gate, CLAUDE.md). The router glue
(`GET /commcalc/compliance-summary`) gathers the raw counts best-effort and this module assembles
the payload: a category whose probe failed reports count=null with a note — NEVER a fake 0 (a
manager must be able to tell "nothing open" from "could not check").

The dashboard itself is tile DATA: the `flags-compliance` house tile layout (mig 948, D1 storage,
tenant-editable in the Dashboard Designer) + the /compliance page (StatTile counts off this
summary + the resolved tile layout). Pure + stdlib-only; proof backend/harness_compliance_summary.py.
"""

# key, label, href (the existing page that owns the queue), one-line meaning of the count
CATEGORIES = (
    ("commission_flags", "Commission flags", "/commcalc/flags",
     "Open flags for the period (commcalc.flags, status open)"),
    ("pay_discrepancy", "Pay discrepancy open items", "/commcalc/commission-discrepancy",
     "Sold-but-unpaid rows still open for the period (discrepancy_results)"),
    ("ingest_quarantine", "Ingest guard quarantine", "/commcalc/ingest-guard",
     "Unrecognised store strings withheld from ingest, pending a decision"),
    ("ops_chargebacks", "Ops chargebacks pending", "/closing/envelope-report",
     "Operational chargebacks (missed closing / missed DM verify / envelope short) awaiting a decision"),
    ("attendance_exceptions", "Attendance exceptions", "/storeops/attendance",
     "No-shows, missed punches and other exceptions this pay period"),
    ("hours_approval", "Hours approval pending", "/storeops/payroll/approvals",
     "Payroll hours awaiting DM or HR approval for the previous complete period"),
    ("approvals_pending", "Approvals inbox", "/approvals",
     "Pending approval requests across modules (time-clock permissions etc.)"),
    ("deposit_accountability", "Deposit accountability", "/closing/deposit-recon",
     "Store-days this month not yet green — missing slip, unconfirmed hand-off, or no disposition"),
    ("billpay_coverage", "Bill-pay coverage exceptions", "/closing/tender-recon-3way",
     "Days this month where bill payments exceed the cash+card the store declared"),
    ("statement_staleness", "Financial statements stale", "/accounts/pl",
     "1 when the period's statements are older than the newest ingested data (recompute due)"),
)


def assemble(counts, period=None, as_of=None):
    """counts = {key: int | None | {'count': int|None, 'note': str}} (missing key = not probed →
    null). Returns the summary payload. total_open sums only the KNOWN counts; `unavailable`
    lists categories whose probe failed so the page can say so honestly."""
    counts = counts or {}
    cats, total, unavailable = [], 0, []
    for key, label, href, desc in CATEGORIES:
        raw = counts.get(key)
        note = None
        if isinstance(raw, dict):
            note = raw.get("note") or None
            raw = raw.get("count")
        n = None
        if raw is not None:
            try:
                n = max(0, int(raw))
            except (TypeError, ValueError):
                n = None
        cat = {"key": key, "label": label, "href": href, "desc": desc, "count": n}
        if note:
            cat["note"] = note
        if n is None:
            unavailable.append(key)
        else:
            total += n
        cats.append(cat)
    return {"period": period, "as_of": as_of, "categories": cats,
            "total_open": total, "unavailable": unavailable}
