'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'

// Store-visit settings (mig 503, 2026-07-16 luxelink-parity audit): the "order accessories" link on
// the DM store-visit checklist was hard-coded to vAccessorize.com (a Boost-house distributor) for
// EVERY tenant. Blank = the historical vAccessorize default (house/Boost stays byte-identical); a
// tenant sets its own distributor link here.
const inp: React.CSSProperties = { padding: '8px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 14, background: 'var(--surface)', width: '100%' }

export default function StoreVisitConfigPage() {
  const [cfg, setCfg] = useState<any>(null)
  const [url, setUrl] = useState('')
  const [label, setLabel] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const load = useCallback(() => {
    api('/api/v1/storevisit/config').then((r: any) => { setCfg(r); setUrl(r.is_default ? '' : (r.accessory_order_url || '')); setLabel(r.is_default ? '' : (r.accessory_order_label || '')) }).catch(() => setCfg({}))
  }, [])
  useEffect(() => { load() }, [load])

  async function save() {
    setBusy(true); setMsg('')
    try {
      const r: any = await api('/api/v1/storevisit/config', { method: 'PUT', body: JSON.stringify({ accessory_order_url: url, accessory_order_label: label }) })
      setCfg(r); setMsg('✅ Saved.')
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    finally { setBusy(false) }
  }

  if (!cfg) return <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>

  return (
    <div style={{ maxWidth: 640 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🏪 Store Visit Settings</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Accessory-reorder link shown on the DM store-visit checklist.</p>
        </div>
        <Link href="/closing" className="btn btn-secondary" style={{ fontSize: 13 }}>← Dashboard</Link>
      </div>

      <div className="card" style={{ padding: 18 }}>
        <label style={{ display: 'block', marginBottom: 10 }}>
          <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>Accessory-order URL</div>
          <input style={inp} value={url} onChange={e => setUrl(e.target.value)} placeholder="https://www.vaccessorize.com (default — leave blank to keep)" />
        </label>
        <label style={{ display: 'block', marginBottom: 10 }}>
          <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>Button label</div>
          <input style={inp} value={label} onChange={e => setLabel(e.target.value)} placeholder="Order on vAccessorize.com (default — leave blank to keep)" />
        </label>
        <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 12 }}>
          Currently: <b>{cfg.accessory_order_url}</b>{cfg.is_default && ' (default)'}
        </div>
        <button className="btn btn-primary" style={{ fontSize: 14 }} disabled={busy} onClick={save}>💾 Save</button>
        {msg && <span style={{ fontSize: 13, marginLeft: 12 }}>{msg}</span>}
      </div>
    </div>
  )
}
