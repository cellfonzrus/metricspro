"""SUPER-ADMIN CONTROL BOX — the pure evaluator behind the platform's red/green board.

OWNER DIRECTIVE 2026-09-05 (sanjot@): "a separate agent is needed to work on the super admin side
control box to monitor the functions of all aspects of the platform, showing red light or green light
of the system and a daily check required to make sure the system is working, the control box will have
a link to those module and a way to fix that problem connected with Claude code so that can be fixed,
must protected from third party misuse of the ai api and only restricted to this module".

WHAT THIS MODULE IS. One screen answers "is the platform working?" — but that screen must not be a
SECOND opinion about subsystem health, because two answers to the same question drift and the platform
has already paid for that (CLAUDE.md duplicate-check build gate, owner 2026-09-02). So the control box
DERIVES nothing about a subsystem's condition. It COMPOSES the checks the platform already runs:

  · `core.import_health.collect_attention` — the ~40 registered attention PROVIDERS (imports, mapping,
    duplicates, config gaps, system errors, closing, CRM, HR, finance, asset, storeops, helpdesk,
    notify, storevisit). Each provider becomes ONE lamp. A module that registers a new provider gets a
    new lamp with no code change here — the registry is the registry.
  · `commcalc.portal_session_health.summarize` — durable merchant-portal sessions. Its severity ladder
    is THE ladder for portal sessions; this module does not re-rank those states, it MAPS them
    (`LAMP_FROM_PORTAL_STATE`) and the harness proves every one of `psh.STATES` is covered, so the two
    can never drift apart.
  · `core.import_health.feed_health` — feed freshness (already the authority on overdue/never).

WHAT IT ADDS, because nothing else answered it:
  · SCHEDULER LIVENESS. The platform's automation is a set of self-registering pg_cron jobs (migs
    922 / 940 / 950 / 956). Nothing ever told anyone one had STOPPED. `heartbeat_lamp` turns "when did
    this job last succeed" into a lamp.
  · THE DAILY CHECK ITSELF is monitored the same way (`selfcheck` row): the mig-950 lesson — "an
    automation whose repair step is a human click defeats the automation" — applies hardest to the
    watchman. A control box whose own daily run silently stopped is worse than no control box.

HONESTY IS THE POINT (owner: a report that prints a quiet 0 instead of admitting its definition matched
nothing is a defect). Three rules are enforced here rather than left to the caller:
  1. An unrecognised probe kind, a probe that raised, or missing evidence resolves to `unknown` —
     NEVER `green`. Being blind is a state you can see, not a pass.
  2. A subsystem with no check, or whose check is disabled, resolves to `unmonitored` and is reported
     in `coverage`, never folded into a green headline.
  3. `roll_up` of ZERO monitored checks is `unknown`, not `green`. An empty board is not a healthy one.

THE LAMP LADDER (worst last) is deliberately NOT the portal ladder: that alphabet describes one
session's condition (`needs_login`, `never_linked`, …) and prescribes a remedy; this one describes any
subsystem's condition to an operator. `unknown` outranks `amber` because not knowing whether payroll
computed is more urgent than knowing one feed is late; `red` outranks both because it is a confirmed
outage. `unmonitored` sits just above green so a single-check roll-up reads honestly, but `roll_up`
excludes it from the headline and reports it as coverage instead (see rule 2).

PURE: stdlib only (datetime, re). No DB, no network, no FastAPI, no pandas. Every branch is proven by
`backend/harness_control_box.py`, which runs in the bare container.
"""
import re
from datetime import datetime, timedelta, timezone

# ── The lamp alphabet ────────────────────────────────────────────────────────────────────────────
# Ordered best → worst. `worst_lamp` and every escalation comparison use this index, never a string
# compare. See the module docstring for why this is not the portal ladder.
LAMPS = ("green", "unmonitored", "amber", "unknown", "red")
_RANK = {l: i for i, l in enumerate(LAMPS)}

# Lamps that mean "a human has something to do here". `unmonitored` is a COVERAGE gap (fixed by
# registering a check), not an incident, so it is not actionable in the incident sense.
ACTIONABLE = ("amber", "unknown", "red")

# ADAPTER, not a second ladder: commcalc.portal_session_health owns what a portal session's state
# means; this only says which lamp each of ITS states lights. harness_control_box.py asserts this dict
# covers `psh.STATES` exactly — add a state there and the harness fails until it is mapped here.
LAMP_FROM_PORTAL_STATE = {
    "healthy": "green",
    "expiring_soon": "amber",
    "error": "amber",          # the session is fine; the last pull failed for another reason
    "expired": "red",          # tonight's pull WILL need a human
    "needs_login": "red",      # the portal actively rejected us — a working connector just broke
    "never_linked": "red",     # configured but never signed in: this source has produced nothing, ever
}

