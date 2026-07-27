"""PORTAL RATE-LIMIT DETECTION + COOLDOWN BACKOFF — mod-commission (migration 244).

OWNER REPORT 2026-07-27 (verbatim): "for vidapay it says you have too many requests, and have been
temporarily blocked, try again later".

WHY THIS EXISTS
    Nothing in the portal-pull stack recognised a rate-limit / temporary-block response. A blocked
    portal produced a GENERIC failure ("could not find the password field", "report not listed",
    "session expired"), the row was stamped with that misleading text, and **the very next trigger
    fired immediately** — the scheduled /run-due poll, the automatic post-login pull, or an operator
    clicking Live login again because the error looked like a calibration problem. Retrying into an
    active block is what turns a 30-minute throttle into a day-long ban.

WHAT THIS MODULE IS
    The shared cooldown layer for `commcalc.data_source` — every processor that pulls through a portal
    login (vidapay / total_access / b2bsoft, and any future one) gets the same protection, because the
    guard lives at the data_source layer rather than inside one scraper. Three parts:

      1. DETECTION   `detect_block()` — HTTP 429 (and 503-with-Retry-After), the `Retry-After` header
                     (delta-seconds AND HTTP-date), and configurable block-page TEXT markers.
      2. STATE       `blocked_until` / `block_reason` / `blocked_at` / `consecutive_failures` on the
                     data_source row, stamped with an ESCALATING backoff (default 30m → 2h → 8h cap).
      3. RESPECT     `read_state()` / `guard()` answer "is this login in cooldown right now?" for the
                     scheduler, the auto-pull and the UI.

RULE TWO (SAP-configurable). No marker phrase and no backoff step is a constant in a decision path:
    • markers  → `commcalc.portal_block_marker` (org-scoped rows override the house defaults; a row
      may be pinned to one processor). `DEFAULT_MARKERS` below is the SEED, used verbatim only when
      the table is absent (pre-migration) or empty.
    • ladder   → `commcalc.commission_org_config.portal_backoff_minutes` (CSV, e.g. "30,120,480"),
      falling back to DEFAULT_BACKOFF_MINUTES.
    • failure alert threshold → `commission_org_config.portal_block_alert_failures`.

RULE ONE (multi-tenant). Every read and every write here is `.eq("org_id", org_id)` with the org
passed in by the caller; the house org is used ONLY as the source of DEFAULT marker rows (the same
inheritance shape `report_pull_map` uses) and never as a data scope.

DEGRADES BOTH WAYS. Before migration 244 runs: `read_state()` sees no `blocked_until` key and reports
"not blocked", every write is a best-effort UPDATE whose failure is swallowed, and the marker/ladder
loaders fall back to the seeded defaults. The feature is INERT, not broken — no 500s, no behaviour
change. After it runs with no config rows, the seeded defaults apply.

NOT MONEY-TOUCHING. Nothing here reads or writes a rate, tier, plan rule, payout or any calculation
input; it only decides WHEN a portal may be contacted.
"""
from datetime import datetime, timedelta, timezone

# ── seeds (RULE TWO: these are DEFAULTS, overridable per tenant in config) ───────────────────────
# Phrases that mean "we are being throttled / temporarily blocked", matched case-insensitively as
# SUBSTRINGS of the visible page text. Deliberately narrow: a false positive parks a healthy login in
# a needless cooldown, so generic phrases ("try again later", "please wait") are seeded DISABLED in
# migration 244 for an operator to switch on if their portal needs them.
DEFAULT_MARKERS = (
    "too many requests",          # ← the T-CETRA/VidaPay phrasing the owner pasted
    "temporarily blocked",        # ← ditto
    "temporarily block",          # "we have temporarily blocked your access"
    "blocked temporarily",
    "rate limit",                 # covers "rate limited" / "rate limiting" by substring
    "too many attempts",
    "too many failed",
    "request throttled",
    "throttled",
    "exceeded the maximum number of requests",
    "unusual number of requests",
)

# HTTP statuses that mean rate-limited. 429 is unambiguous. 503 is counted ONLY when the response
# also carries a Retry-After header (a bare 503 is an outage, not a throttle).
BLOCK_STATUSES = (429,)
RETRY_AFTER_STATUSES = (429, 503)

