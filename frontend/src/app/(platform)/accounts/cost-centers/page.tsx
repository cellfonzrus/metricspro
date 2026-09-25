'use client'
// COST CENTERS (owner 2026-09-25, mig 1022, index §37.1): "detailed cost centers assigned by" the franchisor. A cost
// center is a SET OF P&L LINES — a whole line, or one drill-down detail of it (e.g. the 'Rent' expense inside store
// operating expenses). Its view REGROUPS the assembled P&L (GET /account/centers/cost-view → the statement engine's
// own statement, then centers.cost_center_view): every dollar lands in exactly one center or in "Untagged", and the
// view ties to the statement to the cent — it never computes a number of its own.
import { Fragment, useCallback, useEffect, useState } from 'react'
import { api, fmt } from '@/lib/client'

const th: React.CSSProperties = { textAlign: 'left', padding: '6px 9px', fontSize: 12, color: 'var(--text2)', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }
const thr: React.CSSProperties = { ...th, textAlign: 'right' }
const td: React.CSSProperties = { padding: '6px 9px', fontSize: 13, borderBottom: '1px solid var(--border)' }
const tdr: React.CSSProperties = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }
const lastMonth = () => { const d = new Date(); d.setMonth(d.getMonth() - 1); return d.toISOString().slice(0, 7) }
const blank = { code: '', name: '', parent_code: '', external_ref: '', is_active: true }

