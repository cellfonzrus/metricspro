// Proof harness — HR "Completed" board / Review-packet document rendering regression (2026-07-15,
// owner report, luxelink org_id 854f6d7b-6590-4e4d-88ab-646f560d4f4c).
//
// Root cause: `frontend/src/app/(platform)/hr/onboarding/[employeeId]/page.tsx` (reached from the
// ✅ Completed board's "📄 Review packet" link) had a boundary bug introduced by the multi-file-docs
// commit (b2a8b76): the single-file "View" button only rendered when `documents.length === 0`
// (pre-402 legacy rows) and the new per-file list only rendered when `documents.length > 1`. A task
// with EXACTLY ONE uploaded file — the ordinary case for every normal single-photo upload after
// migration 402 — matched NEITHER branch and rendered nothing: an empty document section, exactly the
// reported symptom. `/hr/compliance` (a different component, `onboarding_compliance_documents`) was
// never affected because it renders off `has_document` directly, unconditioned on `documents.length`
// — which is exactly why the SAME record showed full details there while the Completed/Review-packet
// path showed empty.
//
// This harness translates the JSX conditions LITERALLY (copy them here verbatim if they ever change —
// a drift between this file and the real JSX would make the proof lie) and asserts what actually
// renders for every documents-count case, for OLD (buggy) and NEW (fixed) logic, plus the unaffected
// Compliance-page condition for comparison. No React, no DB — pure boolean/string logic, synthetic
// rows only, zero real PII.

function assertEq(actual, expected, label) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected)
  if (a !== e) { console.error(`FAIL ${label}: expected ${e}, got ${a}`); process.exitCode = 1; return false }
  console.log(`PASS ${label}`)
  return true
}

// ── literal translation of the [employeeId]/page.tsx task-row logic ──────────────────────────────
function oldButtonSlot(t) {
  // {t.has_document && (!t.documents || t.documents.length === 0)
  //   ? <button>View</button>
  //   : (!t.has_document && <span>no document yet</span>)}
  if (t.has_document && (!t.documents || t.documents.length === 0)) return 'button:View'
  if (!t.has_document) return 'span:no-document-yet'
  return null // <- renders nothing (React renders `false`)
}
function oldListSlot(t) {
  // {(t.documents || []).length > 1 && ( ...per-file list... )}
  return (t.documents || []).length > 1 ? `list:${t.documents.length}-files` : null
}
function newButtonSlot(t) {
  // unchanged condition — still the correct legacy-only fallback
  if (t.has_document && (!t.documents || t.documents.length === 0)) return 'button:View'
  if (!t.has_document) return 'span:no-document-yet'
  return null
}
function newListSlot(t) {
  // {(t.documents || []).length > 0 && ( ...per-file list... )}  <- the fix: 1 -> 0
  return (t.documents || []).length > 0 ? `list:${t.documents.length}-files` : null
}
function rendersSomething(buttonSlot, listSlot) {
  return buttonSlot !== null || listSlot !== null
}

// ── the unaffected /hr/compliance row condition, literal translation ─────────────────────────────
// {d.has_document && <button>View</button>}
function complianceSlot(d) {
  return d.has_document ? 'button:View' : null
}

// ── backend derivation, literal translation of onboarding_for_employee (hr/router.py ~L760-766) ───
// has_document = bool(document_path) or bool(documents)
function deriveHasDocument(rec) {
  return Boolean(rec.document_path) || Boolean(rec.documents && rec.documents.length)
}

let all = true

// Case A: pre-402 legacy row — document_path set, documents[] empty/absent. Never regressed.
{
  const rec = { document_path: 'org/emp/legacy.jpg', documents: [] }
  const t = { has_document: deriveHasDocument(rec), documents: rec.documents }
  all &= assertEq(oldButtonSlot(t), 'button:View', 'A pre-402 legacy row — OLD button renders')
  all &= assertEq(newButtonSlot(t), 'button:View', 'A pre-402 legacy row — NEW button renders (unchanged)')
  all &= assertEq(oldListSlot(t), null, 'A pre-402 legacy row — OLD list absent (correct, nothing to list)')
  all &= assertEq(newListSlot(t), null, 'A pre-402 legacy row — NEW list absent (correct, unchanged)')
}

// Case B: THE BUG — exactly one file uploaded post-402 (the ordinary case, e.g. one SS-card photo).
{
  const rec = { document_path: 'org/emp/ss_card.jpg',
                documents: [{ id: 'f1', name: 'ss_card.jpg', uploaded_by: 'employee', uploaded_role: 'employee' }] }
  const t = { has_document: deriveHasDocument(rec), documents: rec.documents }
  const oldB = oldButtonSlot(t), oldL = oldListSlot(t)
  const newB = newButtonSlot(t), newL = newListSlot(t)
  all &= assertEq(oldB, null, 'B ONE file — OLD button slot is nothing')
  all &= assertEq(oldL, null, 'B ONE file — OLD list slot is nothing (length===1, not >1)')
  all &= assertEq(rendersSomething(oldB, oldL), false, 'B ONE file — OLD: THE BUG, task row renders EMPTY')
  all &= assertEq(newB, null, 'B ONE file — NEW button slot still nothing (list owns it now)')
  all &= assertEq(newL, 'list:1-files', 'B ONE file — NEW list slot renders the file')
  all &= assertEq(rendersSomething(newB, newL), true, 'B ONE file — NEW: FIXED, task row renders the file')
  // and prove /hr/compliance was NEVER broken for this exact same record — the "smoking gun" contrast
  // from the bug report (same data, one surface empty, the other full).
  const complianceRow = { has_document: t.has_document }
  all &= assertEq(complianceSlot(complianceRow), 'button:View', 'B ONE file — /hr/compliance renders fine (was never broken)')
}

// Case C: two files (SS-card front + back) — already worked before and after; list must stay >1-safe.
{
  const rec = { document_path: 'org/emp/back.jpg',
                documents: [{ id: 'f1', name: 'front.jpg' }, { id: 'f2', name: 'back.jpg' }] }
  const t = { has_document: deriveHasDocument(rec), documents: rec.documents }
  all &= assertEq(oldListSlot(t), 'list:2-files', 'C TWO files — OLD list already rendered (unaffected by the bug)')
  all &= assertEq(newListSlot(t), 'list:2-files', 'C TWO files — NEW list still renders (no regression)')
}

// Case D: no document at all, upload required — must show the placeholder, not the button or a list.
{
  const rec = { document_path: null, documents: [] }
  const t = { has_document: deriveHasDocument(rec), documents: rec.documents, requires_upload: true }
  all &= assertEq(oldButtonSlot(t), 'span:no-document-yet', 'D no document — OLD placeholder shown')
  all &= assertEq(newButtonSlot(t), 'span:no-document-yet', 'D no document — NEW placeholder shown (unchanged)')
  all &= assertEq(newListSlot(t), null, 'D no document — NEW list absent')
}

// Case E: zero files but a lingering document_path only (should not happen post-402, but the migration
// leaves document_path as a mirror — prove it degrades to the legacy button, not silently to nothing).
{
  const rec = { document_path: 'org/emp/mirror.jpg', documents: undefined }
  const t = { has_document: deriveHasDocument(rec), documents: rec.documents }
  all &= assertEq(newButtonSlot(t), 'button:View', 'E documents undefined, path set — NEW falls back to button correctly')
}

console.log(all ? '\nALL GREEN' : '\nFAILURES ABOVE')
if (!all) process.exit(1)
