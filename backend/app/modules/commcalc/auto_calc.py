"""AUTO-CALCULATION ON LANDING — the ONE home of "data landed for org X, period P" (index §6l).

Owner, 2026-09-28: *"when sept is uploaded the system should calculate automatically without manual
intervention"* — platform-wide.

THE CLASS, NOT THE INSTANCE. Before this module, three separate things decided whether a landing recalculated:
the DLAR sweep called `_run_calculation` inline on every run, the email sweep called it after a feed promotion
(plan-mode tenants only, current month only), and every other lander — the upload pages, the onboarding intake,
the FTP drop, the store-guard release, the POS sync, the portal pulls — recalculated nothing. Three answers to
one question, which drift. This module is the only answer:

  • every lander calls `landed(client, org_id, table=…, periods=…, source=…)` after it has WRITTEN rows;
  • `landed` looks the table up in the registry (`data_lineage_registry.COMMISSION_CALC_FEEDS` — the tables the
    Run Calculation reads) and, when the org's config says so, QUEUES one request per (org, period) touched —
    a late correction to an older month queues that month too;
  • a background poller (one daemon thread per process, started at app boot) claims each request once uploads
    for that (org, period) have been quiet for the org's debounce window, and runs THE standard full-period
    Run Calculation — `router._run_calculation(period, org_id)`, all reps — through `_default_runner`. Every
    guard it has stays armed: the unconfigured-tenant refusal (R1), the zero-wipe guard, the single-flight
    recompute guard (no token is passed, so it claims its own slot) — `force` is never passed;
  • the outcome — calculated / refused / failed / waiting / off — is RECORDED on `commcalc.calc_status` for that
    (org, period) (`auto_calc_last`, mig 1030) and served by `GET /calc-status/{period}` as `auto_calc`, which the
    Rep Incentive page renders. A refusal is never swallowed: `_run_calculation` also keeps writing its own
    `calc_status='error'` + `save_errors`, which the CommCalc dashboard already shows.

DEBOUNCE / COALESCE. A request is ONE row per (org, period): `auto_calc_requested_at` (the latest landing) and
`auto_calc_landings` (what landed). Thirty daily files uploaded in a burst move the timestamp thirty times and run
ONE calculation, after the quiet window; a steady stream cannot starve it (`MAX_WAIT_FACTOR`). The claim is one
conditional UPDATE (`requested_at <= the value we judged due`), so across gunicorn workers and the sweeps service
exactly one process runs it, and a landing that arrives while a calculation runs queues exactly one follow-up.
IDEMPOTENT: the Run Calculation is a deterministic delete-and-rewrite of the period from what has landed, so
landing the same data again recalculates to the same rows.

CONFIG, NEVER CODE (RULE TWO). `commission_org_config.auto_calc_on_landing` / `.auto_calc_debounce_minutes`
(mig 1030): the tenant's row wins, else the house org's row, else the code default. `load_config` is the ONLY
reader (the lock fails the build on a second). House default ON — see `DEFAULT_ON`.

NO PERIOD LOCK EXISTS. The index has no locked / finalized / paid state for a rep-commission period (the only
draft→approved→paid lifecycle is the Management Incentive's `mi_payout`, which the Run Calculation never writes).
So a late correction to a paid month recalculates it — exactly as a manual Run Calculation would. Index §6l / §19
carries this as an open gap: when a period lock is built it belongs in `run_one`, before the runner.

MIGRATION 1030 NOT APPLIED → the queue degrades to THIS PROCESS (`_MEM`): the same debounce and one calculation
per process, the outcome recorded as a `calc_notices` entry of type `auto_calc` (mig 247, applied). So the two
auto-recalcs that existed before this module (DLAR sweep, email promotion) never stop while the SQL is pending.

Stdlib + two pure modules at import time — safe to import from anywhere (the router, the sweeps, the POS).
"""
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from app.modules.commcalc import data_lineage_registry as _lineage
from app.modules.account import _period as _pd

HOUSE_ORG = "00000000-0000-0000-0000-000000000001"
MIGRATION = "1030_auto_calc_on_landing.sql"

