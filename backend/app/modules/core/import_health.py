"""UNIVERSAL IMPORT HEALTH + ADMIN ATTENTION (mig 717) — platform-core.

OWNER DIRECTIVE 2026-07-25 (verbatim): "the commision of vida pay is suppoed to run on a schedule, the
system should update the latest import time and if any imports are not scheduled as defined in the entire
system it should come up as a pop for every admin as soon as they log in on the main page and take them to
the upload menu to manually upload the data or fix the import channel - this should be built universally
for all uploads now and going forward for all tenants. also the admin should be notified for pending
mappings or duplicate data"

WHAT THIS IS
  A CROSS-MODULE, READ-ONLY health layer over the imports that ALREADY exist. It adds NO write path to any
  ingest and changes NO ingest behaviour. Three pieces:

  1. FEED REGISTRY (core.import_feed, mig 717) — one row per expected import per tenant: cadence, grace,
     the deep link an admin fixes it at, and the EVIDENCE probes that answer "when did this last land".
     Rows are AUTO-DERIVED from the schedule the system already knows (RULE TWO — nothing hard-coded per
     tenant/carrier): commcalc.email_sweep_config patterns, ftp_sweep_config patterns, the five portal
     *_sweep_config rows, closing_sweep_config (Google service-account sheet), commcalc.data_source portal
     logins x commcalc.report_pull_map reports (the VidaPay / T-CETRA pulls the owner named), and
     commcalc.report_definitions for anything the tenant can only upload by hand. Derivation is IDEMPOTENT
     (keyed on a deterministic feed_key, insert-if-absent, NEVER overwriting an admin's edits) and runs
     on-read, so a brand-new tenant gets its registry the first time an admin opens the app — no seed
     dependency, no SEED_VERSION bump.

  2. FRESHNESS — `last_success` per feed comes from the AUTHORITATIVE trail each ingest already writes
     (see EVIDENCE below), aggregated in ONE Postgres round trip via core.import_evidence(org).
     overdue  = now - last_success > cadence_hours + grace_hours
     never    = no evidence at all (registered but has never delivered)
     `channel_stale` additionally flags "the data arrived, but NOT through the configured channel"
     (i.e. someone is manually uploading around a broken sweep) without raising a false alarm.

  3. ATTENTION — GET /core/attention aggregates PROVIDERS into one list of items
     {group, key, severity, label, detail, count, deep_link, deep_link_label}. Providers are registered
     through `register_provider()`, so another module can contribute an item WITHOUT touching this
     aggregation. Cheap providers run on every call (the login popup); heavy ones run only with deep=1
     (a login must never pay for a 40k-row scan) and are listed under `deferred` so the UI can offer
     "Run full check". Groups in use: import · mapping · duplicate · config (setup / dead-wiring gaps) ·
     system (errors awaiting review) · other.

     PROVIDER CONTRACT — "a notification MUST clear when the check says everything is OK" (owner,
     2026-07-26): a provider item may report LIVE state only, and its deep_link must land on a surface
     where completing the offered action makes THAT item disappear on the next GET /core/attention. If the
     state cannot be resolved from the linked page, the item does not belong in the popup. When every
     provider returns zero items the frontend renders NOTHING at all — no pill, no popup (see
     frontend/src/components/AdminAttention.tsx).

EVIDENCE — the authoritative "last success" source chosen per feed shape (all pre-existing; nothing new):
  email_sweep  → commcalc.email_processed  (status='ok' AND rows_saved>0), per mailbox account + upload_type
  ftp          → commcalc.ftp_processed    (status='ok' AND rows_saved>0), per upload_type
  portal sweep → the sweep's own <table>.last_run_at / last_status (what the sweep itself stamps)
  portal pull  → commcalc.data_source.last_run_at / last_status, per login row
  ANY path     → commcalc.upload_trace (mig 202 universal ingest trace: manual upload, email sweep, ftp
                 sweep, feed promotion) filtered to rows_saved>0 — this is what makes "an admin uploaded
                 the file by hand" correctly clear the alert.
  last resort  → max(created_at) on the raw target table via core.import_table_freshness (opt-in per feed;
                 covers a tenant whose data predates mig 202).

MULTI-TENANT (RULE ONE): every read and every write is filtered/stamped with the org_id QUERY PARAM.
  tenant_middleware rewrites that param from the verified JWT for every normal user; a super-admin's
  client-supplied value is honored (that IS acting-as-tenant). For a non-super-admin the org is
  additionally CLAMPED to the caller's resolved membership org here — defence in depth if enforcement is
  ever toggled off. No house-org constant is ever used as a data scope.

DEGRADES GRACEFULLY: mig 717 un-run ⇒ every endpoint returns an honest empty payload + a `hint`, and the
  popup never fires. A missing sibling table (mig 202, 075, 083 …) just contributes no evidence.
"""
import json
import os
import re
import threading
import time
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Header

from app.core.database import get_supabase
from app.modules.core.safe_href import safe_href

# NO prefix: this sub-router is mounted ONTO core/router.py's `router` (which already carries
# "/core"), so main.py — a SHARED file — needs no change at all. Final paths: /api/v1/core/…
router = APIRouter(tags=["Core / Import health"])

ORG_ID = "00000000-0000-0000-0000-000000000001"
MIG_HINT = "Import health is not set up yet — run migration 717 (core.import_feed)."


def sb():
    return get_supabase()


def _scan_all(client, schema, table, select, page=1000, cap=2_000_000, **eqs):
    """Read EVERY matching row, paginated. Replaces a single `.limit(N)` shot (2026-08-10).

    The two `cost="heavy"` mapping providers below scanned `commcalc.raw_comp_report` and
    `commcalc.raw_mi` with a bare `.limit(200000)`. Measured live, `raw_mi` holds **234,610** rows —
    so 34,610 were silently discarded, with no ORDER BY, meaning WHICH ones changed between calls.

    A truncated scan cannot under-report a little here; it under-reports in exactly the direction that
    looks CLEAN. Both providers answer "what is NOT mapped yet", so a plan or compensation category
    whose only rows fell past the cut simply does not appear, and the board reports fewer gaps than
    exist. The failure mode of an attention board is silence, which is why the limit had to go rather
    than be raised.

    `cap` is a runaway guard far above any real table, not a business limit — the loop exits on a
    short page long before it.
    """
    out, start = [], 0
    while start < cap:
        q = client.schema(schema).table(table).select(select)
        for k, v in eqs.items():
            q = q.in_(k, list(v)) if isinstance(v, (list, tuple, set)) else q.eq(k, v)
        rows = (q.range(start, start + page - 1).execute().data) or []
        out.extend(rows)
        if len(rows) < page:
            break
        start += page
    return out


def _now():
    return datetime.now(timezone.utc)


def _parse_ts(v):
    """'2026-07-25T12:00:00+00:00' | datetime | None → aware datetime | None. Never raises."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _iso(d):
    return d.isoformat() if isinstance(d, datetime) else None


# ── Derivation defaults (SEED values only — every one is editable per feed row in the admin UI) ──────
# A tenant tunes cadence/grace per feed at /admin/import-health; these are just the values a freshly
# derived row starts with, read from the schedule the tenant ALREADY configured wherever one exists.
_FREQ_HOURS = {"hourly": 1.0, "daily": 24.0, "weekly": 168.0, "monthly": 720.0}
_MANUAL_CADENCE_HOURS = 720.0     # a hand-uploaded (period) report is expected monthly by default
_MANUAL_GRACE_HOURS = 72.0


def freq_hours(frequency, default=24.0):
    """'daily' → 24.0. Unknown/blank → `default`. PURE."""
    return _FREQ_HOURS.get(str(frequency or "").strip().lower(), float(default))


def default_grace(cadence_hours):
    """Slack before 'overdue'. Scales with cadence so a monthly feed isn't flagged 6h late. PURE."""
    c = float(cadence_hours or 24.0)
    if c <= 24.0:
        return 6.0
    if c <= 168.0:
        return 24.0
    return 72.0


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").strip().lower()).strip("_") or "x"


# ── Feed derivation — PURE given the already-fetched config rows ─────────────────────────────────────
# Every candidate carries a DETERMINISTIC feed_key: re-deriving produces the identical key, which is what
# makes the on-read merge idempotent (insert-if-absent on (org_id, feed_key)).

