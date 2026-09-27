"""PROOF + LOCK: setup documents — the per-carrier required uploads, the setup wizard, automation history, reminders
(owner 2026-09-27, index §39).

Owner, verbatim: *"Create a list of documents which need to be uploaded for each carrier … as a part of super admin
console and attach them by default when the tenant is set up, then on the tenant side the first page which opens up is
the set up wizard which requires the tenant to upload these files, tell the Tenant where to download those files from
… tell them to add credentials if they want automated data updating if we have a successful history of doing that,
if we don't have a successful history then don't ask and keep the tenant on manual uploads, create reminders for the
tenant to upload the files on the period as chosen by the tenant."*

  A. migration 1028: additive + idempotent; the seed only fills empty columns; live tenants are stamped done (never
     redirected); every seeded key is a real house kind
  B. which documents a tenant owes: required + visible; a generic document only for a business type that uses carriers
  C. automation history: org-days of automated landings (email/FTP traces + portal-sweep job runs), threshold + window
     from the kind's own config; nothing offered below the bar
  D. the gate and the reminders: pending/skip/done; one reminder per cycle; never before the tenant schedules it
  E. the super-admin edit is validated; house endpoints are super-admin only; the tenant's writes use the import-health
     edit gate
  F. WIRING — one home per fact: import_health derives the document feeds through setup_documents.feed_candidate; the
     one sweep status writer appends job_run; the hourly connector-health tick sends reminders through the alert
     pipeline; the wizard takes ownership of the feed; no portal / brand word in code; the frontend gate is the pure
     lib/setup-gate; the recipient scope is listed on the alerts page
  G. registered in the index

PURE / DB-FREE.
"""
import os
import re
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.modules.commcalc import setup_documents as SD  # noqa: E402
from app.modules.commcalc import report_kinds as RK  # noqa: E402
from app.modules.commcalc.setup_router import clean_house_patch  # noqa: E402

FAILS = []