# ── THE CONFIG — one table, two keys, ONE reader (`load_config`). ────────────────────────────────────────────
CONFIG_TABLE = "commission_org_config"
CONFIG_ON = "auto_calc_on_landing"
CONFIG_DEBOUNCE = "auto_calc_debounce_minutes"
# HOUSE DEFAULT = ON. (1) The owner's directive is platform-wide and the house org is theirs. (2) It is what the
# platform already did de facto: every Boost tenant was recalculated by the daily DLAR sweep and every plan-mode
# tenant by each email-sweep promotion — those two triggers are now THIS hook, so a default of OFF would silently
# switch off recalculations tenants already rely on. (3) Nothing new can move money unasked: the run is the same
# Run Calculation a person presses, with every refusal guard armed, and its outcome is shown on the page. A tenant
# that wants attended recalculation sets its row to false.
DEFAULT_ON = True
DEFAULT_DEBOUNCE_MINUTES = 5
DEBOUNCE_BOUNDS = (1, 240)
# A steady stream of landings (an hourly feed during a long import) must not postpone the calculation forever:
# once the OLDEST pending landing is this many debounce windows old, the request is due regardless.
MAX_WAIT_FACTOR = 6

# ── THE STATE — on commcalc.calc_status (UNIQUE org_id, period), mig 1030. ───────────────────────────────────
STATE_TABLE = "calc_status"
COL_REQUESTED = "auto_calc_requested_at"
COL_LANDINGS = "auto_calc_landings"
COL_LAST = "auto_calc_last"
LANDINGS_CAP = 40
NOTICE_TYPE = "auto_calc"          # the calc_notices entry used when mig 1030 is not applied
POLL_SECONDS = 30

# Plain words for where a landing came from (the lander's own trace-source word → a phrase an owner reads).
SOURCE_LABELS = {
    "manual": "the Upload page",
    "email_sweep": "Email Auto-Import",
    "ftp_sweep": "the FTP drop",
    "onboarding-import": "the mapped upload",
    "onboarding-intake": "the onboarding intake",
    "custom-binding": "a custom report",
    "promotion": "the daily-feed to monthly-sales derivation",
    "dlar_sweep": "the carrier KPI (DLAR) sweep",
    "epay_sweep": "the ePay portal sweep",
    "portal_pull": "a portal pull",
    "pos_module": "the built-in POS sync",
    "ingest_guard_release": "rows released from the store-guard review",
    "ma_manual": "the master-agent manual upload",
    "import-wizard": "the commission import wizard",
}

# ── THE LANDERS — every function that writes rows into a table the calculation reads (or a sales sibling of
#    one). Each MUST call `landed(`; `harness_auto_calc_lock.py` discovers every writer in backend/app and fails
#    the build on one that is in neither list, on a listed lander that stops calling the hook, and on a stale
#    entry. (path relative to backend/, function name, what lands through it)
LANDERS = (
    ("app/modules/commcalc/router.py", "_upload_file_impl",
     "POST /upload/{file_type} — the Sales / Daily Sales / DLAR / MI / payment-detail / MA upload pages, AND the "
     "Email Auto-Import and FTP drop (both call upload_file per attachment)"),
    ("app/modules/commcalc/router.py", "_ingest_mapped_df",
     "the mapped ingest core — /upload-mapped (sales-by-invoice, POS line sales, any carrier layout), the "
     "onboarding intake commit (sales / pos / invoice / MA daily tx), a custom report's dataset binding"),
    ("app/modules/commcalc/router.py", "_promote_feed_impl",
     "the daily-feed → raw_sales derivation (email-sweep auto-promote, month-boundary grace, "
     "POST /sales/promote-feed, the hourly promote-all-due)"),
    ("app/modules/commcalc/router.py", "decide_ingest_guard_item",
     "rows the cross-tenant store guard withheld, released into raw_sales / daily_sales_feed on 'allow'"),
    ("app/modules/commcalc/router.py", "commission_import_commit",
     "the commission import wizard → carrier_commission (read by the calculation's statement block)"),
    ("app/modules/commcalc/router.py", "manual_upload_ingest",
     "the master-agent manual upload → raw_ma_commission / raw_ma_daily_tx (the sale-installment paid gate)"),
    ("app/modules/commcalc/dlar_sweep.py", "run_dlar_sweep",
     "the carrier KPI portal sweep → raw_dlar_rep / raw_dlar_store (its inline recalculation is retired)"),
    ("app/modules/commcalc/epay_sweep.py", "_process_report",
     "the ePay portal sweep, month grain → raw_mi / raw_payment_detail"),
    ("app/modules/commcalc/epay_sweep.py", "_store_day_grain",
     "the ePay portal sweep, day grain (comp report)"),
    ("app/modules/commcalc/report_pull.py", "ingest_report_rows",
     "every portal pull (VidaPay / T-CETRA / Total Access) → raw_ma_* (vidapay_sweep._pull_one_report)"),
    ("app/modules/pos/commcalc_feed.py", "sync_period",
     "the built-in POS promotion into daily_sales_feed / raw_sales (builtin-primary tenants)"),
)

