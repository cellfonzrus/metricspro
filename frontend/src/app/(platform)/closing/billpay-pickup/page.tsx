'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import Link from 'next/link'
import { api, fmt, localToday } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { useAuth } from '@/lib/auth-context'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import EntityPicker, { EntityOption } from '@/components/EntityPicker'
import { EntityPickerChips } from '../_lib/EntityPickerChips'
import { MarketStorePicker, type StoreOpt } from '../_lib/MarketStorePicker'
import { resolveStoreCodes } from '../_lib/market-store-cascade'
import EnvelopeViewLink from '@/components/EnvelopeViewLink'

// Bill Payment Pickup (owner directive 2026-09-02, mig 942): "one more pick up for the bill
// payment pickup and deposit menu, just under the cash pick up module, the same process same
// wiring as the cash pick up." A DELIBERATE mirror of /closing/pickup — same states, same
// approvals, same UX flow — pointed at the BILL-PAY side of the declared-cash split: each
// envelope's amount here is the rep's declared ePay-on-cash (the bill payments collected in
// cash, which are INSIDE the total cash the Cash Pickup envelope carries — "Total cash in store
// including Bill Payments"). Picking one up records the physical counterpart of the bill-pay
// remittance (mig-939 coverage recon); it does NOT double-relieve the general cash-on-hand
// movement unless the org's split-envelope knob is on (mig 942 billpay_relieves_cash).
const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const inp: React.CSSProperties = { ...sel, width: '100%' }
const cell: React.CSSProperties = { padding: '8px 10px', borderTop: '1px solid var(--border)', fontSize: 13, verticalAlign: 'middle' }
const round2 = (n: number) => Math.round((n + Number.EPSILON) * 100) / 100

