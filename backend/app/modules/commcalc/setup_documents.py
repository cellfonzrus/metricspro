"""SETUP DOCUMENTS — the per-carrier required uploads a tenant brings in at setup, where each one comes from,
whether it can be automated, how often it is due, and the reminders (owner 2026-09-27, index §39).

Owner, verbatim: *"Create a list of documents which need to be uploaded for each carrier to get all the required
reports and queries, create them as a part of super admin console and attach them by default when the tenant is set
up, then on the tenant side the first page which opens up is the set up wizard which requires the tenant to upload
these files, tell the Tenant where to download those files from as that toiled their carrier, tell them to add
credentials if they want automated data updating if we have a successful history of doing that, if we don't have a
successful history then don't ask and keep the tenant on manual uploads, create reminders for the tenant to upload the
files on the period as chosen by the tenant."*

NOTHING HERE IS A SECOND REGISTRY — every fact is dereferenced from its one home:
  · WHICH documents / for WHICH carrier  → the report-kind registry (mig 1010, `report_kinds`): house rows are the
    platform list every tenant inherits at read time (= "attached by default"); the tenant's declaration + the one
    visibility function decide what applies. Mig 1028 adds the setup columns (report_kinds.SETUP_COLS).
  · HOW OFTEN it is due (the tenant's chosen period) and WHETHER it arrived → the tenant's core.import_feed row
    (`kind:<key>`, mig 717, registered by ensure_feeds in import_health's own row shape) and import_health's evidence /
    feed_status — the same freshness every health surface reads.
  · AUTOMATION HISTORY → commcalc.upload_trace (email / FTP sweeps) + core.job_run `sweep:<table>` rows (every portal
    sweep run, written by commcalc.router._sweep_set_status) mapped to upload types by import_health._SWEEP_SPECS.
  · REMINDERS → the existing alert pipeline (closing._send_alert: storeops.alert_recipient scope `upload_due`,
    email + WhatsApp, deduped by alert_log) + the existing login popup (import_health `_p_imports`).

PURE functions first (proven DB-free by harness_setup_documents.py); thin I/O loaders after.
"""
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from app.modules.commcalc import report_kinds as RK
from app.modules.core import import_health as IH

FEED_PREFIX = "kind:"                       # core.import_feed.feed_key of a setup document
FEED_MODULE = "setup"
WIZARD_PATH = "/commcalc/upload/wizard"
INTAKE_PATH = "/onboarding/intake"
ALERT_SCOPE = "upload_due"                  # storeops.alert_recipient scope (Cash & Closing Alerts page)
CADENCES = ("daily", "weekly", "monthly")   # = the mig-1028 CHECK; hours come from import_health.freq_hours
SKIP_DAYS = 30                              # "I don't have this yet — remind me later"
NEVER_GRACE_HOURS = 24                      # a scheduled, never-uploaded document is first reminded a day later
AUTOMATED_TRACE_SOURCES = ("email_sweep", "ftp_sweep")
SWEEP_JOB_PREFIX = "sweep:"                 # core.job_run.job_name written by router._sweep_set_status
EMAIL_AUTOMATION_PATH = "/commcalc/email-imports"
SETUP_DEFAULTS = {"required": False, "default_cadence": "monthly", "download_url": None, "download_steps": None,
                  "evidence_table": None, "upload_path": None, "automation_min_runs": 3, "automation_window_days": 60}

def _now():
    return datetime.now(timezone.utc)


def _ts(v):
    return IH._parse_ts(v)


