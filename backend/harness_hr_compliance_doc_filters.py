"""Integration-style proof for the Compliance Document Repository's two new date-range filters +
employee multi-select (OWNER DIRECTIVE 2026-07-27) + the Gate-1 fold (N1-N6, same date). Runs the
ACTUAL shipped `onboarding_compliance_documents` / `_compliance_not_submitted_rows` / `_date_range_ok`
from app.modules.hr.router against an in-memory fake Supabase client (for the endpoint) and, for the
not-submitted mechanism specifically, direct pure calls (no DB needed — it's a pure function). No live
DB/network either way. Run: `python3 harness_hr_compliance_doc_filters.py` from backend/.

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
  6. "Not yet submitted" honesty, COUNT-level through the real endpoint (the raw per-row list was
     dropped from the HTTP response per Gate-1 N6 — it was computed but never rendered; counts are
     everything the page actually uses), isolated per employee via employee_ids so each count is an
     exact, fixture-computed expectation, not just "some number changed."
  7. Org isolation: a second org's employees/documents/not_submitted numbers are never visible from
     org ORGX's calls, in every filter combination above.
  8. Graceful degrade: a pre-077 tenant (no employee_onboarding_profile rows at all) never 500s, and a
     sent-range filter against it correctly reports everything as sent_unknown, never fabricated.
  9. Gate-1 N2/N3, PURE unit-level, direct calls into `_compliance_not_submitted_rows` (module-level,
     DB-free, hand-built inputs — the precise mechanism proof the dropped raw list used to give at the
     integration level): a DEACTIVATED task never counts as outstanding regardless of resolution state
     (N2); a status='na' (HR-waived) task with ZERO artifact is resolved, not outstanding forever
     (N3a); a status='verified' task with ZERO artifact (HR verified an original in person) is ALSO
     resolved (N3b — mirrors onboarding_for_employee's own `ok_done` and onboarding_doc_status's
     bucketing); a task incapable of ever producing a document (neither requires_upload nor
     is_fillable) never appears regardless; a genuinely untouched, active, eligible task DOES appear
     (negative control — the fix isn't accidentally suppressing everything).
 10. `_date_range_ok` (hoisted, module-level, pure) — inclusive boundaries, blank-both-sides = no
     filter, missing timestamp = None ("unknown"), never conflated with a confirmed out-of-range False.
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
check("t1z: Gate-1 N6 — the raw not_submitted LIST is no longer on the wire (counts only; it was never rendered)",
      "not_submitted" not in base, list(base.keys()))
emp1_row = next(d for d in base["documents"] if d["employee_id"] == "EMP1")
check("t1g: EMP1's request_sent_at = docs_sent_at (preferred over invited_at)",
      emp1_row["request_sent_at"] == "2026-07-01T10:00:00+00:00", emp1_row)
emp2_row = next(d for d in base["documents"] if d["employee_id"] == "EMP2")
check("t1h: EMP2's request_sent_at FALLS BACK to invited_at (docs_sent_at unset)",
      emp2_row["request_sent_at"] == "2026-06-15T09:00:00+00:00", emp2_row)

# ── 2. not_submitted honesty (no filters), COUNT-level — the raw per-employee/per-task list was
# dropped from the response (Gate-1 N6); isolate one employee at a time via employee_ids so each exact
# integer count is a fixture-computed, unambiguous proof (the PRECISE mechanism — which task, why
# excluded — is proven separately in section 9 against the pure helper directly). ───────────────────
check("t2i: not_submitted_employee_count = 3 (EMP1, EMP2, EMP3 — EMP4 excluded as inactive)",
      base["not_submitted_employee_count"] == 3, base["not_submitted_employee_count"])
c1 = C(org_id=ORG, employee_ids="EMP1")
check("t2a: EMP1 owes exactly 2 (Handbook + ID Docs; Federal W-4 already submitted, IL W-4 inapplicable "
      "— no work_state, I-9 Verify can never appear — not requires_upload/is_fillable)",
      c1["not_submitted_count"] == 2, c1)
c2 = C(org_id=ORG, employee_ids="EMP2")
check("t2f: EMP2 owes exactly 1 (Handbook only — both W-4s submitted, the undated id_docs row still "
      "counts as submitted via has_artifact, not owed)", c2["not_submitted_count"] == 1, c2)
c3 = C(org_id=ORG, employee_ids="EMP3")
check("t2g: EMP3 (zero submissions, work_state=None) owes exactly 3 (Federal W-4, Handbook, ID Docs — not IL W-4)",
      c3["not_submitted_count"] == 3, c3)
c4 = C(org_id=ORG, employee_ids="EMP4")
check("t2h: EMP4 (INACTIVE) is excluded from not_submitted entirely (count=0) even though she'd otherwise owe items",
      c4["not_submitted_count"] == 0, c4)

# ── 3. employee_ids multi-select ─────────────────────────────────────────────────────────────────
r = C(org_id=ORG, employee_ids="EMP1,EMP3")
check("t3a: employee_ids narrows `documents` to EMP1 only (EMP3 has none)",
      {d["employee_id"] for d in r["documents"]} == {"EMP1"}, r["documents"])
check("t3b: employee_ids narrows not_submitted_count to EMP1(2)+EMP3(3)=5", r["not_submitted_count"] == 5, r)
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
check("t5c: not_submitted_employee_count under the SAME sent-range filter narrows to 1 (EMP2 only; EMP1/EMP3 excluded)",
      r["not_submitted_employee_count"] == 1, r)

# ── 6. composable AND: employee_ids + submitted range + sent range together ────────────────────────
r = C(org_id=ORG, employee_ids="EMP1,EMP2,EMP4", sent_from="2026-07-01", sent_to="2026-07-31",
      submitted_from="2026-07-01", submitted_to="2026-07-31")
check("t6a: AND of employee_ids + sent(Jul) + submitted(Jul) -> EMP1 and EMP4 only "
      "(EMP2 sent in June is excluded by the sent filter even though not in the id-narrowed submitted set)",
      {d["employee_id"] for d in r["documents"]} == {"EMP1", "EMP4"}, r["documents"])

# ── 7. org isolation across every filter combination exercised above ───────────────────────────────
for kwargs in [{}, {"employee_ids": "EMP1"}, {"submitted_from": "2026-01-01"}, {"sent_from": "2026-01-01"}]:
    rr = C(org_id=ORG, **kwargs)
    check(f"t7: org isolation (documents) holds under filters={kwargs}",
          all(d["employee_id"] != "EMPY1" for d in rr["documents"]), rr)
other_view = C(org_id=OTHER)
check("t7b: querying OTHER org sees ONLY its own employee's documents (EMPY1), never ORGX's",
      {d["employee_id"] for d in other_view["documents"]} == {"EMPY1"}, other_view["documents"])
check("t7c: OTHER org's not_submitted is scoped to ITS OWN roster only (EMPY1 owes Handbook+ID Docs = 2 of "
      "2 tasks, 1 employee — nowhere close to ORGX's totals, proving no cross-org bleed into the counts)",
      other_view["not_submitted_count"] == 2 and other_view["not_submitted_employee_count"] == 1, other_view)

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

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 9. Gate-1 N2/N3 — PURE unit-level, direct calls into the shipped `_compliance_not_submitted_rows`
# (module-level, DB-free) with hand-built inputs. This is the precise mechanism proof the dropped raw
# list used to give at the integration level, now done the way this file already proves its other pure
# helpers (_aba_checksum_valid, _normalize_state, _blocking_gate) — no fake DB needed at all.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
pure_tasks = [
    {"id": "tA", "label": "Untouched", "requires_upload": True, "is_fillable": False, "applies_state": None, "is_active": True},
    {"id": "tB", "label": "Deactivated", "requires_upload": True, "is_fillable": False, "applies_state": None, "is_active": False},
    {"id": "tC", "label": "Waived", "requires_upload": True, "is_fillable": False, "applies_state": None, "is_active": True},
    {"id": "tD", "label": "VerifiedNoFile", "requires_upload": True, "is_fillable": False, "applies_state": None, "is_active": True},
    {"id": "tE", "label": "NoUploadCapability", "requires_upload": False, "is_fillable": False, "applies_state": None, "is_active": True},
]
pure_emps = {"P1": {"employee_id": "P1", "name": "Pat Rep", "email": "pat@p.com", "is_active": True}}
pure_sent_of = {"P1": {"work_state": None, "request_sent_at": "2026-07-01"}}
pure_has_artifact = set()   # nothing has ever actually been uploaded/signed for ANY task here
# N3: 'na' (HR-waived) and 'verified' (HR verified in person) both resolve a task WITHOUT an artifact —
# mirrors onboarding_for_employee's `ok_done = status in ("verified", "na")` and onboarding_doc_status's
# bucketing (status='na' buckets as 'verified').
pure_resolved_by_status = {("P1", "tC"), ("P1", "tD")}
pure_cat_of = {t["id"]: "Misc" for t in pure_tasks}

rows_out, sent_unknown = hr_router._compliance_not_submitted_rows(
    pure_tasks, pure_emps, pure_sent_of, pure_has_artifact, pure_resolved_by_status, pure_cat_of,
    set(), "", "", "")
labels = {r["document_label"] for r in rows_out}
check("t9a: negative control — 'Untouched' (active, eligible, zero artifact, zero status-resolution) DOES appear "
      "(the fix isn't accidentally suppressing everything)", labels == {"Untouched"}, labels)
check("t9b (N2): 'Deactivated' (is_active=False) is excluded regardless of resolution state", "Deactivated" not in labels)
check("t9c (N3a): 'Waived' (resolved_by_status, status='na') is excluded even with ZERO artifact — not "
      "outstanding forever just because status='na' never uploads anything", "Waived" not in labels)
check("t9d (N3b): 'VerifiedNoFile' (resolved_by_status, status='verified') is ALSO excluded with ZERO "
      "artifact — mirrors onboarding_for_employee's own ok_done + onboarding_doc_status's bucketing "
      "(an in-person-verified original document is done, not outstanding)", "VerifiedNoFile" not in labels)
check("t9e: 'NoUploadCapability' (neither requires_upload nor is_fillable) never appears regardless of anything",
      "NoUploadCapability" not in labels)
check("t9f: sent_unknown stays 0 when the employee has a recorded sent date", sent_unknown == 0)

# is_active exclusion holds even when a deactivated task IS also resolved by status (belt-and-suspenders
# — is_active is checked before either resolution set, so this can never accidentally re-include it)
rows_out2, _ = hr_router._compliance_not_submitted_rows(
    pure_tasks, pure_emps, pure_sent_of, set(), {("P1", "tB")}, pure_cat_of, set(), "", "", "")
check("t9g (N2, belt-and-suspenders): a deactivated task stays excluded even if it's ALSO resolved_by_status",
      "Deactivated" not in {r["document_label"] for r in rows_out2})

# a genuinely unknown sent date increments sent_unknown and excludes that employee entirely (no owed
# items surfaced for someone we can't even confirm is in the requested sent-range)
pure_sent_of_unknown = {"P1": {"work_state": None, "request_sent_at": None}}
rows_out3, sent_unknown3 = hr_router._compliance_not_submitted_rows(
    pure_tasks, pure_emps, pure_sent_of_unknown, set(), set(), pure_cat_of, set(), "", "2026-01-01", "2026-12-31")
check("t9h: an active sent-range filter against an employee with no sent date at all -> 0 rows + sent_unknown=1",
      rows_out3 == [] and sent_unknown3 == 1, (rows_out3, sent_unknown3))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 9i. Same N2/N3 semantics proven end-to-end through the REAL endpoint too (not just the pure helper in
# isolation) — a small dedicated org so none of sections 1-8's numeric expectations are touched.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
ORGN = "ORGN"
STORE.setdefault("onboarding_category", []).append(
    {"id": "cat-n", "org_id": ORGN, "key": "misc", "label": "Misc", "sort_order": 10, "is_active": True})
STORE.setdefault("onboarding_task", []).extend([
    {"id": "t-untouched-n", "org_id": ORGN, "category_id": "cat-n", "key": "untouched", "label": "Untouched Doc",
     "requires_upload": True, "is_fillable": False, "applies_state": None, "owner_role": "employee",
     "is_active": True, "sort_order": 10},
    {"id": "t-deactivated-n", "org_id": ORGN, "category_id": "cat-n", "key": "deactivated", "label": "Deactivated Doc",
     "requires_upload": True, "is_fillable": False, "applies_state": None, "owner_role": "employee",
     "is_active": False, "sort_order": 20},
    {"id": "t-waived-n", "org_id": ORGN, "category_id": "cat-n", "key": "waived", "label": "Waived Doc",
     "requires_upload": True, "is_fillable": False, "applies_state": None, "owner_role": "employee",
     "is_active": True, "sort_order": 30},
    {"id": "t-inperson-n", "org_id": ORGN, "category_id": "cat-n", "key": "inperson", "label": "In-Person Verified Doc",
     "requires_upload": True, "is_fillable": False, "applies_state": None, "owner_role": "employee",
     "is_active": True, "sort_order": 40},
])
STORE["employees"].append({"employee_id": "EMPN1", "org_id": ORGN, "name": "Nia Rep", "email": "nia@n.com", "is_active": True})
STORE["employee_onboarding_profile"].append(
    {"employee_id": "EMPN1", "org_id": ORGN, "work_state": None, "docs_sent_at": "2026-07-01T10:00:00+00:00", "invited_at": None})
STORE["employee_onboarding"].extend([
    # waived (status='na', zero artifact) -> must NOT be reported
    {"employee_id": "EMPN1", "org_id": ORGN, "task_id": "t-waived-n", "status": "na",
     "document_path": None, "document_name": None, "documents": None, "signature_path": None,
     "signed_at": None, "signed_name": None, "verified_by": "HR", "verified_at": "2026-07-02T09:00:00+00:00",
     "submitted_at": None},
    # in-person verified (status='verified', zero artifact) -> must NOT be reported
    {"employee_id": "EMPN1", "org_id": ORGN, "task_id": "t-inperson-n", "status": "verified",
     "document_path": None, "document_name": None, "documents": None, "signature_path": None,
     "signed_at": None, "signed_name": None, "verified_by": "HR", "verified_at": "2026-07-02T09:00:00+00:00",
     "submitted_at": None},
    # t-untouched-n: deliberately NO row at all -> negative control, MUST still be reported
    # t-deactivated-n: deliberately NO row at all either -> is_active=False alone must suppress it
])
rn = C(org_id=ORGN)
check("t9i: end-to-end through the REAL endpoint — only 'Untouched Doc' is outstanding (1), "
      "deactivated/waived/verified-no-file are all correctly excluded",
      rn["not_submitted_count"] == 1 and rn["not_submitted_employee_count"] == 1, rn)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# 10. `_date_range_ok` (hoisted, module-level, pure) — inclusive boundaries + the None/False distinction.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
DRO = hr_router._date_range_ok
check("t10a: both bounds blank -> True (no filter) even with no timestamp", DRO(None, "", "") is True)
check("t10b: a timestamp with no bounds set -> True", DRO("2026-07-10T12:00:00+00:00", "", "") is True)
check("t10c: missing timestamp WITH a bound set -> None (unknown, not False)", DRO(None, "2026-07-01", "2026-07-31") is None)
check("t10d: exact lower-bound date -> True (inclusive)", DRO("2026-07-01T00:00:00+00:00", "2026-07-01", "2026-07-31") is True)
check("t10e: exact upper-bound date, even late in the day -> True (inclusive, UTC calendar date only)",
      DRO("2026-07-31T23:59:59+00:00", "2026-07-01", "2026-07-31") is True)
check("t10f: one day before the lower bound -> False", DRO("2026-06-30T23:59:59+00:00", "2026-07-01", "2026-07-31") is False)
check("t10g: one day after the upper bound -> False", DRO("2026-08-01T00:00:00+00:00", "2026-07-01", "2026-07-31") is False)
check("t10h: only a lower bound set -> open-ended upward", DRO("2099-01-01", "2026-07-01", "") is True)
check("t10i: only an upper bound set -> open-ended downward", DRO("2000-01-01", "", "2026-07-31") is True)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("FAIL:", f)
if FAIL:
    sys.exit(1)
print("ALL PASS")