export default function CostCentersPage() {
  const [data, setData] = useState<any>(null)
  const [draft, setDraft] = useState<any>(blank)
  const [tag, setTag] = useState<any>({ pl_line_key: '', detail_label: '', cost_center_code: '' })
  const [period, setPeriod] = useState(lastMonth())
  const [view, setView] = useState<any>(null)
  const [openC, setOpenC] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const load = useCallback(() => api('/api/v1/account/centers').then(setData).catch(e => setErr(e?.message || String(e))), [])
  useEffect(() => { load() }, [load])
  const loadView = useCallback(() => {
    let alive = true
    api(`/api/v1/account/centers/cost-view/${encodeURIComponent(period)}`).then(d => { if (alive) setView(d) }).catch(e => { if (alive) setErr(e?.message || String(e)) })
    return () => { alive = false }
  }, [period])
  useEffect(() => loadView(), [loadView])
  const centers = (data?.centers || []).filter((c: any) => c.center_type === 'cost')
  const plName: Record<string, string> = Object.fromEntries((data?.pl_lines || []).map((l: any) => [l.key, l.label]))

  const save = (row: any) => api('/api/v1/account/centers', { method: 'PUT', body: JSON.stringify({ ...row, center_type: 'cost' }) })
    .then(() => { setDraft(blank); load() }).catch(e => setErr(e?.message || String(e)))
  const saveTag = (t: any) => api('/api/v1/account/centers/line-tag', { method: 'PUT', body: JSON.stringify(t) })
    .then(() => { setTag({ pl_line_key: '', detail_label: '', cost_center_code: '' }); load(); loadView() }).catch(e => setErr(e?.message || String(e)))

  return (
    <div style={{ padding: 16, maxWidth: 1200 }}>
      <h1 style={{ fontSize: 20, fontWeight: 700, margin: '0 0 4px' }}>Cost Centers</h1>
      <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 10 }}>Tag P&L lines (or one detail of a line) to a cost center; the view below regroups the P&L Statement by center and ties to it to the cent.</div>
      {err && <div style={{ color: '#b91c1c', marginBottom: 8 }}>{err}</div>}
      {data?.problems?.cost?.length > 0 && <div style={{ color: '#b45309', marginBottom: 8 }}>{data.problems.cost.join(' · ')}</div>}

      <div className="card" style={{ padding: 14, marginBottom: 14 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6 }}>Centers</div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>Code</th><th style={th}>Name</th><th style={th}>Parent</th><th style={th}>Franchisor reference</th><th style={th}>Active</th><th style={th}></th></tr></thead>
          <tbody>
            {centers.map((c: any) => (
              <tr key={c.code}><td style={td}>{c.code}</td><td style={td}>{c.name}</td><td style={td}>{c.parent_code || ''}</td><td style={td}>{c.external_ref || ''}</td>
                <td style={td}><input type="checkbox" checked={c.is_active} onChange={e => save({ ...c, is_active: e.target.checked })} /></td>
                <td style={td}><button className="btn" onClick={() => window.confirm(`Delete cost center ${c.code}?`) && api(`/api/v1/account/centers/cost/${encodeURIComponent(c.code)}`, { method: 'DELETE' }).then(load)}>Delete</button></td></tr>
            ))}
            <tr>
              <td style={td}><input placeholder="code" value={draft.code} onChange={e => setDraft({ ...draft, code: e.target.value })} /></td>
              <td style={td}><input placeholder="name" value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></td>
              <td style={td}><select value={draft.parent_code} onChange={e => setDraft({ ...draft, parent_code: e.target.value })}><option value="">—</option>{centers.map((c: any) => <option key={c.code} value={c.code}>{c.code}</option>)}</select></td>
              <td style={td}><input placeholder="assigned code" value={draft.external_ref} onChange={e => setDraft({ ...draft, external_ref: e.target.value })} /></td>
              <td style={td}></td>
              <td style={td}><button className="btn btn-primary" disabled={!draft.code.trim()} onClick={() => save(draft)}>Add</button></td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className="card" style={{ padding: 14, marginBottom: 14 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6 }}>Line tags</div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>P&L line</th><th style={th}>Detail (blank = whole line)</th><th style={th}>Cost center</th><th style={th}></th></tr></thead>
          <tbody>
            {(data?.line_tags || []).map((t: any) => (
              <tr key={t.pl_line_key + '|' + t.detail_label}><td style={td}>{plName[t.pl_line_key] || t.pl_line_key}</td><td style={td}>{t.detail_label || '—'}</td><td style={td}>{t.cost_center_code}</td>
                <td style={td}><button className="btn" onClick={() => saveTag({ ...t, cost_center_code: null })}>Remove</button></td></tr>
            ))}
            <tr>
              <td style={td}><select value={tag.pl_line_key} onChange={e => setTag({ ...tag, pl_line_key: e.target.value })}><option value="">— line —</option>{(data?.pl_lines || []).map((l: any) => <option key={l.key} value={l.key}>{l.label} ({l.section})</option>)}</select></td>
              <td style={td}><input placeholder="e.g. Rent" value={tag.detail_label} onChange={e => setTag({ ...tag, detail_label: e.target.value })} /></td>
              <td style={td}><select value={tag.cost_center_code} onChange={e => setTag({ ...tag, cost_center_code: e.target.value })}><option value="">— center —</option>{centers.map((c: any) => <option key={c.code} value={c.code}>{c.code} — {c.name}</option>)}</select></td>
              <td style={td}><button className="btn btn-primary" disabled={!tag.pl_line_key || !tag.cost_center_code} onClick={() => saveTag(tag)}>Tag</button></td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className="card" style={{ padding: 14 }}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 8 }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>P&L by cost center</div>
          <input type="month" value={period} onChange={e => setPeriod(e.target.value)} />
          {view?.computed && <span style={{ fontSize: 12.5, color: view.tie ? '#15803d' : '#b91c1c' }}>{view.tie ? 'ties to the P&L Statement' : 'does NOT tie to the P&L Statement'}</span>}
        </div>
        {view && !view.computed && <div style={{ color: '#b45309' }}>{view.note}</div>}
        {view?.unknown_codes?.length > 0 && <div style={{ color: '#b45309', fontSize: 13 }}>Tags name centers that do not exist: {view.unknown_codes.join(', ')}</div>}
        {view?.computed && (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr><th style={th}>Center</th><th style={thr}>Revenue</th><th style={thr}>COGS</th><th style={thr}>Expenses</th><th style={thr}>Other</th><th style={thr}>Net (incl. children)</th><th style={th}></th></tr></thead>
            <tbody>
              {view.centers.map((c: any) => (
                <Fragment key={String(c.code)}>
                  <tr><td style={td}>{c.code ? `${c.code} — ${c.name}` : 'Untagged'}</td>
                    <td style={tdr}>{fmt(c.own.revenue)}</td><td style={tdr}>{fmt(c.own.cogs)}</td><td style={tdr}>{fmt(c.own.opex)}</td><td style={tdr}>{fmt(c.own.other)}</td>
                    <td style={tdr}>{fmt(c.rolled_up.net_income)}</td>
                    <td style={td}><button className="btn" onClick={() => setOpenC(openC === String(c.code) ? null : String(c.code))}>{openC === String(c.code) ? 'Hide' : 'Lines'}</button></td></tr>
                  {openC === String(c.code) && c.lines.map((l: any, i: number) => (
                    <tr key={i}><td style={{ ...td, paddingLeft: 22 }} colSpan={5}>{l.label} <span style={{ color: 'var(--text3)' }}>({l.section})</span></td><td style={tdr}>{fmt(l.amount)}</td><td style={td}></td></tr>
                  ))}
                </Fragment>
              ))}
              <tr><td style={{ ...td, fontWeight: 700 }}>Statement</td><td style={{ ...tdr, fontWeight: 700 }}>{fmt(view.statement.revenue)}</td><td style={{ ...tdr, fontWeight: 700 }}>{fmt(view.statement.cogs)}</td>
                <td style={{ ...tdr, fontWeight: 700 }}>{fmt(view.statement.opex)}</td><td style={{ ...tdr, fontWeight: 700 }}>{fmt(view.statement.other)}</td><td style={{ ...tdr, fontWeight: 700 }}>{fmt(view.statement.net_income)}</td><td style={td}></td></tr>
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