# Writers the discovery finds whose table it cannot resolve statically (a parameter, a loop variable) and that
# are NOT landings of calculation input — each with the reason, so a reader can check it. Anything else the lock
# discovers must be classified before the build is green; an entry that no longer writes is stale (RED).
EXCUSED = (
    ("app/modules/commcalc/router.py", "_restore_rows",
     "puts a snapshot BACK after a failed insert — nothing new landed, the period is as it was before the attempt"),
    ("app/modules/commcalc/router.py", "upload_vip_invoices", "VIP invoice tables — not calculation input"),
    ("app/modules/commcalc/vip_sweep.py", "run_invoice_sweep", "VIP invoice tables — not calculation input"),
    ("app/modules/commcalc/router.py", "mi_save_plan", "Management Incentive plan config — not a feed"),
    ("app/modules/commcalc/management_incentive_seed.py", "seed_management_incentive_defaults",
     "Management Incentive seed rows — not a feed"),
    ("app/modules/closing/router.py", "_record_deposit_impl", "daily-closing deposit tables — not a feed"),
    ("app/modules/closing/router.py", "_confirm_pickup_impl", "daily-closing pickup tables — not a feed"),
    ("app/modules/closing/router.py", "_put_pickup_config_impl", "cash-pickup notification config — not a feed"),
    ("app/modules/pos/commcalc_feed.py", "_replace_period",
     "the built-in POS OWN stream (pos_builtin_*), which the calculation never reads; the promotion into the "
     "feed is sync_period, a lander"),
)

# Paths an owner would expect to be landers that were REVIEWED and provably write no calculation input (the lock
# requires each to exist and to stay OUT of the writer discovery — the day one starts writing a feed, it is RED
# until it is wired and moved to LANDERS).
REVIEWED_NON_LANDERS = (
    ("app/modules/pos/sales_from_reports.py", "rebuild",
     "\"Rebuild sales from the landed reports\" writes pos.receipt_imports / pos.sales, which the calculation never "
     "reads; the two reports it rebuilds from landed through _ingest_mapped_df (the intake commit), which already "
     "fired the hook"),
    ("app/modules/commcalc/router.py", "_ingest_custom_report",
     "writes raw_custom_import (not read by the calculation); a custom report bound to a dataset lands through "
     "_ingest_mapped_df, which calls the hook"),
    ("app/modules/commcalc/ingest_store_guard.py", "record",
     "parks WITHHELD rows in the review queue — nothing reaches a feed until decide_ingest_guard_item (a lander) "
     "releases it"),
    ("app/modules/commcalc/vidapay_sweep.py", "_pull_one_report",
     "lands through report_pull.ingest_report_rows, which calls the hook itself"),
    ("app/modules/commcalc/epay_ingest.py", "ingest",
     "raw_epay_daily_tx — the bill-pay processor feed; the Run Calculation does not read it"),
    ("app/modules/commcalc/router.py", "_ledger_land_rows",
     "commission_ledger — the carrier statement ledger books the P&L; the Run Calculation does not read it"),
)

# The ONLY places that may start a Run Calculation: the Run Calculation button (POST /calculate) and this
# module's runner. A third is a second auto-calc trigger — the lock fails the build on it.
CALC_STARTERS = (
    ("app/modules/commcalc/router.py", "calculate"),
    ("app/modules/commcalc/auto_calc.py", "_default_runner"),
)


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════
# PURE — config, periods, due-ness, outcomes, sentences. No client, no clock (the caller passes `now`).
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════
def _truthy(v):
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "true", "t", "yes", "y", "on"):
        return True
    if s in ("0", "false", "f", "no", "n", "off"):
        return False
    return None


