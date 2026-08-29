'use client'
import { useState, useEffect } from 'react'
import { api, ORG_ID } from '@/lib/client'

const inp: React.CSSProperties = { padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

export default function RepMapPage() {
  const [data, setData] = useState<any>({ configured: true, aliases: [], names: [] })
  const [loading, setLoading] = useState(true)
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [canonical, setCanonical] = useState('')
  const [search, setSearch] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  function load() {
    setLoading(true)
    api(`/api/v1/commcalc/rep-aliases?org_id=${ORG_ID}`)
      .then(setData).catch(e => setMsg('Load failed: ' + (e?.message || e))).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  const mapped = new Set((data.aliases || []).map((a: any) => (a.alias || '').toUpperCase()))
  const names: string[] = (data.names || []).filter((n: string) =>
    !mapped.has(n.toUpperCase()) && (!search || n.toLowerCase().includes(search.toLowerCase())))

  function toggle(n: string) { setSel(s => { const x = new Set(s); x.has(n) ? x.delete(n) : x.add(n); return x }) }

  async function merge() {
    const aliases = Array.from(sel).filter(n => n !== canonical)
    if (!canonical || !aliases.length) { setMsg('Pick a canonical name + at least one other variant to merge into it.'); return }
    setBusy(true); setMsg('')
    try {
      const r = await api(`/api/v1/commcalc/rep-aliases?org_id=${ORG_ID}`, { method: 'POST', body: JSON.stringify({ canonical, aliases }) })
      setMsg(`Merged ${r.merged} variant(s) → ${canonical}. Targets/dashboard reflect it on next load (re-run calc to update commission matching).`)
      setSel(new Set()); setCanonical(''); load()
    } catch (e: any) { setMsg('Merge failed: ' + (e?.message || e)) }
    setBusy(false)
  }
  async function unmap(alias: string) {
    try { await api(`/api/v1/commcalc/rep-aliases/${encodeURIComponent(alias)}?org_id=${ORG_ID}`, { method: 'DELETE' }); load() }
    catch (e: any) { setMsg('Remove failed: ' + (e?.message || e)) }
  }

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <a href="/commcalc/targets/my" style={{ fontSize: 13, color: 'var(--text3)', textDecoration: 'none' }}>← My Targets</a>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 0' }}>Merge Duplicate Reps</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          The same person can appear under different name spellings (e.g. "Abdul K" vs "Abdul Kakar") across the
          schedule and the DLAR/sales data — which splits them into two rows and breaks their target totals.
          Merge the variants into one canonical name here.
        </p>
      </div>

      {msg && <div style={{ fontSize: 13, marginBottom: 12, color: 'var(--text2)' }}>{msg}</div>}

      {!loading && data.configured === false && (
        <div className="card" style={{ padding: 16, marginBottom: 16, background: '#fffbeb', border: '1px solid #fde68a', color: '#92400e', fontSize: 13 }}>
          ⚠️ Run migration <strong>016_rep_aliases.sql</strong> in Supabase to enable saving merges.
        </div>
      )}

      {loading ? <div style={{ padding: 40, color: 'var(--text3)' }}>Loading…</div> : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16, alignItems: 'start' }}>
          {/* Merge tool */}
          <div className="card" style={{ padding: 16 }}>
            <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 10 }}>1. Pick the variants that are the same person</div>
            <input style={{ ...inp, width: '100%', marginBottom: 10 }} placeholder="Search names…" value={search} onChange={e => setSearch(e.target.value)} />
            <div style={{ maxHeight: 320, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 8 }}>
              {names.map(n => (
                <label key={n} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', borderBottom: '1px solid var(--border)', fontSize: 13, cursor: 'pointer', background: sel.has(n) ? '#eff6ff' : 'transparent' }}>
                  <input type="checkbox" checked={sel.has(n)} onChange={() => toggle(n)} />{n}
                </label>
              ))}
              {!names.length && <div style={{ padding: 14, color: 'var(--text3)', fontSize: 13 }}>No unmapped names.</div>}
            </div>
            {sel.size > 0 && (
              <div style={{ marginTop: 12 }}>
                <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6 }}>2. Canonical name (keep this one)</div>
                <select style={{ ...inp, width: '100%', marginBottom: 10 }} value={canonical} onChange={e => setCanonical(e.target.value)}>
                  <option value="">— choose the name to keep —</option>
                  {Array.from(sel).map(n => <option key={n} value={n}>{n}</option>)}
                </select>
                <button className="btn btn-primary" onClick={merge} disabled={busy}>
                  {busy ? 'Merging…' : `Merge ${sel.size} → canonical`}
                </button>
              </div>
            )}
          </div>

          {/* Existing mappings */}
          <div className="card" style={{ padding: 16 }}>
            <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 10 }}>Existing merges ({(data.aliases || []).length})</div>
            {(data.aliases || []).length === 0 ? (
              <div style={{ color: 'var(--text3)', fontSize: 13 }}>No merges yet.</div>
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr><th style={{ textAlign: 'left', fontSize: 11, color: 'var(--text2)', padding: '6px 8px' }}>Variant</th><th style={{ textAlign: 'left', fontSize: 11, color: 'var(--text2)', padding: '6px 8px' }}>→ Canonical</th><th /></tr></thead>
                <tbody>
                  {(data.aliases || []).map((a: any) => (
                    <tr key={a.alias} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '6px 8px', fontSize: 13 }}>{a.alias}</td>
                      <td style={{ padding: '6px 8px', fontSize: 13, fontWeight: 600 }}>{a.canonical}</td>
                      <td style={{ padding: '6px 8px', textAlign: 'right' }}><button className="btn btn-secondary" style={{ fontSize: 11, padding: '3px 8px' }} onClick={() => unmap(a.alias)}>✕</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
