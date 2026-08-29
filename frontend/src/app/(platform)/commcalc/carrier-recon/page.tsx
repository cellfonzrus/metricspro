'use client'
import { useCallback, useMemo, useState } from 'react'
import { apiUpload, fmt, getActiveOrg } from '@/lib/client'

// CARRIER RECONCILIATION (upload & reconcile) — v1 Boost/ePay.
//
// The owner uploads the back-office "Rebate Reconciliation" workbook; the backend parses it, computes OUR
// per-store figures for the same period by REUSING the existing engines (imei-rebate, GP, the ePay
// payment classifier) and returns Boost vs Ours vs Diff per store. This page renders that comparison plus
// the Boost-side Escalation / Unpaid / Missing sheets and an "Unmatched stores" callout.
//
// DISPLAY / ANALYSIS ONLY — uploading here NEVER changes anyone's pay. It is a read-and-compare surface so
// the back office can be retired.

// Super-admin org resolution (same targeted mitigation the IMEI-rebate + Sales pages use): a super-admin
// is not rewritten by the tenant middleware, so the multipart POST carries the active org explicitly.
const orgQS = () => { const o = getActiveOrg(); return o ? `?org_id=${encodeURIComponent(o)}` : '' }

const card: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 10, background: 'var(--surface)' }
const th: React.CSSProperties = { textAlign: 'right', padding: '6px 8px', fontSize: 11, color: 'var(--text2)', fontWeight: 700, whiteSpace: 'nowrap' }
const thL: React.CSSProperties = { ...th, textAlign: 'left' }
const td: React.CSSProperties = { padding: '5px 8px', fontSize: 13, textAlign: 'right', whiteSpace: 'nowrap' }
const tdL: React.CSSProperties = { ...td, textAlign: 'left' }
const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const lbl: React.CSSProperties = { fontSize: 12, color: 'var(--text2)', display: 'inline-flex', flexDirection: 'column', gap: 4 }
const tabBtn: (on: boolean) => React.CSSProperties = (on) => ({
  padding: '6px 12px', fontSize: 13, cursor: 'pointer', borderRadius: 8, border: '1px solid var(--border)',
  background: on ? 'var(--accent, #2563eb)' : 'var(--surface)', color: on ? '#fff' : 'var(--text)', fontWeight: on ? 700 : 500,
})

const money = (n: number) => fmt(Math.round((n || 0) * 100) / 100)

// Diff colouring: green when |diff| ≤ $0.01 (a match), amber for a small gap, red for a large one.
function diffColor(d: number): string {
  const a = Math.abs(d || 0)
  if (a <= 0.01) return '#15803d'
  if (a <= 25) return '#b45309'
  return '#b91c1c'
}

// GP is intentionally omitted from the comparison (owner decision — the backend neither computes nor
// returns an ours-side GP for this recon, and strips GP from the diff). Kept optional for forward-compat.
type Cmp = { rebate_paid: number; comm_paid: number; epay_paid: number; gp?: number }
type PerStore = {
  store: string; resolved_store: string | null; matched: boolean
  boost: Cmp & { rebate_expected?: number; device_cost?: number; rebate_diff?: number }
  ours: Cmp; diff: Cmp; match_ok: Record<string, boolean>
}
type ReconResp = {
  period: string; carrier: string
  per_store: PerStore[]; unmatched_stores: string[]
  escalations: Record<string, any>[]; unpaid_devices: Record<string, any>[]; missing: Record<string, any>[]
  totals: { boost: Cmp; ours: Cmp; diff: Cmp; boost_workbook: Record<string, number> }
  raw_txn_count: number; notes: string[]
}

type MoneyKey = 'rebate_paid' | 'comm_paid' | 'epay_paid'
const FIELDS: { key: MoneyKey; label: string }[] = [
  { key: 'rebate_paid', label: 'Rebate Paid' },
  { key: 'comm_paid', label: 'Comm Paid' },
  { key: 'epay_paid', label: 'ePay Paid' },
  // GP intentionally skipped (owner decision) — not computed/compared on the ours side.
]

function thisMonthLabel() {
  return new Date().toLocaleDateString('en-US', { month: 'short', year: 'numeric' }).replace(' ', '-')
}

function TripleHead() {
  return (
    <>
      <th style={th}>Boost</th><th style={th}>Ours</th><th style={th}>Diff</th>
    </>
  )
}