def resolve_config(tenant_row, house_row):
    """The effective setting for an org: its own row's value wins, else the house org's, else the code default.
    Each key resolves on its own (a tenant may set only the switch and inherit the window). PURE."""
    def pick(key):
        for src, row in (("tenant", tenant_row), ("house", house_row)):
            v = (row or {}).get(key)
            if v is not None and str(v).strip() != "":
                return v, src
        return None, "default"

    on_raw, on_src = pick(CONFIG_ON)
    on = _truthy(on_raw) if on_raw is not None else None
    if on is None:
        on, on_src = DEFAULT_ON, "default"
    deb_raw, deb_src = pick(CONFIG_DEBOUNCE)
    try:
        deb = int(float(deb_raw)) if deb_raw is not None else DEFAULT_DEBOUNCE_MINUTES
    except (TypeError, ValueError):
        deb, deb_src = DEFAULT_DEBOUNCE_MINUTES, "default"
    lo, hi = DEBOUNCE_BOUNDS
    return {"on": bool(on), "on_source": on_src,
            "debounce_minutes": min(hi, max(lo, deb)), "debounce_source": deb_src}


def is_calc_feed(table):
    """True when `table` is one the Run Calculation reads — the registry's answer, never a list here."""
    return str(table or "").strip() in _lineage.COMMISSION_CALC_FEEDS


def month_periods(periods):
    """The distinct month-periods a landing touched, in the ONE stored spelling ('September 2026'), oldest first.
    A value that is not a month-period is dropped (never guessed). PURE."""
    if isinstance(periods, str):
        periods = [periods]
    out = {}
    for p in (periods or []):
        raw = str(p or "").strip()
        pm, py = _pd.parse_period(raw)
        if 1 <= pm <= 12 and py:
            out[(py, pm)] = _pd.canonical_period(raw)
    return [out[k] for k in sorted(out)]


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_ts(v):
    """A stored timestamp (PostgREST ISO, 'Z' or offset) → aware UTC datetime; None when absent/unreadable."""
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v or "").strip()
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def landing_entry(source, table, filename=None, rows=None, at=None):
    """One landing, as recorded on the request and shown on the page."""
    return {"at": at, "source": str(source or "").strip() or "unknown", "table": str(table or ""),
            "filename": (str(filename).strip() or None) if filename else None,
            "rows": (int(rows) if isinstance(rows, (int, float)) else None)}


def merge_landings(existing, new, cap=LANDINGS_CAP):
    """The request's landing list after `new` arrive: oldest first; the FIRST landing is always kept (it anchors
    the max-wait), then the most recent ones up to `cap`. PURE."""
    allv = [x for x in (existing or []) if isinstance(x, dict)] + [x for x in (new or []) if isinstance(x, dict)]
    if len(allv) <= cap:
        return allv
    return [allv[0]] + allv[-(cap - 1):]


def first_landing_at(landings):
    ts = [parse_ts(x.get("at")) for x in (landings or []) if isinstance(x, dict)]
    ts = [t for t in ts if t]
    return min(ts) if ts else None


def due_at(requested_at, landings, debounce_minutes):
    """When a pending request becomes runnable: `debounce` after the LATEST landing, but never later than
    `MAX_WAIT_FACTOR × debounce` after the OLDEST one. PURE."""
    req = parse_ts(requested_at)
    if req is None:
        return None
    deb = timedelta(minutes=debounce_minutes)
    quiet = req + deb
    first = first_landing_at(landings)
    if first is not None:
        return min(quiet, first + deb * MAX_WAIT_FACTOR)
    return quiet


def is_due(requested_at, landings, debounce_minutes, now):
    d = due_at(requested_at, landings, debounce_minutes)
    return d is not None and now >= d


def classify_run(result):
    """What a Run Calculation returned → (outcome, message). `_run_calculation` returns
    {'status': 'done', 'save_errors', 'reps'} | {'status': 'error', 'error'} | {'skipped': 'already_running', …}.
    A refusal is an 'error' whose message starts REFUSED (R1 / zero-wipe) — kept apart from a crash. PURE."""
    r = result if isinstance(result, dict) else {}
    if r.get("skipped") == "already_running":
        return "busy", f"a calculation for this month was already running (started {r.get('running_since') or 'just now'})"
    status = str(r.get("status") or "").strip().lower()
    if status == "done":
        errs = r.get("save_errors") or []
        if errs:
            return "calculated_with_errors", "; ".join(str(e) for e in errs)[:600]
        return "calculated", ""
    if status == "error":
        msg = str(r.get("error") or "the calculation stopped with no message").strip()
        return ("refused" if msg.upper().startswith("REFUSED") else "failed"), msg[:1200]
    return "failed", "the calculation returned no outcome"