# Attention-item severity → lamp. Any item at all means not-green: an item only exists while the
# condition is live (the provider contract — "a notification MUST clear when the check says everything
# is OK", owner 2026-07-26), so its presence is the signal.
LAMP_FROM_SEVERITY = {"error": "red", "warning": "amber", "info": "amber"}

# The generic probe KINDS. RULE TWO: registering a new subsystem is an INSERT into core.system_check,
# not a new branch here — a kind describes a SHAPE of evidence, never a tenant, carrier or module.
CHECK_KINDS = ("attention_provider", "portal_sessions", "heartbeat", "counter", "boolean", "unmonitored")

DEFAULT_CADENCE_HOURS = 24.0     # "a daily check required" (owner 2026-09-05)
DEFAULT_GRACE_HOURS = 6.0        # late is amber; properly overdue is red


# ── time helpers (same tolerant parsing discipline as portal_session_health._dt) ──────────────────
def _dt(v):
    """Parse a timestamp (ISO string or datetime) to an aware UTC datetime, or None. Never raises."""
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v).strip().replace("Z", "+00:00")
    for candidate in (s, s.split(".")[0]):
        try:
            d = datetime.fromisoformat(candidate)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _now_utc(now=None):
    return _dt(now) or datetime.now(timezone.utc)


