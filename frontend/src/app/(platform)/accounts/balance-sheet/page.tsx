'use client'
import { useState, useEffect, Suspense } from 'react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { StalenessBanner } from '../_components/StalenessBanner'

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

  useEffect(() => {
    api(`/api/v1/account/overview/${encodeURIComponent(period)}?org_id=${ORG_ID}`)
      .then((o: any) => setScopes(o.scopes || [])).catch(() => setScopes([]))
  }, [period, reloadKey])
  useEffect(() => {
    setLoading(true)
    api(`/api/v1/account/balance-sheet/${encodeURIComponent(period)}?scope=${encodeURIComponent(scope)}&org_id=${ORG_ID}`)
      .then(setData).catch(console.error).finally(() => setLoading(false))
  }, [period, scope, reloadKey])

  const st = data?.statement
  const sec = (t: string) => (st?.sections || []).find((s: any) => s.type === t)

  function buildPayload(): ExportPayload {
    const rows: any[] = []
    ;(st?.sections || []).forEach((s: any) => {
      s.lines.forEach((l: any) => rows.push({ section: SEC[s.type] || s.type, line: l.label, amount: l.amount }))
      rows.push({ section: SEC[s.type] || s.type, line: `  Total ${SEC[s.type] || s.type}`, amount: s.subtotal })
    })
    return {
      title: `Balance Sheet — ${st?.scope_label || scope}`, subtitle: `${period} · point-in-time`,
      filename: `balance-sheet-${scope.replace(/[^a-z0-9]+/gi, '-')}-${period.replace(/\s+/g, '-')}`,
      sheets: [{ name: 'Balance Sheet', rows, columns: [
        { header: 'Section', get: (r: any) => r.section },
        { header: 'Line', get: (r: any) => r.line },
        { header: 'Amount', get: (r: any) => r.amount, money: true },
      ] }],
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>⚖️ Balance Sheet</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>{period} · point-in-time · {st?.scope_label || scope}</p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <select className="select" value={scope} onChange={e => setScope(e.target.value)}>
            {scopes.map((s: any) => <option key={s.scope_key} value={s.scope_key}>{(s.scope_label || s.scope_key).substring(0, 50)}</option>)}
            {!scopes.find((s: any) => s.scope_key === scope) && <option value={scope}>{scope}</option>}
          </select>
          <Link className="btn" href="/accounts/inventory" style={{ fontSize: 13 }}>📦 Edit inventory</Link>
          {st && <ExportButtons payload={buildPayload} />}
          {data?.computed && <SendReportButton reportKey="account_balance_sheet" filters={{ period, scope }} />}
        </div>
      </div>

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
