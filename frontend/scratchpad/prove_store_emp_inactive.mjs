// Static-source regression proof (2026-07-25, "stores not going inactive" + auto-save/bulk-save UX)
// — NOT committed, scratch only, same convention as prove_payroll_range_exports.mjs /
// prove_people_rule5_wave1.mjs. Traces: toggle -> auto-save write -> read-side filtering on every
// in-scope surface (owner: "check and fix ... the option to save the inactive on stores and
// employees should be auto save and also a save button ... to save multiple or single value").
// Run: node scratchpad/prove_store_emp_inactive.mjs (from frontend/)
import { readFileSync } from 'fs'

let pass = 0, fail = 0
function ok(name, cond) { if (cond) { pass++ } else { fail++; console.log('  FAIL:', name) } }

const admin = readFileSync('src/app/(platform)/storeops/admin/page.tsx', 'utf8')

// ── 1. Auto-save: the Active checkbox calls a dedicated toggle handler, not just local setState ──
ok('admin: employee Active checkbox onChange calls toggleEmpActive (auto-save)',
  /onChange=\{ev => toggleEmpActive\(e, ev\.target\.checked\)\}/.test(admin))
ok('admin: store Active checkbox onChange calls toggleStoreActive (auto-save)',
  /onChange=\{ev => toggleStoreActive\(s, ev\.target\.checked\)\}/.test(admin))
ok('admin: toggleEmpActive PATCHes /employees/{id} with is_active immediately',
  /function toggleEmpActive[\s\S]{0,400}PATCH'[\s\S]{0,50}is_active: checked/.test(admin))
ok('admin: toggleStoreActive PATCHes /stores/{id} with is_active immediately',
  /function toggleStoreActive[\s\S]{0,400}PATCH'[\s\S]{0,50}is_active: checked/.test(admin))

// ── 2. Rollback-on-failure: a failed auto-save reverts the optimistic UI change (never shows a fake
//      success) — checked for BOTH toggles.
ok('admin: toggleEmpActive rolls back on failure (setEmp back to prevVal in the catch)',
  /function toggleEmpActive[\s\S]{0,900}catch[\s\S]{0,80}setEmp\(e\.id, \{ is_active: prevVal \}\)/.test(admin))
ok('admin: toggleStoreActive rolls back on failure (setStore back to prevVal in the catch)',
  /function toggleStoreActive[\s\S]{0,900}catch[\s\S]{0,80}setStore\(s\.id, \{ is_active: prevVal \}\)/.test(admin))

// ── 3. Bulk save: a "Save All Changed" button exists for BOTH tabs, disabled with nothing dirty,
//      and both single-row Save (💾) and the bulk button coexist (owner: "auto save AND a save
//      button ... single or multiple").
ok('admin: bulk saveAllEmps button present, gated on dirtyEmpCount', /disabled=\{!dirtyEmpCount \|\| bulkBusy\} onClick=\{saveAllEmps\}/.test(admin))
ok('admin: bulk saveAllStores button present, gated on dirtyStoreCount', /disabled=\{!dirtyStoreCount \|\| bulkBusy\} onClick=\{saveAllStores\}/.test(admin))
ok('admin: per-row single-Save button (💾) still present for employees', /onClick=\{\(\) => saveEmp\(e\)\}/.test(admin))
ok('admin: per-row single-Save button (💾) still present for stores', /onClick=\{\(\) => saveStore\(s\)\}/.test(admin))
ok('admin: dirty-row indicator ("unsaved") wired off the same isDirty() comparison bulk-save uses',
  /isDirty\(e, origEmps\[e\.id\], EMP_EDIT_FIELDS\)/.test(admin) && /isDirty\(s, origStores\[s\.id\], STORE_EDIT_FIELDS\)/.test(admin))

// ── 4. Every write stays org-scoped (RULE ONE) — org_id is the query-param default on both PATCH
//      endpoints; the admin page never overrides it (no org_id in any request body here).
ok('admin: no request body anywhere sets org_id (org_id stays the query-param default, RULE ONE)',
  !/body: JSON\.stringify\(\{[^}]*org_id/.test(admin))

// ── 5. Read-side filtering: every PICKER surface that assigns something NEW to a store now excludes
//      inactive stores, while surfaces that only need a market/name LOOKUP (payroll/payroll-tax/
//      reports pages) are deliberately left untouched (verified by absence of the filter there — a
//      historical report must still label a now-closed store's rows with its market).
const PICKERS = {
  'storeops/schedule (assign a NEW shift)': 'src/app/(platform)/storeops/schedule/page.tsx',
  'storeops/shift-extensions (file a NEW extension request)': 'src/app/(platform)/storeops/shift-extensions/page.tsx',
  'hr/people (assign a NEW hire\'s store)': 'src/app/(platform)/hr/people/page.tsx',
}
for (const [label, path] of Object.entries(PICKERS)) {
  const src = readFileSync(path, 'utf8')
  ok(`${label}: store picker excludes inactive stores (is_active !== false)`,
    /is_active !== false/.test(src))
}

const LOOKUP_ONLY = {
  'storeops/payroll (market lookup on historical rows)': 'src/app/(platform)/storeops/payroll/page.tsx',
  'storeops/reports (market lookup on historical rows)': 'src/app/(platform)/storeops/reports/page.tsx',
}
for (const [label, path] of Object.entries(LOOKUP_ONLY)) {
  const src = readFileSync(path, 'utf8')
  ok(`${label}: deliberately UNCHANGED (still sees every store, incl. inactive, for correct historical market labels)`,
    !/is_active !== false/.test(src))
}

console.log(`\n${pass} passed, ${fail} failed`)
if (fail) process.exit(1)
console.log('ALL GREEN')
