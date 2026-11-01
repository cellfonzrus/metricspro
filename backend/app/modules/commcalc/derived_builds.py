"""DERIVED BUILDS — the ONE registry of "a stored snapshot that must be rebuilt when its inputs land"
(index §COMMCALC-41).

Owner, 2026-10-27: *"The rebuild ledger or recalculate should be real time all across, then tenant would not
know when to run, so the data should be live update as soon as something changes."*

THE CLASS, NOT THE INSTANCE. `auto_calc` (§6l) already made ONE derived thing live: the rep commission
calculation recalculates itself after every landing, and nobody presses Run Calculation. Every OTHER derived
snapshot in this platform was left on a button:

  · `commcalc.device_payable_ledger`  — "↻ Rebuild ledger" on /commcalc/payables. Measured 2026-10-27, it had
    last been built 2026-09-04 while its feed (`asset_ledger`) was uploaded 2026-10-23: 53 days stale, max due
    date in the past, so every "what is owed next week" surface read EMPTY and nothing on the page said why.
  · `commcalc.appeal_recovery`        — "Rebuild" on the Appeal Recovery report, same shape.

So the instance is "the payable ledger is stale"; the class is **a derived table's freshness is its own fact,
and a tenant cannot be the scheduler for it**. The fix is not a second hook for payables — that would be the
patchwork this house forbids. It is this registry plus the SAME post-landing hook, the same debounce, the same
claim and the same poller that commission already rides.

ONE FACT, ONE HOME, DEREFERENCED. For each build: WHICH TABLES ARE ITS INPUTS, WHAT REFRESHES IT, and WHICH
endpoint a person may still press. `auto_calc.landed` reads this registry to decide what a landing makes stale
— it never lists a build or a table itself, and the registry never lists a table that already has a name in
`data_lineage_registry`. A build whose inputs are DECLARED per carrier (payables) names a resolver function
rather than copying the config's table names here, so declaring a new carrier changes no code.

NO SECOND TRIGGER, NO SECOND POLLER. `auto_calc.run_due` — the one poller, started once per process at boot —
claims and runs these alongside the commission calculation. Everything else here is queue bookkeeping that
mirrors `auto_calc`'s, keyed on (org, build) instead of (org, period) because a snapshot of "what we owe" is
not a month's figure. The pure debounce (`due_at` / `is_due` / `merge_landings`) is IMPORTED from `auto_calc`,
never restated. Lock: `backend/harness_derived_build_lock.py`.

MIGRATION 1100 NOT APPLIED -> the queue degrades to THIS PROCESS (`_MEM`), exactly as `auto_calc` degrades
without mig 1030: the same debounce, one build per process, the outcome logged. Nothing a person presses
changes, so an unapplied migration costs liveness and never correctness.

Stdlib only at import time. Every runner and every input resolver is imported LAZILY inside the function that
needs it, so this module stays importable from the router, the sweeps and a harness.
"""
import threading

from app.modules.commcalc import data_lineage_registry as _lineage

MIGRATION = "1100_derived_build_queue.sql"

#: The state table, mig 1100. One row per (org, build): the pending request and the last outcome.
STATE_TABLE = "derived_build_status"
COL_REQUESTED = "requested_at"
COL_LANDINGS = "landings"
COL_LAST = "last"

SCOPE_PERIOD = "period"          # rebuilt for ONE month — the commission calculation
SCOPE_ORG = "org"                # rebuilt whole for an org — every snapshot in this registry but commission


# ── WHAT DECLARES REP PAY (index §COMMCALC-41.9) ────────────────────────────────────────────────────────────
# The tables a commission plan is read OUT of by the pay engines. Named here, once, because the `commission`
# build below declares them and `harness_derived_build_lock.py` §B8 DERIVES the same set from those engines'
# own reads — so dropping one from this tuple contradicts the code that reads it, rather than passing quietly.
# (§B7's lesson, 2026-10-29: a pinned list cannot catch its own deletion.)
PLAN_DECLARATION = (
    # commission_engine._load_plans — the plan and its three children
    "commission_plan", "commission_rule", "commission_tier", "commission_plan_assignment",
    # installment_engine._load_schedules — the payout schedules a plan pays through
    "payout_schedule", "payout_schedule_line",
    # the multi-month (installment) schedules
    "plan_installment_schedule", "plan_installment_line",
)


