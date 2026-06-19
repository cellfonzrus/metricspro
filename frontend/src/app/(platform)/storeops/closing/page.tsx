'use client'
import { useState, useEffect, useCallback } from 'react'
import { api, apiUpload, fmt, localToday } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'

const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const tin: React.CSSProperties = { ...sel, width: 110 }
const cell: React.CSSProperties = { padding: '6px 9px', borderBottom: '1px solid var(--border)', fontSize: 13 }

type Form = { dm_store_cash: string; dm_store_cc: string; dm_epay_cash: string; dm_epay_cc: string; dm_acc_sale: string; dm_other: string; note: string }

export default function DailyClosingPage() {
  const { user, permissions } = useAuth()
  const [date, setDate] = useState(localToday())
  const [dates, setDates] = useState<any[]>([])
  const [market, setMarket] = useState('')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [forms, setForms] = useState<Record<string, Form>>({})
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [upBusy, setUpBusy] = useState(false)
  const [upMsg, setUpMsg] = useState('')

  useEffect(() => { if (user?.market && permissions?.scope === 'market') setMarket(user.market) }, [user, permissions])

  useEffect(() => { api('/api/v1/closing/dates').then(d => setDates(d || [])).catch(() => {}) }, [])

  const load = useCallback(() => {
    if (!date) return
    setLoading(true)
    api(`/api/v1/closing/summary?date=${date}${market ? `&market=${encodeURIComponent(market)}` : ''}`)
      .then(d => {
        setData(d)
        const f: Record<string, Form> = {}
        ;(d?.stores || []).forEach((s: any) => {
          const v = s.verification || {}
          const t = s.totals || {}
          const k = s.store_code || s.store_name
          f[k] = {
            dm_store_cash: String(v.dm_store_cash ?? t.store_cash ?? ''),
            dm_store_cc: String(v.dm_store_cc ?? t.store_cc ?? ''),
            dm_epay_cash: String(v.dm_epay_cash ?? t.epay_cash ?? ''),
            dm_epay_cc: String(v.dm_epay_cc ?? t.epay_cc ?? ''),
            dm_acc_sale: String(v.dm_acc_sale ?? t.acc_sale ?? ''),
            dm_other: String(v.dm_other ?? t.other_account ?? ''),
            note: v.note || '',
          }
        })
        setForms(f)
      })
      .catch(console.error).finally(() => setLoading(false))
  }, [date, market])
  useEffect(() => { load() }, [load])

  async function upload(file: File) {
    setUpBusy(true); setUpMsg('')
    const fd = new FormData(); fd.append('file', file)
    try {
      const r = await apiUpload('/api/v1/closing/upload', fd)
      setUpMsg(`✅ Loaded ${r.rows_saved} rows across ${r.dates?.length || 0} day(s)${r.unresolved_stores ? ` · ${r.unresolved_stores} rows had an unrecognized SFID` : ''}.`)
      api('/api/v1/closing/dates').then(d => setDates(d || [])).catch(() => {})
      if (r.dates?.length) setDate(r.dates[0]); else load()
    } catch (e: any) { setUpMsg('❌ ' + (e?.message || e)) }
    finally { setUpBusy(false) }
  }

  async function verify(s: any) {
    const k = s.store_code || s.store_name
    const f = forms[k]
    if (!s.store_code) { alert('This store has no resolved store code (unrecognized SFID) — fix the SFID/store mapping first.'); return }
    try {
      await api('/api/v1/closing/verify', { method: 'POST', body: JSON.stringify({
        close_date: date, store_code: s.store_code, store_name: s.store_name,
        verified: true, verified_by: user?.full_name || 'DM',
        dm_store_cash: num(f.dm_store_cash), dm_store_cc: num(f.dm_store_cc),
        dm_epay_cash: num(f.dm_epay_cash), dm_epay_cc: num(f.dm_epay_cc),
        dm_acc_sale: num(f.dm_acc_sale), dm_other: num(f.dm_other), note: f.note,
      }) })
      load()
    } catch (e: any) { alert('Verify failed: ' + (e?.message || e)) }
  }

  function setForm(k: string, patch: Partial<Form>) { setForms(p => ({ ...p, [k]: { ...p[k], ...patch } })) }

  const markets = Array.from(new Set((data?.stores || []).map((s: any) => s.market).filter(Boolean))).sort()
  const stores: any[] = data?.stores || []
  const verifiedCount = stores.filter(s => s.verification?.verified).length

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧾 Daily Closing</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Verify every evening that each store's closing sheet was submitted, confirm the totals, and reconcile against B2B actual sales.
        </p>
      </div>

      {/* Upload */}
      <div className="card" style={{ padding: 14, marginBottom: 16, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <label className="btn btn-secondary" style={{ fontSize: 13, cursor: 'pointer' }}>
          {upBusy ? '⏳ Uploading…' : '📤 Upload closing sheet'}
          <input type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) upload(f) }} />
        </label>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>Export the Google "Envelopes Data" sheet as .xlsx/.csv and upload it here.</span>
        {upMsg && <span style={{ fontSize: 13 }}>{upMsg}</span>}
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <input type="date" style={sel} value={date} onChange={e => setDate(e.target.value)} />
        {dates.length > 0 && (
          <select style={sel} value="" onChange={e => e.target.value && setDate(e.target.value)}>
            <option value="">Recent days…</option>
            {dates.map(d => <option key={d.date} value={d.date}>{d.date} ({d.rows})</option>)}
          </select>
        )}
        <select style={sel} value={market} onChange={e => setMarket(e.target.value)}>
          <option value="">All markets</option>
          {markets.map(m => <option key={m as string} value={m as string}>{m as string}</option>)}
        </select>
        {!loading && stores.length > 0 && <span style={{ fontSize: 13, color: 'var(--text2)' }}>{verifiedCount}/{stores.length} stores verified</span>}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : stores.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          No closing-sheet rows for {date}. Upload the sheet above (or pick another day).
        </div>
      ) : stores.map(s => {
        const k = s.store_code || s.store_name
        const f = forms[k] || {} as Form
        const t = s.totals || {}
        const ver = s.verification?.verified
        const recon = s.recon
        return (
          <div key={k} className="card" style={{ padding: 16, marginBottom: 14, borderLeft: `4px solid ${ver ? 'var(--green, #16794a)' : recon?.discrepancy ? 'var(--amber, #b45309)' : 'var(--border)'}` }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8 }}>
              <div>
                <div style={{ fontSize: 16, fontWeight: 700 }}>{s.store_address || s.store_name}</div>
                <div style={{ fontSize: 12, color: 'var(--text3)' }}>{s.market || '—'} · {t.rep_count} rep submission{t.rep_count === 1 ? '' : 's'}{s.scheduled_count ? ` of ${s.scheduled_count} scheduled` : ''}</div>
              </div>
              {ver
                ? <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--green, #16794a)' }}>✅ Verified by {s.verification.verified_by}</span>
                : <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text3)' }}>Unverified</span>}
            </div>

            {s.missing_reps?.length > 0 && (
              <div style={{ marginTop: 8, fontSize: 12, color: 'var(--amber, #b45309)' }}>
                ⚠️ Scheduled but no closing submitted: {s.missing_reps.join(', ')}
              </div>
            )}

            {/* Totals */}
            <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 12, fontSize: 13 }}>
              <Stat label="Store cash" value={fmt(t.store_cash)} />
              <Stat label="Store CC" value={fmt(t.store_cc)} />
              <Stat label="ePay cash" value={fmt(t.epay_cash)} />
              <Stat label="ePay CC" value={fmt(t.epay_cc)} />
              <Stat label="Acc sale" value={fmt(t.acc_sale)} />
              <Stat label="Other" value={fmt(t.other_account)} />
              <Stat label="Upg / New / Post" value={`${t.upgrade_count} / ${t.new_line_count} / ${t.postpaid_count}`} />
            </div>

            {/* B2B reconciliation */}
            {recon && (
              <div style={{ marginTop: 12, padding: '8px 12px', borderRadius: 8, background: recon.discrepancy ? '#fef3e2' : '#e6f7ec', fontSize: 13 }}>
                <strong>B2B reconciliation:</strong>{' '}
                Activations closing {recon.closing_activations} vs B2B {recon.b2b_activations}{' '}
                {recon.act_var !== 0 ? <b style={{ color: 'var(--amber, #b45309)' }}>(Δ{recon.act_var > 0 ? '+' : ''}{recon.act_var})</b> : '✓'}
                {' · '}Upgrades closing {recon.closing_upgrades} vs B2B {recon.b2b_upgrades}{' '}
                {recon.upg_var !== 0 ? <b style={{ color: 'var(--amber, #b45309)' }}>(Δ{recon.upg_var > 0 ? '+' : ''}{recon.upg_var})</b> : '✓'}
                <span style={{ color: 'var(--text3)' }}>{' · '}Acc GP (B2B) {fmt(recon.b2b_acc_gp)}</span>
              </div>
            )}

            {/* Reps */}
            <button className="btn btn-secondary" style={{ fontSize: 12, marginTop: 12 }} onClick={() => setOpen(o => ({ ...o, [k]: !o[k] }))}>
              {open[k] ? '▾' : '▸'} {t.rep_count} rep row{t.rep_count === 1 ? '' : 's'}
            </button>
            {open[k] && (
              <div className="table-wrapper" style={{ marginTop: 8 }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead><tr style={{ background: 'var(--surface2)' }}>
                    {['Employee', 'Store cash', 'Store CC', 'ePay cash', 'ePay CC', 'Acc', 'Other', 'Upg', 'New', 'Post', 'Env'].map(h =>
                      <th key={h} style={{ textAlign: 'left', padding: '6px 9px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
                  </tr></thead>
                  <tbody>
                    {s.reps.map((r: any) => (
                      <tr key={r.id}>
                        <td style={cell}>{r.employee_name || '—'}</td>
                        <td style={cell}>{fmt(r.store_cash)}</td>
                        <td style={cell}>{fmt(r.store_cc)}</td>
                        <td style={cell}>{fmt(r.epay_cash)}</td>
                        <td style={cell}>{fmt(r.epay_cc)}</td>
                        <td style={cell}>{fmt(r.acc_sale)}</td>
                        <td style={cell}>{fmt(r.other_account)}</td>
                        <td style={cell}>{r.upgrade_count}</td>
                        <td style={cell}>{r.new_line_count}</td>
                        <td style={cell}>{r.postpaid_count}</td>
                        <td style={cell}>{r.envelope_picture ? <a href={r.envelope_picture} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>📷</a> : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* DM verification */}
            {!ver && (
              <div style={{ marginTop: 14, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 8 }}>Confirm totals (prefilled from rep entries — adjust if needed)</div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                  <Lbl t="Store cash"><input style={tin} value={f.dm_store_cash || ''} onChange={e => setForm(k, { dm_store_cash: e.target.value })} /></Lbl>
                  <Lbl t="Store CC"><input style={tin} value={f.dm_store_cc || ''} onChange={e => setForm(k, { dm_store_cc: e.target.value })} /></Lbl>
                  <Lbl t="ePay cash"><input style={tin} value={f.dm_epay_cash || ''} onChange={e => setForm(k, { dm_epay_cash: e.target.value })} /></Lbl>
                  <Lbl t="ePay CC"><input style={tin} value={f.dm_epay_cc || ''} onChange={e => setForm(k, { dm_epay_cc: e.target.value })} /></Lbl>
                  <Lbl t="Acc sale"><input style={tin} value={f.dm_acc_sale || ''} onChange={e => setForm(k, { dm_acc_sale: e.target.value })} /></Lbl>
                  <Lbl t="Other"><input style={tin} value={f.dm_other || ''} onChange={e => setForm(k, { dm_other: e.target.value })} /></Lbl>
                </div>
                <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                  <input style={{ ...sel, flex: '1 1 280px' }} placeholder="Note (optional)" value={f.note || ''} onChange={e => setForm(k, { note: e.target.value })} />
                  <button className="btn btn-primary" style={{ fontSize: 13 }} onClick={() => verify(s)}>✅ Mark verified</button>
                </div>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function num(v: string): number | null { const n = Number(String(v).replace(/[$,]/g, '')); return isNaN(n) ? null : n }
const Stat = ({ label, value }: { label: string; value: string }) => (
  <div><div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div><div style={{ fontWeight: 600 }}>{value}</div></div>
)
const Lbl = ({ t, children }: { t: string; children: React.ReactNode }) => (
  <label style={{ fontSize: 11, color: 'var(--text3)' }}><div style={{ marginBottom: 2 }}>{t}</div>{children}</label>
)
