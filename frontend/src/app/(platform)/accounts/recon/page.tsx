'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import DrillDownPanel, {
  DrillTileLinks, useDrillTile, type DrillExtraColumn, type DrillPanel, type DrillRow,
} from '@/components/DrillDownPanel'

// WHERE EACH OF THESE THREE FIGURES CAME FROM (index §CORE-17.3w). The server serves a panel per
// figure, from account/memo_recon_drilldown.py, folded over the SAME store rows the By store table
// prints — ONE row shape and THREE grains on it, separated by PROVENANCE ALONE: what the
// distributor CREDITED, what the residual report says was EARNED, and their per-store difference.
// AN ACCUSATION NAMES ITS EVIDENCE (§CORE-17.3f): this report prints "underpaid" in dollars and
// writes those findings into the flag stream, so every one of its figures opens to its rows.
//
// THE EARNED FIGURE AND THE DIFFERENCE CARRY ONE MORE ROW than the table below them — the earned
// money no rep name placed on a store, which the report folds and prints beside the attribution
// percentage. Without it the panels would come back short by exactly that money.
//
// THIS PAGE IS SERVER-FOLDED AND IS DELIBERATELY *NOT* NARROWED. Its controls are a tolerance, a
// recompute, an analyze and two writes; it folds nothing in the browser and prints the company-wide
// object as served, so `lib/drill-narrow` has no place here. `harness_batch23_drilldown.py` §G
// fails the build in both directions off this page's own text.
const RECON_DRILL_COLUMNS: DrillExtraColumn[] = [
  { header: 'Status', get: (r: DrillRow) => String(r.fields?.status ?? '—'),
    sortValue: (r: DrillRow) => String(r.fields?.status ?? '') },
  { header: 'Memos', get: (r: DrillRow) => String(r.fields?.memos ?? '—'),
    sortValue: (r: DrillRow) => Number(r.fields?.memos ?? 0) },
  { header: 'Rep names listed', get: (r: DrillRow) => String(r.fields?.['rep names the report lists'] ?? '—'),
    sortValue: (r: DrillRow) => Number(r.fields?.['rep names the report lists'] ?? 0) },
]

const inp: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const badge = (s: string) => ({
  ok: { t: '✓ matches', bg: 'var(--c-bg-dcfce7)', c: 'var(--c-fg-166534)' },
  under: { t: '⚠ underpaid', bg: 'var(--c-bg-fee2e2)', c: 'var(--c-fg-991b1b)' },
  over: { t: 'over', bg: 'var(--c-bg-fef9c3)', c: 'var(--c-fg-854d0e)' },
} as any)[s] || { t: s, bg: 'var(--c-bg-f1f5f9)', c: 'var(--c-fg-475569)' }

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
    setBusy('scrape'); setMsg('Scraping Distributor credit memos…')
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
  // ONE open panel at a time, through the shared hook — the offer rule is the one home's, so a
  // figure the server served no panel for shows no link rather than a link to an empty panel.
  const drill = useDrillTile((data?.drilldown || {}) as Record<string, DrillPanel>)
  function buildPayload(): ExportPayload {
    return {
      title: `Distributor Credit-Memo Reconciliation — ${period}`,
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
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🔎 Distributor Credit-Memo Reconciliation</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>{period} · Distributor &quot;Weekly Incentive Credit&quot; memos vs MI + ATU earned (Xfinity excluded)</p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {msg && <span style={{ fontSize: 12, color: 'var(--text2)', maxWidth: 320 }}>{msg}</span>}
          {data && <><ExportButtons payload={buildPayload} /><SendReportButton exportPayload={buildPayload} compact /></>}
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
          No Distributor credit memos loaded for {period}. Click <strong>Scrape credit memos</strong> above (uses the Distributor sweep credentials).
        </div>
      ) : (
        <>
          {cw && (
            <div className="card" style={{ padding: 18, marginBottom: 16 }}>
              <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 10 }}>Company-wide (authoritative)</div>
              <div style={{ display: 'flex', gap: 30, flexWrap: 'wrap', alignItems: 'center' }}>
                <Tile label="Distributor credit memos" v={cw.memo_total} />
                <Tile label="MI + ATU earned" v={cw.mi_atu_total} />
                <Tile label="Difference" v={cw.diff} signed />
                <span style={{ fontSize: 13, padding: '4px 12px', borderRadius: 999, fontWeight: 600, background: badge(cw.status).bg, color: badge(cw.status).c }}>{badge(cw.status).t}</span>
              </div>
              {/* A link per figure in the strip. The STATUS chip gets none: it is a verdict against
                  the tolerance in the box above, not a folded figure (§CORE-17.3h's neighbour), and
                  the figure it judges is the difference, which does drill. */}
              <DrillTileLinks
                drills={drill.dl('memo_total', 'the stores behind the credit memos',
                  'mi_atu_total', 'the stores behind MI + ATU earned',
                  'diff', 'the stores behind the difference')}
                open={drill.open} onToggle={drill.toggle} />
              {drill.panel && <DrillDownPanel panel={drill.panel} extraColumns={RECON_DRILL_COLUMNS} />}
              {cw.attributed_pct != null && (
                <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 8 }}>
                  Per-store attribution: <strong style={{ color: cw.attributed_pct >= 90 ? 'var(--c-fg-166534)' : cw.attributed_pct >= 70 ? 'var(--c-fg-854d0e)' : 'var(--c-fg-991b1b)' }}>{cw.attributed_pct}%</strong> of MI+ATU $ attributed to a store by rep name
                  {cw.mi_unattributed > 0 && <> · {fmt(cw.mi_unattributed)} unattributed (still counted company-wide)</>}.
                  {cw.top_unattributed_reps?.length > 0 && (
                    <>
                      {' '}<button onClick={() => setOpen(o => ({ ...o, __unattr: !o.__unattr }))} style={{ border: 'none', background: 'none', color: 'var(--accent, var(--c-fg-2563eb))', cursor: 'pointer', fontSize: 12, padding: 0 }}>
                        {open.__unattr ? 'hide' : 'show'} unmatched reps ▾
                      </button>
                      {open.__unattr && (
                        <div style={{ marginTop: 6, display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                          {cw.top_unattributed_reps.map((u: any) => <span key={u.rep}>{u.rep}: <strong>{fmt(u.amount)}</strong></span>)}
                          <span style={{ color: 'var(--text3)' }}>— add these as aliases on <a href="/commcalc/targets/rep-map" style={{ color: 'var(--accent,var(--c-fg-2563eb))' }}>Rep Map</a> to attribute them.</span>
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
                          <td style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 600, color: r.diff < 0 ? 'var(--c-fg-dc2626)' : r.diff > 0 ? 'var(--c-fg-854d0e)' : 'var(--text3)' }}>{fmt(r.diff)}</td>
                          <td style={{ padding: '8px 12px', textAlign: 'center' }}><span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 999, fontWeight: 600, background: badge(r.status).bg, color: badge(r.status).c }}>{badge(r.status).t}</span></td>
                        </tr>
                        {expandable && open[r.store] && (
                          <tr style={{ background: 'var(--c-bg-fafbfc)' }}>
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
      <div style={{ fontSize: 22, fontWeight: 700, color: signed ? (v < 0 ? 'var(--c-fg-dc2626)' : v > 0 ? 'var(--c-fg-854d0e)' : 'var(--text)') : 'var(--text)' }}>{fmt(v || 0)}</div>
    </div>
  )
}
