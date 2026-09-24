'use client'
import { useState } from 'react'
import { apiUpload } from '@/lib/client'

// MANY MONTHLY STATEMENTS AT ONCE (owner 2026-09-24, index §30.17): "give me an option to upload commission for
// multiple periods at the same time since they only give monthly commission reports".
// Pick several monthly files → the backend PREVIEWS each (the month read off the statement's own dates, rows,
// total, whether that month is already landed, what blocks it) → fix any month → Upload all. Every file is read,
// mapped and landed by the single import's own path (/commission-ledger/import-batch → _ledger_import_prepared →
// the one lander), so the batch is, row for row, N single imports. This component never works a month out
// itself: every month it shows is the backend's, and a month typed here is spelled by the backend's one
// canonicaliser. Nothing is written until "Upload all".

type Landing = { origin: string; source_report: string; period: string; rows: number; payout_total: number; landed_at: string | null
  legacy_key?: boolean; orphan_period?: boolean }
type PlanFile = {
  index: number; filename: string; rows: number; payout_total: number; net_total: number
  period: string | null; period_source: 'set' | 'detected' | null; detected: string | null; override: string | null
  detection: { months?: { period: string; rows: number }[]; spans?: boolean; tied?: boolean } | null
  headers_differ: string[]; blocking: string[]; warnings: string[]
  already: { rows: number; payout_total: number; landings: Landing[] } | null
  replaces: Landing[]; other_origin: Landing[]; action: 'land' | 'replace' | 'refused'
}
type Plan = { files: PlanFile[]; refused: boolean; refusals: string[]; replace_periods: string[]; other_origin_periods: string[]
  source_report: string }
type FileResult = { index: number; filename: string; period: string | null; ok: boolean; saved?: number; payout_total?: number
  error?: string; replaced_note?: string | null }
type BatchResult = { results: FileResult[]; sentence: string }

const money = (n: number) => (n || 0).toLocaleString('en-US', { style: 'currency', currency: 'USD' })
const inp: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const th: React.CSSProperties = { padding: '6px 8px', textAlign: 'left', fontSize: 12, color: 'var(--text2)', fontWeight: 700 }
const td: React.CSSProperties = { padding: '6px 8px', fontSize: 13, verticalAlign: 'top' }