# ── PURE: the setup facts of one kind ──────────────────────────────────────────────────────────────────────────────
def setup_fields(kind):
    """The kind's setup facts: the registry row's own columns (mig 1028), else the defaults. There is deliberately NO
    code mirror of the seed — the portal names and steps are DATA and live only in the registry (RULE TWO). A database
    that predates 1028 therefore reads "nothing required": no gate, no reminder — the safe direction."""
    out = {}
    for k, default in SETUP_DEFAULTS.items():
        v = kind.get(k)
        out[k] = v if v is not None else default
    out["required"] = bool(out["required"])
    if out["default_cadence"] not in CADENCES:
        out["default_cadence"] = SETUP_DEFAULTS["default_cadence"]
    out["automation_min_runs"] = max(1, int(out["automation_min_runs"] or 1))
    out["automation_window_days"] = max(1, int(out["automation_window_days"] or 1))
    return out


def is_generic(kind):
    """A kind scoped to no carrier and no business type — a wireless-POS report every carrier tenant has."""
    return not kind.get("applies_to_carrier") and not kind.get("applies_to_vertical")


def required_documents(visible, uses_carriers=True):
    """The documents THIS tenant must upload: its visible kinds (the ONE visibility function already applied) that the
    platform marks required. A generic (unscoped) document is required only for a business type that uses carriers —
    the vertical's own `uses_carriers` DATA, so a franchise store is never asked for an IMEI sales report."""
    out = []
    for k in visible or []:
        f = setup_fields(k)
        if not f["required"]:
            continue
        if is_generic(k) and uses_carriers is False:
            continue
        out.append(k)
    return out


def sweep_upload_types():
    """{sweep config table: [upload types it lands]} — read from import_health._SWEEP_SPECS (its one home)."""
    return {spec[0]: list(spec[6] or []) for spec in IH._SWEEP_SPECS}


def sweep_links():
    return {spec[0]: spec[4] for spec in IH._SWEEP_SPECS}


def feed_key(kind_key):
    return FEED_PREFIX + (kind_key or "")


def evidence_probes(kind, fields=None):
    """How import_health proves this document arrived: every upload route it lands through (manual or e-mailed —
    both write upload_trace), every portal sweep that lands one of its upload types, and its landing table when it
    has no upload route (e.g. the royalty report)."""
    f = fields or setup_fields(kind)
    ups = list(kind.get("upload_types") or [])
    probes = [{"kind": "upload_trace", "upload_type": u} for u in ups]
    for table, types in sweep_upload_types().items():
        if set(types) & set(ups):
            probes.append({"kind": "sweep", "table": table})
    if f.get("evidence_table"):
        probes.append({"kind": "raw_table", "schema": "commcalc", "table": f["evidence_table"], "column": "created_at"})
    return probes


def feed_candidate(kind):
    """The core.import_feed row a setup document is tracked by. Registered DISABLED (no alert, no reminder) — it is
    switched on by the tenant choosing its period in the setup wizard (put_document), exactly the posture
    import_health already takes for manual report_definitions, so shipping this sends nobody anything."""
    f = setup_fields(kind)
    probes = evidence_probes(kind, f)
    if not probes:
        return None
    cad = IH.freq_hours(f["default_cadence"], IH._MANUAL_CADENCE_HOURS)
    return {
        "feed_key": feed_key(kind["key"]), "label": kind.get("label") or kind["key"], "module": FEED_MODULE,
        "source_type": "manual_expected", "cadence_hours": cad, "grace_hours": IH.default_grace(cad),
        "deep_link": WIZARD_PATH, "evidence": probes, "enabled": False,
        "derived_from": f"commcalc.report_kind:{kind['key']}",
    }


def upload_path(kind, fields=None):
    """Where a document WITHOUT an upload route is uploaded: its own page when the registry names one, else the guided
    intake for the kinds the intake takes. None = it uploads through its route on the wizard itself."""
    f = fields or setup_fields(kind)
    if kind.get("upload_types"):
        return None
    if f.get("upload_path"):
        return f["upload_path"]
    return INTAKE_PATH if (kind.get("landing") in RK.INTAKE_LANDINGS) else None


