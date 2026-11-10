// THE SCREEN HALF of the tiled Daily Action Plan (owner ask 2026-11-10, index §COMMCALC-57).
//
// The fold is proved away from the DOM (`src/lib/__tests__/action-plan-tiles.test.ts`). This file
// proves the SCREEN: that the tile row renders off the payload the endpoint really serves, that the
// KPI / Stores / Sales reps toggle switches what the tiles are about, that the detail starts
// COLLAPSED and opens per store, and that a metric nothing fed says so instead of showing a zero.
//
// MULTI-TENANT (project rule 2026-10-30): every case runs over TWO tenants whose declared metrics
// are different words, so a house default behind the picker would surface as the wrong tenant's
// labels on screen rather than as a number nobody can argue about.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'

const apiMock = vi.hoisted(() => vi.fn())
vi.mock('@/lib/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/client')>()
  return { ...actual, api: apiMock, ORG_ID: 'org-under-test', localToday: () => '2026-10-20' }
})
vi.mock('@/lib/cache', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/cache')>()
  return { ...actual, apiCached: () => Promise.resolve(null) }
})

import ActionPlanPage from '../page'

type Tenant = 'A' | 'B'

/** Tenant A's words: a count category and a percentage KPI, both FED. */
const PLAN_A = {
  period: 'October 2026', today: '2026-10-20',
  summary: { critical: 1, warning: 0, stores: 1, commission_at_risk: 1000 },
  team_total: { categories: { boxes: { monthly: 100, achieved_mtd: 40, need: 60 } } },
  drilldown: {},
  stores: [{
    store_code: 'B-1598', address: '104-08 Lefferts Blvd', market: 'Queens',
    conversion: { boxes: 40, billpays: 200, rate: 20, target: 25, meets_target: false },
    metrics: [{ cat: 'boxes', label: 'Boxes', unit: 'count', target: 100, achieved: 40, need: 60, pace: 6, today_target: 4 }],
    items: [{ severity: 'critical', metric: 'boxes', title: 'Behind on Boxes', detail: '60 to go.' }],
    reps: [{
      rep: 'Khan, Abid', below_store: true,
      conversion: { boxes: 10, billpays: 90, rate: 11, target: 25, meets_target: false },
      items: [{ severity: 'warning', metric: 'conversion', title: 'Below store conversion', detail: '11% vs 20%.' }],
      commission: {
        tier: 0.75, kpis_met: 0, total_kpis: 1, subtotal: 4000, total_payout: 3000, at_risk: 1000,
        short_kpis: ['Family Plan'], t100: 1, t75: 1, kpis_no_data: [],
        kpis: [{ kpi: 'familyplan', label: 'Family Plan', target: 45, actual: 20, met: false }],
      },
    }],
    commission_at_risk: 1000, counts: { critical: 1, warning: 1 },
  }],
}

/** Tenant B's OWN words, and a KPI nothing fed — the live Elevate Go shape. */
const PLAN_B = {
  period: 'October 2026', today: '2026-10-20',
  summary: { critical: 1, warning: 0, stores: 1, commission_at_risk: 250 },
  team_total: { categories: { handsets: { monthly: 60, achieved_mtd: 10, need: 50 } } },
  drilldown: {},
  stores: [{
    store_code: 'L-900', address: '9 Harbor Rd', market: 'Nassau',
    conversion: { boxes: 15, billpays: 50, rate: 30, target: 25, meets_target: true },
    metrics: [{ cat: 'handsets', label: 'Handsets', unit: 'count', target: 60, achieved: 10, need: 50, pace: 5, today_target: 2 }],
    items: [{ severity: 'critical', metric: 'handsets', title: 'Behind on Handsets', detail: '50 to go.' }],
    reps: [{
      rep: 'Novak, Imre', below_store: false,
      conversion: { boxes: 15, billpays: 50, rate: 30, target: 25, meets_target: true },
      items: [],
      commission: {
        tier: 0.75, kpis_met: 0, total_kpis: 1, subtotal: 1000, total_payout: 750, at_risk: 250,
        short_kpis: [], t100: 1, t75: 1,
        kpis: [],
        kpis_no_data: [{ kpi: 'readyapp', label: 'Ready App', target: 65 }],
      },
    }],
    commission_at_risk: 250, counts: { critical: 1, warning: 0 },
  }],
}
const PLAN: Record<Tenant, unknown> = { A: PLAN_A, B: PLAN_B }

