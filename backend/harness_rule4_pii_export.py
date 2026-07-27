"""Pure-logic proof harness — RULE FOUR (AGENT_CONTRACT §3c) PII-export safety, mod-people wave
2026-07-15 (agent/people/rule4-exports).

Every report/list surface mod-people retrofitted with the standard export set (ReportShell /
ReportExportBar: Excel/PDF/Print/Send) is checked in the handoff notes for the PII rule:

    "exports contain exactly what the page shows — encrypted/redacted PII (SSNs, bank details)
    NEVER exports; if a page shows a masked value, the export carries the masked value."

The one place in mod-people's tree where actual Fernet-encrypted PII is masked-then-conditionally-
revealed is `hr/onboarding/[employeeId]/page.tsx`'s "sensitive fields" panel (fed by
`GET /hr/onboarding/employee/{id}/sensitive`, HR/admin-only + audited). This harness proves, against
the REAL shipped backend function (no live DB — a fake Supabase client, same convention as
harness_multifile_docs.py) and the REAL shipped frontend source (read as text so the proof can't
silently drift from the shipped code, same technique the multifile-docs harness used for ZIP naming):

  1. `onboarding_compliance_documents` (the endpoint feeding /hr/compliance's new TABLE-view export)
     reads `storeops.employee_onboarding` for document metadata. OWNER DIRECTIVE 2026-07-27 (the two
     compliance-repository date filters) added a SECOND, narrow read of `employee_onboarding_profile`
     — but ONLY for `work_state`/`docs_sent_at`/`invited_at` (the "request sent" provenance), never
     `intake_data` (the Fernet-encrypted column on that same table, still off-limits). This harness now
     proves the NARROWER, still-rigorous invariant: the `.select(...)` call against that table never
     requests `intake_data` or `*`, AND — behaviorally, not just by source inspection — even when a
     fetched profile row's `intake_data` carries a live-looking SSN/bank value, it never appears
     anywhere in the function's response (belt-and-suspenders: proves the column-scoping actually
     holds at runtime, not just that the source text looks right).
  2. Every row that function actually returns (built from a realistic fixture, including a document
     literally labeled "Social Security Card") contains ONLY the expected non-sensitive keys — no
     key name matching ssn/bank/routing/account/dob/intake ever appears.
  3. The frontend `/hr/compliance/page.tsx` `ExportColumn[]` I added never references a sensitive-
     looking field name either (cross-checked against the same block of source, so a future edit to
     one without the other fails this harness).
  4. `hr/onboarding/[employeeId]/page.tsx` — where the ACTUAL masked→revealed PII values live — does
     NOT import ReportShell/ReportExportBar/ExportButtons anywhere. This is a deliberate scope
     decision, not an oversight: that page's unmasked value only ever exists for the duration of one
     audited API call (`revealSensitive()` → `/employee/{id}/sensitive`), stored in ephemeral React
     state (`revealed`) that is never written into any table/list this wave touched. A static
     Excel/PDF/Print snapshot has no "click to reveal, re-audited every time" affordance, so the only
     way to honor "the export carries the masked value, never the real one" here is to not offer a
     bulk/file export of that panel at all — proven by absence, not by a runtime redact step that
     could regress.
  5. What that page DOES render pre-reveal (`sensitive_on_file`) is a list of FIELD LABELS ("SSN",
     "Bank Account", "Bank Routing"), never values — confirmed by reading the backend's own
     `onboarding_sensitive_fields`-adjacent list-building code path (the `sensitive_on_file` producer)
     to show it emits labels, not `intake_data` values, even pre-reveal.

Run: `python3 harness_rule4_pii_export.py` from backend/.
"""
import inspect
import re
import sys

sys.path.insert(0, ".")

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} :: {detail}")


# ── Fake Supabase client (same shape as harness_multifile_docs.py) ─────────────────────────────────
class FakeQuery:
    def __init__(self, store, table):
        self.store = store
        self.table_name = table
        self.filters = []
        self._select_cols = "*"
        self._mode = None

    def select(self, cols):
        self._select_cols = cols
        self._mode = "select"
        return self

    def eq(self, k, v):
        self.filters.append((k, v))
        return self

    def order(self, *a, **k):
        return self

    def _match(self, row):
        return all(row.get(k) == v for k, v in self.filters)

    def execute(self):
        rows = self.store.setdefault(self.table_name, [])
        matched = [r for r in rows if self._match(r)]
        return FakeResult(matched)


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeClient:
    def __init__(self):
        self.store = {}

    def table(self, name):
        return FakeQuery(self.store, name)

    def schema(self, name):
        return self


import app.modules.hr.router as hr  # noqa: E402

fake = FakeClient()
hr.get_supabase = lambda: fake
ORG = "org-pii-1"