def allow_paths(docs):
    """The pages a gated admin may still open: the wizard, and every page a document's upload / automation needs."""
    out = [WIZARD_PATH, INTAKE_PATH, EMAIL_AUTOMATION_PATH]
    for d in docs or []:
        for p in (d.get("upload_path"), (d.get("automation") or {}).get("setup_link")):
            if p and p not in out:
                out.append(p)
    return out


def cadence_of(hours):
    """The period name for a cadence in hours (nearest of daily / weekly / monthly)."""
    try:
        h = float(hours)
    except (TypeError, ValueError):
        return None
    return min(CADENCES, key=lambda c: abs(IH.freq_hours(c) - h))


# ── PURE: automation history ───────────────────────────────────────────────────────────────────────────────────────
def automation_runs(trace_rows, job_rows):
    """{upload_type: {(org_id, 'YYYY-MM-DD', route)}} — one successful AUTOMATED landing per org per day per route.
    Counting (org, day) rather than files stops a sweep that lands 30 files from reading as 30 runs."""
    runs = defaultdict(set)
    for t in trace_rows or []:
        if (t.get("source") or "") not in AUTOMATED_TRACE_SOURCES:
            continue
        if not (t.get("rows_saved") or 0) > 0 or (t.get("status") or "ok") not in ("ok", "partial"):
            continue
        d = _ts(t.get("created_at"))
        if not d or not t.get("upload_type"):
            continue
        runs[t["upload_type"]].add((str(t.get("org_id")), d.date().isoformat(), t["source"]))
    types_by_sweep = sweep_upload_types()
    for j in job_rows or []:
        name = j.get("job_name") or ""
        if not name.startswith(SWEEP_JOB_PREFIX) or (j.get("status") or "") != "succeeded":
            continue
        table = name[len(SWEEP_JOB_PREFIX):]
        d = _ts(j.get("finished_at") or j.get("started_at"))
        if not d:
            continue
        for u in types_by_sweep.get(table, []):
            runs[u].add((str(j.get("org_id")), d.date().isoformat(), name))
    return runs


def automation_proof(kind, runs, now=None):
    """Do we have a successful history of automating this document? `min_runs` distinct org-days inside the window,
    any tenant (the kind's own config, house default 3 in 60 days — owner 2026-09-27). Returns where to set it up."""
    f = setup_fields(kind)
    now = now or _now()
    cutoff = (now - timedelta(days=f["automation_window_days"])).date().isoformat()
    hits, routes = set(), defaultdict(int)
    for u in kind.get("upload_types") or []:
        for (org, day, route) in (runs or {}).get(u, ()):
            if day >= cutoff:
                hits.add((org, day, route))
                routes[route] += 1
    n = len({(o, d) for (o, d, _r) in hits})
    route = max(routes, key=lambda r: (routes[r], r)) if routes else None
    if route and route.startswith(SWEEP_JOB_PREFIX):
        table = route[len(SWEEP_JOB_PREFIX):]
        how, link = "portal", sweep_links().get(table) or "/commcalc/connectors"
    elif route in AUTOMATED_TRACE_SOURCES:
        how, link = ("email" if route == "email_sweep" else "ftp"), EMAIL_AUTOMATION_PATH if route == "email_sweep" \
            else "/commcalc/ftp-imports"
    else:
        how, link = None, None
    proven = n >= f["automation_min_runs"]
    return {"proven": proven, "runs": n, "needed": f["automation_min_runs"], "window_days": f["automation_window_days"],
            "how": how if proven else None, "setup_link": link if proven else None}


