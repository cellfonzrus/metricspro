'use client'
import { useState, useEffect, useMemo, Fragment } from 'react'
import Link from 'next/link'
import { api, fmt, localToday } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { MarketStorePicker, type StoreOpt } from '../_lib/MarketStorePicker'

// ePay bill-payment recon: DECLARED ePay (closing) vs SALES bill-payments (by tender) vs BANK deposited.
export default function EpayReconPage() {
  const [date, setDate] = useState(() => localToday())
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [flaggedOnly, setFlaggedOnly] = useState(false)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [stores, setStores] = useState<any[]>([])
  // bank-deposit entry (mig 502: inline slip -> OCR-verified against the tenant's configured basis)
  const [dep, setDep] = useState<{ store_code: string; category_id: string; amount: string; employee_name: string; note: string; slip?: string; manual_confirmed?: boolean }>({ store_code: '', category_id: '', amount: '', employee_name: '', note: '' })
  const [depMsg, setDepMsg] = useState('')
  const [depBusy, setDepBusy] = useState(false)
  const [depCfg, setDepCfg] = useState<any>(null)
  // Cash Deposit Recon (mig 509, OWNER 2026-08-05): deposit categories are pick-don't-type here too —
  // the SAME /closing/deposit-categories list the deposit-recon report + admin page use.
  const [depCats, setDepCats] = useState<any[]>([])
  const [shortModal, setShortModal] = useState<any>(null)

  function load() {
    setLoading(true)
    api(`/api/v1/closing/epay-recon?date=${date}`).then(setData).catch(e => setData({ error: e?.message || String(e) })).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [date])
  useEffect(() => {
    apiCached('/api/v1/closing/stores', LOOKUP).then((s: any) => setStores(Array.isArray(s) ? s : (s?.stores || []))).catch(() => {})
    api('/api/v1/closing/deposit-config').then(setDepCfg).catch(() => {})
    api('/api/v1/closing/deposit-categories').then((d: any) => setDepCats(d?.categories || [])).catch(() => {})
  }, [])

  function pickSlip(f: File) {
    const r = new FileReader()
    r.onload = () => { setDep(d => ({ ...d, slip: String(r.result) })); setDepMsg('📎 Slip attached — will be OCR-verified on save.') }
    r.readAsDataURL(f)
  }
  async function saveDeposit() {
    if (!dep.store_code || (!dep.amount && !dep.slip)) { setDepMsg('❌ Pick a store, and enter the amount or attach a slip.'); return }
    setDepBusy(true); setDepMsg('')
    try {
      const r: any = await api('/api/v1/closing/bank-deposit', { method: 'POST', body: JSON.stringify({ ...dep, close_date: date, store_name: stores.find((s: any) => s.store_code === dep.store_code)?.store_address }) })
      const badge = r.ocr_match === 'matched' ? '✅ OCR matched declared cash.'
        : r.ocr_match === 'mismatch' ? `⚠️ OCR MISMATCH — slip vs ${String(r.match_target || '').replace('_', ' ')} (${fmt(r.declared_amount)}). Management alerted.`
        : r.ocr_match === 'ocr_unavailable' ? 'ℹ️ OCR not configured on this server — recorded as entered.'
        : r.ocr_match === 'unreadable' ? '⚠️ Slip uploaded but the amount couldn’t be read — recorded as entered.'
        : ''
      setDepMsg(`✅ Bank deposit recorded. ${badge}`)
      if (r?.recon?.is_short) {
        setShortModal({ recon: r.recon, depositId: r.row?.id, store_code: dep.store_code, category_id: dep.category_id })
      } else {
        setDep({ store_code: '', category_id: '', amount: '', employee_name: '', note: '' })
      }
      load()
    } catch (e: any) { setDepMsg('❌ ' + (e?.message || e)) }
    setDepBusy(false)
  }
  const [shortReason, setShortReason] = useState('')
  async function closeShortModal(willDepositMore: boolean) {
    if (shortModal?.depositId) {
      try { await api(`/api/v1/closing/bank-deposit/${shortModal.depositId}`, { method: 'PUT', body: JSON.stringify({ short_reason: shortReason, will_deposit_more: willDepositMore }) }) } catch { /* best-effort */ }
    }
    setShortModal(null); setShortReason(''); setDep({ store_code: '', category_id: '', amount: '', employee_name: '', note: '' })
  }

  // OWNER DIRECTIVE 2026-08-04 (market->store cascade + checkbox picker): this page had NO store/
  // market filter at all before — added client-side over `stores` (already fetched for the deposit
  // form's picker), same pattern as accessory-recon's sibling addition.
  const [fMarkets, setFMarkets] = useState<string[]>([])
  const [fStores, setFStores] = useState<string[]>([])
  const marketByCode = useMemo(() => {
    const m: Record<string, string> = {}
    for (const s of stores) if (s.store_code && s.market) m[s.store_code] = s.market
    return m
  }, [stores])
  const storesForCascade: StoreOpt[] = useMemo(
    () => stores.filter((s: any) => s.store_code).map((s: any) => ({ id: s.store_code, label: s.store_address || s.store_code, market: s.market || null })),
    [stores])
  const fMarketsFold = useMemo(() => new Set(fMarkets.map(m => m.trim().toLowerCase())), [fMarkets])
  const allRows: any[] = data?.rows || []
  const rows = allRows.filter(r =>
    (!flaggedOnly || r.flag) &&
    (!fStores.length || fStores.includes(r.store_code)) &&
    (!fMarketsFold.size || fMarketsFold.has((marketByCode[r.store_code] || '').trim().toLowerCase())))
  const t = data?.totals || {}
  const inp = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

  function buildPayload(): ExportPayload {
    return {
      title: 'ePay Bank-Deposit Recon', subtitle: date, filename: `epay-recon_${date}`,
      sheets: [{ name: 'By store', rows, columns: [
        { header: 'Store', get: (r: any) => r.store_address },
        { header: 'Market', get: (r: any) => marketByCode[r.store_code] || '' },
        { header: 'Declared ePay cash', get: (r: any) => r.declared.cash, money: true },
        { header: 'Sales bill-pay cash', get: (r: any) => r.sales.cash, money: true },
        { header: 'Bank deposited', get: (r: any) => r.bank_deposited, money: true },
        { header: 'Cash variance', get: (r: any) => r.cash_variance, money: true },
        { header: 'Status', get: (r: any) => r.flag ? r.direction.toUpperCase() : 'OK' },
      ] }],
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🏦 ePay Bank‑Deposit Recon</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 740 }}>
            ePay bill‑payment cash: what reps <strong>declared</strong> (closing) vs the <strong>bill‑payments in sales</strong> (by tender)
            vs what was <strong>deposited in the bank</strong>. Headline variance = declared ePay cash − bank deposited.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <input className="select" type="date" value={date} onChange={e => setDate(e.target.value)} />
          {allRows.length > 0 && <ExportButtons payload={buildPayload} />}
        </div>
      </div>

      {/* Record a bank deposit */}
      <div className="card" style={{ padding: 14, marginBottom: 16, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <strong style={{ fontSize: 13 }}>Record bank deposit:</strong>
        <select style={inp} value={dep.store_code} onChange={e => setDep(d => ({ ...d, store_code: e.target.value }))}>
          <option value="">Store…</option>
          {stores.map((s, i) => <option key={i} value={s.store_code}>{s.store_address || s.store_code}</option>)}
        </select>
        <select style={inp} value={dep.category_id} onChange={e => setDep(d => ({ ...d, category_id: e.target.value }))}>
          <option value="">Category (optional)…</option>
          {depCats.map((c: any) => <option key={c.id || c.name} value={c.id || ''}>{c.name}</option>)}
        </select>
        <input style={{ ...inp, width: 110 }} inputMode="decimal" placeholder="Amount $" value={dep.amount} onChange={e => setDep(d => ({ ...d, amount: e.target.value }))} />
        <input style={{ ...inp, width: 130 }} placeholder="Deposited by" value={dep.employee_name} onChange={e => setDep(d => ({ ...d, employee_name: e.target.value }))} />
        <label className="btn" style={{ cursor: 'pointer' }}>{dep.slip ? '📎 Slip ✓' : '📎 Deposit slip'}
          <input type="file" accept="image/*,.pdf" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) pickSlip(f) }} />
        </label>
        {depCfg && depCfg.anthropic_configured === false && (
          <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 5 }}>
            <input type="checkbox" checked={!!dep.manual_confirmed} onChange={e => setDep(d => ({ ...d, manual_confirmed: e.target.checked }))} />
            I've verified this slip manually
          </label>
        )}
        <button className="btn" disabled={depBusy} onClick={saveDeposit} style={{ background: 'var(--accent)', color: '#fff' }}>{depBusy ? 'Saving…' : 'Save'}</button>
        {depMsg && <span style={{ fontSize: 12, color: depMsg.startsWith('❌') ? '#b91c1c' : 'var(--text2)' }}>{depMsg}</span>}
        {depCfg && (
          <span style={{ fontSize: 11, color: 'var(--text3)' }}>
            Slip checked against: <b>{String(depCfg.match_target || 'total_cash').replace('_', ' ')}</b>
            {depCfg.anthropic_configured === false ? ' · OCR unavailable (server key not set)' : ''}
            {' · '}<Link href="/closing/cash-config" style={{ color: 'var(--accent)' }}>change</Link>
            {' · '}<Link href="/closing/deposit-recon" style={{ color: 'var(--accent)' }}>Cash Deposit Recon report</Link>
          </span>
        )}
      </div>

      {shortModal && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50 }}>
          <div className="card" style={{ padding: 20, maxWidth: 420, width: '90%' }}>
            <h3 style={{ margin: 0, fontSize: 16 }}>⚠️ Cash deposit is short by {fmt(shortModal.recon.remaining_short)}</h3>
            <p style={{ fontSize: 13, color: 'var(--text2)' }}>
              Expected {fmt(shortModal.recon.expected_deposit)}, deposited {fmt(shortModal.recon.total_deposited_today)} so far.
            </p>
            <textarea style={{ width: '100%', minHeight: 70, padding: 8, borderRadius: 6, border: '1px solid var(--border)', fontSize: 13 }}
              placeholder="Reason for the short deposit…" value={shortReason} onChange={e => setShortReason(e.target.value)} />
            <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
              <button className="btn btn-secondary" onClick={() => closeShortModal(true)}>Save reason — will deposit more (record it on <Link href="/closing/deposit-recon" style={{ color: 'var(--accent)' }}>Cash Deposit Recon</Link>)</button>
              <button className="btn btn-secondary" onClick={() => closeShortModal(false)}>Save reason — that's final</button>
            </div>
          </div>
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : data?.error ? (
        <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {data.error}</div>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 14, marginBottom: 16 }}>
            <Stat label="Declared ePay cash" value={fmt(t.declared_cash)} />
            <Stat label="Sales bill‑pay cash" value={fmt(t.sales_cash)} color="var(--text2)" />
            <Stat label="Bank deposited" value={fmt(t.bank_deposited)} color="#16a34a" />
            <Stat label="Stores flagged" value={`${t.flagged || 0} / ${t.stores || 0}`} color={t.flagged ? '#dc2626' : '#059669'} />
          </div>
          <div className="card" style={{ padding: '10px 14px', display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
            <MarketStorePicker
              stores={storesForCascade}
              selectedMarkets={fMarkets} onMarketsChange={setFMarkets}
              selectedStores={fStores} onStoresChange={setFStores}
            />
            <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
              <input type="checkbox" checked={flaggedOnly} onChange={e => setFlaggedOnly(e.target.checked)} /> Discrepancies only
            </label>
            <span style={{ fontSize: 12, color: 'var(--text3)' }}>{rows.length} store(s)</span>
          </div>

          {rows.length === 0 ? (
            <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No ePay data for {date}.</div>
          ) : (
            <div className="card" style={{ padding: 0, overflow: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 880 }}>
                <thead><tr style={{ background: 'var(--surface2)', fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                  {['Store', 'Declared cash', 'Declared credit', 'Declared ACIMA', 'Sales cash', 'Bank deposited', 'Cash variance', 'Status'].map(h =>
                    <th key={h} style={{ textAlign: h === 'Store' ? 'left' : 'right', padding: '8px 12px', whiteSpace: 'nowrap' }}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {rows.map(r => (
                    <Fragment key={r.store_code}>
                      <tr onClick={() => setOpen(o => ({ ...o, [r.store_code]: !o[r.store_code] }))}
                        style={{ borderTop: '1px solid var(--border)', cursor: 'pointer', background: r.flag ? '#fffafa' : undefined }}>
                        <td style={{ padding: '9px 12px', fontSize: 13, fontWeight: 600 }}>{(r.reps?.length || r.deposits?.length) ? (open[r.store_code] ? '▾ ' : '▸ ') : ''}{r.store_address}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13 }}>{fmt(r.declared.cash)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13, color: 'var(--text2)' }}>{fmt(r.declared.credit)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13, color: 'var(--text2)' }}>{fmt(r.declared.acima)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13, color: 'var(--text3)' }}>{fmt(r.sales.cash)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13, color: '#16a34a' }}>{fmt(r.bank_deposited)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontSize: 13, fontWeight: 700, color: r.flag ? '#dc2626' : 'var(--text1)' }}>{r.cash_variance >= 0 ? '+' : ''}{fmt(r.cash_variance)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'center', fontSize: 12 }}>
                          {r.flag ? <span style={{ background: '#fee2e2', color: '#b91c1c', borderRadius: 5, padding: '1px 8px', fontWeight: 600 }}>{r.direction === 'short' ? 'NOT DEPOSITED' : 'OVER‑DEPOSITED'}</span> : <span style={{ color: '#059669' }}>✓ OK</span>}
                        </td>
                      </tr>
                      {open[r.store_code] && [...(r.reps || []).map((x: any, i: number) => (
                        <tr key={r.store_code + '_r' + i} style={{ background: 'var(--surface2)', fontSize: 12 }}>
                          <td style={{ padding: '4px 12px 4px 30px', color: 'var(--text2)' }}>declared · {x.employee_name || '(unnamed)'}</td>
                          <td style={{ padding: '4px 12px', textAlign: 'right' }}>{fmt(x.cash)}</td>
                          <td style={{ padding: '4px 12px', textAlign: 'right' }}>{fmt(x.credit)}</td>
                          <td style={{ padding: '4px 12px', textAlign: 'right' }}>{fmt(x.acima)}</td>
                          <td colSpan={4} />
                        </tr>
                      )), ...(r.deposits || []).map((x: any, i: number) => (
                        <tr key={r.store_code + '_d' + i} style={{ background: 'var(--surface2)', fontSize: 12 }}>
                          <td style={{ padding: '4px 12px 4px 30px', color: '#16a34a' }}>bank deposit · {x.employee_name || ''}{x.receipt_path ? ' 📎' : ''}</td>
                          <td colSpan={4} style={{ padding: '4px 12px', color: 'var(--text3)' }}>
                            {x.note || ''}
                            {x.ocr_match === 'mismatch' && <span style={{ color: '#b91c1c', fontWeight: 700, marginLeft: 6 }}>⚠ OCR MISMATCH{x.ocr_amount != null ? ` (slip read ${fmt(x.ocr_amount)})` : ''}</span>}
                            {x.ocr_match === 'matched' && <span style={{ color: '#16a34a', marginLeft: 6 }}>✓ OCR matched</span>}
                            {x.ocr_match === 'unreadable' && <span style={{ color: '#b45309', marginLeft: 6 }}>OCR unreadable</span>}
                          </td>
                          <td style={{ padding: '4px 12px', textAlign: 'right', color: '#16a34a' }}>{fmt(x.amount)}</td>
                          <td colSpan={2} />
                        </tr>
                      ))]}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 12 }}>
            "Declared" = the ePay‑on‑cash/credit/ACIMA reps entered on the closing sheet. "Sales" = bill‑payment
            transactions from the sales feed split by tender (best‑effort classifier). "Bank deposited" = the receipts
            recorded above. NOT DEPOSITED = declared ePay cash exceeds what hit the bank. Tolerance ±{data?.tolerance ?? 1}.
          </p>
        </>
      )}
    </div>
  )
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return <div className="card" style={{ padding: '14px 16px' }}>
    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '.05em' }}>{label}</div>
    <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4, color: color || 'var(--text1)' }}>{value}</div>
  </div>
}