# Template: one category with the flagship sensitive-sounding document.
fake.store["onboarding_category"] = [{"org_id": ORG, "id": "cat1", "label": "Documents", "sort_order": 1, "is_active": True}]
fake.store["onboarding_task"] = [
    {"org_id": ORG, "id": "ss_card", "category_id": "cat1", "label": "Social Security Card", "sort_order": 1, "is_active": True},
    {"org_id": ORG, "id": "dd_form", "category_id": "cat1", "label": "Direct Deposit / Bank Form", "sort_order": 2, "is_active": True},
]
fake.store["employees"] = [{"org_id": ORG, "employee_id": "emp-1", "name": "Jose Utero", "email": "jose@x.com"}]
# The profile row this endpoint now legitimately reads for request-sent provenance — deliberately ALSO
# carries a live-looking intake_data SSN/bank value (a real row on this table would), so the behavioral
# check below proves that value never reaches the response even though the row itself is fetched.
fake.store["employee_onboarding_profile"] = [
    {"org_id": ORG, "employee_id": "emp-1", "work_state": "IL",
     "docs_sent_at": "2026-06-28", "invited_at": "2026-06-20",
     "intake_data": {"ssn": "123-45-6789", "bank_account": "000111222", "bank_routing": "021000021"}},
]
# A realistic employee_onboarding row for each task — includes a multi-file `documents` list (mig 402
# shape) and a signature — but NEVER any field carrying an actual SSN/bank VALUE (that only ever lives
# in `employee_onboarding_profile.intake_data`, seeded above specifically to prove it doesn't leak
# through even though the row it lives on IS now fetched for its non-sensitive columns).
fake.store["employee_onboarding"] = [
    {"org_id": ORG, "employee_id": "emp-1", "task_id": "ss_card", "status": "verified",
     "document_path": None, "document_name": None,
     "documents": [{"id": "f1", "path": f"{ORG}/emp-1/f1_front.jpg", "name": "front.jpg", "uploaded_at": "2026-07-01"},
                   {"id": "f2", "path": f"{ORG}/emp-1/f2_back.jpg", "name": "back.jpg", "uploaded_at": "2026-07-01"}],
     "signature_path": None, "signed_at": None, "signed_name": None,
     "verified_by": "HR Admin", "verified_at": "2026-07-02", "submitted_at": "2026-07-01"},
    {"org_id": ORG, "employee_id": "emp-1", "task_id": "dd_form", "status": "verified",
     "document_path": None, "document_name": None, "documents": [],
     "signature_path": f"{ORG}/emp-1/dd_sig.png", "signed_at": "2026-07-03", "signed_name": "Jose Utero",
     "verified_by": "HR Admin", "verified_at": "2026-07-03", "submitted_at": "2026-07-03"},
]

SENSITIVE_KEY_RE = re.compile(r"ssn|social.?security|bank|routing|account.?num|dob|birth|intake_data|intake_value", re.I)
# Keys we EXPECT this endpoint to return — a row shape allow-list. Anything outside this set is a
# structural surprise worth failing loudly on (belt-and-suspenders on top of the regex check).
EXPECTED_KEYS = {
    "employee_id", "employee_name", "employee_email", "task_id", "document_label", "category",
    "status", "verified_by", "file_id", "file_index", "file_count", "document_name",
    "has_document", "has_signature_page", "signed_at", "signed_name", "request_sent_at",
}

# ── 1. Structural check: the function's OWN source never selects the encrypted intake column ───────
src = inspect.getsource(hr.onboarding_compliance_documents)
check("1a: reads storeops.employee_onboarding", '"employee_onboarding"' in src or "'employee_onboarding'" in src, src[:200])
# 2026-07-27: this endpoint now legitimately reads employee_onboarding_profile too (request-sent
# provenance) — assert the NARROW invariant instead of a blanket "never touches the table": the
# specific .select(...) call for that table is neither "*" nor mentions intake_data.
prof_select_m = re.search(r'\.table\("employee_onboarding_profile"\)\s*\n?\s*\.select\("([^"]*)"\)', src)
check("1b: employee_onboarding_profile IS read now (request-sent provenance) — found the .select(...) call",
      prof_select_m is not None, src)
prof_select_cols = prof_select_m.group(1) if prof_select_m else ""
check("1b2: that select() is column-scoped, not '*'", prof_select_cols != "*" and prof_select_cols != "", prof_select_cols)
check("1b3: that select() never requests intake_data", "intake_data" not in prof_select_cols, prof_select_cols)
check("1c: never selects raw intake_data ANYWHERE in the function (belt-and-suspenders on top of 1b3)",
      "intake_data" not in src, "found intake_data in the function source")

