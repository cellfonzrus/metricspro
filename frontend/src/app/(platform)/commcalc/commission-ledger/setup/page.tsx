'use client'
import { useEffect, useState } from 'react'
import { api, apiUpload } from '@/lib/client'

// Guided SETUP WIZARD for the Commission Ledger. Walks a non-technical user through: pick a carrier
// template → upload a commission file → confirm the columns we detected + preview how each line buckets
// (nothing saved yet) → set a period and import. Plain-language instructions at every step. Read-only
// preview uses /commission-ledger/analyze; the final step calls /commission-ledger/import.

type Tmpl = { key: string; label: string; builtin: boolean; rule_count: number }
type Analysis = {
  row_count: number; usable_rows: number; headers: string[]; amount_source: string
  suggestions: { target_field: string; label: string; suggested_source: string; confidence: string }[]
  summary: { payout_total: number; charge_total: number; other_total: number; other_count: number; line_count: number; categories: Record<string, { total: number; count: number }> }
  observed: { order_type: string; product_name: string; count: number; payout_total: number; category: string }[]
  categories: string[]; category_labels: Record<string, string>
}
const CATS = ['commission', 'spiff', 'equipment_rebate', 'residual_monthly', 'autopay_residual']
const KEY_FIELDS = [
  { tf: 'raw_amount', label: 'Amount', hint: 'the money column — negative = a payout', star: true, transform: 'number' },
  { tf: 'product_name', label: 'Product / description', hint: 'drives which bucket a line goes to', star: true, transform: 'text' },
  { tf: 'order_type', label: 'Order type', hint: 'a secondary classifier', star: false, transform: 'text' },
  { tf: 'rep_user', label: 'Rep / salesperson', hint: 'who earned it', star: false, transform: 'text' },
  { tf: 'trans_date', label: 'Transaction date', hint: 'optional', star: false, transform: 'date10' },
]
const money = (n: number) => (n || 0).toLocaleString('en-US', { style: 'currency', currency: 'USD' })
const inp: React.CSSProperties = { padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const btn: React.CSSProperties = { ...inp, cursor: 'pointer', fontWeight: 600 }
const primary: React.CSSProperties = { ...btn, background: 'var(--accent,#2563eb)', color: '#fff', border: 'none', padding: '9px 18px' }
const card: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 12, padding: 16, background: 'var(--surface)' }
const STEPS = ['Carrier', 'Upload file', 'Review', 'Import']

