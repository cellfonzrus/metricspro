// WHICH PEOPLE MAY A MENU OFFER (owner report 2026-11-25: "once the employee is made inactive it
// should [be] deactivated from all menus — i just deactivated aiman from ny logistics, it was still
// appearing in employee set up") — the pure rules of the one picker home.
//
// The class this proves: a surface that asks the server for inactive people ON PURPOSE (so a past
// month's rows still carry a name) was handing that SAME unfiltered list to a dropdown. Ten screens
// had each written their own option builder; the roster read had a home, the MENU had none.
import { describe, expect, it } from 'vitest'
import { INACTIVE_SUFFIX, rosterOptions } from '@/lib/roster-visibility'

type Emp = { name?: string; email?: string | null; employee_id?: string; is_active?: unknown }

const ROSTER: Emp[] = [
  { name: 'Zoe', email: 'zoe@x.com', employee_id: 'E1' },                     // flag never set -> LIVE
  { name: 'Aiman', email: null, employee_id: 'E275', is_active: false },       // deactivated
  { name: 'Ali', email: 'ali@x.com', employee_id: 'E3', is_active: true },
  { name: 'Wire', email: null, employee_id: 'E4', is_active: 'false' },        // came off the wire
]

describe('rosterOptions', () => {
  it('offers only the people who are still active, by default', () => {
    expect(rosterOptions(ROSTER, false).map(o => o.label)).toEqual(['Ali', 'Zoe'])
  })

  it('offers the deactivated people when the surface asks, and SAYS which they are', () => {
    expect(rosterOptions(ROSTER, true).map(o => o.label))
      .toEqual([`Aiman${INACTIVE_SUFFIX}`, 'Ali', `Wire${INACTIVE_SUFFIX}`, 'Zoe'])
  })

  it('defaults to the roster shape every caller already used: name as id and label, email beneath', () => {
    expect(rosterOptions(ROSTER, false)[0]).toEqual({ id: 'Ali', label: 'Ali', sublabel: 'ali@x.com' })
    // A person with no email carries NO sublabel, rather than an empty second line in the menu.
    expect(rosterOptions(ROSTER, false).find(o => o.id === 'Zoe')?.sublabel).toBe('zoe@x.com')
    expect(rosterOptions([{ name: 'Nia' }], false)[0]).toEqual({ id: 'Nia', label: 'Nia' })
  })

  it('lets a caller key the option by the business id, and still SHOWS the name', () => {
    // A menu of `E275`s is a menu nobody can read, so overriding the id must not change the label.
    expect(rosterOptions(ROSTER, false, { id: e => e.employee_id }))
      .toEqual([{ id: 'E3', label: 'Ali', sublabel: 'ali@x.com' }, { id: 'E1', label: 'Zoe', sublabel: 'zoe@x.com' }])
  })

  it('falls back to the id as the label when a row has no name at all', () => {
    expect(rosterOptions([{ employee_id: 'E9' }], false, { id: e => e.employee_id })[0].label).toBe('E9')
  })

  it('drops a person with no id, because an option nobody can select is noise', () => {
    expect(rosterOptions([{ name: '' }, { name: '   ' }, { name: 'Ali' }], false).map(o => o.id))
      .toEqual(['Ali'])
  })

  it('offers one option per id, so a repeated name cannot select two different people at once', () => {
    const dupes = [{ name: 'Ali', email: 'a@x.com' }, { name: 'Ali', email: 'b@x.com' }]
    expect(rosterOptions(dupes, false)).toHaveLength(1)
  })

  it('sorts by what the user reads, so the menu is not in insertion order', () => {
    expect(rosterOptions([{ name: 'Zoe' }, { name: 'Ali' }], false).map(o => o.label)).toEqual(['Ali', 'Zoe'])
  })

  it('survives a roster that has not loaded yet', () => {
    expect(rosterOptions<Emp>(null, false)).toEqual([])
    expect(rosterOptions<Emp>(undefined, true)).toEqual([])
  })

  it('reads the active flag the one way, not by truthiness — the STRING false is truthy in JS', () => {
    expect(rosterOptions(ROSTER, false).map(o => o.id)).not.toContain('Wire')
  })

  it('takes a caller-named flag for a roster that spells it some other way', () => {
    const people = [{ name: 'Ali', status: 'terminated' }, { name: 'Zoe', status: 'employed' }]
    expect(rosterOptions(people, false, { active: p => p.status !== 'terminated' }).map(o => o.id))
      .toEqual(['Zoe'])
  })
})

describe('the report of 2026-11-25, in the shape it arrived', () => {
  // NY LOGISTICS has exactly ONE deactivated employee, and his row says only `is_active: false`
  // (no termination date, nothing else) — verified against production the day it was reported. The
  // page he appeared on fetches `include_inactive=true`, so the server correctly hands him over and
  // the SCREEN is what must keep him out of the menu.
  const nyRoster = [
    { name: 'Aiman', employee_id: 'E275', home_store: '7710', is_active: false },
    { name: 'Still Here', employee_id: 'E276', home_store: '7710', is_active: true },
  ]

  it('does not offer him once he is deactivated', () => {
    expect(rosterOptions(nyRoster, false).map(o => o.label)).toEqual(['Still Here'])
  })

  it('still offers him to somebody correcting a week he worked, named as inactive', () => {
    expect(rosterOptions(nyRoster, true).map(o => o.label))
      .toEqual([`Aiman${INACTIVE_SUFFIX}`, 'Still Here'])
  })
})
