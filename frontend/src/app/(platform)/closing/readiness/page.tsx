'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'

// Closing readiness — self-diagnostic (2026-07-16 luxelink-parity audit). Surfaces the exact
// config/data gaps Daily Closing already degrades around SAFELY but SILENTLY (no stores mapped, no
// B2B sales source, no X-report ever imported, module not entitled, …) so an admin can see in one
// place why recon/gates look empty instead of discovering it one broken report at a time. Universal —
// the SAME checks run for every tenant; a fully-wired tenant (house/Boost today) just shows all green.
const SEV_STYLE: Record<string, { bg: string; fg: string; label: string }> = {
  critical: { bg: '#fdeaea', fg: '#b42318', label: '🔴 Critical' },
  warning: { bg: '#fef3e2', fg: '#b45309', label: '🟡 Warning' },
  info: { bg: '#eef2ff', fg: '#3730a3', label: '🔵 Info' },
}

export default function ClosingReadinessPage() {
  const [data, setData] = useState<any>(null)
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    api('/api/v1/closing/readiness').then(setData).catch((e: any) => setErr(e?.message || String(e)))
  }, [])
  useEffect(() => { load() }, [load])

  return (
    <div style={{ maxWidth: 760 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🩺 Closing Readiness</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Is Daily Closing actually wired for this tenant — stores, sales source, X-report, module access.</p>
        </div>
        <Link href="/closing" className="btn btn-secondary" style={{ fontSize: 13 }}>← Dashboard</Link>
      </div>

      {err && <div className="card" style={{ padding: 16, color: '#b42318' }}>❌ {err}</div>}
      {!data && !err && <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>}

      {data && (
        <>
          <div className="card" style={{ padding: 16, marginBottom: 16, background: data.ok ? '#e6f7ec' : '#fdeaea' }}>
            <div style={{ fontWeight: 700, fontSize: 15 }}>
              {data.ok ? '✅ No critical gaps found.' : `🚫 ${data.issues.filter((i: any) => i.severity === 'critical').length} critical gap(s) found.`}
            </div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 4 }}>
              Module enabled: <b>{data.module_enabled ? 'yes' : 'NO'}</b> · Tender config: <b>{data.tender_config}</b> · Count config: <b>{data.count_config}</b>
            </div>
          </div>

          {data.issues.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 20 }}>
              {data.issues.map((it: any, i: number) => {
                const s = SEV_STYLE[it.severity] || SEV_STYLE.info
                return (
                  <div key={i} className="card" style={{ padding: 14, background: s.bg, border: `1px solid ${s.fg}22` }}>
                    <div style={{ fontWeight: 700, fontSize: 13, color: s.fg }}>{s.label} — {it.code}</div>
                    <div style={{ fontSize: 13, marginTop: 4 }}>{it.message}</div>
                  </div>
                )
              })}
            </div>
          )}

          <div className="card table-wrapper" style={{ padding: 0 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                {['Source', 'Row count'].map(h => <th key={h} style={{ textAlign: 'left', padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
              </tr></thead>
              <tbody>
                {Object.entries(data.counts || {}).map(([k, v]: any) => (
                  <tr key={k}>
                    <td style={{ padding: '7px 10px', fontSize: 13 }}>{k}</td>
                    <td style={{ padding: '7px 10px', fontSize: 13 }}>{v === null ? <span style={{ color: 'var(--text3)' }}>unknown (table not migrated)</span> : v}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 14 }}>
            This page never changes any recon or gate math — it only makes the existing "not loaded yet" /
            "recon-pending" degrade states visible up front. Fix data gaps via Email Imports (sales/X-report
            mailbox rules), StoreOps → Admin → Stores, or Admin → Billing / Modules.
          </p>
        </>
      )}
    </div>
  )
}
