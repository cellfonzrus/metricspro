"""Integration-style proof for the Compliance Document Repository's two new date-range filters +
employee multi-select (OWNER DIRECTIVE 2026-07-27). Runs the ACTUAL shipped
`onboarding_compliance_documents` from app.modules.hr.router against an in-memory fake Supabase
client — no live DB/network. Run: `python3 harness_hr_compliance_doc_filters.py` from backend/.

Proves:
  1. Baseline (no filters) is unchanged — every submitted document across the whole org shows.
  2. employee_ids (multi, comma-separated business ids) narrows correctly; the legacy singular
     employee_id param still works (per-employee ZIP export link is unaffected).
  3. submitted_from/submitted_to narrows on the per-file/per-task submission date, inclusive both
     ends, and does NOT touch rows genuinely outside the range vs rows with NO recorded date
     (submitted_unknown_count) — never fabricated.
  4. sent_from/sent_to narrows on the employee-level request-sent date (docs_sent_at, falling back to
     invited_at), same inclusive/unknown-degrade contract (sent_unknown_count).
  5. The two ranges are composable (AND) together and with employee_ids.
  6. "Not yet submitted" honesty: an active employee with an applicable, artifact-less
     requires_upload/is_fillable task is reported in not_submitted / not_submitted_count /
     not_submitted_employee_count, scoped by the SAME employee/sent-range filters — and a
     submitted-range filter never hides that count (there's nothing to date, so it's not silently
     dropped). Work-state gating (applies_state) is respected. Inactive employees are excluded from
     not_submitted (matches the Documents board's own active-roster convention) but their EXISTING
     submitted documents still show normally.
  7. Org isolation: a second org's employees/documents/template are never visible from org ORGX's
     calls, in every filter combination above.
"""
import sys

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


# ── Fake Supabase client (eq filters + select; enough for this endpoint's read-only queries) ────────
class FakeQuery:
    def __init__(self, store, table):
        self.store = store
        self.table_name = table
        self.filters = []

    def select(self, cols):
        return self

    def eq(self, k, v):
        self.filters.append((k, v)); return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def _match(self, row):
        return all(row.get(k) == v for k, v in self.filters)

    def execute(self):
        rows = self.store.setdefault(self.table_name, [])
        return FakeResult([r for r in rows if self._match(r)])


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeSchemaClient:
    def __init__(self, store):
        self.store = store

    def schema(self, name):
        return self   # single flat store — schema name ignored, matches FakeQuery's flat table keying

    def table(self, name):
        return FakeQuery(self.store, name)


STORE = {}
FAKE_CLIENT = FakeSchemaClient(STORE)


def fake_get_supabase():
    return FAKE_CLIENT


import app.modules.hr.router as hr_router  # noqa: E402
hr_router.get_supabase = fake_get_supabase

ORG = "ORGX"
OTHER = "ORGY"

# ── template: 2 orgs, each with its own categories/tasks (org isolation proof needs a second org) ──
def seed_template(org, suffix):
    STORE.setdefault("onboarding_category", []).extend([
        {"id": f"cat-tax-{suffix}", "org_id": org, "key": "tax", "label": "Tax Forms", "sort_order": 10, "is_active": True},
        {"id": f"cat-agree-{suffix}", "org_id": org, "key": "agreements", "label": "Company Policies", "sort_order": 20, "is_active": True},
        {"id": f"cat-elig-{suffix}", "org_id": org, "key": "eligibility", "label": "Eligibility", "sort_order": 30, "is_active": True},
    ])
    STORE.setdefault("onboarding_task", []).extend([
        # requires_upload/is_fillable -> could ever produce a document -> counts toward not_submitted
        {"id": f"t-w4fed-{suffix}", "org_id": org, "category_id": f"cat-tax-{suffix}", "key": "w4_federal",
         "label": "Federal W-4", "requires_upload": True, "is_fillable": True, "applies_state": None,
         "owner_role": "employee", "is_active": True, "sort_order": 10},
        {"id": f"t-w4il-{suffix}", "org_id": org, "category_id": f"cat-tax-{suffix}", "key": "w4_il",
         "label": "IL W-4", "requires_upload": True, "is_fillable": True, "applies_state": "IL",
         "owner_role": "employee", "is_active": True, "sort_order": 20},
        {"id": f"t-handbook-{suffix}", "org_id": org, "category_id": f"cat-agree-{suffix}", "key": "handbook",
         "label": "Handbook Ack", "requires_upload": True, "is_fillable": False, "applies_state": None,
         "owner_role": "hr", "is_active": True, "sort_order": 10},
        {"id": f"t-iddocs-{suffix}", "org_id": org, "category_id": f"cat-elig-{suffix}", "key": "id_docs",
         "label": "ID Docs", "requires_upload": True, "is_fillable": False, "applies_state": None,
         "owner_role": "employee", "is_active": True, "sort_order": 20},
        # NOT requires_upload/is_fillable -> can never produce a document -> never counted as "missing"
        {"id": f"t-i9verify-{suffix}", "org_id": org, "category_id": f"cat-elig-{suffix}", "key": "i9_verify",
         "label": "I-9 Verify", "requires_upload": False, "is_fillable": False, "applies_state": None,
         "owner_role": "hr", "is_active": True, "sort_order": 10},
    ])