TONE = {"calculated": "ok", "calculated_with_errors": "warn", "refused": "error", "failed": "error",
        "busy": "info", "off": "warn", "running": "info", "queued": "info"}


def fmt_time(v):
    t = parse_ts(v)
    return t.strftime("%b %d, %Y %H:%M UTC") if t else "an unknown time"


def _label(source):
    return SOURCE_LABELS.get(str(source or ""), str(source or "an import"))


def landings_text(landings):
    """What landed, in plain words: 'the upload of sept.xlsx (Email Auto-Import)' / '3 uploads (…)'. PURE."""
    ls = [x for x in (landings or []) if isinstance(x, dict)]
    if not ls:
        return "a data landing"

    def one(x):
        what = f"the upload of {x['filename']}" if x.get("filename") else f"the {x.get('table') or 'data'} landing"
        return f"{what} ({_label(x.get('source'))})"
    if len(ls) == 1:
        return one(ls[0])
    counts = {}
    for x in ls:
        counts[_label(x.get("source"))] = counts.get(_label(x.get("source")), 0) + 1
    by = ", ".join(f"{k} ×{n}" if n > 1 else k for k, n in counts.items())
    return f"{len(ls)} uploads ({by}); the latest was {one(ls[-1])}"


def sentence(outcome, period, landings, message="", at=None, reps=None, debounce_minutes=None, due=None):
    """The ONE sentence the Rep Incentive page shows for a state. PURE."""
    txt = landings_text(landings)
    cap = txt[0].upper() + txt[1:]
    when = fmt_time(at)
    if outcome == "calculated":
        return (f"Auto-calculated at {when} from {txt}"
                + (f" — {reps} rep(s)." if isinstance(reps, int) else "."))
    if outcome == "calculated_with_errors":
        return f"Auto-calculated at {when} from {txt}, but part of it did not save: {message}"
    if outcome == "refused":
        return (f"Auto-calculation refused at {when} after {txt}: {message} "
                f"The last good snapshot for {period} was kept.")
    if outcome == "failed":
        return f"Auto-calculation failed at {when} after {txt}: {message}"
    if outcome == "busy":
        return (f"Auto-calculation waiting — {txt} landed, but {message}. "
                f"It runs again automatically once that one finishes.")
    if outcome == "off":
        return (f"{cap} landed at {when}. Auto-calculation is off for this company, so the stored commission "
                f"for {period} does not include it yet — press Run Calculation.")
    if outcome == "running":
        return f"Auto-calculating {period} now (started {when}) from {txt}."
    if outcome == "queued":
        return (f"{cap} landed at {when}. Auto-calculation is queued — it runs once uploads for {period} have "
                f"been quiet for {debounce_minutes} minute(s)" + (f" (about {fmt_time(due)})." if due else "."))
    return f"{cap}: {message}"


def record(outcome, period, landings, message="", at=None, started_at=None, reps=None):
    """The outcome row stored in `calc_status.auto_calc_last` — what happened, when, from which landings. PURE."""
    return {"outcome": outcome, "tone": TONE.get(outcome, "info"), "period": period, "at": at,
            "started_at": started_at, "message": message or None, "reps": reps,
            "landings": list(landings or []),
            "sentence": sentence(outcome, period, landings, message, at=at, reps=reps)}


def notices_with(notices, rec):
    """calc_notices with this module's entry replaced by `rec` (the pre-1030 home of the outcome). PURE."""
    keep = [n for n in (notices or []) if not (isinstance(n, dict) and n.get("type") == NOTICE_TYPE)]
    return keep + [{"type": NOTICE_TYPE, "severity": {"ok": "info", "error": "error"}.get(rec.get("tone"), "warning"),
                    "message": rec.get("sentence"), "auto_calc": rec}]


