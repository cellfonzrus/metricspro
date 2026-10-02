// Proof harness — ONE ROW, ONE SAVE (owner report 2026-09-29, index §19.35). Owner: "i just saved hourly
// salary in vzone but it did not save when i came back".
//
// This harness does NOT re-implement the planner. It imports the REAL `src/lib/rowSave.ts` and the REAL
// `src/lib/employeeRowSlices.ts` (Node >= 22.18 strips their erasable TypeScript natively) and drives the
// genuine functions. If either stops loading, the harness fails instead of testing a copy.
//
// SECTIONS
//   A. the reported defect, reproduced — the owner's exact Vzone edit (E278: pay 0 → 17, lunch default →
//      on). The PRE-FIX editor bound one button per slice, so the button on screen (Lunch) planned ONLY
//      the lunch PUT and the pay PATCH was never sent (live access log: 4× PUT …/lunch-config, 0× PATCH).
//      The row's one Save plans BOTH.
//   B. bodies unchanged — each slice builds exactly the body the pre-fix editor sent (the old savePay /
//      saveLunch / saveFace / saveDetails / email PATCH, transcribed below from main), bar one tightening:
//      a withheld pay_rate is never sent.
//   C. only what changed — a clean row plans nothing; one edited field plans one slice.
//   D. honest results — one slice failing never stops another, is never reported as saved, and stays
//      dirty; the saved one is clean.
//   E. a reload never drops typed work — rebaseRows keeps a dirty row's edits over fresh values.
//   F. counting — pendingRowCount drives the banner + leave guard.
//   G. Roles & Access — the row Save and "Save details" both plan details (incl. pay) AND email; a manual
//      login (id <= 0) plans nothing.
//   V. a 2xx is not proof (§19.37, 2026-10-02) — the owner's case (admin, hourly, pay_rate 0 → 18) is saved
//      only because the reply shows 18 stored; a 200 whose gate dropped pay_rate (`pay_fields_ignored`),
//      kept another value, or echoed nothing is NOT saved and names the field (pre-fix: "Saved").
//   N. negative controls — the checks are not vacuous.
//
// Run:  node frontend/prove_row_save.mjs     (Node >= 22.18; no node_modules, no network, no DB)

import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const RS = await import(pathToFileURL(join(HERE, 'src/lib/rowSave.ts')).href)
const SL = await import(pathToFileURL(join(HERE, 'src/lib/employeeRowSlices.ts')).href)

let pass = 0, fail = 0
function check(name, cond, detail) {
  if (cond) { pass++; console.log(`  PASS  ${name}`) }
  else { fail++; console.log(`  FAIL  ${name}${detail !== undefined ? `  :: ${typeof detail === 'string' ? detail : JSON.stringify(detail)}` : ''}`) }
}
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b)
const HR = SL.HR_EMPLOYEE_ROW_SLICES
const CTX = { salaryFieldsAvailable: true }

// The live row as GET /storeops/employees returned it (Vzone, E278, read 2026-09-29).
const E278 = { id: 278, employee_id: 'E278', name: 'mehribon gulyamova', role: 'sales_rep', pay_rate: 0, pay_basis: 'hourly',
  pay_amount: null, termination_date: null, lunch_deduction_enabled: null, lunch_deduction_minutes: null,
  face_recognition_enabled: null, face_consent_status: null, is_active: true }

// ── the PRE-FIX bodies, transcribed from main (frontend/src/app/(platform)/hr/page.tsx, admin/roles) ──────
function oldSavePayBody(e, salaryFieldsAvailable) {
  const body = { pay_rate: Number(e.pay_rate) || 0 }
  if (salaryFieldsAvailable && Object.prototype.hasOwnProperty.call(e, 'pay_basis')) {
    body.pay_basis = e.pay_basis || 'hourly'
    body.pay_amount = e.pay_basis && e.pay_basis !== 'hourly' ? (e.pay_amount === '' || e.pay_amount == null ? null : Number(e.pay_amount)) : null
    body.termination_date = e.termination_date || null
  }
  return body
}
function oldLunchBody(mode, minutes) {
  return { enabled: mode === 'default' ? null : mode === 'on', minutes: mode === 'on' && minutes.trim() !== '' ? Number(minutes) : null }
}
function oldFaceBody(mode, consent) {
  return { enabled: mode === 'default' ? null : mode === 'on', consent: consent === '' ? null : consent }
}
function oldDetailsBody(e) {
  const body = { name: e.name, home_store: e.home_store, role: e.role, phone: e.phone || null, is_active: !!e.is_active }
  if (Object.prototype.hasOwnProperty.call(e, 'pay_rate')) body.pay_rate = e.pay_rate == null || e.pay_rate === '' ? null : Number(e.pay_rate)
  return body
}