seed_template(ORG, "x")
seed_template(OTHER, "y")

# ── employees ──────────────────────────────────────────────────────────────────────────────────────
STORE["employees"] = [
    {"employee_id": "EMP1", "org_id": ORG, "name": "Alice Rep", "email": "alice@x.com", "is_active": True},
    {"employee_id": "EMP2", "org_id": ORG, "name": "Bob Manager", "email": "bob@x.com", "is_active": True},
    {"employee_id": "EMP3", "org_id": ORG, "name": "Carol Rep", "email": "carol@x.com", "is_active": True},
    {"employee_id": "EMP4", "org_id": ORG, "name": "Dana Rep", "email": "dana@x.com", "is_active": False},
    {"employee_id": "EMPY1", "org_id": OTHER, "name": "Yara Other", "email": "yara@y.com", "is_active": True},
]

# ── profiles: request-sent provenance (docs_sent_at > invited_at > nothing) ─────────────────────────
STORE["employee_onboarding_profile"] = [
    {"employee_id": "EMP1", "org_id": ORG, "work_state": None,
     "docs_sent_at": "2026-07-01T10:00:00+00:00", "invited_at": "2026-06-01T09:00:00+00:00"},
    {"employee_id": "EMP2", "org_id": ORG, "work_state": "IL",
     "docs_sent_at": None, "invited_at": "2026-06-15T09:00:00+00:00"},   # docs_sent_at unset -> falls back
    # EMP3: no profile row at all -> request_sent_at unknown -> "(no date recorded)"
    {"employee_id": "EMP4", "org_id": ORG, "work_state": None,
     "docs_sent_at": "2026-07-05T10:00:00+00:00", "invited_at": "2026-07-01T09:00:00+00:00"},
    {"employee_id": "EMPY1", "org_id": OTHER, "work_state": None,
     "docs_sent_at": "2026-07-01T10:00:00+00:00", "invited_at": None},
]

