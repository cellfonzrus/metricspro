'use client'
import { useState, useEffect, useCallback } from 'react'
import { api, fmt, localToday } from '@/lib/client'

// Rep-facing in-app closing form — one row per rep per day. Posts to /closing/row (source='manual').
// Money is captured by the 6 tender types that mirror the POS X-report (cash / credit / external CC /
// gift card / store account / zelle). Shared by the platform /closing/submit page AND the /portal kiosk.
const inp: React.CSSProperties = { padding: '8px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 14, background: 'var(--surface)', width: '100%' }
const cell: React.CSSProperties = { padding: '6px 9px', borderBottom: '1px solid var(--border)', fontSize: 13 }

// The 6 tender fields, in display order — labels match the X-report vocabulary.
const TENDERS: { key: TenderKey; label: string }[] = [
  { key: 't_cash', label: 'Cash $' },
  { key: 't_credit', label: 'Credit $' },
  { key: 't_ext_cc', label: 'External Credit Card $' },
  { key: 't_gift', label: 'Gift Card $' },
  { key: 't_store_acct', label: 'Store Account $' },
  { key: 't_zelle', label: 'Zelle / CashApp $' },
  { key: 't_acima', label: 'ACIMA (lease) $' },
]
type TenderKey = 't_cash' | 't_credit' | 't_ext_cc' | 't_gift' | 't_store_acct' | 't_zelle' | 't_acima'

type State = {
  close_date: string; sfid: string; store_name: string; store_code: string; employee_name: string
  t_cash: string; t_credit: string; t_ext_cc: string; t_gift: string; t_store_acct: string; t_zelle: string; t_acima: string
  epay_on_cash: string; epay_on_credit: string; epay_on_acima: string
  acc_sale: string
  expense_amount: string; expense_description: string
  upgrade_count: string; new_line_count: string; postpaid_count: string; envelope_picture: string; remarks: string
}

const blank = (): State => ({
  close_date: localToday(), sfid: '', store_name: '', store_code: '', employee_name: '',
  t_cash: '', t_credit: '', t_ext_cc: '', t_gift: '', t_store_acct: '', t_zelle: '', t_acima: '', epay_on_cash: '', epay_on_credit: '', epay_on_acima: '', acc_sale: '',
  expense_amount: '', expense_description: '',
  upgrade_count: '', new_line_count: '', postpaid_count: '', envelope_picture: '', remarks: '',
})
// Total collected = the tender boxes ONLY. Accessory is declared separately (tallied vs sales), NOT a tender.
const MONEY_KEYS: (keyof State)[] = ['t_cash', 't_credit', 't_ext_cc', 't_gift', 't_store_acct', 't_zelle', 't_acima']
// The 7 built-in tender_keys that map to physical t_* columns; anything else is a custom tender (mig 111).
const STD_KEYS = ['cash', 'credit', 'ext_cc', 'gift', 'store_acct', 'zelle', 'acima']

// The 3 built-in activation-count fields, in display order (mig 501). field_key IS the physical
// daily_closing column name for these three.
const COUNTS: { key: 'upgrade_count' | 'new_line_count' | 'postpaid_count'; label: string }[] = [
  { key: 'upgrade_count', label: 'Upgrades #' },
  { key: 'new_line_count', label: 'New Lines #' },
  { key: 'postpaid_count', label: 'Postpaid #' },
]

export default function ClosingSubmitForm({ defaultEmployeeName = '', onSubmitted }:
  { defaultEmployeeName?: string; onSubmitted?: () => void }) {
  const [f, setF] = useState<State>(blank())
  const [stores, setStores] = useState<any[]>([])
  const [recent, setRecent] = useState<any[]>([])
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [retry, setRetry] = useState<{ message: string } | null>(null)
  const [envPreview, setEnvPreview] = useState('')
  const [ocrCash, setOcrCash] = useState('')
  const [ocrAmounts, setOcrAmounts] = useState<number[]>([])
  const [ocrBusy, setOcrBusy] = useState(false)
  const [tdefs, setTdefs] = useState<any[] | null>(null)     // configured tenders (null = built-in 7, static)
  const [tv, setTv] = useState<Record<string, string>>({})   // amount per configured tender_key
  const [cdefs, setCdefs] = useState<any[] | null>(null)      // configured count fields (null = built-in 3, static)
  const [cv, setCv] = useState<Record<string, string>>({})    // value per configured field_key

  const set = (patch: Partial<State>) => setF(p => ({ ...p, ...patch }))

  const enteredCash = parseFloat(tdefs ? (tv['cash'] || '') : f.t_cash) || 0
  const ocrNum = parseFloat(ocrCash) || 0
  const ocrMismatch = ocrCash !== '' && Math.abs(ocrNum - enteredCash) > 1

  async function onPickPhoto(file: File) {
    const reader = new FileReader()
    reader.onload = async () => {
      const dataUrl = reader.result as string
      setEnvPreview(dataUrl)
      try { const u: any = await api('/api/v1/closing/envelope-photo', { method: 'POST', body: JSON.stringify({ image: dataUrl }) }); set({ envelope_picture: u.path }) }
      catch (e: any) { setMsg('Photo upload failed: ' + (e?.message || e)) }
      runOcr(dataUrl)
    }
    reader.readAsDataURL(file)
  }
  async function runOcr(dataUrl: string) {
    setOcrBusy(true); setOcrAmounts([])
    try {
      const T = await loadTesseract()
      const { data } = await T.recognize(dataUrl, 'eng')
      const nums = (String(data?.text || '').match(/\d[\d,]*\.?\d{0,2}/g) || [])
        .map((s: string) => parseFloat(s.replace(/,/g, ''))).filter((n: number) => !isNaN(n) && n >= 1)
      setOcrAmounts(nums)
      if (nums.length) setOcrCash(String(Math.max(...nums)))
    } catch { /* OCR best-effort */ } finally { setOcrBusy(false) }
  }

  useEffect(() => { api('/api/v1/closing/stores').then(s => setStores(s || [])).catch(() => {}) }, [])
  // Configured tenders (mig 111): render the tenant's own tender fields; null → the built-in 7 (static).
  useEffect(() => { api('/api/v1/closing/tender-config').then((d: any) => setTdefs((d?.defs && d.defs.length) ? d.defs : null)).catch(() => setTdefs(null)) }, [])
  // Configured count fields (mig 501): render the tenant's own activation-count fields; null → the
  // built-in 3 (static), so an un-opted tenant's form is byte-identical to today.
  useEffect(() => { api('/api/v1/closing/count-config').then((d: any) => setCdefs((d?.defs && d.defs.length) ? d.defs : null)).catch(() => setCdefs(null)) }, [])
  useEffect(() => { if (defaultEmployeeName && !f.employee_name) set({ employee_name: defaultEmployeeName }) }, [defaultEmployeeName]) // eslint-disable-line

  const loadRecent = useCallback(() => {
    if (!f.close_date) return
    api(`/api/v1/closing/days?date=${f.close_date}`).then(r => setRecent(r || [])).catch(() => {})
  }, [f.close_date])
  useEffect(() => { loadRecent() }, [loadRecent])

  function pickStore(idx: string) {
    const s = stores[Number(idx)]
    if (!s) { set({ sfid: '', store_code: '', store_name: '' }); return }
    set({ sfid: s.sfid, store_code: s.store_code, store_name: s.store_address || s.store_code })
  }

  async function submit() {
    if (!f.close_date) { setMsg('❌ Pick a date.'); return }
    if (!f.sfid && !f.store_code) { setMsg('❌ Pick your store.'); return }
    if (!f.employee_name.trim()) { setMsg('❌ Enter your name.'); return }
    if ((parseFloat(f.expense_amount) || 0) > 0 && !f.expense_description.trim()) { setMsg('❌ Describe the expense before submitting.'); return }
    setBusy(true); setMsg('')
    // Build the tender fields: configured tenders → standard keys to t_*, custom keys to custom_tenders;
    // no config → the static t_* fields (unchanged behaviour).
    let tenderFields: any
    let customTenders: any = undefined
    if (tdefs) {
      tenderFields = {}; customTenders = {}
      for (const d of tdefs) {
        const val = tv[d.tender_key] || ''
        if (STD_KEYS.includes(d.tender_key)) tenderFields['t_' + d.tender_key] = val
        else customTenders[d.tender_key] = val
      }
    } else {
      tenderFields = { t_cash: f.t_cash, t_credit: f.t_credit, t_ext_cc: f.t_ext_cc,
        t_gift: f.t_gift, t_store_acct: f.t_store_acct, t_zelle: f.t_zelle, t_acima: f.t_acima }
    }
    // Build the count fields: configured count fields (mig 501) → a single `counts` map keyed by
    // field_key (standard field_keys route to the physical column server-side, custom ones to jsonb);
    // no config → the static upgrade_count/new_line_count/postpaid_count fields (unchanged behaviour).
    let countFields: any
    if (cdefs) {
      countFields = { counts: Object.fromEntries(cdefs.map((d: any) => [d.field_key, cv[d.field_key] || '0'])) }
    } else {
      countFields = { upgrade_count: f.upgrade_count, new_line_count: f.new_line_count, postpaid_count: f.postpaid_count }
    }
    try {
      const r = await api('/api/v1/closing/row', { method: 'POST', body: JSON.stringify({
        close_date: f.close_date, sfid: f.sfid, store_code: f.store_code, store_name: f.store_name,
        employee_name: f.employee_name.trim(),
        ...tenderFields, custom_tenders: customTenders,
        epay_on_cash: f.epay_on_cash, epay_on_credit: f.epay_on_credit, epay_on_acima: f.epay_on_acima,
        acc_sale: f.acc_sale,
        expense_amount: f.expense_amount, expense_description: f.expense_description.trim(),
        ...countFields,
        envelope_picture: f.envelope_picture, remarks: f.remarks,
        ocr_cash: ocrCash || undefined,
      }) })
      // Not accepted → recount (direction only, never the amount). Keep the form so they can re-enter.
      if (r && r.accepted === false && r.retry) {
        setRetry({ message: r.retry.message })
        setMsg('')
        return
      }
      setRetry(null)
      const flags: string[] = r?.recon?.flags || []
      const pending = r?.recon?.status === 'recon_pending'
      const auto = r?.recon?.auto_accepted
      setMsg(auto ? `✅ Submitted — your report does not match the system and has been sent for management review.`
        : flags.length ? `⚠️ Submitted — ${flags.join('; ')}`
        : pending ? '✅ Submitted (B2B not loaded yet — will reconcile once it lands).'
        : '✅ Closing submitted and tallies with the system. You can enter another below.')
      setF(p => ({ ...p, t_cash: '', t_credit: '', t_ext_cc: '', t_gift: '', t_store_acct: '', t_zelle: '', t_acima: '', epay_on_cash: '', epay_on_credit: '', epay_on_acima: '', acc_sale: '',
        expense_amount: '', expense_description: '',
        upgrade_count: '', new_line_count: '', postpaid_count: '', envelope_picture: '', remarks: '' }))
      setTv({}); setCv({})
      setEnvPreview(''); setOcrCash(''); setOcrAmounts([])
      loadRecent()
      onSubmitted?.()
    } catch (e: any) { setMsg('🚫 ' + (e?.message || e)) }
    finally { setBusy(false) }
  }

  const storeIdx = stores.findIndex(s => (f.sfid && s.sfid === f.sfid) || (!f.sfid && f.store_code && s.store_code === f.store_code))
  const total = tdefs
    ? tdefs.reduce((a, d) => a + (parseFloat(tv[d.tender_key] || '') || 0), 0)
    : MONEY_KEYS.reduce((a, k) => a + (parseFloat(f[k] as string) || 0), 0)
  // The "recent submissions" columns: configured count fields (mig 501), else the built-in 3.
  const countCols = cdefs
    ? cdefs.map((d: any) => ({ key: d.field_key as string, label: d.label || d.field_key }))
    : COUNTS.map(c => ({ key: c.key as string, label: c.label.replace(' #', '') }))
  // A row's value for one count field_key: a standard key is a physical column, a custom one lives
  // in the `counts` jsonb (mig 501).
  function countVal(r: any, key: string) {
    return key in r ? r[key] : (r.counts?.[key] ?? 0)
  }

  return (
    <>
      <div className="card" style={{ padding: 18 }}>
        <Row>
          <Field label="Date"><input type="date" style={inp} value={f.close_date} onChange={e => set({ close_date: e.target.value })} /></Field>
          <Field label="Store">
            <select style={inp} value={storeIdx >= 0 ? String(storeIdx) : ''} onChange={e => pickStore(e.target.value)}>
              <option value="">Select store…</option>
              {stores.map((s, i) => <option key={i} value={i}>{s.store_address || s.store_code}{s.market ? ` · ${s.market}` : ''}</option>)}
            </select>
          </Field>
          <Field label="Employee"><input style={inp} value={f.employee_name} onChange={e => set({ employee_name: e.target.value })} placeholder="Your name" /></Field>
        </Row>

        <SectionLabel>Money collected — by tender (matches the X-report)</SectionLabel>
        {tdefs ? (
          <Row>
            {tdefs.map((d: any) => (
              <Field key={d.tender_key} label={`${d.label || d.tender_key} $`}>
                <input style={inp} inputMode="decimal" value={tv[d.tender_key] || ''} onChange={e => setTv(v => ({ ...v, [d.tender_key]: e.target.value }))} placeholder="0.00" />
              </Field>
            ))}
          </Row>
        ) : (
          <>
            <Row>
              {TENDERS.slice(0, 3).map(t => (
                <Field key={t.key} label={t.label}><input style={inp} inputMode="decimal" value={f[t.key]} onChange={e => set({ [t.key]: e.target.value } as Partial<State>)} placeholder="0.00" /></Field>
              ))}
            </Row>
            <Row>
              {TENDERS.slice(3).map(t => (
                <Field key={t.key} label={t.label}><input style={inp} inputMode="decimal" value={f[t.key]} onChange={e => set({ [t.key]: e.target.value } as Partial<State>)} placeholder="0.00" /></Field>
              ))}
            </Row>
          </>
        )}
        <Row>
          <Field label="Accessory Sale $ (declared — tallied vs sales, NOT in total)"><input style={inp} inputMode="decimal" value={f.acc_sale} onChange={e => set({ acc_sale: e.target.value })} placeholder="0.00" /></Field>
          <Field label="Total collected (tenders only)"><div style={{ ...inp, background: 'var(--surface2)', fontWeight: 700 }}>{fmt(total)}</div></Field>
        </Row>

        <SectionLabel>Of which ePay bill payments (already inside the tenders above — NOT added to the total)</SectionLabel>
        <Row>
          <Field label="ePay on Cash $"><input style={inp} inputMode="decimal" value={f.epay_on_cash} onChange={e => set({ epay_on_cash: e.target.value })} placeholder="0.00" /></Field>
          <Field label="ePay on Credit $"><input style={inp} inputMode="decimal" value={f.epay_on_credit} onChange={e => set({ epay_on_credit: e.target.value })} placeholder="0.00" /></Field>
          <Field label="ePay on Financing / ACIMA $"><input style={inp} inputMode="decimal" value={f.epay_on_acima} onChange={e => set({ epay_on_acima: e.target.value })} placeholder="0.00" /></Field>
        </Row>

        <SectionLabel>Transaction counts</SectionLabel>
        {cdefs ? (
          <Row>
            {cdefs.map((d: any) => (
              <Field key={d.field_key} label={`${d.label || d.field_key} #`}>
                <input style={inp} inputMode="numeric" value={cv[d.field_key] || ''} onChange={e => setCv(v => ({ ...v, [d.field_key]: e.target.value }))} placeholder="0" />
              </Field>
            ))}
          </Row>
        ) : (
          <Row>
            {COUNTS.map(c => (
              <Field key={c.key} label={c.label}><input style={inp} inputMode="numeric" value={f[c.key]} onChange={e => set({ [c.key]: e.target.value } as Partial<State>)} placeholder="0" /></Field>
            ))}
          </Row>
        )}

        <SectionLabel>Expense incurred (reimbursement — DM approves)</SectionLabel>
        <Row>
          <Field label="Expense amount $"><input style={inp} inputMode="decimal" value={f.expense_amount} onChange={e => set({ expense_amount: e.target.value })} placeholder="0.00" /></Field>
          <Field label={`Description${(parseFloat(f.expense_amount) || 0) > 0 ? ' (required)' : ''}`} wide>
            <input style={inp} value={f.expense_description} onChange={e => set({ expense_description: e.target.value })} placeholder="What was the expense for? (required if an amount is entered)" />
          </Field>
        </Row>

        <SectionLabel>Envelope photo & remarks</SectionLabel>
        <Row>
          <Field label="Envelope photo" wide>
            <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
              <label className="btn btn-secondary" style={{ fontSize: 13, cursor: 'pointer' }}>
                📷 Take / choose photo
                <input type="file" accept="image/*" capture="environment" style={{ display: 'none' }} onChange={e => { const file = e.target.files?.[0]; if (file) onPickPhoto(file) }} />
              </label>
              {envPreview && <img src={envPreview} alt="envelope" style={{ height: 70, borderRadius: 8, border: '1px solid var(--border)' }} />}
              {ocrBusy && <span style={{ fontSize: 13, color: 'var(--text3)' }}>🔍 Reading envelope…</span>}
            </div>
            {(ocrCash !== '' || ocrAmounts.length > 0) && (
              <div style={{ marginTop: 8, padding: 10, borderRadius: 8, background: ocrMismatch ? '#fdeaea' : '#e7f6ec', fontSize: 13 }}>
                <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                  <span>📸 OCR cash read: <b>$</b><input style={{ ...inp, width: 110, display: 'inline-block', padding: '4px 8px' }} inputMode="decimal" value={ocrCash} onChange={e => setOcrCash(e.target.value)} /></span>
                  <span>vs cash entered <b>${enteredCash.toFixed(2)}</b></span>
                  {ocrMismatch ? <span style={{ color: '#b42318', fontWeight: 600 }}>⚠️ mismatch — off by ${Math.abs(ocrNum - enteredCash).toFixed(2)}</span> : <span style={{ color: '#16794a' }}>✓ matches</span>}
                </div>
                {ocrAmounts.length > 0 && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>amounts detected: {ocrAmounts.map(n => `$${n}`).join(', ')} — adjust the read above if wrong (handwriting reads roughly).</div>}
              </div>
            )}
          </Field>
        </Row>
        <Row>
          <Field label="Remarks" wide><input style={inp} value={f.remarks} onChange={e => set({ remarks: e.target.value })} placeholder="Optional note" /></Field>
        </Row>

        {retry && (
          <div style={{ marginTop: 14, padding: 12, borderRadius: 8, background: '#fdeaea', border: '1px solid #f3b4b4' }}>
            <div style={{ fontWeight: 700, color: '#b42318', fontSize: 14 }}>⚠️ Report doesn’t match — recount needed</div>
            <div style={{ fontSize: 13, marginTop: 4 }}>{retry.message}</div>
          </div>
        )}

        <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 16 }}>
          <button className="btn btn-primary" style={{ fontSize: 14 }} disabled={busy} onClick={submit}>{busy ? '⏳ Submitting…' : retry ? '🔁 Re-submit count' : '✅ Submit closing'}</button>
          {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
        </div>
        <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 8 }}>
          🔒 Your close is checked against the system. If your report does not match, it will be reviewed by management.
        </div>
      </div>

      <div style={{ marginTop: 20 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text2)', marginBottom: 8 }}>Submissions for {f.close_date} ({recent.length})</div>
        {recent.length === 0 ? (
          <div className="card" style={{ padding: 20, textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>No entries yet for this date.</div>
        ) : (
          <div className="card table-wrapper" style={{ padding: 0 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                {['Employee', 'Store', 'Cash', 'Credit', 'Ext CC', 'Gift', 'Acct', 'Zelle', 'Acc', ...countCols.map(c => c.label), ''].map((h, i) =>
                  <th key={i} style={{ textAlign: 'left', padding: '6px 9px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
              </tr></thead>
              <tbody>
                {recent.map((r: any) => (
                  <tr key={r.id}>
                    <td style={cell}>{r.employee_name || '—'}</td>
                    <td style={cell}>{r.store_address || r.store_name || r.store_code || '—'}</td>
                    <td style={cell}>{fmt(r.t_cash ?? ((r.store_cash || 0) + (r.epay_cash || 0)))}</td>
                    <td style={cell}>{fmt(r.t_credit ?? ((r.store_cc || 0) + (r.epay_cc || 0)))}</td>
                    <td style={cell}>{fmt(r.t_ext_cc)}</td>
                    <td style={cell}>{fmt(r.t_gift)}</td>
                    <td style={cell}>{fmt(r.t_store_acct)}</td>
                    <td style={cell}>{fmt(r.t_zelle ?? r.other_account)}</td>
                    <td style={cell}>{fmt(r.acc_sale)}</td>
                    {countCols.map(c => <td key={c.key} style={cell}>{countVal(r, c.key)}</td>)}
                    <td style={cell}>{r.auto_accepted ? <span title="accepted after 3 tries — under management review" style={{ fontSize: 11, color: '#b42318' }}>⚑ review</span> : <span style={{ fontSize: 11, color: 'var(--text3)' }}>{r.source === 'manual' ? 'form' : 'sheet'}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  )
}

const Row = ({ children }: { children: React.ReactNode }) => (
  <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>{children}</div>
)
const Field = ({ label, children, wide }: { label: string; children: React.ReactNode; wide?: boolean }) => (
  <label style={{ flex: wide ? '1 1 100%' : '1 1 200px', minWidth: 160 }}>
    <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 600, marginBottom: 4 }}>{label}</div>
    {children}
  </label>
)
const SectionLabel = ({ children }: { children: React.ReactNode }) => (
  <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', margin: '14px 0 8px', borderTop: '1px solid var(--border)', paddingTop: 12 }}>{children}</div>
)

// tesseract.js loaded on demand from CDN (in-browser OCR, no API key). Best-effort: printed digits
// read well, handwriting roughly — the rep confirms/edits the read before submitting.
let _tessP: Promise<any> | null = null
function loadTesseract(): Promise<any> {
  if ((window as any).Tesseract) return Promise.resolve((window as any).Tesseract)
  if (_tessP) return _tessP
  _tessP = new Promise((resolve, reject) => {
    const s = document.createElement('script')
    s.src = 'https://cdn.jsdelivr.net/npm/tesseract.js@5/dist/tesseract.min.js'
    s.onload = () => resolve((window as any).Tesseract)
    s.onerror = reject
    document.body.appendChild(s)
  })
  return _tessP
}