def view(row, cfg=None, now=None):
    """What `GET /calc-status/{period}` serves as `auto_calc` — the page renders `sentence` + `tone`. PURE."""
    row = row or {}
    deb = (cfg or {}).get("debounce_minutes") or DEFAULT_DEBOUNCE_MINUTES
    pending = row.get(COL_REQUESTED)
    landings = row.get(COL_LANDINGS) or []
    last = row.get(COL_LAST) if isinstance(row.get(COL_LAST), dict) else None
    if last is None:
        for n in (row.get("calc_notices") or []):
            if isinstance(n, dict) and n.get("type") == NOTICE_TYPE and isinstance(n.get("auto_calc"), dict):
                last = n["auto_calc"]
    out = {"state": None, "tone": None, "sentence": None, "last": last,
           "enabled": (cfg or {}).get("on") if cfg else None}
    if pending:
        due = due_at(pending, landings, deb)
        out.update(state="queued", tone="info", due_at=(_iso(due) if due else None), landings=landings,
                   sentence=sentence("queued", row.get("period"), landings, at=pending,
                                     debounce_minutes=deb, due=(_iso(due) if due else None)))
    elif last:
        out.update(state=last.get("outcome"), tone=last.get("tone"), sentence=last.get("sentence"))
    return out


def phrase(result):
    """A sweep status line's clause for a `landed(...)` result — never claims a recalculation that did not queue."""
    r = result if isinstance(result, dict) else {}
    ps = ", ".join(r.get("periods") or []) or "the period"
    if r.get("queued"):
        return f"auto-calculation queued for {ps}"
    reason = r.get("reason")
    if reason == "off":
        return f"auto-calculation is off for this company — {ps} not recalculated"
    if reason in ("not_a_calc_input", "no_period", "nothing_landed"):
        return "no calculation input changed"
    return f"⚠ auto-calculation could not be queued: {r.get('error') or reason or 'unknown'}"


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════
# IMPURE — the config read, the queue, the claim, the run. Every query is org-scoped except the scheduler's
# own scan, which reads pending request KEYS across orgs and then handles each org-scoped.
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════
def _now():
    return datetime.now(timezone.utc)


def load_config(client, org_id):
    """THE one reader of the auto-calc config: this org's commission_org_config row and the house org's, read
    WHOLE (`select('*')`, the §4b.1 reading rule — a column not yet migrated is simply absent). Never raises."""
    rows = []
    try:
        rows = (client.schema("commcalc").table(CONFIG_TABLE).select("*")
                .in_("org_id", list(dict.fromkeys([org_id, HOUSE_ORG]))).execute().data) or []
    except Exception as e:                                    # table absent / transient → the code default
        print(f"WARN auto-calc config read failed for org={org_id}: {e}")
    tenant = next((r for r in rows if str(r.get("org_id")) == str(org_id)), None)
    house = next((r for r in rows if str(r.get("org_id")) == HOUSE_ORG), None) if org_id != HOUSE_ORG else None
    return resolve_config(tenant, house)


_STATE = {"ready": None, "checked": 0.0}
_STATE_LOCK = threading.Lock()


def state_ready(client, refresh=False):
    """Whether mig 1030's columns exist (the durable, cross-process queue). Re-probed every 10 minutes while
    absent, so applying the migration takes effect without a restart."""
    with _STATE_LOCK:
        if not refresh and _STATE["ready"] is True:
            return True
        if not refresh and _STATE["ready"] is False and time.monotonic() - _STATE["checked"] < 600:
            return False
    try:
        (client.schema("commcalc").table(STATE_TABLE)
         .select(f"org_id,{COL_REQUESTED},{COL_LANDINGS},{COL_LAST}").limit(1).execute())
        ok = True
    except Exception:
        ok = False
    with _STATE_LOCK:
        _STATE.update(ready=ok, checked=time.monotonic())
    return ok


_MEM = {}                  # (org_id, period) -> {"requested_at": iso, "landings": [...]}  (pre-1030 fallback)
_MEM_LOCK = threading.Lock()


def _read_row(client, org_id, period, cols):
    rows = (client.schema("commcalc").table(STATE_TABLE).select(cols)
            .eq("org_id", org_id).eq("period", period).limit(1).execute().data) or []
    return rows[0] if rows else {}


def _queue(client, org_id, period, entries, now, durable):
    if durable:
        cur = _read_row(client, org_id, period, f"org_id,period,{COL_LANDINGS}")
        merged = merge_landings(cur.get(COL_LANDINGS), entries)
        (client.schema("commcalc").table(STATE_TABLE).upsert(
            {"org_id": org_id, "period": period, COL_REQUESTED: _iso(now), COL_LANDINGS: merged},
            on_conflict="org_id,period").execute())
        return
    with _MEM_LOCK:
        cur = _MEM.get((org_id, period)) or {}
        _MEM[(org_id, period)] = {"requested_at": _iso(now),
                                  "landings": merge_landings(cur.get("landings"), entries)}


