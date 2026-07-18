'use client'
import { useState, useEffect } from 'react'
import { api, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

type Store = { store_code: string; store_address: string; market?: string }
type Alias = { id: string; alias: string; store_code: string; note?: string; source?: string; confidence?: string }
type Suggestion = { store_code: string; store_address: string; market?: string; confidence: string; score: number; reason: string }
type ResItem = { raw: string; sources: string[]; status: string; resolved_code: string | null; suggestions: Suggestion[] }
type ResReport = { items: ResItem[]; counts: Record<string, number>; total: number; stores: Store[]; sources_scanned: string[] }

const STATUS_META: Record<string, { label: string; bg: string; fg: string }> = {
  explicit: { label: '✓ Explicit mapping', bg: '#dcfce7', fg: '#15803d' },
  store_mapping: { label: 'Store map', bg: '#dbeafe', fg: '#1d4ed8' },
  'exact-fallback': { label: '⚠ Exact fallback', bg: '#fef3c7', fg: '#b45309' },
  unresolved: { label: '✗ Unresolved', bg: '#fee2e2', fg: '#b91c1c' },
}
const CONF_FG: Record<string, string> = { exact: '#15803d', high: '#15803d', medium: '#b45309', low: '#6b7280' }

export default function StoreMatchPage() {
  const { period } = usePeriod()
  const [report, setReport] = useState<ResReport | null>(null)
  const [aliases, setAliases] = useState<Alias[]>([])
  const [stores, setStores] = useState<Store[]>([])
  const [pick, setPick] = useState<Record<string, string>>({})   // raw -> chosen store_code
  const [showAll, setShowAll] = useState(false)
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState('')

  function load() {
    setLoading(true)
    Promise.all([
      api(`/api/v1/commcalc/store-resolution?org_id=${ORG_ID}`),
      api(`/api/v1/commcalc/store-aliases?org_id=${ORG_ID}`),
    ]).then(([r, a]) => {
      setReport(r)
      setAliases(a.aliases || [])
      setStores(a.stores || [])
    }).catch(e => setMsg(`Error: ${e?.message || e}`)).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  async function confirmMapping(raw: string, code: string, source: string, confidence: string, key: string) {
    if (!code) { setMsg('Pick a store first.'); return }
    setBusy(key); setMsg('')
    try {
      await api('/api/v1/commcalc/store-aliases', {
        method: 'POST',
        body: JSON.stringify({ alias: raw, store_code: code, org_id: ORG_ID, source, confidence }),
      })
      setMsg(`Mapped “${raw}” → ${code}.`)
      load()
    } catch (e: any) { setMsg(`Error: ${e?.message || e}`) }
    finally { setBusy('') }
  }

  async function delAlias(id: string) {
    setBusy(id)
    try { await api(`/api/v1/commcalc/store-aliases/${id}?org_id=${ORG_ID}`, { method: 'DELETE' }); load() }
    catch (e: any) { setMsg(`Error: ${e?.message || e}`) }
    finally { setBusy('') }
  }

  async function recompute() {
    setBusy('recompute'); setMsg('')
    try {
      await api(`/api/v1/account/compute/${encodeURIComponent(period)}?org_id=${ORG_ID}`, { method: 'POST' })
      setMsg(`Recomputed P&L for ${period}. Store splits should now be merged.`)
    } catch (e: any) { setMsg(`Recompute failed: ${e?.message || e}`) }
    finally { setBusy('') }
  }

  const counts = report?.counts || {}
  const items = report?.items || []
  const needsAttention = items.filter(i => i.status === 'unresolved' || i.status === 'exact-fallback')
  const shown = showAll ? items : needsAttention

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🏬 Store Matching</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 760 }}>
            Map each store spelling from your POS/sales feed to one of your canonical stores so the same
            physical store never splits across the P&amp;L, Daily Targets and recon. An explicit mapping is the
            source of truth (it always wins). Rows resolving only by an <b>exact address match</b> are shown so
            you can lock them in with one click — that removes any ambiguity. Matching is case-insensitive.
          </p>
        </div>
        <button className="btn" disabled={!!busy} onClick={recompute}
          style={{ padding: '8px 14px', fontWeight: 600 }}>
          {busy === 'recompute' ? 'Recomputing…' : `↻ Recompute P&L (${period})`}
        </button>
      </div>

      {msg && <div className="card" style={{ padding: '10px 14px', marginBottom: 14, color: msg.startsWith('Error') || msg.includes('failed') ? '#b91c1c' : '#15803d' }}>{msg}</div>}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <div style={{ display: 'grid', gap: 16 }}>
          {/* status summary */}
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            {(['explicit', 'store_mapping', 'exact-fallback', 'unresolved'] as const).map(k => (
              <div key={k} className="card" style={{ padding: '8px 14px', display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 10, background: STATUS_META[k].bg, color: STATUS_META[k].fg }}>{STATUS_META[k].label}</span>
                <span style={{ fontSize: 18, fontWeight: 800 }}>{counts[k] || 0}</span>
              </div>
            ))}
          </div>

          {/* resolution table */}
          <div className="card" style={{ padding: 0, overflow: 'auto' }}>
            <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <span>{showAll ? `All POS store strings (${items.length})` : `Needs attention (${needsAttention.length})`}</span>
              <label style={{ fontSize: 12, fontWeight: 500, color: 'var(--text2)', display: 'flex', alignItems: 'center', gap: 6 }}>
                <input type="checkbox" checked={showAll} onChange={e => setShowAll(e.target.checked)} /> show already-resolved
              </label>
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 860 }}>
              <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>POS store string</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Status</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Resolution / suggestions</th>
              </tr></thead>
              <tbody>
                {shown.map((it) => {
                  const meta = STATUS_META[it.status] || STATUS_META.unresolved
                  const key = it.raw
                  return (
                    <tr key={key} style={{ borderTop: '1px solid var(--border)', verticalAlign: 'top' }}>
                      <td style={{ padding: '9px 12px', fontSize: 13, fontWeight: 600 }}>
                        {it.raw}
                        <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 400 }}>{(it.sources || []).join(', ')}</div>
                      </td>
                      <td style={{ padding: '9px 12px' }}>
                        <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 10, background: meta.bg, color: meta.fg, whiteSpace: 'nowrap' }}>{meta.label}</span>
                        {it.resolved_code && <div style={{ fontSize: 12, marginTop: 4, color: 'var(--text2)' }}>→ <b>{it.resolved_code}</b></div>}
                      </td>
                      <td style={{ padding: '9px 12px' }}>
                        {it.status === 'store_mapping' && (
                          <span style={{ fontSize: 12, color: 'var(--text2)' }}>Resolved by your store map — no action.</span>
                        )}
                        {it.status === 'explicit' && (
                          <span style={{ fontSize: 12, color: '#15803d' }}>Confirmed mapping. Remove it below to change.</span>
                        )}
                        {it.status === 'exact-fallback' && (
                          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                            <span style={{ fontSize: 12, color: 'var(--text2)' }}>Matches store <b>{it.resolved_code}</b> by exact address only.</span>
                            <button className="btn" disabled={busy === key} onClick={() => confirmMapping(it.raw, it.resolved_code || '', 'fallback-confirmed', 'exact', key)} style={{ padding: '4px 12px', fontSize: 12, fontWeight: 600 }}>
                              {busy === key ? 'Saving…' : `✓ Confirm as mapping`}
                            </button>
                          </div>
                        )}
                        {it.status === 'unresolved' && (
                          <div style={{ display: 'grid', gap: 6 }}>
                            {(it.suggestions || []).length > 0 ? (it.suggestions).map((s, i) => (
                              <div key={s.store_code + i} style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                                <button className="btn" disabled={busy === key} onClick={() => confirmMapping(it.raw, s.store_code, 'suggested', s.confidence, key)} style={{ padding: '4px 10px', fontSize: 12, fontWeight: 600 }}>
                                  {busy === key ? '…' : `✓ ${s.store_code}`}
                                </button>
                                <span style={{ fontSize: 12 }}>{s.store_address}</span>
                                <span style={{ fontSize: 10, fontWeight: 700, color: CONF_FG[s.confidence] || '#6b7280', textTransform: 'uppercase' }}>{s.confidence}</span>
                                <span style={{ fontSize: 11, color: 'var(--text3)' }}>({s.reason})</span>
                              </div>
                            )) : <span style={{ fontSize: 12, color: 'var(--text3)' }}>No close suggestion — pick manually.</span>}
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 2 }}>
                              <select className="select" value={pick[key] || ''} onChange={e => setPick({ ...pick, [key]: e.target.value })} style={{ minWidth: 260, fontSize: 12 }}>
                                <option value="">— or pick manually —</option>
                                {stores.map(s => <option key={s.store_code} value={s.store_code}>{s.store_code} — {s.store_address}</option>)}
                              </select>
                              <button className="btn" disabled={busy === key || !pick[key]} onClick={() => confirmMapping(it.raw, pick[key], 'manual', '', key)} style={{ padding: '4px 12px', fontSize: 12 }}>
                                {busy === key ? '…' : 'Save'}
                              </button>
                            </div>
                          </div>
                        )}
                      </td>
                    </tr>
                  )
                })}
                {shown.length === 0 && <tr><td colSpan={3} style={{ padding: 24, textAlign: 'center', color: '#15803d' }}>✓ Every POS store string resolves to a canonical store. Nothing needs attention.</td></tr>}
              </tbody>
            </table>
          </div>

          {/* existing explicit mappings */}
          <div className="card" style={{ padding: 0, overflow: 'auto' }}>
            <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>
              Explicit mappings ({aliases.length})
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 620 }}>
              <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>POS store string</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>→ Store code</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>How</th>
                <th style={{ padding: '8px 12px' }}></th>
              </tr></thead>
              <tbody>
                {aliases.map((a) => (
                  <tr key={a.id} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '7px 12px', fontSize: 13 }}>{a.alias}</td>
                    <td style={{ padding: '7px 12px', fontSize: 13, fontWeight: 600 }}>{a.store_code}</td>
                    <td style={{ padding: '7px 12px', fontSize: 12, color: 'var(--text2)' }}>
                      {a.source || 'manual'}{a.confidence ? ` · ${a.confidence}` : ''}{a.note ? ` · ${a.note}` : ''}
                    </td>
                    <td style={{ padding: '7px 12px', textAlign: 'right' }}>
                      <button className="btn" disabled={busy === a.id} onClick={() => delAlias(a.id)} style={{ padding: '4px 10px', fontSize: 12, color: '#b91c1c' }}>
                        {busy === a.id ? '…' : 'Remove'}
                      </button>
                    </td>
                  </tr>
                ))}
                {aliases.length === 0 && <tr><td colSpan={4} style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No explicit mappings yet.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
