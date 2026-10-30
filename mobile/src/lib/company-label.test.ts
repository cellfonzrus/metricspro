// What a human PICKS a company by, on the phone — the client half of index §POS-02.
//
// THE DEFECT (owner, 2026-10-30, with a screenshot): the "Choose a company" screen listed four raw
// UUIDs. `tenantName()` fell through `name → org_name → org_id`, so the moment the payload did not
// carry a usable name — an older server, a company with none, a bundle older than the payload —
// the screen printed the id. An id is not a label: it is the one string on that row that tells a
// person nothing about which business they are about to sign into.
//
// The server now guarantees a label (backend/app/modules/core/tenant_identity.picker_label_from).
// These tests pin the CLIENT's own promise, which has to hold even against a server that does not:
// the app never renders a UUID as a company name, whatever shape the payload arrives in. `api/core`
// re-exports this function as `tenantName`, which is the name the two screens call it by.
import { describe, expect, it } from 'vitest'

import { GENERIC_COMPANY_LABEL, companyLabel as tenantName, looksLikeOrgId } from './company-label'

const HOUSE = '00000000-0000-0000-0000-000000000001'
const OTHER = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'

describe('looksLikeOrgId', () => {
  it('knows the shape of an org id, in either case', () => {
    expect(looksLikeOrgId(HOUSE)).toBe(true)
    expect(looksLikeOrgId(OTHER.toUpperCase())).toBe(true)
  })
  it('does not mistake a company name for one', () => {
    for (const name of ['Vzone', 'NY LOGISTICS', 'Cellfonz R Us', '', null, undefined])
      expect(looksLikeOrgId(name)).toBe(false)
  })
})

describe('tenantName', () => {
  it('shows the name the server sent', () => {
    expect(tenantName({ org_id: HOUSE, name: 'Cellfonz R Us' })).toBe('Cellfonz R Us')
  })

  it('tolerates the older org_name spelling', () => {
    expect(tenantName({ org_id: OTHER, org_name: 'Luxelink Wireless LLC' })).toBe('Luxelink Wireless LLC')
  })

  it('trims a name to one line, exactly as the server does', () => {
    expect(tenantName({ org_id: OTHER, name: '  NY   LOGISTICS \n' })).toBe('NY LOGISTICS')
  })

  it('falls back to the slug read back as words before anything generic', () => {
    expect(tenantName({ org_id: OTHER, slug: 'ny-logistics' })).toBe('Ny Logistics')
  })

  // THE REGRESSION. Four of these were on the screen the owner photographed.
  it.each([
    ['no name at all', { org_id: HOUSE }],
    ['an empty name', { org_id: HOUSE, name: '' }],
    ['a null name and no slug', { org_id: HOUSE, name: null, slug: null }],
    ['a name that IS the id', { org_id: HOUSE, name: HOUSE }],
    ['an org_name that is the id', { org_id: OTHER, org_name: OTHER }],
    ['a slug that is the id', { org_id: OTHER, slug: OTHER }],
  ])('never renders the org id as the company label — %s', (_why, membership) => {
    const label = tenantName(membership)
    expect(label).toBeTruthy()
    expect(looksLikeOrgId(label)).toBe(false)
    expect(label).not.toContain(membership.org_id)
  })

  it('still gives a pickable button when there is nothing to go on', () => {
    expect(tenantName({ org_id: HOUSE })).toBe(GENERIC_COMPANY_LABEL)
  })
})