def _num(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _hours(a, b):
    return (a - b).total_seconds() / 3600.0


def humanize_hours(h):
    if h is None:
        return "an unknown time"
    h = abs(float(h))
    if h < 1:
        return "%d minutes" % max(1, int(round(h * 60)))
    if h < 48:
        return "%d hours" % int(round(h))
    return "%d days" % int(round(h / 24.0))


# ── lamp algebra ─────────────────────────────────────────────────────────────────────────────────
def worst_lamp(*lamps):
    """The most severe lamp among several. An UNRECOGNISED lamp is treated as `unknown`, never
    silently dropped to green — a typo upstream must not manufacture a passing board."""
    seen = [(l if l in _RANK else "unknown") for l in lamps if l is not None]
    return max(seen, key=lambda l: _RANK[l]) if seen else "green"


def is_worse(lamp, than):
    """True when `lamp` is strictly more severe than `than` (both coerced to a known lamp)."""
    a = lamp if lamp in _RANK else "unknown"
    b = than if than in _RANK else "unknown"
    return _RANK[a] > _RANK[b]


# ── per-kind evaluation ──────────────────────────────────────────────────────────────────────────
def _result(spec, lamp, headline, detail, *, count=0, evidence=None, measured_at=None):
    """One board row. `deep_link` is the link-to-the-module the owner asked for; `fix_hint` and the
    index/file references travel with the row so the fix bundle needs no second lookup."""
    return {
        "key": spec.get("key"),
        "subsystem": spec.get("subsystem") or "other",
        "label": spec.get("label") or spec.get("key"),
        "kind": spec.get("kind"),
        "lamp": lamp,
        "headline": headline,
        "detail": detail,
        "count": int(count or 0),
        "monitored": lamp != "unmonitored",
        "actionable": lamp in ACTIONABLE,
        "deep_link": spec.get("deep_link"),
        "deep_link_label": spec.get("deep_link_label"),
        "index_ref": spec.get("index_ref"),
        "code_refs": list(spec.get("code_refs") or []),
        "owner_agent": spec.get("owner_agent"),
        "evidence": evidence or {},
        "measured_at": (_now_utc(measured_at)).isoformat(),
    }


def _eval_attention(spec, ev, now):
    """A registered attention PROVIDER's live items become one lamp. Zero items = green, because the
    provider contract guarantees an item exists only while its condition is live."""
    if ev.get("provider_error"):
        return _result(spec, "unknown", "The check itself failed to run.",
                       "This subsystem's attention provider raised, so its condition is UNKNOWN — not "
                       "confirmed healthy. " + redact(str(ev.get("provider_error"))[:240]),
                       evidence={"provider_error": redact(str(ev.get("provider_error"))[:240])})
    items = ev.get("items")
    if items is None:
        return _result(spec, "unknown", "No result from the attention registry.",
                       "The provider produced neither items nor an error, so nothing can be claimed "
                       "about this subsystem.")
    if not items:
        return _result(spec, "green", "No open items.",
                       "The provider that watches this subsystem is reporting nothing to fix.")
    lamp = worst_lamp(*[LAMP_FROM_SEVERITY.get(str(i.get("severity") or "").lower(), "amber")
                        for i in items])
    top = items[0] or {}
    total = sum(int(i.get("count") or 0) for i in items) or len(items)
    return _result(spec, lamp, str(top.get("label") or "Needs attention"),
                   redact(str(top.get("detail") or ""))[:400], count=total,
                   evidence={"items": [{"key": i.get("key"), "severity": i.get("severity"),
                                        "label": i.get("label"), "count": i.get("count"),
                                        "detail": redact(str(i.get("detail") or ""))[:300]}
                                       for i in items[:8]],
                             "item_total": len(items)})


def _eval_portal_sessions(spec, ev, now):
    """Delegates entirely to portal_session_health.summarize's output — this module only maps its
    worst state onto a lamp (LAMP_FROM_PORTAL_STATE)."""
    summary = ev.get("summary")
    if not isinstance(summary, dict):
        return _result(spec, "unknown", "Portal session health unavailable.",
                       "The durable-session roll-up could not be read, so no claim is made about "
                       "whether the nightly portal pulls will run.")
    if not summary.get("total"):
        return _result(spec, "unmonitored", "No portal logins configured.",
                       "This tenant has no merchant-portal sources, so there is nothing to watch here. "
                       "Reported as unmonitored rather than green — an absent subsystem is not a "
                       "healthy one.")
    worst = str(summary.get("worst") or "")
    lamp = LAMP_FROM_PORTAL_STATE.get(worst, "unknown")
    need = int(summary.get("needs_human") or 0)
    return _result(spec, lamp,
                   "%d of %d portal session(s) need a human." % (need, int(summary.get("total") or 0))
                   if need else "All portal sessions are riding a valid login.",
                   "Worst session state: %s." % (worst or "unrecognised"), count=need,
                   evidence={"worst": worst, "needs_human": need, "total": summary.get("total"),
                             "items": [{"label": i.get("label"), "state": i.get("state")}
                                       for i in (summary.get("items") or [])[:8]]})


def heartbeat_lamp(last_success, cadence_hours=None, grace_hours=None, now=None):
    """Is a scheduled job still alive? PURE. Returns (lamp, age_hours, reason).

    THE GAP THIS CLOSES. The platform's automation is four self-registering pg_cron jobs (migs 922 /
    940 / 950 / 956). Each self-heals its REGISTRATION on boot — but nothing ever noticed that a job
    which is registered has stopped PRODUCING. Mig 950 found the reviews sweep had never once run on
    its own and the only evidence was a hole in the data, discovered by the owner.

    Grace exists so a job that ticks at 03:00 is not red at 03:05 the next day for being minutes late:
    late-but-inside-grace is amber (look at it in daylight), past grace is red (it stopped).
    NEVER-run is red, not amber — a job that has produced nothing has never worked at all."""
    now = _now_utc(now)
    cadence = _num(cadence_hours, DEFAULT_CADENCE_HOURS) or DEFAULT_CADENCE_HOURS
    grace = _num(grace_hours, DEFAULT_GRACE_HOURS)
    if grace is None:
        grace = DEFAULT_GRACE_HOURS
    last = _dt(last_success)
    if last is None:
        return "red", None, "never"
    age = round(_hours(now, last), 2)
    if age < 0:                                  # clock skew / a future stamp: report it, don't pass it
        return "unknown", age, "future_timestamp"
    if age <= cadence:
        return "green", age, "fresh"
    if age <= cadence + grace:
        return "amber", age, "late"
    return "red", age, "overdue"


def _eval_heartbeat(spec, ev, now):
    cfg = spec.get("config") or {}
    lamp, age, reason = heartbeat_lamp(ev.get("last_success"), cfg.get("cadence_hours"),
                                       cfg.get("grace_hours"), now=now)
    cadence = _num(cfg.get("cadence_hours"), DEFAULT_CADENCE_HOURS)
    words = {
        "never": "Has never run — this automation has produced nothing, ever.",
        "fresh": "Last ran %s ago." % humanize_hours(age),
        "late": "Last ran %s ago (expected every %s)." % (humanize_hours(age), humanize_hours(cadence)),
        "overdue": "Last ran %s ago (expected every %s) — it has stopped."
                   % (humanize_hours(age), humanize_hours(cadence)),
        "future_timestamp": "The last-run stamp is in the future; the clock or the writer is wrong.",
    }
    return _result(spec, lamp, words.get(reason, reason), cfg.get("note") or
                   "Scheduler liveness is measured from the job's own last SUCCESSFUL run, not from "
                   "whether a cron entry exists — a registered job that produces nothing is the "
                   "failure mode this catches.",
                   evidence={"last_success": ev.get("last_success"), "age_hours": age,
                             "reason": reason, "cadence_hours": cadence})


def _eval_counter(spec, ev, now):
    """A numeric backlog with configured thresholds. `warn_at`/`red_at` are per-org CONFIG (RULE TWO):
    what counts as "too many stuck rows" is a tenant's tolerance, never a constant in code."""
    cfg = spec.get("config") or {}
    value = _num(ev.get("value"), None)
    if value is None:
        return _result(spec, "unknown", "No measurement.",
                       "The counter behind this check returned nothing, so its condition is unknown.")
    warn = _num(cfg.get("warn_at"), 1.0)
    red = _num(cfg.get("red_at"), None)
    noun = cfg.get("noun") or "item(s)"
    lamp = "green"
    if red is not None and value >= red:
        lamp = "red"
    elif warn is not None and value >= warn:
        lamp = "amber"
    return _result(spec, lamp,
                   "%d %s" % (int(value), noun) if lamp != "green" else "Nothing queued (%s)." % noun,
                   cfg.get("note") or "", count=int(value),
                   evidence={"value": value, "warn_at": warn, "red_at": red})


def _eval_boolean(spec, ev, now):
    """A yes/no fact (a key is configured, a guard is installed, a service answered). `ok` MUST be a
    real boolean — a None means the probe could not tell, which is `unknown`, never green."""
    cfg = spec.get("config") or {}
    ok = ev.get("ok")
    if ok is None:
        return _result(spec, "unknown", "Could not determine.",
                       redact(str(ev.get("detail") or "The probe returned no answer."))[:300])
    if ok:
        return _result(spec, "green", cfg.get("ok_headline") or "OK.",
                       redact(str(ev.get("detail") or cfg.get("ok_detail") or ""))[:300])
    lamp = "amber" if str(cfg.get("severity") or "red").lower() == "amber" else "red"
    return _result(spec, lamp, cfg.get("fail_headline") or "Not working.",
                   redact(str(ev.get("detail") or cfg.get("fail_detail") or ""))[:300],
                   evidence={"ok": False})


_KIND_EVALUATORS = {
    "attention_provider": _eval_attention,
    "portal_sessions": _eval_portal_sessions,
    "heartbeat": _eval_heartbeat,
    "counter": _eval_counter,
    "boolean": _eval_boolean,
}


def evaluate_check(spec, evidence=None, now=None):
    """ONE registry row + the evidence a probe gathered → one board row. PURE.

    `spec` is the core.system_check row (key, subsystem, label, kind, config, deep_link, index_ref,
    code_refs, enabled). `evidence` is whatever the probe of that KIND produces.

    Honesty rules 1 and 2 from the module docstring are enforced HERE, so no caller can bypass them:
    a disabled row, the `unmonitored` kind, an unknown kind and a raising probe each resolve to a
    non-green lamp that says what it does not know."""
    spec = dict(spec or {})
    ev = dict(evidence or {})
    kind = str(spec.get("kind") or "").strip()

    if spec.get("enabled") is False:
        return _result(spec, "unmonitored", "Check disabled.",
                       "Someone turned this check off, so this subsystem is NOT being watched. It is "
                       "reported as unmonitored, never as green.")
    if kind == "unmonitored":
        return _result(spec, "unmonitored", "Not monitored yet.",
                       str(spec.get("config", {}).get("note") or
                           "This subsystem is registered on the board but has no probe yet. It is "
                           "listed so the coverage gap is visible instead of being mistaken for "
                           "health."))
    if ev.get("probe_error"):
        return _result(spec, "unknown", "The probe failed.",
                       "This check could not be measured, so the subsystem's condition is unknown — "
                       "not healthy. " + redact(str(ev.get("probe_error")))[:240],
                       evidence={"probe_error": redact(str(ev.get("probe_error")))[:240]})
    fn = _KIND_EVALUATORS.get(kind)
    if fn is None:
        return _result(spec, "unknown", "Unrecognised check kind %r." % kind,
                       "The registry asks for a probe kind this build does not implement, so nothing "
                       "can be claimed about this subsystem. Known kinds: %s."
                       % ", ".join(CHECK_KINDS))
    try:
        return fn(spec, ev, now)
    except Exception as e:                       # a bug in one evaluator may never green the board
        return _result(spec, "unknown", "The check raised while being evaluated.",
                       redact("%s: %s" % (type(e).__name__, e))[:240])


# ── the board ────────────────────────────────────────────────────────────────────────────────────
def roll_up(results, now=None):
    """Every evaluated check → the one headline lamp plus honest coverage. PURE.

    THE HEADLINE EXCLUDES `unmonitored` and reports it as coverage instead: a board that can never go
    green because six subsystems have no probe yet is a board nobody reads, and "37 green, 6 not
    monitored" is both usable and true. The one thing that must never happen — a green headline over
    an empty or fully-unmonitored board — is the explicit `monitored == 0 → unknown` rule."""
    rows = list(results or [])
    monitored = [r for r in rows if r.get("lamp") != "unmonitored"]
    unmonitored = [r for r in rows if r.get("lamp") == "unmonitored"]
    counts = {l: sum(1 for r in rows if r.get("lamp") == l) for l in LAMPS}
    if not monitored:
        lamp = "unknown"
        headline = ("Nothing on this board is actually being checked (%d registered, %d monitored)."
                    % (len(rows), 0))
    else:
        lamp = worst_lamp(*[r.get("lamp") for r in monitored])
        if lamp == "green":
            headline = "All %d monitored check(s) are green." % len(monitored)
        else:
            worst_rows = [r for r in monitored if r.get("lamp") == lamp]
            headline = "%d of %d monitored check(s) are %s — first: %s." % (
                len(worst_rows), len(monitored), lamp,
                (worst_rows[0].get("label") or worst_rows[0].get("key")))
    by_sub = {}
    for r in rows:
        s = r.get("subsystem") or "other"
        by_sub[s] = worst_lamp(by_sub.get(s, "green"), r.get("lamp"))
    return {
        "lamp": lamp,
        "headline": headline,
        "counts": counts,
        "actionable": sum(1 for r in rows if r.get("lamp") in ACTIONABLE),
        "coverage": {
            "registered": len(rows),
            "monitored": len(monitored),
            "unmonitored": len(unmonitored),
            "unmonitored_keys": [r.get("key") for r in unmonitored],
            # Stated as a fraction, never as a bare "OK": the owner must be able to see at a glance
            # how much of the platform this board actually speaks for.
            "note": ("%d of %d registered checks are actually measured; %d subsystem(s) are declared "
                     "but not monitored." % (len(monitored), len(rows), len(unmonitored))),
        },
        "by_subsystem": dict(sorted(by_sub.items())),
        "generated_at": _now_utc(now).isoformat(),
    }


def sort_board(results):
    """Worst first, then subsystem, then label — the order an operator triages in."""
    return sorted(results or [], key=lambda r: (-_RANK.get(r.get("lamp"), 3),
                                                r.get("subsystem") or "", r.get("label") or ""))


# ── the DAILY check: scheduling + self-monitoring ────────────────────────────────────────────────
def is_due(last_run_at, cadence_hours=None, now=None):
    """Should the daily check run for this org now? PURE. Never run ⇒ due immediately."""
    last = _dt(last_run_at)
    if last is None:
        return True
    cadence = _num(cadence_hours, DEFAULT_CADENCE_HOURS) or DEFAULT_CADENCE_HOURS
    return _hours(_now_utc(now), last) >= cadence


def next_run_at(now=None, cadence_hours=None):
    cadence = _num(cadence_hours, DEFAULT_CADENCE_HOURS) or DEFAULT_CADENCE_HOURS
    return (_now_utc(now) + timedelta(hours=cadence)).isoformat()


def due_orgs(rows, cadence_hours=None, now=None, limit=None):
    """Which orgs' daily checks are due. `rows` are {org_id, last_run_at, enabled} state rows.

    A disabled org is skipped; an org with NO state row has never been checked and is due. Ordered
    oldest-first so a backlog drains fairly instead of one org starving behind another."""
    out = []
    for r in rows or []:
        if r.get("enabled") is False:
            continue
        org = r.get("org_id")
        if not org:
            continue
        if is_due(r.get("last_run_at"), r.get("cadence_hours") or cadence_hours, now=now):
            out.append({"org_id": org, "last_run_at": r.get("last_run_at")})
    out.sort(key=lambda x: (_dt(x["last_run_at"]) or datetime(1970, 1, 1, tzinfo=timezone.utc)))
    return out[:limit] if limit else out


def selfcheck_row(last_run_at, cadence_hours=None, grace_hours=None, now=None):
    """The board's row ABOUT ITSELF. Mig-950 lesson, applied to the watchman: if the daily check has
    stopped firing, every other lamp on this board is stale — and a stale green is the most dangerous
    thing a control box can show. So the freshness of the daily run is itself a monitored check."""
    spec = {"key": "control_box_daily_check", "subsystem": "platform",
            "label": "Daily system check (this board)", "kind": "heartbeat",
            "deep_link": "/admin/control-box", "deep_link_label": "Open the control box",
            "index_ref": "§20 Super-admin control box",
            "code_refs": ["backend/app/modules/core/control_box.py",
                          "database/migrations/971_system_check_cron.sql"],
            "config": {"cadence_hours": _num(cadence_hours, DEFAULT_CADENCE_HOURS),
                       "grace_hours": _num(grace_hours, DEFAULT_GRACE_HOURS),
                       "note": "If this row is not green, every other lamp on the board may be stale: "
                               "the scheduled check that refreshes them has not run. Registration "
                               "self-heals on every backend boot (mig 971); a red here means the job "
                               "is registered but not producing."}}
    return evaluate_check(spec, {"last_success": last_run_at}, now=now)


def escalations(current, previous):
    """Which checks got WORSE since the last daily run — the notify-once discipline
    `portal_session_health.should_notify` established, applied to the board. PURE.

    A board that is red for a week must not send seven identical alerts; only a NEW or WORSENED lamp
    pages. Improvements are returned separately so a daily digest can also say what recovered."""
    prev = {r.get("key"): r.get("lamp") for r in (previous or [])}
    worsened, recovered, new = [], [], []
    for r in current or []:
        k, lamp = r.get("key"), r.get("lamp")
        if k not in prev:
            if lamp in ACTIONABLE:
                new.append(r)
            continue
        if is_worse(lamp, prev[k]):
            worsened.append(r)
        elif is_worse(prev[k], lamp) and prev[k] in ACTIONABLE:
            recovered.append(r)
    return {"worsened": worsened, "recovered": recovered, "new": new,
            "should_notify": bool(worsened or new)}


# ── redaction: nothing from a diagnostic may carry a secret ──────────────────────────────────────
# Diagnostics quote error strings, and error strings have historically carried connection URLs and
# tokens (commcalc/url_guard.py exists for this reason). Every string this module puts on the board,
# into a run record, or into a fix bundle passes through here first. Patterns are ordered
# longest-context first so a URL's password is masked before the generic key=value rule sees it.
_SECRET_PATTERNS = (
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}"), "sk-ant-***REDACTED***"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"), "***JWT-REDACTED***"),
    (re.compile(r"(?i)\b(postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s'\"]+"), r"\1://***REDACTED***"),
    # `<NAME>: <value>` / `<NAME>=<value>`. The name may be EMBEDDED in a longer identifier — the first
    # cut of this rule used \b and so sailed straight past `SUPABASE_SERVICE_KEY=…`, because the
    # underscore before SERVICE is a word character and there is no boundary there. The surrounding
    # `[A-Za-z0-9_\-]*` is what makes prefixed/suffixed env names match. (Caught by the harness.)
    (re.compile(r"(?i)([A-Za-z0-9_\-]*(?:api[_-]?key|secret|token|password|passwd|pwd|authorization"
                r"|service[_-]?key)[A-Za-z0-9_\-]*)\s*[:=]\s*['\"]?(?:bearer\s+|basic\s+)?"
                r"[^\s'\",;)]{4,}"), r"\1=***REDACTED***"),
    # A scheme-prefixed credential with no name in front of it (`Bearer abc…`, `Basic dGVzdA==`).
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9_\-\.=+/]{8,}"), r"\1 ***REDACTED***"),
)