# ── THE REGISTRY ────────────────────────────────────────────────────────────────────────────────────────────
# key          — the queue key, and what an outcome is recorded under
# label        — how the build is named in the one sentence an operator reads
# scope        — SCOPE_PERIOD (auto_calc's own path) or SCOPE_ORG (claimed and run here)
# inputs       — the tables whose landing makes this build stale, by IDENTITY from data_lineage_registry
# inputs_fn    — "module:function" returning MORE input tables for an org, where they are DECLARED config
#                rather than fixed. Called with (client, org_id). A failure is reported, never swallowed into
#                "this build has no inputs" (§COMMCALC-29's posture, applied to a registry read).
# config_inputs— the tables that DECLARE this build rather than feed it. A DECLARATION IS AN INPUT: owner ask
#                2026-10-29, "Put the Net term days as 14 from the back, programmable not hard code". Net
#                terms live in `payable_source_map.net_terms_days` and nothing in code holds a number, so the
#                setting was already programmable — but changing it left the stored due dates on the old
#                terms until some unrelated feed happened to land. Editing a setting and waiting for a
#                shipment to arrive before the figure agrees with it is the same defect as the button: the
#                tenant is the scheduler again. These are kept APART from `inputs` because `inputs` are
#                external feeds carrying lineage identity (data_lineage_registry's job) and a config table
#                has none; for staleness they are the same thing, which is what `matches` says.
# run          — "module:function", called with (client, org_id) -> {"status": "done"|"error", ...}
# endpoint     — the manual door that stays, so a person can still force a rebuild. The lock requires that this
#                endpoint and `run` reach the SAME function: a button that rebuilds differently from the hook
#                is two answers to one question.
BUILDS = (
    {
        "key": "commission",
        "label": "the commission calculation",
        "scope": SCOPE_PERIOD,
        "inputs": _lineage.COMMISSION_CALC_FEEDS,
        "inputs_fn": None,
        # A DECLARATION IS AN INPUT — the rule §COMMCALC-41.8 established on 2026-10-29 for the carrier's net
        # payment terms, applied at last to the build that moves REP PAY. Owner, 2026-10-31: *"i upldated the
        # commission for luxelink in ny plan but it did not get auto updated as we confirmed yesterday that
        # everytime anything is change it will auto update acorss the board, i changed teh comission from june
        # - oct"*. He was right to expect it: the oldest live build declared its feeds and no declaration at
        # all, so the one thing a tenant actually edits — the plan — made nothing stale, and the figures waited
        # for an unrelated upload. These are the tables the pay engines read a plan OUT of:
        # `commission_engine._load_plans` (the plan, its rules, its tiers, its assignments),
        # `installment_engine._load_schedules` (the payout schedules) and the multi-month plan schedules.
        "config_inputs": PLAN_DECLARATION,
        # A FEED landing names its own month; AN EDIT DOES NOT. So the build says which months it restates,
        # rather than every endpoint that can save a plan guessing — and it is bounded, because a rate fixed
        # today must not rewrite July 2024.
        "periods_fn": "app.modules.commcalc.router:_plan_restate_periods",
        "run": "app.modules.commcalc.auto_calc:_default_runner",
        "endpoint": "POST /commcalc/calculate",
        "why": "index §6l — the build this registry generalises; auto_calc runs it per (org, period) itself",
    },
    {
        "key": "payable_ledger",
        "label": "the device payable ledger",
        "scope": SCOPE_ORG,
        # The feeds every configured carrier's build reads whatever its config says: the Boost/VIP consignment
        # snapshot, the distributor's reimbursement evidence (`_epay_payments_map`) and the distributor's own
        # invoices (`_vip_invoice_map`) — all three read unconditionally by `engine._rows_for_page`.
        "inputs": (_lineage.ASSET_LEDGER, _lineage.PAYMENT_DETAIL, _lineage.DISTRIBUTOR_INVOICES),
        # …plus every table the carrier's own `payable_source_map` row DECLARES (source, price list, sold-match,
        # reimbursement-match). Declared, so a new carrier is a config row and this registry does not change.
        "inputs_fn": "app.modules.payables.refresh:input_tables",
        # The map is not a feed — it is the DECLARATION this build reads: which table, which columns, and the
        # carrier's net payment terms. Editing it changes every stored due date, so it makes the ledger stale
        # exactly as a shipment does. Its writers are `POST /payables/source-maps` and the delete beside it,
        # and §C of the lock requires both to call the hook.
        "config_inputs": ("payable_source_map",),
        "run": "app.modules.payables.refresh:refresh",
        "endpoint": "POST /payables/rebuild",
        "why": "measured 53 days stale on 2026-10-27 while its feed was 4 days old (index §COMMCALC-41.1)",
    },
    {
        "key": "appeal_recovery",
        "label": "the denied-appeal recovery ledger",
        "scope": SCOPE_ORG,
        "inputs": (_lineage.PAYMENT_DETAIL, _lineage.MI_RESIDUAL),
        "inputs_fn": None,
        "run": "app.modules.recovery.refresh:refresh",
        "endpoint": "POST /recovery/rebuild",
        "why": "the sibling found by the same question — same delete-then-insert snapshot, same stale button",
    },
)