def _email_candidates(rows):
    out = []
    for r in rows or []:
        if not (r.get("username") or r.get("imap_host")):
            continue                      # mailbox never configured → the tenant expects nothing from it
        acct = (r.get("account") or "default").strip() or "default"
        cad = freq_hours(r.get("frequency"), 24.0)
        pats = r.get("patterns")
        if isinstance(pats, str):
            try:
                pats = json.loads(pats)
            except Exception:
                pats = []
        for p in (pats or []):
            if not isinstance(p, dict):
                continue
            ut = (p.get("upload_type") or "").strip()
            if not ut:
                continue
            out.append({
                "feed_key": f"email:{_slug(acct)}:{_slug(ut)}",
                "label": f"Email import · {r.get('label') or acct} · {ut}",
                "module": "commissions", "source_type": "email_sweep",
                "cadence_hours": cad, "grace_hours": default_grace(cad),
                "deep_link": "/commcalc/email-imports",
                "evidence": [{"kind": "email", "account": acct, "upload_type": ut},
                             {"kind": "upload_trace", "upload_type": ut}],
                "enabled": bool(r.get("enabled")),
                "derived_from": f"commcalc.email_sweep_config:{acct}:{p.get('pattern') or ut}",
                "covers_upload_type": ut,
            })
    return out


def _ftp_candidates(rows):
    out = []
    for r in rows or []:
        if not r.get("host"):
            continue
        cad = freq_hours(r.get("frequency"), 24.0)
        pats = r.get("patterns")
        if isinstance(pats, str):
            try:
                pats = json.loads(pats)
            except Exception:
                pats = []
        for p in (pats or []):
            if not isinstance(p, dict):
                continue
            ut = (p.get("upload_type") or "").strip()
            if not ut:
                continue
            out.append({
                "feed_key": f"ftp:{_slug(ut)}",
                "label": f"FTP import · {ut}",
                "module": "commissions", "source_type": "ftp",
                "cadence_hours": cad, "grace_hours": default_grace(cad),
                "deep_link": "/commcalc/ftp-imports",
                "evidence": [{"kind": "ftp", "upload_type": ut},
                             {"kind": "upload_trace", "upload_type": ut}],
                "enabled": bool(r.get("enabled")),
                "derived_from": f"commcalc.ftp_sweep_config:{p.get('pattern') or ut}",
                "covers_upload_type": ut,
            })
    return out


# table → (feed_key suffix, label, module, deep_link, "is this row configured?" field, upload types covered)
_SWEEP_SPECS = [
    ("dlar_sweep_config",    "dlar",    "Store/Rep metrics sync (DLAR portal)", "commissions",
     "/commcalc/dlar/sweep",   "portal_user", ["dlar_store", "dlar_rep"]),
    ("epay_sweep_config",    "epay",    "Payment-processor sync (ePay portal)", "commissions",
     "/commcalc/epay/sweep",   "portal_user", ["mi_report", "comp_report", "payment_detail"]),
    ("vip_sweep_config",     "vip",     "Distributor invoices sync (VIP portal)", "vip",
     "/commcalc/vip/sweep",    "portal_user", []),
    ("b2b_sweep_config",     "b2b",     "POS inventory sync (B2B Soft)", "commissions",
     "/commcalc/connectors",   "portal_user", ["inventory_aging"]),
    ("closing_sweep_config", "closing", "Daily-closing responses sheet (Google)", "closing",
     "/closing/imports",       "sheet_id",    []),
]
_SWEEP_SOURCE_TYPE = {"closing_sweep_config": "google_sa"}


def _sweep_candidates(sweeps):
    """`sweeps` = {table_name: row|None}. One feed per CONFIGURED portal/sheet sweep."""
    out = []
    for table, key, label, module, link, cfg_field, uts in _SWEEP_SPECS:
        r = (sweeps or {}).get(table)
        if not r:
            continue
        configured = bool(r.get(cfg_field)) or bool(r.get("enabled")) or bool(r.get("last_run_at"))
        if not configured:
            continue                      # tenant does not use this connector at all
        cad = freq_hours(r.get("frequency"), 24.0)
        ev = [{"kind": "sweep", "table": table}]
        ev += [{"kind": "upload_trace", "upload_type": u} for u in uts]
        out.append({
            "feed_key": f"sweep:{key}",
            "label": label, "module": module,
            "source_type": _SWEEP_SOURCE_TYPE.get(table, "pull"),
            "cadence_hours": cad, "grace_hours": default_grace(cad),
            "deep_link": link, "evidence": ev,
            "enabled": bool(r.get("enabled")),
            "derived_from": f"commcalc.{table}",
            "covers_upload_type": None,
        })
    return out


# Where a pulled MA/processor report can be uploaded BY HAND when the channel is broken (the owner's
# "take them to the upload menu"). report_key → page. Anything unlisted falls back to the generic upload.
_PULL_UPLOAD_PAGE = {
    "ma_commission": "/commcalc/ma-upload", "ma_daily_tx": "/commcalc/ma-upload",
    "ma_marketplace_orders": "/commcalc/ma-upload", "ma_fulfillment": "/commcalc/ma-upload",
    "ma_sim_assignment": "/commcalc/ma-upload", "ma_pr_activation": "/commcalc/ma-upload",
}
_PULL_RAW_TABLE = {
    "ma_commission": "raw_ma_commission", "ma_daily_tx": "raw_ma_daily_tx",
    "ma_marketplace_orders": "raw_ma_fulfillment", "ma_fulfillment": "raw_ma_fulfillment",
    "ma_sim_assignment": "raw_ma_sim_assignment", "ma_pr_activation": "raw_ma_pr_activation",
}


def _pull_candidates(data_sources, report_map):
    """Portal-login pulls x the reports each login provides.

    The OWNER named VidaPay commission explicitly. Two shapes are handled:
      • a data_source login exists  → one feed per enabled report for that processor, evidence = the
        login's own last_run_at + the universal upload_trace + the report's raw table;
      • NO login exists but the tenant has report_pull_map rows → the report is still REGISTERED as
        `manual_expected` so its staleness SURFACES and the popup routes to the manual upload page.
        That is precisely the directive: register the expected cadence even when no scheduler exists.
    """
    out = []
    rmap = [r for r in (report_map or []) if r.get("enabled", True)]
    covered_procs = set()
    for s in (data_sources or []):
        if not (s.get("enabled") or s.get("username") or s.get("last_run_at")):
            continue
        proc = (s.get("processor") or "").strip().lower()
        covered_procs.add(proc)
        cad = freq_hours(s.get("frequency"), 24.0)
        sid = str(s.get("id") or "")
        label_base = s.get("label") or (proc or "portal") + " login"
        reports = [r for r in rmap if (r.get("processor") or "vidapay").strip().lower() == proc] or [None]
        for r in reports:
            rk = (r or {}).get("report_key") or ""
            suffix = f":{_slug(rk)}" if rk else ""
            ev = [{"kind": "source", "id": sid}]
            if rk:
                ev.append({"kind": "upload_trace", "upload_type": rk})
                if _PULL_RAW_TABLE.get(rk):
                    ev.append({"kind": "raw_table", "schema": "commcalc",
                               "table": _PULL_RAW_TABLE[rk], "column": "created_at"})
            out.append({
                "feed_key": f"pull:{_slug(proc)}:{sid[:8]}{suffix}",
                "label": (f"{label_base} · {(r or {}).get('display_name') or rk}" if rk else label_base),
                "module": "commissions", "source_type": "pull",
                "cadence_hours": cad, "grace_hours": default_grace(cad),
                "deep_link": _PULL_UPLOAD_PAGE.get(rk, "/commcalc/email-imports"),
                "evidence": ev, "enabled": bool(s.get("enabled")),
                "derived_from": f"commcalc.data_source:{sid}" + (f":{rk}" if rk else ""),
                "covers_upload_type": rk or None,
            })
    # Reports the tenant has configured but for which NO login is registered → manual-expected.
    for r in rmap:
        proc = (r.get("processor") or "vidapay").strip().lower()
        if proc in covered_procs:
            continue
        rk = (r.get("report_key") or "").strip()
        if not rk:
            continue
        ev = [{"kind": "upload_trace", "upload_type": rk}]
        if _PULL_RAW_TABLE.get(rk):
            ev.append({"kind": "raw_table", "schema": "commcalc",
                       "table": _PULL_RAW_TABLE[rk], "column": "created_at"})
        out.append({
            "feed_key": f"manual:{_slug(proc)}:{_slug(rk)}",
            "label": f"{r.get('display_name') or rk} (no automated pull configured)",
            "module": "commissions", "source_type": "manual_expected",
            "cadence_hours": _MANUAL_CADENCE_HOURS, "grace_hours": _MANUAL_GRACE_HOURS,
            "deep_link": _PULL_UPLOAD_PAGE.get(rk, "/commcalc/upload"),
            "evidence": ev, "enabled": True,
            "derived_from": f"commcalc.report_pull_map:{rk}",
            "covers_upload_type": rk,
        })
    return out


