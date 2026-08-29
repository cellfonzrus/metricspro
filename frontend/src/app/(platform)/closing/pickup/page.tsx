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

// DM cash pickup — see the day's cash envelopes, check off the ones collected with a note, confirm.
// On confirm, the assigned recipient gets an email + WhatsApp summary.
// OWNER DIRECTIVE 2026-08-04 ("cash on hand needs to be completed along with cash pickup"): the page
// also shows each STORE's true accumulated cash on hand (declared-to-date minus everything already
// taken — the same number Store Cash on Hand / Cash Position report, reused not recomputed) and a
// live "left after this pickup" preview as envelopes get checked off — closing the loop the two
// features previously left open (a DM working Day-mode never saw carryover cash sitting in a store).
const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const inp: React.CSSProperties = { ...sel, width: '100%' }
const cell: React.CSSProperties = { padding: '8px 10px', borderTop: '1px solid var(--border)', fontSize: 13, verticalAlign: 'middle' }
const round2 = (n: number) => Math.round((n + Number.EPSILON) * 100) / 100

export default function CashPickupPage() {
  const { user, permissions } = useAuth()
  const [rangeMode, setRangeMode] = useState(false)   // Day | Range (retail-ops-7 item 2)
  const [date, setDate] = useState(localToday())
  const [rangeStart, setRangeStart] = useState(localToday())
  const [rangeEnd, setRangeEnd] = useState(localToday())
  const [market, setMarket] = useState('')
  // OWNER DIRECTIVE 2026-08-04 (market->store cascade + checkbox picker): a manual, multi-select
  // market filter alongside `market` above (unchanged — that one stays the scope AUTO-default for a
  // market-scoped DM, sent to the server exactly as before). `fMarkets` is purely picker-side; it
  // narrows the store checkbox list and, when no explicit store is picked, RESOLVES to that market's
  // full store-code set for the `stores=` query param (owner Q2 — "the filter sent to the backend is
  // the resolved store set").
  const [fMarkets, setFMarkets] = useState<string[]>([])
  const [fStores, setFStores] = useState<string[]>([])   // multi-select stores (chips)
  const [fEmps, setFEmps] = useState<string[]>([])       // multi-select employees (chips) — one store,
                                                          // many closers/day
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
  const [dep, setDep] = useState<any>(null)   // { e, disposition, deposit_amount, handed_to, slip } when depositing
  const [depBusy, setDepBusy] = useState(false)
  const [undoBusy, setUndoBusy] = useState<string | null>(null)   // key of the envelope being undone
  const [pStores, setPStores] = useState<any[]>([])   // store roster (RULE THREE picker — see below)
  const [pEmps, setPEmps] = useState<any[]>([])        // employee roster (RULE THREE picker — see below)

  function fileToDataUrl(f: File): Promise<string> {
    return new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(String(r.result)); r.onerror = rej; r.readAsDataURL(f) })
  }
  async function recordDeposit() {
    if (!dep) return
    setDepBusy(true)
    try {
      const r: any = await api('/api/v1/closing/pickup/deposit', { method: 'POST', body: JSON.stringify({
        store_code: dep.e.store_code, close_date: dep.e.close_date || date, employee_name: dep.e.employee_name,
        disposition: dep.disposition, deposit_amount: dep.deposit_amount || undefined, handed_to: dep.handed_to || undefined,
        declared_amount: dep.e.cash, deposit_slip: dep.slip || undefined,
      }) })
      setMsg(dep.disposition === 'deposited'
        ? (r.flagged ? `⚠️ Deposit ${fmt(r.deposit_amount)} vs declared ${fmt(r.declared_amount)} — flagged for review.` : `✅ Deposit recorded${r.matched ? ' — matches declared cash.' : '.'}`)
        : '✅ Marked handed to management.')
      setDep(null); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    finally { setDepBusy(false) }
  }

  // Undo a mis-tapped pickup confirmation (OWNER DIRECTIVE 2026-08-04 — edit-safe recording).
  // Server refuses (409) once a disposition is already recorded; the cash-on-hand numbers re-derive
  // automatically on the next load() since they're always computed live from cash_pickup rows, never
  // a stored running balance.
  async function undoPickup(e: any) {
    const k = key(e)
    setUndoBusy(k); setMsg('')
    try {
      const r: any = await api('/api/v1/closing/pickup/undo', { method: 'POST', body: JSON.stringify({
        store_code: e.store_code, close_date: e.close_date, employee_name: e.employee_name,
      }) })
      setMsg(r.already ? 'Nothing to undo.' : `↩️ Pickup undone for ${e.store_name || e.store_code} · ${e.employee_name} — cash is back to "still to collect."`)
      load()
    } catch (er: any) { setMsg('❌ ' + (er?.message || er)) }
    finally { setUndoBusy(null) }
  }

  function exportPayload(): ExportPayload {
    // "Stores Not Closed" is single-date only (same restriction as the on-screen card above — a
    // range has no single day to report "not closed" against) and already reflects whatever
    // market/store/dm filters are ACTIVE (computed server-side inside GET /closing/pickups off the
    // same query, RULE FOUR — what you see is what exports).
    const notClosedSheet = (!rangeMode && date && (data?.not_closed || []).length > 0) ? [{
      name: 'Stores Not Closed',
      columns: [
        { header: 'Store', get: (r: any) => r.store_name || r.store_code },
        { header: 'Market', get: (r: any) => r.market || '' },
        { header: 'Who worked', get: (r: any) => r.worked_summary || 'no worked-signal recorded' },
        { header: 'Signal', get: (r: any) => r.worked_source === 'actual' ? 'clock-in/sale' : r.worked_source === 'scheduled' ? 'scheduled only' : 'none' },
      ],
      rows: data?.not_closed || [],
    }] : []
    return {
      title: `Cash pickup — ${date}`, filename: `cash-pickup-${date}`,
      sheets: [{
        name: 'Cash Pickup',
        columns: [
          { header: 'Store', get: (r: any) => r.store_name || r.store_code },
          { header: 'Rep', get: (r: any) => r.employee_name },
          { header: 'Cash', get: (r: any) => r.cash, money: true },
          { header: 'Picked up', get: (r: any) => (r.picked_up ? 'Yes' : 'No') },
          { header: 'By (DM)', get: (r: any) => r.picked_up_by || '' },
          { header: 'Disposition', get: (r: any) => r.disposition || '' },
          { header: 'Deposit', get: (r: any) => r.deposit_amount ?? '', money: true },
          { header: 'Status', get: (r: any) => (r.deposit_flagged ? 'FLAGGED' : (r.deposit_matched ? 'matched' : '')) },
        ],
        rows: data?.envelopes || [],
      }, ...notClosedSheet],
    }
  }

  useEffect(() => { if (user?.market && permissions?.scope === 'market') setMarket(user.market) }, [user, permissions])
  useEffect(() => { api('/api/v1/closing/pickup-config').then(setCfg).catch(() => setCfg({})) }, [])
  // RULE THREE (§3b) pickers for the store/rep filters + the "handed to" field below: the SAME
  // rosters ClosingSubmitForm/cash-config already fetch elsewhere in this module — not derived from
  // the day's (possibly already-filtered) envelope rows, so the pickers stay full even on a slow day.
  useEffect(() => {
    apiCached('/api/v1/closing/stores', LOOKUP).then((s: any) => setPStores(Array.isArray(s) ? s : [])).catch(() => {})
    apiCached('/api/v1/storeops/employees?all_company=true', LOOKUP).then((r: any) => setPEmps(Array.isArray(r) ? r : (r?.employees || []))).catch(() => {})
  }, [])

  // Store roster shaped for the cascade widget (needs `.market` per store) — declared before `load`
  // so the resolved store-code set below can use it.
  const storesForCascade: StoreOpt[] = useMemo(
    () => pStores.filter((s: any) => s.store_code).map((s: any) => ({ id: s.store_code, label: s.store_address || s.store_code, market: s.market || null })),
    [pStores])
  // Resolved store-code set (owner Q2 semantics): explicit store picks win; markets-picked-with-no-
  // explicit-store expands to that market's whole store list; neither -> no store scoping at all. Fed
  // to the SAME `stores=` param the backend already accepted (mig-502-era, retail-ops-7) — no backend
  // change needed.
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
    api(`/api/v1/closing/pickups?${qs}`).then(setData).catch(console.error).finally(() => setLoading(false))
  }, [rangeMode, date, rangeStart, rangeEnd, market, resolvedStores, fEmps, fDm])
  useEffect(() => { load() }, [load])

  // Filters/id use exact server-side matching (store_code exact-match; employee/dm substring on the
  // name) — the SAME query params as before, just picked instead of typed (id === label for the
  // employee/dm name fields; store's id is already the canonical store_code, not a hack).
  const empOptions: EntityOption[] = useMemo(
    () => pEmps.filter((e: any) => (e.name || '').trim()).map((e: any) => ({ id: e.name, label: e.name, sublabel: e.email || undefined })),
    [pEmps])

  const envelopes: any[] = data?.envelopes || []
  // key includes close_date (2026-07-15, range mode): the same store+employee can have a pending
  // envelope on SEVERAL different days when viewing a range — without the date in the key, checking
  // one day's envelope would also (de)select another day's for the same store+rep.
  const key = (e: any) => `${e.close_date || ''}|${e.store_code || ''}|${e.employee_name || ''}`
  const ready = envelopes.filter(e => !e.picked_up)
  const selectedKeys = ready.filter(e => sel_[key(e)])
  const selTotal = selectedKeys.reduce((s, e) => s + (e.cash || 0), 0)

  // Per-store "cash on hand" + a live "left after this pickup" preview (OWNER DIRECTIVE 2026-08-04).
  // `by_store` (backend, reusing the SAME `_cash_position_core` Store Cash on Hand / Cash Position
  // read) already nets out every past pickup/EEP-withdrawal — subtracting just the CURRENTLY-SELECTED
  // envelopes' cash gives exactly what will remain once this batch is confirmed.
  const selByStore = useMemo(() => {
    const m: Record<string, number> = {}
    for (const e of selectedKeys) { const c = e.store_code || ''; m[c] = (m[c] || 0) + (e.cash || 0) }
    return m
  }, [selectedKeys])
  const byStoreView = useMemo(
    () => (data?.by_store || []).map((s: any) => ({
      ...s, selected: selByStore[s.store_code] || 0,
      after: round2((s.cash_on_hand || 0) - (selByStore[s.store_code] || 0)),
    })),
    [data, selByStore])

  async function confirm() {
    if (!selectedKeys.length) { setMsg('Select at least one envelope.'); return }
    setBusy(true); setMsg('')
    try {
      const r = await api('/api/v1/closing/pickup', { method: 'POST', body: JSON.stringify({
        date: rangeMode ? undefined : date, picked_up_by: user?.full_name || 'DM',
        // close_date PER ITEM (mig-502-era backend, retail-ops-7 item 2) so a range-mode multi-day
        // selection is never mis-stamped with one shared date.
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
    try { const r = await api('/api/v1/closing/pickup-config', { method: 'PUT', body: JSON.stringify(cfg) }); setCfg(r); setCfgMsg('✅ Saved.') }
    catch (e: any) { setCfgMsg('❌ ' + (e?.message || e)) }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>💵 Cash Pickup</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Check off each cash envelope you collected, add a note, and confirm. The assigned recipient is notified by email + WhatsApp.</p>
        </div>
        <Link href="/closing" className="btn btn-secondary" style={{ fontSize: 13 }}>← Dashboard</Link>
      </div>

      {/* Recipient config */}
      <div className="card" style={{ padding: 14, marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer' }} onClick={() => setCfgOpen(o => !o)}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>🔔 Pickup notification recipient {cfg?.recipient_email || cfg?.recipient_whatsapp ? '' : '— not set'}</span>
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

      {/* Filters */}
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
        {/* Market->store cascade + checkbox picker (OWNER DIRECTIVE 2026-08-04). `market` (scope
            auto-default) is untouched above; this is the manual, editable multi-market/store filter. */}
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
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          {(data?.envelopes || []).length > 0 && <><ExportButtons payload={exportPayload} compact /><SendReportButton exportPayload={exportPayload} compact /></>}
          <Link href="/closing/cash-position" style={{ fontSize: 12, color: 'var(--accent)', textDecoration: 'none' }}>💰 Cash Position</Link>
          <Link href="/closing/cash-config" style={{ fontSize: 12, color: 'var(--accent)', textDecoration: 'none' }}>⚙️ Setup</Link>
        </div>
      </div>

      {/* Day summary — total cash collected end-of-day + collection progress */}
      {data && (
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
          <Stat label="Total cash (end of day)" value={fmt(data.total_cash || 0)} accent />
          <Stat label="Collected" value={fmt(data.collected_cash || 0)} sub={`${data.collected} envelope${data.collected === 1 ? '' : 's'}`} />
          <Stat label="Still to collect" value={fmt(data.ready_cash || 0)} sub={`${data.ready} envelope${data.ready === 1 ? '' : 's'}`} />
        </div>
      )}

      {/* Per-store cash on hand (OWNER DIRECTIVE 2026-08-04): the TRUE accumulated balance for each
          store as of the filter's as-of date — includes carryover from days outside the current
          view, not just the envelopes on screen. Same number as Cash Position / Store Cash on Hand
          (reused via the backend's _cash_position_core), so this page never drifts from those
          reports. "Left after" updates live as envelopes get checked off below. */}
      {byStoreView.length > 0 && (
        <div className="card" style={{ padding: 14, marginBottom: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
            <div style={{ fontSize: 13, fontWeight: 700 }}>🏦 Cash on hand by store <span style={{ fontWeight: 400, color: 'var(--text3)' }}>as of {data.as_of}</span></div>
            <Link href="/closing/store-cash-on-hand" style={{ fontSize: 12, color: 'var(--accent)', textDecoration: 'none' }}>Full Store Cash on Hand report →</Link>
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {byStoreView.map((s: any) => (
              <div key={s.store_code} style={{ minWidth: 190, padding: '8px 12px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface2)' }}>
                <div style={{ fontSize: 12, fontWeight: 600 }}>{s.store_name}{s.market ? <span style={{ color: 'var(--text3)', fontWeight: 400 }}> · {s.market}</span> : null}</div>
                <div style={{ fontSize: 18, fontWeight: 700, marginTop: 2 }}>{fmt(s.cash_on_hand)}</div>
                {s.selected > 0 && (
                  <div style={{ fontSize: 11, color: 'var(--accent)', marginTop: 2 }}>− {fmt(s.selected)} selected → {fmt(s.after)} left</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Stores that did NOT submit a daily closing for the selected day (single-day only — ambiguous over a range).
          OWNER REQUEST 2026-08-06 ("it should also show the sales rep who worked that day"): each chip now names
          who to actually chase — reusing the backend's shared who-worked signal (clocked-in ∪ B2B-sold), the SAME
          one DM-Verify's missing-rep check uses. `worked_source` distinguishes real signal from a scheduled-only
          fallback (labeled, never presented as fact) and "no worked-signal recorded" (data gap, not an empty store). */}
      {data && !rangeMode && date && (
        (data.not_closed || []).length > 0 ? (
          <div className="card" style={{ padding: 14, marginBottom: 16, borderLeft: '3px solid #dc2626' }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8, color: '#dc2626' }}>
              ⚠️ {data.not_closed.length} store{data.not_closed.length === 1 ? '' : 's'} did not do the daily closing for {date}
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {(data.not_closed as any[]).map(s => (
                <div key={s.store_code} style={{ fontSize: 12, padding: '6px 10px', borderRadius: 7, background: 'var(--surface2)', border: '1px solid var(--border)', minWidth: 200, maxWidth: 280 }}>
                  <div style={{ fontWeight: 600 }}>
                    {s.store_name || s.store_code}{s.market ? <span style={{ color: 'var(--text3)', fontWeight: 400 }}> · {s.market}</span> : null}
                  </div>
                  <div
                    style={{
                      marginTop: 3, fontSize: 11,
                      color: s.worked_source === 'actual' ? 'var(--text2)' : s.worked_source === 'scheduled' ? '#b45309' : 'var(--text3)',
                      fontStyle: s.worked_source === 'none' ? 'italic' : 'normal',
                    }}
                    title={(s.worked || []).map((w: any) => `${w.name}${w.emails ? ` — name shared by: ${w.emails.join(', ')}` : (w.email ? ` (${w.email})` : '')}${w.tag && w.tag !== 'clocked' ? ` [${w.tag}]` : ''}`).join('\n')}
                  >
                    {s.worked_source === 'scheduled' && '📅 '}
                    {s.worked_summary || 'no worked-signal recorded'}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 16 }}>✅ All active stores submitted a closing for {date}.</div>
        )
      )}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : envelopes.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>No cash envelopes for {rangeMode ? `${rangeStart} → ${rangeEnd}` : date}.</div>
      ) : (
        <>
          <div className="card table-wrapper" style={{ padding: 0 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                {[...(rangeMode ? ['Date'] : []), '', 'Store', 'Rep', 'Cash', 'Envelope', 'Note / status', 'Deposit'].map((h, i) =>
                  <th key={i} style={{ textAlign: 'left', padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
              </tr></thead>
              <tbody>
                {envelopes.map(e => {
                  const k = key(e); const done = e.picked_up
                  return (
                    <tr key={k} style={{ background: done ? 'var(--surface2)' : undefined }}>
                      {rangeMode && <td style={{ ...cell, fontSize: 12, color: 'var(--text3)' }}>{e.close_date}</td>}
                      <td style={cell}>{done ? '✅' : <input type="checkbox" checked={!!sel_[k]} onChange={ev => setSel(s => ({ ...s, [k]: ev.target.checked }))} />}</td>
                      <td style={cell}>{e.store_name || e.store_code || '—'}</td>
                      <td style={cell}>{e.employee_name || '—'}</td>
                      <td style={{ ...cell, fontWeight: 600 }}>{fmt(e.cash)}</td>
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
                              {/* Undo (OWNER DIRECTIVE 2026-08-04 — edit-safe recording): only offered before a
                                  disposition is recorded; once deposited/handed off it's a completed cash event. */}
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
              {busy ? '⏳ Confirming…' : `✅ Confirm pickup (${selectedKeys.length} · ${fmt(selTotal)})`}
            </button>
            {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
          </div>
        </>
      )}

      {dep && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50 }} onClick={() => !depBusy && setDep(null)}>
          <div className="card" style={{ padding: 22, width: 420, maxWidth: '92vw' }} onClick={e => e.stopPropagation()}>
            <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 4 }}>Record cash disposition</div>
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
                    {/* Camera (capture) + file picker (no capture) — owner directive 2026-08-19. */}
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
