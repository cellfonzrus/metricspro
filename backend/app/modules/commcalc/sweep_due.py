"""SWEEP DUE-NESS — the ONE home for "should this sweep run now, and why" (index §COMMCALC-45).

Owner, 2026-10-29: *"I should not be running the sweep it should be automatic like the rebuild or the
commission calculation - whenever anything changes it should automatically do the sweep as required."*

THE CLASS, NOT THE INSTANCE. `auto_calc` (§6l) made the commission calculation live, and
`derived_builds` (§COMMCALC-41) made every other stored snapshot live, both on the same rule: a
derived thing's freshness is ITS OWN FACT, and a tenant cannot be the scheduler for it. A sweep is
the one thing that rule had not reached, because a sweep's due-ness was a STORED CLOCK TIME and
nothing else:

    connector_due = not nra or nra <= now_iso          # the shipped rule, in eight places

Three things follow from that, each measured on the house org:

  · **A PARTIAL run satisfied the schedule.** `next_run_at` is advanced before dispatch, so a run that
    came back with a column missing waited a full day exactly like a clean one. From 2026-10-16 to
    2026-10-28 the DLAR sweep ran thirteen times, read `⚠ PARTIAL — COLUMN STOPPED ARRIVING` every
    time, and changed nothing about when it would next try.
  · **Changing the declaration did not make it due.** The config PUT re-derives `next_run_at` from the
    calendar, which pushes it INTO THE FUTURE. So correcting a field mapping, a credential or the
    report list left the sweep sitting on a result computed under the old declaration until tomorrow's
    slot — the one moment a re-run is most obviously owed.
  · **The decision was copied, not dereferenced.** Eight places answered it: `/connectors/run-due`,
    four per-vendor `run-due`s, the per-report slot loop, and two more. A `.lte('next_run_at', now)`
    filter in SQL *cannot* express "due because the declaration changed", so the copies were not just
    duplication — they were a ceiling on what due-ness could ever mean.

THE ONE RULE. `verdict(cfg, now)` is PURE and answers from STATE: the config row the dispatcher
already reads. Nothing here contacts a portal, reads a second table or knows a vendor's name.

WHY `last_attempt_at` AND NOT `last_run_at` IS THE GUARD. The house already separates the two
(`_sweep_set_status(..., mark_run=, success=)`): `last_run_at` advances only for a run that IMPORTED,
`last_attempt_at` for every attempt including a refusal. Every reason below is therefore gated on the
ATTEMPT. Gating on the run would let a sweep that fails to log in become due again immediately,
forever, and hammer the portal into the cooldown `portal_backoff` exists to avoid — a sweep that
cannot get in is exactly the sweep that must not be retried in a tight loop.

RULE TWO. No vendor, carrier or tenant name appears here, and no behaviour branches on one. The two
intervals are PLATFORM behaviour, not a tenant's: they bound how often this software may knock on
somebody else's door. They are declared once, below, with their reason, and `retry_gap_s` derives the
retry from the tenant's OWN declared cadence so a slower feed is retried more slowly without a knob.

Lock: `backend/harness_sweep_due.py`. Stdlib only, no imports from the app at module scope.
"""
from datetime import datetime, timedelta, timezone

#: THE FLOOR ON CONTACT. No reason in this module can make a sweep due within this long of its last
#: ATTEMPT. It is what makes the new reasons safe: however many facts change at once, the portal is
#: knocked on at most once per gap. A human pressing "Run sweep now" is NOT governed by this — a
#: person asking is not the automatic schedule, and that endpoint is untouched.
MIN_ATTEMPT_GAP_S = 15 * 60

#: A retry is a FRACTION OF THE TENANT'S OWN CADENCE, not a fixed number: an hourly feed that came
#: back short should be re-tried in minutes, a monthly one in hours. Quarter-cadence means a daily
#: sweep that keeps reading PARTIAL tries four times a day instead of once, which is enough to catch
#: a portal that publishes late and far short of anything a portal would call abuse.
RETRY_FRACTION = 0.25
RETRY_GAP_MAX_S = 6 * 60 * 60