def _report_definition_candidates(report_defs, already_covered):
    """Anything in the tenant's own connector report catalogue that no other feed already covers.

    report_definitions IS the system's "what reports exist, auto vs manual" registry, so it is the right
    place to learn about an expected upload nothing else models (e.g. a manual 78-col Sales file). Rows
    are registered DISABLED when the report is manual (auto=false): the feed exists in the admin UI with
    a cadence the tenant can set, but it does not raise an alert until an admin opts in — so shipping this
    can never bury a tenant in noise for reports they never intended to schedule.
    """
    out = []
    for r in (report_defs or []):
        rk = (r.get("report_key") or "").strip()
        if not rk or rk in already_covered:
            continue
        # INERT ROW GUARD (2026-08-09). A registry row with no target_table AND no upload_endpoint
        # cannot receive data by any route, so a feed for it can only ever read "never" -- a phantom
        # alert for something that is not wired up. Five such rows exist live (hand-typed MA entries
        # with empty label/target_table, e.g. "MA Dailt TX SubMA"). Registering them as feeds is what
        # put blank-labelled errors on the board. Fix the registry row and the feed appears.
        if not (r.get("target_table") or "").strip() and not (r.get("upload_endpoint") or "").strip():
            continue
        auto = bool(r.get("auto"))
        cad = _MANUAL_CADENCE_HOURS if not auto else 24.0
        ep = (r.get("upload_endpoint") or "").strip()
        out.append({
            "feed_key": f"report:{_slug(rk)}",
            "label": f"{r.get('label') or rk}" + ("" if auto else " (manual upload)"),
            "module": "commissions",
            "source_type": "pull" if auto else "manual_expected",
            "cadence_hours": cad, "grace_hours": default_grace(cad) if auto else _MANUAL_GRACE_HOURS,
            "deep_link": "/commcalc/upload" if ep else "/commcalc/connectors",
            "evidence": [{"kind": "upload_trace", "upload_type": rk}]
                        + ([{"kind": "raw_table", "schema": "commcalc",
                             "table": r.get("target_table"), "column": "created_at"}]
                           if r.get("target_table") else []),
            "enabled": auto,
            "derived_from": f"commcalc.report_definitions:{rk}",
            "covers_upload_type": rk,
        })
    return out


def derive_candidates(cfg):
    """PURE: every config shape the system already knows → deterministic feed candidates.
    `cfg` = {'email': [...], 'ftp': [...], 'sweeps': {table: row}, 'data_sources': [...],
             'report_map': [...], 'report_defs': [...]}. Deduped on feed_key (first wins)."""
    cands = []
    cands += _email_candidates(cfg.get("email"))
    cands += _ftp_candidates(cfg.get("ftp"))
    cands += _sweep_candidates(cfg.get("sweeps"))
    cands += _pull_candidates(cfg.get("data_sources"), cfg.get("report_map"))
    covered = {c.get("covers_upload_type") for c in cands if c.get("covers_upload_type")}
    cands += _report_definition_candidates(cfg.get("report_defs"), covered)
    seen, out = set(), []
    for c in cands:
        k = c["feed_key"]
        if k in seen:
            continue
        seen.add(k)
        c.pop("covers_upload_type", None)
        out.append(c)
    return out


def fetch_config(client, org_id):
    """IO half of derivation: read every schedule-bearing config table for ONE org. Each read is
    best-effort — a table that doesn't exist on this database simply contributes nothing."""
    def q(schema, table, **filters):
        try:
            b = client.schema(schema).table(table).select("*").eq("org_id", org_id)
            for k, v in filters.items():
                b = b.eq(k, v)
            return b.limit(500).execute().data or []
        except Exception:
            return []
    sweeps = {}
    for table, *_ in _SWEEP_SPECS:
        rows = q("commcalc", table)
        sweeps[table] = rows[0] if rows else None
    return {
        "email": q("commcalc", "email_sweep_config"),
        "ftp": q("commcalc", "ftp_sweep_config"),
        "sweeps": sweeps,
        "data_sources": q("commcalc", "data_source"),
        "report_map": q("commcalc", "report_pull_map"),
        "report_defs": q("commcalc", "report_definitions"),
    }


# ── Registry read + idempotent merge ─────────────────────────────────────────────────────────────────
_FEED_COLS = ("feed_key", "label", "module", "source_type", "cadence_hours", "grace_hours",
              "deep_link", "evidence", "enabled", "auto_derived", "derived_from", "muted_until", "notes")

# Re-derive at most once per org per _DERIVE_TTL seconds. Derivation reads 10 config tables; the login
# popup calls this on every hard page load, and the tenant's import CONFIG changes maybe monthly — so a
# short TTL removes ~10 round trips from the common path while a newly configured pattern still appears
# within 15 minutes (and instantly via the admin page's "Re-sync" button, which passes force=True).
# Keyed on org_id (NOT on the client object — that would be a process-lifetime cache) and BOUNDED, so it
# can never grow without limit or hold stale data across a deploy.
_DERIVE_TTL = 900.0
_DERIVE_MAX = 500
_derived_at: dict = {}


def _derive_due(org_id, registry_empty, force):
    """True when the derive pass should run for this org. PURE except for the module-level TTL map."""
    if force or registry_empty:
        return True
    now = time.time()
    if len(_derived_at) > _DERIVE_MAX:                 # bounded: drop everything rather than grow
        _derived_at.clear()
    exp = _derived_at.get(org_id)
    return not (exp and exp > now)


def _derive_done(org_id):
    _derived_at[org_id] = time.time() + _DERIVE_TTL


def load_feeds(client, org_id, persist=True, force=False):
    """Stored registry rows for `org_id`, MERGED with freshly derived candidates.

    Idempotence contract: a candidate whose feed_key already exists is IGNORED (an admin's cadence /
    label / enabled / deep-link edits are never overwritten, and a disabled feed stays disabled). Only
    genuinely NEW keys are inserted. Running this twice therefore inserts nothing the second time.
    Returns (feeds, meta) — meta carries {'ready', 'hint', 'derived_new'}.
    """
    try:
        stored = (client.schema("core").table("import_feed").select("*")
                  .eq("org_id", org_id).limit(1000).execute().data) or []
    except Exception as e:
        return [], {"ready": False, "hint": MIG_HINT, "error": str(e)[:200], "derived_new": 0}
    if not _derive_due(org_id, not stored, force):
        return stored, {"ready": True, "hint": None, "derived_new": 0, "derived": False}
    have = {r.get("feed_key") for r in stored}
    try:
        cands = derive_candidates(fetch_config(client, org_id))
        _derive_done(org_id)
    except Exception:
        cands = []
    # ── REGISTRY auto=false MUST TURN AN EXISTING FEED OFF (owner directive 2026-08-09) ──────────
    # The idempotence contract below only ever INSERTS, so a report whose `auto` was later unticked
    # kept the `enabled: true` it was first stored with and went on counting as overdue. Measured
    # live: house `report:inventory` and `report:sales` were auto=false in report_definitions and
    # enabled=true in core.import_feed -- exactly the "sheets I never scheduled are showing errors"
    # the owner reported.
    #
    # DELIBERATELY ONE-DIRECTIONAL: this only ever DISABLES, and only for `auto_derived` feeds. It
    # can therefore never override an admin who switched a feed ON by hand, and can never re-enable
    # something a human switched OFF -- the two ways a bidirectional sync would fight the operator.
    # Ticking auto back on in the registry surfaces the feed in the admin UI to be re-enabled there.
    stale_off = [r for r in stored
                 if r.get("auto_derived") and r.get("enabled")
                 and any(c["feed_key"] == r.get("feed_key") and not c.get("enabled") for c in cands)]
    if stale_off and persist:
        for r in stale_off:
            try:
                (client.schema("core").table("import_feed")
                 .update({"enabled": False}).eq("org_id", org_id).eq("id", r.get("id")).execute())
                r["enabled"] = False        # reflect it in THIS response, not only the next one
            except Exception:
                pass                        # a sync failure must never break the health read
    new_rows = []
    for c in cands:
        if c["feed_key"] in have:
            continue
        row = {k: c.get(k) for k in _FEED_COLS if k in c}
        row["org_id"] = org_id
        row["auto_derived"] = True
        new_rows.append(row)
    meta = {"ready": True, "hint": None, "derived_new": len(new_rows), "derived": True}
    if not new_rows:
        return stored, meta
    if persist:
        try:
            # on_conflict + ignore_duplicates → two admins logging in at the same instant can't
            # duplicate a feed. Re-READ afterwards rather than trusting the upsert payload, so the
            # loser of that race sees the winner's rows exactly once (never a phantom copy).
            (client.schema("core").table("import_feed")
             .upsert(new_rows, on_conflict="org_id,feed_key", ignore_duplicates=True).execute())
            fresh = (client.schema("core").table("import_feed").select("*")
                     .eq("org_id", org_id).limit(1000).execute().data) or []
            if fresh:
                return fresh, meta
        except Exception:
            pass
    # Persistence unavailable / not requested → still SHOW the derived feeds for THIS request.
    return stored + [dict(r, id=None, created_at=_iso(_now())) for r in new_rows], meta