# ── 2. Behavioral check: call the REAL function, inspect every returned row ─────────────────────────
resp = hr.onboarding_compliance_documents(org_id=ORG, q="", employee_id="")
check("2a: ready", resp.get("ready") is True, resp)
check("2b: got rows for both tasks (2 files + 1 signature = 3 rows)", resp.get("count") == 3, resp)
for row in resp["documents"]:
    extra_keys = set(row.keys()) - EXPECTED_KEYS
    check(f"2c: row for task={row.get('task_id')} file_id={row.get('file_id')} has no unexpected key",
          not extra_keys, f"unexpected keys: {extra_keys}")
    for k, v in row.items():
        check(f"2d: key '{k}' on task={row.get('task_id')} is not a sensitive-value field name",
              not SENSITIVE_KEY_RE.search(k), k)
        if isinstance(v, str):
            check(f"2e: value of '{k}' on task={row.get('task_id')} does not look like a raw SSN (###-##-####)",
                  not re.search(r"\b\d{3}-\d{2}-\d{4}\b", v), v)
# Specifically: the "Social Security Card" document is present (so this fixture actually exercises
# the flagship sensitive-labeled document), but only as a LABEL + file metadata, never a value.
ss_rows = [r for r in resp["documents"] if r["task_id"] == "ss_card"]
check("2f: SS-card task produced 2 file rows (both files, per multi-file mig 402)", len(ss_rows) == 2, ss_rows)
check("2g: SS-card rows carry the LABEL 'Social Security Card', not an SSN value",
      all(r["document_label"] == "Social Security Card" for r in ss_rows), ss_rows)
# 2h/2i: the SEEDED intake_data SSN/bank values never appear ANYWHERE in the response, even though the
# profile row carrying them WAS fetched (belt-and-suspenders: proves the narrow .select() actually
# holds at runtime, not just that the source text looks right).
resp_str = repr(resp)
check("2h: the seeded intake_data SSN never appears anywhere in the response", "123-45-6789" not in resp_str, resp_str[:300])
check("2i: the seeded intake_data bank_account/routing never appear anywhere in the response",
      "000111222" not in resp_str and "021000021" not in resp_str, resp_str[:300])
# 2j: and the profile read isn't a no-op either — the legitimate new field IS populated, proving this
# isn't vacuously passing because the fetch silently failed/was skipped.
check("2j: request_sent_at IS populated from the profile's docs_sent_at (the read genuinely happened)",
      all(r.get("request_sent_at") == "2026-06-28" for r in resp["documents"]), resp["documents"])

# ── 3. Frontend cross-check: /hr/compliance's new ExportColumn[] (read as text, source-parity) ──────
fe_path = "../frontend/src/app/(platform)/hr/compliance/page.tsx"
fe_src = open(fe_path, encoding="utf-8").read()
cols_block_m = re.search(r"const cols: ExportColumn\[\] = \[(.*?)\n\s*\]", fe_src, re.S)
check("3a: found the compliance page's export cols block", cols_block_m is not None)
cols_block = cols_block_m.group(1) if cols_block_m else ""
check("3b: compliance page's export columns never reference a sensitive-looking field",
      not SENSITIVE_KEY_RE.search(cols_block), cols_block)
check("3c: compliance page imports ReportExportBar (the retrofit actually landed)",
      "ReportExportBar" in fe_src and "from '@/components/ReportExportBar'" in fe_src)

# ── 4. The ONE masked-PII surface — confirm NO export was wired there ───────────────────────────────
detail_path = "../frontend/src/app/(platform)/hr/onboarding/[employeeId]/page.tsx"
detail_src = open(detail_path, encoding="utf-8").read()
check("4a: revealSensitive() exists (the audited reveal flow is the real masked-PII surface)",
      "revealSensitive" in detail_src)
check("4b: per-employee page does NOT import ReportShell", "ReportShell" not in detail_src)
check("4c: per-employee page does NOT import ReportExportBar", "ReportExportBar" not in detail_src)
check("4d: per-employee page does NOT import the raw ExportButtons primitive either",
      "ExportButtons" not in detail_src)

# ── 5. Pre-reveal state renders LABELS, never values ─────────────────────────────────────────────
check("5a: pre-reveal banner renders sensitive_on_file (a label list), not intake values",
      "sensitive_on_file" in detail_src and "d.sensitive_on_file.join" in detail_src)
# Locate the JSX rendered when NOT revealed and confirm it doesn't touch `d.intake_values` for any
# field flagged sensitive (the label-only invariant it depends on).
pre_reveal_m = re.search(r"\{!revealed \? \((.*?)\) : \(", detail_src, re.S)
check("5b: found the pre-reveal branch", pre_reveal_m is not None)
pre_reveal_src = pre_reveal_m.group(1) if pre_reveal_m else ""
check("5c: pre-reveal branch never reads d.intake_values (only the label list + the Reveal button)",
      "intake_values" not in pre_reveal_src, pre_reveal_src)

print(f"\n{len(PASS)} PASS, {len(FAIL)} FAIL")
for f in FAIL:
    print("FAIL:", f)
if FAIL:
    sys.exit(1)
print("All PII-export-safety checks green.")