# Escalating cooldown, in MINUTES, indexed by consecutive failure count. The last entry is the cap.
DEFAULT_BACKOFF_MINUTES = (30, 120, 480)
# A cooldown is never shorter than this even if a portal's Retry-After says otherwise going DOWN —
# see backoff_seconds(): Retry-After can only ever push the cooldown LATER, never earlier.
MIN_BACKOFF_SECONDS = 5 * 60
MAX_BACKOFF_SECONDS = 7 * 24 * 3600           # sanity cap on a hostile Retry-After
DEFAULT_ALERT_FAILURES = 4                    # consecutive non-delivering attempts → attention item

HOUSE_ORG = "00000000-0000-0000-0000-000000000001"


class PortalRateLimited(Exception):
    """The portal answered with a rate-limit / temporary-block. Carries the parsed Retry-After (seconds)
    when the portal supplied one, so the cooldown can honour it."""

    def __init__(self, message, retry_after_s=None, marker=None, status=None):
        super().__init__(message)
        self.retry_after_s = retry_after_s
        self.marker = marker
        self.status = status


# ════════════════════════════════════════════════════════════════════════════════════════════════
# 1) DETECTION  (PURE — no DB, no network, unit-testable)
# ════════════════════════════════════════════════════════════════════════════════════════════════
def parse_retry_after(value):
    """`Retry-After` → seconds from now, or None. Handles BOTH RFC-9110 forms: delta-seconds
    ("120") and an HTTP-date ("Wed, 21 Oct 2026 07:28:00 GMT"). A past date yields 0. PURE."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        n = int(float(s))
        return max(0, min(n, MAX_BACKOFF_SECONDS))
    except Exception:
        pass
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%A, %d-%b-%y %H:%M:%S %Z", "%a %b %d %H:%M:%S %Y"):
        try:
            d = datetime.strptime(s, fmt)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            delta = (d - datetime.now(timezone.utc)).total_seconds()
            return max(0, min(int(delta), MAX_BACKOFF_SECONDS))
        except Exception:
            continue
    return None


def _header(headers, name):
    """Case-insensitive header lookup over a dict-ish. Never raises."""
    if not headers:
        return None
    try:
        for k, v in dict(headers).items():
            if str(k).strip().lower() == name:
                return v
    except Exception:
        return None
    return None


def detect_block(text=None, status=None, headers=None, markers=None):
    """Is this outcome a rate-limit / temporary block? Returns a dict when it is, else None. PURE.

        {"reason": <one human line>, "marker": <what matched>, "status": <http status|None>,
         "retry_after_s": <int|None>}

    `text` is any page/status/error string (visible page text, the driver's status line, an exception
    message). `markers` overrides the seeded DEFAULT_MARKERS with the tenant's configured list."""
    marks = [str(m).strip().lower() for m in (markers if markers is not None else DEFAULT_MARKERS)
             if str(m or "").strip()]
    retry = parse_retry_after(_header(headers, "retry-after"))
    st = None
    try:
        st = int(status) if status is not None else None
    except Exception:
        st = None
    if st is not None and st in BLOCK_STATUSES:
        return {"reason": ("The portal answered HTTP %d (too many requests) — it is rate-limiting us."
                           % st),
                "marker": "http_%d" % st, "status": st, "retry_after_s": retry}
    if st is not None and st in RETRY_AFTER_STATUSES and retry is not None:
        return {"reason": ("The portal answered HTTP %d with Retry-After — it is asking us to back off."
                           % st),
                "marker": "http_%d_retry_after" % st, "status": st, "retry_after_s": retry}
    hay = (text or "")
    if hay:
        low = str(hay).lower()
        for m in marks:
            if m in low:
                return {"reason": ("The portal's page says “%s” — it has temporarily "
                                   "blocked or throttled us." % m),
                        "marker": m, "status": st, "retry_after_s": retry}
    return None