# ── Evidence ─────────────────────────────────────────────────────────────────────────────────────────
def read_evidence(client, org_id):
    """ONE Postgres round trip (core.import_evidence) → list of
    {kind, k1, k2, last_success, last_status, n}. Falls back to a BOUNDED Python tally if the RPC is
    missing (mig 717 un-run) so the page still works, just less precisely."""
    try:
        rows = client.schema("core").rpc("import_evidence", {"p_org": org_id}).execute().data or []
        if rows:
            return rows
    except Exception:
        pass
    return _evidence_fallback(client, org_id)


def _evidence_fallback(client, org_id):
    """Degraded path: newest-N scans of the same trails, tallied in Python. Bounded by design."""
    out = []

    def newest(schema, table, ts_col, key_fn, status_ok=None, limit=500):
        try:
            rows = (client.schema(schema).table(table).select("*").eq("org_id", org_id)
                    .order(ts_col, desc=True).limit(limit).execute().data) or []
        except Exception:
            return
        seen = set()
        for r in rows:
            if status_ok and not status_ok(r):
                continue
            k = key_fn(r)
            if k in seen:
                continue
            seen.add(k)
            out.append({"kind": k[0], "k1": k[1], "k2": k[2],
                        "last_success": r.get(ts_col), "last_status": "ok", "n": 1})

    newest("commcalc", "upload_trace", "created_at",
           lambda r: ("upload_trace", (r.get("upload_type") or ""), None),
           lambda r: (r.get("rows_saved") or 0) > 0 and (r.get("status") or "ok") in ("ok", "partial"))
    newest("commcalc", "email_processed", "processed_at",
           lambda r: ("email", (r.get("account") or "default"), (r.get("upload_type") or "")),
           lambda r: (r.get("status") or "").lower() == "ok" and (r.get("rows_saved") or 0) > 0)
    newest("commcalc", "ftp_processed", "processed_at",
           lambda r: ("ftp", (r.get("upload_type") or ""), None),
           lambda r: (r.get("status") or "").lower() == "ok" and (r.get("rows_saved") or 0) > 0)
    for table, *_ in _SWEEP_SPECS:
        try:
            for r in (client.schema("commcalc").table(table).select("*")
                      .eq("org_id", org_id).limit(50).execute().data) or []:
                out.append({"kind": "sweep", "k1": table, "k2": r.get("account"),
                            "last_success": r.get("last_run_at"),
                            "last_status": r.get("last_status") or "", "n": 1})
        except Exception:
            pass
    try:
        for r in (client.schema("commcalc").table("email_sweep_config").select("*")
                  .eq("org_id", org_id).limit(50).execute().data) or []:
            out.append({"kind": "sweep", "k1": "email_sweep_config", "k2": r.get("account") or "default",
                        "last_success": r.get("last_run_at"), "last_status": r.get("last_status") or "", "n": 1})
    except Exception:
        pass
    try:
        for r in (client.schema("commcalc").table("data_source").select("*")
                  .eq("org_id", org_id).limit(200).execute().data) or []:
            out.append({"kind": "source", "k1": str(r.get("id")), "k2": r.get("processor"),
                        "last_success": r.get("last_run_at"), "last_status": r.get("last_status") or "", "n": 1})
    except Exception:
        pass
    return out


def probe_matches(probe, ev):
    """Does evidence row `ev` satisfy `probe`? PURE. Unknown probe kind → False (never a false green)."""
    kind = (probe.get("kind") or "").strip()
    if kind != (ev.get("kind") or ""):
        return False
    k1, k2 = (ev.get("k1") or ""), (ev.get("k2") or "")
    if kind == "upload_trace":
        return k1 == (probe.get("upload_type") or "")
    if kind == "email":
        return (k1 == (probe.get("account") or "default")
                and k2 == (probe.get("upload_type") or ""))
    if kind == "ftp":
        return k1 == (probe.get("upload_type") or "")
    if kind == "sweep":
        if k1 != (probe.get("table") or ""):
            return False
        want = probe.get("account")
        return True if want in (None, "") else (k2 in ("", want))
    if kind == "source":
        return k1 == str(probe.get("id") or "")
    return False


def feed_status(feed, evidence, now=None, raw_freshness=None):
    """PURE freshness verdict for ONE feed.

    last_success        = newest evidence across ALL probes (data arrived, by ANY route incl. a manual
                          upload — which is exactly what should clear the alert);
    channel_success     = newest evidence for the FIRST (channel) probe only;
    state               = 'ok' | 'overdue' | 'never';
    channel_stale       = the data is fresh but the CONFIGURED channel is not (someone is uploading
                          around a broken sweep) — informational, never the alert itself.
    """
    now = now or _now()
    probes = feed.get("evidence") or []
    if isinstance(probes, str):
        try:
            probes = json.loads(probes)
        except Exception:
            probes = []
    best, channel, last_status = None, None, ""
    for i, p in enumerate(probes):
        if not isinstance(p, dict):
            continue
        if (p.get("kind") or "") == "raw_table":
            key = f"{p.get('schema') or 'commcalc'}.{p.get('table')}.{p.get('column') or 'created_at'}"
            ts = _parse_ts((raw_freshness or {}).get(key))
            if ts and (best is None or ts > best):
                best = ts
            continue
        for ev in (evidence or []):
            if not probe_matches(p, ev):
                continue
            ts = _parse_ts(ev.get("last_success"))
            if ts is None:
                continue
            if best is None or ts > best:
                best, last_status = ts, (ev.get("last_status") or "")
            if i == 0 and (channel is None or ts > channel):
                channel = ts
    cadence = float(feed.get("cadence_hours") or 24.0)
    grace = float(feed.get("grace_hours") if feed.get("grace_hours") is not None else default_grace(cadence))
    window = timedelta(hours=cadence + grace)
    if best is None:
        state, age_h, due_at = "never", None, None
    else:
        due_at = best + window
        age_h = round((now - best).total_seconds() / 3600.0, 1)
        state = "overdue" if now > due_at else "ok"
    # channel_stale asserts ONE specific thing: "the data IS arriving — just NOT through the configured
    # channel" (someone is uploading around a broken sweep). Gate-1 MINOR-1: the first cut set it whenever
    # the channel was past its window, which is ALSO true for the ordinary single-channel feed where
    # nothing arrived by ANY route — so every plain-overdue feed was mislabelled "arrived another way".
    # All four conditions are now required, and they are mutually reinforcing:
    #   1. state == 'ok'          — something DID land recently. An overdue feed is just overdue; there is
    #                               no "another route" to point at, so the honest message is "no data".
    #   2. has_channel            — probe 0 is a real channel (not a raw-table fallback) to compare against.
    #   3. best > channel         — the recent arrival did NOT come from the channel (None = never did).
    #   4. channel past its window— the channel itself really is late, not merely a few hours behind a
    #                               manual upload that happened to also run.
    # Consequence: channel_stale is now IMPOSSIBLE on an overdue/never feed, which is exactly why the
    # "arrived by another route" sentence is no longer emitted on those items (_p_imports).
    has_channel = bool(probes) and isinstance(probes[0], dict) and probes[0].get("kind") != "raw_table"
    channel_stale = bool(
        state == "ok" and has_channel and best is not None
        and (channel is None or (best > channel and (now - channel) > window)))
    return {
        "last_success": _iso(best), "last_status": last_status,
        "channel_success": _iso(channel), "channel_stale": channel_stale,
        "state": state, "overdue": state == "overdue", "never_run": state == "never",
        "age_hours": age_h, "due_at": _iso(due_at),
        "cadence_hours": cadence, "grace_hours": grace,
    }


