'use client'
import { useState, useEffect, useMemo, Suspense } from 'react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { api, fmt, ORG_ID } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { usePeriod } from '@/lib/period-context'
import ReportExportBar from '@/components/ReportExportBar'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'
import type { EntityOption } from '@/components/EntityPicker'
import type { ExportSheet } from '@/lib/export'
import { StalenessBanner } from '../_components/StalenessBanner'
import { statementInfoSheet, statementSubtitle, type StatementMeta } from '../_components/statementExport'

const SEC: Record<string, string> = { asset: 'Assets', liability: 'Liabilities', equity: 'Equity' }

function BSInner() {
  const { period } = usePeriod()
  const sp = useSearchParams()
  const [scope, setScope] = useState(sp.get('scope') || 'consolidated')
  const [scopes, setScopes] = useState<any[]>([])
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [reloadKey, setReloadKey] = useState(0)
  const [inv, setInv] = useState<any[]>([])   // per-store inventory backing the BS inventory line (for the detail sheet)
  // RULE FIVE (§3d) standard store/market filter — see the P&L page for the full rationale. Period =
  // section switcher (usePeriod); rep n/a at statement grain; so only stores + markets shown. Filter
  // active → the read endpoint re-attributes the BS to the selected store(s) (sums per-store snapshots).
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const [fopts, setFopts] = useState<{ stores?: any[]; markets?: string[] }>({})
  const filterActive = filt.stores.length > 0 || filt.markets.length > 0
  const filtKey = `${filt.stores.join('|')}|${filt.markets.join('|')}`

  useEffect(() => {
    apiCached(`/api/v1/core/filter-options?org_id=${ORG_ID}`, LOOKUP).then((d: any) => setFopts(d || {})).catch(() => setFopts({}))
  }, [])
  useEffect(() => {
    api(`/api/v1/account/overview/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      .then((o: any) => setScopes(o.scopes || [])).catch(() => setScopes([]))
  }, [period, reloadKey])
  useEffect(() => {
    api(`/api/v1/account/inventory-values?org_id=${ORG_ID}`)
      .then((d: any) => setInv(d.rows || [])).catch(() => setInv([]))
  }, [reloadKey])
  useEffect(() => {
    setLoading(true)
    const q = `scope=${encodeURIComponent(scope)}&org_id=${ORG_ID}`
      + (filterActive ? `&stores=${encodeURIComponent(filt.stores.join('|'))}&markets=${encodeURIComponent(filt.markets.join('|'))}` : '')
    api(`/api/v1/account/balance-sheet/${encodeURIComponent(period)}?${q}`)
      .then(setData).catch(console.error).finally(() => setLoading(false))
  }, [period, scope, reloadKey, filtKey])   // eslint-disable-line react-hooks/exhaustive-deps

  const storeMarket = useMemo(() => {
    const m: Record<string, string> = {}
    ;(fopts.stores || []).forEach((s: any) => { if (s.store && s.market) m[s.store] = s.market })
    return m
  }, [fopts])
  const storeOpts: EntityOption[] = useMemo(() =>
    scopes.filter((s: any) => String(s.scope_key || '').startsWith('store:'))
      .map((s: any) => { const a = String(s.scope_key).slice('store:'.length); return { id: a, label: a, sublabel: storeMarket[a] || undefined } }),
    [scopes, storeMarket])
  const marketOpts: string[] = useMemo(() => fopts.markets || [], [fopts])

  const st = data?.statement
  const sec = (t: string) => (st?.sections || []).find((s: any) => s.type === t)

  // RULE FOUR (§3c) export. DISPLAY/EXPORT ONLY — figures come straight from the computed snapshot.
  function bsMeta(): StatementMeta {
    return {
      reportName: 'Balance Sheet', scopeLabel: st?.scope_label || scope, period, basis: 'Point-in-time',
      computed: !!data?.computed, computedAt: data?.computed_at,
      newestIngestAt: data?.newest_ingest_at, stale: !!data?.stale,
      extra: st ? [
        ['Balanced', st.balanced ? 'Yes' : 'No'],
        ['Imbalance (Assets − L+E)', fmt(st.imbalance || 0)],
      ] : undefined,
    }
  }
  function bsSheets(): ExportSheet[] {
    const rows: any[] = []
    ;(st?.sections || []).forEach((s: any) => {
      s.lines.forEach((l: any) => rows.push({ section: SEC[s.type] || s.type, line: l.label, amount: l.amount }))
      rows.push({ section: SEC[s.type] || s.type, line: `  Total ${SEC[s.type] || s.type}`, amount: s.subtotal })
    })
    const sheets: ExportSheet[] = [
      statementInfoSheet(bsMeta()),                                   // self-describing cover (Excel too)
      { name: 'Balance Sheet', rows, columns: [
        { header: 'Section', get: (r: any) => r.section },
        { header: 'Line', get: (r: any) => r.line },
        { header: 'Amount', get: (r: any) => r.amount, money: true },
      ] },
    ]
    // Multi-sheet: the per-store inventory backing the BS inventory line (effective = manual override
    // if set, else swept b2bsoft value). Narrowed to the selected store(s) — the standard store/market
    // filter (§3d) when active, else the single store scope — so the export mirrors what's on screen.
    const invRows = data?.filtered
      ? inv.filter((r: any) => (data.filtered_stores || []).includes(r.store))
      : scope.startsWith('store:')
        ? inv.filter((r: any) => r.store === scope.slice('store:'.length))
        : inv
    if (invRows.length > 0) {
      sheets.push({ name: 'Inventory detail', rows: invRows, columns: [
        { header: 'Store', get: (r: any) => r.store },
        { header: 'Swept (b2bsoft)', get: (r: any) => r.swept_value, money: true },
        { header: 'Manual override', get: (r: any) => r.manual_value, money: true },
        { header: 'Effective (on BS)', get: (r: any) => r.effective, money: true },
        { header: 'Source', get: (r: any) => r.effective_source },
        { header: 'As of', get: (r: any) => r.as_of_date },
      ] })
    }
    return sheets
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>⚖️ Balance Sheet</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>{period} · point-in-time · {st?.scope_label || scope}</p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <select className="select" value={scope} onChange={e => setScope(e.target.value)}>
            {scopes.map((s: any) => <option key={s.scope_key} value={s.scope_key}>{(s.scope_label || s.scope_key).substring(0, 50)}</option>)}
            {!scopes.find((s: any) => s.scope_key === scope) && <option value={scope}>{scope}</option>}
          </select>
          <Link className="btn" href="/accounts/inventory" style={{ fontSize: 13 }}>📦 Edit inventory</Link>
          <Link className="btn" href="/accounts/cash-flow" style={{ fontSize: 13 }}>💧 Cash Flow</Link>
          {st && <ReportExportBar
            title={`Balance Sheet — ${st?.scope_label || scope}`}
            subtitle={statementSubtitle(bsMeta())}
            filename={`balance-sheet-${(data?.filtered ? 'filtered' : scope).replace(/[^a-z0-9]+/gi, '-')}-${period.replace(/\s+/g, '-')}`}
            sheets={bsSheets()} />}
        </div>
      </div>

      {/* RULE FIVE (§3d) standard filter bar — stores + markets. Period = section switcher; rep n/a. */}
      <StandardFilterBar value={filt} onChange={setFilt} show={{ period: false, reps: false }}
        periodMode="none" storeOptions={storeOpts} marketOptions={marketOpts} />
      {data?.filtered && (
        <div style={{ fontSize: 12, color: 'var(--text2)', margin: '-6px 0 12px' }}>
          Filtered to <strong>{data.matched_stores}</strong> store(s){data.filtered_markets?.length ? <> · markets: {data.filtered_markets.join(', ')}</> : null}.
          Company-wide lines (cash / opening balances / MI/ATU residual) read $0 — see the Consolidated view; a store subset rarely balances on its own.
        </div>
      )}

      <StalenessBanner period={period} computed={data?.computed} computedAt={data?.computed_at}
        newestIngestAt={data?.newest_ingest_at} stale={data?.stale} onRecomputed={() => setReloadKey(k => k + 1)} />

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : !data?.computed ? (
        <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>
          Not computed for this period/scope. Go to the <Link href="/accounts">Account dashboard</Link> and click <strong>Compute statements</strong>.
        </div>
      ) : (
        <div style={{ display: 'grid', gap: 16 }}>
          {!st.balanced && (
            <div className="card" style={{ padding: 12, background: '#fef2f2', border: '1px solid #fecaca', fontSize: 13, color: '#991b1b' }}>
              ⚠ Assets ≠ Liabilities + Equity by <strong>{fmt(st.imbalance)}</strong>. Enter cash / opening balances on the <Link href="/accounts/journal">Journal</Link> page to balance.
            </div>
          )}
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <tbody>
                {['asset', 'liability', 'equity'].map(t => <Section key={t} s={sec(t)} open={open} setOpen={setOpen} />)}
                <tr style={{ borderTop: '2px solid var(--border)', background: 'var(--surface2, #f1f5f9)' }}>
                  <td style={{ padding: '10px 16px', fontWeight: 700, fontSize: 14 }}>Liabilities + Equity</td>
                  <td style={{ padding: '10px 16px', textAlign: 'right', fontWeight: 700, fontSize: 14 }}>{fmt((st.liabilities_total || 0) + (st.equity_total || 0))}</td>
                </tr>
              </tbody>
            </table>
          </div>
          {data.narrative && (
            <div className="card" style={{ padding: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6, color: 'var(--text2)' }}>Narrative</div>
              <div style={{ fontSize: 14, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>{data.narrative}</div>
            </div>
          )}
          {st.notes?.length > 0 && <div style={{ fontSize: 12, color: 'var(--text3)' }}>{st.notes.map((n: string, i: number) => <div key={i}>· {n}</div>)}</div>}
          {/* Manual-entry grain conflicts (mig 954): a hand-entered total that is SMALLER than the
              per-store/per-company rows inside it cannot be booked as negative cash — the shortfall
              is suppressed and surfaced here rather than silently dropped. */}
          {(st.journal_grains?.conflicts || []).length > 0 && (
            <div style={{ fontSize: 12.5, color: '#991b1b', marginTop: 6 }}>
              {st.journal_grains.conflicts.map((c: any, i: number) => (
                <div key={i}>⚠ “{c.line}”: the {c.grain === 'tenant' ? 'tenant total' : 'company total'} you
                  entered ({fmt(c.stated)}) is less than the more detailed rows inside it ({fmt(c.finer)}).
                  {' '}{fmt(c.suppressed)} could not be placed — fix the entries on the Journal page.</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function Section({ s, open, setOpen }: { s: any; open: Record<string, boolean>; setOpen: any }) {
  if (!s) return null
  return (
    <>
      <tr style={{ background: 'var(--surface2, #f8fafc)' }}>
        <td colSpan={2} style={{ padding: '8px 16px', fontWeight: 700, fontSize: 12, textTransform: 'uppercase', color: 'var(--text2)' }}>{SEC[s.type] || s.type}</td>
      </tr>
      {s.lines.map((l: any) => {
        const hasDetail = l.detail && Object.keys(l.detail).length > 0
        const k = s.type + ':' + l.key
        return (
          <>
            <tr key={k} style={{ borderTop: '1px solid var(--border)' }}>
              <td style={{ padding: '7px 16px', fontSize: 13 }}>
                {hasDetail && <button onClick={() => setOpen((o: any) => ({ ...o, [k]: !o[k] }))} style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 11, marginRight: 6, padding: 0 }}>{open[k] ? '▾' : '▸'}</button>}
                {l.label}
                {l.kind === 'manual' && <span style={{ marginLeft: 6, fontSize: 10, color: '#92400e', background: '#fef3c7', padding: '1px 5px', borderRadius: 999 }}>manual</span>}
                {l.kind === 'computed' && <span style={{ marginLeft: 6, fontSize: 10, color: '#3730a3', background: '#e0e7ff', padding: '1px 5px', borderRadius: 999 }}>computed</span>}
                {/* Same "declared, not measured" passthrough as the P&L. `engine._assemble` is shared
                    by both statements, so a note set on a BS line would otherwise render nowhere. */}
                {l.note && (
                  <div style={{ marginTop: 3, fontSize: 11.5, lineHeight: 1.45, color: 'var(--text3)', maxWidth: 620 }}>
                    <span style={{ marginRight: 5, fontSize: 10, color: '#92400e', background: '#fef3c7', padding: '1px 5px', borderRadius: 999, whiteSpace: 'nowrap' }}>not measured</span>
                    {l.note}
                  </div>
                )}
              </td>
              <td style={{ padding: '7px 16px', textAlign: 'right', fontSize: 13, color: l.amount ? 'var(--text)' : 'var(--text3)' }}>{l.amount ? fmt(l.amount) : '—'}</td>
            </tr>
            {hasDetail && open[k] && Object.entries(l.detail).map(([dl, dv]: any) => (
              <tr key={k + ':' + dl} style={{ background: '#fafbfc' }}>
                <td style={{ padding: '4px 16px 4px 40px', fontSize: 12, color: 'var(--text2)' }}>↳ {dl}</td>
                <td style={{ padding: '4px 16px', textAlign: 'right', fontSize: 12, color: 'var(--text2)' }}>{fmt(dv)}</td>
              </tr>
            ))}
          </>
        )
      })}
      <tr style={{ borderTop: '1px solid var(--border)' }}>
        <td style={{ padding: '7px 16px', fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Total {SEC[s.type] || s.type}</td>
        <td style={{ padding: '7px 16px', textAlign: 'right', fontSize: 13, fontWeight: 600 }}>{fmt(s.subtotal)}</td>
      </tr>
    </>
  )
}

export default function BSPage() {
  return <Suspense fallback={<div style={{ padding: 60, textAlign: 'center' }}><div className="spinner" /></div>}><BSInner /></Suspense>
}