# EVERY MANUAL REBUILD DOOR IN THE PLATFORM, classified. The lock discovers each `@router.post`/`put` whose
# path contains rebuild / recalc / recompute / rerun — that IS the owner's question, stated as code: which
# buttons does a tenant have to know when to press? Each must be a registered build's `endpoint` above, or be
# excused here with its reason. A new one cannot ship unclassified.
EXCUSED_DOORS = (
    ("POST /pos/sales-from-reports/rebuild",
     "already live: the intake commit calls `_intake_pos_rebuild_after_landing` for the stores and dates it "
     "landed, so this snapshot refreshes on its own landing and a second trigger would run it twice"),
    ("POST /commcalc/recompute-rep",
     "ONE REP inside the registered `commission` build — a narrower manual action on a build that is already "
     "live, not a second build. The whole-period run it narrows is `POST /commcalc/calculate`"),
    ("POST /vision/behavior/recompute",
     "the vision module's own behaviour scoring over camera events, not a figure derived from a landed feed; "
     "no upload makes it stale"),
)

# Snapshot builders REVIEWED and deliberately NOT registered, each with the reason — the write side of the
# same question. `harness_derived_build_lock.py` requires every entry to still exist, so a stale excuse is RED.
EXCUSED_BUILDS = (
    ("app/modules/asset/router.py", "process_asset_ledger_bytes",
     "a LANDING of asset_ledger (staging swap, mig 300) — it is this registry's main input, and it calls the "
     "hook; treating it as a build would have it rebuild itself"),
    ("app/modules/asset/router.py", "_stage_and_swap_ledger",
     "the staging swap INSIDE that landing, not a build derived from one"),
    ("app/modules/commcalc/router.py", "_restore_rows",
     "puts a snapshot BACK after a failed insert — nothing landed, so nothing derived from it changed"),
)

# SAME CLASS, NOT WIRED IN THIS PR — reported rather than silently left out (the house rule: find the siblings
# and fix them OR excuse them explicitly). Each rebuilds a derived snapshot from landed data and still waits
# for somebody to press something. The lock pins the COUNT, so this list may only shrink.
#
# Why not now: every one of them writes `commcalc.flags`, the discrepancy-flag table several modules share, so
# making them live means deciding which feed landing owns each flag family — a question about the flag
# registry, not about this queue. Wiring them blind would queue a flag sweep after every unrelated upload.
UNWIRED_SNAPSHOTS = (
    ("app/modules/account/recon.py", "sync_flags",
     "flags", "the device-reimbursement / sales recon flag sweep, on three endpoints"),
    ("app/modules/asset/invoice_due.py", "_sync_invoice_due_flags",
     "flags", "the invoice-due flag sweep, on its own endpoint"),
    ("app/modules/asset/router.py", "sync_inventory_flags",
     "flags", "the inventory flag sweep — no caller at all in backend/app today"),
    ("app/modules/asset/router.py", "_sync_hotsheet_flags",
     "flags", "the hotsheet flag sweep, on its own endpoint"),
)