def _raw_freshness(client, org_id, feeds):
    """Resolve every `raw_table` probe across the registry in ONE RPC call (opt-in per feed)."""
    specs, seen = [], set()
    for f in feeds:
        probes = f.get("evidence") or []
        if isinstance(probes, str):
            try:
                probes = json.loads(probes)
            except Exception:
                probes = []
        for p in probes:
            if isinstance(p, dict) and (p.get("kind") == "raw_table") and p.get("table"):
                key = (p.get("schema") or "commcalc", p.get("table"), p.get("column") or "created_at")
                if key in seen:
                    continue
                seen.add(key)
                specs.append({"schema": key[0], "table": key[1], "column": key[2]})
    if not specs:
        return {}
    try:
        rows = client.schema("core").rpc(
            "import_table_freshness", {"p_org": org_id, "p_specs": specs}).execute().data or []
        return {r.get("spec_key"): r.get("last_success") for r in rows}
    except Exception:
        return {}


def feed_health(client, org_id, persist=True, force=False):
    """Registry + freshness for one org. The single source both the admin page and the popup read."""
    feeds, meta = load_feeds(client, org_id, persist=persist, force=force)
    if not feeds:
        return {"feeds": [], "overdue": 0, "never": 0, **meta}
    evidence = read_evidence(client, org_id)
    raw = _raw_freshness(client, org_id, feeds)
    now = _now()
    out = []
    for f in feeds:
        st = feed_status(f, evidence, now=now, raw_freshness=raw)
        out.append({**{k: f.get(k) for k in
                       ("id", "feed_key", "label", "module", "source_type", "deep_link", "enabled",
                        "auto_derived", "derived_from", "muted_until", "notes", "evidence")}, **st})
    out.sort(key=lambda r: ({"overdue": 0, "never": 1, "ok": 2}.get(r["state"], 3), r["label"] or ""))
    return {"feeds": out, **meta,
            "overdue": sum(1 for r in out if r["enabled"] and r["state"] == "overdue"),
            "never": sum(1 for r in out if r["enabled"] and r["state"] == "never")}


def _muted(feed, now):
    m = _parse_ts(feed.get("muted_until"))
    return bool(m and m > now)


# ── ATTENTION PROVIDER REGISTRY ──────────────────────────────────────────────────────────────────────
# A provider is a callable (client, org_id, ctx) -> list[item]. `ctx` carries {'now', 'feed_health'} so a
# provider never re-reads what the aggregator already has. Items are plain dicts:
#   {group: 'import'|'mapping'|'duplicate'|<own>, key, severity: 'error'|'warning'|'info',
#    label, detail, count, deep_link, deep_link_label}
# ANOTHER MODULE ADDS ONE WITHOUT TOUCHING THIS FILE:
#     from app.modules.core.import_health import register_provider
#     register_provider("closing_gaps", label="Closing gaps", group="ops", cost="cheap")(my_fn)
# Cost 'heavy' ⇒ only executed when the caller asks for deep=1 (a login popup must never pay for a
# 40k-row scan); it is reported under `deferred` otherwise. Every provider is exception-isolated: one
# that raises is reported in `provider_errors` and can never break the popup for the others.
PROVIDERS = []


def register_provider(key, *, label, group="other", cost="cheap"):
    def deco(fn):
        spec = {"key": key, "label": label, "group": group,
                "cost": ("heavy" if cost == "heavy" else "cheap"), "fn": fn}
        for i, p in enumerate(PROVIDERS):     # idempotent: re-registering a key REPLACES it
            if p["key"] == key:
                PROVIDERS[i] = spec
                return fn
        PROVIDERS.append(spec)
        return fn
    return deco


def _item(group, key, severity, label, detail, count, deep_link, deep_link_label):
    return {"group": group, "key": key, "severity": severity, "label": label, "detail": detail,
            "count": int(count or 0), "deep_link": deep_link, "deep_link_label": deep_link_label}


@register_provider("imports", label="Imports overdue / never run", group="import", cost="cheap")
def _p_imports(client, org_id, ctx):
    """Overdue + never-run feeds, each with the deep link an admin fixes it at."""
    health = ctx.get("feed_health") or {}
    now = ctx.get("now") or _now()
    out = []
    for f in health.get("feeds") or []:
        if not f.get("enabled") or _muted(f, now):
            continue
        if f["state"] == "overdue":
            age = f.get("age_hours")
            out.append(_item("import", f"feed:{f['feed_key']}", "error", f.get("label") or f["feed_key"],
                             # NO "arrived by another route" note here: after the Gate-1 MINOR-1 fix
                             # channel_stale can never be True on an overdue feed (it requires state
                             # 'ok'), so the clause was dead AND its claim was false for the common
                             # single-channel case. A genuinely channel-stale feed is state 'ok' and
                             # surfaces as the badge on /admin/import-health, not as an alert.
                             (f"No successful import for {age:g}h "
                              f"(expected every {f['cadence_hours']:g}h + {f['grace_hours']:g}h grace)."
                              if age is not None else "Import is overdue."),
                             1, f.get("deep_link") or "/commcalc/upload",
                             "Fix channel / Upload manually"))
        elif f["state"] == "never":
            out.append(_item("import", f"feed:{f['feed_key']}", "warning", f.get("label") or f["feed_key"],
                             "This import has never delivered any data. Set it up, upload the file "
                             "manually, or disable the feed if this tenant doesn't use it.",
                             1, f.get("deep_link") or "/commcalc/upload",
                             "Fix channel / Upload manually"))
    return out


# ── STORE COVERAGE (the "attention must clear when the fix is done" rule) ────────────────────
# 2026-07-26 BUG (user-reported): "the notification to resolve store mapping worked, but after the mapping
# was done it still shows that the mapping needs to be done." Root cause: this provider only asked whether
# commcalc.store_mapping contains the store, while the page it deep-links to (/commcalc/store-match — the
# only user-facing store-mapping surface) fixes things by writing an EXPLICIT commcalc.store_aliases row;
# it never inserts a store_mapping row (no endpoint does). So completing the exact flow the alert sent the
# admin to could NEVER clear the alert. It also compared raw lowercased strings, so a punctuation /
# suite-token difference read as "unmapped" even where the resolver matches it.
#
# A store COUNTS AS COVERED when the app can actually attach data to it, which is true either way:
#   (a) commcalc.store_mapping knows it — by store_code OR store_address — compared with commcalc's OWN
#       normalizer (_norm_store_match: case-fold, punctuation → space, drop suite/unit tokens), so this
#       check and the Store-Matching UI can never disagree about what "the same store" means; or
#   (b) an explicit commcalc.store_aliases row TARGETS its store_code — i.e. an admin has confirmed a
#       mapping to this store, which is exactly what the "Map stores" flow writes.
# store_mapping rows count REGARDLESS of is_active because the resolvers (commcalc._store_maps /
# _store_code_resolver) ignore that flag — coverage must mirror the resolver, not a tidier rule. An
# INACTIVE storeops store is skipped: a closed store is not a mapping gap.
def _store_norm():
    """The shared store-name normalizer, via a LAZY GUARDED import (same pattern as the carrier_map
    provider): never duplicate commcalc's matching logic here. If that import ever fails we degrade to the
    previous exact strip/lower compare — stricter, never wrong."""
    try:
        from app.modules.commcalc.router import _norm_store_match
        if isinstance(_norm_store_match("1 Main St, Ste 4"), str):
            return _norm_store_match
    except Exception:
        pass
    return lambda s: (s or "").strip().lower()


def _uncovered_stores(stores, mapping, aliases, norm=None):
    """PURE (no I/O, unit-testable): the storeops stores the app cannot attach data to. Coverage rule in
    the block comment above."""
    norm = norm or _store_norm()
    mapped_codes = {norm(m.get("store_code")) for m in mapping if (m.get("store_code") or "").strip()}
    mapped_addrs = {norm(m.get("store_address")) for m in mapping if (m.get("store_address") or "").strip()}
    alias_codes = {(a.get("store_code") or "").strip().upper()
                   for a in aliases if (a.get("store_code") or "").strip()}
    out = []
    for s in stores:
        if s.get("is_active") is False:
            continue                       # closed store — not a mapping gap
        code = (s.get("store_code") or "").strip()
        addr = (s.get("address") or "").strip()
        if not code and not addr:
            continue                       # nothing identifiable to map
        if code and norm(code) in mapped_codes:
            continue                       # (a) store_mapping knows the code
        if addr and norm(addr) in mapped_addrs:
            continue                       # (a) store_mapping knows the address
        if code and code.upper() in alias_codes:
            continue                       # (b) an explicit store-matching rule points at this store
        out.append(s)
    return out


