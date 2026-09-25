'use client'
// PROFIT CENTERS (owner 2026-09-25, mig 1022, index §37.1): "dedicated profit centers". A profit center is a SET OF
// STORES — codes and names are the tenant's (or the franchisor's, entered or imported), a center may sit under a
// parent, and its external reference is the number the franchisor prints on its reports (how a royalty report finds
// its store). Its P&L is a SCOPE of the platform's one statement engine (GET /account/centers/pl → the engine's
// `profit_center:<code>` scope) — the same lines, the same numbers as the P&L Statement, never a second P&L.
import { Fragment, useCallback, useEffect, useState } from 'react'
import { api, fmt } from '@/lib/client'

const th: React.CSSProperties = { textAlign: 'left', padding: '6px 9px', fontSize: 12, color: 'var(--text2)', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '6px 9px', fontSize: 13, borderBottom: '1px solid var(--border)' }
const tdr: React.CSSProperties = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }
const lastMonth = () => { const d = new Date(); d.setMonth(d.getMonth() - 1); return d.toISOString().slice(0, 7) }
const blank = { code: '', name: '', parent_code: '', external_ref: '', is_active: true }

// The fields of the /account/centers payloads this page reads.
type CenterDraft = typeof blank
interface Center { code: string; name: string; parent_code?: string | null; external_ref?: string | null; is_active: boolean; center_type: string }
interface CentersData {
  centers?: Center[]; store_map?: { store: string; profit_center_code: string }[]; stores?: string[]
  store_map_conflicts?: Record<string, unknown>; problems?: { profit?: string[] }
}
interface PlSection { type: string; name: string; subtotal: number; lines: { key: string; label: string; amount: number }[] }
interface CenterStatement { scope_label?: string; computed?: boolean; note?: string; pl?: { sections: PlSection[]; gross_profit: number; net_income: number } }