def evaluate_result(res, markers=None):
    """Detect a block in a PULL RESULT dict (the shape run_vidapay_sweep / _pull_all_reports_on_page /
    pull_b2bsoft_on_page return). Looks at the explicit block field the driver may set, then at the
    status/error/reason text and every per-report error. Returns the detect_block() dict or None. PURE."""
    if not isinstance(res, dict):
        return None
    blk = res.get("rate_limited") or res.get("blocked")
    if isinstance(blk, dict) and blk.get("reason"):
        return {"reason": str(blk.get("reason"))[:400], "marker": blk.get("marker"),
                "status": blk.get("status"), "retry_after_s": blk.get("retry_after_s")}
    parts = [res.get("status"), res.get("error"), res.get("reason")]
    for r in (res.get("reports") or []):
        if isinstance(r, dict):
            parts.extend([r.get("error"), r.get("reason")])
    hit = detect_block(" \n ".join([str(p) for p in parts if p]), markers=markers)
    if hit:
        return hit
    return None


def is_block_error(exc, markers=None):
    """Detect a block in an EXCEPTION (a PortalRateLimited, or any driver error whose message carries
    the portal's block text). Returns the detect_block() dict or None. PURE."""
    if exc is None:
        return None
    if isinstance(exc, PortalRateLimited):
        return {"reason": str(exc)[:400], "marker": getattr(exc, "marker", None),
                "status": getattr(exc, "status", None),
                "retry_after_s": getattr(exc, "retry_after_s", None)}
    return detect_block(str(exc), markers=markers)


# ════════════════════════════════════════════════════════════════════════════════════════════════
# 2) BACKOFF MATH  (PURE)
# ════════════════════════════════════════════════════════════════════════════════════════════════
def _ladder(ladder=None):
    steps = []
    for v in (ladder if ladder is not None else DEFAULT_BACKOFF_MINUTES):
        try:
            m = float(v)
        except Exception:
            continue
        if m > 0:
            steps.append(m)
    return steps or list(DEFAULT_BACKOFF_MINUTES)


def backoff_seconds(consecutive_failures, retry_after_s=None, ladder=None):
    """How long to stay off this portal, in seconds. PURE.

    The ladder is indexed by the failure count SO FAR (0 → first step) and its last entry is the cap,
    so 30m → 2h → 8h → 8h → … A portal-supplied Retry-After can only push the cooldown LATER
    (`max(ladder, retry_after)`), never earlier: a throttling portal that answers "Retry-After: 5" must
    not be able to talk us into hammering it again five seconds later."""
    steps = _ladder(ladder)
    try:
        n = max(0, int(consecutive_failures or 0))
    except Exception:
        n = 0
    mins = steps[min(n, len(steps) - 1)]
    secs = int(mins * 60)
    try:
        ra = int(retry_after_s) if retry_after_s is not None else None
    except Exception:
        ra = None
    if ra is not None:
        secs = max(secs, ra)
    return max(MIN_BACKOFF_SECONDS, min(secs, MAX_BACKOFF_SECONDS))


def _ts(v):
    """ISO/timestamptz → aware datetime, or None. PURE."""
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def read_state(row, now=None):
    """Cooldown state of ONE data_source row. PURE, and the pre-migration-244 degrade point: a row with
    no `blocked_until` key simply reports blocked=False, so every caller behaves exactly as before.

        {"blocked": bool, "blocked_until": datetime|None, "remaining_s": int,
         "reason": str|None, "consecutive_failures": int}
    """
    r = row if isinstance(row, dict) else {}
    now = now or datetime.now(timezone.utc)
    until = _ts(r.get("blocked_until"))
    try:
        fails = int(r.get("consecutive_failures") or 0)
    except Exception:
        fails = 0
    remaining = int((until - now).total_seconds()) if until else 0
    return {"blocked": bool(until and remaining > 0),
            "blocked_until": until, "remaining_s": max(0, remaining),
            "reason": (r.get("block_reason") or None), "consecutive_failures": fails}


def humanize(state, tz=None):
    """One operator-facing line for a cooldown state. PURE."""
    if not state or not state.get("blocked"):
        return ""
    until = state.get("blocked_until")
    when = ""
    if until:
        try:
            when = until.astimezone(tz).strftime("%-I:%M %p") if tz else until.strftime("%H:%M UTC")
        except Exception:
            when = str(until)[:16]
    mins = int(round((state.get("remaining_s") or 0) / 60.0))
    return ("⛔ The portal has temporarily blocked us. Next automatic attempt %s (in ~%d min). %s"
            % (when or "after the cooldown", mins, (state.get("reason") or "")))[:400]