// A DB-free double of the three real endpoints' REPLY shapes (storeops/router.py): `update_employee`
// returns the stored row (UPDATE … RETURNING) — minus pay keys and plus `pay_fields_ignored` when its
// pay-visibility gate drops them; `set_employee_lunch_config` / `set_employee_face_config` return the
// stored lunch / face columns. `canSeePay=false` models a caller below the org's pay-visibility line.
function server(stored, { canSeePay = true, keepPayRate = true } = {}) {
  const GATED = ['pay_rate', 'pay_basis', 'pay_amount', 'termination_date']
  return {
    stored,
    async send(w) {
      if (w.method === 'PATCH') {
        let body = { ...w.body }, ignored = []
        if (!canSeePay) {
          ignored = Object.keys(body).filter(k => GATED.includes(k)).sort()
          for (const k of ignored) delete body[k]
          if (!Object.keys(body).length) { const e = new Error('Pay fields are restricted for your role'); e.status = 403; throw e }
        }
        if (!keepPayRate) delete body.pay_rate          // a column the write silently did not take
        for (const [k, v] of Object.entries(body)) stored[k] = (k === 'pay_rate' || k === 'pay_amount') && v != null ? Number(v) : v
        const out = { ...stored }
        if (!canSeePay) for (const k of ['pay_rate', 'pay_amount']) delete out[k]
        if (ignored.length) out.pay_fields_ignored = ignored
        return out
      }
      if (w.path.endsWith('/lunch-config')) {
        stored.lunch_deduction_enabled = w.body.enabled
        stored.lunch_deduction_minutes = w.body.minutes == null ? null : Math.max(0, Math.trunc(Number(w.body.minutes)))
        return { ok: true, employee_id: stored.employee_id, lunch_deduction_enabled: stored.lunch_deduction_enabled, lunch_deduction_minutes: stored.lunch_deduction_minutes }
      }
      if (w.path.endsWith('/face-config')) {
        stored.face_recognition_enabled = w.body.enabled
        stored.face_consent_status = w.body.consent
        return { ok: true, employee_id: stored.employee_id, face_recognition_enabled: stored.face_recognition_enabled,
                 face_consent_status: stored.face_consent_status, face_consent_at: w.body.consent ? '2026-10-02T12:00:00Z' : null, face_consent_source: null }
      }
      throw new Error('unknown endpoint ' + w.path)
    },
  }
}
// The PRE-§19.37 runRowSave, transcribed from main: any 2xx counted as saved.
async function oldRunRowSave(writes, send) {
  const res = { saved: [], failed: [] }
  for (const w of writes) {
    try { res.saved.push({ slice: w.slice, label: w.label, response: await send(w) }) }
    catch (e) { res.failed.push({ slice: w.slice, label: w.label, error: String(e?.message || e) }) }
  }
  return res
}

