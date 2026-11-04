/**
 * "My HR" on the phone: the questions this tenant asks, the paperwork this rep still owes, and the
 * one thing about a sensitive answer that a screen must not get wrong.
 *
 * A SENSITIVE ANSWER HAS THREE STATES, NOT TWO. `GET /hr/onboarding/me` deliberately never echoes a
 * sensitive stored value back (bank account, routing number) — the bundle's own comment says so. So
 * blank does NOT mean unanswered for such a field; it means *we are not telling you*. Reading blank
 * as missing nags a rep about a form they already filled; reading it as answered lets a required
 * field through unfilled. Both render perfectly plausibly, which is why `missingRequired` reports a
 * third list, `cannotTell`, and the screen says "enter it to set or replace it".
 *
 * WHICH QUESTIONS ARE ASKED IS THE TENANT'S DECLARATION. `onboarding_intake_field` is per org, with
 * its own keys, labels, types, sections and required flags, and the server trims it to what a portal
 * may render (`_public_intake_fields`). There is therefore no field list in this file — not even a
 * fallback one. Nothing declared means no form, and the surface says so (owner directive 2026-10-30).
 *
 * WHETHER A REP MAY DELETE A FILE IS THE SERVER'S RULE. `_employee_can_delete_document` computes it
 * once, server-side, precisely so "every caller renders the identical rule instead of re-deriving
 * it". `canDeleteDocument` reads the flag and looks at nothing else — and an ABSENT flag is NO, the
 * §66 allowlist posture: a permission this version did not receive is not a permission.
 */

export type IntakeField = {
  key: string
  label?: string
  section?: string
  field_type?: string
  options?: unknown
  required?: boolean
  sensitive?: boolean
  help_text?: string
}

export type OnboardingDoc = {
  id?: string
  name?: string
  uploaded_at?: string
  employee_can_delete?: boolean
}

export type OnboardingTask = {
  id: string
  label?: string
  description?: string
  status?: string
  requires_upload?: boolean
  requires_signature?: boolean
  has_document?: boolean
  documents?: OnboardingDoc[]
  template_url?: string
  template_name?: string
  returned_reason?: string
  missing_fields?: unknown
  is_mandatory?: boolean
  work_auth?: boolean
  [k: string]: unknown
}

export type OnboardingCategory = { key: string; label?: string; tasks?: OnboardingTask[] }

/** The section a field is shown under when its declaration names none. A PLACE to put it, not a
 *  guess at what it is — the alternative is a declared question that never renders. */
export const UNSECTIONED = 'other'

/** Statuses whose meaning this version knows. Anything else is reported, never counted (§66). */
export const NEEDS_EMPLOYEE = new Set(['pending', 'returned'])
export const WAITING_ON_HR = new Set(['submitted', 'signed'])
export const COMPLETE = new Set(['verified', 'approved', 'complete', 'completed'])

function list<T>(v: unknown): T[] {
  return Array.isArray(v) ? (v as T[]) : []
}

function text(v: unknown): string {
  return typeof v === 'string' ? v : (v == null ? '' : String(v))
}

function answered(v: unknown): boolean {
  // A zero is an answer. Only an absent or whitespace-only value is a blank.
  if (v === 0) return true
  return text(v).trim() !== ''
}

export type Section = { key: string; fields: IntakeField[] }

/** The declared fields grouped into the tenant's declared sections, each in declared order. */
export function sectionsOf(fields: unknown): Section[] {
  const out: Section[] = []
  const byKey = new Map<string, Section>()
  for (const f of list<IntakeField>(fields)) {
    const key = text(f?.key)
    if (!key) continue
    const section = text(f?.section).trim() || UNSECTIONED
    let bucket = byKey.get(section)
    if (!bucket) {
      bucket = { key: section, fields: [] }
      byKey.set(section, bucket)
      out.push(bucket)
    }
    // The label a person reads is the tenant's own; with none declared the KEY is shown rather than
    // a prettified guess, because a guessed label is a label nobody agreed to (§CORE-26's class).
    bucket.fields.push({ ...f, key, label: text(f?.label).trim() || key })
  }
  return out
}

/** `missing` — required and demonstrably unanswered. `cannotTell` — required, sensitive, and not
 *  echoed by the server, so its state is genuinely unknown and must be said rather than assumed. */
export function missingRequired(
  fields: unknown,
  values: Record<string, unknown> | null | undefined,
): { missing: string[]; cannotTell: string[] } {
  const v = values || {}
  const missing: string[] = []
  const cannotTell: string[] = []
  for (const f of list<IntakeField>(fields)) {
    const key = text(f?.key)
    if (!key || !f?.required) continue
    if (answered(v[key])) continue
    if (f.sensitive) cannotTell.push(key)
    else missing.push(key)
  }
  return { missing, cannotTell }
}

/** What may go in the box for this field. A sensitive field renders EMPTY whatever arrived — a value
 *  on screen is a value in a screenshot, and the server is not supposed to have sent one. */
export function valueFor(field: IntakeField, values: Record<string, unknown> | null | undefined): string {
  if (field?.sensitive) return ''
  return text((values || {})[text(field?.key)])
}

/** Every task across the declared categories, in declared order. */
export function visibleTasks(categories: unknown): OnboardingTask[] {
  return list<OnboardingCategory>(categories).flatMap((c) => list<OnboardingTask>(c?.tasks))
}

/** How much is left, split by WHOSE move it is. `submitted` is the rep's part done and HR's part
 *  pending; counting it as outstanding would tell a rep to do something they already did. */
export function taskSummary(categories: unknown): {
  total: number; needsMe: number; waitingOnHr: number; done: number; unknown: string[]
} {
  const tasks = visibleTasks(categories)
  const unknown: string[] = []
  let needsMe = 0
  let waitingOnHr = 0
  let done = 0
  for (const t of tasks) {
    const status = text(t?.status).trim().toLowerCase()
    if (NEEDS_EMPLOYEE.has(status)) needsMe += 1
    else if (WAITING_ON_HR.has(status)) waitingOnHr += 1
    else if (COMPLETE.has(status)) done += 1
    else if (!unknown.includes(status)) unknown.push(status)
  }
  return { total: tasks.length, needsMe, waitingOnHr, done, unknown }
}

/** May this rep delete this file? The server already decided. */
export function canDeleteDocument(doc: OnboardingDoc | null | undefined): boolean {
  return doc?.employee_can_delete === true
}