# ── PURE: one document's status, the gate, the reminder ────────────────────────────────────────────────────────────
def document_status(kind, feed, status, proof, now=None):
    """What the wizard shows for one document."""
    now = now or _now()
    f = setup_fields(kind)
    muted = _ts((feed or {}).get("muted_until"))
    skipped = bool(muted and muted > now)
    last = (status or {}).get("last_success")
    return {
        "key": kind["key"], "label": kind.get("label") or kind["key"], "what_in_it": kind.get("what_in_it"),
        "required": f["required"], "carriers": list(kind.get("applies_to_carrier") or []),
        "download_steps": f["download_steps"], "download_url": f["download_url"],
        "source_hint": kind.get("source_hint"), "upload_types": list(kind.get("upload_types") or []),
        "upload_path": upload_path(kind, f),
        "landing": kind.get("landing"),
        "cadence": cadence_of((feed or {}).get("cadence_hours")) or f["default_cadence"],
        "default_cadence": f["default_cadence"],
        "scheduled": bool((feed or {}).get("enabled")), "feed_id": (feed or {}).get("id"),
        "uploaded": bool(last), "last_uploaded_at": last,
        "state": (status or {}).get("state") or "never", "due_at": (status or {}).get("due_at"),
        "skipped": skipped, "skipped_until": (feed or {}).get("muted_until") if skipped else None,
        "automation": proof,
    }


def gate(docs, done_at):
    """Walk the tenant's admins to the wizard while any required document has never arrived and was not put off.
    Once a tenant is done (`documents_setup_done_at`) it is never gated again — reminders carry on."""
    pending = [d["key"] for d in docs or [] if d.get("required") and not d.get("uploaded") and not d.get("skipped")]
    return {"active": done_at is None and bool(pending), "pending": pending, "done": done_at is not None,
            "complete": not pending, "allow_paths": allow_paths(docs)}


def reminder_ref(feed, status, now=None):
    """The alert_log key for a reminder that is due NOW, else None. A scheduled (enabled), un-muted document is
    reminded when it becomes due and again once per period while it stays missing — the key changes per cycle, so
    the alert pipeline's dedup sends each exactly once."""
    now = now or _now()
    if not (feed or {}).get("enabled"):
        return None
    muted = _ts(feed.get("muted_until"))
    if muted and muted > now:
        return None
    state = (status or {}).get("state")
    if state not in ("overdue", "never"):
        return None
    cad = timedelta(hours=float(feed.get("cadence_hours") or 24.0))
    last = _ts((status or {}).get("last_success"))
    # never uploaded: a day's grace from when the tenant scheduled it, then once per period
    due = (last + cad) if last else ((_ts(feed.get("updated_at")) or _ts(feed.get("created_at")) or now)
                                     + timedelta(hours=NEVER_GRACE_HOURS))
    if now < due:
        return None
    cycle = int((now - due) / cad) if cad.total_seconds() > 0 else 0
    return f"{feed.get('feed_key')}:{due.date().isoformat()}:{cycle}"


def reminder_text(doc, company=""):
    steps = doc.get("download_steps") or doc.get("source_hint") or ""
    subject = f"Upload due: {doc['label']}" + (f" — {company}" if company else "")
    body = (f"It's time to upload “{doc['label']}”" + (f" for {company}" if company else "") + ". "
            + (f"Where to get it: {steps} " if steps else "")
            + f"Upload it in MetricsPro → Upload Wizard ({WIZARD_PATH}). "
            + f"You chose to upload this {doc.get('cadence') or 'regularly'}; change that on the same page.")
    return subject, body


# ── I/O ────────────────────────────────────────────────────────────────────────────────────────────────────────────
_RUNS_CACHE = {"at": 0.0, "runs": None}
_RUNS_TTL = 900.0