def redact(text):
    """Mask anything that looks like a credential. Never raises; returns '' for None."""
    if text is None:
        return ""
    s = str(text)
    for pat, repl in _SECRET_PATTERNS:
        s = pat.sub(repl, s)
    return s


# ── AI GUARD — "must protected from third party misuse of the ai api and only restricted to this
#    module" (owner, 2026-09-05) ──────────────────────────────────────────────────────────────────
# The decision to spend an Anthropic call is PURE and proven, so the guard cannot be reasoned about
# only by reading an endpoint. Six gates, in this ORDER on purpose:
#
#   1. AUTHORIZATION first, fail-closed. A caller who is not a platform super-admin is refused before
#      any other state is consulted, so an unauthorized probe cannot learn the budget, the usage, or
#      even whether a key is configured (the mig-434 fail-closed 403 posture).
#   2. MODULE RESTRICTION. The call must declare `purpose='control_box_triage'`. This module's key
#      usage is not a general-purpose AI endpoint and will not act as one.
#   3. INPUT SHAPE. The only caller-supplied value is a check KEY that must already exist in the
#      registry (`validate_check_key`). There is no prompt passthrough: the prompt is assembled
#      server-side from diagnostics. A caller cannot make the model do anything except comment on a
#      check that is already failing.
#   4. ENABLED / 5. KEY PRESENT — the module degrades to a fully working red/green board with no AI.
#   6. RATE LIMIT then BUDGET. Per-hour calls bound a runaway loop; per-day calls and tokens bound
#      the spend. Both are per-org CONFIG rows with house defaults (RULE TWO), enforced server-side.
#
# The AI is COMMENTARY. `evaluate_check` decides every lamp deterministically before any of this runs,
# so a refused, throttled, absent or failed AI call can never change whether a light is red.
AI_PURPOSE = "control_box_triage"

