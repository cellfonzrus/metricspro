// ONE HOME — which request persists which field of an EMPLOYEE record from an editor row (index §19.35).
//
// Every browser write to `PATCH /api/v1/storeops/employees/{id}` and its per-employee sub-resources
// (`PUT …/lunch-config`, `PUT …/face-config`) from a row editor is BUILT here and PLANNED by
// `lib/rowSave.ts::planRowSave`, so a row's single Save sends every slice the person edited — never
// just the one whose button happened to be on screen (the 2026-09-29 Vzone defect: hourly rates typed,
// the Lunch 💾 pressed, the pay PATCH never sent). `backend/harness_row_save_lock.py` fails the build if
// a page writes these paths itself instead of planning them from this file.
//
// The request BODIES are unchanged from the editors they were lifted out of (HR → Employees & Pay
// `savePay` / `saveLunch` / `saveFace`, Roles & Access `saveDetails` / the row Save's email PATCH),
// except one tightening, the platform's existing rule "you may not write a figure you may not see"
// (index §14 DM sweep): `pay_rate` is sent only when the roster read actually carried it.
//
// Every slice declares `echo` — which key of the endpoint's REPLY carries the stored value of each
// request key — so `rowSave.runRowSave` counts a slice as saved only when the reply proves it (§19.37).
//
// Pure module: only erasable `import type`, no runtime imports (the node proof loads it as is).
import type { RowSlice } from './rowSave'

const has = (row: any, k: string) => !!row && Object.prototype.hasOwnProperty.call(row, k)
const idOk = (row: any) => row && row.id != null && String(row.id) !== '' && !(Number(row.id) <= 0)
const empPath = (row: any) => `/api/v1/storeops/employees/${encodeURIComponent(String(row.id))}`

/** Context the HR pay slice needs: whether the tenant has the salary columns (migrations 416/417). */
export interface EmpPayCtx { salaryFieldsAvailable: boolean }

/** PAY — `PATCH /storeops/employees/{id}`: pay_rate (+ pay_basis / pay_amount / termination_date once
 *  the salary columns exist). Server-side still manager-gated and pay-visibility-gated. */
export const EMP_PAY_SLICE: RowSlice<any, EmpPayCtx> = {
  key: 'pay',
  label: 'pay',
  fields: ['pay_rate', 'pay_basis', 'pay_amount', 'termination_date'],
  // The PATCH replies with the stored row (PostgREST UPDATE … RETURNING), and names any pay field its
  // pay-visibility gate dropped in `pay_fields_ignored` — both read by rowSave.notPersisted (§19.37).
  echo: { pay_rate: 'pay_rate', pay_basis: 'pay_basis', pay_amount: 'pay_amount', termination_date: 'termination_date' },
  build: (row, ctx) => {
    if (!idOk(row)) return null
    const body: Record<string, unknown> = {}
    if (has(row, 'pay_rate')) body.pay_rate = Number(row.pay_rate) || 0
    if (ctx && ctx.salaryFieldsAvailable && has(row, 'pay_basis')) {
      body.pay_basis = row.pay_basis || 'hourly'
      body.pay_amount = row.pay_basis && row.pay_basis !== 'hourly'
        ? (row.pay_amount === '' || row.pay_amount == null ? null : Number(row.pay_amount))
        : null
      body.termination_date = row.termination_date || null
    }
    if (!Object.keys(body).length) return null
    return { path: empPath(row), method: 'PATCH', body }
  },
  // The server echoes the saved row (pay keys stripped for a caller who may not see pay — then there is
  // nothing to fold back and the typed values stand as the snapshot).
  reseat: (resp) => {
    const out: Record<string, unknown> = {}
    for (const k of ['pay_rate', 'pay_basis', 'pay_amount', 'termination_date']) if (has(resp, k)) out[k] = resp[k]
    return out
  },
}

/** LUNCH — `PUT /storeops/employees/{id}/lunch-config`. enabled true/false = override, null = inherit
 *  the tenant default; minutes only accompany an explicit 'on'. */
export const EMP_LUNCH_SLICE: RowSlice<any, unknown> = {
  key: 'lunch',
  label: 'lunch',
  fields: ['lunch_deduction_enabled', 'lunch_deduction_minutes'],
  echo: { enabled: 'lunch_deduction_enabled', minutes: 'lunch_deduction_minutes' },
  build: (row) => {
    if (!idOk(row)) return null
    const enabled = row.lunch_deduction_enabled === true ? true : row.lunch_deduction_enabled === false ? false : null
    const m = String(row.lunch_deduction_minutes ?? '').trim()
    return { path: `${empPath(row)}/lunch-config`, method: 'PUT',
             body: { enabled, minutes: enabled === true && m !== '' ? Number(m) : null } }
  },
  reseat: (resp) => resp && typeof resp === 'object'
    ? { lunch_deduction_enabled: resp.lunch_deduction_enabled ?? null, lunch_deduction_minutes: resp.lunch_deduction_minutes ?? null }
    : null,
}