console.log('A. the reported defect, reproduced (Vzone E278: hourly rate typed + Lunch set On)')
{
  const snap = { ...E278 }
  const row = { ...E278, pay_rate: '17', lunch_deduction_enabled: true }
  const dirty = RS.dirtySlices(row, snap, HR).map(s => s.key)
  check('A1 both slices are edited', eq(dirty, ['pay', 'lunch']), dirty)
  // PRE-FIX: the Lunch 💾 saved the lunch slice alone.
  const oldPlan = RS.planRowSave(row, snap, [SL.EMP_LUNCH_SLICE], CTX)
  const unsent = dirty.filter(k => !oldPlan.some(w => w.slice === k))
  check('A2 PRE-FIX model: the visible Lunch button plans only PUT …/lunch-config', eq(oldPlan.map(w => `${w.method} ${w.path}`), ['PUT /api/v1/storeops/employees/278/lunch-config']), oldPlan)
  check('A3 PRE-FIX model: the pay edit is left unsent (the live defect)', eq(unsent, ['pay']), unsent)
  // THE FIX: the row's one Save.
  const plan = RS.planRowSave(row, snap, HR, CTX)
  check('A4 the row Save plans the pay PATCH', plan.some(w => w.method === 'PATCH' && w.path === '/api/v1/storeops/employees/278' && w.body.pay_rate === 17), plan)
  check('A5 …and the lunch PUT, in one action', plan.some(w => w.method === 'PUT' && w.path.endsWith('/278/lunch-config') && w.body.enabled === true && w.body.minutes === null), plan)
  check('A6 nothing dirty is left unplanned', dirty.every(k => plan.some(w => w.slice === k)))
}

console.log('B. bodies unchanged from the pre-fix editors')
{
  const cases = [
    { ...E278, pay_rate: '17' },
    { ...E278, pay_rate: '' },
    { ...E278, pay_basis: 'annual', pay_amount: '52000', termination_date: '2026-10-01' },
    { ...E278, pay_basis: 'weekly', pay_amount: '' },
  ]
  for (const [i, r] of cases.entries()) {
    const w = SL.EMP_PAY_SLICE.build(r, CTX)
    check(`B1.${i} pay body == old savePay body`, eq(w.body, oldSavePayBody(r, true)), { got: w.body, want: oldSavePayBody(r, true) })
    const w2 = SL.EMP_PAY_SLICE.build(r, { salaryFieldsAvailable: false })
    check(`B2.${i} pay body (pre-416 tenant) == old`, eq(w2.body, oldSavePayBody(r, false)), { got: w2.body })
  }
  const lunchCases = [['on', '30', { lunch_deduction_enabled: true, lunch_deduction_minutes: '30' }],
                      ['on', '', { lunch_deduction_enabled: true, lunch_deduction_minutes: null }],
                      ['off', '', { lunch_deduction_enabled: false, lunch_deduction_minutes: null }],
                      ['default', '', { lunch_deduction_enabled: null, lunch_deduction_minutes: null }]]
  for (const [mode, min, fields] of lunchCases) {
    const w = SL.EMP_LUNCH_SLICE.build({ ...E278, ...fields }, CTX)
    check(`B3 lunch '${mode}'/${JSON.stringify(min)} body == old saveLunch body`, eq(w.body, oldLunchBody(mode, min)) && w.method === 'PUT', w.body)
  }
  const faceCases = [['on', 'signed', { face_recognition_enabled: true, face_consent_status: 'signed' }],
                     ['off', 'declined', { face_recognition_enabled: false, face_consent_status: 'declined' }],
                     ['default', '', { face_recognition_enabled: null, face_consent_status: null }]]
  for (const [mode, consent, fields] of faceCases) {
    const w = SL.EMP_FACE_SLICE.build({ ...E278, ...fields }, CTX)
    check(`B4 face '${mode}'/'${consent}' body == old saveFace body`, eq(w.body, oldFaceBody(mode, consent)) && w.method === 'PUT' && w.path.endsWith('/face-config'), w.body)
  }
  const det = { id: 12, name: 'A', home_store: 'B-1', role: 'Rep', phone: '', is_active: 1, pay_rate: '18.5', email: 'a@x' }
  check('B5 details body == old saveDetails body', eq(SL.EMP_DETAILS_SLICE.build(det).body, oldDetailsBody(det)), SL.EMP_DETAILS_SLICE.build(det).body)
  const detNoPay = { ...det }; delete detNoPay.pay_rate
  check('B6 details: pay withheld by the server → pay_rate not sent (as before)', !('pay_rate' in SL.EMP_DETAILS_SLICE.build(detNoPay).body))
  check('B7 email body == old row-Save email PATCH', eq(SL.EMP_EMAIL_SLICE.build({ id: 12, email: '  a@x ' }).body, { email: 'a@x' })
    && eq(SL.EMP_EMAIL_SLICE.build({ id: 12, email: '' }).body, { email: null }))
  const noPay = { ...E278, pay_basis: 'annual', pay_amount: '1' }; delete noPay.pay_rate
  check('B8 THE ONE TIGHTENING: a withheld pay_rate is never sent (old code sent pay_rate: 0)',
    !('pay_rate' in SL.EMP_PAY_SLICE.build(noPay, CTX).body) && oldSavePayBody(noPay, true).pay_rate === 0)
}

