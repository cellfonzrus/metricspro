'use client'
import { useState, useEffect, useCallback } from 'react'
import { api } from '@/lib/client'
import { computePay, W4 } from '@/lib/payroll-tax'

// Payroll with tax withholding (Part B / B3). Pulls raw inputs (clocked + manual hours, rate, W-4)
// and computes FICA + federal + state withholding + net client-side. W-4 editable per employee; each
// row has a printable pay slip. Estimate (flat-rate) — labeled as such.
const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '8px', borderTop: '1px solid var(--border)', fontSize: 13, whiteSpace: 'nowrap' }
const $ = (n: number) => `$${(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const STATES = ['NY', 'NJ', 'PA', 'DE']
const FILING = ['Single', 'Married', 'HOH']

export default function PayrollTaxPage() {
  const today = new Date(); const weekAgo = new Date(); weekAgo.setDate(today.getDate() - 6)
  const [start, setStart] = useState(iso(weekAgo))
  const [end, setEnd] = useState(iso(today))
  const [rows, setRows] = useState<any[]>([])
  const [edit, setEdit] = useState<string>('')        // employee_id whose W-4 is being edited
  const [slip, setSlip] = useState<any>(null)
  const [msg, setMsg] = useState('')

  const load = useCallback(() => {
    api(`/api/v1/storeops/payroll-raw?start=${start}&end=${end}`).then((r: any) => setRows(r?.rows || [])).catch((e: any) => setMsg('Load failed (run migration 045?): ' + (e?.message || e)))
  }, [start, end])
  useEffect(() => { load() }, [load])

  async function saveW4(eid: string, w4: W4) {
    try { await api(`/api/v1/storeops/payroll-settings/${encodeURIComponent(eid)}`, { method: 'PUT', body: JSON.stringify(w4) }); setMsg('✅ W-4 saved.'); setEdit(''); load() }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  const lines = rows.map(r => ({ ...r, pay: computePay(r.total_hours, r.pay_rate, r.settings) }))
  const tot = lines.reduce((a, l) => ({ gross: a.gross + l.pay.gross, ded: a.ded + l.pay.deductions, net: a.net + l.pay.net, emp: a.emp + l.pay.employer_fica }), { gross: 0, ded: 0, net: 0, emp: 0 })

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>💵 Payroll (with tax)</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Gross from clocked + manual hours, with FICA, federal and state withholding → net. <b>Estimate</b> (flat-rate) — not a substitute for your payroll provider.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 12 }}>From <input type="date" style={sel} value={start} onChange={e => setStart(e.target.value)} /></label>
        <label style={{ fontSize: 12 }}>To <input type="date" style={sel} value={end} onChange={e => setEnd(e.target.value)} /></label>
        <div style={{ flex: 1 }} />
        <span className="badge" style={{ fontSize: 12 }}>Gross {$(tot.gross)}</span>
        <span className="badge" style={{ fontSize: 12 }}>Net {$(tot.net)}</span>
        {msg && <span style={{ fontSize: 13, width: '100%' }}>{msg}</span>}
      </div>

      <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1000 }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            {['Employee', 'Hrs (reg/OT)', 'Rate', 'Gross', 'SS', 'Medicare', 'Federal', 'State', 'Net', 'W-4', ''].map(h =>
              <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {lines.map(l => (
              <tr key={l.employee_id}>
                <td style={cell}>{l.name}<div style={{ fontSize: 10, color: 'var(--text3)' }}>{l.store || ''}</div></td>
                <td style={cell}>{l.pay.regular_hours}{l.pay.ot_hours > 0 && <span style={{ color: '#b45309' }}> +{l.pay.ot_hours} OT</span>}</td>
                <td style={cell}>{$(l.pay_rate)}</td>
                <td style={{ ...cell, fontWeight: 600 }}>{$(l.pay.gross)}</td>
                <td style={cell}>{$(l.pay.fica_ss)}</td>
                <td style={cell}>{$(l.pay.fica_medicare)}</td>
                <td style={cell}>{$(l.pay.federal)}</td>
                <td style={cell}>{$(l.pay.state)}{l.pay.disability > 0 && <div style={{ fontSize: 10, color: 'var(--text3)' }}>+{$(l.pay.disability)} dis</div>}</td>
                <td style={{ ...cell, fontWeight: 700, color: '#16794a' }}>{$(l.pay.net)}</td>
                <td style={{ ...cell, fontSize: 11, color: 'var(--text3)' }}>{l.settings.filing_status[0]} · {l.settings.state}{l.settings.skipped ? ' · flat' : ''}</td>
                <td style={cell}>
                  <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setEdit(l.employee_id)}>W-4</button>{' '}
                  <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setSlip(l)}>Slip</button>
                </td>
              </tr>
            ))}
            {lines.length === 0 && <tr><td colSpan={11} style={{ textAlign: 'center', padding: 36, color: 'var(--text3)' }}>No hours in range. Employees clock in from the /portal; closed punches show here.</td></tr>}
          </tbody>
        </table>
      </div>
      <p style={{ fontSize: 11, color: 'var(--text3)', marginTop: 8 }}>
        Employer FICA match this period: {$(tot.emp)} (cost, not a deduction). Federal/state are flat-rate estimates; FUTA/SUTA are employer taxes filed separately.
      </p>

      {edit && <W4Modal line={lines.find(l => l.employee_id === edit)} onClose={() => setEdit('')} onSave={saveW4} />}
      {slip && <SlipModal line={slip} start={start} end={end} onClose={() => setSlip(null)} />}
    </div>
  )
}

function W4Modal({ line, onClose, onSave }: { line: any; onClose: () => void; onSave: (eid: string, w4: W4) => void }) {
  const [w4, setW4] = useState<W4>(line?.settings || { filing_status: 'Single', allowances: 0, state: 'NY', extra_withholding: 0, skipped: false })
  if (!line) return null
  return (
    <Overlay onClose={onClose}>
      <h3 style={{ marginTop: 0 }}>W-4 — {line.name}</h3>
      <div style={{ display: 'grid', gap: 10, fontSize: 13 }}>
        <label>Filing status <select style={sel} value={w4.filing_status} onChange={e => setW4({ ...w4, filing_status: e.target.value })}>{FILING.map(f => <option key={f}>{f}</option>)}</select></label>
        <label>Allowances <input type="number" style={{ ...sel, width: 80 }} value={w4.allowances} onChange={e => setW4({ ...w4, allowances: Number(e.target.value) || 0 })} /></label>
        <label>State <select style={sel} value={w4.state} onChange={e => setW4({ ...w4, state: e.target.value })}>{STATES.map(s => <option key={s}>{s}</option>)}</select></label>
        <label>Extra withholding / period <input type="number" style={{ ...sel, width: 100 }} value={w4.extra_withholding} onChange={e => setW4({ ...w4, extra_withholding: Number(e.target.value) || 0 })} /></label>
        <label><input type="checkbox" checked={w4.skipped} onChange={e => setW4({ ...w4, skipped: e.target.checked })} /> Use flat 22% federal (skip W-4 table)</label>
      </div>
      <div style={{ marginTop: 14, display: 'flex', gap: 8 }}>
        <button className="btn btn-primary" onClick={() => onSave(line.employee_id, w4)}>Save</button>
        <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
      </div>
    </Overlay>
  )
}

function SlipModal({ line, start, end, onClose }: { line: any; start: string; end: string; onClose: () => void }) {
  const p = line.pay
  const Row = ({ k, v, bold }: { k: string; v: string; bold?: boolean }) => (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '3px 0', fontWeight: bold ? 700 : 400, borderTop: bold ? '1px solid #ccc' : 'none' }}><span>{k}</span><span>{v}</span></div>
  )
  return (
    <Overlay onClose={onClose}>
      <div id="slip" style={{ fontSize: 13 }}>
        <h3 style={{ margin: '0 0 2px' }}>Pay Slip — {line.name}</h3>
        <div style={{ color: '#666', fontSize: 12, marginBottom: 10 }}>{start} → {end} · {line.store || ''}</div>
        <Row k={`Regular (${p.regular_hours}h @ ${$(line.pay_rate)})`} v={$(p.regular_hours * line.pay_rate)} />
        {p.ot_hours > 0 && <Row k={`Overtime (${p.ot_hours}h @ 1.5×)`} v={$(p.ot_hours * line.pay_rate * 1.5)} />}
        <Row k="Gross pay" v={$(p.gross)} bold />
        <Row k="Social Security (6.2%)" v={'-' + $(p.fica_ss)} />
        <Row k="Medicare (1.45%)" v={'-' + $(p.fica_medicare)} />
        <Row k="Federal withholding" v={'-' + $(p.federal)} />
        <Row k={`State (${line.settings.state})`} v={'-' + $(p.state)} />
        {p.disability > 0 && <Row k="NY Disability" v={'-' + $(p.disability)} />}
        <Row k="Total deductions" v={'-' + $(p.deductions)} bold />
        <Row k="Net pay" v={$(p.net)} bold />
        <div style={{ marginTop: 10, fontSize: 11, color: '#999' }}>Employer FICA match: {$(p.employer_fica)}. Estimate (flat-rate withholding).</div>
      </div>
      <div style={{ marginTop: 14, display: 'flex', gap: 8 }}>
        <button className="btn btn-primary" onClick={() => window.print()}>🖨 Print</button>
        <button className="btn btn-secondary" onClick={onClose}>Close</button>
      </div>
    </Overlay>
  )
}

function Overlay({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50, padding: 16 }}>
      <div onClick={e => e.stopPropagation()} className="card" style={{ padding: 20, maxWidth: 420, width: '100%', background: 'var(--surface)' }}>{children}</div>
    </div>
  )
}
