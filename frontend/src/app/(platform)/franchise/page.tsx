'use client'
// Store Operations Dashboard (module `franchise_ops`, index §35) — one screen for a franchise store's day:
// is today's closing in, how much cash is sitting in the stores, what supply orders are open and where the
// savings are, and where the month's royalty report stands.
//
// IT COMPUTES NOTHING. Every number is read from the endpoint that already owns it (duplicate-check build
// gate): cash on hand = GET /closing/store-cash-on-hand (the same _cash_position_core the Cash Position
// report uses), closing wiring = GET /closing/readiness, supply = GET /supply/summary (§36), royalty =
// GET /account/royalty/summary (§37). A source that is not set up (or not deployed yet) says so on its own
// tile instead of showing a zero. Which tenants see this page is DATA: the module's applies_to_vertical
// (mig 1020) — nothing here names a kind of business.
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'

type Load<T> = { state: 'loading' } | { state: 'ok'; data: T } | { state: 'off'; why: string }
type Issue = { severity: string; message: string }
type CashResp = { totals?: { total_cash_on_hand?: number; today_declared?: number; stores?: number }; error?: string }
type ReadyResp = { issues?: Issue[]; error?: string }
type SupplyResp = { open_orders?: number; spend_mtd?: number; savings_mtd?: number; migrated?: boolean; note?: string; error?: string }
type RoyaltyResp = { has_data?: boolean; period?: string; str?: number; fees_due?: number; centers?: number; error?: string }

const panel: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: 16,
}
const money = (n: unknown) =>
  typeof n === 'number' && isFinite(n) ? n.toLocaleString(undefined, { style: 'currency', currency: 'USD' }) : '—'

function useLoad<T>(path: string): Load<T> {
  const [v, setV] = useState<Load<T>>({ state: 'loading' })
  useEffect(() => {
    let alive = true
    api(path)
      .then((d: (T & { error?: string }) | null) => { if (alive) setV(d && !d.error ? { state: 'ok', data: d as T } : { state: 'off', why: d?.error || 'not available' }) })
      .catch((e: { status?: number }) => {
        const why = e?.status === 404 ? 'not set up yet'
          : e?.status === 403 ? 'not enabled for this company'
          : 'could not load'
        if (alive) setV({ state: 'off', why })
      })
    return () => { alive = false }
  }, [path])
  return v
}