def confirm_warning(state, tz=None):
    """The text the UI must show before allowing a HUMAN retry during a cooldown. PURE."""
    until = state.get("blocked_until") if state else None
    when = ""
    if until:
        try:
            when = until.astimezone(tz).strftime("%-I:%M %p") if tz else until.strftime("%H:%M UTC")
        except Exception:
            when = str(until)[:16]
    return ("The portal rate-limited us until ~%s. Another attempt now may EXTEND the block. "
            "Continue anyway?" % (when or "the cooldown ends"))


# ════════════════════════════════════════════════════════════════════════════════════════════════
# 3) CONFIG LOADERS  (best-effort; org-scoped; fall back to the seeds)
# ════════════════════════════════════════════════════════════════════════════════════════════════
def load_markers(client, org_id, processor=None):
    """The tenant's enabled block-page markers. This org's rows win; otherwise the house/default rows;
    otherwise the seeded DEFAULT_MARKERS. A row with a `processor` applies only to that processor.
    Never raises — a missing table (pre-mig-244) returns the seeds."""
    try:
        rows = (client.schema("commcalc").table("portal_block_marker")
                .select("org_id,processor,marker,enabled")
                .in_("org_id", [str(org_id), HOUSE_ORG]).limit(400).execute().data) or []
    except Exception:
        return list(DEFAULT_MARKERS)
    proc = (processor or "").strip().lower()
    mine, house = [], []
    for r in rows:
        if r.get("enabled") is False:
            continue
        rp = (r.get("processor") or "").strip().lower()
        if rp and proc and rp != proc:
            continue
        if rp and not proc:
            continue
        m = str(r.get("marker") or "").strip().lower()
        if not m:
            continue
        (mine if str(r.get("org_id")) == str(org_id) else house).append(m)
    out = mine or house
    return list(dict.fromkeys(out)) or list(DEFAULT_MARKERS)


def _org_cfg(client, org_id):
    try:
        rows = (client.schema("commcalc").table("commission_org_config").select("*")
                .eq("org_id", org_id).limit(1).execute().data) or []
        return rows[0] if rows else {}
    except Exception:
        return {}


def load_ladder(client, org_id):
    """The tenant's escalating cooldown ladder, in minutes. `commission_org_config
    .portal_backoff_minutes` is a CSV ("30,120,480"); anything unparseable falls back to the default."""
    raw = (_org_cfg(client, org_id) or {}).get("portal_backoff_minutes")
    if raw in (None, ""):
        return list(DEFAULT_BACKOFF_MINUTES)
    if isinstance(raw, (list, tuple)):
        vals = list(raw)
    else:
        vals = [p for p in str(raw).replace(";", ",").split(",")]
    return _ladder(vals)


def load_alert_failures(client, org_id):
    """How many consecutive non-delivering attempts before the admin attention popup calls it out."""
    raw = (_org_cfg(client, org_id) or {}).get("portal_block_alert_failures")
    try:
        n = int(raw)
        return n if n > 0 else DEFAULT_ALERT_FAILURES
    except Exception:
        return DEFAULT_ALERT_FAILURES


# ════════════════════════════════════════════════════════════════════════════════════════════════
# 4) STATE WRITES  (self-contained best-effort UPDATEs — org-scoped, never raise)
# ════════════════════════════════════════════════════════════════════════════════════════════════
# A SEPARATE update path from router._status_update on purpose: that helper marks EVERY optional
# column in a failing update as missing-for-this-process, so folding a pre-migration-244 column into
# it would also disable the already-shipped last_attempt_at write (the same trap _store_pull_diag
# documents). Here a missing column simply means the cooldown is not persisted this deploy.
def _write(client, sid, org_id, patch):
    try:
        (client.schema("commcalc").table("data_source").update(patch)
         .eq("id", sid).eq("org_id", org_id).execute())
        return True
    except Exception as e:
        print(f"WARN portal cooldown not stored (run mig 244?): {e}")
        return False


def _fetch_row(client, sid, org_id):
    try:
        rows = (client.schema("commcalc").table("data_source")
                .select("id,processor,blocked_until,block_reason,consecutive_failures")
                .eq("id", sid).eq("org_id", org_id).limit(1).execute().data) or []
        return rows[0] if rows else {}
    except Exception:
        return {}