@register_provider("upload_duty", label="Daily uploads owed / unassigned", group="import", cost="cheap")
def _p_upload_duty(client, org_id, ctx):
    """Daily-upload duties that need an ADMIN's attention (owner directive 2026-08-09).

    Deliberately narrow: the assignee's own to-do list is NOT surfaced here — this feed is
    admin-gated, and a rep who owes today's MA upload would never see it. They get it from
    /commcalc/upload-duties?mine=1 on login. What an admin needs to know is the two things only they
    can fix: a duty nobody owns, and an auto-pull that FAILED and has therefore fallen back to a
    human — the owner's "the user will not be able to handle the error ... communicate it to the
    designated person"."""
    try:
        from app.modules.commcalc.router import _duty_rows
        duties = _duty_rows(client, org_id)
    except Exception:
        return []
    out = []
    for d in duties:
        if d.get("urgent"):
            who = d.get("assignee") or "nobody (unassigned)"
            rng = d.get("date_range") or {}
            span = f" for {rng.get('start')} → {rng.get('end')}" if rng.get("start") else ""
            out.append({
                "severity": "error", "group": "import",
                "label": f"Automatic pull failed — {d.get('label')}",
                "detail": (f"Needs a manual upload{span}. Assigned to {who}. "
                           f"Portal said: {(d.get('auto_error') or 'no detail')[:160]}"),
                "count": 1, "deep_link": d.get("upload_endpoint") or "/commcalc/upload",
                "deep_link_label": "Upload it",
            })
        elif d.get("unassigned"):
            out.append({
                "severity": "warning", "group": "import",
                "label": f"Daily upload has no owner — {d.get('label')}",
                "detail": "Nobody is assigned, so no one is prompted or reminded for it.",
                "count": 1, "deep_link": "/commcalc/connectors",
                "deep_link_label": "Assign someone",
            })
    return out


@register_provider("unmapped_stores", label="Stores the system can't resolve",
                   group="mapping", cost="cheap")
def _p_unmapped_stores(client, org_id, ctx):
    """PENDING MAPPING (cheap — config tables only, org-scoped): stores the app can't attach data to, plus
    mapped stores with no market. Each item deep-links to the page where DOING the fix clears the item."""
    try:
        stores = (client.schema("storeops").table("stores").select("store_code,address,market,is_active")
                  .eq("org_id", org_id).limit(5000).execute().data) or []
    except Exception:
        stores = []
    try:
        mapping = (client.schema("commcalc").table("store_mapping")
                   .select("store_code,store_address,market,is_active")
                   .eq("org_id", org_id).limit(5000).execute().data) or []
    except Exception:
        mapping = []
    try:   # the EXPLICIT mappings the Store-Matching page writes (mig 023; absent → treated as none)
        aliases = (client.schema("commcalc").table("store_aliases").select("alias,store_code")
                   .eq("org_id", org_id).limit(5000).execute().data) or []
    except Exception:
        aliases = []
    if not stores and not mapping:
        return []
    missing = _uncovered_stores(stores, mapping, aliases)
    # blank market only for ACTIVE mapped stores (a closed store's market changes no live report)
    no_market = [m for m in mapping
                 if m.get("is_active") is not False and not (m.get("market") or "").strip()]
    out = []
    if missing:
        out.append(_item("mapping", "stores_unmapped", "warning", "Stores the system can't resolve",
                         f"{len(missing)} store(s) in your StoreOps roster are not in the store map and no "
                         f"store-matching rule points at them, so data arriving for them can land under an "
                         f"unknown store. Open Store Matching and confirm each unrecognised store name "
                         f"against the right store — that saves the mapping and clears this. e.g. "
                         + ", ".join(sorted((s.get("address") or s.get("store_code") or "?")
                                            for s in missing)[:3]),
                         len(missing), "/commcalc/store-match", "Map stores"))
    if no_market:
        out.append(_item("mapping", "stores_no_market", "info", "Mapped stores with no market",
                         f"{len(no_market)} mapped store(s) have a blank market, so market filters and "
                         f"market roll-ups skip them. Set each store's market on Commission Settings → "
                         f"Stores & Markets.",
                         len(no_market), "/commcalc/settings", "Set markets"))
    return out


@register_provider("duplicate_uploads", label="Duplicate rows skipped on import",
                   group="duplicate", cost="cheap")
def _p_duplicate_uploads(client, org_id, ctx):
    """DUPLICATE DATA — surfaces signals the system ALREADY produces (no new detection in v1):
    upload_trace notes that report duplicates dropped, and email/ftp attachments the sweep SKIPPED."""
    now = ctx.get("now") or _now()
    since = (now - timedelta(days=14)).isoformat()
    dupes, skipped = [], 0
    try:
        rows = (client.schema("commcalc").table("upload_trace")
                .select("id,created_at,upload_type,filename,note,skipped,status")
                .eq("org_id", org_id).gte("created_at", since)
                .order("created_at", desc=True).limit(200).execute().data) or []
    except Exception:
        rows = []
    for r in rows:
        txt = " ".join(str(r.get(k) or "") for k in ("note", "skipped")).lower()
        if "duplicate" in txt or "dupe" in txt:
            dupes.append(r)
    try:
        ep = (client.schema("commcalc").table("email_processed")
              .select("id,processed_at,filename,status,detail")
              .eq("org_id", org_id).eq("status", "skipped")
              .gte("processed_at", since).limit(200).execute().data) or []
        skipped = len(ep)
    except Exception:
        skipped = 0
    out = []
    if dupes:
        n = len(dupes)
        eg = ", ".join(sorted({(d.get("upload_type") or d.get("filename") or "?") for d in dupes})[:3])
        out.append(_item("duplicate", "upload_dupes", "warning", "Duplicate rows dropped on import",
                         f"{n} import(s) in the last 14 days reported duplicate rows being skipped ({eg}). "
                         f"Re-uploading the same file, or two channels delivering it, both look like this.",
                         n, "/commcalc/upload", "Review upload history"))
    if skipped:
        out.append(_item("duplicate", "email_skipped", "info", "Email attachments skipped by the sweep",
                         f"{skipped} attachment(s) in the last 14 days were skipped (already processed or "
                         f"refused by a guard). Expected for a re-sent file; investigate if unexpected.",
                         skipped, "/commcalc/email-imports", "Open email imports"))
    return out


@register_provider("duplicate_closings", label="Duplicate daily-closing submissions",
                   group="duplicate", cost="cheap")
def _p_duplicate_closings(client, org_id, ctx):
    """DUPLICATE DATA — the EXISTING closing double-submit fingerprint (2+ rows for the same store +
    employee + close_date, the shape GET /closing/duplicates reports). Bounded to the last 30 days."""
    now = ctx.get("now") or _now()
    since = (now - timedelta(days=30)).date().isoformat()
    try:
        rows = (client.schema("commcalc").table("daily_closing")
                .select("store_code,employee_name,close_date,released_at")
                .eq("org_id", org_id).gte("close_date", since).limit(20000).execute().data) or []
    except Exception:
        return []
    groups = {}
    for r in rows:
        k = ((r.get("store_code") or ""), (r.get("employee_name") or "").strip().lower(),
             str(r.get("close_date") or ""))
        groups.setdefault(k, []).append(r)
    dup = [g for g in groups.values() if len(g) > 1 and not any(x.get("released_at") for x in g)]
    if not dup:
        return []
    return [_item("duplicate", "closing_dupes", "warning", "Duplicate daily-closing submissions",
                  f"{len(dup)} store/rep/day combination(s) in the last 30 days have more than one "
                  f"un-released closing row — totals can double-count until one is released.",
                  len(dup), "/closing/duplicates", "Review duplicates")]


@register_provider("carrier_category_map", label="Unmapped carrier compensation categories",
                   group="mapping", cost="heavy")
def _p_carrier_categories(client, org_id, ctx):
    """PENDING MAPPING (heavy — scans raw_comp_report): carrier comp categories with no bucket rule, so
    their $ falls into UNMAPPED on the canonical ledger. Reuses commcalc's own matcher (no second
    implementation) via a lazy import, so it can never disagree with the mapping page."""
    try:
        from app.modules.commcalc import carrier_map
    except Exception:
        return []
    try:
        rules = carrier_map.load_rules(client, org_id, None)
        rows = _scan_all(client, "commcalc", "raw_comp_report",
                         "compensation_type,payment_amount", org_id=org_id)
    except Exception:
        return []
    agg = {}
    for r in rows:
        cat = (r.get("compensation_type") or "").strip()
        if cat:
            agg[cat] = agg.get(cat, 0.0) + (float(r.get("payment_amount") or 0) or 0.0)
    unmapped = {c: a for c, a in agg.items() if not carrier_map.match_rule(rules, c)}
    if not unmapped:
        return []
    amt = round(sum(unmapped.values()), 2)
    return [_item("mapping", "carrier_categories", "warning", "Unmapped carrier comp categories",
                  f"{len(unmapped)} compensation category value(s) worth ${amt:,.2f} have no bucket "
                  f"rule, so they land in UNMAPPED on the commission ledger.",
                  len(unmapped), "/commcalc/commission-category-map", "Map categories")]