function Tile({ title, href, children, load }: { title: string; href?: string; children?: React.ReactNode; load: Load<unknown> }) {
  return (
    <div style={{ ...panel, flex: '1 1 240px', minWidth: 240 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
        <div style={{ fontSize: 12, textTransform: 'uppercase', letterSpacing: '.4px', color: 'var(--text2)' }}>{title}</div>
        {href && <Link href={href} style={{ fontSize: 12 }}>Open →</Link>}
      </div>
      {load.state === 'loading' && <div style={{ color: 'var(--text2)' }}>Loading…</div>}
      {load.state === 'off' && <div style={{ color: 'var(--text2)', fontSize: 13 }}>{load.why}</div>}
      {load.state === 'ok' && children}
    </div>
  )
}

function Big({ value, sub, color }: { value: string | number; sub?: string; color?: string }) {
  return (
    <div>
      <div style={{ fontSize: 26, fontWeight: 700, color: color || 'var(--text)' }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

const LINKS: { group: string; items: [string, string][] }[] = [
  { group: 'Every day', items: [['/closing/submit', 'Submit closing'], ['/closing/verify', 'Verify closings'],
    ['/closing/pickup', 'Cash pickup'], ['/closing/store-cash-on-hand', 'Cash on hand']] },
  { group: 'Reconcile', items: [['/closing/deposit-recon', 'Cash deposit recon'],
    ['/closing/external-credit-recon', 'Card settlement recon'], ['/closing/tender-recon-3way', '3-way tender recon'],
    ['/closing/imports', 'Auto-import']] },
  { group: 'Supplies', items: [['/supply/compare', 'Compare prices'], ['/supply/cart', 'Build an order'],
    ['/supply/orders', 'Orders & confirmations'], ['/supply/vendors', 'Vendors']] },
  { group: 'Finance', items: [['/accounts/royalty', 'Royalty report'], ['/accounts/royalty/recon', 'Royalty vs daily sales'],
    ['/accounts/pl', 'P&L'], ['/accounts/profit-centers', 'Profit centers'], ['/accounts/cost-centers', 'Cost centers'],
    ['/commcalc/expenses', 'Store expenses']] },
  { group: 'People', items: [['/storeops/schedule', 'Schedule'], ['/storeops/payroll', 'Payroll'],
    ['/storeops/timeclock', 'Time clock'], ['/storeops/employees', 'Employees']] },
]

export default function StoreOperationsDashboard() {
  const { tenant } = useAuth()
  const cash = useLoad<CashResp>('/api/v1/closing/store-cash-on-hand')
  const ready = useLoad<ReadyResp>('/api/v1/closing/readiness')
  const supply = useLoad<SupplyResp>('/api/v1/supply/summary')
  const royalty = useLoad<RoyaltyResp>('/api/v1/account/royalty/summary')

  const issues: Issue[] = ready.state === 'ok' ? (ready.data.issues || []) : []
  const critical = issues.filter(i => i.severity === 'critical')

  return (
    <div style={{ padding: 20, display: 'grid', gap: 16 }}>
      <div>
        <h1 style={{ margin: 0 }}>Store Operations</h1>
        <div style={{ color: 'var(--text2)', fontSize: 13 }}>
          {tenant?.name ? `${tenant.name} · ` : ''}{tenant?.vertical?.label || ''}
        </div>
      </div>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <Tile title="Cash in the stores" href="/closing/store-cash-on-hand" load={cash}>
          {cash.state === 'ok' && (
            <Big value={money(cash.data.totals?.total_cash_on_hand)}
                 sub={`declared today ${money(cash.data.totals?.today_declared)} · ${cash.data.totals?.stores ?? 0} stores`} />
          )}
        </Tile>
        <Tile title="Daily closing set-up" href="/closing" load={ready}>
          {ready.state === 'ok' && (
            <Big value={critical.length ? `${critical.length} to fix` : 'Ready'}
                 color={critical.length ? 'var(--danger, #c0392b)' : 'var(--success, #1e8449)'}
                 sub={critical[0]?.message || `${issues.length} note${issues.length === 1 ? '' : 's'}`} />
          )}
        </Tile>
        <Tile title="Supply orders" href="/supply/orders" load={supply}>
          {supply.state === 'ok' && (supply.data.migrated === false
            ? <div style={{ color: 'var(--text2)', fontSize: 13 }}>{supply.data.note || 'not set up yet'}</div>
            : <Big value={supply.data.open_orders ?? 0}
                   sub={`open · spend this month ${money(supply.data.spend_mtd)} · savings this month ${money(supply.data.savings_mtd)}`} />
          )}
        </Tile>
        <Tile title="Royalty report" href="/accounts/royalty" load={royalty}>
          {royalty.state === 'ok' && (royalty.data.has_data === false
            ? <div style={{ color: 'var(--text2)', fontSize: 13 }}>No royalty report imported yet.</div>
            : <Big value={money(royalty.data.fees_due)}
                   sub={`fees due · ${royalty.data.period || 'latest period'} · subject to royalty ${money(royalty.data.str)}`} />
          )}
        </Tile>
      </div>

      {issues.length > 0 && (
        <div style={panel}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>Closing set-up notes</div>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
            {issues.slice(0, 6).map((i, n) => <li key={n}><b>{i.severity}</b> — {i.message}</li>)}
          </ul>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12 }}>
        {LINKS.map(g => (
          <div key={g.group} style={panel}>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>{g.group}</div>
            <div style={{ display: 'grid', gap: 6 }}>
              {g.items.map(([href, lbl]) => <Link key={href} href={href}>{lbl}</Link>)}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