def load_automation_runs(client, days=365, force=False):
    """Platform-wide automated landings (service role; COUNTS only ever leave this module — no tenant's data does).
    Cached 15 minutes: the answer changes daily, the wizard reads it on every open."""
    if not force and _RUNS_CACHE["runs"] is not None and time.time() - _RUNS_CACHE["at"] < _RUNS_TTL:
        return _RUNS_CACHE["runs"]
    since = (_now() - timedelta(days=days)).isoformat()
    traces, jobs = [], []
    try:
        traces = (client.schema("commcalc").table("upload_trace")
                  .select("org_id,created_at,upload_type,source,rows_saved,status")
                  .in_("source", list(AUTOMATED_TRACE_SOURCES)).gte("created_at", since)
                  .limit(50000).execute().data) or []
    except Exception as e:                                              # pragma: no cover - I/O guard
        print(f"WARN setup_documents upload_trace read failed: {e}")
    try:
        jobs = (client.schema("core").table("job_run").select("org_id,job_name,status,started_at,finished_at")
                .like("job_name", SWEEP_JOB_PREFIX + "%").eq("status", "succeeded").gte("started_at", since)
                .limit(50000).execute().data) or []
    except Exception as e:                                              # pragma: no cover - I/O guard
        print(f"WARN setup_documents job_run read failed: {e}")
    runs = automation_runs(traces, jobs)
    _RUNS_CACHE.update(at=time.time(), runs=runs)
    return runs


def tenant_context(client, org_id):
    """(visible kinds, uses_carriers, done_at, company name) — through the registry's own loaders."""
    rows, _ready = RK.load_registry(client, org_id)
    decl = RK.tenant_declaration(client, org_id)
    caps = {}
    try:
        from app.modules.commcalc.router import _intake_caps       # the SAME caps GET /report-kinds applies
        caps = _intake_caps(client, org_id) or {}
    except Exception:
        caps = {}
    visible = RK.visible_kinds(rows, decl, caps, org_id=org_id)
    uses_carriers = True
    try:
        from app.modules.core import verticals as _vert
        uses_carriers = _vert.tenant_vertical(client, org_id).get("uses_carriers") is not False
    except Exception:
        pass
    done_at, name = None, ""
    try:
        t = (client.schema("storeops").table("tenants").select("name,documents_setup_done_at")
             .eq("org_id", org_id).limit(1).execute().data) or []
        if t:
            done_at, name = t[0].get("documents_setup_done_at"), t[0].get("name") or ""
    except Exception:
        # pre-1028: the column is absent — read the name alone and treat the tenant as done (never gated)
        try:
            t = (client.schema("storeops").table("tenants").select("name").eq("org_id", org_id)
                 .limit(1).execute().data) or []
            name = (t[0].get("name") if t else "") or ""
        except Exception:
            pass
        done_at = "pre-1028"
    return visible, uses_carriers, done_at, name


def ensure_feeds(client, org_id, kinds):
    """Register (never overwrite) the core.import_feed row of every document this tenant owes — the SAME row shape and
    idempotent insert import_health.load_feeds uses (on_conflict org_id,feed_key, ignore_duplicates): a row the tenant
    already scheduled, edited or put off is never touched. import_health itself stays unaware of the registry (its
    derivation reads only the tenant's own config — harness_import_health §D pins that every read is org-filtered)."""
    rows = []
    for k in kinds or []:
        c = feed_candidate(k)
        if not c:
            continue
        row = {col: c.get(col) for col in IH._FEED_COLS if col in c}
        row.update({"org_id": org_id, "auto_derived": True})
        rows.append(row)
    if not rows:
        return 0
    try:
        client.schema("core").table("import_feed").upsert(
            rows, on_conflict="org_id,feed_key", ignore_duplicates=True).execute()
    except Exception as e:                                              # pragma: no cover - I/O guard
        print(f"WARN setup_documents could not register document feeds: {e}")
        return 0
    return len(rows)


def payload(client, org_id, persist=True):
    """Everything the wizard needs for one tenant. Feeds + freshness come from import_health.feed_health — the
    required documents' feeds are among the candidates it derives (derive_candidates → feed_candidate)."""
    return _evaluate(client, org_id, persist)[0]


