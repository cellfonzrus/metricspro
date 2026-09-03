'use client'
// Current Monetary Liabilities (owner directive 2026-09-03): monies owed to the distributor,
// this week's payments due, payroll due this week, payroll tax due, rents due this week and
// insurance/recurring premiums due — per store, with the standard market/store filters.
//
// Reads ONLY GET /account/liabilities-due — a composition of EXISTING derivations (mig-933 handset
// payables + stored Balance-Sheet snapshot, storeops payroll-raw + the tax-estimate twin, the
// mig-946 store-lease rent/insurance helpers). Money gates are SERVER-side and fail closed: the
// mig-434 pay gate hides the payroll sections, the mig-946 lease gate hides rents/insurance — the
// page renders the restriction note instead (never a zero). Store/market filtering is client-side
// over the already-span-scoped per-store rows (what-you-see-is-what-exports, RULE FIVE).
import { useEffect, useMemo, useState } from 'react'
import { api, fmt } from '@/lib/client'
import StandardFilterBar from '@/components/StandardFilterBar'
import ReportExportBar from '@/components/ReportExportBar'
import StatTile from '@/components/StatTile'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'
import type { ExportSheet } from '@/lib/export'

const th: React.CSSProperties = { textAlign: 'left', padding: '7px 10px', fontSize: 12, color: 'var(--text2)', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '7px 10px', fontSize: 13, borderBottom: '1px solid var(--border)' }
const money = (v: any) => (v == null ? '—' : fmt(v))

function Section({ title, note, children }: { title: string; note?: string; children?: React.ReactNode }) {
  return (
    <div className="card" style={{ padding: 14, marginBottom: 14 }}>
      <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 6 }}>{title}</div>
      {note && <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 8 }}>{note}</div>}
      {children}
    </div>
  )
}

function Restricted({ what }: { what: string }) {
  return <div style={{ fontSize: 13, color: 'var(--text2)' }}>🔒 {what} is restricted for your role (org policy) — the figures are hidden server-side.</div>
}