def _record_last(client, org_id, period, rec, durable):
    """Store the outcome where operators read it. Never raises (a failed record is logged, and the Run
    Calculation's own calc_status / save_errors stamp still stands)."""
    try:
        if durable:
            (client.schema("commcalc").table(STATE_TABLE).upsert(
                {"org_id": org_id, "period": period, COL_LAST: rec}, on_conflict="org_id,period").execute())
            return
        cur = _read_row(client, org_id, period, "org_id,period,calc_notices")
        (client.schema("commcalc").table(STATE_TABLE).upsert(
            {"org_id": org_id, "period": period, "calc_notices": notices_with(cur.get("calc_notices"), rec)},
            on_conflict="org_id,period").execute())
    except Exception as e:
        print(f"WARN auto-calc outcome not recorded org={org_id} period={period} ({rec.get('outcome')}): {e}")


def landed(client, org_id, *, table, periods, source, filename=None, rows=None, now=None):
    """THE post-landing hook. Every lander calls it AFTER its rows are written. Queues one Run Calculation per
    (org, month) touched when `table` is calculation input and the org's config is on; records 'off' when it is
    not. Returns what it did (landers put it in their response). NEVER raises into a lander."""
    try:
        if not org_id:
            return {"queued": False, "reason": "no_org"}
        if rows is not None and not rows:
            return {"queued": False, "reason": "nothing_landed", "table": table}
        if not is_calc_feed(table):
            return {"queued": False, "reason": "not_a_calc_input", "table": table}
        ps = month_periods(periods)
        if not ps:
            return {"queued": False, "reason": "no_period", "table": table}
        now = now or _now()
        cfg = load_config(client, org_id)
        entry = landing_entry(source, table, filename, rows, _iso(now))
        durable = state_ready(client)
        if not cfg["on"]:
            for p in ps:
                _record_last(client, org_id, p, record("off", p, [entry], at=_iso(now)), durable)
            return {"queued": False, "reason": "off", "periods": ps, "config": cfg,
                    "sentence": sentence("off", ", ".join(ps), [entry], at=_iso(now))}
        for p in ps:
            _queue(client, org_id, p, [entry], now, durable)
        return {"queued": True, "periods": ps, "mode": "durable" if durable else "process",
                "debounce_minutes": cfg["debounce_minutes"],
                "sentence": sentence("queued", ", ".join(ps), [entry], at=_iso(now),
                                     debounce_minutes=cfg["debounce_minutes"])}
    except Exception as e:
        print(f"WARN auto-calc hook failed org={org_id} table={table} periods={periods}: {e}")
        return {"queued": False, "reason": "error", "error": f"{type(e).__name__}: {e}"}


def _pending(client, now, durable):
    """Every pending request: [(org_id, period, requested_at, landings, durable)]."""
    out = []
    if durable:
        try:
            rows = (client.schema("commcalc").table(STATE_TABLE)
                    .select(f"org_id,period,{COL_REQUESTED},{COL_LANDINGS}")
                    .lte(COL_REQUESTED, _iso(now))   # org-guard-ok: the scheduler scans request KEYS across orgs; each is then claimed and run org-scoped
                    .limit(500).execute().data) or []
        except Exception as e:
            print(f"WARN auto-calc pending scan failed: {e}")
            rows = []
        out += [(r["org_id"], r["period"], r.get(COL_REQUESTED), r.get(COL_LANDINGS) or [], True)
                for r in rows if r.get(COL_REQUESTED) and r.get("org_id") and r.get("period")]
    with _MEM_LOCK:
        out += [(o, p, v["requested_at"], v["landings"], False) for (o, p), v in _MEM.items()]
    return out


