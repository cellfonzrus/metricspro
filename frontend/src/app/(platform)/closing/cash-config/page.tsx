'use client'
import { useEffect, useState, useCallback } from 'react'
import { api } from '@/lib/client'

// Cash-management configuration (mig 089): the daily-closing deadline + gate, the assigned closer
// per store (only they are blocked from clocking out until the store closing is in), the cash-aging
// alert window, and the alert recipients (auto-DM + named extras, email/WhatsApp) per alert type.
const SCOPES = [
  { key: 'closing_missing', label: 'Daily closing not submitted' },
  { key: 'cash_unpicked', label: 'Cash not picked up' },
  { key: 'deposit_mismatch', label: 'Deposit mismatch' },
  { key: 'connector', label: 'Data source failed / stale (imports, sweeps)' },
  { key: 'all', label: 'All alerts' },
]

const MATCH_TARGETS = [
  { key: 'total_cash', label: 'Total cash (whole envelope — declared cash + ePay cash combined)' },
  { key: 'store_cash', label: 'Store cash only (excludes the ePay/bill-payment portion)' },
  { key: 'bill_payment_cash', label: 'ePay bill-payment cash only' },
]

export default function CashConfigPage() {
  const [cfg, setCfg] = useState<any>(null)
  const [emps, setEmps] = useState<any[]>([])
  const [stores, setStores] = useState<any[]>([])
  const [rec, setRec] = useState<any>({ scope: 'all', via_email: true, via_whatsapp: false, include_dm: true })
  const [msg, setMsg] = useState('')
  const [depCfg, setDepCfg] = useState<any>(null)
  const [depMsg, setDepMsg] = useState('')
  const [cbPolicy, setCbPolicy] = useState<any[]>([])
  const [cbMsg, setCbMsg] = useState('')
  const [cbForbidden, setCbForbidden] = useState(false)

  const load = useCallback(() => {
    api('/api/v1/closing/cash-config').then(setCfg).catch((e: any) => setMsg('❌ ' + (e?.message || e)))
  }, [])
  const loadDepCfg = useCallback(() => {
    api('/api/v1/closing/deposit-config').then(setDepCfg).catch(() => {})
  }, [])
  const loadCbPolicy = useCallback(() => {
    api('/api/v1/closing/ops-chargebacks/policy').then((r: any) => setCbPolicy(r?.policy || [])).catch(() => {})
  }, [])
  useEffect(() => {
    load()
    loadDepCfg()
    loadCbPolicy()
    api('/api/v1/storeops/employees').then((r: any) => setEmps(Array.isArray(r) ? r : (r?.employees || []))).catch(() => {})
    api('/api/v1/storeops/stores').then((r: any) => setStores(Array.isArray(r) ? r : (r?.stores || []))).catch(() => {})
  }, [load, loadDepCfg, loadCbPolicy])

  async function saveDepCfg(match_target: string) {
    setDepMsg('')
    try { const r: any = await api('/api/v1/closing/deposit-config', { method: 'PUT', body: JSON.stringify({ match_target }) }); setDepCfg(r); setDepMsg('✅ Saved.') }
    catch (e: any) { setDepMsg('❌ ' + (e?.message || e)) }
  }

  function patchCbPolicy(reason: string, patch: any) {
    setCbPolicy(rows => rows.map(r => r.reason === reason ? { ...r, ...patch } : r))
  }
  async function saveCbPolicy() {
    setCbMsg(''); setCbForbidden(false)
    try {
      const r: any = await api('/api/v1/closing/ops-chargebacks/policy', { method: 'PUT', body: JSON.stringify({ policy: cbPolicy }) })
      setCbPolicy(r?.policy || cbPolicy); setCbMsg('✅ Saved.')
    } catch (e: any) {
      const s = e?.message || String(e)
      if (/permission|403|restricted/i.test(s)) setCbForbidden(true)
      setCbMsg('❌ ' + s)
    }
  }

  async function saveCfg(patch: any) {
    try { const r: any = await api('/api/v1/closing/cash-config', { method: 'PUT', body: JSON.stringify(patch) }); setCfg(r); setMsg('✅ Saved.') }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function setCloser(store_code: string, employee_id: string) {
    const emp = emps.find(e => String(e.employee_id || e.id) === employee_id)
    try { await api('/api/v1/closing/cash-config/closer', { method: 'PUT', body: JSON.stringify({ store_code, employee_id, employee_name: emp?.name }) }); load(); setMsg('✅ Closer set.') }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function addRecipient() {
    if (!rec.email && !rec.whatsapp && !rec.include_dm) { setMsg('Add an email/WhatsApp or keep "include DM".'); return }
    try { await api('/api/v1/closing/cash-config/recipient', { method: 'PUT', body: JSON.stringify(rec) }); setRec({ scope: rec.scope, via_email: true, via_whatsapp: false, include_dm: true }); load(); setMsg('✅ Recipient added.') }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function delRecipient(id: string) {
    try { await api(`/api/v1/closing/cash-config/recipient/${id}`, { method: 'DELETE' }); load() } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  if (!cfg) return <div style={{ padding: 8, color: 'var(--text3)' }}>{msg || 'Loading…'}</div>
  const closerBy: Record<string, any> = {}; (cfg.closers || []).forEach((c: any) => { closerBy[c.store_code] = c })
  const inp: React.CSSProperties = { padding: '7px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
  const card: React.CSSProperties = { padding: 16, marginBottom: 16 }

  return (
    <div style={{ maxWidth: 900 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 4px' }}>💵 Cash management — setup</h1>
      <p style={{ color: 'var(--text2)', fontSize: 14, margin: '0 0 14px' }}>Closing deadline & gate, assigned closers, cash-aging window, and who gets alerted.</p>
      {msg && <div style={{ fontSize: 13, marginBottom: 12 }}>{msg}</div>}

      <div className="card" style={card}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 10 }}>Closing gate & deadlines</div>
        <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', alignItems: 'center' }}>
          <label style={{ fontSize: 13 }}>Daily closing deadline<br />
            <input type="time" style={{ ...inp, marginTop: 4 }} defaultValue={cfg.closing_deadline || ''} onBlur={e => saveCfg({ closing_deadline: e.target.value })} /></label>
          <label style={{ fontSize: 13, display: 'flex', gap: 8, alignItems: 'center' }}>
            <input type="checkbox" checked={!!cfg.closing_gate_enabled} onChange={e => saveCfg({ closing_gate_enabled: e.target.checked })} />
            Block the assigned closer from clocking out until the store closing is submitted</label>
          <label style={{ fontSize: 13 }}>Alert if cash not picked up after (days)<br />
            <input type="number" style={{ ...inp, marginTop: 4, width: 90 }} defaultValue={cfg.cash_alert_after_days ?? ''} onBlur={e => saveCfg({ cash_alert_after_days: e.target.value })} /></label>
        </div>
        <div style={{ marginTop: 14, paddingTop: 14, borderTop: '1px solid var(--border)' }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>Who closes the store each day?</div>
          <label style={{ fontSize: 13, display: 'block', marginBottom: 4 }}>
            <input type="radio" name="closing_mode" checked={(cfg.closing_mode || 'per_rep') === 'per_rep'} onChange={() => saveCfg({ closing_mode: 'per_rep' })} style={{ marginRight: 6 }} />
            <b>One closing per rep</b> — every rep who worked the store tallies their own envelope.
          </label>
          <label style={{ fontSize: 13, display: 'block' }}>
            <input type="radio" name="closing_mode" checked={cfg.closing_mode === 'one_closing'} onChange={() => saveCfg({ closing_mode: 'one_closing' })} style={{ marginRight: 6 }} />
            <b>One closing per store</b> — only the assigned closer submits; they tally the whole store&apos;s cash (the onus is on the closer).
          </label>
          <p style={{ fontSize: 11, color: 'var(--text3)', margin: '6px 0 0' }}>
            The DM verify view checks who <i>actually worked</i> (clock-in ∪ B2B sales), so a scheduled rep who never showed isn&apos;t dunned, and a rep who sold under someone else&apos;s login is flagged.
          </p>
        </div>
      </div>

      <div className="card" style={card}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 10 }}>Assigned closer per store</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(280px,1fr))', gap: 10 }}>
          {stores.map(s => (
            <div key={s.store_code} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <span style={{ fontSize: 12, fontWeight: 600, minWidth: 70 }}>{s.store_code}</span>
              <select style={{ ...inp, flex: 1 }} value={String(closerBy[s.store_code]?.employee_id || '')} onChange={e => setCloser(s.store_code, e.target.value)}>
                <option value="">— no closer —</option>
                {emps.map(e => <option key={e.id} value={String(e.employee_id || e.id)}>{e.name}</option>)}
              </select>
            </div>
          ))}
        </div>
        {stores.length === 0 && <div style={{ fontSize: 13, color: 'var(--text3)' }}>No stores found.</div>}
      </div>

      <div className="card" style={card}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 10 }}>Alert recipients</div>
        <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 10px' }}>The store&apos;s District Manager is alerted automatically when &quot;include DM&quot; is on; add any extra people here.</p>
        {(cfg.recipients || []).length > 0 && (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, marginBottom: 10 }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>{['Alert', 'Name', 'Email', 'WhatsApp', 'Channels', 'DM', ''].map(h => <th key={h} style={{ textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
            <tbody>
              {cfg.recipients.map((r: any) => (
                <tr key={r.id} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '6px 8px' }}>{SCOPES.find(s => s.key === r.scope)?.label || r.scope}</td>
                  <td style={{ padding: '6px 8px' }}>{r.name || '—'}</td>
                  <td style={{ padding: '6px 8px', color: 'var(--text3)' }}>{r.email || '—'}</td>
                  <td style={{ padding: '6px 8px', color: 'var(--text3)' }}>{r.whatsapp || '—'}</td>
                  <td style={{ padding: '6px 8px', fontSize: 12 }}>{[r.via_email && 'email', r.via_whatsapp && 'WhatsApp'].filter(Boolean).join(' + ') || '—'}</td>
                  <td style={{ padding: '6px 8px' }}>{r.include_dm ? '✓' : ''}</td>
                  <td style={{ padding: '6px 8px', textAlign: 'right' }}><button className="btn btn-secondary" style={{ fontSize: 12, padding: '2px 8px', color: '#dc2626' }} onClick={() => delRecipient(r.id)}>✕</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', border: '1px dashed var(--border)', borderRadius: 8, padding: 10 }}>
          <select style={inp} value={rec.scope} onChange={e => setRec({ ...rec, scope: e.target.value })}>{SCOPES.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}</select>
          <input style={inp} placeholder="Name" value={rec.name || ''} onChange={e => setRec({ ...rec, name: e.target.value })} />
          <input style={inp} placeholder="Email" value={rec.email || ''} onChange={e => setRec({ ...rec, email: e.target.value })} />
          <input style={inp} placeholder="WhatsApp (+1…)" value={rec.whatsapp || ''} onChange={e => setRec({ ...rec, whatsapp: e.target.value })} />
          <label style={{ fontSize: 12, display: 'flex', gap: 4, alignItems: 'center' }}><input type="checkbox" checked={!!rec.via_email} onChange={e => setRec({ ...rec, via_email: e.target.checked })} />email</label>
          <label style={{ fontSize: 12, display: 'flex', gap: 4, alignItems: 'center' }}><input type="checkbox" checked={!!rec.via_whatsapp} onChange={e => setRec({ ...rec, via_whatsapp: e.target.checked })} />WhatsApp</label>
          <label style={{ fontSize: 12, display: 'flex', gap: 4, alignItems: 'center' }}><input type="checkbox" checked={!!rec.include_dm} onChange={e => setRec({ ...rec, include_dm: e.target.checked })} />include DM</label>
          <button className="btn btn-primary" style={{ fontSize: 12 }} onClick={addRecipient}>Add</button>
        </div>
      </div>

      <div className="card" style={card}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6 }}>🏦 Bank deposit slip — OCR verification</div>
        <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 10px' }}>
          When a deposit slip photo is attached (Cash Pickup or the ePay Bank-Deposit Recon page), the amount is
          read automatically and compared against your chosen basis below. A mismatch never blocks anything — it
          just flags loudly for management review (the &quot;Deposit mismatch&quot; alert scope above).
        </p>
        {depCfg && (
          <>
            {depCfg.anthropic_configured === false && (
              <div style={{ fontSize: 12, color: '#b45309', marginBottom: 8 }}>
                ⚠️ OCR is not configured on this server (no API key) — deposits still save; managers confirm the amount manually.
              </div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {MATCH_TARGETS.map(t => (
                <label key={t.key} style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 8 }}>
                  <input type="radio" name="match_target" checked={(depCfg.match_target || 'total_cash') === t.key} onChange={() => saveDepCfg(t.key)} />
                  {t.label}
                </label>
              ))}
            </div>
            {depMsg && <div style={{ fontSize: 12, marginTop: 8 }}>{depMsg}</div>}
          </>
        )}
        {!depCfg && <div style={{ fontSize: 12, color: 'var(--text3)' }}>Loading…</div>}
      </div>

      <div className="card" style={card}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6 }}>🧾 Ops chargeback amounts</div>
        <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 10px' }}>
          A missed daily closing charges the store&apos;s effective closer (decided at payroll); a missed DM
          verification charges the store&apos;s District Manager&apos;s commission (deducted from commission
          FIRST — any uncovered remainder falls to payroll or forwards to the next commission cycle, per the
          &quot;if unpaid&quot; setting below). Disabled (default) means nothing is ever charged for that reason.
          The list below is every reason this system knows about <i>plus</i> every reason that has actually
          occurred — pick from it, you can&apos;t type a new one here.
        </p>
        {cbForbidden && <div style={{ fontSize: 12, color: '#b45309', marginBottom: 8 }}>🔒 Editing these amounts is limited to company-wide leadership.</div>}
        {cbPolicy.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--text3)' }}>Loading…</div>
        ) : (
          <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {cbPolicy.map((p: any) => (
                <div key={p.reason} style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap', borderBottom: '1px solid var(--border)', paddingBottom: 10 }}>
                  <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6, minWidth: 130 }}>
                    <input type="checkbox" checked={!!p.enabled} onChange={e => patchCbPolicy(p.reason, { enabled: e.target.checked })} />
                    <span>
                      {p.reason}
                      {!p.known && <span className="badge" style={{ fontSize: 10, marginLeft: 6, padding: '1px 6px', background: 'var(--surface2)', color: 'var(--text3)' }}>custom</span>}
                    </span>
                  </label>
                  <label style={{ fontSize: 12, color: 'var(--text3)', flex: '1 1 220px' }}>Display message<br />
                    <input style={{ ...inp, marginTop: 4, width: '100%' }} value={p.label || ''} placeholder={p.reason}
                      onChange={e => patchCbPolicy(p.reason, { label: e.target.value })} /></label>
                  <label style={{ fontSize: 12, color: 'var(--text3)' }}>Amount ($)<br />
                    <input type="number" step="0.01" style={{ ...inp, marginTop: 4, width: 110 }} value={p.amount ?? 0}
                      onChange={e => patchCbPolicy(p.reason, { amount: e.target.value })} /></label>
                  {p.applied_to === 'commission' && (
                    <label style={{ fontSize: 12, color: 'var(--text3)' }}>If commission can&apos;t cover it<br />
                      <select style={{ ...inp, marginTop: 4 }} value={p.overflow || 'payroll'}
                        onChange={e => patchCbPolicy(p.reason, { overflow: e.target.value })}>
                        <option value="payroll">Remainder goes to payroll</option>
                        <option value="next_cycle">Forward remainder to next commission cycle</option>
                      </select></label>
                  )}
                  <span style={{ fontSize: 11, color: 'var(--text3)' }}>
                    {p.applied_to === 'commission' ? 'commission-first' : p.applied_to === 'payroll' ? 'decided at payroll' : '—'}
                  </span>
                </div>
              ))}
            </div>
            <button className="btn btn-primary" style={{ fontSize: 12, marginTop: 10 }} onClick={saveCbPolicy}>Save</button>
            {cbMsg && <span style={{ fontSize: 12, marginLeft: 10 }}>{cbMsg}</span>}
          </>
        )}
      </div>
    </div>
  )
}
