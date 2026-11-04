/**
 * "My HR" — proved before the screen exists.
 *
 * THE TRAP THIS FILE EXISTS FOR. `GET /hr/onboarding/me` deliberately **never echoes a sensitive
 * value back** (bank account, routing number). So for a sensitive field, a blank value does NOT mean
 * "unanswered" — it means "we are not telling you". A screen that read blank as missing would nag a
 * rep forever about a form they already filled; one that read blank as answered would let a required
 * field through unfilled. Both look entirely plausible on screen. The honest answer is a third
 * state, and these tests pin it.
 *
 * The second trap is the same class as everywhere else here: which questions a rep is asked is the
 * TENANT's declaration (`onboarding_intake_field`, per org, with its own labels, types, sections and
 * required flags). There is no field list in the code, so there is no field list in these tests
 * either — each case builds a declaration and checks the answer follows it.
 */
import { describe, expect, it } from 'vitest'

import {
  canDeleteDocument, missingRequired, sectionsOf, taskSummary, valueFor, visibleTasks,
} from './hr-intake'

const FIELDS = [
  { key: 'first_name', label: 'First name', section: 'personal', field_type: 'text', required: true, sensitive: false },
  { key: 'middle', label: 'Middle name', section: 'personal', field_type: 'text', required: false, sensitive: false },
  { key: 'street', label: 'Street', section: 'address', field_type: 'text', required: true, sensitive: false },
  { key: 'routing', label: 'Routing number', section: 'direct_deposit', field_type: 'text', required: true, sensitive: true },
  { key: 'shirt', label: 'Shirt size', section: 'custom', field_type: 'select', options: ['S', 'M'], required: false, sensitive: false },
]

describe('sectionsOf — the tenant`s own sections, in the declared order', () => {
  it('groups by the declared section and keeps each field`s declared order', () => {
    const out = sectionsOf(FIELDS)
    expect(out.map((s) => s.key)).toEqual(['personal', 'address', 'direct_deposit', 'custom'])
    expect(out[0].fields.map((f) => f.key)).toEqual(['first_name', 'middle'])
  })

  it('a field with no declared section still gets a home rather than vanishing', () => {
    const out = sectionsOf([{ key: 'x', label: 'X', required: false, sensitive: false }])
    expect(out).toHaveLength(1)
    expect(out[0].fields.map((f) => f.key)).toEqual(['x'])
  })

  it('nothing declared is no form at all — never a house form', () => {
    for (const junk of [[], null, undefined, 'nope', {}]) {
      expect(sectionsOf(junk as never)).toEqual([])
    }
  })

  it('invents no label: a field with no label is shown by its key, not by a guessed name', () => {
    const out = sectionsOf([{ key: 'emergency_phone', required: false, sensitive: false }])
    expect(out[0].fields[0].label).toBe('emergency_phone')
  })
})

describe('missingRequired — and the third state a sensitive field has', () => {
  it('names a required field the rep has not answered', () => {
    const r = missingRequired(FIELDS, { first_name: 'Ana' })
    expect(r.missing).toEqual(['street'])
  })

  it('a SENSITIVE required field is never called missing, because the server does not echo it', () => {
    const r = missingRequired(FIELDS, { first_name: 'Ana', street: '1 Main' })
    expect(r.missing).toEqual([])
    expect(r.cannotTell).toEqual(['routing'])
  })

  it('a sensitive field the rep is typing right now counts as answered', () => {
    const r = missingRequired(FIELDS, { first_name: 'Ana', street: '1 Main', routing: '0210' })
    expect(r.cannotTell).toEqual([])
    expect(r.missing).toEqual([])
  })

  it('an OPTIONAL field left blank is never reported either way', () => {
    const r = missingRequired(FIELDS, { first_name: 'Ana', street: '1 Main', routing: '1' })
    expect(r.missing).not.toContain('middle')
    expect(r.cannotTell).not.toContain('middle')
  })

  it('whitespace is not an answer', () => {
    expect(missingRequired(FIELDS, { first_name: '   ', street: '1 Main' }).missing)
      .toEqual(['first_name'])
  })

  it('a zero IS an answer — a declared number of 0 is a figure, not a blank', () => {
    const f = [{ key: 'deps', label: 'Dependents', field_type: 'number', required: true, sensitive: false }]
    expect(missingRequired(f, { deps: '0' }).missing).toEqual([])
  })

  it('no declaration means nothing is required — never a house list of required fields', () => {
    expect(missingRequired(null as never, {})).toEqual({ missing: [], cannotTell: [] })
  })
})

