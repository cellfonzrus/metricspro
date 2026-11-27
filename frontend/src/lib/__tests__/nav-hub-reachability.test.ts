// A tileOnly ROW IS SKIPPED ONLY WHILE ITS HUB IS THERE (§CORE-06.5, owner report 2026-11-27).
//
// Owner, in the NY LOGISTICS tenant: *"i cannot see the targtet setting dashboard and the incentives
// dashboard in ny logistcis - why is that and fix it"*. Nothing was switched off for that tenant. Its
// business type (the `ups_store` vertical, core/verticals.py) hides `/hub/incentives`,
// `/hub/incentive-payout-plans` and `/hub/targets-coaching` — and EVERY other item in those groups is
// `tileOnly`, i.e. "a master tile on this group's hub covers this page, so the sidebar skips the row".
// With the hub gone nothing covers them, so the sidebar dropped each group whole
// (`loose.length === 0 && shownSubs.length === 0`) and 20 pages the hide-list deliberately left VISIBLE
// had no door at all — "Daily Incentive Rules", the mig-1072 mechanism built FOR that vertical, among
// them. `tileOnly` is a PRECONDITION, not a property.
//
// The rule proven here reveals nothing a gate hid: it only stops SKIPPING rows the gate already let
// through. The cross-vertical sweep over the real nav_hidden lists is backend/harness_nav_reachability.py.
import { describe, it, expect } from 'vitest'
import { tileOnlySkipped, groupHubItem, slugGroup } from '@/lib/tile-hubs'
// TENANT_NAV, not bare NAV: the platform-only groups are the super admin's own (index §38) and a
// consumer that walks NAV without asking platformOK fails harness_super_admin_toolbox §E1 — rightly,
// since it is the same item objects. The cross-group sweep that DOES cover every group, toolbox
// included, is backend/harness_nav_reachability.py §B, which parses rbac.ts rather than rendering it.
import { TENANT_NAV as NAV, type NavItem } from '@/lib/rbac'

const item = (href: string, tileOnly = true): NavItem =>
  ({ href, label: href, icon: '·', module: 'commissions', ...(tileOnly ? { tileOnly: true } : {}) })

const GROUP = 'Incentive Payout Plans'
const HUB = '/hub/incentive-payout-plans'

describe("groupHubItem — a group's hub is READ off the group, never derived from its title", () => {
  it('finds the one hub entry', () => {
    expect(groupHubItem([item('/hub/incentives', false), item('/x')])?.href).toBe('/hub/incentives')
    expect(groupHubItem([item('/storeops', false), item('/x')])).toBeUndefined()
  })
  it('every converted group declares exactly ONE hub, and it is the slug /hub/[group] resolves', () => {
    const converted = NAV.filter(g => g.items.some(i => i.href.startsWith('/hub/')))
    expect(converted.length).toBeGreaterThan(15)
    for (const g of converted) {
      const hubs = g.items.filter(i => i.href.startsWith('/hub/'))
      expect(hubs.length).toBe(1)
      // The predicate reads the item; this pins that reading it and deriving it agree, so the sidebar and
      // the /hub/[group] route can never disagree about which page is a group's dashboard.
      expect(hubs[0].href).toBe('/hub/' + slugGroup(g.group))
      expect(groupHubItem(g.items)?.href).toBe(hubs[0].href)
    }
  })
  it('a group with a hand-woven front door declares no hub, and is left alone', () => {
    for (const name of ['Store Operations', 'Workforce', 'Payroll & HR', 'Flags & Compliance']) {
      const g = NAV.find(x => x.group === name)!
      expect(groupHubItem(g.items)).toBeUndefined()
    }
  })
})

describe('tileOnlySkipped', () => {
  const declared = [item(HUB, false), item('/commcalc/incentive-rules'), item('/commcalc/incentive-plan')]

  it('skips a tileOnly row while the hub is visible (today behaviour, unchanged)', () => {
    const visible = declared
    expect(tileOnlySkipped(item('/commcalc/incentive-rules'), GROUP, declared, visible, false)).toBe(true)
  })

  it('SHOWS a tileOnly row when the hub is gated away — the defect', () => {
    const visible = [item('/commcalc/incentive-rules'), item('/commcalc/incentive-plan')]
    expect(tileOnlySkipped(item('/commcalc/incentive-rules'), GROUP, declared, visible, false)).toBe(false)
  })

  it('never skips a non-tileOnly row', () => {
    expect(tileOnlySkipped(item(HUB, false), GROUP, declared, declared, false)).toBe(false)
  })

  it('never skips inside a Reports directory category', () => {
    expect(tileOnlySkipped(item('/commcalc/incentive-rules'), 'Reports · Commissions', declared, declared, true))
      .toBe(false)
  })

  it('leaves a group that never had a generic hub exactly as it was (hand-woven hubs)', () => {
    const hand = [item('/storeops', false), item('/storeops/schedule')]
    expect(tileOnlySkipped(item('/storeops/schedule'), 'Workforce', hand, hand, false)).toBe(true)
    // …and still skips when the hand-woven front door itself is gated away: that group renders no
    // header at all, which is the pre-existing behaviour this change deliberately does not alter.
    expect(tileOnlySkipped(item('/storeops/schedule'), 'Workforce', hand, [item('/storeops/schedule')], false))
      .toBe(true)
  })

  it('reveals nothing: a row the gate removed is never asked about', () => {
    // The caller only ever asks about rows already in `visible`; the predicate is a RENDER skip, not a gate.
    const visible = [item('/commcalc/incentive-rules')]
    expect(tileOnlySkipped(item('/commcalc/incentive-rules'), GROUP, declared, visible, false)).toBe(false)
  })
})

describe('the owner report, over the REAL nav', () => {
  it('Daily Incentive Rules gets a sidebar row once its hub is hidden', () => {
    const g = NAV.find(x => x.group === GROUP)!
    const rules = g.items.find(i => i.href === '/commcalc/incentive-rules')!
    expect(rules.tileOnly).toBe(true)
    const visible = g.items.filter(i => i.href !== HUB)
    expect(tileOnlySkipped(rules, g.group, g.items, visible, false)).toBe(false)
  })
  it('Target Settings is a page under /commcalc/targets, so an exact hide must not sweep it', () => {
    const g = NAV.find(x => x.group === 'Targets & Coaching')!
    expect(g.items.some(i => i.href === '/commcalc/targets/settings')).toBe(true)
  })
})