#: Cadence in seconds, by the `frequency` the tenant declared. Unknown spellings fall to weekly, the
#: same default `_vip_next_run` already takes, so an unrecognised value is never read as "very often".
CADENCE_S = {"hourly": 3600, "daily": 86400, "weekly": 7 * 86400, "monthly": 30 * 86400}
CADENCE_DEFAULT_S = CADENCE_S["weekly"]

#: A run whose status is one of these left the feed whole, so only the clock makes it due again.
#: Everything else — 'partial', 'error', a word this module has never heard of — is an INCOMPLETE run
#: and earns a retry. An allowlist, deliberately: an unruled status reads as incomplete, which costs
#: one extra attempt, where a denylist would read a new failure word as success and cost the feed.
#: `component_basis_is_evidence` (§COMMCALC-66) took the same shape for the same reason.
OK_STATUSES = ("ok", "success", "idle")

#: Every reason this module can give, so a caller can log or display one without inventing wording.
#: `due` is True for the first four.
REASON_NEVER_RUN = "never_run"
REASON_CONFIG_CHANGED = "config_changed"
REASON_RETRY_INCOMPLETE = "retry_incomplete"
REASON_CLOCK = "clock"
REASON_DISABLED = "disabled"
REASON_TOO_SOON = "too_soon"
REASON_WAITING = "waiting"

DUE_REASONS = (REASON_NEVER_RUN, REASON_CONFIG_CHANGED, REASON_RETRY_INCOMPLETE, REASON_CLOCK)
NOT_DUE_REASONS = (REASON_DISABLED, REASON_TOO_SOON, REASON_WAITING)


def _ts(v):
    """ISO / timestamptz / datetime → aware datetime, or None. PURE.

    Same tolerance as `portal_backoff._ts`, which this mirrors deliberately: a status column written
    by Postgres, by PostgREST or by our own `isoformat()` must all read the same."""
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def cadence_s(cfg):
    """How long this tenant's declared cadence is, in seconds. PURE."""
    freq = str(((cfg or {}).get("frequency") or "")).strip().lower()
    return CADENCE_S.get(freq, CADENCE_DEFAULT_S)


def retry_gap_s(cfg):
    """How long after a failed or partial ATTEMPT this sweep may be tried again. PURE.

    Never below `MIN_ATTEMPT_GAP_S` (the floor on contact) and never above `RETRY_GAP_MAX_S` (so a
    monthly feed that came back short is still re-tried the same day, not next month)."""
    gap = int(cadence_s(cfg) * RETRY_FRACTION)
    return max(MIN_ATTEMPT_GAP_S, min(RETRY_GAP_MAX_S, gap))


def status_is_complete(status):
    """Did the last run leave the feed WHOLE? PURE.

    Not the same question as `_sweep_set_status`'s `success`, which asks whether the run IMPORTED and
    counts 'partial' as yes — correctly, because a partial landed rows and the freshness trail must
    see them. Wholeness is the other axis: a partial imported AND left something out, which is why it
    is the one status that is success for the trail and incomplete for the schedule. An unruled word reads as INCOMPLETE (see
    `OK_STATUSES`). 'running' is incomplete on purpose: a run that never wrote a terminal status is a
    run that died, and the floor on contact is what stops a dead run being retried in a loop."""
    return str(status or "").strip().lower() in OK_STATUSES