def _evaluate(client, org_id, persist=True):
    """(payload, {feed_key: feed-with-status}) — the reminder runner needs the feed rows the payload was built from."""
    visible, uses_carriers, done_at, name = tenant_context(client, org_id)
    docs_kinds = required_documents(visible, uses_carriers)
    if persist:
        ensure_feeds(client, org_id, docs_kinds)
    health = IH.feed_health(client, org_id, persist=persist)
    by_key = {f.get("feed_key"): f for f in health.get("feeds") or []}
    runs = load_automation_runs(client)
    now = _now()
    docs = []
    for k in docs_kinds:
        fr = by_key.get(feed_key(k["key"]))
        docs.append(document_status(k, fr, fr, automation_proof(k, runs, now), now))
    g = gate(docs, done_at)
    if persist and done_at is None and g["complete"]:
        try:
            client.schema("storeops").table("tenants").update(
                {"documents_setup_done_at": now.isoformat()}).eq("org_id", org_id).execute()
            g = gate(docs, now.isoformat())
        except Exception as e:                                          # pragma: no cover - I/O guard
            print(f"WARN setup_documents could not stamp setup done: {e}")
    return ({"company": name, "documents": docs, "gate": g, "wizard_path": WIZARD_PATH,
             "uses_carriers": uses_carriers, "ready": bool(health.get("ready", True))}, by_key)


def put_document(client, org_id, kind_key, cadence=None, skip=None):
    """The tenant's choices for one document: its period (switches the reminder ON and takes ownership of the feed, so
    import_health's one-way auto-disable never turns it back off) and "I don't have this yet" (mutes it SKIP_DAYS)."""
    if cadence is not None and cadence not in CADENCES:
        raise ValueError("cadence must be one of " + ", ".join(CADENCES))
    visible, uses_carriers, _done, _name = tenant_context(client, org_id)
    ensure_feeds(client, org_id, required_documents(visible, uses_carriers))
    health = IH.feed_health(client, org_id, persist=True)
    fr = next((f for f in health.get("feeds") or [] if f.get("feed_key") == feed_key(kind_key)), None)
    if not fr or not fr.get("id"):
        raise LookupError("this document is not tracked for this company")
    upd = {"auto_derived": False, "updated_at": _now().isoformat()}
    if cadence is not None:
        hrs = IH.freq_hours(cadence)
        upd.update({"cadence_hours": hrs, "grace_hours": IH.default_grace(hrs), "enabled": True})
    if skip is True:
        upd["muted_until"] = (_now() + timedelta(days=SKIP_DAYS)).isoformat()
    elif skip is False:
        upd["muted_until"] = None
    client.schema("core").table("import_feed").update(upd).eq("org_id", org_id).eq("id", fr["id"]).execute()
    return upd


async def run_reminders(client, send_alert, org_ids=None):
    """Hourly (rides the connector-health tick): for every tenant with a scheduled setup document that is due, send
    ONE reminder per cycle through the alert pipeline (email + WhatsApp to the `upload_due` recipients, falling back
    to the tenant's admins; deduped by alert_log on reminder_ref)."""
    sent, checked = 0, 0
    if org_ids is None:
        try:
            rows = (client.schema("core").table("import_feed").select("org_id")
                    .like("feed_key", FEED_PREFIX + "%").eq("enabled", True).limit(5000).execute().data) or []
            org_ids = sorted({r["org_id"] for r in rows if r.get("org_id")})
        except Exception:
            org_ids = []
    now = _now()
    for org in org_ids:
        try:
            p, by_key = _evaluate(client, org, persist=False)
        except Exception as e:                                          # pragma: no cover - I/O guard
            print(f"WARN setup reminders skipped for {org}: {e}")
            continue
        for d in p["documents"]:
            fr = by_key.get(feed_key(d["key"]))
            checked += 1
            ref = reminder_ref(fr, fr, now)
            if not ref:
                continue
            subject, body = reminder_text(d, p.get("company") or "")
            try:
                await send_alert(client, org, ALERT_SCOPE, subject, body, ref)
                sent += 1
            except Exception as e:                                      # pragma: no cover - I/O guard
                print(f"WARN setup reminder failed ({org}/{d['key']}): {e}")
    return {"tenants": len(org_ids), "checked": checked, "sent": sent}