# ── employee_onboarding: submitted documents ─────────────────────────────────────────────────────
STORE["employee_onboarding"] = [
    # EMP1: submitted federal W-4 only (IL W-4 doesn't apply — no work_state)
    {"employee_id": "EMP1", "org_id": ORG, "task_id": "t-w4fed-x", "status": "verified",
     "document_path": None, "document_name": None,
     "documents": [{"id": "f1", "name": "w4.pdf", "path": "p1", "uploaded_at": "2026-07-10T12:00:00+00:00"}],
     "signature_path": None, "signed_at": None, "signed_name": None,
     "verified_by": "HR", "verified_at": "2026-07-11T09:00:00+00:00", "submitted_at": "2026-07-10T12:00:00+00:00"},
    # EMP2: submitted BOTH federal + IL W-4 (work_state=IL) ...
    {"employee_id": "EMP2", "org_id": ORG, "task_id": "t-w4fed-x", "status": "verified",
     "document_path": None, "document_name": None,
     "documents": [{"id": "f2", "name": "w4.pdf", "path": "p2", "uploaded_at": "2026-06-20T12:00:00+00:00"}],
     "signature_path": None, "signed_at": None, "signed_name": None,
     "verified_by": "HR", "verified_at": None, "submitted_at": "2026-06-20T12:00:00+00:00"},
    {"employee_id": "EMP2", "org_id": ORG, "task_id": "t-w4il-x", "status": "verified",
     "document_path": None, "document_name": None,
     "documents": [{"id": "f3", "name": "il-w4.pdf", "path": "p3", "uploaded_at": "2026-06-21T12:00:00+00:00"}],
     "signature_path": None, "signed_at": None, "signed_name": None,
     "verified_by": "HR", "verified_at": None, "submitted_at": "2026-06-21T12:00:00+00:00"},
    # ... plus a LEGACY id_docs row with NO date anywhere (document_path only, no documents[], no
    # submitted_at/verified_at) — proves submitted_unknown_count without fabricating a date.
    {"employee_id": "EMP2", "org_id": ORG, "task_id": "t-iddocs-x", "status": "submitted",
     "document_path": "legacy/id.pdf", "document_name": "id.pdf", "documents": None,
     "signature_path": None, "signed_at": None, "signed_name": None,
     "verified_by": None, "verified_at": None, "submitted_at": None},
    # EMP4 (inactive): one already-submitted doc — must still show in `documents` even though she's
    # excluded from not_submitted (departed hires aren't an actionable "outstanding request").
    {"employee_id": "EMP4", "org_id": ORG, "task_id": "t-w4fed-x", "status": "verified",
     "document_path": None, "document_name": None,
     "documents": [{"id": "f4", "name": "w4.pdf", "path": "p4", "uploaded_at": "2026-07-06T12:00:00+00:00"}],
     "signature_path": None, "signed_at": None, "signed_name": None,
     "verified_by": "HR", "verified_at": None, "submitted_at": "2026-07-06T12:00:00+00:00"},
    # EMPY1 (OTHER org) — must never appear from an ORG query.
    {"employee_id": "EMPY1", "org_id": OTHER, "task_id": "t-w4fed-y", "status": "verified",
     "document_path": None, "document_name": None,
     "documents": [{"id": "fy", "name": "w4.pdf", "path": "py", "uploaded_at": "2026-07-10T12:00:00+00:00"}],
     "signature_path": None, "signed_at": None, "signed_name": None,
     "verified_by": "HR", "verified_at": None, "submitted_at": "2026-07-10T12:00:00+00:00"},
]

C = hr_router.onboarding_compliance_documents

# ── 1. baseline (no filters) — unchanged behavior ────────────────────────────────────────────────
base = C(org_id=ORG)
check("t1a: ready", base["ready"] is True, base)
check("t1b: baseline document count = 5 (EMP1 x1, EMP2 x3, EMP4 x1)", base["count"] == 5, base["documents"])
check("t1c: OTHER org's document never appears", all(d["employee_id"] != "EMPY1" for d in base["documents"]))
emp_ids_seen = {d["employee_id"] for d in base["documents"]}
check("t1d: EMP3 (zero submissions) has no rows in `documents`", "EMP3" not in emp_ids_seen)
check("t1e: EMP4 (inactive) STILL shows her existing submitted document", "EMP4" in emp_ids_seen)
check("t1f: every row carries request_sent_at (employee-level, per docstring)",
      all("request_sent_at" in d for d in base["documents"]), base["documents"])
emp1_row = next(d for d in base["documents"] if d["employee_id"] == "EMP1")
check("t1g: EMP1's request_sent_at = docs_sent_at (preferred over invited_at)",
      emp1_row["request_sent_at"] == "2026-07-01T10:00:00+00:00", emp1_row)
emp2_row = next(d for d in base["documents"] if d["employee_id"] == "EMP2")
check("t1h: EMP2's request_sent_at FALLS BACK to invited_at (docs_sent_at unset)",
      emp2_row["request_sent_at"] == "2026-06-15T09:00:00+00:00", emp2_row)

# ── 2. not_submitted honesty (no filters) ────────────────────────────────────────────────────────
ns = {(d["employee_id"], d["document_label"]) for d in base["not_submitted"]}
check("t2a: EMP1 still owes Handbook (requires_upload, hr-owned, no artifact)", ("EMP1", "Handbook Ack") in ns)
check("t2b: EMP1 still owes ID Docs", ("EMP1", "ID Docs") in ns)
check("t2c: EMP1's IL W-4 is NOT owed (applies_state=IL, EMP1 has no work_state)", ("EMP1", "IL W-4") not in ns)
check("t2d: EMP1's Federal W-4 is NOT owed (already submitted)", ("EMP1", "Federal W-4") not in ns)
check("t2e: I-9 Verify NEVER appears (not requires_upload/is_fillable — can't ever produce a doc)",
      all(lbl != "I-9 Verify" for (_, lbl) in ns))
