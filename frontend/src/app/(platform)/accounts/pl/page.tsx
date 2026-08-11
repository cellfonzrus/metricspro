'use client'
import { useState, useEffect, useMemo, Suspense } from 'react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import ReportExportBar from '@/components/ReportExportBar'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'
import type { EntityOption } from '@/components/EntityPicker'
import type { ExportSheet } from '@/lib/export'
import { StalenessBanner } from '../_components/StalenessBanner'
import { statementInfoSheet, statementSubtitle, type StatementMeta } from '../_components/statementExport'

const SECTION_TITLE: Record<string, string> = { revenue: 'Revenue', cogs: 'Cost of Goods Sold', opex: 'Operating Expenses', other: 'Other' }

function PLInner() {
  const { period } = usePeriod()
  const sp = useSearchParams()
  const [scope, setScope] = useState(sp.get('scope') || 'consolidated')
  const [scopes, setScopes] = useState<any[]>([])
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [reloadKey, setReloadKey] = useState(0)
  // RULE FIVE (§3d) standard store/market filter. Period comes from the section-wide period switcher
  // (usePeriod) and rep is n/a at statement grain, so only stores + markets are shown (deviations
  // documented in the handoff). When a store/market filter is active the READ endpoint re-attributes
  // the statement to the selected store(s) (sums the per-store snapshots); with NO filter the page is
  // byte-identical to before (the stored snapshot for the chosen company scope).
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const [fopts, setFopts] = useState<{ stores?: any[]; markets?: string[] }>({})
  const filterActive = filt.stores.length > 0 || filt.markets.length > 0
  const filtKey = `${filt.stores.join('|')}|${filt.markets.join('|')}`

  useEffect(() => {
    api(`/api/v1/core/filter-options?org_id=${ORG_ID}`).then((d: any) => setFopts(d || {})).catch(() => setFopts({}))
  }, [])

  useEffect(() => {
    api(`/api/v1/account/overview/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      .then((o: any) => setScopes(o.scopes || [])).catch(() => setScopes([]))
  }, [period, reloadKey])

  useEffect(() => {
    setLoading(true)
    const q = `scope=${encodeURIComponent(scope)}&org_id=${ORG_ID}`
      + (filterActive ? `&stores=${encodeURIComponent(filt.stores.join('|'))}&markets=${encodeURIComponent(filt.markets.join('|'))}` : '')
    api(`/api/v1/account/pl/${encodeURIComponent(period)}?${q}`)
      .then(setData).catch(console.error).finally(() => setLoading(false))
  }, [period, scope, reloadKey, filtKey])   // eslint-disable-line react-hooks/exhaustive-deps

  // Store options = the period's per-store scopes (guaranteed selectable, canonical addresses that
  // exactly match the backend store: scope keys); market sublabel + market options from the org
  // filter roster. Both org-scoped, pick-don't-type (§3b).
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
  function plMeta(): StatementMeta {
    return {
      reportName: 'Profit & Loss', scopeLabel: st?.scope_label || scope, period, basis: 'Cash basis',
      computed: !!data?.computed, computedAt: data?.computed_at,
      newestIngestAt: data?.newest_ingest_at, stale: !!data?.stale,
    }
  }
  function plSheets(): ExportSheet[] {
    const rows: any[] = []
    ;(st?.sections || []).forEach((s: any) => {
      s.lines.forEach((l: any) => rows.push({ section: SECTION_TITLE[s.type] || s.type, line: l.label, amount: l.amount }))
      rows.push({ section: SECTION_TITLE[s.type] || s.type, line: `  Subtotal — ${SECTION_TITLE[s.type] || s.type}`, amount: s.subtotal })
    })
    rows.push({ section: 'Totals', line: 'Gross Profit', amount: st?.gross_profit })
    rows.push({ section: 'Totals', line: 'Net Operating Income', amount: st?.net_operating_income })
    rows.push({ section: 'Totals', line: 'Net Income', amount: st?.net_income })
    const sheets: ExportSheet[] = [
      statementInfoSheet(plMeta()),                                   // self-describing cover (Excel too)
      { name: 'P&L', rows, columns: [
        { header: 'Section', get: (r: any) => r.section },
        { header: 'Line', get: (r: any) => r.line },
        { header: 'Amount', get: (r: any) => r.amount, money: true },
      ] },
    ]
    // Multi-sheet: a company-wide company/store breakdown for the SAME period — every computed scope
    // the dropdown offers — so one export answers "which store drove it". Numbers are the stored
    // per-scope snapshots (overview), untouched. When a store/market filter is active the breakdown is
    // narrowed to the SAME selected stores (what-you-see-is-what-exports, §3c).
    const breakdown = data?.filtered
      ? scopes.filter((s: any) => (data.filtered_stores || []).includes(String(s.scope_key || '').slice('store:'.length)))
      : scopes
    if (breakdown.length > 0) {
      sheets.push({ name: 'By Scope', rows: breakdown, columns: [
        { header: 'Scope', get: (r: any) => r.scope_label || r.scope_key },
        { header: 'Revenue', get: (r: any) => r.revenue, money: true },
        { header: 'Gross Profit', get: (r: any) => r.gross_profit, money: true },
        { header: 'Net Income', get: (r: any) => r.net_income, money: true },
      ] })
    }
    return sheets
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📈 Profit &amp; Loss</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>{period} · cash basis · {st?.scope_label || scope}</p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <select className="select" value={scope} onChange={e => setScope(e.target.value)}>
            {scopes.map((s: any) => <option key={s.scope_key} value={s.scope_key}>{(s.scope_label || s.scope_key).substring(0, 50)}</option>)}
            {!scopes.find((s: any) => s.scope_key === scope) && <option value={scope}>{scope}</option>}
          </select>
          {st && <ReportExportBar
            title={`Profit & Loss — ${st?.scope_label || scope}`}
            subtitle={statementSubtitle(plMeta())}
            filename={`pl-${(data?.filtered ? 'filtered' : scope).replace(/[^a-z0-9]+/gi, '-')}-${period.replace(/\s+/g, '-')}`}
            sheets={plSheets()} />}
        </div>
      </div>

      {/* RULE FIVE (§3d) standard filter bar — stores + markets. Period = section switcher; rep n/a. */}
      <StandardFilterBar value={filt} onChange={setFilt} show={{ period: false, reps: false }}
        periodMode="none" storeOptions={storeOpts} marketOptions={marketOpts} />
      {data?.filtered && (
        <div style={{ fontSize: 12, color: 'var(--text2)', margin: '-6px 0 12px' }}>
          Filtered to <strong>{data.matched_stores}</strong> store(s){data.filtered_markets?.length ? <> · markets: {data.filtered_markets.join(', ')}</> : null}.
          Company-wide lines (MI/ATU residual, carrier comp without a store) read $0 — see the Consolidated view.
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
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr)', gap: 16 }}>
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <tbody>
                {['revenue', 'cogs'].map(t => <Section key={t} s={sec(t)} open={open} setOpen={setOpen} />)}
                <TotalRow label="Gross Profit" v={st.gross_profit} strong />
                {['opex'].map(t => <Section key={t} s={sec(t)} open={open} setOpen={setOpen} />)}
                <TotalRow label="Net Operating Income" v={st.net_operating_income} strong />
                {['other'].map(t => <Section key={t} s={sec(t)} open={open} setOpen={setOpen} />)}
                <TotalRow label="Net Income" v={st.net_income} strong accent />
              </tbody>
            </table>
          </div>

          {data.narrative && (
            <div className="card" style={{ padding: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6, color: 'var(--text2)' }}>
                Narrative {data.model && data.model !== 'deterministic' ? `· ${data.model}` : ''}
              </div>
              <div style={{ fontSize: 14, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>{data.narrative}</div>
            </div>
          )}
          {st.notes?.length > 0 && (
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>
              {st.notes.map((n: string, i: number) => <div key={i}>· {n}</div>)}
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
        <td colSpan={2} style={{ padding: '8px 16px', fontWeight: 700, fontSize: 12, textTransform: 'uppercase', color: 'var(--text2)' }}>{SECTION_TITLE[s.type] || s.type}</td>
      </tr>
      {s.lines.map((l: any) => {
        const hasDetail = l.detail && Object.keys(l.detail).length > 0
        const k = s.type + ':' + l.key
        return (
          <>
            <tr key={k} style={{ borderTop: '1px solid var(--border)' }}>
              <td style={{ padding: '7px 16px', fontSize: 13 }}>
                {hasDetail && (
                  <button onClick={() => setOpen((o: any) => ({ ...o, [k]: !o[k] }))}
                    style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 11, marginRight: 6, padding: 0 }}>
                    {open[k] ? '▾' : '▸'}
                  </button>
                )}
                {l.label}
                {l.kind === 'manual' && <span style={{ marginLeft: 6, fontSize: 10, color: '#92400e', background: '#fef3c7', padding: '1px 5px', borderRadius: 999 }}>manual</span>}
                {l.kind === 'auto*' && <span style={{ marginLeft: 6, fontSize: 10, color: '#3730a3', background: '#e0e7ff', padding: '1px 5px', borderRadius: 999 }}>auto*</span>}
                {/* Owner ruling K3(b): a DECLARED zero must say so. A $0 with a reason is a different
                    statement from a $0 that was measured, and the reader cannot tell them apart from
                    the number. Only rendered when the backend actually attached a note. */}
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
        <td style={{ padding: '7px 16px', fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Subtotal — {SECTION_TITLE[s.type] || s.type}</td>
        <td style={{ padding: '7px 16px', textAlign: 'right', fontSize: 13, fontWeight: 600 }}>{fmt(s.subtotal)}</td>
      </tr>
    </>
  )
}

function TotalRow({ label, v, strong, accent }: { label: string; v: number; strong?: boolean; accent?: boolean }) {
  return (
    <tr style={{ borderTop: '2px solid var(--border)', background: accent ? 'var(--surface2, #f1f5f9)' : 'transparent' }}>
      <td style={{ padding: '10px 16px', fontWeight: strong ? 700 : 500, fontSize: 14 }}>{label}</td>
      <td style={{ padding: '10px 16px', textAlign: 'right', fontWeight: 700, fontSize: 14, color: accent ? (v >= 0 ? '#16a34a' : '#dc2626') : 'var(--text)' }}>{fmt(v || 0)}</td>
    </tr>
  )
}

export default function PLPage() {
  return <Suspense fallback={<div style={{ padding: 60, textAlign: 'center' }}><div className="spinner" /></div>}><PLInner /></Suspense>
}
