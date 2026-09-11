'use client'
// DEVICE PURCHASES FROM THE DISTRIBUTOR (owner directive 2026-09-11) — the cost of every device the
// distributor BILLED us in a period window, segregated by COMPANY and by STORE.
//
// PURCHASES, NOT COGS, AND THE PAGE SAYS SO. Owner: "build it as purchases, keep it separate from
// cogs." The banner below names the question this report answers AND names the other report, so
// nobody reconciles the two, finds a gap, and concludes one of them is broken. They are not meant
// to tie: a device bought in December and sold in February is in this report's December and in the
// device-COGS February.
//
// Reads ONLY GET /account/device-purchases (org-scoped). Store and company come from the platform's
// shared resolvers server-side (coa.store_resolver / coa.company_assignment) — the same two the P&L
// books through — so this page can never disagree with the P&L about where a store sits.
//
// NOTHING IS RENDERED AS A SILENT $0.00. A location no store vocabulary matched, and a store with no
// company assignment, each get their own labelled row carrying their own money, below.
import { useEffect, useMemo, useState } from 'react'
import { api, fmt } from '@/lib/client'
import StandardFilterBar from '@/components/StandardFilterBar'
import ReportExportBar from '@/components/ReportExportBar'
import StatTile from '@/components/StatTile'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'
import type { ExportSheet } from '@/lib/export'

const th: React.CSSProperties = { textAlign: 'left', padding: '7px 10px', fontSize: 12, color: 'var(--text2)', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }
const thr: React.CSSProperties = { ...th, textAlign: 'right' }
const td: React.CSSProperties = { padding: '7px 10px', fontSize: 13, borderBottom: '1px solid var(--border)' }
const tdr: React.CSSProperties = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }

// THREE STATES: a measured figure renders; a figure we do not have renders '—'. Never $0.00 for
// "not measured" — that is the house rule this report exists under.
const money = (v: any) => (v == null ? '—' : fmt(v))
const num = (v: any) => (v == null ? '—' : Number(v).toLocaleString())

const STORE_NOT_MAPPED = '(store not mapped)'
const COMPANY_NOT_MAPPED = '(company not mapped)'

// How a location reached one of our stores, judged from the shared resolver's ANSWER.
const RESOLVED_BY: Record<string, string> = {
  exact: 'the distributor writes this store exactly the way we hold it',
  resolver: 'the shared store resolver reached it another way — a store alias, a store code, or (most often on this feed) the unambiguous leading STREET NUMBER, which never compares the street name. Right today; a store-alias row would make it deliberate',
  unmapped: 'nothing in the store vocabulary matched this location at all',
}

function Card({ title, note, children }: { title: string; note?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="card" style={{ padding: 14, marginBottom: 14 }}>
      <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 4 }}>{title}</div>
      {note && <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 8 }}>{note}</div>}
      {children}
    </div>
  )
}

const firstOfYear = () => `${new Date().getFullYear()}-01-01`
const today = () => new Date().toISOString().slice(0, 10)
const toMonth = (d: string) => (d && d.length >= 7 ? d.slice(0, 7) : '')

