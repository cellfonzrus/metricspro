'use client'
import { useState, useEffect } from 'react'
import Link from 'next/link'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

export default function AccountsDashboard() {
  const { period } = usePeriod()
  const [data, setData] = useState<any>({ computed: false, scopes: [], companies: [] })
  const [loading, setLoading] = useState(true)
  const [computing, setComputing] = useState(false)
  const [msg, setMsg] = useState('')
  const [health, setHealth] = useState<any>({})

  function load() {
    setLoading(true)
    Promise.all([
      api(`/api/v1/account/overview/${encodeURIComponent(period)}?org_id=${ORG_ID}`).catch(() => ({ computed: false, scopes: [] })),
      api(`/api/v1/account/health`).catch(() => ({})),
    ]).then(([o, h]: any) => { setData(o); setHealth(h) }).catch(console.error).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [period])

  async function compute() {
    setComputing(true); setMsg('Building the chart of accounts + statements…')
    try {
      const r = await api(`/api/v1/account/compute/${encodeURIComponent(period)}?org_id=${ORG_ID}`, { method: 'POST' })
      setMsg(`Computed ${r.snapshots} snapshots across ${r.scopes} scopes (${r.companies} companies, ${r.stores} stores) — engine: ${r.engine}.`)
      load()
    } catch (e: any) { setMsg('Compute failed: ' + (e?.message || e)) }
    setComputing(false)
  }

  const consolidated = data.scopes?.find((s: any) => s.scope_key === 'consolidated')
  const companyScopes = (data.scopes || []).filter((s: any) => s.scope_key?.startsWith('company:'))
  const storeScopes = (data.scopes || []).filter((s: any) => s.scope_key?.startsWith('store:'))

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>💼 Account Module</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {period} · P&amp;L + Balance Sheet, per company &amp; consolidated · cash basis
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {msg && <span style={{ fontSize: 12, color: 'var(--text2)', maxWidth: 380 }}>{msg}</span>}
          <button className="btn btn-primary" onClick={compute} disabled={computing}>
            {computing ? '⏳ Computing…' : '⚙️ Compute statements'}
          </button>
        </div>
      </div>

      {!health.engine_configured && (
        <div className="card" style={{ padding: 12, marginBottom: 16, background: '#fffbeb', border: '1px solid #fde68a', fontSize: 13, color: '#92400e' }}>
          ⚠️ The Claude narrative engine is not configured — statements compute with exact deterministic numbers, but without the written analysis. Set <code>ANTHROPIC_API_KEY</code> on the backend to enable narratives.
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : !data.computed ? (
        <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>
          No statements computed for {period} yet. Click <strong>Compute statements</strong> above.
          <div style={{ marginTop: 8, fontSize: 13 }}>First, assign stores to companies on the <Link href="/accounts/companies">Companies</Link> page.</div>
        </div>
      ) : (
        <>
          {consolidated && (
            <div className="card" style={{ padding: 18, marginBottom: 18 }}>
              <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 10 }}>Consolidated (all companies)</div>
              <div style={{ display: 'flex', gap: 28, flexWrap: 'wrap' }}>
                <Tile label="Revenue" v={consolidated.revenue} />
                <Tile label="Gross Profit" v={consolidated.gross_profit} />
                <Tile label="Net Income" v={consolidated.net_income} accent />
                <Tile label="Total Assets" v={consolidated.assets} />
                <div style={{ alignSelf: 'center' }}>
                  <span style={{ fontSize: 12, padding: '3px 9px', borderRadius: 999, fontWeight: 600,
                    background: consolidated.balanced ? '#dcfce7' : '#fee2e2', color: consolidated.balanced ? '#166534' : '#991b1b' }}>
                    {consolidated.balanced ? '✓ Balance sheet balances' : '⚠ Not balanced — enter cash/opening balances'}
                  </span>
                </div>
              </div>
              <div style={{ marginTop: 12, display: 'flex', gap: 10 }}>
                <Link className="btn" href={`/accounts/pl?scope=consolidated`}>📈 View P&amp;L</Link>
                <Link className="btn" href={`/accounts/balance-sheet?scope=consolidated`}>⚖️ View Balance Sheet</Link>
              </div>
            </div>
          )}

          {companyScopes.length > 0 && <ScopeTable title="By Company" rows={companyScopes} />}
          {storeScopes.length > 0 && <ScopeTable title="By Store" rows={storeScopes} />}
        </>
      )}
    </div>
  )
}

function Tile({ label, v, accent }: { label: string; v: number; accent?: boolean }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color: accent ? (v >= 0 ? 'var(--green, #16a34a)' : 'var(--red, #dc2626)') : 'var(--text)' }}>{fmt(v || 0)}</div>
    </div>
  )
}

function ScopeTable({ title, rows }: { title: string; rows: any[] }) {
  return (
    <div className="card" style={{ padding: 0, marginBottom: 18, overflow: 'hidden' }}>
      <div style={{ padding: '10px 16px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>{title}</div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 680 }}>
          <thead>
            <tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
              <th style={{ textAlign: 'left', padding: '8px 16px' }}>Scope</th>
              <th style={{ textAlign: 'right', padding: '8px 12px' }}>Revenue</th>
              <th style={{ textAlign: 'right', padding: '8px 12px' }}>Gross Profit</th>
              <th style={{ textAlign: 'right', padding: '8px 12px' }}>Net Income</th>
              <th style={{ textAlign: 'right', padding: '8px 12px' }}>Assets</th>
              <th style={{ textAlign: 'center', padding: '8px 12px' }}>Bal.</th>
              <th style={{ padding: '8px 16px' }}></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s: any) => (
              <tr key={s.scope_key} style={{ borderTop: '1px solid var(--border)', fontSize: 13 }}>
                <td style={{ padding: '8px 16px', fontWeight: 500 }}>{(s.scope_label || s.scope_key).substring(0, 48)}</td>
                <td style={{ padding: '8px 12px', textAlign: 'right' }}>{fmt(s.revenue || 0)}</td>
                <td style={{ padding: '8px 12px', textAlign: 'right' }}>{fmt(s.gross_profit || 0)}</td>
                <td style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 600, color: (s.net_income || 0) >= 0 ? '#16a34a' : '#dc2626' }}>{fmt(s.net_income || 0)}</td>
                <td style={{ padding: '8px 12px', textAlign: 'right' }}>{fmt(s.assets || 0)}</td>
                <td style={{ padding: '8px 12px', textAlign: 'center' }}>{s.balanced ? '✓' : '⚠'}</td>
                <td style={{ padding: '8px 16px', whiteSpace: 'nowrap' }}>
                  <Link href={`/accounts/pl?scope=${encodeURIComponent(s.scope_key)}`} style={{ fontSize: 12, marginRight: 10 }}>P&amp;L</Link>
                  <Link href={`/accounts/balance-sheet?scope=${encodeURIComponent(s.scope_key)}`} style={{ fontSize: 12 }}>BS</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