export default function CommissionLedgerSetupPage() {
  const [step, setStep] = useState(0)
  const [tmpls, setTmpls] = useState<Tmpl[]>([])
  const [src, setSrc] = useState('ma_daily_tx')
  const [file, setFile] = useState<File | null>(null)
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [fieldMap, setFieldMap] = useState<Record<string, string>>({})
  const [period, setPeriod] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [msg, setMsg] = useState('')

  useEffect(() => { api('/api/v1/commcalc/commission-ledger/templates').then(d => setTmpls(d?.templates || [])).catch(() => {}) }, [])
  function flash(m: string) { setMsg(m); setTimeout(() => setMsg(''), 4500) }

  async function analyze(f: File) {
    setBusy(true); setMsg('')
    try {
      const fd = new FormData(); fd.append('file', f); fd.append('source_report', src)
      const a: Analysis = await apiUpload('/api/v1/commcalc/commission-ledger/analyze', fd)
      setAnalysis(a)
      const fm: Record<string, string> = {}
      a.suggestions?.forEach(s => { fm[s.target_field] = s.suggested_source })
      setFieldMap(fm)
      setStep(2)
    } catch (e: any) { flash(e?.message || 'Could not read the file') }
    setBusy(false)
  }
  async function recheck() {
    if (!file) return
    setBusy(true)
    try {
      // save the key field mappings the user chose, then re-preview
      for (const k of KEY_FIELDS) {
        const sh = fieldMap[k.tf]
        if (sh) await api('/api/v1/commcalc/column-mapping', { method: 'POST', body: JSON.stringify({ report_key: 'commission_ledger', target_field: k.tf, source_header: sh, transform: k.transform }) })
      }
      const fd = new FormData(); fd.append('file', file); fd.append('source_report', src)
      const a: Analysis = await apiUpload('/api/v1/commcalc/commission-ledger/analyze', fd)
      setAnalysis(a); flash('Updated the preview with your column choices')
    } catch (e: any) { flash(e?.message || 'Re-check failed') }
    setBusy(false)
  }
  async function doImport() {
    if (!file) return
    if (!period.trim()) { flash('Enter a period first (e.g. June 2026)'); return }
    setBusy(true)
    try {
      const fd = new FormData(); fd.append('file', file); fd.append('source_report', src); fd.append('period', period)
      const r = await apiUpload('/api/v1/commcalc/commission-ledger/import', fd)
      setResult(r); setStep(3)
    } catch (e: any) { flash(e?.message || 'Import failed — is migration 071 applied?') }
    setBusy(false)
  }

  const tmplLabel = tmpls.find(t => t.key === src)?.label || src
  const a = analysis

  return (
    <div style={{ padding: 24, maxWidth: 880 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 2 }}>🧭 Commission Ledger — Setup</h1>
      <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 16 }}>
        Turn a carrier's commission file into five standard buckets. Do this once per carrier; afterwards
        you just import each month. Nothing is saved until the final step.
      </p>

      {/* stepper */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
        {STEPS.map((s, i) => (
          <div key={s} style={{ flex: 1, textAlign: 'center', padding: '6px 4px', borderRadius: 8, fontSize: 12, fontWeight: 600,
            background: i === step ? 'var(--accent,#2563eb)' : 'var(--surface)', color: i === step ? '#fff' : i < step ? 'var(--text2)' : 'var(--text3)',
            border: '1px solid var(--border)' }}>{i + 1}. {s}{i < step ? ' ✓' : ''}</div>
        ))}
      </div>
      {msg && <div style={{ ...card, padding: '8px 12px', fontSize: 13, marginBottom: 14 }}>{msg}</div>}

      {/* STEP 1 — template */}
      {step === 0 && (
        <div style={card}>
          <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>Step 1 — Which carrier is this file from?</h2>
          <p style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 14 }}>
            A <b>template</b> is the set of rules that read your file and decide which payouts are Commission,
            Spiff, etc. A template marked <b>Ready to use</b> already has its rules built in. Pick the closest
            match — you can fine-tune the rules later.
          </p>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 16 }}>
            {tmpls.map(t => (
              <div key={t.key} onClick={() => setSrc(t.key)} style={{ ...card, cursor: 'pointer',
                borderColor: src === t.key ? 'var(--accent,#2563eb)' : 'var(--border)', borderWidth: src === t.key ? 2 : 1 }}>
                <div style={{ fontWeight: 700, fontSize: 14 }}>{t.label}</div>
                <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 3 }}>
                  {t.builtin ? (t.rule_count ? `Ready to use · ${t.rule_count} rules` : 'Preconfigured (no rules yet)') : `Your template · ${t.rule_count} rules`}
                </div>
              </div>
            ))}
            <div onClick={() => { const k = prompt('Name your carrier (e.g. cricket, metro):')?.trim().toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, ''); if (k) { if (!tmpls.find(t => t.key === k)) setTmpls(p => [...p, { key: k, label: k, builtin: false, rule_count: 0 }]); setSrc(k) } }}
              style={{ ...card, cursor: 'pointer', borderStyle: 'dashed', color: 'var(--text2)' }}>
              <div style={{ fontWeight: 700, fontSize: 14 }}>＋ New carrier</div>
              <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 3 }}>Start a blank template you build yourself</div>
            </div>
          </div>
          <button style={primary} onClick={() => setStep(1)}>Next → Upload a file</button>
        </div>
      )}

      {/* STEP 2 — upload */}
      {step === 1 && (
        <div style={card}>
          <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>Step 2 — Upload one commission file</h2>
          <p style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 6 }}>
            Carrier: <b>{tmplLabel}</b>. Upload the carrier's commission / transaction export (Excel or
            CSV) — the processor's daily transaction file. We'll just <i>read</i> it to preview the
            result; <b>nothing is saved yet</b>.
          </p>
          <label style={{ ...primary, display: 'inline-block', marginTop: 8 }}>
            {busy ? 'Reading…' : (file ? `Chosen: ${file.name} — change` : '📄 Choose file')}
            <input type="file" accept=".xls,.xlsx,.csv,.txt" style={{ display: 'none' }} disabled={busy}
              onChange={e => { const f = e.target.files?.[0]; if (f) { setFile(f); analyze(f) } e.currentTarget.value = '' }} />
          </label>
          <div style={{ marginTop: 16 }}>
            <button style={btn} onClick={() => setStep(0)}>← Back</button>
          </div>
        </div>
      )}

      {/* STEP 3 — review */}
      {step === 2 && a && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div style={card}>
            <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>Step 3 — Check the columns we found</h2>
            <p style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 12 }}>
              We read <b>{a.row_count.toLocaleString()}</b> rows. Confirm which column is which — the ones
              marked ★ matter most. If a dropdown is blank or wrong, fix it and press <b>Re-check</b>.
            </p>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              {KEY_FIELDS.map(k => (
                <div key={k.tf}>
                  <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 3 }}>{k.label} {k.star && <span style={{ color: '#dc2626' }}>★</span>}</div>
                  <select style={{ ...inp, width: '100%' }} value={fieldMap[k.tf] || ''} onChange={e => setFieldMap({ ...fieldMap, [k.tf]: e.target.value })}>
                    <option value="">— none —</option>
                    {a.headers.map(h => <option key={h} value={h}>{h}</option>)}
                  </select>
                  <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{k.hint}</div>
                </div>
              ))}
            </div>
            <button style={{ ...btn, marginTop: 12 }} onClick={recheck} disabled={busy}>{busy ? 'Re-checking…' : '↻ Re-check with these columns'}</button>
          </div>

          <div style={card}>
            <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>How your payouts will bucket</h2>
            <p style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 12 }}>
              Preview only. <b>{money(a.summary.payout_total)}</b> in payouts across {a.summary.line_count} lines
              ({money(a.summary.charge_total)} are bill/activation payments, not payouts).
            </p>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
              {CATS.map(c => (
                <div key={c} style={{ ...card, minWidth: 130, padding: 12 }}>
                  <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase' }}>{a.category_labels[c] || c}</div>
                  <div style={{ fontSize: 18, fontWeight: 700 }}>{money(a.summary.categories[c]?.total || 0)}</div>
                  <div style={{ fontSize: 11, color: 'var(--text3)' }}>{a.summary.categories[c]?.count || 0} lines</div>
                </div>
              ))}
            </div>
            {a.summary.other_count > 0 ? (
              <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '10px 12px', fontSize: 13 }}>
                ⚠️ <b>{a.summary.other_count} payout line(s)</b> ({money(a.summary.other_total)}) didn't match any rule. Open the{' '}
                <a href={`/commcalc/commission-category-map?source_report=${src}`} target="_blank" rel="noreferrer" style={{ color: '#9a3412', fontWeight: 700 }}>Category Map</a>{' '}
                in a new tab, add a rule for the highlighted labels, then come back and press <b>Re-check</b>.
              </div>
            ) : (
              <div style={{ background: '#ecfdf5', border: '1px solid #6ee7b7', color: '#047857', borderRadius: 8, padding: '10px 12px', fontSize: 13 }}>
                ✓ Every payout line matched a bucket. You're good to import.
              </div>
            )}
          </div>

          <div style={{ display: 'flex', gap: 10 }}>
            <button style={btn} onClick={() => setStep(1)}>← Back</button>
            <button style={primary} onClick={() => setStep(3)}>Next → Set period &amp; import</button>
          </div>
        </div>
      )}

      {/* STEP 4 — import */}
      {step === 3 && (
        <div style={card}>
          <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>Step 4 — Set the period &amp; import</h2>
          {!result ? (
            <>
              <p style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 12 }}>
                Which month is this file for? This labels the data so each month stays separate. Importing
                again for the same period replaces it.
              </p>
              <input style={{ ...inp, width: 200 }} placeholder="e.g. June 2026" value={period} onChange={e => setPeriod(e.target.value)} />
              <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>
                <button style={btn} onClick={() => setStep(2)}>← Back</button>
                <button style={primary} onClick={doImport} disabled={busy}>{busy ? 'Importing…' : '✓ Import now'}</button>
              </div>
            </>
          ) : (
            <>
              <div style={{ background: '#ecfdf5', border: '1px solid #6ee7b7', color: '#047857', borderRadius: 8, padding: '12px 14px', fontSize: 14, marginBottom: 14 }}>
                ✅ Imported <b>{result.saved}</b> lines for <b>{period}</b> — {money(result.summary?.payout_total)} in payouts
                {result.summary?.other_count ? `, ${result.summary.other_count} still unmapped` : ', all classified'}.
              </div>
              <div style={{ display: 'flex', gap: 10 }}>
                <a href="/commcalc/commission-ledger" style={{ ...primary, textDecoration: 'none' }}>View the Commission Ledger →</a>
                <button style={btn} onClick={() => { setResult(null); setFile(null); setAnalysis(null); setStep(0) }}>Set up another carrier</button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
