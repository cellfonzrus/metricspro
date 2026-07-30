"""NOTIFY attention providers — report-delivery wiring that is silently dead.

Registered into the shared attention registry (`core.import_health.register_provider`) from this module's
own file — the registry exists precisely so a module contributes an item without touching core aggregation.
Imported for its side effect at the bottom of notify/router.py (which main.py already mounts), so no SHARED
file changes.

WHY THESE (owner ask 2026-07-26, dispatch item 5 — audit platform-core's own settings): notify is the module
whose failures are invisible by design. A scheduled report that targets an unconfigured channel, has no
recipient, or whose sweep is not firing produces NO error anywhere a human looks — the report simply never
arrives. That is exactly the class of "dead wiring" the attention popup is for. The idle-scheduler item in
particular is the failure mode that once looked like "cron isn't running" but was a NOTIFY_RUN_SECRET
mismatch (net.http_post is async, so the 403 was only visible in net._http_response).

CONTRACT: LIVE state only, org-scoped on the acting org, cheap (2 config reads; a tenant with no schedules
costs 1), exception-guarded, and every item clears as soon as its cause is fixed:
  notify_channel_missing  — clears when the channel's credentials are set, or the schedule stops using it.
  notify_no_recipients    — clears when a recipient / email / phone is attached (or the schedule is paused).
  notify_scheduler_idle   — clears the moment the sweep runs (run-due stamps next_run_at forward).
  notify_channel_failing  — LAST attempt per channel, not a rolling error count: clears on the next
                            successful send, so a fixed channel stops nagging immediately.
  notify_schedule_config  — the schedule's SAVED FILTERS can't build a report (pure, in-process
                            check — no DB, no report build): clears the moment the filter is fixed.
"""
import sys
from datetime import timedelta

from app.modules.core.import_health import register_provider, _item, _now, _parse_ts
from . import report_filters
from .channels import email_resend, whatsapp_meta

# Grace on top of a schedule's own next_run_at before we call the scheduler idle. The pg_cron sweep runs
# hourly, so a few hours of slack absorbs a deploy/restart without flapping. TENANT-TUNABLE (RULE TWO)
# via storeops.tenants.notify_policy.scheduler_grace_hours — read ONLY when a schedule already looks idle,
# so the common (healthy) path costs no extra round trip.
_SCHED_GRACE_HOURS_DEFAULT = 6.0
_CHANNEL_LABEL = {"email": "Email", "whatsapp": "WhatsApp"}


def _grace_hours(client, org_id):
    try:
        rows = (client.schema("storeops").table("tenants").select("notify_policy")
                .eq("org_id", org_id).limit(1).execute().data) or []
        raw = (rows[0].get("notify_policy") or {}).get("scheduler_grace_hours") if rows else None
        if raw is not None:
            return max(0.0, float(raw))
    except Exception:
        pass
    return _SCHED_GRACE_HOURS_DEFAULT


def _known_report_keys():
    """The live report-key list, WITHOUT importing report_registry here — that module pulls in the
    asset / commcalc / account routers, which this leaf provider has no business dragging in. The
    app always has it loaded (notify/router.py imports it before this module); when it genuinely is
    not loaded we simply skip the report-key check and validate filters only."""
    mod = sys.modules.get("app.modules.notify.report_registry")
    return set(getattr(mod, "REPORTS", None) or ()) or None


def _has_target(sub):
    return bool((sub.get("recipient_ids") or []) or (sub.get("ad_hoc_emails") or [])
                or (sub.get("ad_hoc_phones") or []))


@register_provider("notify_delivery", label="Report delivery (channels · schedules)",
                   group="config", cost="cheap")