check("t2f: EMP2 owes Handbook only (both W-4s submitted, id_docs submitted-but-undated still counts as submitted)",
      ("EMP2", "Handbook Ack") in ns and ("EMP2", "Federal W-4") not in ns and ("EMP2", "IL W-4") not in ns
      and ("EMP2", "ID Docs") not in ns)
check("t2g: EMP3 (zero submissions, work_state=None) owes Federal W-4, Handbook, ID Docs (not IL W-4)",
      {"Federal W-4", "Handbook Ack", "ID Docs"} == {lbl for (eid, lbl) in ns if eid == "EMP3"})
check("t2h: EMP4 (INACTIVE) is excluded from not_submitted entirely, even though she also owes ID Docs/Handbook",
      all(eid != "EMP4" for (eid, _) in ns))
check("t2i: not_submitted_count matches len(not_submitted)", base["not_submitted_count"] == len(base["not_submitted"]))
check("t2j: not_submitted_employee_count = 3 (EMP1, EMP2, EMP3)", base["not_submitted_employee_count"] == 3, base["not_submitted_employee_count"])
check("t2k: OTHER org never leaks into not_submitted", all(eid != "EMPY1" for (eid, _) in ns))

# ── 3. employee_ids multi-select ─────────────────────────────────────────────────────────────────
r = C(org_id=ORG, employee_ids="EMP1,EMP3")
check("t3a: employee_ids narrows `documents` to EMP1 only (EMP3 has none)",
      {d["employee_id"] for d in r["documents"]} == {"EMP1"}, r["documents"])
check("t3b: employee_ids narrows not_submitted to EMP1+EMP3 only",
      {eid for (eid, _) in {(d["employee_id"], d["document_label"]) for d in r["not_submitted"]}} == {"EMP1", "EMP3"})
check("t3c: not_submitted_employee_count reflects the narrowed set (2)", r["not_submitted_employee_count"] == 2, r)

# legacy singular employee_id still works (the per-employee ZIP export link uses it)
r_single = C(org_id=ORG, employee_id="EMP2")
check("t3d: legacy singular employee_id still exact-matches", {d["employee_id"] for d in r_single["documents"]} == {"EMP2"})

# combined: employee_id + employee_ids both populated -> union (either is honored)
r_combo = C(org_id=ORG, employee_id="EMP4", employee_ids="EMP1")
check("t3e: employee_id + employee_ids together = union of both", {d["employee_id"] for d in r_combo["documents"]} == {"EMP1", "EMP4"}, r_combo["documents"])

# ── 4. submitted_from/submitted_to — inclusive both ends, on the real per-file date ────────────────
r = C(org_id=ORG, submitted_from="2026-07-01", submitted_to="2026-07-31")
ids = {d["employee_id"] for d in r["documents"]}
check("t4a: only EMP1 (Jul 10) and EMP4 (Jul 6) fall in July; EMP2 (June) excluded",
      ids == {"EMP1", "EMP4"}, r["documents"])
check("t4b: EMP2's undated id_docs row is excluded from `documents` under this filter AND counted, not silently dropped",
      r["submitted_unknown_count"] >= 1, r)
# inclusive boundary: exactly the submitted date itself
r_exact = C(org_id=ORG, submitted_from="2026-07-10", submitted_to="2026-07-10")
check("t4c: inclusive lower+upper bound on the exact submission date", any(d["employee_id"] == "EMP1" for d in r_exact["documents"]), r_exact)
# a range with nothing in it
r_none = C(org_id=ORG, submitted_from="2026-01-01", submitted_to="2026-01-31")
check("t4d: a submitted-range with no matches -> 0 documents, not an error", r_none["count"] == 0 and r_none["ready"], r_none)
check("t4e: not_submitted_count is UNCHANGED by a submitted-range filter (never hidden by it)",
      r_none["not_submitted_count"] == base["not_submitted_count"], (r_none["not_submitted_count"], base["not_submitted_count"]))