/** FACE — `PUT /storeops/employees/{id}/face-config`: assignment + biometric consent. The consent
 *  timestamp/source are server-generated and folded back from the response. */
export const EMP_FACE_SLICE: RowSlice<any, unknown> = {
  key: 'face',
  label: 'face recognition',
  fields: ['face_recognition_enabled', 'face_consent_status'],
  echo: { enabled: 'face_recognition_enabled', consent: 'face_consent_status' },
  build: (row) => {
    if (!idOk(row)) return null
    const enabled = row.face_recognition_enabled === true ? true : row.face_recognition_enabled === false ? false : null
    const c = row.face_consent_status
    return { path: `${empPath(row)}/face-config`, method: 'PUT',
             body: { enabled, consent: c === 'signed' || c === 'declined' ? c : null } }
  },
  reseat: (resp) => resp && typeof resp === 'object'
    ? { face_recognition_enabled: resp.face_recognition_enabled ?? null, face_consent_status: resp.face_consent_status ?? null,
        face_consent_at: resp.face_consent_at ?? null, face_consent_source: resp.face_consent_source ?? null }
    : null,
}

/** HR → Employees & Pay: every slice one row edits, in save order. */
export const HR_EMPLOYEE_ROW_SLICES = [EMP_PAY_SLICE, EMP_LUNCH_SLICE, EMP_FACE_SLICE] as const

/** DETAILS — Roles & Access edit panel: `PATCH /storeops/employees/{id}` with the whole details form
 *  (name, home store, job title, phone, active, and Pay $/hr when the roster read carried it). */
export const EMP_DETAILS_SLICE: RowSlice<any, unknown> = {
  key: 'details',
  label: 'details',
  fields: ['name', 'home_store', 'role', 'phone', 'is_active', 'pay_rate'],
  echo: { name: 'name', home_store: 'home_store', role: 'role', phone: 'phone', is_active: 'is_active', pay_rate: 'pay_rate' },
  build: (row) => {
    if (!idOk(row)) return null
    const body: Record<string, unknown> = {
      name: row.name, home_store: row.home_store, role: row.role,
      phone: row.phone || null, is_active: !!row.is_active,
    }
    if (has(row, 'pay_rate')) body.pay_rate = row.pay_rate == null || row.pay_rate === '' ? null : Number(row.pay_rate)
    return { path: empPath(row), method: 'PATCH', body }
  },
}

/** EMAIL — Roles & Access inline Email column: `PATCH /storeops/employees/{id}` {email}. A manually
 *  added login (id <= 0) has no employee row, so there is nothing to write. */
export const EMP_EMAIL_SLICE: RowSlice<any, unknown> = {
  key: 'email',
  label: 'email',
  fields: ['email'],
  echo: { email: 'email' },
  build: (row) => idOk(row)
    ? { path: empPath(row), method: 'PATCH', body: { email: String(row.email || '').trim() || null } }
    : null,
}

/** Roles & Access → People: the employee-record slices one row edits (the role ASSIGNMENT is a login
 *  record, saved by `/core/users/assign` after these). */
export const ROLES_EMPLOYEE_ROW_SLICES = [EMP_DETAILS_SLICE, EMP_EMAIL_SLICE] as const

// ── ONE-CLICK WRITES, in the same home (index §14y) ──────────────────────────────────────────────
// Not every employee write is a row EDITOR. A control whose value is chosen and written in the SAME
// click cannot leave a typed edit pending, so it is not a slice and must not need a row's Save —
// that is the category `harness_row_save_lock.py` already calls "one-click ACTIONS". It still builds
// its request HERE, because the thing the lock is actually protecting is that no page spells an
// employee-write path itself (one fact, one home, dereferenced).

/** PAY FROM SCHEDULE — `PATCH /storeops/employees/{employee_id}/pay-from-schedule` (mig 1073).
 *
 *  The per-employee tick box beside the person's NAME on HR → Employees & Pay. THREE values, not
 *  two: `true` pay from the schedule when a day has no clock-in, `false` clocked hours only, and
 *  `null` follow the organisation's default — which is a different fact from "no", and conflating
 *  them would silently stop paying every unmeasured day in a tenant whose default is yes.
 *
 *  NOTE THE KEY: this path takes the BUSINESS `employee_id` ('E252'), not the numeric row `id` every
 *  slice above uses. It is deliberately not `empPath` — borrowing that helper would send the wrong
 *  key and 404 on every save.
 *
 *  Pay visibility is re-checked SERVER-side on every call (market manager and above, per-org
 *  config), so a caller who may not see pay cannot set this by calling it directly.
 */
export function buildPayFromScheduleWrite(employeeId: string, value: boolean | null) {
  const id = String(employeeId || '').trim()
  if (!id) return null
  return {
    path: `/api/v1/storeops/employees/${encodeURIComponent(id)}/pay-from-schedule`,
    method: 'PATCH' as const,
    body: { pay_from_schedule: value },
  }
}