@register_provider("product_mrc", label="Plans with no MRC mapped", group="mapping", cost="heavy")
def _p_product_mrc(client, org_id, ctx):
    """PENDING MAPPING (heavy — scans raw_mi): subscriber plans whose MRC the catalogue can't resolve,
    which silently pays $0 on every pct-of-MRC rule. Uses the installment engine's own resolver."""
    try:
        from app.modules.commcalc import installment_engine
    except Exception:
        return []
    try:
        catalog = installment_engine._load_product_mrc(client, org_id)
        rows = _scan_all(client, "commcalc", "raw_mi", "customer_plan,carrier_id", org_id=org_id)
    except Exception:
        return []
    seen, unmatched = set(), set()
    for r in rows:
        plan = (r.get("customer_plan") or "").strip()
        if not plan:
            continue
        key = (plan, r.get("carrier_id"))
        if key in seen:
            continue
        seen.add(key)
        if installment_engine._catalog_mrc(catalog, r.get("carrier_id"), plan) is None:
            unmatched.add(plan)
    if not unmatched:
        return []
    # 2026-07-26: deep link moved /commcalc/mapping → /commcalc/payout-schedules. The Mapping page is a
    # link hub with no MRC card at all; the plan-MRC catalogue (POST /commcalc/product-mrc + the price-sheet
    # import + the coverage check) lives on Payout Schedules, so THAT is where doing the fix clears this.
    return [_item("mapping", "product_mrc", "warning", "Subscriber plans with no MRC mapped",
                  f"{len(unmatched)} plan name(s) have no monthly-recurring-charge in the catalogue "
                  f"(e.g. {', '.join(sorted(unmatched)[:3])}), so % -of-MRC pay resolves to $0. Add or "
                  f"import each plan's MRC under Payout Schedules → Plan MRC.",
                  len(unmatched), "/commcalc/payout-schedules", "Map plan MRC")]


@register_provider("plan_coverage", label="Sellers with no commission plan", group="mapping", cost="heavy")
def _p_plan_coverage(client, org_id, ctx):
    """PENDING MAPPING (heavy — runs the read-only coverage preview): reps who SOLD this period but have
    no plan attached, so they legitimately pay $0 with no error anywhere. Calls the SAME read-only
    diagnostic the coverage page uses; it writes nothing and never triggers a calc."""
    try:
        from app.modules.commcalc import commission_engine
    except Exception:
        return []
    period = (ctx.get("now") or _now()).strftime("%B %Y")
    try:
        prev = commission_engine.preview(client, org_id, period, coverage=True)
        cov = prev.get("coverage") or {}
    except Exception:
        return []
    n = int(cov.get("unassigned_count") or 0)
    if not n:
        return []
    amt = float(cov.get("unassigned_ext_price") or 0)
    return [_item("mapping", "plan_coverage", "warning", "Sellers with no commission plan",
                  f"{n} rep(s) sold ${amt:,.2f} in {period} with no commission plan attached — they pay "
                  f"$0 and nothing else reports it.",
                  n, "/commcalc/commission-plans", "Assign plans")]


def collect_attention(client, org_id, *, deep=False, feed_h=None):
    """Run every registered provider for ONE org. Exception-isolated per provider."""
    now = _now()
    ctx = {"now": now, "feed_health": feed_h if feed_h is not None else feed_health(client, org_id)}
    items, deferred, errors = [], [], []
    for p in PROVIDERS:
        if p["cost"] == "heavy" and not deep:
            deferred.append({"key": p["key"], "label": p["label"], "group": p["group"]})
            continue
        try:
            for it in (p["fn"](client, org_id, ctx) or []):
                items.append({**it, "provider": p["key"]})
        except Exception as e:
            errors.append({"key": p["key"], "error": str(e)[:200]})
    rank = {"error": 0, "warning": 1, "info": 2}
    items.sort(key=lambda i: (rank.get(i.get("severity"), 3), i.get("group") or "", i.get("label") or ""))
    return {
        "items": items, "deferred": deferred, "provider_errors": errors,
        "counts": {
            "total": len(items),
            "error": sum(1 for i in items if i.get("severity") == "error"),
            "warning": sum(1 for i in items if i.get("severity") == "warning"),
            "import": sum(1 for i in items if i.get("group") == "import"),
            "mapping": sum(1 for i in items if i.get("group") == "mapping"),
            "duplicate": sum(1 for i in items if i.get("group") == "duplicate"),
            # groups contributed by the platform-core providers (setup gaps / system errors)
            "config": sum(1 for i in items if i.get("group") == "config"),
            "system": sum(1 for i in items if i.get("group") == "system"),
        },
        "deep": bool(deep), "generated_at": _iso(now),
        "ready": (ctx["feed_health"] or {}).get("ready", True),
        "hint": (ctx["feed_health"] or {}).get("hint"),
    }


# ── Permission gates ─────────────────────────────────────────────────────────────────────────────────
# VIEW uses the EXISTING admin-ish concept (super_admin → the `admin` module → company-wide scope → the
# 'admin' role) — the same chain `_can_view_failures` and the Settings link already use. No parallel gate
# is invented. EDIT additionally requires the registered 'import_health' settings area, so a tenant can
# grant/deny "who may change an import schedule" per role exactly like every other setting.
def _caller(client, authorization, active_org):
    from app.modules.core.router import _uid_from_token, _resolve_caller
    uid = _uid_from_token(authorization)
    if not uid:
        return None
    return _resolve_caller(client, uid, active_org)


def can_view_attention(caller):
    """PURE. Mirrors the existing admin-ish gate (see module docstring)."""
    if not caller:
        return False
    if caller.get("super_admin"):
        return True
    perms = caller.get("perms") or {}
    pg = (perms.get("pages") or {}).get("/admin/import-health")
    if pg is not None:
        return bool(pg)
    if (perms.get("modules") or {}).get("admin"):
        return True
    return perms.get("scope") == "all" or (caller.get("role") or "").lower() == "admin"


def _scope_org(caller, org_id):
    """The org this request may act on. A super-admin keeps the client-supplied org (acting-as-tenant);
    anyone else is CLAMPED to their own membership org — defence in depth if enforcement is toggled off."""
    if caller and caller.get("super_admin"):
        return (org_id or "").strip() or caller.get("org_id") or ORG_ID
    return (caller or {}).get("org_id") or (org_id or "").strip()


def _gate(authorization, active_org, org_id, *, edit=False):
    client = sb()
    caller = _caller(client, authorization, active_org)
    if not caller:
        raise HTTPException(401, "not authenticated")
    if not can_view_attention(caller):
        raise HTTPException(403, "Import health is admin-only. Grant /admin/import-health to a role to share it.")
    if edit:
        from app.modules.core.router import _can_edit_setting
        if not _can_edit_setting(caller, "import_health"):
            raise HTTPException(403, "You don't have permission to edit import schedules.")
    org = _scope_org(caller, org_id)
    if not org:
        raise HTTPException(400, "org_id required")
    return client, caller, org


# ── /attention response memo (nav-perf, 2026-08-04) ──────────────────────────────────────────────────
# MEASURED (production, 2026-08-04): GET /api/v1/core/attention takes **5.1 s** (house) / **5.4 s**
# (Luxelink) because ~25 registered providers each run several sequential Supabase round trips at a
# ~170 ms floor. The frontend `AdminAttention` component re-fires it on EVERY navigation (throttled to
# once per 20 s), so an admin clicking through the menu pays for a full re-scan several times a minute —
# and, until the sibling `async def` → `def` fix below, paid for it ON THE EVENT LOOP.
#
# The payload is a pure function of (org, deep): `collect_attention(client, org, deep)` takes no caller
# argument, so two admins of the same tenant provably receive the same answer. Memoising it per
# (org, deep) for a few seconds therefore changes NOTHING a user can observe except latency.
#
# MULTI-TENANT (contract §2): the key's org is `_scope_org(caller, org_id)` — the SERVER-resolved acting
# org, never the raw query param — so one tenant's payload can never be served to another. The
# permission gate `_gate()` runs on EVERY request, before and independently of the memo, so a
# non-admin still gets a 403 and a revoked permission takes effect immediately.
#
# TUNABLE / REVERSIBLE: `ATTENTION_CACHE_TTL_S` (default 45 s). Set it to 0 to disable the memo
# entirely — that is a complete, no-deploy revert to today's behaviour. `?fresh=1` always bypasses it
# (the admin page's "Run full check" button and anyone re-checking after a fix).
_ATTN_MEMO: dict = {}
_ATTN_LOCK = threading.Lock()
_ATTN_MAX = 128          # hard cap: a memo must never become a memory leak on a many-tenant instance