def _p_notify_delivery(client, org_id, ctx):
    """Everything an admin can fix about report delivery, from the tenant's own notify config."""
    now = ctx.get("now") or _now()
    out = []
    try:
        subs = (client.schema("notify").table("subscriptions")
                .select("id,name,report_key,filters,channels,recipient_ids,ad_hoc_emails,ad_hoc_phones,"
                        "is_active,next_run_at,last_run_at")
                .eq("org_id", org_id).limit(500).execute().data) or []
    except Exception:
        subs = []          # notify tables absent (mig 010 un-run) → nothing to say
    active = [s for s in subs if s.get("is_active") is not False]

    # 1) a schedule targets a channel this deployment cannot send on → it fails silently, every time.
    configured = {"email": bool(email_resend.is_configured()), "whatsapp": bool(whatsapp_meta.is_configured())}
    broken = {}
    for s in active:
        for ch in (s.get("channels") or []):
            ch = (ch or "").strip().lower()
            if ch in configured and not configured[ch]:
                broken.setdefault(ch, []).append(s.get("name") or s.get("report_key") or "?")
    for ch, names in sorted(broken.items()):
        label = _CHANNEL_LABEL.get(ch, ch)
        out.append(_item("config", f"notify_channel_missing:{ch}", "error",
                         f"{label} delivery is not configured",
                         f"{len(names)} active report schedule(s) send by {label} "
                         f"(e.g. {', '.join(sorted(set(names))[:3])}), but this system has no working "
                         f"{label} sender set up — those deliveries fail silently and nobody receives the "
                         f"report. Either finish the {label} setup or switch those schedules to a channel "
                         f"that works.",
                         len(names), "/notify?tab=subs", "Review schedules"))

    # 2) an active schedule with no recipient at all: it "runs" and delivers to nobody.
    orphan = [s for s in active if not _has_target(s)]
    if orphan:
        out.append(_item("config", "notify_no_recipients", "warning",
                         "Scheduled reports with no recipients",
                         f"{len(orphan)} active schedule(s) have nobody to send to "
                         f"(e.g. {', '.join(sorted({(s.get('name') or s.get('report_key') or '?') for s in orphan})[:3])})"
                         f" — they run and deliver nothing. Add a recipient, or switch the schedule off.",
                         len(orphan), "/notify?tab=subs", "Fix recipients"))

    # 3) the sweep itself is not firing (this is the NOTIFY_RUN_SECRET / pg_cron class).
    # Only schedules whose send time has ALREADY passed can be idle, so a healthy tenant never pays for
    # the (optional) tenant grace lookup.
    stale = []
    for s in active:
        nxt = _parse_ts(s.get("next_run_at"))
        if nxt:
            behind = (now - nxt).total_seconds() / 3600.0
            if behind > 0:
                stale.append((s, behind))
    if stale:
        grace = _grace_hours(client, org_id)
        idle = [(s, h) for (s, h) in stale if h > grace]
        if idle:
            worst = max(h for _s, h in idle)
            out.append(_item("config", "notify_scheduler_idle", "error",
                             "Scheduled report deliveries are not running",
                             f"{len(idle)} schedule(s) are past their send time — the oldest by "
                             f"{worst:.0f}h (allowing {grace:g}h grace). The delivery sweep is not firing, "
                             f"so no scheduled report is going out. Send one on demand to confirm the "
                             f"report itself works, then have the scheduler checked.",
                             len(idle), "/notify?tab=subs", "Review schedules"))

    # 3b) the schedule's saved filters can't build a report — it is skipped every run and delivers
    # nothing. Pure + in-process (no DB, no report build), so this costs nothing on a healthy tenant.
    misconfig = []
    known = _known_report_keys()
    for s in active:
        try:
            report_filters.validate_filters(s.get("report_key"), s.get("filters") or {},
                                            known_keys=known)
        except report_filters.ReportConfigError as e:
            misconfig.append((s, str(e)))
        except Exception:
            pass                                  # never let a provider break the popup
    if misconfig:
        first = misconfig[0]
        out.append(_item("config", "notify_schedule_config", "error",
                         "A scheduled report can't run — check its filters",
                         f"{len(misconfig)} schedule(s) are skipped every run because their saved "
                         f"filters can't produce a report — e.g. "
                         f"\"{(first[0].get('name') or first[0].get('report_key') or '?')}\": {first[1]} "
                         f"Nothing is being delivered for them until the filter is fixed.",
                         len(misconfig), "/notify?tab=subs", "Fix schedule filters"))

    # 4) the most recent attempt on a channel FAILED (state, not history → clears on the next success).
    try:
        log = (client.schema("notify").table("send_log").select("channel,status,error,created_at")
               .eq("org_id", org_id).order("created_at", desc=True).limit(60).execute().data) or []
    except Exception:
        log = []
    newest, cutoff = {}, now - timedelta(days=30)
    for r in log:
        ch = (r.get("channel") or "").strip().lower()
        ts = _parse_ts(r.get("created_at"))
        if not ch or (ts and ts < cutoff):
            continue
        if ch not in newest:                       # rows arrive newest-first
            newest[ch] = r
    failing = {ch: r for ch, r in newest.items() if (r.get("status") or "") == "failed"}
    for ch, r in sorted(failing.items()):
        label = _CHANNEL_LABEL.get(ch, ch)
        why = (r.get("error") or "").strip()
        out.append(_item("config", f"notify_channel_failing:{ch}", "warning",
                         f"The last {label} delivery failed",
                         f"The most recent {label} send for this company failed"
                         + (f" ({why[:120]})" if why else "")
                         + ". Nothing has gone out successfully on that channel since. Check the delivery "
                           "log, fix the cause, then send once to confirm — this notice clears on the next "
                           "successful send.",
                         1, "/notify?tab=log", "Open delivery log"))
    return out