def by_key(key):
    for b in BUILDS:
        if b["key"] == str(key):
            return b
    return None


def org_builds():
    """Every build this module claims and runs (commission is auto_calc's own path)."""
    return tuple(b for b in BUILDS if b["scope"] == SCOPE_ORG)


def static_inputs(build):
    return tuple(str(t) for t in (build.get("inputs") or ()))


def config_inputs(build):
    """The tables that DECLARE this build. Separate from `static_inputs` because a config table carries no
    lineage identity, and the lock holds each to its own rule."""
    return tuple(str(t) for t in (build.get("config_inputs") or ()))


def matches(build, table, declared=()):
    """Is `table` an input of `build`? PURE — the caller supplies whatever the declared resolver returned.

    A config table counts. A build is stale when the DECLARATION it reads changes, not only when a feed
    arrives: net terms of 14 with due dates still computed at 21 is a stale snapshot by any other name."""
    t = str(table or "").strip()
    if not t:
        return False
    return (t in static_inputs(build) or t in config_inputs(build)
            or t in tuple(str(x) for x in (declared or ())))


def _load(path):
    """'module:function' -> the function. LAZY by design: the runners import the whole app."""
    mod, _, fn = str(path).partition(":")
    import importlib
    return getattr(importlib.import_module(mod), fn)


def declared_inputs(client, org_id, build):
    """The DECLARED input tables for this org, or () when the build declares none.

    A resolver that raises is REPORTED (logged, and returned as the second element) rather than read as "this
    build has no declared inputs" — the latter would silently stop a landing from refreshing anything, which is
    the exact failure §COMMCALC-29 exists to stop.
    """
    path = build.get("inputs_fn")
    if not path:
        return (), None
    try:
        return tuple(str(t) for t in (_load(path)(client, org_id) or ()) if t), None
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"WARN derived-build declared inputs unreadable build={build['key']} org={org_id}: {msg}")
        return (), msg


def build_periods(client, org_id, build, now=None):
    """The months a DECLARATION edit of `build` restates: (periods, error).

    A feed landing names its own month. An edit does not, so the build names the months — through
    `periods_fn`, exactly as `inputs_fn` names declared inputs. Same posture as `declared_inputs`
    (§COMMCALC-29): a resolver that raises is REPORTED, never read as "this edit restates nothing",
    because nothing is the answer that looks like success and leaves the figures stale.
    """
    path = build.get("periods_fn")
    if not path:
        return (), None
    try:
        return tuple(str(p) for p in (_load(path)(client, org_id, now) or ()) if p), None
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"WARN derived-build restate months unreadable build={build['key']} org={org_id}: {msg}")
        return (), msg


def stale_builds(client, org_id, table):
    """Which ORG-scoped builds a landing of `table` makes stale: [(build, declared_error)]."""
    out = []
    for b in org_builds():
        declared, err = declared_inputs(client, org_id, b)
        if matches(b, table, declared):
            out.append((b, err))
    return out


def classify_build(result):
    """What a build runner returned -> (outcome, message). PURE.

    A runner returns {'status': 'done', 'written': n, 'warnings': [...]} or {'status': 'error', 'error': str}.
    `written` is NOT read as a verdict: a build that wrote zero rows and said so is done, and one that wrote
    zero rows because its insert failed says `error` (index §COMMCALC-40 — the status word is the verdict).
    """
    r = result if isinstance(result, dict) else {}
    status = str(r.get("status") or "").strip().lower()
    # A REFUSAL IS ITS OWN VERDICT, never a crash and never a success. A build refuses when the platform
    # cannot do what was asked and SAYS SO — the payable build's write preflight refusing because the
    # database is behind the code (§PAYABLES-01) is the live case. It is kept apart from `failed` because
    # the fix is a migration somebody has to apply, not a retry: `run_one` re-queues nothing, so a refusal
    # waits for the next landing rather than burning the debounce window in a loop.
    if status == "refused":
        msg = str(r.get("error") or "the build refused with no message").strip()
        return "refused", msg[:1200]
    if status == "done":
        warn = [str(w) for w in (r.get("warnings") or [])]
        if warn:
            return "built_with_warnings", "; ".join(warn)[:900]
        return "built", ""
    if status == "error":
        msg = str(r.get("error") or "the build stopped with no message").strip()
        return ("refused" if msg.upper().startswith("REFUSED") else "failed"), msg[:1200]
    return "failed", "the build returned no outcome"