DEFAULT_AI_CONFIG = {
    "enabled": True,
    "max_calls_per_hour": 10,
    "daily_call_cap": 40,
    "daily_token_cap": 400000,
    "max_input_chars": 12000,     # the assembled bundle is truncated to this before it is sent
}

_DENY = {
    "not_super_admin": "The control box AI is restricted to platform super-admins.",
    "wrong_purpose": "This key is restricted to control-box triage and refuses any other purpose.",
    "unknown_check": "That check is not in the registry, so there is nothing to triage.",
    "disabled": "AI triage is switched off for this tenant.",
    "no_key": "No AI key is configured on this backend — the board works without it.",
    "rate_limited": "Too many triage calls in the last hour; try again shortly.",
    "budget_exhausted": "The daily AI budget for this tenant is used up.",
    "not_actionable": "That check is green — triage is only offered for a failing check.",
}


def validate_check_key(key, known_keys):
    """The ONLY caller-supplied input on the AI path. A key must be a short, plain identifier AND
    already exist in the registry — so nothing a caller types ever reaches the model as text."""
    k = str(key or "").strip()
    if not k or len(k) > 80 or not re.fullmatch(r"[a-z0-9_.\-]+", k):
        return None
    return k if k in set(known_keys or ()) else None


def ai_guard_decision(caller, *, purpose=None, check_key=None, known_keys=(), lamp=None,
                      config=None, usage=None, has_key=False, now=None):
    """May this AI call proceed? PURE. Returns {allow, code, reason, remaining}.

    `caller` is the resolved caller dict ({super_admin, org_id, …}) — resolved SERVER-SIDE from the
    verified token, never trusted from the request body."""
    cfg = {**DEFAULT_AI_CONFIG, **{k: v for k, v in (config or {}).items() if v is not None}}
    use = usage or {}

    def deny(code, **extra):
        return {"allow": False, "code": code, "reason": _DENY.get(code, code), **extra}

    # 1. fail-closed authorization — before anything else is revealed
    if not caller or not caller.get("super_admin"):
        return deny("not_super_admin")
    # 2. this key serves THIS module only
    if str(purpose or "") != AI_PURPOSE:
        return deny("wrong_purpose")
    # 3. bounded, validated input — never a free-form prompt
    valid = validate_check_key(check_key, known_keys)
    if not valid:
        return deny("unknown_check")
    if lamp is not None and lamp not in ACTIONABLE:
        return deny("not_actionable")
    # 4/5. config + key presence — the board must work with the AI entirely absent
    if not cfg.get("enabled"):
        return deny("disabled")
    if not has_key:
        return deny("no_key")
    # 6. rate limit, then budget
    per_hour = int(_num(cfg.get("max_calls_per_hour"), 10) or 10)
    if int(_num(use.get("calls_last_hour"), 0) or 0) >= per_hour:
        return deny("rate_limited", retry_after_minutes=int(_num(use.get("minutes_to_window"), 60) or 60))
    day_calls = int(_num(cfg.get("daily_call_cap"), 40) or 40)
    day_tokens = int(_num(cfg.get("daily_token_cap"), 400000) or 400000)
    if int(_num(use.get("calls_today"), 0) or 0) >= day_calls:
        return deny("budget_exhausted", limit="daily_call_cap")
    if int(_num(use.get("tokens_today"), 0) or 0) >= day_tokens:
        return deny("budget_exhausted", limit="daily_token_cap")
    return {"allow": True, "code": "ok", "reason": "", "check_key": valid,
            "remaining": {
                "calls_this_hour": per_hour - int(_num(use.get("calls_last_hour"), 0) or 0),
                "calls_today": day_calls - int(_num(use.get("calls_today"), 0) or 0),
                "tokens_today": day_tokens - int(_num(use.get("tokens_today"), 0) or 0)}}