describe('valueFor — what the screen may show in a box', () => {
  it('shows a plain stored value', () => {
    expect(valueFor(FIELDS[0], { first_name: 'Ana' })).toBe('Ana')
  })

  it('shows NOTHING for a sensitive field, even if a value somehow arrived', () => {
    // Defence in depth: the server already withholds it. If a future payload leaked one, the screen
    // still must not render it back — a value on screen is a value in a screenshot.
    expect(valueFor(FIELDS[3], { routing: '021000021' })).toBe('')
  })
})

// ── the checklist ────────────────────────────────────────────────────────────────────────────────
const CATS = [
  {
    key: 'tax',
    label: 'Tax forms',
    tasks: [
      { id: 't1', label: 'W-4', status: 'pending', requires_upload: true, is_mandatory: true },
      { id: 't2', label: 'State form', status: 'submitted', requires_upload: true, is_mandatory: true },
      { id: 't3', label: 'Handbook', status: 'verified', is_mandatory: false },
      { id: 't4', label: 'Returned one', status: 'returned', returned_reason: 'Page 2 blank', is_mandatory: true },
    ],
  },
]

describe('visibleTasks / taskSummary', () => {
  it('flattens the declared categories without reordering them', () => {
    expect(visibleTasks(CATS).map((t) => t.id)).toEqual(['t1', 't2', 't3', 't4'])
  })

  it('counts what is actually left for the REP to do, not what HR has yet to verify', () => {
    // `submitted` is the rep's part done and HR's part pending. Counting it as outstanding would tell
    // a rep to do something they already did.
    const s = taskSummary(CATS)
    expect(s.total).toBe(4)
    expect(s.needsMe).toBe(2)            // t1 pending + t4 returned for corrections
    expect(s.waitingOnHr).toBe(1)        // t2 submitted
    expect(s.done).toBe(1)               // t3 verified
  })

  it('a status this version has never heard of is NOT silently counted as done', () => {
    const s = taskSummary([{ key: 'k', label: 'K', tasks: [{ id: 'x', label: 'X', status: 'quantum' }] }])
    expect(s.done).toBe(0)
    expect(s.unknown).toEqual(['quantum'])
  })

  it('an empty or absent checklist is an empty summary, never a raise', () => {
    expect(taskSummary(null as never)).toEqual(
      { total: 0, needsMe: 0, waitingOnHr: 0, done: 0, unknown: [] })
    expect(visibleTasks(undefined as never)).toEqual([])
  })
})

describe('canDeleteDocument — the SERVER`s rule, never re-derived', () => {
  it('obeys the flag the server computed', () => {
    expect(canDeleteDocument({ id: 'f1', employee_can_delete: true })).toBe(true)
    expect(canDeleteDocument({ id: 'f1', employee_can_delete: false })).toBe(false)
  })

  it('an ABSENT flag means NO — an allowlist, not a guess', () => {
    // §66 posture: a permission this version did not receive is not a permission.
    for (const junk of [{}, { id: 'f' }, null, undefined, { employee_can_delete: 'yes' }]) {
      expect(canDeleteDocument(junk as never)).toBe(false)
    }
  })

  it('does not look at the task status to work the rule out for itself', () => {
    const src = canDeleteDocument.toString()
    for (const w of ['pending', 'status', 'uploaded_role', 'verified']) {
      expect(src).not.toContain(w)
    }
  })
})