def verdict(cfg, now=None):
    """Should this sweep run now, and why. PURE.

        {"due": bool, "reason": str, "detail": str, "retry_gap_s": int}

    `cfg` is the `*_sweep_config` row the dispatcher already reads. Absent keys are absent facts, never
    defaults: a row with no `last_attempt_at` has never been attempted, and a row with no `updated_at`
    simply cannot report a declaration change — it does not report a false one."""
    cfg = cfg if isinstance(cfg, dict) else {}
    now = _ts(now) or datetime.now(timezone.utc)

    if not cfg.get("enabled"):
        return {"due": False, "reason": REASON_DISABLED, "retry_gap_s": retry_gap_s(cfg),
                "detail": "this sweep is switched off"}

    # THE LATEST CONTACT IS THE LATER OF THE TWO STAMPS, NEVER ONE WITH THE OTHER AS A FALLBACK.
    # `_sweep_set_status` writes exactly ONE of them per run: `last_run_at` when the run imported
    # (which INCLUDES 'partial' — a partial landed rows), `last_attempt_at` when it did not. So a
    # connector that failed in August and has run partially every day since carries a FRESH
    # `last_run_at` beside a two-month-old `last_attempt_at`. Reading the attempt column first and
    # falling back would have taken the August date as "when we last knocked" and let the floor on
    # contact pass every single tick — the floor would have existed and protected nothing.
    run = _ts(cfg.get("last_run_at"))
    attempt = max([t for t in (_ts(cfg.get("last_attempt_at")), run) if t is not None],
                  default=None)
    nxt = _ts(cfg.get("next_run_at"))
    updated = _ts(cfg.get("updated_at"))
    gap = retry_gap_s(cfg)

    # NEVER ATTEMPTED — due at once, and ahead of the floor: there is no previous contact to protect.
    if attempt is None:
        return {"due": True, "reason": REASON_NEVER_RUN, "retry_gap_s": gap,
                "detail": "this sweep has never run"}

    # THE FLOOR ON CONTACT, before every reason below it. Whatever changed, the portal is knocked on
    # at most once per `MIN_ATTEMPT_GAP_S`. The clock is NOT exempt: two ticks of the cron inside the
    # gap used to produce two runs, which is the same abuse arriving by a different door.
    since_attempt = (now - attempt).total_seconds()
    if since_attempt < MIN_ATTEMPT_GAP_S:
        return {"due": False, "reason": REASON_TOO_SOON, "retry_gap_s": gap,
                "detail": "attempted %d min ago; the floor between attempts is %d min"
                          % (since_attempt // 60, MIN_ATTEMPT_GAP_S // 60)}

    # THE DECLARATION CHANGED SINCE WE LAST TRIED. The previous result was computed under the OLD
    # declaration — the old field mapping, the old credentials, the old report list — so it is not an
    # answer to the question now being asked. This is the owner's "whenever anything changes".
    if updated is not None and updated > attempt:
        return {"due": True, "reason": REASON_CONFIG_CHANGED, "retry_gap_s": gap,
                "detail": "the sweep's own settings changed after the last attempt, so the last "
                          "result was produced under the old settings"}

    # THE LAST RUN DID NOT LEAVE THE FEED WHOLE. 'partial' is the house's own word for it
    # (§COMMCALC-40), and until now it bought nothing: a partial waited for tomorrow like a clean run.
    if not status_is_complete(cfg.get("last_status")) and since_attempt >= gap:
        return {"due": True, "reason": REASON_RETRY_INCOMPLETE, "retry_gap_s": gap,
                "detail": "the last run ended '%s' and %d min have passed (retry gap %d min)"
                          % (str(cfg.get("last_status") or "unknown")[:40],
                             since_attempt // 60, gap // 60)}

    # THE CLOCK — the shipped behaviour, unchanged and still the common case.
    if nxt is None or nxt <= now:
        return {"due": True, "reason": REASON_CLOCK, "retry_gap_s": gap,
                "detail": "the scheduled time has passed"}

    waiting = int((nxt - now).total_seconds() // 60)
    return {"due": False, "reason": REASON_WAITING, "retry_gap_s": gap,
            "detail": "next scheduled run in %d min%s" % (
                waiting, "" if run else " (nothing imported yet)")}


def is_due(cfg, now=None):
    """`verdict(...)["due"]`, for a caller that wants only the boolean. PURE."""
    return bool(verdict(cfg, now).get("due"))


#: The columns `verdict` reads. A caller selecting a narrower set would silently lose a reason — the
#: `.select('enabled,next_run_at,...')` in the dispatcher is exactly how the old rule stayed a clock —
#: so the one home names them and the lock requires every caller to select at least these.
READS = ("enabled", "next_run_at", "last_run_at", "last_attempt_at", "last_status", "updated_at",
         "frequency")