def ai_audit_row(org_id, caller, check_key, decision, *, usage=None, model=None, error=None,
                 purpose=AI_PURPOSE, now=None):
    """The audit row for ONE attempted AI call — allowed or refused. Refusals are logged too: a wall
    of `not_super_admin` denials IS the signal that someone is probing the endpoint. Org-scoped, and
    every free-text field is redacted before it is stored.

    Shape matches `core.ai_call_audit` (mig 972), which is deliberately GENERIC (`purpose` +
    `subject_key`) so every outbound AI call in the platform can share ONE meter and ONE audit trail
    instead of each module re-inventing its own — see that migration's header."""
    u = usage or {}
    return {
        "org_id": org_id,
        "purpose": purpose or AI_PURPOSE,
        "subject_key": (check_key or "")[:80],
        "actor_uid": (caller or {}).get("id") or (caller or {}).get("uid"),
        "actor_email": (caller or {}).get("email"),
        "allowed": bool((decision or {}).get("allow")),
        "deny_code": None if (decision or {}).get("allow") else (decision or {}).get("code"),
        "model": (model or "")[:120] or None,
        "input_tokens": int(_num(u.get("input_tokens"), 0) or 0),
        "output_tokens": int(_num(u.get("output_tokens"), 0) or 0),
        "error": redact(error)[:300] or None,
        "created_at": _now_utc(now).isoformat(),
    }


