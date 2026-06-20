'use client'
import { useState, useEffect } from 'react'
import { api, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

type Store = { store_code: string; store_address: string }
type Alias = { id: string; alias: string; store_code: string; note?: string }
type Unmatched = { raw: string; sources: string[]; guess?: string }

export default function StoreMatchPage() {
  const { period } = usePeriod()
  const [unmatched, setUnmatched] = useState<Unmatched[]>([])
  const [aliases, setAliases] = useState<Alias[]>([])
  const [stores, setStores] = useState<Store[]>([])
  const [pick, setPick] = useState<Record<string, string>>({})   // raw -> chosen store_code
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState('')

  function load() {
    setLoading(true)
    Promise.all([
      api(`/api/v1/commcalc/store-unmatched?org_id=${ORG_ID}`),
      api(`/api/v1/commcalc/store-aliases?org_id=${ORG_ID}`),
    ]).then(([u, a]) => {
      setUnmatched(u.unmatched || [])
      setAliases(a.aliases || [])
      setStores(a.stores || [])
    }).catch(e => setMsg(`Error: ${e?.message || e}`)).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  async function saveAlias(raw: string) {
    const code = pick[raw]
    if (!code) { setMsg('Pick a store first.'); return }
    setBusy(raw); setMsg('')
    try {
      await api('/api/v1/commcalc/store-aliases', {
        method: 'POST',
        body: JSON.stringify({ alias: raw, store_code: code, org_id: ORG_ID }),
      })
      setMsg(`Mapped "${raw}" → ${code}. Recompute to apply.`)
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

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🏬 Store Matching</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 720 }}>
            Map any store spelling/number that the system doesn&apos;t recognize to one of your canonical stores.
            Mapped stores stop splitting across the P&amp;L, Daily Targets and recon. Aliases are additive — they
            never change <code>store_mapping</code>, so the asset market join stays intact.
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
          <div className="card" style={{ padding: 0, overflow: 'auto' }}>
            <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>
              Unrecognized stores ({unmatched.length}) — pick the canonical store, then Save
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 760 }}>
              <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Raw store string</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Seen in</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Map to canonical store</th>
                <th style={{ padding: '8px 12px' }}></th>
              </tr></thead>
              <tbody>
                {unmatched.map((u) => (
                  <tr key={u.raw} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '7px 12px', fontSize: 13, fontWeight: 600 }}>{u.raw}</td>
                    <td style={{ padding: '7px 12px', fontSize: 12, color: 'var(--text2)' }}>{(u.sources || []).join(', ')}</td>
                    <td style={{ padding: '7px 12px' }}>
                      <select className="select" value={pick[u.raw] || ''} onChange={e => setPick({ ...pick, [u.raw]: e.target.value })} style={{ minWidth: 280 }}>
                        <option value="">— select store —</option>
                        {stores.map(s => <option key={s.store_code} value={s.store_code}>{s.store_code} — {s.store_address}</option>)}
                      </select>
                    </td>
                    <td style={{ padding: '7px 12px', textAlign: 'right' }}>
                      <button className="btn" disabled={busy === u.raw || !pick[u.raw]} onClick={() => saveAlias(u.raw)} style={{ padding: '5px 12px', fontSize: 12 }}>
                        {busy === u.raw ? 'Saving…' : 'Save'}
                      </button>
                    </td>
                  </tr>
                ))}
                {unmatched.length === 0 && <tr><td colSpan={4} style={{ padding: 24, textAlign: 'center', color: '#15803d' }}>✓ Every store resolves to a canonical store. Nothing to map.</td></tr>}
              </tbody>
            </table>
          </div>

          <div className="card" style={{ padding: 0, overflow: 'auto' }}>
            <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>
              Existing mappings ({aliases.length})
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 560 }}>
              <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Alias (raw string)</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>→ Store code</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Note</th>
                <th style={{ padding: '8px 12px' }}></th>
              </tr></thead>
              <tbody>
                {aliases.map((a) => (
                  <tr key={a.id} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '7px 12px', fontSize: 13 }}>{a.alias}</td>
                    <td style={{ padding: '7px 12px', fontSize: 13, fontWeight: 600 }}>{a.store_code}</td>
                    <td style={{ padding: '7px 12px', fontSize: 12, color: 'var(--text2)' }}>{a.note || '—'}</td>
                    <td style={{ padding: '7px 12px', textAlign: 'right' }}>
                      <button className="btn" disabled={busy === a.id} onClick={() => delAlias(a.id)} style={{ padding: '4px 10px', fontSize: 12, color: '#b91c1c' }}>
                        {busy === a.id ? '…' : 'Remove'}
                      </button>
                    </td>
                  </tr>
                ))}
                {aliases.length === 0 && <tr><td colSpan={4} style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No mappings yet.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