def _attention_ttl() -> float:
    """Seconds to hold a payload. Any unparseable value falls back to the default (never crashes)."""
    try:
        return max(0.0, float(os.getenv("ATTENTION_CACHE_TTL_S", "45")))
    except Exception:
        return 45.0


def _attention_memo_get(org, deep):
    ttl = _attention_ttl()
    if not ttl:
        return None
    hit = _ATTN_MEMO.get((org, bool(deep)))
    return hit[1] if hit and hit[0] > time.monotonic() else None


def _attention_memo_put(org, deep, payload):
    ttl = _attention_ttl()
    if not ttl:
        return
    now = time.monotonic()
    with _ATTN_LOCK:
        _ATTN_MEMO[(org, bool(deep))] = (now + ttl, payload)
        if len(_ATTN_MEMO) > _ATTN_MAX:
            for k in [k for k, v in list(_ATTN_MEMO.items()) if v[0] <= now]:
                _ATTN_MEMO.pop(k, None)
            while len(_ATTN_MEMO) > _ATTN_MAX:
                _ATTN_MEMO.pop(next(iter(_ATTN_MEMO)), None)


def _attention_memo_clear():
    """Drop everything. Used by the harness; also a safe hook for a future 'I fixed it' invalidation."""
    with _ATTN_LOCK:
        _ATTN_MEMO.clear()


# ── Endpoints ────────────────────────────────────────────────────────────────────────────────────────
@router.get("/import-feeds")
def get_import_feeds(org_id: str = ORG_ID, authorization: str = Header(default=""),
                           x_active_org: str = Header(default="")):
    """The tenant's import registry + live freshness. Derives any missing feeds on read (idempotent), so
    a brand-new tenant is covered without a seed step."""
    client, caller, org = _gate(authorization, x_active_org, org_id)
    from app.modules.core.router import _can_edit_setting
    h = feed_health(client, org)
    return {**h, "org_id": org, "can_edit": _can_edit_setting(caller, "import_health")}


@router.post("/import-feeds/sync")
def sync_import_feeds(org_id: str = ORG_ID, authorization: str = Header(default=""),
                            x_active_org: str = Header(default="")):
    """Explicit re-derive. Idempotent: only feed_keys that don't exist yet are inserted; an admin's
    cadence/label/enabled edits and any disabled feed are never touched."""
    client, caller, org = _gate(authorization, x_active_org, org_id, edit=True)
    before, _ = load_feeds(client, org, persist=False, force=False)
    h = feed_health(client, org, force=True)
    return {"ok": True, "org_id": org, "feeds": len(h.get("feeds") or []),
            "added": max(0, len(h.get("feeds") or []) - len(before)), "derived_new": h.get("derived_new", 0)}


@router.post("/import-feeds")
def create_import_feed(body: dict, org_id: str = ORG_ID, authorization: str = Header(default=""),
                             x_active_org: str = Header(default="")):
    """Register a feed the auto-derivation can't know about (a vendor emailing a file to a person, etc.)."""
    client, caller, org = _gate(authorization, x_active_org, org_id, edit=True)
    key = (body.get("feed_key") or "").strip() or f"custom:{_slug(body.get('label'))}"
    cad = float(body.get("cadence_hours") or _MANUAL_CADENCE_HOURS)
    row = {
        "org_id": org, "feed_key": key,
        "label": (body.get("label") or key)[:200],
        "module": (body.get("module") or "commissions")[:60],
        "source_type": (body.get("source_type") or "manual_expected")[:40],
        "cadence_hours": cad,
        "grace_hours": float(body.get("grace_hours") if body.get("grace_hours") is not None
                             else default_grace(cad)),
        # H6 (2026-08-05): rendered as the "Fix / Upload →" link on /admin/import-health.
        "deep_link": safe_href((body.get("deep_link") or "/commcalc/upload")[:300], "/commcalc/upload"),
        "evidence": body.get("evidence") or [],
        "enabled": bool(body.get("enabled", True)),
        "auto_derived": False, "derived_from": None,
        "notes": (body.get("notes") or None), "updated_by": (caller.get("role") or "admin"),
    }
    try:
        res = client.schema("core").table("import_feed").insert(row).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not create the feed ({str(e)[:160]}). {MIG_HINT}")
    return {"ok": True, "feed": (res.data[0] if res.data else row)}


_EDITABLE = ("label", "module", "source_type", "cadence_hours", "grace_hours", "deep_link",
             "evidence", "enabled", "muted_until", "notes")


@router.put("/import-feeds/{feed_id}")
def update_import_feed(feed_id: str, body: dict, org_id: str = ORG_ID,
                             authorization: str = Header(default=""), x_active_org: str = Header(default="")):
    """Edit one feed (cadence / grace / deep link / enabled / snooze). org-scoped on the UPDATE itself."""
    client, caller, org = _gate(authorization, x_active_org, org_id, edit=True)
    patch = {k: body[k] for k in _EDITABLE if k in body}
    if not patch:
        raise HTTPException(400, "nothing to update")
    for k in ("cadence_hours", "grace_hours"):
        if k in patch and patch[k] is not None:
            try:
                patch[k] = max(0.0, float(patch[k]))
            except Exception:
                raise HTTPException(400, f"{k} must be a number of hours")
    patch["updated_at"] = _iso(_now())
    patch["updated_by"] = caller.get("role") or "admin"
    try:
        client.schema("core").table("import_feed").update(patch)\
              .eq("org_id", org).eq("id", feed_id).execute()
    except Exception as e:
        raise HTTPException(400, f"Could not update the feed ({str(e)[:160]}). {MIG_HINT}")
    return {"ok": True, "id": feed_id, "patch": {k: patch[k] for k in patch if k != "updated_by"}}


@router.delete("/import-feeds/{feed_id}")
def delete_import_feed(feed_id: str, org_id: str = ORG_ID, authorization: str = Header(default=""),
                             x_active_org: str = Header(default="")):
    """Delete a HAND-ADDED feed. An auto-derived feed cannot be deleted (the next read would recreate it) —
    disable it instead, which the derivation deliberately never reverses."""
    client, caller, org = _gate(authorization, x_active_org, org_id, edit=True)
    try:
        rows = (client.schema("core").table("import_feed").select("id,auto_derived")
                .eq("org_id", org).eq("id", feed_id).limit(1).execute().data) or []
    except Exception as e:
        raise HTTPException(400, f"{MIG_HINT} ({str(e)[:120]})")
    if not rows:
        raise HTTPException(404, "feed not found")
    if rows[0].get("auto_derived"):
        raise HTTPException(400, "This feed is auto-derived from your import configuration — disable it "
                                 "instead of deleting (a delete would be recreated on the next read).")
    client.schema("core").table("import_feed").delete().eq("org_id", org).eq("id", feed_id).execute()
    return {"ok": True, "id": feed_id}


@router.get("/attention")
def get_attention(org_id: str = ORG_ID, deep: int = 0, fresh: int = 0,
                  authorization: str = Header(default=""),
                  x_active_org: str = Header(default="")):
    """CONSOLIDATED admin-attention feed backing the login popup + the persistent indicator.
    deep=0 (default, what the popup calls): cheap providers only — never makes a login slow.
    deep=1 (the admin page / "Run full check"): also runs the heavy scans listed in `deferred`.
    fresh=1: bypass the short per-org memo (see _ATTN_MEMO above) and re-scan now.
    A non-admin caller is 403'd by _gate — the popup simply never renders for them.

    `def`, NOT `async def` (nav-perf 2026-08-04): the body is entirely blocking Supabase I/O with no
    `await` anywhere, and this scan takes ~5 s in production. As an `async def` it held the SINGLE
    uvicorn event loop for those 5 s, so every other request from every other user — every module, every
    tenant — queued behind one admin's navigation. As a `def`, FastAPI runs it in the threadpool and
    nothing else stalls. The response is byte-identical either way."""
    client, caller, org = _gate(authorization, x_active_org, org_id)
    if not fresh:
        memo = _attention_memo_get(org, deep)
        if memo is not None:
            return {**memo, "org_id": org}
    payload = collect_attention(client, org, deep=bool(deep))
    _attention_memo_put(org, deep, payload)
    return {**payload, "org_id": org}