console.log('C. only what changed')
{
  check('C1 a clean row plans nothing', RS.planRowSave({ ...E278 }, { ...E278 }, HR, CTX).length === 0)
  check('C2 null / undefined / "" compare equal (the setup grids\' old rule)', !RS.fieldsDirty({ a: null, b: '' }, { a: '', b: undefined }, ['a', 'b']))
  check('C3 no snapshot yet → not dirty', !RS.rowDirty({ ...E278, pay_rate: 5 }, undefined, HR))
  const p = RS.planRowSave({ ...E278, face_consent_status: 'signed' }, { ...E278 }, HR, CTX)
  check('C4 one edited field → one request', p.length === 1 && p[0].slice === 'face', p)
}

console.log('D. honest results')
{
  const snap = { ...E278 }
  const row = { ...E278, pay_rate: '17', lunch_deduction_enabled: true }
  const plan = RS.planRowSave(row, snap, HR, CTX)
  const sent = []
  const res = await RS.runRowSave(plan, async w => {
    sent.push(w.slice)
    if (w.slice === 'lunch') throw new Error("Couldn't save the setting — is migration 418 applied?")
    return { ...row, pay_rate: 17 }
  })
  check('D1 a failing slice does not stop the other', eq(sent, ['pay', 'lunch']), sent)
  check('D2 saved = pay, failed = lunch', eq(res.saved.map(s => s.slice), ['pay']) && eq(res.failed.map(f => f.slice), ['lunch']), res)
  const c = RS.commitSaved(row, snap, HR, res)
  const still = RS.dirtySlices(c.row, c.snap, HR).map(s => s.key)
  check('D3 after the save only the failed slice is still unsaved', eq(still, ['lunch']), still)
  check('D4 the saved rate is folded back from the server echo', c.row.pay_rate === 17 && c.snap.pay_rate === 17)
  check('D4b `back` carries only server-reseated values (a keystroke made mid-save is never overwritten)',
    c.back.pay_rate === 17 && !('lunch_deduction_enabled' in c.back) && !('name' in c.back), c.back)
  const msg = RS.rowSaveMessage('mehribon gulyamova', res)
  check('D5 the message names what was saved AND what was not', /Saved pay/.test(msg) && /lunch NOT saved/.test(msg), msg)
  check('D6 the message never claims an unsent slice', !/Saved[^—]*lunch/.test(msg), msg)
  const allOk = await RS.runRowSave(plan, w => server({ ...row }).send(w))
  check('D7 all saved → "Saved pay, lunch for …"', RS.rowSaveMessage('X', allOk) === 'Saved pay, lunch for X', RS.rowSaveMessage('X', allOk))
  const face = RS.commitSaved({ ...E278, face_consent_status: 'signed' }, { ...E278 }, HR,
    { saved: [{ slice: 'face', label: 'face recognition', response: { face_recognition_enabled: null, face_consent_status: 'signed', face_consent_at: '2026-09-29T18:00:00Z', face_consent_source: 'hr' } }], failed: [] })
  check('D8 server-owned values (consent timestamp) are re-seated, row clean', face.row.face_consent_at === '2026-09-29T18:00:00Z' && !RS.rowDirty(face.row, face.snap, HR))
}

