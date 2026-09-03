"""Commission Discrepancy hub — APPEAL STATE on discrepancy rows (owner directive 2026-09-03).

The owner's ask, verbatim: "new tile for Commission Discrepancy which will include any reports or
query for the commission not received and the appeals which need to be done."

WHAT THIS IS: a MINIMAL appeal state machine layered onto the EXISTING
commcalc.discrepancy_results rows (mig 312 canonical DDL; appeal columns added by mig 946) so
management can mark a commission-not-received row "appeal filed / appeal won / written off" with
who/when. It deliberately does NOT create a second discrepancy store, a second recon engine, or a
second claims pipeline (duplicate-check build gate):

  · The rows THEMSELVES stay the two existing engines' output (discrepancy_engine source='boost',
    ma_recon source='ma') — this module never inserts or recomputes a discrepancy, it only
    annotates appeal state onto rows the engines already wrote. Ingest/compute stay separate from
    the human appeal workflow (money posture: nothing here touches expected/received/gap).
  · The DENIED-appeal claw-back pipeline (mig 098 appeal_recovery/appeal_claim, the /recovery/*
    module) is a DIFFERENT lifecycle — asset-ledger denials chased as weekly carrier claim
    batches — and is REUSED as-is: the Commission Discrepancy hub links its open-claims chase
    list, it does not re-derive it.

Everything in this file is PURE (stdlib only) and proven DB-free in
backend/harness_discrepancy_appeals.py: the transition truth table, the patch builder (who/when
stamps), the period-range spelling expansion, and the summary bucketing. The two thin endpoints in
router.py (`GET /discrepancy-appeals`, `PATCH /discrepancy-appeals/{row_id}`) only run queries and
delegate every decision here.

STATES (NULL / '' = no appeal activity — the honest default on every engine-written row):
    ''            --file-->  appeal_filed        (an appeal has been submitted to the carrier/MA)
    ''            ---------> written_off         (management decides not to chase it)
    appeal_filed  --------->  appeal_won         (the carrier paid / credited after the appeal)
    appeal_filed  --------->  appeal_denied      (the carrier rejected the appeal)
    appeal_filed  --------->  written_off        (abandoned mid-appeal)
    appeal_denied --------->  appeal_filed       (re-appeal) | written_off
    written_off   --------->  appeal_filed       (reopened — write-off was premature)
    any state     --------->  ''                 (undo/clear — a mis-click must be reversible)

RULE TWO: no carrier/tenant names anywhere here; states are workflow words, not carrier words.
"""
import calendar as _calendar

# ── The state machine ─────────────────────────────────────────────────────────────────────────────
# '' is the canonical "no appeal state" spelling (stored as NULL). Order = display order.
APPEAL_STATES = ("appeal_filed", "appeal_won", "appeal_denied", "written_off")

ALLOWED_TRANSITIONS = {
    "": ("appeal_filed", "written_off"),
    "appeal_filed": ("appeal_won", "appeal_denied", "written_off", ""),
    "appeal_denied": ("appeal_filed", "written_off", ""),
    "appeal_won": ("",),
    "written_off": ("appeal_filed", ""),
}

MAX_NOTE_LEN = 2000


def normalize_state(value):
    """None/NULL/whitespace -> ''; known state -> itself; anything else raises ValueError."""
    s = str(value or "").strip().lower()
    if s in ("", "none", "null"):
        return ""
    if s not in APPEAL_STATES:
        raise ValueError(f"unknown appeal state {value!r} — one of {', '.join(APPEAL_STATES)} or empty to clear")
    return s


def validate_transition(current, new):
    """PURE. Returns (current, new) normalized, or raises ValueError with the human reason.
    A no-op (same state) is rejected too — the caller should not stamp who/when for nothing."""
    cur = normalize_state(current)
    nxt = normalize_state(new)
    if nxt == cur:
        raise ValueError(f"row is already in state {cur or '(no appeal)'!r}")
    if nxt not in ALLOWED_TRANSITIONS.get(cur, ()):
        cur_h = cur or "(no appeal)"
        nxt_h = nxt or "(no appeal)"
        raise ValueError(f"cannot go from {cur_h!r} to {nxt_h!r}")
    return cur, nxt


