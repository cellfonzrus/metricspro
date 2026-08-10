'use client'
import { useState, useEffect } from 'react'
import Link from 'next/link'
import { api, fmt, ORG_ID } from '@/lib/client'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'

const inp: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

type Row = { store: string; swept_value: number | null; manual_value: number | null; effective: number | null; effective_source: string | null; as_of_date: string | null; note: string | null }

export default function InventoryValuesPage() {
  const [rows, setRows] = useState<Row[]>([])
  const [sweep, setSweep] = useState<any>(null)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')
  // b2bsoft connection form
  const [cfg, setCfg] = useState<any>({})
  const [pass, setPass] = useState('')
  const [savingCfg, setSavingCfg] = useState(false)
  const [fetching, setFetching] = useState(false)

  function load() {
    setLoading(true)
    Promise.all([
      api(`/api/v1/account/inventory-values?org_id=${ORG_ID}`).catch(() => ({ rows: [], sweep: null })),
      api(`/api/v1/commcalc/b2b/sweep/config?org_id=${ORG_ID}`).catch(() => ({})),
    ]).then(([d, c]: any) => {
      setRows(d.rows || []); setSweep(d.sweep); setTotal(d.total_effective || 0); setCfg(c || {})
    }).catch(console.error).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  const setManual = (i: number, v: string) => setRows(r => r.map((x, j) => j === i ? { ...x, manual_value: v === '' ? null : parseFloat(v) } : x))
  const setNote = (i: number, v: string) => setRows(r => r.map((x, j) => j === i ? { ...x, note: v } : x))

  async function save() {
    setSaving(true); setMsg('')
    try {
      const payload = rows.map(r => ({ store: r.store, manual_value: r.manual_value, note: r.note }))
      const r = await api(`/api/v1/account/inventory-values?org_id=${ORG_ID}`, { method: 'PUT', body: JSON.stringify({ rows: payload }) })
      setMsg(`Saved ${r.saved} stores. Re-compute statements on the dashboard to apply to the Balance Sheet.`); load()
    } catch (e: any) { setMsg('Save failed: ' + (e?.message || e)) }
    setSaving(false)
  }

  async function saveCfg() {
    setSavingCfg(true); setMsg('')
    try {
      const body: any = { portal_user: cfg.portal_user || '', enabled: !!cfg.enabled, frequency: cfg.frequency || 'daily', hour: cfg.hour ?? 6, timezone: cfg.timezone || 'America/New_York' }
      if (pass) body.portal_pass = pass
      const c = await api(`/api/v1/commcalc/b2b/sweep/config?org_id=${ORG_ID}`, { method: 'PUT', body: JSON.stringify(body) })
      setCfg(c); setPass(''); setMsg('b2bsoft connection saved.')
    } catch (e: any) { setMsg('Save failed: ' + (e?.message || e)) }
    setSavingCfg(false)
  }

  async function fetchNow() {
    setFetching(true); setMsg('')
    try {
      await api(`/api/v1/commcalc/b2b/sweep/run-now?org_id=${ORG_ID}`, { method: 'POST' })
      setMsg('Fetch started — refresh in a moment to see the swept values + status.')
    } catch (e: any) { setMsg('Fetch failed: ' + (e?.message || e)) }
    setFetching(false)
  }

  function buildPayload(): ExportPayload {
    return {
      title: 'Inventory Values — Balance Sheet', subtitle: 'b2bsoft Inventory Aging · editable',
      filename: 'inventory-values',
      sheets: [{ name: 'Inventory', rows, columns: [
        { header: 'Store', get: (r: Row) => r.store },
        { header: 'Swept (b2bsoft)', get: (r: Row) => r.swept_value, money: true },
        { header: 'Manual override', get: (r: Row) => r.manual_value, money: true },
        { header: 'Effective', get: (r: Row) => r.effective, money: true },
        { header: 'Source', get: (r: Row) => r.effective_source },
        { header: 'As of', get: (r: Row) => r.as_of_date },
      ] }],
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📦 Inventory Values</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Real-time on-hand value per store (b2bsoft Inventory Aging) → Balance Sheet · a manual override always wins.</p>
          {/* PURPOSE LINE (owner 2026-08-10: "this page shows the inventory so what is the difference
              between the 2 pages — show the purpose of each page on top"). Two pages read the SAME
              Inventory Aging file for two different questions; say which one this is, and link the other. */}
          <p style={{ color: 'var(--text3)', fontSize: 12.5, margin: '6px 0 0', maxWidth: 780 }}>
            <b>Purpose — DOLLARS.</b> What the on-hand stock is WORTH per store, for the Balance Sheet
            inventory line. It answers “how much money is sitting on the shelf.” It does not check unit
            counts. For “do our device COUNTS match b2bsoft,” use{' '}
            <Link href="/commcalc/asset/inventory-recon">On-Inventory ↔ b2bsoft Recon</Link>.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {msg && <span style={{ fontSize: 12, color: 'var(--text2)', maxWidth: 360 }}>{msg}</span>}
          {rows.length > 0 && <><ExportButtons payload={buildPayload} /><SendReportButton exportPayload={buildPayload} compact /></>}
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : '💾 Save overrides'}</button>
        </div>
      </div>

      {/* b2bsoft connection */}
      <div className="card" style={{ padding: 14, marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <strong style={{ fontSize: 14 }}>🔌 b2bsoft connection (wsreports.b2bsoft.com)</strong>
          {sweep && <span style={{ fontSize: 12, color: sweep.last_status === 'ok' ? '#15803d' : sweep.last_status === 'error' ? '#b91c1c' : 'var(--text3)' }}>
            {sweep.last_status ? `last: ${sweep.last_status}${sweep.last_run_at ? ' · ' + new Date(sweep.last_run_at).toLocaleString() : ''}` : 'never run'}
          </span>}
        </div>
        {sweep?.last_detail && <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 6 }}>{sweep.last_detail}</div>}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 10 }}>
          <input style={{ ...inp, width: 180 }} placeholder="b2bsoft username" value={cfg.portal_user || ''} onChange={e => setCfg({ ...cfg, portal_user: e.target.value })} />
          <input style={{ ...inp, width: 180 }} type="password" placeholder={cfg.has_credentials ? '•••••• (unchanged)' : 'b2bsoft password'} value={pass} onChange={e => setPass(e.target.value)} />
          <select style={inp} value={cfg.frequency || 'daily'} onChange={e => setCfg({ ...cfg, frequency: e.target.value })}>
            <option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option>
          </select>
          <label style={{ fontSize: 13, display: 'flex', gap: 5, alignItems: 'center' }}>
            <input type="checkbox" checked={!!cfg.enabled} onChange={e => setCfg({ ...cfg, enabled: e.target.checked })} /> Enabled
          </label>
          <button className="btn" onClick={saveCfg} disabled={savingCfg}>{savingCfg ? '…' : 'Save connection'}</button>
          <button className="btn" onClick={fetchNow} disabled={fetching || !cfg.has_credentials}>{fetching ? '…' : '⤓ Fetch inventory now'}</button>
        </div>
        <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 8 }}>The password is stored backend-only and never shown. Auto-fetch runs on the schedule once credentials are saved + Enabled and the portal client is live.</div>
      </div>

      <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 10 }}>
        Total on-hand inventory (effective): <strong style={{ color: 'var(--text)' }}>{fmt(total)}</strong> · {rows.length} stores
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <div className="card" style={{ padding: 0, overflow: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 760 }}>
            <thead>
              <tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Store</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Swept (b2bsoft)</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Manual override</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Effective (on BS)</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>As of</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Note</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.store} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '6px 12px', fontSize: 13 }}>{r.store}</td>
                  <td style={{ padding: '6px 12px', textAlign: 'right', fontSize: 13, color: r.swept_value == null ? 'var(--text3)' : 'var(--text)' }}>{r.swept_value == null ? '—' : fmt(r.swept_value)}</td>
                  <td style={{ padding: '6px 12px', textAlign: 'right' }}>
                    <input type="number" step="0.01" style={{ ...inp, width: 120, textAlign: 'right' }} value={r.manual_value ?? ''} placeholder="—" onChange={e => setManual(i, e.target.value)} />
                  </td>
                  <td style={{ padding: '6px 12px', textAlign: 'right', fontSize: 13, fontWeight: 600 }}>
                    {r.effective == null ? '—' : fmt(r.effective)}
                    {r.effective_source === 'manual' && <span style={{ marginLeft: 6, fontSize: 10, color: '#92400e', background: '#fef3c7', padding: '1px 5px', borderRadius: 999 }}>manual</span>}
                  </td>
                  <td style={{ padding: '6px 12px', fontSize: 12, color: 'var(--text3)' }}>{r.as_of_date || '—'}</td>
                  <td style={{ padding: '6px 12px' }}><input style={{ ...inp, width: 160 }} value={r.note ?? ''} placeholder="" onChange={e => setNote(i, e.target.value)} /></td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={6} style={{ padding: 30, textAlign: 'center', color: 'var(--text3)' }}>No stores found.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 10 }}>
        The Balance Sheet inventory line uses the <strong>effective</strong> value per store (manual override if set, else the swept b2bsoft value; stores with neither fall back to the asset-ledger on-hand value). After editing, re-run <Link href="/accounts">Compute statements</Link>.
      </p>
    </div>
  )
}