function TripleCells({ boost, ours, diff }: { boost: number; ours: number; diff: number }) {
  return (
    <>
      <td style={td}>{money(boost)}</td>
      <td style={{ ...td, color: 'var(--text2)' }}>{money(ours)}</td>
      <td style={{ ...td, color: diffColor(diff), fontWeight: 600 }}>{money(diff)}</td>
    </>
  )
}

function LineTable({ rows }: { rows: Record<string, any>[] }) {
  if (!rows.length) return <div style={{ padding: 14, fontSize: 13, color: 'var(--text3)' }}>No rows on this sheet.</div>
  const cols = Object.keys(rows[0])
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ borderCollapse: 'collapse', width: '100%', minWidth: 640 }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--border)' }}>
            {cols.map(c => <th key={c} style={thL}>{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
              {cols.map(c => <td key={c} style={tdL}>{r[c] == null ? '' : String(r[c])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function CarrierReconPage() {
  const [period, setPeriod] = useState(thisMonthLabel())
  const [carrier, setCarrier] = useState('boost')
  const [fileObj, setFileObj] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [data, setData] = useState<ReconResp | null>(null)
  const [tab, setTab] = useState<'compare' | 'escalations' | 'unpaid' | 'missing'>('compare')

  const upload = useCallback(async () => {
    if (!fileObj) { setErr('Choose a reconciliation workbook (.xlsx) first.'); return }
    setBusy(true); setErr('')
    try {
      const form = new FormData()
      form.append('file', fileObj)
      form.append('period', period)
      form.append('carrier', carrier)
      const res: ReconResp = await apiUpload(`/api/v1/commcalc/carrier-recon/upload${orgQS()}`, form)
      setData(res); setTab('compare')
    } catch (e: any) {
      setErr(e?.message || String(e))
    } finally {
      setBusy(false)
    }
  }, [fileObj, period, carrier])

  const mismatches = useMemo(
    () => (data?.per_store || []).filter(r => FIELDS.some(f => !r.match_ok?.[f.key])).length,
    [data],
  )

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🔁 Carrier Reconciliation</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 820 }}>
          Upload the back-office <b>Rebate Reconciliation</b> workbook and compare it, per store, against{' '}
          <b>our</b> computed figures. <b>Display &amp; analysis only</b> — this never changes anyone&apos;s
          pay or writes to a money table.
        </p>
      </div>

      {/* upload control */}
      <div className="card" style={{ ...card, padding: 14, display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <label style={lbl}>Period
          <input value={period} onChange={e => setPeriod(e.target.value)} placeholder="Jul-2026" style={sel} />
        </label>
        <label style={lbl}>Carrier
          <select value={carrier} onChange={e => setCarrier(e.target.value)} style={sel}>
            <option value="boost">Boost / ePay</option>
          </select>
        </label>
        <label style={lbl}>Workbook (.xlsx)
          <input type="file" accept=".xlsx,.xlsm,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            onChange={e => setFileObj(e.target.files?.[0] || null)} style={{ ...sel, padding: 5 }} />
        </label>
        <button onClick={upload} disabled={busy} className="card"
          style={{ padding: '8px 16px', fontSize: 13, fontWeight: 700, cursor: busy ? 'wait' : 'pointer',
            background: 'var(--accent, #2563eb)', color: '#fff', border: 'none', borderRadius: 8 }}>
          {busy ? 'Reconciling…' : 'Upload & reconcile'}
        </button>
      </div>

      {err && (
        <div className="card" style={{ ...card, padding: 12, marginTop: 12, borderColor: '#b91c1c', color: '#b91c1c', fontSize: 13 }}>
          ⚠️ {err}
        </div>
      )}

      {data && (
        <>
          {/* headline tiles */}
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', margin: '16px 0' }}>
            {[
              { cap: 'Period', val: data.period },
              { cap: 'Stores compared', val: String(data.per_store.length) },
              { cap: 'Stores with a mismatch', val: String(mismatches) },
              { cap: 'Unmatched stores', val: String(data.unmatched_stores.length) },
              { cap: 'Raw txns (workbook)', val: fmt(data.raw_txn_count) },
            ].map(t => (
              <div key={t.cap} className="card" style={{ ...card, padding: '12px 14px', flex: 1, minWidth: 150 }}>
                <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 700, textTransform: 'uppercase' }}>{t.cap}</div>
                <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2 }}>{t.val}</div>
              </div>
            ))}
          </div>

          {/* unmatched callout — never silently dropped */}
          {data.unmatched_stores.length > 0 && (
            <div className="card" style={{ ...card, padding: 12, marginBottom: 14, borderColor: '#b45309',
              background: 'var(--surface2, #fffbeb)', fontSize: 13 }}>
              <b style={{ color: '#b45309' }}>⚠️ {data.unmatched_stores.length} workbook store(s) could not be matched to our data</b>{' '}
              — shown below with a zeroed “Ours” column, never dropped. Fix a mapping at{' '}
              <b>Store Matching</b> to resolve them:
              <div style={{ marginTop: 6, color: 'var(--text2)' }}>{data.unmatched_stores.join(' · ')}</div>
            </div>
          )}

          {(data.notes?.length ?? 0) > 0 && (
            <div className="card" style={{ ...card, padding: 12, marginBottom: 14, fontSize: 12, color: 'var(--text2)' }}>
              {data.notes.map((n, i) => <div key={i}>• {n}</div>)}
            </div>
          )}

          {/* tabs */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
            <button style={tabBtn(tab === 'compare')} onClick={() => setTab('compare')}>Per-store comparison</button>
            <button style={tabBtn(tab === 'escalations')} onClick={() => setTab('escalations')}>Escalations ({data.escalations.length})</button>
            <button style={tabBtn(tab === 'unpaid')} onClick={() => setTab('unpaid')}>Unpaid Devices ({data.unpaid_devices.length})</button>
            <button style={tabBtn(tab === 'missing')} onClick={() => setTab('missing')}>Missing ({data.missing.length})</button>
          </div>

          {tab === 'compare' && (
            <div className="card" style={{ ...card, overflowX: 'auto' }}>
              <table style={{ borderCollapse: 'collapse', width: '100%', minWidth: 900 }}>
                <thead>
                  <tr style={{ borderBottom: '2px solid var(--border)' }}>
                    <th style={{ ...thL, position: 'sticky', left: 0, background: 'var(--surface)' }} rowSpan={2}>Store</th>
                    {FIELDS.map(f => <th key={f.key} style={{ ...th, textAlign: 'center' }} colSpan={3}>{f.label}</th>)}
                  </tr>
                  <tr style={{ borderBottom: '1px solid var(--border)' }}>
                    {FIELDS.map(f => <TripleHead key={f.key} />)}
                  </tr>
                </thead>
                <tbody>
                  {data.per_store.map(r => (
                    <tr key={r.store} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ ...tdL, position: 'sticky', left: 0, background: 'var(--surface)', fontWeight: 600 }}>
                        {r.store}
                        {!r.matched && <span title="No match on our side" style={{ marginLeft: 6, color: '#b45309' }}>⚠️</span>}
                      </td>
                      {FIELDS.map(f => (
                        <TripleCells key={f.key} boost={r.boost[f.key]} ours={r.ours[f.key]} diff={r.diff[f.key]} />
                      ))}
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr style={{ borderTop: '2px solid var(--border)', fontWeight: 700 }}>
                    <td style={{ ...tdL, position: 'sticky', left: 0, background: 'var(--surface)' }}>Grand Total</td>
                    {FIELDS.map(f => (
                      <TripleCells key={f.key} boost={data.totals.boost[f.key]} ours={data.totals.ours[f.key]} diff={data.totals.diff[f.key]} />
                    ))}
                  </tr>
                </tfoot>
              </table>
              <div style={{ padding: '8px 10px', fontSize: 11, color: 'var(--text3)' }}>
                Diff = Boost − Ours. <span style={{ color: '#15803d' }}>Green</span> = match within $0.01,{' '}
                <span style={{ color: '#b45309' }}>amber</span> = small gap, <span style={{ color: '#b91c1c' }}>red</span> = large gap.
              </div>
            </div>
          )}

          {tab === 'escalations' && <div className="card" style={card}><LineTable rows={data.escalations} /></div>}
          {tab === 'unpaid' && <div className="card" style={card}><LineTable rows={data.unpaid_devices} /></div>}
          {tab === 'missing' && <div className="card" style={card}><LineTable rows={data.missing} /></div>}
        </>
      )}
    </div>
  )
}