def sentence(outcome, label, landings, message="", at=None, written=None, debounce_minutes=None, due=None):
    """The ONE sentence an operator reads for a build's state. PURE."""
    from app.modules.commcalc import auto_calc as _ac
    txt = _ac.landings_text(landings)
    cap = txt[0].upper() + txt[1:]
    when = _ac.fmt_time(at)
    rows = f" — {written:,} row(s)." if isinstance(written, int) else "."
    if outcome == "built":
        return f"{label.capitalize()} was rebuilt automatically at {when} after {txt}{rows}"
    if outcome == "built_with_warnings":
        return (f"{label.capitalize()} was rebuilt automatically at {when} after {txt}{rows[:-1]}, "
                f"with warnings: {message}")
    if outcome == "refused":
        return (f"{label.capitalize()} was NOT rebuilt at {when} after {txt}: {message} "
                f"The figures on screen are the ones from the last good build.")
    if outcome == "failed":
        return (f"{label.capitalize()} could not be rebuilt at {when} after {txt}: {message} "
                f"The figures on screen are the ones from the last good build.")
    if outcome == "running":
        return f"Rebuilding {label} now (started {when}) after {txt}."
    if outcome == "queued":
        return (f"{cap} landed at {when}. {label.capitalize()} rebuilds itself once uploads have been quiet "
                f"for {debounce_minutes} minute(s)" + (f" (about {_ac.fmt_time(due)})." if due else "."))
    if outcome == "off":
        return (f"{cap} landed at {when}, but automatic rebuilding is off for this company, so {label} does "
                f"not include it yet — press Rebuild.")
    return f"{cap}: {message}"


def record(outcome, build, landings, message="", at=None, started_at=None, written=None):
    """The outcome row stored in `derived_build_status.last`. PURE."""
    from app.modules.commcalc import auto_calc as _ac
    return {"outcome": outcome, "tone": _ac.TONE.get(outcome, "info"), "build": build["key"],
            "label": build["label"], "at": at, "started_at": started_at, "message": message or None,
            "written": written, "landings": list(landings or []),
            "sentence": sentence(outcome, build["label"], landings, message, at=at, written=written)}


# ── THE QUEUE — (org, build), mig 1100, with the same in-process fallback auto_calc has ─────────────────────
_STATE = {"ready": None, "checked": 0.0}
_STATE_LOCK = threading.Lock()
_MEM = {}                        # (org_id, build_key) -> {"requested_at": iso, "landings": [...]}
_MEM_LOCK = threading.Lock()
#: One build at a time per (org, build) INSIDE this process. The durable claim already keeps two processes
#: apart; this keeps two threads of one process from rebuilding the same snapshot at once, which for a
#: delete-then-insert build would interleave.
_RUNNING = set()
_RUNNING_LOCK = threading.Lock()


def state_ready(client, refresh=False):
    """Whether mig 1100's table exists. Re-probed every 10 minutes while absent, so applying the migration
    takes effect without a restart (auto_calc.state_ready's contract, for this table)."""
    import time
    with _STATE_LOCK:
        if not refresh and _STATE["ready"] is True:
            return True
        if not refresh and _STATE["ready"] is False and time.monotonic() - _STATE["checked"] < 600:
            return False
    try:
        (client.schema("commcalc").table(STATE_TABLE)
         .select(f"org_id,build_key,{COL_REQUESTED},{COL_LANDINGS},{COL_LAST}").limit(1).execute())
        ok = True
    except Exception:
        ok = False
    with _STATE_LOCK:
        _STATE.update(ready=ok, checked=time.monotonic())
    return ok


