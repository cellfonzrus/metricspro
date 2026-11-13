// THE PEER SALES COMPARISON PAGE, rendered in jsdom (owner ask 2026-11-12, index §59.10).
//
// WHAT THIS FILE IS FOR, and it is one thing above all others: the market / store pick must reach the
// server as `focus_markets` / `focus_stores` and NEVER as `markets`. `markets` is the NARROWING
// parameter, and narrowing this report to one market makes that market its own band median — the
// screen would then tell every district manager he is average, which is the one comparison this
// report must not show. That is a single-word mistake with no visible symptom (the page still
// renders, the tiles still fill, the numbers are just wrong and flattering), so it is pinned here.
//
// The arithmetic is NOT re-proved here — it is the server's, proved in `tests/unit/test_peer_focus.py`
// and `harness_peer_comparison.py` §L. What is proved here is the wiring: which parameters go out,
// that the tiles print the figures that come back, and that a comparison the server could not run is
// shown as its stated reason rather than as a zero.
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

const calls: string[] = []
const payload = (focus: unknown) => ({
  period: '2026-11',
  band_cuts: [150, 250, 400],
  band_basis: 'Distinct bill-payment transactions.',
  box_parts: [{ key: 'new', label: 'New activation' }],
  gap_metrics: [
    { key: 'boxes_per_billpay', label: 'Boxes per bill payment', higher_is_better: true },
    { key: 'boxes', label: 'Boxes', higher_is_better: true },
  ],
  default_gap_metric: 'boxes_per_billpay',
  bands: [{
    band: 0, label: 'Under 150 bill payments', stores: 4, comparable: true,
    billpay_low: 100, billpay_high: 100, boxes_best: 40, boxes_median: 25, leader: 'S-3', note: null,
  }],
  rows: [{
    store: 'S-1', market: 'NJ', billpay_txns: 100, boxes: 10, parts: { new: 10 },
    boxes_per_billpay: 0.1, aal: 0, accessory_revenue: 0, accessory_per_box: 0,
    family_plan_pct: null, band: 0, band_label: 'Under 150 bill payments', peers: 3, gaps: {},
  }],
  unbanded: [],
  lagging: [],
  caveats: [],
  markets: ['NJ', 'NY'],
  stores: ['S-1', 'S-2', 'S-3', 'S-4'],
  store_options: [
    { store: 'S-1', market: 'NJ' }, { store: 'S-2', market: 'NJ' },
    { store: 'S-3', market: 'NY' }, { store: 'S-4', market: 'NY' },
  ],
  drilldown: {},
  focus,
})

const BEHIND = {
  subject: { kind: 'market', label: 'NJ', stores: ['S-1', 'S-2'], markets: ['NJ'], unknown: [] },
  metric: 'boxes_per_billpay', metric_label: 'Boxes per bill payment', higher_is_better: true,
  measurable: true, reason: null,
  stores_in_view: 2, peer_stores: 2, not_banded: [], not_banded_reason: null,
  gap_median: 0.2, verdict: 'behind', behind: [], behind_count: 2,
  bands: [{
    band: 0, label: 'Under 150 bill payments', subject_stores: 2, peer_stores: 2,
    subject_median: 0.15, peer_median: 0.35, peer_best: 0.4, peer_leader: 'S-3',
    gap_to_peer_median: 0.2, gap_to_peer_best: 0.25, comparable: true, verdict: 'behind', note: null,
  }],
  peer_basis: 'Each selected store is measured against the OTHER stores in its own traffic band.',
  composite_basis: 'The headline gap is the median of the selected stores own gaps.',
  sentence: 'NJ is 0.20 boxes per bill payment behind the stores it shares a traffic band with.',
  accountable: { dms: [{ name: 'Dana Reyes', stores: ['S-1', 'S-2'] }], no_dm: [], stores: 2, error: null, note: null },
}