def rollup_usage(rows, now=None, window_hours=1.0):
    """Usage counters from this org's audit rows — what `ai_guard_decision` spends against. PURE.

    Counts only ALLOWED calls against the caps: a refused call costs no tokens, and counting refusals
    would let a spray of unauthorized attempts lock the owner out of their own triage."""
    now = _now_utc(now)
    day_start = now - timedelta(hours=24)
    win_start = now - timedelta(hours=float(window_hours or 1.0))
    calls_hour = calls_day = tokens_day = 0
    for r in rows or []:
        if not r.get("allowed"):
            continue
        at = _dt(r.get("created_at"))
        if at is None:
            continue
        if at >= day_start:
            calls_day += 1
            tokens_day += int(_num(r.get("input_tokens"), 0) or 0) + int(_num(r.get("output_tokens"), 0) or 0)
        if at >= win_start:
            calls_hour += 1
    return {"calls_last_hour": calls_hour, "calls_today": calls_day, "tokens_today": tokens_day}


# ── the FIX bundle — "a way to fix that problem connected with Claude code" ──────────────────────
# DELIBERATELY NOT AN AUTO-APPLY LOOP. This assembles, server-side, a scoped, ready-to-run task a
# HUMAN hands to Claude Code (copy button / deep link). No web request can make an AI-authored change
# to production through this module: the human runs it, reviews the diff, and ships it through the
# normal PR path. The bundle carries what a fix actually needs — which check failed, the evidence, the
# module, the index anchor and the files — so the human is not the one assembling context.
_FIX_PREAMBLE = (
    "MetricsPro — control-box fix task (generated server-side, %s UTC).\n"
    "This is a scoped diagnostic handed to Claude Code by a platform super-admin. Follow the repo's "
    "working rules in CLAUDE.md: consult docs/SYSTEM_DATA_FLOW_INDEX.md FIRST, extend the existing "
    "mechanism rather than adding a sibling one, keep every sensitive query org-scoped, and ship pure "
    "logic with a DB-free harness.\n"
)


