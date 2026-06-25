'use client'
import { useState, useEffect, useCallback } from 'react'
import { api } from '@/lib/client'

// Merge rep name-variants (e.g. "Abdul K" + "Abdul Kakar") into one canonical name so commissions,
// KPIs and chargebacks roll up to a single person. Backed by commcalc.rep_aliases (migration 016).
const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '6px 8px', borderTop: '1px solid var(--border)', fontSize: 13 }

export default function RepAliasesPage() {
  const [aliases, setAliases] = useState<any[]>([])
  const [names, setNames] = useState<string[]>([])
  const [configured, setConfigured] = useState(true)
  const [canonical, setCanonical] = useState('')
  const [picked, setPicked] = useState<Record<string, boolean>>({})
  const [search, setSearch] = useState('')
  const [msg, setMsg] = useState('')

  const load = useCallback(() => {
    api('/api/v1/commcalc/rep-aliases').then((r: any) => {
      setAliases(r.aliases || []); setNames(r.names || []); setConfigured(r.configured !== false)
      if (r.configured === false) setMsg('Run migration 016_rep_aliases.sql to save merges.')
    }).catch((e: any) => setMsg('Load failed: ' + (e?.message || e)))
  }, [])
  useEffect(() => { load() }, [load])

  const aliased = new Set(aliases.map(a => (a.alias || '').toUpperCase()))
  const filtered = names.filter(n => !search || n.toLowerCase().includes(search.toLowerCase()))

  async function merge() {
    const picks = Object.keys(picked).filter(k => picked[k])
    if (!canonical) { setMsg('Pick the canonical (correct) name first.'); return }
    if (!picks.length) { setMsg('Select the variant name(s) to merge into it.'); return }
    setMsg('')
    try {
      const r = await api('/api/v1/commcalc/rep-aliases', { method: 'POST', body: JSON.stringify({ canonical, aliases: picks }) })
      setMsg(`Merged ${r.merged} variant(s) → ${canonical}.`); setPicked({}); setCanonical(''); load()
    } catch (e: any) { setMsg('Merge failed: ' + (e?.message || e)) }
  }
  async function unmerge(alias: string) {
    try { await api(`/api/v1/commcalc/rep-aliases/${encodeURIComponent(alias)}`, { method: 'DELETE' }); load() }
    catch (e: any) { setMsg('Remove failed: ' + (e?.message || e)) }
  }

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🔗 Rep Aliases</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Merge name variants of the same rep into one canonical name so their numbers roll up together.
        </p>
      </div>

      <div className="card" style={{ padding: 14, marginBottom: 16 }}>
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>Merge variants → canonical</div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Canonical (correct) name<br />
            <select style={{ ...sel, marginTop: 4, minWidth: 220 }} value={canonical} onChange={e => setCanonical(e.target.value)}>
              <option value="">— pick —</option>{names.map(n => <option key={n} value={n}>{n}</option>)}</select></label>
          <button className="btn btn-primary" onClick={merge}>🔗 Merge selected → canonical</button>
          {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <div className="card" style={{ padding: 0 }}>
          <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>
            All rep names ({filtered.length}) — tick the variants to merge
          </div>
          <div style={{ padding: '8px 14px' }}><input className="input" placeholder="Search…" value={search} onChange={e => setSearch(e.target.value)} style={{ ...sel, width: '100%' }} /></div>
          <div style={{ maxHeight: 420, overflowY: 'auto' }}>
            {filtered.map(n => (
              <label key={n} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 14px', fontSize: 13, borderTop: '1px solid var(--border)' }}>
                <input type="checkbox" checked={!!picked[n]} onChange={e => setPicked(p => ({ ...p, [n]: e.target.checked }))} />
                {n}{aliased.has(n.toUpperCase()) && <span className="badge" style={{ fontSize: 10 }}>merged</span>}
              </label>
            ))}
          </div>
        </div>

        <div className="card" style={{ padding: 0 }}>
          <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>Existing merges ({aliases.length})</div>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>{['Variant', 'Canonical', ''].map(h => <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>)}</tr></thead>
            <tbody>
              {aliases.map(a => (
                <tr key={a.alias}>
                  <td style={cell}>{a.alias}</td>
                  <td style={{ ...cell, fontWeight: 600 }}>{a.canonical}</td>
                  <td style={cell}><button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 8px' }} onClick={() => unmerge(a.alias)}>↺ Unmerge</button></td>
                </tr>
              ))}
              {aliases.length === 0 && <tr><td colSpan={3} style={{ textAlign: 'center', padding: 30, color: 'var(--text3)' }}>No merges yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