const NO_PEER = {
  ...BEHIND,
  subject: { kind: 'market', label: 'NJ + NY', stores: ['S-1', 'S-2', 'S-3', 'S-4'], markets: ['NJ', 'NY'], unknown: [] },
  measurable: false,
  reason: 'Every store in this traffic band belongs to the selection, so there is no competing store left to measure it against.',
  gap_median: null, verdict: 'not_measured', behind_count: 0, peer_stores: 0,
  bands: [{
    band: 0, label: 'Under 150 bill payments', subject_stores: 4, peer_stores: 0,
    subject_median: 0.25, peer_median: null, peer_best: null, peer_leader: null,
    gap_to_peer_median: null, gap_to_peer_best: null, comparable: false, verdict: 'not_measured',
    note: 'Every store in this traffic band belongs to the selection.',
  }],
  sentence: '',
  accountable: { dms: [], no_dm: ['S-1'], stores: 4, error: null,
    note: '1 of 4 stores have no district manager in the org tree. Assigning them in the org chart is what fixes it.' },
}

let FOCUS: unknown = BEHIND

vi.mock('@/lib/client', () => ({
  api: (url: string) => { calls.push(url); return Promise.resolve(payload(FOCUS)) },
  fmt: (n: number) => `$${Number(n || 0).toFixed(2)}`,
  getActiveOrg: () => '',
}))
// The export/send toolbar reaches the network on mount in jsdom; the page's own wiring is the subject
// here, so the shell is stubbed down to what this test reads. ReportShell IS still asserted to be the
// thing the page renders its table through (that is what carries the Send bar, §CORE-27) — by the
// build gate `harness_report_toolbar_coverage.py`, which reads the page source.
vi.mock('@/components/ReportShell', () => ({
  default: ({ subtitle }: { subtitle?: string }) => <div data-testid="report-shell">{subtitle}</div>,
}))

import PeerComparisonPage from '../page'

describe('Peer Sales Comparison — the market / store subject', () => {
  beforeEach(() => { calls.length = 0; FOCUS = BEHIND })

  it('sends the pick as the SUBJECT and never as the narrowing `markets` parameter', async () => {
    render(<PeerComparisonPage />)
    await waitFor(() => expect(calls.length).toBeGreaterThan(0))
    const url = calls[calls.length - 1]
    expect(url).toContain('/api/v1/commcalc/peer-comparison')
    // nothing picked yet: no subject, and above all no narrowing
    expect(url).not.toContain('markets=')
    expect(url).not.toContain('focus_markets=')
  })

  it('prints the server’s own gap, sentence and peer median — it computes none of them', async () => {
    render(<PeerComparisonPage />)
    await screen.findByText(/NJ vs its traffic-band peers/i)
    // the gap is shown as a shortfall, with the server's sentence under it
    expect(screen.getByText('−0.20')).toBeTruthy()
    expect(screen.getByText(BEHIND.sentence)).toBeTruthy()
    // the per-band row carries the subject's median AND the peers' — two different figures
    expect(screen.getByText('0.15')).toBeTruthy()
    expect(screen.getByText('0.35')).toBeTruthy()
    expect(screen.getByText(/S-3 at 0.40/)).toBeTruthy()
  })

  it('names who answers for the selection', async () => {
    render(<PeerComparisonPage />)
    expect(await screen.findByText('Dana Reyes')).toBeTruthy()
  })

  it('says the table is NOT narrowed, so nobody reads the tiles as a filtered table', async () => {
    render(<PeerComparisonPage />)
    const shell = await screen.findByTestId('report-shell')
    expect(shell.textContent).toContain('the table is not narrowed')
    expect(shell.textContent).toContain('comparing NJ against its band peers')
  })

  it('shows a comparison that could not run as its reason, never as a zero gap', async () => {
    FOCUS = NO_PEER
    render(<PeerComparisonPage />)
    await screen.findByText(/This selection cannot be compared/i)
    // the reason is shown twice on purpose — once as the banner, once on the band row that caused it
    expect(screen.getAllByText(new RegExp(NO_PEER.reason.slice(0, 40))).length).toBeGreaterThan(0)
    // the headline figure is an em dash, and no 0.00 is printed in its place
    expect(screen.queryByText('0.00')).toBeNull()
    // and the org-tree gap is stated rather than left blank
    expect(screen.getByText(new RegExp('no district manager in the org tree'))).toBeTruthy()
  })
})