def queue(client, org_id, build_key, entries, now, durable):
    """Record that `entries` landed and this build is stale. Coalescing: ONE row per (org, build)."""
    from app.modules.commcalc import auto_calc as _ac
    if durable:
        cur = ((client.schema("commcalc").table(STATE_TABLE).select(f"org_id,build_key,{COL_LANDINGS}")
                .eq("org_id", org_id).eq("build_key", build_key).limit(1).execute().data) or [{}])[0]
        (client.schema("commcalc").table(STATE_TABLE).upsert(
            {"org_id": org_id, "build_key": build_key, COL_REQUESTED: _ac._iso(now),
             COL_LANDINGS: _ac.merge_landings(cur.get(COL_LANDINGS), entries)},
            on_conflict="org_id,build_key").execute())
        return
    with _MEM_LOCK:
        cur = _MEM.get((org_id, build_key)) or {}
        _MEM[(org_id, build_key)] = {"requested_at": _ac._iso(now),
                                     "landings": _ac.merge_landings(cur.get("landings"), entries)}


def record_last(client, org_id, build_key, rec, durable):
    """Store the outcome where an operator reads it. Never raises."""
    try:
        if durable:
            (client.schema("commcalc").table(STATE_TABLE).upsert(
                {"org_id": org_id, "build_key": build_key, COL_LAST: rec},
                on_conflict="org_id,build_key").execute())
        else:
            print(f"INFO derived-build outcome org={org_id} build={build_key}: {rec.get('sentence')}")
    except Exception as e:
        print(f"WARN derived-build outcome not recorded org={org_id} build={build_key}: {e}")


def pending(client, now, durable):
    """Every pending build request: [(org_id, build_key, requested_at, landings, durable)]."""
    from app.modules.commcalc import auto_calc as _ac
    out = []
    if durable:
        try:
            rows = (client.schema("commcalc").table(STATE_TABLE)
                    .select(f"org_id,build_key,{COL_REQUESTED},{COL_LANDINGS}")
                    .lte(COL_REQUESTED, _ac._iso(now))   # org-guard-ok: the scheduler scans request KEYS across orgs; each is then claimed and run org-scoped
                    .limit(500).execute().data) or []
        except Exception as e:
            print(f"WARN derived-build pending scan failed: {e}")
            rows = []
        out += [(r["org_id"], r["build_key"], r.get(COL_REQUESTED), r.get(COL_LANDINGS) or [], True)
                for r in rows if r.get(COL_REQUESTED) and r.get("org_id") and r.get("build_key")]
    with _MEM_LOCK:
        out += [(o, k, v["requested_at"], v["landings"], False) for (o, k), v in _MEM.items()]
    return out


def claim(client, org_id, build_key, requested_at, durable):
    """Take the request if nothing newer landed since we judged it due. One conditional UPDATE, so exactly one
    process runs it (auto_calc._claim's contract, keyed on the build)."""
    from app.modules.commcalc import auto_calc as _ac
    if durable:
        got = (client.schema("commcalc").table(STATE_TABLE)
               .update({COL_REQUESTED: None, COL_LANDINGS: None})
               .eq("org_id", org_id).eq("build_key", build_key)
               .lte(COL_REQUESTED, _ac._iso(_ac.parse_ts(requested_at)))
               .execute().data) or []
        return bool(got)
    with _MEM_LOCK:
        cur = _MEM.get((org_id, build_key))
        if not cur or cur["requested_at"] != requested_at:
            return False
        del _MEM[(org_id, build_key)]
        return True


def run_one(client, org_id, build_key, landings, *, durable, cfg=None, runner=None, now=None):
    """Run ONE claimed build and record what happened. The config is the SAME switch commission rides
    (`auto_calc.load_config`) — a tenant that wants attended rebuilding switches one thing off, not two."""
    from app.modules.commcalc import auto_calc as _ac
    build = by_key(build_key)
    if build is None:                        # a queued row for a build this version no longer has
        print(f"WARN derived-build unknown key org={org_id} build={build_key} — request dropped")
        return None
    cfg = cfg or _ac.load_config(client, org_id)
    started = _ac._iso(now or _ac._now())
    if not cfg["on"]:                        # switched off between the landing and the run
        rec = record("off", build, landings, at=started)
        record_last(client, org_id, build_key, rec, durable)
        return rec
    with _RUNNING_LOCK:
        if (org_id, build_key) in _RUNNING:
            queue(client, org_id, build_key, landings, _ac._now(), durable)
            print(f"INFO derived-build busy org={org_id} build={build_key} — re-queued")
            return None
        _RUNNING.add((org_id, build_key))
    try:
        record_last(client, org_id, build_key, record("running", build, landings, at=started), durable)
        try:
            result = (runner or _load(build["run"]))(client, org_id)
        except Exception as e:
            result = {"status": "error", "error": f"{type(e).__name__}: {e}"}
    finally:
        with _RUNNING_LOCK:
            _RUNNING.discard((org_id, build_key))
    outcome, message = classify_build(result)
    written = result.get("written") if isinstance(result, dict) else None
    rec = record(outcome, build, landings, message, at=_ac._iso(_ac._now()), started_at=started,
                 written=written if isinstance(written, int) else None)
    record_last(client, org_id, build_key, rec, durable)
    print(f"INFO derived-build org={org_id} build={build_key} outcome={outcome} {message[:200]}")
    return rec


