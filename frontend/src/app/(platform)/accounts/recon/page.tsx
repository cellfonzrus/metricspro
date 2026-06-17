'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { ExportButtons, ExportPayload } from '@/lib/export'

const inp: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const badge = (s: string) => ({
  ok: { t: '✓ matches', bg: '#dcfce7', c: '#166534' },
  under: { t: '⚠ underpaid', bg: '#fee2e2', c: '#991b1b' },
  over: { t: 'over', bg: '#fef9c3', c: '#854d0e' },
} as any)[s] || { t: s, bg: '#f1f5f9', c: '#475569' }

export default function ReconPage() {
  const { period } = usePeriod()
  const [tolerance, setTolerance] = useState(1)
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [open, setOpen] = useState<Record<string, boolean>>({})

  function load(analyze = false) {
    setLoading(true)
    api(`/api/v1/account/recon/${encodeURIComponent(period)}?tolerance=${tolerance}&analyze=${analyze}&org_id=${ORG_ID}`)
      .then(setData).catch(console.error).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [period])

  async function scrape() {
    setBusy('scrape'); setMsg('Scraping VIP credit memos…')
    try {
      const r = await api(`/api/v1/account/credit-memos/sweep?org_id=${ORG_ID}`, { method: 'POST' })
      setMsg(`Scraped ${r.credit_memos} credit memos (${r.xfinity_excluded} Xfinity excluded).`); load()
    } catch (e: any) { setMsg('Scrape failed: ' + (e?.message || e)) }
    setBusy('')
  }
  async function syncFlags() {
    setBusy('flags'); setMsg('')
    try {
      const r = await api(`/api/v1/account/recon/${encodeURIComponent(period)}/sync-flags?tolerance=${tolerance}&org_id=${ORG_ID}`, { method: 'POST' })
      setMsg(`Wrote ${r.flags_written} flags (${r.stores_flagged} stores). See Flags & Compliance.`)
    } catch (e: any) { setMsg('Sync failed: ' + (e?.message || e)) }
    setBusy('')
  }

  const cw = data?.company_wide
  function buildPayload(): ExportPayload {
    return {
      title: `VIP Credit-Memo Reconciliation — ${period}`,
      subtitle: `Credit memos vs MI+ATU · tolerance ${fmt(data?.tolerance || 1)}`,
      filename: `recon-${period.replace(/\s+/g, '-')}`,
      sheets: [{ name: 'Recon', rows: data?.stores || [], columns: [
        { header: 'Store', get: (r: any) => r.store },
        { header: 'Credit memos', get: (r: any) => r.memo_total, money: true },
        { header: 'MI+ATU earned', get: (r: any) => r.mi_atu_total, money: true },
        { header: 'Difference', get: (r: any) => r.diff, money: true },
        { header: 'Status', get: (r: any) => r.status },
      ] }],
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🔎 VIP Credit-Memo Reconciliation</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>{period} · VIP "Weekly Incentive Credit" memos vs MI + ATU earned (Xfinity excluded)</p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {msg && <span style={{ fontSize: 12, color: 'var(--text2)', maxWidth: 320 }}>{msg}</span>}
          {data && <ExportButtons payload={buildPayload} />}
        </div>
      </div>

      <div className="card" style={{ padding: 12, marginBottom: 16, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13 }}>Tolerance $</span>
        <input type="number" step="0.01" style={{ ...inp, width: 90 }} value={tolerance} onChange={e => setTolerance(parseFloat(e.target.value) || 0)} />
        <button className="btn" onClick={() => load(false)}>↻ Recompute</button>
        <button className="btn" onClick={() => load(true)} title="Claude buckets MI/ATU by day vs the memo range for flagged stores">🧠 Analyze short days</button>
        <span style={{ width: 1, height: 22, background: 'var(--border)' }} />
        <button className="btn" onClick={scrape} disabled={!!busy}>{busy === 'scrape' ? '⏳…' : '🔄 Scrape credit memos'}</button>
        <button className="btn" onClick={syncFlags} disabled={!!busy}>{busy === 'flags' ? '⏳…' : '🚩 Write flags'}</button>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : !data?.has_memos ? (
        <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>
          No VIP credit memos loaded for {period}. Click <strong>Scrape credit memos</strong> above (uses the VIP sweep credentials).
        </div>
      ) : (
        <>
          {cw && (
            <div className="card" style={{ padding: 18, marginBottom: 16 }}>
              <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 10 }}>Company-wide (authoritative)</div>
              <div style={{ display: 'flex', gap: 30, flexWrap: 'wrap', alignItems: 'center' }}>
                <Tile label="VIP credit memos" v={cw.memo_total} />
                <Tile label="MI + ATU earned" v={cw.mi_atu_total} />
                <Tile label="Difference" v={cw.diff} signed />
                <span style={{ fontSize: 13, padding: '4px 12px', borderRadius: 999, fontWeight: 600, background: badge(cw.status).bg, color: badge(cw.status).c }}>{badge(cw.status).t}</span>
              </div>
              {cw.attributed_pct != null && (
                <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 8 }}>
                  Per-store attribution: <strong style={{ color: cw.attributed_pct >= 90 ? '#166534' : cw.attributed_pct >= 70 ? '#854d0e' : '#991b1b' }}>{cw.attributed_pct}%</strong> of MI+ATU $ attributed to a store by rep name
                  {cw.mi_unattributed > 0 && <> · {fmt(cw.mi_unattributed)} unattributed (still counted company-wide)</>}.
                  {cw.top_unattributed_reps?.length > 0 && (
                    <>
                      {' '}<button onClick={() => setOpen(o => ({ ...o, __unattr: !o.__unattr }))} style={{ border: 'none', background: 'none', color: 'var(--accent, #2563eb)', cursor: 'pointer', fontSize: 12, padding: 0 }}>
                        {open.__unattr ? 'hide' : 'show'} unmatched reps ▾
                      </button>
                      {open.__unattr && (
                        <div style={{ marginTop: 6, display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                          {cw.top_unattributed_reps.map((u: any) => <span key={u.rep}>{u.rep}: <strong>{fmt(u.amount)}</strong></span>)}
                          <span style={{ color: 'var(--text3)' }}>— add these as aliases on <a href="/commcalc/targets/rep-map" style={{ color: 'var(--accent,#2563eb)' }}>Rep Map</a> to attribute them.</span>
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
          )}

          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <div style={{ padding: '10px 16px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>By store (per-store MI/ATU attributed by rep — best-effort)</div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 640 }}>
                <thead>
                  <tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                    <th style={{ textAlign: 'left', padding: '8px 16px' }}>Store</th>
                    <th style={{ textAlign: 'right', padding: '8px 12px' }}>Credit memos</th>
                    <th style={{ textAlign: 'right', padding: '8px 12px' }}>MI + ATU</th>
                    <th style={{ textAlign: 'right', padding: '8px 12px' }}>Diff</th>
                    <th style={{ textAlign: 'center', padding: '8px 12px' }}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {data.stores.map((r: any) => {
                    const flagged = r.status !== 'ok'
                    const byDay = r.by_day && Object.keys(r.by_day).length > 0
                    const expandable = flagged && (r.missed_days_note || byDay)
                    return (
                      <>
                        <tr key={r.store} onClick={() => expandable && setOpen(o => ({ ...o, [r.store]: !o[r.store] }))}
                          style={{ borderTop: '1px solid var(--border)', fontSize: 13, cursor: expandable ? 'pointer' : 'default', background: r.status === 'under' ? '#fef2f2' : 'transparent' }}>
                          <td style={{ padding: '8px 16px' }}>{expandable && <span style={{ fontSize: 11, marginRight: 6 }}>{open[r.store] ? '▾' : '▸'}</span>}{r.store}</td>
                          <td style={{ padding: '8px 12px', textAlign: 'right' }}>{fmt(r.memo_total)}</td>
                          <td style={{ padding: '8px 12px', textAlign: 'right' }}>{fmt(r.mi_atu_total)}</td>
                          <td style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 600, color: r.diff < 0 ? '#dc2626' : r.diff > 0 ? '#854d0e' : 'var(--text3)' }}>{fmt(r.diff)}</td>
                          <td style={{ padding: '8px 12px', textAlign: 'center' }}><span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 999, fontWeight: 600, background: badge(r.status).bg, color: badge(r.status).c }}>{badge(r.status).t}</span></td>
                        </tr>
                        {expandable && open[r.store] && (
                          <tr style={{ background: '#fafbfc' }}>
                            <td colSpan={5} style={{ padding: '10px 16px 14px 36px' }}>
                              {r.missed_days_note && <div style={{ fontSize: 13, marginBottom: byDay ? 10 : 0 }}>🧠 {r.missed_days_note}</div>}
                              {byDay && (
                                <div style={{ fontSize: 12 }}>
                                  <div style={{ color: 'var(--text3)', marginBottom: 4 }}>MI + ATU accrual by day (activation / residual transfer-in):</div>
                                  <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                                    {Object.entries(r.by_day).map(([d, v]: any) => <span key={d}>{d}: <strong>{fmt(v)}</strong></span>)}
                                  </div>
                                </div>
                              )}
                            </td>
                          </tr>
                        )}
                      </>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
          {data.notes?.length > 0 && <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 10 }}>{data.notes.map((n: string, i: number) => <div key={i}>· {n}</div>)}</div>}
        </>
      )}
    </div>
  )
}

function Tile({ label, v, signed }: { label: string; v: number; signed?: boolean }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color: signed ? (v < 0 ? '#dc2626' : v > 0 ? '#854d0e' : 'var(--text)') : 'var(--text)' }}>{fmt(v || 0)}</div>
    </div>
  )
}