export default function LedgerBatchUpload({ source, carrierId = '', statementType = '', disabled = false, onLanded }:
  { source: string; carrierId?: string; statementType?: string; disabled?: boolean; onLanded?: () => void }) {
  const [files, setFiles] = useState<File[]>([])
  const [months, setMonths] = useState<string[]>([])          // what the person typed per file ('' = the detected month)
  const [plan, setPlan] = useState<Plan | null>(null)
  const [dirty, setDirty] = useState(false)                    // a month was edited since the last preview
  const [confirmReplace, setConfirmReplace] = useState(false)
  const [confirmOther, setConfirmOther] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [result, setResult] = useState<BatchResult | null>(null)

  function form(fs: File[], ms: string[]) {
    const fd = new FormData()
    fs.forEach(f => fd.append('files', f))
    fd.append('source_report', source)
    // the template's carrier (index §30.17a): the statement is read by that carrier's own mapping — the one
    // the intake mapped it with — never the org's global set when the carrier has its own
    if (carrierId) fd.append('carrier_id', carrierId)
    if (statementType) fd.append('statement_type', statementType)
    fd.append('periods', JSON.stringify(ms))
    return fd
  }
  async function preview(fs: File[], ms: string[]) {
    if (!fs.length) { setPlan(null); return }
    setBusy(true); setErr('')
    try {
      setPlan(await apiUpload('/api/v1/commcalc/commission-ledger/import-batch/preview', form(fs, ms)))
      setDirty(false)
    } catch (e: unknown) { setErr((e as Error)?.message || 'Preview failed'); setPlan(null) }
    finally { setBusy(false) }
  }
  function pick(list: FileList | null) {
    const fs = Array.from(list || [])
    const ms = fs.map(() => '')
    setFiles(fs); setMonths(ms); setResult(null); setConfirmReplace(false); setConfirmOther(false)
    preview(fs, ms)
  }
  function removeAt(i: number) {
    const fs = files.filter((_, j) => j !== i), ms = months.filter((_, j) => j !== i)
    setFiles(fs); setMonths(ms); preview(fs, ms)
  }
  async function uploadAll() {
    setBusy(true); setErr('')
    try {
      const fd = form(files, months)
      if (confirmReplace) fd.append('confirm_replace', 'true')
      if (confirmOther) fd.append('confirm_other_origin', 'true')
      const r: BatchResult = await apiUpload('/api/v1/commcalc/commission-ledger/import-batch', fd)
      setResult(r); setPlan(null); setFiles([]); setMonths([])
      onLanded?.()
    } catch (e: unknown) { setErr((e as Error)?.message || 'Upload failed — nothing was landed') }
    finally { setBusy(false) }
  }
  const close = () => { setFiles([]); setMonths([]); setPlan(null); setResult(null); setErr('') }
  const needsReplace = (plan?.replace_periods || []).length > 0
  const needsOther = (plan?.other_origin_periods || []).length > 0
  const canUpload = !!plan && !plan.refused && !dirty && !busy && (!needsReplace || confirmReplace) && (!needsOther || confirmOther)

  return (
    <>
      <label style={{ ...inp, cursor: busy || disabled ? 'wait' : 'pointer', fontWeight: 600 }}
        title="Pick several monthly statements at once — each lands under its own month">
        ⬆ Upload several months
        <input type="file" multiple accept=".xls,.xlsx,.csv,.txt" style={{ display: 'none' }} disabled={busy || disabled}
          onChange={e => { pick(e.target.files); e.currentTarget.value = '' }} />
      </label>
      {(plan || result || err || (busy && files.length > 0)) && (
        <div style={{ flexBasis: '100%', border: '1px solid var(--accent,#2563eb)', borderRadius: 10, padding: 14, background: 'var(--surface)' }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap', marginBottom: 8 }}>
            <b style={{ fontSize: 14 }}>{result ? 'Upload result' : `Several months — ${files.length} file(s)`}</b>
            <span style={{ fontSize: 12, color: 'var(--text3)' }}>
              {result ? '' : 'Each file lands under the month its own dates say. Nothing is saved until Upload all.'}
            </span>
            <div style={{ flex: 1 }} />
            <button onClick={close} style={{ ...inp, cursor: 'pointer', fontSize: 11 }}>✕ close</button>
          </div>
          {err && <div style={{ color: '#b91c1c', fontSize: 13, marginBottom: 8 }}>{err}</div>}
          {busy && <div style={{ color: 'var(--text3)', fontSize: 13 }}>Working…</div>}

          {plan && (
            <>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ borderCollapse: 'collapse', width: '100%' }}>
                  <thead><tr style={{ background: 'var(--surface2)' }}>
                    <th style={th}>File</th><th style={th}>Month</th><th style={{ ...th, textAlign: 'right' }}>Rows</th>
                    <th style={{ ...th, textAlign: 'right' }}>Total</th><th style={th}>Already landed?</th><th style={th}>Status</th><th />
                  </tr></thead>
                  <tbody>{plan.files.map(p => (
                    <tr key={p.index} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ ...td, fontFamily: 'monospace', fontSize: 12 }}>{p.filename}</td>
                      <td style={td}>
                        <input style={{ ...inp, width: 150, borderColor: p.period ? 'var(--border)' : '#b91c1c' }}
                          value={months[p.index] ?? ''} placeholder={p.period || 'e.g. August 2026'}
                          onChange={e => { const ms = [...months]; ms[p.index] = e.target.value; setMonths(ms); setDirty(true) }} />
                        <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>
                          {p.period ? `${p.period} · ${p.period_source === 'set' ? 'set by you' : 'from the statement’s own dates'}` : 'not determined — type the month'}
                        </div>
                      </td>
                      <td style={{ ...td, textAlign: 'right' }}>{p.rows.toLocaleString('en-US')}</td>
                      <td style={{ ...td, textAlign: 'right' }}>{money(p.payout_total)}</td>
                      <td style={td}>
                        {p.already
                          ? <span style={{ color: '#9a3412' }}>Yes — {p.already.rows.toLocaleString('en-US')} rows, {money(p.already.payout_total)}
                              {p.replaces.length ? ' · will be REPLACED' : ''}{p.other_origin.length ? ' · also holds an MA-data refresh' : ''}</span>
                          : <span style={{ color: 'var(--text3)' }}>No</span>}
                      </td>
                      <td style={{ ...td, fontSize: 12 }}>
                        {p.blocking.map((b, i) => <div key={'b' + i} style={{ color: '#b91c1c' }}>⛔ {b}</div>)}
                        {p.warnings.map((w, i) => <div key={'w' + i} style={{ color: '#9a3412' }}>⚠️ {w}</div>)}
                        {!p.blocking.length && !p.warnings.length && <span style={{ color: '#15803d' }}>Ready</span>}
                      </td>
                      <td style={td}><button style={{ ...inp, cursor: 'pointer', fontSize: 11 }} disabled={busy} onClick={() => removeAt(p.index)}>Remove</button></td>
                    </tr>))}</tbody>
                </table>
              </div>
              <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginTop: 10 }}>
                {dirty && <button style={{ ...inp, cursor: 'pointer', fontWeight: 600 }} disabled={busy} onClick={() => preview(files, months)}>↻ Check the months again</button>}
                {needsReplace && (
                  <label style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }}>
                    <input type="checkbox" checked={confirmReplace} onChange={e => setConfirmReplace(e.target.checked)} />
                    Replace the month(s) already landed ({plan.replace_periods.join(', ')})
                  </label>
                )}
                {needsOther && (
                  <label style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }}>
                    <input type="checkbox" checked={confirmOther} onChange={e => setConfirmOther(e.target.checked)} />
                    Land beside the MA-data refresh in {plan.other_origin_periods.join(', ')} (the ledger will refuse to add the two until one is retired)
                  </label>
                )}
                <div style={{ flex: 1 }} />
                <button onClick={uploadAll} disabled={!canUpload}
                  style={{ ...inp, fontWeight: 700, cursor: canUpload ? 'pointer' : 'not-allowed',
                    background: canUpload ? 'var(--accent,#2563eb)' : 'var(--surface)', color: canUpload ? '#fff' : 'var(--text3)' }}>
                  Upload all ({plan.files.length})
                </button>
              </div>
              {plan.refused && (
                <div style={{ marginTop: 8, fontSize: 12, color: '#b91c1c' }}>
                  Nothing can be uploaded until every ⛔ is fixed (set the month, or remove the file). {dirty ? 'Check the months again after editing.' : ''}
                </div>
              )}
            </>
          )}

          {result && (
            <div style={{ fontSize: 13 }}>
              <div style={{ marginBottom: 8 }}>{result.sentence}</div>
              <table style={{ borderCollapse: 'collapse' }}>
                <tbody>{result.results.map(r => (
                  <tr key={r.index} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={td}>{r.ok ? '✅' : '❌'}</td>
                    <td style={{ ...td, fontFamily: 'monospace', fontSize: 12 }}>{r.filename}</td>
                    <td style={td}>{r.period || '—'}</td>
                    <td style={td}>{r.ok ? `${(r.saved || 0).toLocaleString('en-US')} lines · ${money(r.payout_total || 0)}` : r.error}</td>
                    <td style={{ ...td, fontSize: 12, color: 'var(--text3)' }}>{r.replaced_note || ''}</td>
                  </tr>))}</tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </>
  )
}