console.log('E. a reload never drops typed work')
{
  const a = { ...E278 }, b = { ...E278, id: 279, name: 'Nikolas' }
  const typed = [{ ...a, pay_rate: '17' }, { ...b }]
  const snaps = { 278: a, 279: b }
  const fresh = [{ ...a, lunch_deduction_enabled: true }, { ...b, pay_rate: 16 }]
  const out = RS.rebaseRows(typed, r => snaps[r.id], fresh, r => String(r.id), HR)
  check('E1 the dirty row keeps the typed rate', out[0].pay_rate === '17')
  check('E2 …and takes the fresh value of what it did not edit', out[0].lunch_deduction_enabled === true)
  check('E3 a clean row takes the fresh values', out[1].pay_rate === 16)
  check('E4 the kept edit is still dirty against the fresh snapshot', RS.rowDirty(out[0], fresh[0], HR))
}

console.log('F. counting')
{
  const rows = [{ ...E278, pay_rate: 1 }, { ...E278, id: 2 }, { ...E278, id: 3, lunch_deduction_enabled: false }]
  const snaps = { 278: E278, 2: { ...E278, id: 2 }, 3: { ...E278, id: 3 } }
  check('F1 pendingRowCount counts rows with any unsaved slice', RS.pendingRowCount(rows, r => snaps[r.id], HR) === 2)
}

console.log('G. Roles & Access — one plan for the employee record')
{
  const R = SL.ROLES_EMPLOYEE_ROW_SLICES
  const snap = { id: 12, name: 'A', home_store: 'B-1', role: 'Rep', phone: '', is_active: true, pay_rate: 15, email: 'a@x' }
  const row = { ...snap, pay_rate: '18', email: 'new@x' }
  const plan = RS.planRowSave(row, snap, R, undefined)
  check('G1 pay typed in the Edit panel + email edited → BOTH planned by either button', eq(plan.map(w => w.slice), ['details', 'email']), plan)
  check('G2 the details PATCH carries the rate', plan[0].body.pay_rate === 18 && plan[0].method === 'PATCH')
  check('G3 a manual login (id <= 0) writes no employee row', RS.planRowSave({ ...row, id: -4 }, { ...snap, id: -4 }, R, undefined).length === 0)
}