def build_fix_task(result, *, org_id=None, now=None, extra_context=None):
    """A failing board row → the text a human pastes into Claude Code. PURE, deterministic, redacted.

    Contains NO caller-supplied text: every field comes from the registry row and the server-side
    probe evidence, which is what keeps this path free of prompt injection from a browser."""
    r = dict(result or {})
    now_s = _now_utc(now).strftime("%Y-%m-%d %H:%M")
    lines = [_FIX_PREAMBLE % now_s]
    lines.append("CHECK:      %s (%s)" % (r.get("label") or r.get("key"), r.get("key")))
    lines.append("SUBSYSTEM:  %s" % (r.get("subsystem") or "unknown"))
    lines.append("LAMP:       %s" % (r.get("lamp") or "unknown"))
    lines.append("SINCE:      %s" % (r.get("measured_at") or "unknown"))
    if org_id:
        lines.append("ORG:        %s" % org_id)
    lines.append("")
    lines.append("WHAT THE BOARD SEES")
    lines.append("  %s" % redact(r.get("headline") or "(no headline)"))
    if r.get("detail"):
        lines.append("  %s" % redact(r.get("detail")))
    ev = r.get("evidence") or {}
    if ev:
        lines.append("")
        lines.append("EVIDENCE (server-side, redacted)")
        for k, v in sorted(ev.items()):
            if isinstance(v, list):
                lines.append("  %s:" % k)
                for item in v[:8]:
                    lines.append("    - %s" % redact(item))
            else:
                lines.append("  %s: %s" % (k, redact(v)))
    if r.get("index_ref"):
        lines.append("")
        lines.append("INDEX:      %s  (docs/SYSTEM_DATA_FLOW_INDEX.md — read this before changing anything)"
                     % r.get("index_ref"))
    if r.get("code_refs"):
        lines.append("FILES:      %s" % ", ".join(str(c) for c in r.get("code_refs")))
    if r.get("deep_link"):
        lines.append("MODULE:     %s" % r.get("deep_link"))
    if r.get("owner_agent"):
        lines.append("ROUTE TO:   %s (CLAUDE.md routing directive — this subsystem is owned by that agent)"
                     % r.get("owner_agent"))
    if extra_context:
        lines.append("")
        lines.append("CONTEXT")
        lines.append("  %s" % redact(extra_context)[:600])
    lines.append("")
    lines.append("TASK")
    lines.append("  1. Reproduce the condition above from the named files; do not trust this summary alone.")
    lines.append("  2. Find the ROOT CAUSE. A check going red is a symptom; the index says which data")
    lines.append("     path owns it.")
    lines.append("  3. Fix it by EXTENDING the existing mechanism. State what you checked in the index")
    lines.append("     and what you reused.")
    lines.append("  4. Prove the fix with a DB-free harness, and register anything new in the index.")
    lines.append("  5. Do not apply data changes to production directly — surface the SQL for approval.")
    return "\n".join(lines)


def fix_task_bundle(result, *, org_id=None, now=None):
    """The API shape behind the row's 'Fix with Claude Code' button: the copyable task plus the
    structured fields a deep link needs. No AI is involved in producing this — it is deterministic,
    so it works with ANTHROPIC_API_KEY entirely absent."""
    return {
        "check_key": (result or {}).get("key"),
        "lamp": (result or {}).get("lamp"),
        "label": (result or {}).get("label"),
        "deep_link": (result or {}).get("deep_link"),
        "index_ref": (result or {}).get("index_ref"),
        "code_refs": list((result or {}).get("code_refs") or []),
        "owner_agent": (result or {}).get("owner_agent"),
        "task": build_fix_task(result, org_id=org_id, now=now),
        "generated_at": _now_utc(now).isoformat(),
        "note": "Copy this into Claude Code. A human runs and reviews the change — this platform "
                "never lets a web request apply an AI-authored change to production.",
    }