function mount(t: Tenant) {
  apiMock.mockImplementation((url: string) =>
    url.includes('/action-plan') ? Promise.resolve(PLAN[t]) : Promise.resolve(null))
  return render(<ActionPlanPage />)
}

beforeEach(() => { apiMock.mockReset() })
afterEach(() => cleanup())

describe('the tile row renders each tenant\'s own declared metrics', () => {
  it('tenant A sees A\'s words and the count of who is behind', async () => {
    mount('A')
    await waitFor(() => expect(screen.getByText('Boxes')).toBeTruthy())
    expect(screen.getByText('Family Plan')).toBeTruthy()
    // the headline is the CARDINALITY of the list the tile prints, not a re-summed target
    expect(screen.getAllByText(/of 1 behind/).length).toBeGreaterThan(0)
    expect(screen.queryByText('Handsets')).toBeNull()
  })

  it('tenant B sees B\'s words — no other tenant\'s label leaks in', async () => {
    mount('B')
    await waitFor(() => expect(screen.getByText('Handsets')).toBeTruthy())
    expect(screen.queryByText('Boxes')).toBeNull()
    expect(screen.queryByText('Family Plan')).toBeNull()
  })

  it('A KPI NOTHING FED SAYS SO — never a zero that reads as a clean sheet', async () => {
    mount('B')
    await waitFor(() => expect(screen.getByText('Ready App')).toBeTruthy())
    expect(screen.getAllByText('not measured').length).toBeGreaterThan(0)
    expect(screen.getByText(/no recorded value for Ready App/i)).toBeTruthy()
  })
})

describe('the toggle switches what the tiles are about', () => {
  it('Stores shows one tile per store, Sales reps one tile per rep', async () => {
    mount('A')
    await waitFor(() => expect(screen.getByText('Boxes')).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: 'Stores' }))
    await waitFor(() => expect(screen.getAllByText('104-08 Lefferts Blvd').length).toBeGreaterThan(0))
    expect(screen.getByText(/Behind on \d+ of \d+ focused metric/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Sales reps' }))
    await waitFor(() => expect(screen.getAllByText('Khan, Abid').length).toBeGreaterThan(0))
    // the rep tile carries the at-risk money the SERVER computed (the summary strip prints the
    // same figure, which is the point: the tile re-folds nothing)
    expect(screen.getAllByText('$1,000').length).toBeGreaterThan(1)
  })

  it('the pressed view is the only pressed one', async () => {
    mount('A')
    await waitFor(() => expect(screen.getByText('Boxes')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Sales reps' }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Sales reps' }).getAttribute('aria-pressed')).toBe('true'))
    expect(screen.getByRole('button', { name: 'KPI' }).getAttribute('aria-pressed')).toBe('false')
  })
})

describe('the detail is collapsed per store', () => {
  it('starts closed and opens on a click', async () => {
    mount('A')
    await waitFor(() => expect(screen.getByText('Boxes')).toBeTruthy())
    // the store header is named by its code, which nothing else on the KPI view prints
    const header = screen.getByRole('button', { name: /B-1598/ })
    expect(header.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByText('Behind on Boxes')).toBeNull()
    fireEvent.click(header)
    await waitFor(() => expect(screen.getByText('Behind on Boxes')).toBeTruthy())
    // the rep's own block comes with it
    expect(screen.getByText(/Below store conversion/)).toBeTruthy()
  })

  it('an unmeasured KPI is marked in the detail too, beside the scored ones', async () => {
    mount('B')
    await waitFor(() => expect(screen.getByText('Handsets')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /L-900/ }))
    await waitFor(() => expect(screen.getByText(/Ready App not measured/)).toBeTruthy())
  })
})

describe('the standard filters and the send bar are on the screen', () => {
  it('renders the month, the store/market cascade, the people picker and Send', async () => {
    mount('A')
    await waitFor(() => expect(screen.getByText('Boxes')).toBeTruthy())
    expect(screen.getByLabelText('As of date')).toBeTruthy()
    expect(screen.getByLabelText('Focus metrics')).toBeTruthy()
    expect(screen.getByText(/Send/)).toBeTruthy()
  })
})