export default function ProfitCentersPage() {
  const [data, setData] = useState<CentersData | null>(null)
  const [draft, setDraft] = useState<CenterDraft>(blank)
  const [err, setErr] = useState('')
  const [pc, setPc] = useState('')
  const [period, setPeriod] = useState(lastMonth())
  // The last statement loaded; shown only while a center is picked (derived below, not reset in an effect).
  const [loadedStmt, setStmt] = useState<CenterStatement | null>(null)
  const stmt = pc ? loadedStmt : null
  const load = useCallback(() => api('/api/v1/account/centers').then(setData).catch(e => setErr(e?.message || String(e))), [])
  useEffect(() => { load() }, [load])
  const centers = (data?.centers || []).filter(c => c.center_type === 'profit')
  const mapOf: Record<string, string> = Object.fromEntries((data?.store_map || []).map(m => [m.store, m.profit_center_code]))

  useEffect(() => {
    if (!pc) return
    let alive = true
    api(`/api/v1/account/centers/pl/${encodeURIComponent(period)}?profit_center=${encodeURIComponent(pc)}`)
      .then(d => { if (alive) setStmt(d) }).catch(e => { if (alive) setErr(e?.message || String(e)) })
    return () => { alive = false }
  }, [pc, period])

  const save = (row: CenterDraft | Center) => api('/api/v1/account/centers', { method: 'PUT', body: JSON.stringify({ ...row, center_type: 'profit' }) })
    .then(() => { setDraft(blank); load() }).catch(e => setErr(e?.message || String(e)))
  const mapStore = (store: string, code: string) => api('/api/v1/account/centers/store-map', { method: 'PUT', body: JSON.stringify({ store_ref: store, profit_center_code: code || null }) })
    .then(load).catch(e => setErr(e?.message || String(e)))

  return (
    <div style={{ padding: 16, maxWidth: 1200 }}>
      <h1 style={{ fontSize: 20, fontWeight: 700, margin: '0 0 4px' }}>Profit Centers</h1>
      <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 10 }}>Each profit center is a set of stores; its P&L is the P&L Statement narrowed to those stores.</div>
      {err && <div style={{ color: '#b91c1c', marginBottom: 8 }}>{err}</div>}
      {(data?.problems?.profit?.length ?? 0) > 0 && data?.problems?.profit && <div style={{ color: '#b45309', marginBottom: 8 }}>{data.problems.profit.join(' · ')}</div>}

      <div className="card" style={{ padding: 14, marginBottom: 14 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6 }}>Centers</div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>Code</th><th style={th}>Name</th><th style={th}>Parent</th><th style={th}>Franchisor reference</th><th style={th}>Active</th><th style={th}></th></tr></thead>
          <tbody>
            {centers.map(c => (
              <tr key={c.code}><td style={td}>{c.code}</td><td style={td}>{c.name}</td><td style={td}>{c.parent_code || ''}</td><td style={td}>{c.external_ref || ''}</td>
                <td style={td}><input type="checkbox" checked={c.is_active} onChange={e => save({ ...c, is_active: e.target.checked })} /></td>
                <td style={td}><button className="btn" onClick={() => setPc(c.code)}>P&L</button>{' '}
                  <button className="btn" onClick={() => window.confirm(`Delete profit center ${c.code}?`) && api(`/api/v1/account/centers/profit/${encodeURIComponent(c.code)}`, { method: 'DELETE' }).then(load)}>Delete</button></td></tr>
            ))}
            <tr>
              <td style={td}><input placeholder="code" value={draft.code} onChange={e => setDraft({ ...draft, code: e.target.value })} /></td>
              <td style={td}><input placeholder="name" value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></td>
              <td style={td}><select value={draft.parent_code} onChange={e => setDraft({ ...draft, parent_code: e.target.value })}><option value="">—</option>{centers.map(c => <option key={c.code} value={c.code}>{c.code}</option>)}</select></td>
              <td style={td}><input placeholder="e.g. center number" value={draft.external_ref} onChange={e => setDraft({ ...draft, external_ref: e.target.value })} /></td>
              <td style={td}></td>
              <td style={td}><button className="btn btn-primary" disabled={!draft.code.trim()} onClick={() => save(draft)}>Add</button></td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className="card" style={{ padding: 14, marginBottom: 14 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6 }}>Stores</div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>Store</th><th style={th}>Profit center</th></tr></thead>
          <tbody>{(data?.stores || []).map((s: string) => (
            <tr key={s}><td style={td}>{s}</td><td style={td}>
              <select value={mapOf[s] || ''} onChange={e => mapStore(s, e.target.value)}><option value="">— none —</option>{centers.map(c => <option key={c.code} value={c.code}>{c.code} — {c.name}</option>)}</select>
            </td></tr>
          ))}</tbody>
        </table>
        {Object.keys(data?.store_map_conflicts || {}).length > 0 && <div style={{ color: '#b91c1c', fontSize: 13, marginTop: 6 }}>A store is mapped to two centers: {JSON.stringify(data?.store_map_conflicts)}</div>}
      </div>

      {pc && (
        <div className="card" style={{ padding: 14 }}>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 8 }}>
            <div style={{ fontWeight: 700, fontSize: 14 }}>P&L — {stmt?.scope_label || pc}</div>
            <input type="month" value={period} onChange={e => setPeriod(e.target.value)} />
          </div>
          {stmt && !stmt.computed && <div style={{ color: '#b45309' }}>{stmt.note}</div>}
          {stmt?.pl && (
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <tbody>
                {stmt.pl.sections.map(s => (
                  <Fragment key={s.type}>
                    <tr><td style={{ ...td, fontWeight: 700 }}>{s.name}</td><td style={{ ...tdr, fontWeight: 700 }}>{fmt(s.subtotal)}</td></tr>
                    {s.lines.map(l => <tr key={s.type + l.key}><td style={{ ...td, paddingLeft: 22 }}>{l.label}</td><td style={tdr}>{fmt(l.amount)}</td></tr>)}
                  </Fragment>
                ))}
                <tr><td style={{ ...td, fontWeight: 700 }}>Gross profit</td><td style={{ ...tdr, fontWeight: 700 }}>{fmt(stmt.pl.gross_profit)}</td></tr>
                <tr><td style={{ ...td, fontWeight: 700 }}>Net income</td><td style={{ ...tdr, fontWeight: 700 }}>{fmt(stmt.pl.net_income)}</td></tr>
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  )
}