def run_due(client, now=None, runner=None):
    """Claim and run every build request whose quiet window has passed. Called by auto_calc's poller — this
    module starts no thread of its own."""
    from app.modules.commcalc import auto_calc as _ac
    now = now or _ac._now()
    durable = state_ready(client)
    cfgs, out = {}, []
    for org_id, build_key, requested_at, landings, is_durable in pending(client, now, durable):
        cfg = cfgs.get(org_id) or cfgs.setdefault(org_id, _ac.load_config(client, org_id))
        if not _ac.is_due(requested_at, landings, cfg["debounce_minutes"], now):
            continue
        try:
            got = claim(client, org_id, build_key, requested_at, is_durable)
        except Exception as e:
            print(f"WARN derived-build claim failed org={org_id} build={build_key}: {e}")
            continue
        if not got:
            continue
        rec = run_one(client, org_id, build_key, landings, durable=is_durable, cfg=cfg, runner=runner, now=now)
        if rec:
            out.append(rec)
    return out


def view(row, cfg=None, now=None):
    """What a screen shows for one build: the queued sentence while a request is pending, else the last
    outcome. PURE (mirrors auto_calc.view, for a build row)."""
    from app.modules.commcalc import auto_calc as _ac
    row = row or {}
    build = by_key(row.get("build_key")) or {"label": "this report"}
    deb = (cfg or {}).get("debounce_minutes") or _ac.DEFAULT_DEBOUNCE_MINUTES
    landings = row.get(COL_LANDINGS) or []
    last = row.get(COL_LAST) if isinstance(row.get(COL_LAST), dict) else None
    out = {"build": row.get("build_key"), "label": build.get("label"), "state": None, "tone": None,
           "sentence": None, "last": last, "enabled": (cfg or {}).get("on") if cfg else None}
    if row.get(COL_REQUESTED):
        due = _ac.due_at(row[COL_REQUESTED], landings, deb)
        out.update(state="queued", tone="info", due_at=(_ac._iso(due) if due else None), landings=landings,
                   sentence=sentence("queued", build.get("label"), landings, at=row[COL_REQUESTED],
                                     debounce_minutes=deb, due=(_ac._iso(due) if due else None)))
    elif last:
        out.update(state=last.get("outcome"), tone=last.get("tone"), sentence=last.get("sentence"))
    return out


def read_view(client, org_id, build_key, cfg=None):
    """One build's state for an org, read from wherever it lives. Never raises."""
    from app.modules.commcalc import auto_calc as _ac
    cfg = cfg or _ac.load_config(client, org_id)
    if state_ready(client):
        try:
            rows = (client.schema("commcalc").table(STATE_TABLE).select("*")
                    .eq("org_id", org_id).eq("build_key", build_key).limit(1).execute().data) or []
            if rows:
                return view(rows[0], cfg)
        except Exception as e:
            print(f"WARN derived-build state read failed org={org_id} build={build_key}: {e}")
    with _MEM_LOCK:
        cur = _MEM.get((org_id, build_key))
    if cur:
        return view({"build_key": build_key, COL_REQUESTED: cur["requested_at"],
                     COL_LANDINGS: cur["landings"]}, cfg)
    b = by_key(build_key) or {}
    return {"build": build_key, "label": b.get("label"), "state": None, "tone": None, "sentence": None,
            "last": None, "enabled": cfg.get("on")}