console.log('V. a 2xx is not proof — a save counts only what the server shows it stored (§19.37, 2026-10-02)')
{
  // V1–V4: the owner's exact case — the Vzone ADMIN (pay visible), E278 hourly, pay_rate 0 → 18.
  const snap = { ...E278 }
  const row = { ...E278, pay_rate: '18' }
  const db = server({ ...E278 })
  const plan = RS.planRowSave(row, snap, HR, CTX)
  check('V1 admin, hourly, 0 → 18: the row Save plans ONE PATCH carrying pay_rate 18',
    plan.length === 1 && plan[0].method === 'PATCH' && plan[0].path === '/api/v1/storeops/employees/278' && plan[0].body.pay_rate === 18
    && plan[0].body.pay_basis === 'hourly', plan)
  const res = await RS.runRowSave(plan, w => db.send(w))
  check('V2 …the server stores 18 and the reply proves it → saved', res.saved.length === 1 && !res.failed.length && db.stored.pay_rate === 18, res)
  const c = RS.commitSaved(row, snap, HR, res)
  check('V3 …the row reads back the STORED rate and is clean', c.row.pay_rate === 18 && !RS.rowDirty(c.row, c.snap, HR), c.row.pay_rate)
  check('V4 …message "Saved pay for …"', RS.rowSaveMessage('mehribon gulyamova', res) === 'Saved pay for mehribon gulyamova')

  // V5–V8: the gate drops a field with a 200 (Roles & Access details: name + a typed rate, caller below
  // the pay-visibility line). PRE-FIX: "Saved details" and the typed rate became the snapshot.
  const R = SL.ROLES_EMPLOYEE_ROW_SLICES
  const dSnap = { id: 12, employee_id: 'E012', name: 'A', home_store: 'B-1', role: 'Rep', phone: '', is_active: true, email: 'a@x' }
  const dRow = { ...dSnap, name: 'A B', pay_rate: '18' }
  const dPlan = RS.planRowSave(dRow, dSnap, R, undefined)
  const before = await oldRunRowSave(dPlan, w => server({ ...dSnap }, { canSeePay: false }).send(w))
  check('V5 PRE-FIX model: a 200 whose gate dropped pay_rate was reported "Saved details" (the defect class)',
    RS.rowSaveMessage('A B', before) === 'Saved details for A B' && before.saved[0].response.pay_fields_ignored?.[0] === 'pay_rate', before)
  const after = await RS.runRowSave(dPlan, w => server({ ...dSnap }, { canSeePay: false }).send(w))
  const msg = RS.rowSaveMessage('A B', after)
  check('V6 THE FIX: the same reply fails the slice and NAMES pay_rate', after.failed.length === 1 && /details NOT saved/.test(msg) && /pay_rate/.test(msg), msg)
  const dc = RS.commitSaved(dRow, dSnap, R, after)
  check('V7 …and the row stays visibly unsaved (the typed rate is not taken as stored)', RS.rowDirty(dc.row, dc.snap, R))

  // V8: the server kept a different value with a 200 (a write that silently did not take the column).
  const stale = await RS.runRowSave(plan, w => server({ ...E278 }, { keepPayRate: false }).send(w))
  check('V8 a 200 whose stored pay_rate is still 0 → NOT saved, naming what was stored',
    stale.failed.length === 1 && /pay_rate: the server stored nothing, not 18|pay_rate: the server stored 0, not 18/.test(stale.failed[0].error), stale.failed)
  // V9: a reply that does not carry the field cannot confirm it.
  const blind = await RS.runRowSave(plan, async () => ({ ok: true }))
  check('V9 a bare {ok:true} proves nothing → NOT saved', blind.failed.length === 1 && /did not confirm/.test(blind.failed[0].error), blind.failed)
  // V10: a pay-hidden caller on HR's pay slice gets the 403 (every field gated) — surfaced, not saved.
  const hid = await RS.runRowSave(plan, w => server({ ...E278 }, { canSeePay: false }).send(w))
  check('V10 HR pay save by a caller below the pay line → the 403 is the message', hid.failed.length === 1 && /restricted/.test(hid.failed[0].error), hid)
  // V11: every registered slice's echo covers every key its build can send (else notPersisted fails it).
  const full = { ...E278, id: 9, pay_rate: '1', pay_basis: 'annual', pay_amount: '2', termination_date: '2026-10-01',
                 lunch_deduction_enabled: true, lunch_deduction_minutes: '30', face_recognition_enabled: true, face_consent_status: 'signed',
                 name: 'n', home_store: 's', phone: 'p', email: 'e' }
  const uncovered = []
  for (const s of [...HR, ...R]) {
    const b = s.build(full, CTX)
    for (const k of Object.keys(b.body)) if (!s.echo || !s.echo[k]) uncovered.push(`${s.key}.${k}`)
  }
  check('V11 every slice declares an echo key for every request key', uncovered.length === 0, uncovered)
  // V12: lunch + face round-trip through their real reply shapes as saved (no false failure).
  const lf = { ...E278, lunch_deduction_enabled: true, lunch_deduction_minutes: '30', face_recognition_enabled: false, face_consent_status: 'declined' }
  const lfRes = await RS.runRowSave(RS.planRowSave(lf, E278, HR, CTX), w => server({ ...E278 }).send(w))
  check('V12 lunch + face replies prove their fields → both saved', eq(lfRes.saved.map(x => x.slice), ['lunch', 'face']) && !lfRes.failed.length, lfRes)
  check('V13 numbers compare as numbers, blanks as blanks', RS.sameStoredValue(18, '18.00') && RS.sameStoredValue(null, '') && !RS.sameStoredValue(18, 0) && !RS.sameStoredValue('', 0))
}

console.log('N. negative controls')
{
  check('N1 a changed value IS dirty', RS.fieldChanged({ a: 1 }, { a: 2 }, 'a'))
  check('N2 an empty plan runs nothing', (await RS.runRowSave([], async () => { throw new Error('x') })).failed.length === 0)
  const oneSlice = RS.planRowSave({ ...E278, pay_rate: 9, lunch_deduction_enabled: true }, E278, [SL.EMP_PAY_SLICE], CTX)
  check('N3 a subset of slices plans a subset (the check in A would see it)', oneSlice.length === 1)
}

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