def apply_appeal(current, new, note, actor, now_iso):
    """PURE. Validate the transition and build the row PATCH dict (the only fields the appeal
    workflow may touch — never expected/received/gap/status). Clearing back to '' resets the
    appeal fields to NULL entirely (the row returns to pristine engine output)."""
    _, nxt = validate_transition(current, new)
    if nxt == "":
        return {"appeal_status": None, "appeal_note": None, "appealed_by": None, "appealed_at": None}
    note_s = str(note or "").strip()[:MAX_NOTE_LEN] or None
    actor_s = str(actor or "").strip()[:200] or "web"
    return {"appeal_status": nxt, "appeal_note": note_s,
            "appealed_by": actor_s, "appealed_at": str(now_iso)}


def allowed_next(current):
    """The states a row may move to from `current` (drives the hub's action buttons)."""
    return list(ALLOWED_TRANSITIONS.get(normalize_state(current), ()))


# ── Period-range expansion (spelling-agnostic, the _pvariants doctrine) ───────────────────────────
MAX_RANGE_MONTHS = 36


def parse_month(period):
    """'2026-04' | 'April 2026' -> (2026, 4). Raises ValueError on anything else (strict — never
    leniently mapped to January; same posture as router._pvariants)."""
    p = str(period or "").strip()
    if len(p) >= 7 and p[:4].isdigit() and p[4] == "-" and p[5:7].isdigit():
        yr, mo = int(p[:4]), int(p[5:7])
    else:
        parts = p.split()
        names = {m.lower(): i for i, m in enumerate(_calendar.month_name) if m}
        if len(parts) == 2 and parts[0].lower() in names and parts[1].isdigit():
            yr, mo = int(parts[1]), names[parts[0].lower()]
        else:
            raise ValueError(f"not a month period: {period!r} (use YYYY-MM)")
    if not (1 <= mo <= 12 and 2000 <= yr <= 2100):
        raise ValueError(f"month period out of range: {period!r}")
    return yr, mo


def month_spellings(yr, mo):
    """Both stored spellings of one month: ['2026-04', 'April 2026']."""
    return [f"{yr:04d}-{mo:02d}", f"{_calendar.month_name[mo]} {yr}"]


def period_range_variants(period_from, period_to):
    """PURE. Every stored spelling of every month in [period_from, period_to] inclusive — the
    `.in_('period', …)` list for a date-range query over the spelling-mixed period column.
    Missing side defaults to the other side (single-month). Raises ValueError on a reversed or
    over-long (> MAX_RANGE_MONTHS) range."""
    pf = str(period_from or "").strip() or str(period_to or "").strip()
    pt = str(period_to or "").strip() or pf
    if not pf:
        raise ValueError("period_from (YYYY-MM) is required")
    y0, m0 = parse_month(pf)
    y1, m1 = parse_month(pt)
    n = (y1 - y0) * 12 + (m1 - m0) + 1
    if n < 1:
        raise ValueError("period_from is after period_to")
    if n > MAX_RANGE_MONTHS:
        raise ValueError(f"period range too long (max {MAX_RANGE_MONTHS} months)")
    out = []
    for i in range(n):
        yy, mm = y0 + (m0 - 1 + i) // 12, (m0 - 1 + i) % 12 + 1
        out.extend(month_spellings(yy, mm))
    return out


# ── Summary bucketing (the hub's headline cards) ──────────────────────────────────────────────────
NO_RULE_TEXT = "no business rule configured"   # ma_recon's literal honest-absence marker (mig 312)


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def summarize_appeals(rows):
    """PURE. Bucket discrepancy rows for the hub's cards. Evidence-first: the 'no_rule' bucket
    counts rows whose engines explicitly said 'no business rule configured' (never inferred).
    Appeal buckets key on appeal_status ('' bucket = 'none'). gap totals are rounded once."""
    by_appeal = {}
    open_gap = 0.0
    open_count = 0
    no_rule_count = 0
    for r in rows:
        st = str(r.get("appeal_status") or "").strip() or "none"
        b = by_appeal.setdefault(st, {"count": 0, "gap": 0.0})
        b["count"] += 1
        b["gap"] += _f(r.get("gap"))
        if (r.get("status") or "") == "open":
            open_count += 1
            open_gap += _f(r.get("gap"))
        blob = f"{r.get('notes') or ''} {r.get('rule_reason') or ''}".lower()
        if NO_RULE_TEXT in blob:
            no_rule_count += 1
    for b in by_appeal.values():
        b["gap"] = round(b["gap"], 2)
    return {"by_appeal": by_appeal, "open_count": open_count,
            "open_gap": round(open_gap, 2), "no_rule_count": no_rule_count,
            "total_rows": len(rows)}