def check(name, ok, why=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  -- " + why))
    if not ok:
        FAILS.append(name)


def read(p):
    with open(os.path.join(ROOT, p), encoding="utf-8") as f:
        return f.read()


NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
MIG = read("database/migrations/1028_setup_documents.sql")
mig_code = "\n".join(ln for ln in MIG.split("\n") if not ln.strip().startswith("--"))

# ── A. migration ─────────────────────────────────────────────────────────────────────────────────────────────────
print("A. migration 1028")
for col in RK.SETUP_COLS:
    check("A1 adds report_kind.%s idempotently" % col,
          re.search(r"ALTER TABLE commcalc\.report_kind ADD COLUMN IF NOT EXISTS %s\b" % col, mig_code) is not None)
check("A2 constraints are guarded (re-runnable)", mig_code.count("IF NOT EXISTS (SELECT 1 FROM pg_constraint") >= 2)
check("A3 cadence CHECK = setup_documents.CADENCES",
      "CHECK (default_cadence IN ('daily', 'weekly', 'monthly'))" in mig_code and SD.CADENCES == ("daily", "weekly", "monthly"))
check("A4 tenants.documents_setup_done_at added AND every existing tenant stamped done (no live company redirected)",
      "ADD COLUMN IF NOT EXISTS documents_setup_done_at" in mig_code
      and "UPDATE storeops.tenants SET documents_setup_done_at = now() WHERE documents_setup_done_at IS NULL" in mig_code)
seed_updates = re.findall(r"UPDATE commcalc\.report_kind SET (.*?)WHERE (.*?);", mig_code, re.S)
check("A5 seed UPDATEs touch HOUSE rows only", seed_updates and all(
    "org_id = '00000000-0000-0000-0000-000000000001'" in w for _s, w in seed_updates))
fills = [w for s, w in seed_updates if "download_steps" in s]
check("A6 every steps-seeding UPDATE only fills an empty row (an edit survives a re-run)",
      fills and all("download_steps IS NULL" in w for w in fills))
seeded_keys = set(re.findall(r"key = '([a-z_]+)'", mig_code)) | set(
    k for grp in re.findall(r"key IN \(([^)]*)\)", mig_code) for k in re.findall(r"'([a-z_]+)'", grp))
house_keys = {k["key"] for k in RK.HOUSE_KINDS}
check("A7 every seeded key is a real house kind (%d)" % len(seeded_keys), seeded_keys and seeded_keys <= house_keys,
      "unknown: %s" % sorted(seeded_keys - house_keys))
check("A8 the DLAR carrier scope is set only while still unscoped",
      "key IN ('dlar_rep', 'dlar_store') AND applies_to_carrier = '{}'" in mig_code)
check("A9 REVERT note + schema reload", "-- REVERT:" in MIG and "NOTIFY pgrst, 'reload schema';" in mig_code)
required_seeded = {m.group(1) for m in re.finditer(r"SET required = true[^;]*?key = '([a-z_]+)'", mig_code, re.S)}
check("A10 required documents seeded (%s)" % ", ".join(sorted(required_seeded)), len(required_seeded) >= 8)

# ── B. which documents ───────────────────────────────────────────────────────────────────────────────────────────
print("B. which documents a tenant owes")


def kind(key, **kw):
    base = RK._k(key=key, label=key.title(), landing="other", sort_order=10)
    base.update(kw)
    n = RK.normalise_row({**base, "org_id": RK.HOUSE_ORG})
    n["_source"] = "house"
    return n


k_generic = kind("sales", required=True, upload_types=["daily_sales"], default_cadence="daily")
k_carrier = kind("carrier_rpt", required=True, upload_types=["carrier_x"], applies_to_carrier=["acme"])
k_vert = kind("franchise_rpt", required=True, applies_to_vertical=["franchise"], evidence_table="franchise_report",
              upload_path="/accounts/franchise")
k_optional = kind("optional_rpt", required=False, upload_types=["opt"])
k_pre1028 = kind("old")
check("B1 a row with no setup columns reads defaults (nothing required — the safe direction)",
      SD.setup_fields(k_pre1028) == SD.SETUP_DEFAULTS | {"required": False})
docs = SD.required_documents([k_generic, k_carrier, k_vert, k_optional], uses_carriers=True)
check("B2 required + visible only (optional dropped)", [d["key"] for d in docs] == ["sales", "carrier_rpt", "franchise_rpt"])
docs2 = SD.required_documents([k_generic, k_carrier, k_vert, k_optional], uses_carriers=False)
check("B3 a generic document is not owed by a business type that uses no carriers",
      [d["key"] for d in docs2] == ["carrier_rpt", "franchise_rpt"])
probes = SD.evidence_probes(kind("x", upload_types=["dlar_rep"]))
check("B4 arrival is proven by the upload route AND the portal sweep that lands it",
      {"kind": "upload_trace", "upload_type": "dlar_rep"} in probes and {"kind": "sweep", "table": "dlar_sweep_config"} in probes)
check("B5 a document with no route is proven by its landing table",
      SD.evidence_probes(k_vert) == [{"kind": "raw_table", "schema": "commcalc", "table": "franchise_report", "column": "created_at"}])
fc = SD.feed_candidate(k_generic)
check("B6 the feed is registered DISABLED (nobody is reminded until they schedule it)", fc and fc["enabled"] is False)
check("B7 feed key / module / deep link", fc["feed_key"] == "kind:sales" and fc["module"] == "setup" and fc["deep_link"] == SD.WIZARD_PATH)
check("B8 default cadence → hours through import_health.freq_hours", fc["cadence_hours"] == 24.0)
check("B9 cadence_of rounds hours to the nearest period", SD.cadence_of(24) == "daily" and SD.cadence_of(168) == "weekly"
      and SD.cadence_of(720) == "monthly" and SD.cadence_of(None) is None)
check("B10 upload_path: route → wizard itself; own page; intake kinds → intake",
      SD.upload_path(k_generic) is None and SD.upload_path(k_vert) == "/accounts/franchise"
      and SD.upload_path(kind("c", landing="commission")) == SD.INTAKE_PATH)

# ── C. automation history ────────────────────────────────────────────────────────────────────────────────────────
print("C. automation history")


def tr(org, day, src="email_sweep", ut="daily_sales", saved=10, status="ok"):
    return {"org_id": org, "created_at": f"2026-09-{day:02d}T08:00:00+00:00", "source": src, "upload_type": ut,
            "rows_saved": saved, "status": status}


traces = [tr("o1", 20), tr("o1", 20), tr("o1", 21), tr("o2", 22),              # 3 org-days (dup same day collapses)
          tr("o3", 23, src="manual"), tr("o4", 23, saved=0), tr("o5", 23, status="error")]
runs = SD.automation_runs(traces, [])
check("C1 org-days, not files; manual / empty / failed never count",
      {(o, d) for (o, d, _r) in runs["daily_sales"]} == {("o1", "2026-09-20"), ("o1", "2026-09-21"), ("o2", "2026-09-22")})
p = SD.automation_proof(k_generic, runs, NOW)
check("C2 3 org-days in 60 days = proven, via e-mail", p["proven"] and p["runs"] == 3 and p["how"] == "email"
      and p["setup_link"] == SD.EMAIL_AUTOMATION_PATH)
p2 = SD.automation_proof(kind("s2", required=True, upload_types=["daily_sales"], automation_min_runs=4), runs, NOW)
check("C3 below the kind's own threshold → manual, no offer", not p2["proven"] and p2["setup_link"] is None and p2["how"] is None)
p3 = SD.automation_proof(kind("s3", required=True, upload_types=["daily_sales"], automation_window_days=4), runs, NOW)
check("C4 outside the kind's own window → not counted", p3["runs"] == 0 and not p3["proven"])
jobs = [{"org_id": "o9", "job_name": "sweep:dlar_sweep_config", "status": "succeeded", "finished_at": f"2026-09-{d}T06:00:00+00:00"}
        for d in (10, 11, 12)] + [{"org_id": "o9", "job_name": "sweep:dlar_sweep_config", "status": "failed",
                                   "finished_at": "2026-09-13T06:00:00+00:00"}]
runs2 = SD.automation_runs([], jobs)
p4 = SD.automation_proof(kind("d", upload_types=["dlar_rep"]), runs2, NOW)
check("C5 portal-sweep job runs count through import_health._SWEEP_SPECS; failures do not",
      p4["proven"] and p4["runs"] == 3 and p4["how"] == "portal" and p4["setup_link"] == "/commcalc/dlar/sweep")
check("C6 no history at all → manual", not SD.automation_proof(k_carrier, {}, NOW)["proven"])

# ── D. gate + reminders ──────────────────────────────────────────────────────────────────────────────────────────
print("D. gate + reminders")
d_up = {"key": "a", "required": True, "uploaded": True, "skipped": False}
d_need = {"key": "b", "required": True, "uploaded": False, "skipped": False, "upload_path": "/accounts/franchise"}
d_skip = {"key": "c", "required": True, "uploaded": False, "skipped": True}
d_opt = {"key": "d", "required": False, "uploaded": False, "skipped": False}
g = SD.gate([d_up, d_need, d_skip, d_opt], None)
check("D1 pending = required, not uploaded, not put off", g["active"] and g["pending"] == ["b"])
check("D2 a tenant already done is never gated", not SD.gate([d_need], "2026-09-01")["active"])
check("D3 everything in or put off → gate lifts", SD.gate([d_up, d_skip], None) == SD.gate([d_up, d_skip], None)
      and not SD.gate([d_up, d_skip], None)["active"] and SD.gate([d_up, d_skip], None)["complete"])
check("D4 allow_paths carries the wizard, the intake and each document's own page",
      {SD.WIZARD_PATH, SD.INTAKE_PATH, "/accounts/franchise"} <= set(g["allow_paths"]))
feed = {"feed_key": "kind:sales", "enabled": True, "cadence_hours": 24.0, "updated_at": "2026-09-25T00:00:00+00:00"}
check("D5 not scheduled → never reminded", SD.reminder_ref({**feed, "enabled": False}, {"state": "never"}, NOW) is None)
check("D6 put off → not reminded",
      SD.reminder_ref({**feed, "muted_until": "2026-10-20T00:00:00+00:00"}, {"state": "never"}, NOW) is None)
check("D7 on time → not reminded", SD.reminder_ref(feed, {"state": "ok", "last_success": "2026-09-27T01:00:00+00:00"}, NOW) is None)
fresh = {**feed, "updated_at": "2026-09-27T06:00:00+00:00"}
check("D8 scheduled but never uploaded gets a day's grace", SD.reminder_ref(fresh, {"state": "never"}, NOW) is None)
r1 = SD.reminder_ref(feed, {"state": "overdue", "last_success": "2026-09-24T12:00:00+00:00"}, NOW)
r1b = SD.reminder_ref(feed, {"state": "overdue", "last_success": "2026-09-24T12:00:00+00:00"}, NOW + timedelta(hours=5))
r2 = SD.reminder_ref(feed, {"state": "overdue", "last_success": "2026-09-24T12:00:00+00:00"}, NOW + timedelta(hours=25))
check("D9 overdue → a key; the SAME key within a cycle; a NEW key next cycle (one reminder per period)",
      bool(r1) and r1 == r1b and r2 and r2 != r1)
subj, body = SD.reminder_text({"label": "Sales", "download_steps": "Export it.", "cadence": "daily"}, "Acme")
check("D10 the reminder names the document, where to get it and the page", "Sales" in subj and "Export it." in body
      and SD.WIZARD_PATH in body)

# ── E. validation + gates ────────────────────────────────────────────────────────────────────────────────────────
print("E. super-admin edit validation + endpoint gates")
ok = clean_house_patch({"required": 1, "default_cadence": "weekly", "applies_to_carrier": "Acme, beta",
                        "download_url": "https://x.example", "automation_min_runs": "5"})
check("E1 a clean edit normalises", ok == {"required": True, "default_cadence": "weekly", "applies_to_carrier": ["acme", "beta"],
                                           "download_url": "https://x.example", "automation_min_runs": 5})
for bad, name in (({"default_cadence": "hourly"}, "cadence"), ({"download_url": "javascript:alert(1)"}, "url scheme"),
                  ({"automation_window_days": 999}, "window bound"), ({"evidence_table": "x; drop"}, "table name"),
                  ({"label": ""}, "empty label"), ({"landing": "nowhere"}, "landing")):
    try:
        clean_house_patch(bad)
        check("E2 refuses a bad %s" % name, False, "accepted")
    except (ValueError, TypeError):
        check("E2 refuses a bad %s" % name, True)
rtr = read("backend/app/modules/commcalc/setup_router.py")


def fn(src, name):
    m = re.search(r"def %s\(.*?\n(?=@router\.|\ndef |\nclass )" % name, src + "\n@router.", re.S)
    return m.group(0) if m else ""


for f in ("get_house_kinds", "put_house_kind", "post_house_kind", "reopen_setup"):
    check("E3 %s is super-admin only" % f, "_require_super_admin(authorization, x_active_org)" in fn(rtr, f))
check("E4 the tenant's write uses the import-health EDIT gate",
      "IH._gate(authorization, x_active_org, org_id, edit=True)" in fn(rtr, "put_setup_document"))
check("E5 the tenant reads use the import-health view gate",
      all("IH._gate(authorization, x_active_org, org_id)" in fn(rtr, f) for f in ("get_setup_documents", "get_setup_gate")))

# ── F. wiring ────────────────────────────────────────────────────────────────────────────────────────────────────
print("F. wiring — one home per fact")
ih = read("backend/app/modules/core/import_health.py")
sd_src = read("backend/app/modules/commcalc/setup_documents.py")
check("F1 document feeds are registered in import_health's OWN row shape + idempotent insert, and import_health stays "
      "unaware of the registry (its derivation reads only the tenant's own config)",
      "col in IH._FEED_COLS" in sd_src and 'on_conflict="org_id,feed_key", ignore_duplicates=True' in sd_src
      and "setup_documents" not in ih.replace("import_health", ""))
router = read("backend/app/modules/commcalc/router.py")
m = re.search(r"def _sweep_set_status\(.*?\n(?=\ndef )", router, re.S)
check("F2 the ONE sweep status writer appends job_run 'sweep:<table>' on every finished run",
      bool(m) and "'job_name': f'sweep:{table}'" in m.group(0) and "if mark_run:" in m.group(0))
m2 = re.search(r"async def connector_health_run_due\(.*?\n(?=\n\n# |\n@router\.)", router, re.S)
check("F3 the hourly connector-health tick sends reminders through the alert pipeline",
      bool(m2) and "_setup_docs.run_reminders(client, _send_alert)" in m2.group(0))
sd_src = read("backend/app/modules/commcalc/setup_documents.py")
check("F4 scheduling a document takes ownership of the feed (import_health's auto-disable can never undo it)",
      '"auto_derived": False' in sd_src and '"enabled": True' in sd_src)
check("F5 caps come from the SAME loader GET /report-kinds uses", "from app.modules.commcalc.router import _intake_caps" in sd_src)
hosts = set(re.findall(r"https?://(?:www\.)?([a-z0-9.-]+)", mig_code))
stems = {h.split(".")[-2] for h in hosts if h.count(".") >= 1} | {"b2b soft", "epay", "vidapay", "elevate go", "yoobic", "vip wireless"}
code_files = {"setup_documents.py": sd_src, "setup_router.py": rtr}
bad = [(n, s) for n, src in code_files.items() for s in stems
       if re.search(re.escape(s), "\n".join(ln for ln in src.split("\n") if not ln.strip().startswith("#")), re.I)]
check("F6 no portal / brand word in the backend code (it is registry DATA): %d stems scanned" % len(stems), not bad, str(bad))
wiz = read("frontend/src/app/(platform)/commcalc/upload/wizard/page.tsx")
check("F7 the wizard reads GET /commcalc/setup-documents and renders the registry's download steps",
      "/api/v1/commcalc/setup-documents" in wiz and "d.download_steps" in wiz and "d.automation.proven && d.automation.setup_link" in wiz)
bad_fe = [s for s in stems if re.search(re.escape(s), wiz, re.I)]
check("F8 the wizard names no portal", not bad_fe, str(bad_fe))
lay = read("frontend/src/app/(platform)/layout.tsx")
gate_ts = read("frontend/src/lib/setup-gate.ts")
check("F9 the layout redirects through the pure lib/setup-gate, asking the server",
      "setupRedirect(setupGate, pathname)" in lay and "/api/v1/commcalc/setup-documents/gate" in lay
      and "setupGateApplies(isSuperAdmin(permissions), isPlatformAdmin(user))" in lay)
check("F10 setup-gate: inactive → no redirect; allowed prefixes open; platform admin never gated",
      "if (!gate?.active) return null" in gate_ts and "pathname.startsWith(p + '/')" in gate_ts
      and "return isCompanyAdmin && !isPlatformAdmin" in gate_ts)
cash = read("frontend/src/app/(platform)/closing/cash-config/page.tsx")
check("F11 the reminder recipient scope is listed on the alerts page", "{ key: '%s'" % SD.ALERT_SCOPE in cash)
rbac = read("frontend/src/lib/rbac.ts")
check("F12 Carrier Documents sits in the Super Admin Toolbox", "href: '/admin/carrier-documents'" in rbac)
check("F13 the setup columns are carried by the registry's normaliser (one list)",
      "for k in SETUP_COLS:" in read("backend/app/modules/commcalc/report_kinds.py"))

# ── G. registered ────────────────────────────────────────────────────────────────────────────────────────────────
print("G. registered")
idx = read("docs/SYSTEM_DATA_FLOW_INDEX.md")
check("G1 index §39", "## 39. SETUP DOCUMENTS" in idx and "harness_setup_documents.py" in idx)

print()
print("%d FAIL(s)" % len(FAILS) if FAILS else "ALL PASS")
sys.exit(1 if FAILS else 0)