export default function LiabilitiesDuePage() {
  const [filt, setFilt] = useState<StandardFilterValue>(() => emptyStandardFilter(''))
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  useEffect(() => {
    setLoading(true); setErr('')
    api('/api/v1/account/liabilities-due')
      .then(setData)
      .catch(e => { setErr(e?.message || String(e)); setData(null) })
      .finally(() => setLoading(false))
  }, [])

  const selStores = useMemo(() => new Set(filt.stores.map(s => s.trim().toUpperCase())), [filt.stores])
  const selMkts = useMemo(() => new Set(filt.markets.map(m => m.trim().toUpperCase())), [filt.markets])
  const keep = (store: any, market: any) => {
    const s = String(store || '').trim().toUpperCase()
    const m = String(market || '').trim().toUpperCase()
    if (selStores.size && !selStores.has(s)) return false
    if (selMkts.size && !selMkts.has(m)) return false
    return true
  }
  const filtering = selStores.size > 0 || selMkts.size > 0

  const dist = data?.distributor || {}
  const outstanding = dist.outstanding
  const dueWeek = dist.due_this_week
  const snap = dist.snapshot
  const payroll = data?.payroll || {}
  const rents = data?.rents || {}
  const insurance = data?.insurance || {}

  const outByStore = (outstanding?.by_store || []).filter((r: any) => keep(r.store, r.market))
  const dueByStore = (dueWeek?.by_store || []).filter((r: any) => keep(r.store, r.market))
  const rentRows = (rents.rows || []).filter((r: any) => keep(r.store_code, r.market))
  const insRows = (insurance.rows || []).filter((r: any) => keep(r.store_code, r.market))
  const rentTotal = rentRows.reduce((a: number, r: any) => a + (r.amount ?? 0), 0)
  const rentUnknown = rentRows.filter((r: any) => r.amount == null).length
  const insTotal = insRows.reduce((a: number, r: any) => a + (r.amount ?? 0), 0)

  // store/market options from every per-store row the payload carries
  const allRows: { store?: string; market?: string }[] = useMemo(() => ([
    ...(outstanding?.by_store || []).map((r: any) => ({ store: r.store, market: r.market })),
    ...(dueWeek?.by_store || []).map((r: any) => ({ store: r.store, market: r.market })),
    ...((payroll.current?.by_store) || []).map((r: any) => ({ store: r.store, market: r.market })),
    ...(rents.rows || []).map((r: any) => ({ store: r.store_code, market: r.market })),
    ...(insurance.rows || []).map((r: any) => ({ store: r.store_code, market: r.market })),
  ]), [outstanding, dueWeek, payroll, rents, insurance])
  const storeOpts = useMemo(() => [...new Set(allRows.map(r => (r.store || '').trim()).filter(Boolean))].sort(), [allRows])
  const marketOpts = useMemo(() => [...new Set(allRows.map(r => (r.market || '').trim()).filter(Boolean))].sort(), [allRows])

  const payrollDue = (payroll.due || []) as any[]
  const payrollDueGross = payrollDue.reduce((a, p) => a + (p.gross_total || 0), 0)
  const payrollDueTax = payrollDue.reduce((a, p) => a + (p.tax?.total || 0), 0)

  function sheets(): ExportSheet[] {
    const out: ExportSheet[] = []
    out.push({ name: 'Owed to Distributor', rows: outByStore, columns: [
      { header: 'Store', get: (r: any) => r.store }, { header: 'Market', get: (r: any) => r.market },
      { header: 'Outstanding', money: true, get: (r: any) => r.amount },
    ] })
    out.push({ name: 'Due This Week', rows: dueByStore, columns: [
      { header: 'Store', get: (r: any) => r.store }, { header: 'Market', get: (r: any) => r.market },
      { header: 'Due this week', money: true, get: (r: any) => r.amount },
    ] })
    if (payroll.allowed) {
      out.push({ name: 'Payroll', rows: (payroll.current?.by_store || []), columns: [
        { header: 'Store', get: (r: any) => r.store }, { header: 'Market', get: (r: any) => r.market },
        { header: 'Employees', get: (r: any) => r.employees },
        { header: 'Gross (current period)', money: true, get: (r: any) => r.gross },
      ] })
    }
    if (rents.allowed) {
      out.push({ name: 'Rents Due', rows: rentRows, columns: [
        { header: 'Store', get: (r: any) => r.store_code }, { header: 'Market', get: (r: any) => r.market },
        { header: 'Month', get: (r: any) => r.month },
        { header: 'Due window', get: (r: any) => `${r.due_start} → ${r.due_end}` },
        { header: 'Rent', money: true, get: (r: any) => r.amount },
      ] })
      out.push({ name: 'Insurance Due', rows: insRows, columns: [
        { header: 'Store', get: (r: any) => r.store_code }, { header: 'Market', get: (r: any) => r.market },
        { header: 'Due', get: (r: any) => r.due_date }, { header: 'Company', get: (r: any) => r.company },
        { header: 'Frequency', get: (r: any) => r.frequency },
        { header: 'Premium', money: true, get: (r: any) => r.amount },
      ] })
    }
    return out
  }

  return (
    <div style={{ maxWidth: 1150 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>💳 Current Monetary Liabilities</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 13.5, margin: '4px 0 12px' }}>
            What the company owes right now and what falls due this week
            {data?.week ? <> (<b>{data.week.start}</b> → <b>{data.week.end}</b>)</> : null} — distributor
            handset payables, payroll &amp; payroll tax, rents and insurance premiums. Composed from the
            balance-sheet, payroll and store-lease machinery — never a second derivation.
          </p>
        </div>
        <ReportExportBar title="Current Monetary Liabilities" filename={`liabilities_due_${data?.as_of || ''}`} sheets={sheets()} />
      </div>

      <StandardFilterBar value={filt} onChange={setFilt} periodMode="none" show={{ reps: false }}
        storeOptions={storeOpts} marketOptions={marketOpts} />

      {err && <div className="card" style={{ padding: 14, color: 'crimson', marginBottom: 12 }}>{err}</div>}
      {loading && <div style={{ color: 'var(--text2)', padding: 20 }}>Loading…</div>}

      {!loading && data && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10, marginBottom: 16 }}>
            <StatTile label="Owed to distributor (outstanding)" value={outstanding ? fmt(outstanding.total) : '—'}
              sub={dist.configured ? `as of ${outstanding?.as_of || data.as_of}` : 'not configured for this org'} />
            <StatTile label="Distributor payments due this week" value={dueWeek ? fmt(dueWeek.total) : '—'} />
            <StatTile label="Payroll due this week" value={payroll.allowed ? (payrollDue.length ? fmt(payrollDueGross) : 'none') : '🔒'}
              sub={payroll.allowed ? (payrollDue.length ? `payday ${payrollDue.map(p => p.payday).join(', ')}` : 'no payday falls this week') : 'restricted'} />
            <StatTile label="Payroll tax due" value={payroll.allowed ? (payrollDue.length ? fmt(payrollDueTax) : 'none') : '🔒'}
              sub={payroll.allowed && payrollDue.length ? 'employer FICA + withheld' : undefined} />
            <StatTile label="Rents due this week" value={rents.allowed ? fmt(filtering ? rentTotal : rents.total) : '🔒'}
              sub={rents.allowed && (filtering ? rentUnknown : rents.unknown) ? `${filtering ? rentUnknown : rents.unknown} store(s) with rent not set` : undefined} />
            <StatTile label="Insurance premiums due" value={insurance.allowed ? fmt(filtering ? insTotal : insurance.total) : '🔒'} />
          </div>

          <Section title="🏭 Monies owed to the distributor"
            note={dist.configured
              ? 'Handset payables inside the vendor due-date window (the same mig-933 machinery as the Balance Sheet), attributed to stores via the processor-account index. Unmapped accounts show company-wide.'
              : 'Handset-payable order types are not configured for this org (Accounting settings) — the balance-sheet snapshot lines below still show the distributor position where computed.'}>
            {dist.note && <div style={{ fontSize: 12.5, color: 'crimson', marginBottom: 8 }}>{dist.note}</div>}
            {outstanding && (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead><tr><th style={th}>Store</th><th style={th}>Market</th><th style={th}>Outstanding</th><th style={th}>Due this week</th></tr></thead>
                  <tbody>
                    {outByStore.map((r: any) => {
                      const due = dueByStore.find((d: any) => d.store === r.store)
                      return (
                        <tr key={r.store}>
                          <td style={td}>{r.store}</td><td style={td}>{r.market || '—'}</td>
                          <td style={td}>{money(r.amount)}</td><td style={td}>{money(due?.amount ?? (dueWeek ? 0 : null))}</td>
                        </tr>
                      )
                    })}
                    {(outstanding.company_wide || dueWeek?.company_wide) ? (
                      <tr>
                        <td style={td}><i>Company-wide (unmapped accounts)</i></td><td style={td}>—</td>
                        <td style={td}>{money(outstanding.company_wide)}</td><td style={td}>{money(dueWeek?.company_wide)}</td>
                      </tr>
                    ) : null}
                    <tr>
                      <td style={{ ...td, fontWeight: 700 }}>Total</td><td style={td}></td>
                      <td style={{ ...td, fontWeight: 700 }}>{money(outstanding.total)}</td>
                      <td style={{ ...td, fontWeight: 700 }}>{money(dueWeek?.total)}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            )}
            {snap && (
              <div style={{ fontSize: 12.5, color: 'var(--text2)', marginTop: 8 }}>
                Balance-sheet snapshot ({snap.period}{snap.computed_at ? `, computed ${new Date(snap.computed_at).toLocaleString()}` : ''}):
                {' '}Owed to Distributor (device financing) <b>{money(snap.owed_vip)}</b> · Distributor invoices unpaid <b>{money(snap.vip_ap)}</b>
                {snap.handset_payable != null && <> · Handset payables <b>{money(snap.handset_payable)}</b></>}
              </div>
            )}
          </Section>

          <Section title="🧑‍💼 Payroll due this week & payroll tax"
            note="A payroll is “due this week” when its pay period’s PAYDAY (your tenant’s pay-period settings) falls inside the week. Tax = employer FICA + amounts withheld from employees, estimated by the same calculator as the Payroll-with-Tax page.">
            {!payroll.allowed ? <Restricted what="Payroll" /> : (
              <>
                {payroll.note && <div style={{ fontSize: 12.5, color: 'crimson', marginBottom: 8 }}>{payroll.note}</div>}
                {payrollDue.length === 0 && <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 8 }}>No payday falls inside this week.</div>}
                {payrollDue.map((p: any) => (
                  <div key={p.start} style={{ marginBottom: 10 }}>
                    <div style={{ fontSize: 13, marginBottom: 4 }}>
                      Pay period <b>{p.start} → {p.end}</b> · payday <b>{p.payday}</b> · gross <b>{fmt(p.gross_total)}</b> ·
                      tax due <b>{fmt(p.tax?.total)}</b> <span style={{ color: 'var(--text2)' }}>(employer {fmt(p.tax?.employer_fica)} + withheld {fmt(p.tax?.withheld)})</span>
                    </div>
                  </div>
                ))}
                {payroll.current && (
                  <div style={{ overflowX: 'auto' }}>
                    <div style={{ fontSize: 12.5, color: 'var(--text2)', margin: '6px 0' }}>
                      Current period {payroll.current.start} → {payroll.current.end} (accruing; payday {payroll.current.payday}) — gross so far {fmt(payroll.current.gross_total)}, est. tax {fmt(payroll.current.tax?.total)}:
                    </div>
                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                      <thead><tr><th style={th}>Store</th><th style={th}>Market</th><th style={th}>Employees</th><th style={th}>Gross</th></tr></thead>
                      <tbody>
                        {(payroll.current.by_store || []).filter((r: any) => keep(r.store, r.market)).map((r: any) => (
                          <tr key={r.store}><td style={td}>{r.store}</td><td style={td}>{r.market || '—'}</td><td style={td}>{r.employees}</td><td style={td}>{fmt(r.gross)}</td></tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            )}
          </Section>

          <Section title="🏢 Rents due this week"
            note="Per the store’s lease setup (mig 946): the store’s own rent-due rule, else your org default, else the house default (first week of the month) — the rent amount follows the explicit schedule, else the annual escalation, else current rent.">
            {!rents.allowed ? <Restricted what="Lease / rent data" /> : (
              <>
                {rents.note && <div style={{ fontSize: 12.5, color: 'crimson', marginBottom: 8 }}>{rents.note}</div>}
                {rentRows.length === 0 && <div style={{ fontSize: 13, color: 'var(--text2)' }}>No store rent falls due this week.</div>}
                {rentRows.length > 0 && (
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                      <thead><tr><th style={th}>Store</th><th style={th}>Market</th><th style={th}>Month</th><th style={th}>Due window</th><th style={th}>Rent</th></tr></thead>
                      <tbody>
                        {rentRows.map((r: any, i: number) => (
                          <tr key={r.store_code + r.month + i}>
                            <td style={td}>{r.store_code}</td><td style={td}>{r.market || '—'}</td>
                            <td style={td}>{r.month}</td><td style={td}>{r.due_start} → {r.due_end}</td>
                            <td style={td}>{r.amount == null ? <i style={{ color: 'var(--text2)' }}>rent not set</i> : fmt(r.amount)}</td>
                          </tr>
                        ))}
                        <tr><td style={{ ...td, fontWeight: 700 }}>Total</td><td style={td}></td><td style={td}></td><td style={td}></td><td style={{ ...td, fontWeight: 700 }}>{fmt(rentTotal)}</td></tr>
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            )}
          </Section>

          <Section title="🛡️ Insurance & recurring premiums due"
            note="Insurance premiums recurring per each store’s premium frequency (annual / semiannual / quarterly / monthly) from the lease record.">
            {!insurance.allowed ? <Restricted what="Insurance data" /> : (
              insRows.length === 0 ? <div style={{ fontSize: 13, color: 'var(--text2)' }}>No premiums fall due this week.</div> : (
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                    <thead><tr><th style={th}>Store</th><th style={th}>Market</th><th style={th}>Due</th><th style={th}>Company</th><th style={th}>Frequency</th><th style={th}>Premium</th></tr></thead>
                    <tbody>
                      {insRows.map((r: any, i: number) => (
                        <tr key={r.store_code + r.due_date + i}>
                          <td style={td}>{r.store_code}</td><td style={td}>{r.market || '—'}</td>
                          <td style={td}>{r.due_date}</td><td style={td}>{r.company || '—'}</td>
                          <td style={td}>{r.frequency}</td><td style={td}>{fmt(r.amount)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            )}
          </Section>
        </>
      )}
    </div>
  )
}
