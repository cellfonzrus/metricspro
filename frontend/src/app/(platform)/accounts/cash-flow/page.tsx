'use client'
import { useState, useEffect, Suspense } from 'react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import ReportExportBar from '@/components/ReportExportBar'
import { StalenessBanner } from '../_components/StalenessBanner'
import type { ExportSheet } from '@/lib/export'

// Cash Flow statement (roadmap Phase 2 UI; backend since PR #179): the stored DERIVED cash-flow
// snapshot (statement_type 'cash_flow', indirect method over the period's balance-sheet deltas)
// written by statement_engine.compute_and_store next to the P&L / BS. DISPLAY-ONLY — figures come
// straight from GET /account/cash-flow/{period}; the tie-out is REPORTED, never papered over.
const SEC_ICON: Record<string, string> = { operating: '⚙️', investing: '🏗️', financing: '🏦' }

function CFInner() {
  const { period } = usePeriod()
  const sp = useSearchParams()
  const [scope, setScope] = useState(sp.get('scope') || 'consolidated')
  const [scopes, setScopes] = useState<any[]>([])
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    api(`/api/v1/account/overview/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      .then((o: any) => setScopes(o.scopes || [])).catch(() => setScopes([]))
  }, [period, reloadKey])
  useEffect(() => {
    setLoading(true)
    api(`/api/v1/account/cash-flow/${encodeURIComponent(period)}?scope=${encodeURIComponent(scope)}&org_id=${ORG_ID}`)
      .then(setData).catch(console.error).finally(() => setLoading(false))
  }, [period, scope, reloadKey])

  const st = data?.statement

  function sheets(): ExportSheet[] {
    const rows: any[] = []
    ;(st?.sections || []).forEach((s: any) => {
      s.lines.forEach((l: any) => rows.push({ section: s.name, line: l.label, amount: l.amount }))
      rows.push({ section: s.name, line: `  Total ${s.name}`, amount: s.subtotal })
    })
    rows.push({ section: 'Cash', line: 'Implied change in cash', amount: st?.implied_cash_change })
    rows.push({ section: 'Cash', line: 'Cash & equivalents — beginning', amount: st?.cash_begin })
    rows.push({ section: 'Cash', line: 'Cash & equivalents — ending', amount: st?.cash_end })
    rows.push({ section: 'Cash', line: 'Reported change (from BS cash lines)', amount: st?.cash_delta_reported })
    rows.push({ section: 'Cash', line: 'Tie-out delta (implied − reported)', amount: st?.tie_delta })
    return [{ name: 'Cash Flow', rows, columns: [
      { header: 'Section', get: (r: any) => r.section },
      { header: 'Line', get: (r: any) => r.line },
      { header: 'Amount', get: (r: any) => r.amount, money: true },
    ] }]
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>💧 Cash Flow</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {period} · derived (indirect method over balance-sheet deltas) · {st?.scope_label || scope}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <select className="select" value={scope} onChange={e => setScope(e.target.value)}>
            {scopes.map((s: any) => <option key={s.scope_key} value={s.scope_key}>{(s.scope_label || s.scope_key).substring(0, 50)}</option>)}
            {!scopes.find((s: any) => s.scope_key === scope) && <option value={scope}>{scope}</option>}
          </select>
          <Link className="btn" href="/accounts/balance-sheet" style={{ fontSize: 13 }}>⚖️ Balance Sheet</Link>
          {st && <ReportExportBar
            title={`Cash Flow — ${st?.scope_label || scope}`} subtitle={`${period} · derived (indirect method)`}
            filename={`cash-flow-${scope.replace(/[^a-z0-9]+/gi, '-')}-${period.replace(/\s+/g, '-')}`}
            sheets={sheets()} />}
        </div>
      </div>

      <StalenessBanner period={period} computed={data?.computed} computedAt={data?.computed_at}
        newestIngestAt={data?.newest_ingest_at} stale={data?.stale} onRecomputed={() => setReloadKey(k => k + 1)} />

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : !data?.computed ? (
        <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>
          Not computed for this period/scope. Go to the <Link href="/accounts">Account dashboard</Link> and click <strong>Compute statements</strong> (cash-flow snapshots exist for periods computed after 2026-09-02).
        </div>
      ) : (
        <div style={{ display: 'grid', gap: 16 }}>
          {/* tie-out banner: honest, never papered over */}
          <div className="card" style={{ padding: 12, fontSize: 13, display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center',
            background: st.tied ? '#f0fdf4' : '#fffbeb', border: `1px solid ${st.tied ? '#bbf7d0' : '#fde68a'}` }}>
            <span>{st.tied ? '✅ Ties out' : '⚠️ Tie-out gap'}</span>
            <span>Implied change: <b>{fmt(st.implied_cash_change)}</b></span>
            <span>Reported change (BS cash lines): <b>{fmt(st.cash_delta_reported)}</b></span>
            {!st.tied && <span>Delta: <b>{fmt(st.tie_delta)}</b> — usually the manual Cash / bank line not keyed for one of the two months (<Link href="/accounts/journal">Journal</Link>).</span>}
            {st.comparative === false && <span style={{ color: 'var(--text2)' }}>First computed period — changes equal the full balances.</span>}
          </div>

          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <tbody>
                {(st.sections || []).map((s: any) => (
                  <SectionRows key={s.type} s={s} />
                ))}
                <tr style={{ borderTop: '2px solid var(--border)', background: 'var(--surface2, #f1f5f9)' }}>
                  <td style={{ padding: '10px 16px', fontWeight: 700, fontSize: 14 }}>Implied change in cash</td>
                  <td style={{ padding: '10px 16px', textAlign: 'right', fontWeight: 700, fontSize: 14 }}>{fmt(st.implied_cash_change)}</td>
                </tr>
                <tr><td style={{ padding: '7px 16px', fontSize: 13 }}>Cash &amp; equivalents — beginning</td>
                  <td style={{ padding: '7px 16px', textAlign: 'right', fontSize: 13 }}>{fmt(st.cash_begin)}</td></tr>
                <tr style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '7px 16px', fontSize: 13, fontWeight: 600 }}>Cash &amp; equivalents — ending</td>
                  <td style={{ padding: '7px 16px', textAlign: 'right', fontSize: 13, fontWeight: 600 }}>{fmt(st.cash_end)}</td></tr>
              </tbody>
            </table>
          </div>
          {st.notes?.length > 0 && <div style={{ fontSize: 12, color: 'var(--text3)' }}>{st.notes.map((n: string, i: number) => <div key={i}>· {n}</div>)}</div>}
        </div>
      )}
    </div>
  )
}

function SectionRows({ s }: { s: any }) {
  return (
    <>
      <tr style={{ background: 'var(--surface2, #f8fafc)' }}>
        <td colSpan={2} style={{ padding: '8px 16px', fontWeight: 700, fontSize: 12, textTransform: 'uppercase', color: 'var(--text2)' }}>
          {SEC_ICON[s.type] || ''} {s.name}
        </td>
      </tr>
      {s.lines.map((l: any) => (
        <tr key={s.type + ':' + l.key} style={{ borderTop: '1px solid var(--border)' }}>
          <td style={{ padding: '7px 16px', fontSize: 13 }}>{l.label}</td>
          <td style={{ padding: '7px 16px', textAlign: 'right', fontSize: 13, color: l.amount ? 'var(--text)' : 'var(--text3)' }}>{l.amount ? fmt(l.amount) : '—'}</td>
        </tr>
      ))}
      <tr style={{ borderTop: '1px solid var(--border)' }}>
        <td style={{ padding: '7px 16px', fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Net cash — {s.name.toLowerCase()}</td>
        <td style={{ padding: '7px 16px', textAlign: 'right', fontSize: 13, fontWeight: 600 }}>{fmt(s.subtotal)}</td>
      </tr>
    </>
  )
}

export default function CashFlowPage() {
  return <Suspense fallback={<div style={{ padding: 60, textAlign: 'center' }}><div className="spinner" /></div>}><CFInner /></Suspense>
}