def plan_block(row, hit, *, now=None, ladder=None):
    """The cooldown a detected block produces, WITHOUT writing anything. PURE — this is what the proof
    harness asserts the escalation schedule against.

        {"blocked_until": iso, "block_reason": str, "consecutive_failures": int, "seconds": int}
    """
    now = now or datetime.now(timezone.utc)
    st = read_state(row, now=now)
    fails = st["consecutive_failures"]
    secs = backoff_seconds(fails, retry_after_s=(hit or {}).get("retry_after_s"), ladder=ladder)
    until = now + timedelta(seconds=secs)
    reason = str((hit or {}).get("reason") or "The portal temporarily blocked us.")[:400]
    return {"blocked_until": until.isoformat(), "block_reason": reason,
            "consecutive_failures": fails + 1, "seconds": secs,
            "marker": (hit or {}).get("marker"), "blocked_at": now.isoformat()}


def apply_block(client, sid, org_id, hit, *, row=None, now=None, ladder=None):
    """Stamp an escalating cooldown on this login after a detected block. Returns the plan (even when
    the write fails / the columns don't exist yet). org-scoped; never raises."""
    if not hit:
        return None
    cur = row if isinstance(row, dict) and "consecutive_failures" in row else _fetch_row(client, sid, org_id)
    if ladder is None:
        ladder = load_ladder(client, org_id)
    plan = plan_block(cur, hit, now=now, ladder=ladder)
    _write(client, sid, org_id,
           {"blocked_until": plan["blocked_until"], "block_reason": plan["block_reason"],
            "blocked_at": plan["blocked_at"], "consecutive_failures": plan["consecutive_failures"]})
    return plan


def note_failure(client, sid, org_id, *, row=None):
    """A non-delivering attempt that was NOT a detected block: count it (so the attention provider can
    escalate on N consecutive failures) but do NOT start a cooldown. org-scoped; never raises."""
    cur = row if isinstance(row, dict) and "consecutive_failures" in row else _fetch_row(client, sid, org_id)
    try:
        n = int((cur or {}).get("consecutive_failures") or 0)
    except Exception:
        n = 0
    _write(client, sid, org_id, {"consecutive_failures": n + 1})
    return n + 1


def clear_block(client, sid, org_id):
    """A pull DELIVERED rows: the portal is talking to us again. Reset the counter and lift any
    cooldown. org-scoped; never raises."""
    _write(client, sid, org_id,
           {"blocked_until": None, "blocked_at": None, "block_reason": None,
            "consecutive_failures": 0})


def record_outcome(client, sid, org_id, res, *, delivered, exc=None, markers=None, row=None):
    """ONE place the pull/login outcome updates the cooldown state, so ▶ Pull now, the scheduled pull,
    the automatic post-login pull and the live-login error path all behave identically:

        delivered            → clear_block()  (recovery resets consecutive_failures)
        block detected       → apply_block()  (escalating; honours Retry-After)
        otherwise (failure)  → note_failure() (counts, no cooldown)

    Returns the cooldown plan when one was stamped, else None. Never raises."""
    try:
        if delivered:
            clear_block(client, sid, org_id)
            return None
        if markers is None:
            markers = load_markers(client, org_id, (row or {}).get("processor")
                                   if isinstance(row, dict) else None)
        hit = is_block_error(exc, markers=markers) if exc is not None else None
        if hit is None:
            hit = evaluate_result(res, markers=markers)
        if hit:
            return apply_block(client, sid, org_id, hit, row=row)
        note_failure(client, sid, org_id, row=row)
        return None
    except Exception as e:                                   # never break a pull on bookkeeping
        print(f"WARN portal cooldown bookkeeping skipped: {e}")
        return None


def guard(client, sid, org_id, *, row=None, now=None):
    """"May we contact this portal right now?" — the single question the scheduler, the auto-pull and
    the UI ask. Returns read_state(); pre-migration-244 it always reports blocked=False."""
    cur = row if isinstance(row, dict) and ("blocked_until" in row or "consecutive_failures" in row) \
        else _fetch_row(client, sid, org_id)
    return read_state(cur, now=now)