export default function BillPayPickupPage() {
  const { user, permissions } = useAuth()
  const [rangeMode, setRangeMode] = useState(false)
  const [date, setDate] = useState(localToday())
  const [rangeStart, setRangeStart] = useState(localToday())
  const [rangeEnd, setRangeEnd] = useState(localToday())
  const [market, setMarket] = useState('')
  const [fMarkets, setFMarkets] = useState<string[]>([])
  const [fStores, setFStores] = useState<string[]>([])
  const [fEmps, setFEmps] = useState<string[]>([])
  const [fDm, setFDm] = useState('')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [sel_, setSel] = useState<Record<string, boolean>>({})
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [cfg, setCfg] = useState<any>(null)
  const [cfgOpen, setCfgOpen] = useState(false)
  const [cfgMsg, setCfgMsg] = useState('')
  const [dep, setDep] = useState<any>(null)
  const [depBusy, setDepBusy] = useState(false)
  const [undoBusy, setUndoBusy] = useState<string | null>(null)
  const [pStores, setPStores] = useState<any[]>([])
  const [pEmps, setPEmps] = useState<any[]>([])

  function fileToDataUrl(f: File): Promise<string> {
    return new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(String(r.result)); r.onerror = rej; r.readAsDataURL(f) })
  }
  async function recordDeposit() {
    if (!dep) return
    setDepBusy(true)
    try {
      const r: any = await api('/api/v1/closing/billpay-pickup/deposit', { method: 'POST', body: JSON.stringify({
        store_code: dep.e.store_code, close_date: dep.e.close_date || date, employee_name: dep.e.employee_name,
        disposition: dep.disposition, deposit_amount: dep.deposit_amount || undefined, handed_to: dep.handed_to || undefined,
        declared_amount: dep.e.cash, deposit_slip: dep.slip || undefined,
      }) })
      setMsg(dep.disposition === 'deposited'
        ? (r.flagged ? `⚠️ Deposit ${fmt(r.deposit_amount)} vs declared ${fmt(r.declared_amount)} — flagged for review.` : `✅ Deposit recorded${r.matched ? ' — matches declared bill-pay cash.' : '.'}`)
        : '✅ Marked handed to management.')
      setDep(null); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    finally { setDepBusy(false) }
  }

  // Undo a mis-tapped confirmation — same edit-safe semantics as Cash Pickup (server refuses 409
  // once a disposition is recorded; numbers re-derive live from billpay_pickup rows).
  async function undoPickup(e: any) {
    const k = key(e)
    setUndoBusy(k); setMsg('')
    try {
      const r: any = await api('/api/v1/closing/billpay-pickup/undo', { method: 'POST', body: JSON.stringify({
        store_code: e.store_code, close_date: e.close_date, employee_name: e.employee_name,
      }) })
      setMsg(r.already ? 'Nothing to undo.' : `↩️ Pickup undone for ${e.store_name || e.store_code} · ${e.employee_name} — bill-pay cash is back to "still to collect."`)
      load()
    } catch (er: any) { setMsg('❌ ' + (er?.message || er)) }
    finally { setUndoBusy(null) }
  }

  function exportPayload(): ExportPayload {
    return {
      title: `Bill payment pickup — ${date}`, filename: `billpay-pickup-${date}`,
      sheets: [{
        name: 'Bill Payment Pickup',
        columns: [
          { header: 'Store', get: (r: any) => r.store_name || r.store_code },
          { header: 'Rep', get: (r: any) => r.employee_name },
          { header: 'Bill-pay cash', get: (r: any) => r.cash, money: true },
          { header: 'Bill pay on credit card', get: (r: any) => r.credit ?? 0, money: true },
          { header: 'POS bill pay (store-day)', get: (r: any) => r.pos_billpay ?? 'no POS data', money: true },
          { header: 'POS status', get: (r: any) => r.pos_status || '' },
          { header: 'Picked up', get: (r: any) => (r.picked_up ? 'Yes' : 'No') },
          { header: 'By (DM)', get: (r: any) => r.picked_up_by || '' },
          { header: 'Disposition', get: (r: any) => r.disposition || '' },
          { header: 'Deposit', get: (r: any) => r.deposit_amount ?? '', money: true },
          { header: 'Status', get: (r: any) => (r.deposit_flagged ? 'FLAGGED' : (r.deposit_matched ? 'matched' : '')) },
        ],
        rows: data?.envelopes || [],
      }],
    }
  }

  useEffect(() => { if (user?.market && permissions?.scope === 'market') setMarket(user.market) }, [user, permissions])
  useEffect(() => { api('/api/v1/closing/billpay-pickup-config').then(setCfg).catch(() => setCfg({})) }, [])
  useEffect(() => {
    apiCached('/api/v1/closing/stores', LOOKUP).then((s: any) => setPStores(Array.isArray(s) ? s : [])).catch(() => {})
    apiCached('/api/v1/storeops/employees?all_company=true', LOOKUP).then((r: any) => setPEmps(Array.isArray(r) ? r : (r?.employees || []))).catch(() => {})
  }, [])

  const storesForCascade: StoreOpt[] = useMemo(
    () => pStores.filter((s: any) => s.store_code).map((s: any) => ({ id: s.store_code, label: s.store_address || s.store_code, market: s.market || null })),
    [pStores])
  const resolvedStores = useMemo(
    () => resolveStoreCodes(storesForCascade, fMarkets, fStores),
    [storesForCascade, fMarkets, fStores])

  const load = useCallback(() => {
    if (rangeMode ? !(rangeStart && rangeEnd) : !date) return
    setLoading(true); setSel({}); setNotes({})
    const qs = [
      rangeMode ? `start=${rangeStart}&end=${rangeEnd}` : `date=${date}`,
      market && `market=${encodeURIComponent(market)}`,
      resolvedStores.length && `stores=${encodeURIComponent(resolvedStores.join(','))}`,
      fEmps.length && `employees=${encodeURIComponent(fEmps.join(','))}`,
      fDm && `dm=${encodeURIComponent(fDm)}`,
    ].filter(Boolean).join('&')
    api(`/api/v1/closing/billpay-pickups?${qs}`).then(setData).catch(console.error).finally(() => setLoading(false))
  }, [rangeMode, date, rangeStart, rangeEnd, market, resolvedStores, fEmps, fDm])
  useEffect(() => { load() }, [load])

  const empOptions: EntityOption[] = useMemo(
    () => pEmps.filter((e: any) => (e.name || '').trim()).map((e: any) => ({ id: e.name, label: e.name, sublabel: e.email || undefined })),
    [pEmps])

  const envelopes: any[] = data?.envelopes || []
  const key = (e: any) => `${e.close_date || ''}|${e.store_code || ''}|${e.employee_name || ''}`
  const ready = envelopes.filter(e => !e.picked_up)
  const selectedKeys = ready.filter(e => sel_[key(e)])
  const selTotal = selectedKeys.reduce((s, e) => s + (e.cash || 0), 0)

  // Per-store bill-pay position + a live "pending after this pickup" preview — same pattern as
  // Cash Pickup's by_store panel, on the billpay side (declared − picked = pending remittance).
  const selByStore = useMemo(() => {
    const m: Record<string, number> = {}
    for (const e of selectedKeys) { const c = e.store_code || ''; m[c] = (m[c] || 0) + (e.cash || 0) }
    return m
  }, [selectedKeys])
  const byStoreView = useMemo(
    () => (data?.by_store || []).map((s: any) => ({
      ...s, selected: selByStore[s.store_code] || 0,
      after: round2((s.billpay_pending || 0) - (selByStore[s.store_code] || 0)),
    })),
    [data, selByStore])

  async function confirm() {
    if (!selectedKeys.length) { setMsg('Select at least one envelope.'); return }
    setBusy(true); setMsg('')
    try {
      const r = await api('/api/v1/closing/billpay-pickup', { method: 'POST', body: JSON.stringify({
        date: rangeMode ? undefined : date, picked_up_by: user?.full_name || 'DM',
        items: selectedKeys.map(e => ({ store_code: e.store_code, store_name: e.store_name, employee_name: e.employee_name, close_date: e.close_date, amount: e.cash, note: notes[key(e)] || '' })),
      }) })
      const n = (r.notify || []) as any[]
      const sent = n.filter(x => x.ok).map(x => x.channel)
      const failed = n.filter(x => !x.ok)
      setMsg(`✅ ${r.count} envelope(s) picked up (${fmt(r.total)}).` +
        (sent.length ? ` Notified: ${sent.join(', ')}.` : '') +
        (failed.length ? ` ⚠️ ${failed.map(f => `${f.channel}: ${f.detail}`).join('; ')}` : ''))
      load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    finally { setBusy(false) }
  }

  async function saveCfg() {
    setCfgMsg('')
    try { const r = await api('/api/v1/closing/billpay-pickup-config', { method: 'PUT', body: JSON.stringify(cfg) }); setCfg(r); setCfgMsg('✅ Saved.') }
    catch (e: any) { setCfgMsg('❌ ' + (e?.message || e)) }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧾 Bill Payment Pickup</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Check off each store&rsquo;s BILL-PAY cash (the declared ePay-on-cash dollars — already inside the total cash
            the Cash Pickup envelope carries), add a note, and confirm. Same flow as Cash Pickup; the assigned recipient
            is notified by email + WhatsApp.</p>
        </div>
        <Link href="/closing" className="btn btn-secondary" style={{ fontSize: 13 }}>← Dashboard</Link>
      </div>

      {/* Recipient config (falls back to the Cash Pickup recipient when unset) */}
      <div className="card" style={{ padding: 14, marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer' }} onClick={() => setCfgOpen(o => !o)}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>🔔 Bill-pay pickup notification recipient {cfg?.recipient_email || cfg?.recipient_whatsapp ? '' : '— not set (falls back to the Cash Pickup recipient)'}</span>
          <span style={{ fontSize: 12, color: 'var(--text3)' }}>{cfgOpen ? '▾' : '▸'}</span>
        </div>
        {cfgOpen && cfg && (
          <div style={{ marginTop: 12, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <L t="Name"><input style={{ ...inp, width: 160 }} value={cfg.recipient_name || ''} onChange={e => setCfg({ ...cfg, recipient_name: e.target.value })} /></L>
            <L t={`Email${cfg.email_configured ? '' : ' (server not configured)'}`}><input style={{ ...inp, width: 220 }} value={cfg.recipient_email || ''} onChange={e => setCfg({ ...cfg, recipient_email: e.target.value })} placeholder="name@company.com" /></L>
            <L t={`WhatsApp${cfg.whatsapp_configured ? '' : ' (server not configured)'}`}><input style={{ ...inp, width: 180 }} value={cfg.recipient_whatsapp || ''} onChange={e => setCfg({ ...cfg, recipient_whatsapp: e.target.value })} placeholder="2125550123 or +1516…" /></L>
            <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 5 }}><input type="checkbox" checked={cfg.notify_email !== false} onChange={e => setCfg({ ...cfg, notify_email: e.target.checked })} /> email</label>
            <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 5 }}><input type="checkbox" checked={cfg.notify_whatsapp !== false} onChange={e => setCfg({ ...cfg, notify_whatsapp: e.target.checked })} /> whatsapp</label>
            <button className="btn btn-primary" style={{ fontSize: 13 }} onClick={saveCfg}>Save</button>
            {cfgMsg && <span style={{ fontSize: 12 }}>{cfgMsg}</span>}
          </div>
        )}
      </div>

      {/* Filters — identical widget set to Cash Pickup */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', marginBottom: 16, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <div style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
            <button className="btn" style={{ borderRadius: 0, border: 'none', fontSize: 12, background: !rangeMode ? 'var(--accent)' : 'transparent', color: !rangeMode ? 'white' : 'var(--text2)' }} onClick={() => setRangeMode(false)}>Day</button>
            <button className="btn" style={{ borderRadius: 0, border: 'none', fontSize: 12, background: rangeMode ? 'var(--accent)' : 'transparent', color: rangeMode ? 'white' : 'var(--text2)' }} onClick={() => setRangeMode(true)}>Range</button>
          </div>
          {!rangeMode
            ? <input type="date" style={sel} value={date} onChange={e => setDate(e.target.value)} />
            : <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
                <input type="date" style={sel} value={rangeStart} onChange={e => setRangeStart(e.target.value)} />
                →<input type="date" style={sel} value={rangeEnd} onChange={e => setRangeEnd(e.target.value)} />
              </span>}
        </div>
        <MarketStorePicker
          stores={storesForCascade}
          selectedMarkets={fMarkets} onMarketsChange={setFMarkets}
          selectedStores={fStores} onStoresChange={setFStores}
        />
        <EntityPickerChips options={empOptions} value={fEmps} onChange={setFEmps} placeholder="Add a rep…" width={180} />
        <EntityPicker options={empOptions} value={fDm || null} onChange={v => setFDm(v || '')} placeholder="DM (picked up by)" width={200} />
        {(fMarkets.length > 0 || fStores.length > 0 || fEmps.length > 0 || fDm) && <button className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 9px' }} onClick={() => { setFMarkets([]); setFStores([]); setFEmps([]); setFDm('') }}>Clear</button>}
        {market && <span style={{ fontSize: 12, color: 'var(--text3)' }}>Market: {market}</span>}
        {data && <span style={{ fontSize: 13, color: 'var(--text2)' }}>{data.ready} ready · {data.collected} collected{data.flagged ? ` · ${data.flagged} ⚠ flagged` : ''}</span>}
        {data?.pos_source && data.pos_source !== 'none' && <span style={{ fontSize: 12, color: 'var(--text3)' }}>POS source: {data.pos_source}</span>}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          {(data?.envelopes || []).length > 0 && <><ExportButtons payload={exportPayload} compact /><SendReportButton exportPayload={exportPayload} compact /></>}
          <Link href="/closing/pickup" style={{ fontSize: 12, color: 'var(--accent)', textDecoration: 'none' }}>💵 Cash Pickup</Link>
          <Link href="/closing/cash-config" style={{ fontSize: 12, color: 'var(--accent)', textDecoration: 'none' }}>⚙️ Setup</Link>
        </div>
      </div>

      {/* Day summary — bill-pay cash declared / collected / still to collect */}
      {data && (
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
          <Stat label="Bill-pay cash (declared)" value={fmt(data.total_cash || 0)} accent />
          <Stat label="Bill pay on credit card (declared)" value={fmt(data.total_credit || 0)} sub="taken on card — no cash to pick up" />
          <Stat label="Collected" value={fmt(data.collected_cash || 0)} sub={`${data.collected} envelope${data.collected === 1 ? '' : 's'}`} />
          <Stat label="Still to collect" value={fmt(data.ready_cash || 0)} sub={`${data.ready} envelope${data.ready === 1 ? '' : 's'}`} />
        </div>
      )}

      {/* Per-store bill-pay position — declared-to-date minus picked-to-date = pending remittance
          (the physical counterpart of the bill-pay coverage recon). "Pending after" updates live. */}
      {byStoreView.length > 0 && (
        <div className="card" style={{ padding: 14, marginBottom: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
            <div style={{ fontSize: 13, fontWeight: 700 }}>🧾 Bill-pay cash pending by store <span style={{ fontWeight: 400, color: 'var(--text3)' }}>as of {data.as_of}</span></div>
            <Link href="/closing/store-cash-on-hand" style={{ fontSize: 12, color: 'var(--accent)', textDecoration: 'none' }}>Store Cash on Hand (total cash) →</Link>
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {byStoreView.map((s: any) => (
              <div key={s.store_code} style={{ minWidth: 190, padding: '8px 12px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface2)' }}>
                <div style={{ fontSize: 12, fontWeight: 600 }}>{s.store_name}{s.market ? <span style={{ color: 'var(--text3)', fontWeight: 400 }}> · {s.market}</span> : null}</div>
                <div style={{ fontSize: 18, fontWeight: 700, marginTop: 2 }}>{fmt(s.billpay_pending)}</div>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>declared {fmt(s.billpay_declared)} · picked {fmt(s.billpay_picked)}</div>
                {s.selected > 0 && (
                  <div style={{ fontSize: 11, color: 'var(--accent)', marginTop: 2 }}>− {fmt(s.selected)} selected → {fmt(s.after)} pending</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : envelopes.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>No bill-pay cash envelopes for {rangeMode ? `${rangeStart} → ${rangeEnd}` : date}.</div>
      ) : (
        <>
          <div className="card table-wrapper" style={{ padding: 0 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                {[...(rangeMode ? ['Date'] : []), '', 'Store', 'Rep', 'Bill-pay cash', 'On credit card', 'POS bill pay', 'Envelope', 'Note / status', 'Deposit'].map((h, i) =>
                  <th key={i} style={{ textAlign: 'left', padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
              </tr></thead>
              <tbody>
                {envelopes.map(e => {
                  const k = key(e); const done = e.picked_up
                  return (
                    <tr key={k} style={{ background: done ? 'var(--surface2)' : undefined }}>
                      {rangeMode && <td style={{ ...cell, fontSize: 12, color: 'var(--text3)' }}>{e.close_date}</td>}
                      {/* A credit-only row (bill payments taken on card, no cash declared) is a
                          DISPLAY row — there is no physical cash envelope to pick up. */}
                      <td style={cell}>{done ? '✅' : (e.cash > 0 ? <input type="checkbox" checked={!!sel_[k]} onChange={ev => setSel(s => ({ ...s, [k]: ev.target.checked }))} /> : <span style={{ color: 'var(--text3)' }}>—</span>)}</td>
                      <td style={cell}>{e.store_name || e.store_code || '—'}</td>
                      <td style={cell}>{e.employee_name || '—'}</td>
                      <td style={{ ...cell, fontWeight: 600 }}>{fmt(e.cash)}</td>
                      {/* Bill payment on credit card (owner 2026-09-02 #2): the rep's declared
                          ePay-on-credit split — card money settles with the processor, so it is
                          never in the envelope; shown so the day's declared bill-pay total is
                          complete next to the POS figure. */}
                      <td style={{ ...cell, color: 'var(--text2)' }} title="Declared bill payments taken on credit/debit card — settles with the processor, not cash to collect">{fmt(e.credit || 0)}</td>
                      {/* The SYSTEM'S number right next to the store-entered one (owner 2026-09-02):
                          POS-report bill payments for this store-day — the SAME processor-feed
                          resolution the coverage recon / Cash Recon (Management) uses, compared at
                          store-day grain against the declared ePay-on-cash. Feed absent => an honest
                          "no POS data"; feed present but silent for this store-day => honest zero. */}
                      <td style={{ ...cell, color: e.pos_status === 'mismatch' ? '#dc2626' : 'var(--text2)', fontWeight: e.pos_status === 'mismatch' ? 700 : 400 }}
                          title={e.pos_status === 'no_pos_data' ? 'No processor bill-payment feed resolved for this range'
                            : `Store-day declared (cash + card) ${fmt(e.pos_declared_day)} vs POS ${fmt(e.pos_billpay)} (Δ ${fmt(e.pos_delta)})`}>
                        {e.pos_billpay == null ? <span style={{ color: 'var(--text3)', fontStyle: 'italic' }}>no POS data</span>
                          : <>{fmt(e.pos_billpay)}{e.pos_status === 'mismatch' ? ' ⚠' : e.pos_status === 'ok' ? <span style={{ color: '#166534' }}> ✓</span> : null}</>}
                      </td>
                      <td style={cell}><EnvelopeViewLink row={e} label="📷 view" /></td>
                      <td style={cell}>
                        {done
                          ? <span style={{ fontSize: 12, color: 'var(--text3)' }}>by {e.picked_up_by} · {e.picked_up_at ? new Date(e.picked_up_at).toLocaleString() : ''}{e.note ? ` · ${e.note}` : ''}</span>
                          : <input style={{ ...inp, minWidth: 200 }} placeholder="Note (optional)" value={notes[k] || ''} onChange={ev => setNotes(n => ({ ...n, [k]: ev.target.value }))} />}
                      </td>
                      <td style={cell}>
                        {done && (e.disposition
                          ? <span style={{ fontSize: 12 }}>
                              {e.disposition === 'deposited' ? '🏦 Deposited' : '🤝 To mgmt'}
                              {e.deposit_flagged && <span style={{ color: '#dc2626', fontWeight: 700 }}> · ⚠ {fmt(e.deposit_amount)} vs {fmt(e.cash)}</span>}
                              {e.deposit_matched && <span style={{ color: '#166534' }}> · ✓ matched</span>}
                              {e.deposit_url && <> · <a href={e.deposit_url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>slip</a></>}
                            </span>
                          : <span style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                              <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px' }} onClick={() => setDep({ e, disposition: 'deposited', deposit_amount: '' })}>💰 Record deposit</button>
                              <button className="btn btn-secondary" style={{ fontSize: 11, padding: '3px 8px', color: 'var(--text3)' }}
                                      disabled={undoBusy === key(e)} onClick={() => undoPickup(e)} title="Undo this pickup confirmation">
                                {undoBusy === key(e) ? '…' : '↩ Undo'}
                              </button>
                            </span>)}
                        {!done && <span style={{ fontSize: 11, color: 'var(--text3)' }}>—</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 14, flexWrap: 'wrap' }}>
            <button className="btn btn-primary" style={{ fontSize: 14 }} disabled={busy || !selectedKeys.length} onClick={confirm}>
              {busy ? '⏳ Confirming…' : `✅ Confirm bill-pay pickup (${selectedKeys.length} · ${fmt(selTotal)})`}
            </button>
            {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
          </div>
        </>
      )}

      {dep && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50 }} onClick={() => !depBusy && setDep(null)}>
          <div className="card" style={{ padding: 22, width: 420, maxWidth: '92vw' }} onClick={e => e.stopPropagation()}>
            <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 4 }}>Record bill-pay cash disposition</div>
            <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 12 }}>{dep.e.store_name || dep.e.store_code} · {dep.e.employee_name} · declared {fmt(dep.e.cash)}</div>
            <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
              {['deposited', 'handed_to_mgmt'].map(d => (
                <button key={d} className={dep.disposition === d ? 'btn btn-primary' : 'btn btn-secondary'} style={{ fontSize: 13 }} onClick={() => setDep({ ...dep, disposition: d })}>
                  {d === 'deposited' ? '🏦 Deposited' : '🤝 Handed to mgmt'}</button>
              ))}
            </div>
            {dep.disposition === 'deposited' ? (
              <>
                <div style={{ fontSize: 12, fontWeight: 600 }}>Deposit slip photo (OCR reads the amount)
                  <div style={{ display: 'flex', gap: 8, marginTop: 4, flexWrap: 'wrap', alignItems: 'center' }}>
                    <label className="btn btn-secondary" style={{ fontSize: 12, cursor: 'pointer' }}>📷 Take photo
                      <input type="file" accept="image/*" capture="environment" style={{ display: 'none' }} onChange={async e => { const f = e.target.files?.[0]; if (f) setDep({ ...dep, slip: await fileToDataUrl(f) }); e.currentTarget.value = '' }} /></label>
                    <label className="btn btn-secondary" style={{ fontSize: 12, cursor: 'pointer' }}>🖼️ Upload from files
                      <input type="file" accept="image/*" style={{ display: 'none' }} onChange={async e => { const f = e.target.files?.[0]; if (f) setDep({ ...dep, slip: await fileToDataUrl(f) }); e.currentTarget.value = '' }} /></label>
                    {dep.slip && <span style={{ fontSize: 12, color: 'var(--green)', fontWeight: 600 }}>✓ attached</span>}
                  </div>
                </div>
                <label style={{ fontSize: 12, fontWeight: 600, display: 'block', marginTop: 10 }}>Deposit amount {dep.slip ? '(leave blank to auto-read)' : '(enter manually)'}<br />
                  <input type="number" style={{ ...inp, marginTop: 4 }} placeholder={String(dep.e.cash)} value={dep.deposit_amount} onChange={e => setDep({ ...dep, deposit_amount: e.target.value })} /></label>
              </>
            ) : (
              <label style={{ fontSize: 12, fontWeight: 600 }}>Handed to<br />
                <div style={{ marginTop: 4 }}>
                  <EntityPicker options={empOptions} value={dep.handed_to || null}
                    onChange={v => setDep({ ...dep, handed_to: v || '' })} placeholder="Manager name" width="100%" />
                </div>
              </label>
            )}
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
              <button className="btn btn-secondary" style={{ fontSize: 13 }} disabled={depBusy} onClick={() => setDep(null)}>Cancel</button>
              <button className="btn btn-primary" style={{ fontSize: 13 }} disabled={depBusy} onClick={recordDeposit}>{depBusy ? 'Saving…' : 'Save'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

const L = ({ t, children }: { t: string; children: React.ReactNode }) => (
  <label style={{ fontSize: 11, color: 'var(--text3)' }}><div style={{ marginBottom: 3 }}>{t}</div>{children}</label>
)

const Stat = ({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent?: boolean }) => (
  <div className="card" style={{ padding: '12px 16px', minWidth: 150, flex: '0 1 auto', borderTop: accent ? '3px solid var(--accent)' : undefined }}>
    <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{label}</div>
    <div style={{ fontSize: 22, fontWeight: 700, marginTop: 2 }}>{value}</div>
    {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
  </div>
)