export default function DevicePurchasesPage() {
  const [filt, setFilt] = useState<StandardFilterValue>(() => ({
    ...emptyStandardFilter(firstOfYear()), periodTo: today(),
  }))
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  const from = toMonth(filt.period || '')
  const to = toMonth(filt.periodTo || '')

  // STALE-RESPONSE GUARD. Changing the From box and then the To box puts two requests in flight,
  // and without this the one that RESOLVES LAST wins — which is routinely the earlier, WIDER window.
  // Live defect 2026-09-11: the owner set 01/01/2025-12/31/2025 and the page rendered $11,243,145.03
  // over 31,110 units, which is 2025 PLUS 2026 to the cent; the correct 2025 figure is $7,099,841.56
  // over 19,235. The date boxes read right while the money was a year too wide — the worst shape of
  // wrong, because nothing on screen contradicted it. Same defect class as the Cash/ePay Pickup
  // filter (fixed 2026-09-10); `alive` is the pattern StandardFilterBar itself already uses.
  useEffect(() => {
    let alive = true
    setLoading(true); setErr('')
    const qs = new URLSearchParams()
    if (from) qs.set('from_period', from)
    if (to) qs.set('to_period', to)
    api(`/api/v1/account/device-purchases?${qs.toString()}`)
      .then(d => { if (alive) setData(d) })
      .catch(e => { if (!alive) return; setErr(e?.message || String(e)); setData(null) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [from, to])

  const distributor = data?.distributor_label || 'distributor'
  const stores: any[] = useMemo(() => data?.by_store || [], [data])

  // Store/market narrowing is client-side over the rows already loaded — what you see is what exports.
  const selStores = useMemo(() => new Set(filt.stores.map(s => s.trim().toUpperCase())), [filt.stores])
  const selMkts = useMemo(() => new Set(filt.markets.map(m => m.trim().toUpperCase())), [filt.markets])
  const keep = (r: any) => {
    if (selStores.size && !selStores.has(String(r.store || '').trim().toUpperCase())) return false
    if (selMkts.size && !selMkts.has(String(r.market || '').trim().toUpperCase())) return false
    return true
  }
  const rows = useMemo(() => stores.filter(keep), [stores, selStores, selMkts])
  const narrowed = selStores.size > 0 || selMkts.size > 0

  // The company roll-up is folded from the SAME filtered store rows, so the two tables can never
  // disagree and a market filter narrows both at once.
  const companies = useMemo(() => {
    const m = new Map<string, any>()
    for (const r of rows) {
      const c = m.get(r.company) || { company: r.company, amount: 0, units: 0, lines: 0, stores: 0 }
      c.amount += r.amount || 0; c.units += r.units || 0; c.lines += r.lines || 0; c.stores += 1
      m.set(r.company, c)
    }
    return [...m.values()].sort((a, b) => b.amount - a.amount)
  }, [rows])

  const deviceTotal = rows.reduce((a, r) => a + (r.amount || 0), 0)
  const unitTotal = rows.reduce((a, r) => a + (r.units || 0), 0)
  const cascade = useMemo(() => [...new Map(stores
    .filter(r => r.store !== STORE_NOT_MAPPED)
    .map(r => [r.store, { id: r.store, label: r.store, market: r.market || '' }])).values()], [stores])

  const unmapped: any[] = data?.unresolved?.store_not_mapped || []
  const noCompany: any[] = (data?.unresolved?.company_not_mapped || []).filter((r: any) => r.store !== STORE_NOT_MAPPED)
  const products: any[] = data?.by_product || []
  const nonDevice: any[] = data?.non_device_lines || []
  const res = data?.meta?.resolution || {}
  const byResolver = res.resolver || null

  function sheets(): ExportSheet[] {
    return [
      { name: 'By Company', rows: companies, columns: [
        { header: 'Company', get: (r: any) => r.company },
        { header: 'Stores', get: (r: any) => r.stores },
        { header: 'Units', get: (r: any) => r.units },
        { header: 'Device purchases', money: true, get: (r: any) => r.amount },
      ] },
      { name: 'By Store', rows, columns: [
        { header: 'Company', get: (r: any) => r.company },
        { header: 'Store', get: (r: any) => r.store },
        { header: 'Market', get: (r: any) => r.market },
        { header: 'Units', get: (r: any) => r.units },
        { header: 'Device purchases', money: true, get: (r: any) => r.amount },
        { header: 'Store matched by', get: (r: any) => RESOLVED_BY[r.resolved_by] || r.resolved_by },
      ] },
      { name: 'By Product', rows: products, columns: [
        { header: 'Product (as the distributor bills it)', get: (r: any) => r.name },
        { header: 'Units', get: (r: any) => r.units },
        { header: 'Purchases', money: true, get: (r: any) => r.amount },
      ] },
      { name: 'Not devices', rows: nonDevice, columns: [
        { header: 'Line', get: (r: any) => r.name },
        { header: 'Lines', get: (r: any) => r.lines },
        { header: 'Amount', money: true, get: (r: any) => r.amount },
      ] },
      { name: 'Unresolved', rows: unmapped, columns: [
        { header: 'Location as the distributor wrote it', get: (r: any) => r.location },
        { header: 'Lines', get: (r: any) => r.lines },
        { header: 'Units', get: (r: any) => r.units },
        { header: 'Amount', money: true, get: (r: any) => r.amount },
      ] },
    ]
  }

  return (
    <div style={{ padding: 20, maxWidth: 1280, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
        <div>
          <h1 style={{ fontSize: 20, fontWeight: 800, margin: 0 }}>Device Purchases — {distributor}</h1>
          <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 3 }}>
            Cost of every phone and device the {distributor} billed us, by company and by store.
            {data?.window && <> Window <strong>{data.window.from}</strong> → <strong>{data.window.to}</strong>.</>}
          </div>
        </div>
        <ReportExportBar
          title={`Device Purchases — ${distributor}`}
          subtitle={data?.window ? `Purchases (billed), ${data.window.from} → ${data.window.to}${narrowed ? ' · filtered' : ''}` : 'Purchases (billed)'}
          filename="device_purchases" sheets={sheets()} />
      </div>

      {/* ── THE ONE LINE THAT KEEPS THIS REPORT FROM BEING MISREAD ───────────────────────────── */}
      <div className="card" style={{ padding: '10px 14px', marginBottom: 14, borderLeft: '3px solid var(--accent, #6b7cff)' }}>
        <div style={{ fontSize: 13 }}>
          <strong>This is PURCHASES, not COGS.</strong> It answers <em>&ldquo;what did the {distributor} bill
          us in this window?&rdquo;</em> — every billed unit, recognised on the invoice date, whether it has
          since sold, is still on the shelf, or never activated. The device <strong>COGS</strong> figure on the
          P&amp;L and Balance Sheet answers a different question — <em>&ldquo;what did the units we SOLD cost
          us?&rdquo;</em> — IMEI-deduped and recognised at sale. <strong>The two are not meant to tie</strong>,
          and a gap between them is not a defect in either. Nothing on this page is booked to the P&amp;L or the
          Balance Sheet.
        </div>
      </div>

      <StandardFilterBar
        value={filt} onChange={setFilt}
        periodMode="range"
        show={{ period: true, stores: true, markets: true, reps: false }}
        cascadeStores={cascade}
        storeLabel="Stores…" marketLabel="Markets…" />

      {loading && <div className="card" style={{ padding: 14 }}>Loading…</div>}
      {err && <div className="card" style={{ padding: 14, color: 'var(--danger, #c33)' }}>Could not load: {err}</div>}

      {data && !loading && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 10, marginBottom: 14 }}>
            <StatTile label="Device purchases" value={money(deviceTotal)} />
            <StatTile label="Units billed" value={num(unitTotal)} />
            <StatTile label="Invoices in window" value={narrowed ? '—' : num(data.totals?.invoices)} />
            <StatTile label="Not devices (reported, excluded)" value={narrowed ? '—' : money(data.totals?.non_device_amount)} />
            <StatTile label="Unresolved location spend" value={narrowed ? '—' : money(unmapped.reduce((a, r) => a + (r.amount || 0), 0))} />
          </div>

          <Card title="By company"
                note={<>Company comes from the store&rsquo;s assignment (<code>store_companies</code>) through the same
                      resolver the P&amp;L books with. A store nobody has assigned is shown as
                      <strong> {COMPANY_NOT_MAPPED}</strong> rather than folded into the default company — printing
                      the booking fallback would state a fact we do not have.</>}>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr><th style={th}>Company</th><th style={thr}>Stores</th><th style={thr}>Units</th><th style={thr}>Device purchases</th></tr></thead>
                <tbody>
                  {companies.map(c => (
                    <tr key={c.company}>
                      <td style={td}>{c.company}</td>
                      <td style={tdr}>{num(c.stores)}</td>
                      <td style={tdr}>{num(c.units)}</td>
                      <td style={tdr}><strong>{money(c.amount)}</strong></td>
                    </tr>
                  ))}
                  {!companies.length && <tr><td style={td} colSpan={4}>No device purchases in this window.</td></tr>}
                </tbody>
              </table>
            </div>
          </Card>

          <Card title="By store"
                note={<>The distributor writes each store as a street address in its own house style; the
                      <em> matched by</em> column says how that address reached one of our stores.</>}>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr><th style={th}>Company</th><th style={th}>Store</th><th style={th}>Market</th><th style={thr}>Units</th><th style={thr}>Device purchases</th><th style={th}>Matched by</th></tr></thead>
                <tbody>
                  {rows.map(r => (
                    <tr key={`${r.company}|${r.store}`}>
                      <td style={td}>{r.company}</td>
                      <td style={td}>{r.store}</td>
                      <td style={td}>{r.market || '—'}</td>
                      <td style={tdr}>{num(r.units)}</td>
                      <td style={tdr}><strong>{money(r.amount)}</strong></td>
                      <td style={{ ...td, fontSize: 12, color: 'var(--text2)' }} title={RESOLVED_BY[r.resolved_by] || ''}>{r.resolved_by}</td>
                    </tr>
                  ))}
                  {!rows.length && <tr><td style={td} colSpan={6}>No device purchases in this window.</td></tr>}
                </tbody>
              </table>
            </div>
          </Card>

          {/* ── UNRESOLVED: labelled rows carrying their money, never a silent omission ───────── */}
          {(unmapped.length > 0 || noCompany.length > 0) && (
            <Card title="Unresolved — money that is IN this report but could not be placed"
                  note="These rows are counted in every total above. They are shown separately so the amount is visible and the setup gap is actionable — they are never dropped, and never booked to a store or company we cannot evidence.">
              {unmapped.length > 0 && (
                <div style={{ marginBottom: noCompany.length ? 14 : 0 }}>
                  <div style={{ fontSize: 12.5, fontWeight: 700, marginBottom: 5 }}>{STORE_NOT_MAPPED} — no retail store matched this location</div>
                  <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 6 }}>
                    Expect the distributor&rsquo;s own <strong>master / dealer account address</strong> here. That is a
                    legal entity, not a retail location, and what is billed to it is typically chargebacks, fees and
                    loans rather than store purchases — so it is deliberately kept out of every store&rsquo;s figures
                    instead of being attributed to whichever store happens to share its street number.
                  </div>
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                      <thead><tr><th style={th}>Location, exactly as the distributor wrote it</th><th style={thr}>Lines</th><th style={thr}>Units</th><th style={thr}>Amount</th></tr></thead>
                      <tbody>{unmapped.map((u: any) => (
                        <tr key={u.location}><td style={td}>{u.location}</td><td style={tdr}>{num(u.lines)}</td><td style={tdr}>{num(u.units)}</td><td style={tdr}><strong>{money(u.amount)}</strong></td></tr>
                      ))}</tbody>
                    </table>
                  </div>
                </div>
              )}
              {noCompany.length > 0 && (
                <div>
                  <div style={{ fontSize: 12.5, fontWeight: 700, marginBottom: 5 }}>{COMPANY_NOT_MAPPED} — the store is ours, but it has no company assignment</div>
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                      <thead><tr><th style={th}>Store</th><th style={th}>Market</th><th style={thr}>Amount</th></tr></thead>
                      <tbody>{noCompany.map((r: any) => (
                        <tr key={r.store}><td style={td}>{r.store}</td><td style={td}>{r.market || '—'}</td><td style={tdr}><strong>{money(r.amount)}</strong></td></tr>
                      ))}</tbody>
                    </table>
                  </div>
                </div>
              )}
            </Card>
          )}

          {byResolver && byResolver.lines > 0 && (
            <div className="card" style={{ padding: '10px 14px', marginBottom: 14, fontSize: 12.5, color: 'var(--text2)' }}>
              <strong>{money(byResolver.amount)}</strong> across {num(byResolver.lines)} line(s) reached a store by a
              route other than an exact address — on this feed that is usually the unambiguous leading street
              NUMBER, which never compares the street name. The right store today, a coincidence-prone route
              tomorrow: a <strong>store-alias row</strong> makes each of those matches deliberate. The money is
              already counted above; this is a setup note, not a discrepancy.
            </div>
          )}

          <Card title="By product — what was actually bought"
                note={<><strong>Tablets are included.</strong> A tablet arrives serialised and is billed as a device, so
                      it counts here. This table names every product with its own dollars, so a reader who wants
                      handsets only can separate them — nothing is silently excluded. A line counts as a device
                      because that product actually arrived as a serialised unit, never because its name looks
                      like a phone.</>}>
            <div style={{ overflowX: 'auto', maxHeight: 420, overflowY: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr><th style={th}>Product, as the distributor bills it</th><th style={thr}>Units</th><th style={thr}>Purchases</th></tr></thead>
                <tbody>{products.map((p: any) => (
                  <tr key={p.name}><td style={td}>{p.name}</td><td style={tdr}>{num(p.units)}</td><td style={tdr}>{money(p.amount)}</td></tr>
                ))}</tbody>
              </table>
            </div>
          </Card>

          <Card title="Not devices — on the same invoices, excluded from the figures above"
                note={<>SIM packs, services, chargebacks and fees are on these invoices but are not device purchases.
                      They are listed rather than hidden, so devices + these always reconcile to every line the
                      {' '}{distributor} billed in the window ({money(data.totals?.all_lines_amount)}). Invoice-level
                      shipping, other cost and tax are not device purchase price and are not in this report at all —
                      the P&amp;L already books them.</>}>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr><th style={th}>Line</th><th style={thr}>Lines</th><th style={thr}>Amount</th></tr></thead>
                <tbody>{nonDevice.map((n: any) => (
                  <tr key={n.name}><td style={td}>{n.name}</td><td style={tdr}>{num(n.lines)}</td><td style={tdr}>{money(n.amount)}</td></tr>
                ))}
                {!nonDevice.length && <tr><td style={td} colSpan={3}>Every line in this window was a device.</td></tr>}</tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  )
}