# ── 5. sent_from/sent_to — the employee-level request-sent date ────────────────────────────────────
r = C(org_id=ORG, sent_from="2026-06-01", sent_to="2026-06-30")
ids = {d["employee_id"] for d in r["documents"]}
check("t5a: only EMP2 (invited_at in June) is in range; EMP1/EMP4 (July) excluded", ids == {"EMP2"}, r["documents"])
check("t5b: EMP3 (no sent date at all) contributes to sent_unknown_count, not fabricated into range or out",
      r["sent_unknown_count"] >= 1, r)
ns_ids = {eid for (eid, _) in {(d["employee_id"], d["document_label"]) for d in r["not_submitted"]}}
check("t5c: not_submitted under the SAME sent-range filter also narrows to EMP2 only (EMP1/EMP3 excluded)",
      ns_ids == {"EMP2"}, ns_ids)

# ── 6. composable AND: employee_ids + submitted range + sent range together ────────────────────────
r = C(org_id=ORG, employee_ids="EMP1,EMP2,EMP4", sent_from="2026-07-01", sent_to="2026-07-31",
      submitted_from="2026-07-01", submitted_to="2026-07-31")
check("t6a: AND of employee_ids + sent(Jul) + submitted(Jul) -> EMP1 and EMP4 only "
      "(EMP2 sent in June is excluded by the sent filter even though not in the id-narrowed submitted set)",
      {d["employee_id"] for d in r["documents"]} == {"EMP1", "EMP4"}, r["documents"])

# ── 7. org isolation across every filter combination exercised above ───────────────────────────────
for kwargs in [{}, {"employee_ids": "EMP1"}, {"submitted_from": "2026-01-01"}, {"sent_from": "2026-01-01"}]:
    rr = C(org_id=ORG, **kwargs)
    check(f"t7: org isolation holds under filters={kwargs}",
          all(d["employee_id"] != "EMPY1" for d in rr["documents"])
          and all(eid != "EMPY1" for (eid, _) in {(d["employee_id"], d["document_label"]) for d in rr["not_submitted"]}),
          rr)
other_view = C(org_id=OTHER)
check("t7b: querying OTHER org sees ONLY its own employee (EMPY1), never ORGX's",
      {d["employee_id"] for d in other_view["documents"]} == {"EMPY1"}, other_view["documents"])

# ── 8. graceful degrade: pre-077 tenant (no employee_onboarding_profile table at all) ───────────────
NOPROF_ORG = "ORGZ"
seed_template(NOPROF_ORG, "z")
STORE["employees"].append({"employee_id": "EMPZ1", "org_id": NOPROF_ORG, "name": "Zed Rep", "email": "z@z.com", "is_active": True})
STORE["employee_onboarding"].append(
    {"employee_id": "EMPZ1", "org_id": NOPROF_ORG, "task_id": "t-w4fed-z", "status": "verified",
     "document_path": None, "document_name": None,
     "documents": [{"id": "fz1", "name": "w4.pdf", "path": "pz1", "uploaded_at": "2026-07-10T12:00:00+00:00"}],
     "signature_path": None, "signed_at": None, "signed_name": None,
     "verified_by": "HR", "verified_at": None, "submitted_at": "2026-07-10T12:00:00+00:00"})
# deliberately NO employee_onboarding_profile rows seeded for ORGZ at all
rz = C(org_id=NOPROF_ORG)
check("t8a: no profile rows at all -> still ready, documents still show (request_sent_at just None)",
      rz["ready"] is True and rz["count"] == 1 and rz["documents"][0]["request_sent_at"] is None, rz)
rz_sent = C(org_id=NOPROF_ORG, sent_from="2026-01-01", sent_to="2026-12-31")
# sent_unknown_count is one combined counter across BOTH listings (the doc row AND the not_submitted
# employee entry each contribute once for EMPZ1 — one shared, honest "excluded for lack of a date" tally
# the UI surfaces as a single banner, not split by listing).
check("t8b: a sent-range filter against an all-unknown org excludes everything via sent_unknown_count (doc row + not_submitted employee, never a 500/fabrication)",
      rz_sent["count"] == 0 and rz_sent["not_submitted_count"] == 0 and rz_sent["sent_unknown_count"] == 2, rz_sent)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("FAIL:", f)
if FAIL:
    sys.exit(1)
print("ALL PASS")