def _claim(client, org_id, period, requested_at, durable):
    """Take the request if nothing newer landed since we judged it due → True. One conditional UPDATE: a second
    process, or a landing that moved the timestamp forward in the meantime, matches zero rows → False. (Every
    landing sets the timestamp to its own `now`, so `requested_at <= the value we read` ⇔ unchanged.)"""
    if durable:
        got = (client.schema("commcalc").table(STATE_TABLE)
               .update({COL_REQUESTED: None, COL_LANDINGS: None})
               .eq("org_id", org_id).eq("period", period)
               .lte(COL_REQUESTED, _iso(parse_ts(requested_at)))
               .execute().data) or []
        return bool(got)
    with _MEM_LOCK:
        cur = _MEM.get((org_id, period))
        if not cur or cur["requested_at"] != requested_at:
            return False
        del _MEM[(org_id, period)]
        return True


def _default_runner(period, org_id):
    """THE standard full-period Run Calculation, all reps — the very function the Run Calculation button runs.
    No `force` (R1 + zero-wipe stay armed) and no `guard_token` (it claims the single-flight slot itself)."""
    from app.modules.commcalc.router import _run_calculation
    return _run_calculation(period, org_id)


def run_one(client, org_id, period, landings, *, durable, cfg=None, runner=None, now=None):
    """Run ONE claimed request and record what happened. A 'busy' outcome (another calculation holds the slot)
    re-queues the same landings so the data that landed is still calculated once that run finishes."""
    runner = runner or _default_runner
    cfg = cfg or load_config(client, org_id)
    started = _iso(now or _now())
    if not cfg["on"]:                                   # switched off between the landing and the run
        rec = record("off", period, landings, at=started)
        _record_last(client, org_id, period, rec, durable)
        return rec
    _record_last(client, org_id, period, record("running", period, landings, at=started), durable)
    try:
        result = runner(period, org_id)
    except Exception as e:                              # the runner never raises today; if it ever does, say so
        result = {"status": "error", "error": f"{type(e).__name__}: {e}"}
    outcome, message = classify_run(result)
    reps = result.get("reps") if isinstance(result, dict) else None
    rec = record(outcome, period, landings, message, at=_iso(_now()), started_at=started, reps=reps)
    _record_last(client, org_id, period, rec, durable)
    if outcome == "busy":
        try:
            _queue(client, org_id, period, landings, _now(), durable)
        except Exception as e:
            print(f"WARN auto-calc re-queue failed org={org_id} period={period}: {e}")
    print(f"INFO auto-calc org={org_id} period={period} outcome={outcome} {message[:200]}")
    return rec


def run_due(client, now=None, runner=None):
    """Claim and run every request whose quiet window has passed. Called by the poller every POLL_SECONDS."""
    now = now or _now()
    durable = state_ready(client)
    cfgs, out = {}, []
    for org_id, period, requested_at, landings, is_durable in _pending(client, now, durable):
        cfg = cfgs.get(org_id) or cfgs.setdefault(org_id, load_config(client, org_id))
        if not is_due(requested_at, landings, cfg["debounce_minutes"], now):
            continue
        try:
            claimed = _claim(client, org_id, period, requested_at, is_durable)
        except Exception as e:
            print(f"WARN auto-calc claim failed org={org_id} period={period}: {e}")
            continue
        if not claimed:
            continue
        out.append(run_one(client, org_id, period, landings, durable=is_durable, cfg=cfg,
                           runner=runner, now=now))
    return out


_POLLER = {"pid": None, "thread": None}
_POLLER_LOCK = threading.Lock()


def start_poller(client_factory=None):
    """Start this process's poller (app startup). Idempotent per process; `AUTO_CALC_POLLER=0` disables it
    (a harness, a one-off script). A request queued before a restart is picked up by the next boot's poller."""
    if str(os.environ.get("AUTO_CALC_POLLER", "1")).strip().lower() in ("0", "false", "no", "off"):
        return "disabled (AUTO_CALC_POLLER=0)"
    with _POLLER_LOCK:
        t = _POLLER["thread"]
        if _POLLER["pid"] == os.getpid() and t is not None and t.is_alive():
            return "already running"
        if client_factory is None:
            from app.core.database import get_supabase as client_factory
        t = threading.Thread(target=_poll_loop, args=(client_factory,), name="auto-calc-poller", daemon=True)
        t.start()
        _POLLER.update(pid=os.getpid(), thread=t)
    return f"started (every {POLL_SECONDS}s)"


def _poll_loop(client_factory):
    while True:
        try:
            run_due(client_factory())
        except Exception as e:                          # a poller that dies silently is the defect; log and go on
            print(f"WARN auto-calc poller tick failed: {e}")
        time.sleep(POLL_SECONDS)
