'use client'
// Store Setup (Phase W2, owner directive 2026-09-01) — the STORES half of the old combined
// /storeops/admin page, lifted verbatim into its own route (mechanical extraction; shared helpers
// in ../lib.tsx). /storeops/admin keeps working for backward compat with a banner pointing here.
import React, { useState, useEffect } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { sel, cell, STORE_EDIT_FIELDS, STORE_TZ_OPTS, isDirty, MarketField, StoreMultiSelect } from '../lib'
import LeasePanel from './LeasePanel'
import SalesTaxRateLink from '@/components/SalesTaxRateLink'

type ClosingSrcCfg = {
  org_default: string
  house_default: string
  labels: Record<string, string>
  sources: string[]
  store_overrides: { store_code: string; source: string; updated_at?: string; updated_by?: string }[]
}

const CLOSING_SRC_LABEL: Record<string, string> = {
  rep_entry: 'Sales reps submit it',
  b2b_derived: 'Derived from the sales feed',
}

export default function StoreSetupPage() {
  const [stores, setStores] = useState<any[]>([])
  const [origStores, setOrigStores] = useState<Record<string, any>>({})
  const [rowBusy, setRowBusy] = useState<Record<string, boolean>>({})
  const [rowMsg, setRowMsg] = useState<Record<string, string>>({})
  const [bulkBusy, setBulkBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [upBusy, setUpBusy] = useState(false)
  // `closing_source: ''` = follow the company default. The owner's "selected at the time of setting up
  // the store" is this field: the choice is made on the ADD row, not only after the store exists.
  const [newStore, setNewStore] = useState<any>({ store_code: '', address: '', market: '', monthly_target: '', timezone: '', closing_source: '' })
  // Lease & Insurance (owner 2026-09-03, mig 946): per-store expandable panel — landlord, rent
  // rails/ACH, escalation, rent-due, insurance, lease/COI docs. Server-gated (management only).
  const [leaseOpen, setLeaseOpen] = useState<Record<string, boolean>>({})
  const [markets, setMarkets] = useState<string[]>([])   // RULE THREE dropdown options (GET /storeops/markets)
  // DAILY CLOSING SOURCE (owner 2026-10-02, mig 1035): who produces this store's daily closing —
  // its sales reps type it, or it is derived from the sales feed. Chosen here at store setup and
  // changeable at any time; the backend holds ONE registry (closing/closing_source.py) that the
  // submit endpoint, the deadline alert, the stale-store check and the closing picker all read, so
  // this screen is the only place the answer is SET and nothing re-derives it.
  const [srcCfg, setSrcCfg] = useState<ClosingSrcCfg | null>(null)
  const [srcBusy, setSrcBusy] = useState<Record<string, boolean>>({})
  // BACKFILL (owner 2026-10-02: "do it retroactive from the data you can pull for whatever time").
  // The endpoint has existed since the backfill PR; without a button the only way to run it was a
  // hand-made HTTP call, which is not a thing a tenant admin should have to do. Preview first is the
  // default posture: `dry_run` reports the whole span and writes nothing.
  // SEVERAL STORES AT ONCE (owner 2026-10-03: "in store setup to assign the store it should be a drop
  // down list to select multiple stores"). The per-row column stays — this is the same ONE setting,
  // set for a selection instead of one row at a time, through the same ONE endpoint.
  const [bulkSrcCodes, setBulkSrcCodes] = useState<string[]>([])
  const [bulkSrcValue, setBulkSrcValue] = useState('b2b_derived')
  const [bfFrom, setBfFrom] = useState('')
  const [bfTo, setBfTo] = useState('')
  const [bfBusy, setBfBusy] = useState('')
  const [bfRes, setBfRes] = useState<any>(null)
  // The panel reports its OWN progress and its OWN failure. It used to report both through the
  // page-level `msg` at the very top of the screen, far above the button that was pressed, so a
  // failed Preview looked like a button that did nothing (owner report 2026-10-03).
  const [bfProg, setBfProg] = useState('')
  const [bfErr, setBfErr] = useState('')

  async function loadAll() {
    setLoading(true)
    try {
      const [s, mk, cs] = await Promise.all([
        // 2026-08-06: GET /stores now defaults to active-only (the disabled-T-store picker-leak fix)
        // — this page manages/re-enables stores, so it MUST keep seeing inactive ones.
        api('/api/v1/storeops/stores?include_inactive=true').catch(() => []),
        api('/api/v1/storeops/markets').catch(() => ({ markets: [] })),
        // Never fails this page: a tenant on a deploy without migration 1035 gets null and the
        // column falls back to the house default (reps submit), which is what the backend resolves too.
        api('/api/v1/closing/source-config').catch(() => null),
      ])
      const sList = (s || []).map((x: any) => ({ ...x }))
      setStores(sList)
      setMarkets(mk?.markets || [])
      setSrcCfg(cs || null)
      setOrigStores(Object.fromEntries(sList.map((x: any) => [x.id, { ...x }])))
      setRowMsg({})
    } catch (err: any) { setMsg('Load failed: ' + (err?.message || err)) }
    setLoading(false)
  }
  useEffect(() => { loadAll() }, [])

  const setStore = (id: any, patch: any) => setStores(ss => ss.map(s => s.id === id ? { ...s, ...patch } : s))

  function flashRow(key: string, text: string, ms = 2500) {
    setRowMsg(m => ({ ...m, [key]: text }))
    if (ms) setTimeout(() => setRowMsg(m => (m[key] === text ? { ...m, [key]: '' } : m)), ms)
  }

  async function refreshMarkets() {
    try { const mk = await api('/api/v1/storeops/markets'); setMarkets(mk?.markets || []) } catch { /* best-effort */ }
  }

  async function saveStore(s: any) {
    setMsg('')
    const key = `store-${s.id}`
    setRowBusy(b => ({ ...b, [key]: true }))
    try {
      await api(`/api/v1/storeops/stores/${s.id}`, { method: 'PATCH', body: JSON.stringify({
        store_code: s.store_code, address: s.address, market: s.market,
        monthly_target: Number(s.monthly_target) || 0, net_profit_target: Number(s.net_profit_target) || 0,
        is_active: !!s.is_active, phone: s.phone,
        timezone: s.timezone || null,
      }) })
      setOrigStores(o => ({ ...o, [s.id]: { ...s } }))
      setMsg(`Saved ${s.store_code}`)
      flashRow(key, '✓ saved')
      refreshMarkets()
    } catch (err: any) { setMsg('Save failed: ' + (err?.message || err)); flashRow(key, '✗ failed') }
    finally { setRowBusy(b => ({ ...b, [key]: false })) }
  }

  // AUTO-SAVE: a store's Active checkbox saves itself immediately (optimistic; rolls back on failure).
  async function toggleStoreActive(s: any, checked: boolean) {
    const prevVal = !!s.is_active
    const key = `store-${s.id}`
    setStore(s.id, { is_active: checked })
    setRowBusy(b => ({ ...b, [key]: true }))
    try {
      await api(`/api/v1/storeops/stores/${s.id}`, { method: 'PATCH', body: JSON.stringify({ is_active: checked }) })
      setOrigStores(o => ({ ...o, [s.id]: { ...(o[s.id] || s), is_active: checked } }))
      flashRow(key, checked ? '✓ activated' : '✓ deactivated')
    } catch (err: any) {
      setStore(s.id, { is_active: prevVal })   // rollback — never show a fake success
      flashRow(key, '✗ ' + (err?.message || 'save failed'), 5000)
    } finally { setRowBusy(b => ({ ...b, [key]: false })) }
  }

  async function saveAllStores() {
    const dirty = stores.filter(s => isDirty(s, origStores[s.id], STORE_EDIT_FIELDS))
    if (!dirty.length) return
    setBulkBusy(true); setMsg('')
    let ok = 0, fail = 0
    for (const s of dirty) {
      try {
        await api(`/api/v1/storeops/stores/${s.id}`, { method: 'PATCH', body: JSON.stringify({
          store_code: s.store_code, address: s.address, market: s.market,
          monthly_target: Number(s.monthly_target) || 0, is_active: !!s.is_active, phone: s.phone,
        }) })
        setOrigStores(o => ({ ...o, [s.id]: { ...s } }))
        flashRow(`store-${s.id}`, '✓ saved')
        ok++
      } catch (err: any) { flashRow(`store-${s.id}`, '✗ failed', 5000); fail++ }
    }
    setMsg(`Saved ${ok} store(s)${fail ? ` · ${fail} failed (see row)` : ''}.`)
    setBulkBusy(false)
    refreshMarkets()
  }

  async function addStore() {
    if (!newStore.store_code.trim()) { setMsg('Store code is required.'); return }
    setMsg('')
    try {
      const { closing_source, ...storeBody } = newStore
      await api('/api/v1/storeops/stores', { method: 'POST', body: JSON.stringify({ ...storeBody, monthly_target: Number(newStore.monthly_target) || 0 }) })
      // The store's own daily-closing setting, written right after the store exists (it is keyed by
      // store_code, so it cannot be set before). Left blank, the store simply follows the company
      // default and no override row is created — so adding a store is unchanged for anyone who
      // ignores this field.
      if (closing_source) {
        try {
          await api('/api/v1/closing/source-config', { method: 'PUT', body: JSON.stringify({ store_code: newStore.store_code.trim(), source: closing_source }) })
        } catch (err: any) {
          // Never report the store as fully added when half of it failed.
          setMsg(`Added ${newStore.store_code}, but its daily-closing setting was NOT saved (${err?.message || err}). Set it on the store's row below.`)
          setNewStore({ store_code: '', address: '', market: '', monthly_target: '', timezone: '', closing_source: '' })
          await loadAll()
          return
        }
      }
      setMsg(`Added ${newStore.store_code}`)
      setNewStore({ store_code: '', address: '', market: '', monthly_target: '', timezone: '', closing_source: '' })
      await loadAll()
    } catch (err: any) { setMsg('Add failed: ' + (err?.message || err)) }
  }

  // ---- daily closing source (per store, over the org default) ----
  const srcOf = (code: string) => {
    const ov = (srcCfg?.store_overrides || []).find(o => (o.store_code || '').toUpperCase() === (code || '').toUpperCase())
    return ov ? ov.source : (srcCfg?.org_default || 'rep_entry')
  }
  const srcIsOverride = (code: string) =>
    (srcCfg?.store_overrides || []).some(o => (o.store_code || '').toUpperCase() === (code || '').toUpperCase())

  // `value` is '' for "follow the company default" (clears the override), else a source key.
  async function saveClosingSource(rowId: any, code: string, value: string) {
    if (!code) return
    const key = `src-${code}`
    setSrcBusy(b => ({ ...b, [key]: true }))
    try {
      const body = value ? { store_code: code, source: value } : { store_code: code, clear: true }
      await api('/api/v1/closing/source-config', { method: 'PUT', body: JSON.stringify(body) })
      const cs = await api('/api/v1/closing/source-config')
      setSrcCfg(cs || null)
      flashRow(`store-${rowId}`, '✓ closing source saved')
    } catch (err: any) {
      flashRow(`store-${rowId}`, '✗ ' + (err?.message || 'closing source not saved'), 5000)
    } finally { setSrcBusy(b => ({ ...b, [key]: false })) }
  }

  async function saveOrgClosingSource(value: string) {
    const key = 'src-org'
    setSrcBusy(b => ({ ...b, [key]: true }))
    setMsg('')
    try {
      await api('/api/v1/closing/source-config', { method: 'PUT', body: JSON.stringify({ source: value }) })
      const cs = await api('/api/v1/closing/source-config')
      setSrcCfg(cs || null)
      setMsg(`Company default for new stores: ${value === 'b2b_derived' ? 'derived from the sales feed' : 'sales reps submit it'}.`)
    } catch (err: any) { setMsg('Could not save the company default: ' + (err?.message || err)) }
    finally { setSrcBusy(b => ({ ...b, [key]: false })) }
  }

  // ONE call for the whole selection: the endpoint takes `store_codes` and fans out through its own
  // single row-writer, so picking ten stores cannot behave differently from picking one.
  async function applyClosingSourceToStores() {
    if (!bulkSrcCodes.length) { setMsg('Pick at least one store in the dropdown first.'); return }
    const key = 'src-bulk'
    setSrcBusy(b => ({ ...b, [key]: true }))
    setMsg('')
    try {
      const body = bulkSrcValue
        ? { store_codes: bulkSrcCodes, source: bulkSrcValue }
        : { store_codes: bulkSrcCodes, clear: true }
      const r = await api('/api/v1/closing/source-config', { method: 'PUT', body: JSON.stringify(body) })
      const cs = await api('/api/v1/closing/source-config')
      setSrcCfg(cs || null)
      const label = bulkSrcValue
        ? (srcCfg?.labels?.[bulkSrcValue] || CLOSING_SRC_LABEL[bulkSrcValue] || bulkSrcValue).toLowerCase()
        : 'the company default'
      setMsg(`${r?.count || bulkSrcCodes.length} store(s) set to ${label}: ${bulkSrcCodes.join(', ')}.`)
    } catch (err: any) { setMsg('Could not set those stores: ' + (err?.message || err)) }
    finally { setSrcBusy(b => ({ ...b, [key]: false })) }
  }

  // One call per run; the server walks the span day by day through the SAME nightly sweep, so a
  // preview and a real run differ only in whether anything is written.
  // WHY THE SPAN IS SENT IN CHUNKS (owner report 2026-10-03: "does not show anything in preview").
  // A proxied request on the platform must produce its first byte within 120 s or the edge kills it
  // with ROUTER_EXTERNAL_TARGET_ERROR (index §40.3). One call covering four months walks ~124 days of
  // the sales feed and cannot answer inside that budget, so the single-call version could only ever
  // fail on a real backfill — and the failure was reported at the top of the page, out of sight.
  //
  // THE CLASS, NOT THE INSTANCE: an admin action whose work grows with its input must not be ONE
  // synchronous request. §40.3's registered answer for an endpoint that outruns the proxy is the
  // DIRECT_ROUTES class, and derive-range was never in it — but direct would only move the ceiling
  // and still show nothing for minutes. Bounded chunks cost the same total work, keep every request
  // far inside the budget, show progress as they land, and leave the days already done DONE when one
  // chunk fails. The endpoint is unchanged: still the ONE range endpoint, never a per-day loop.
  const BACKFILL_CHUNK_DAYS = 7

  function backfillChunks(from: string, to: string, size = BACKFILL_CHUNK_DAYS): { start: string; end: string }[] {
    const DAY = 86400000
    const t0 = Date.parse(`${from}T00:00:00Z`)
    const t1 = Date.parse(`${to}T00:00:00Z`)
    if (!Number.isFinite(t0) || !Number.isFinite(t1) || t1 < t0) return []
    const iso = (t: number) => new Date(t).toISOString().slice(0, 10)
    const out: { start: string; end: string }[] = []
    for (let t = t0; t <= t1; t += size * DAY) {
      out.push({ start: iso(t), end: iso(Math.min(t + (size - 1) * DAY, t1)) })
    }
    return out
  }

  function addTotals(into: any, from: any) {
    const out = { ...(into || {}) }
    for (const k of Object.keys(from || {})) out[k] = (Number(out[k]) || 0) + (Number(from[k]) || 0)
    return out
  }

  async function runBackfill(dryRun: boolean) {
    setBfErr('')
    if (!bfFrom || !bfTo) { setBfErr('Pick both a start and an end date for the backfill.'); return }
    const chunks = backfillChunks(bfFrom, bfTo)
    if (!chunks.length) { setBfErr('The end date is before the start date.'); return }
    setBfBusy(dryRun ? 'preview' : 'run')
    setMsg('')
    setBfRes(null)
    const agg: any = { start: bfFrom, end: bfTo, dry_run: dryRun, days: 0, days_with_feed: 0,
                       totals: {}, per_day: [], failed: [] }
    let i = 0
    for (const c of chunks) {
      i++
      setBfProg(`${dryRun ? 'Checking' : 'Filling in'} ${c.start} to ${c.end} — part ${i} of ${chunks.length}…`)
      try {
        const q = `start=${encodeURIComponent(c.start)}&end=${encodeURIComponent(c.end)}&dry_run=${dryRun ? 'true' : 'false'}`
        const r = await api(`/api/v1/closing/derive-range?${q}`, { method: 'POST' })
        agg.days += Number(r?.days) || 0
        agg.days_with_feed += Number(r?.days_with_feed) || 0
        agg.totals = addTotals(agg.totals, r?.totals)
        agg.per_day = [...agg.per_day, ...(r?.per_day || [])]
        agg.failed = [...agg.failed, ...(r?.failed || [])]
        setBfRes({ ...agg, partial: i < chunks.length })   // show what has landed so far
      } catch (err: any) {
        // Honest about what DID happen: the days already covered stay covered, and the rest is named.
        setBfErr(`Stopped at ${c.start} to ${c.end} (part ${i} of ${chunks.length}): `
                 + (err?.message || err)
                 + (i > 1 ? ` — the ${agg.days} day(s) before it ${dryRun ? 'were checked' : 'were filled in'} and are not lost.`
                          : ''))
        setBfProg('')
        setBfBusy('')
        return
      }
    }
    setBfProg('')
    setBfRes({ ...agg, partial: false })
    const t = agg.totals || {}
    setMsg(dryRun
      ? `Preview only, nothing written: ${t.wrote || 0} day-stores would be written, ${t.unchanged || 0} already current, ${t.kept_manual || 0} left as submitted, ${t.skipped || 0} with no feed.`
      : `Backfill done: ${t.wrote || 0} written, ${t.updated || 0} refreshed, ${t.unchanged || 0} already current, ${t.kept_manual || 0} left as submitted, ${t.skipped || 0} with no feed.`)
    setBfBusy('')
  }

  // ---- bulk STORE setup ----
  async function downloadStoreTemplate() {
    const XLSX = await import('xlsx')
    const aoa = [['store_code', 'address', 'market', 'monthly_target', 'phone'],
      ['STORE01', '123 Main St, City, ST', 'North', '', '2125550123']]
    const ws = XLSX.utils.aoa_to_sheet(aoa)
    ws['!cols'] = [{ wch: 12 }, { wch: 30 }, { wch: 14 }, { wch: 14 }, { wch: 16 }]
    const wb = XLSX.utils.book_new(); XLSX.utils.book_append_sheet(wb, ws, 'Stores')
    XLSX.writeFile(wb, 'stores-template.xlsx')
  }
  async function uploadStoreBulk(file: File) {
    setUpBusy(true); setMsg('Reading sheet…')
    try {
      const XLSX = await import('xlsx')
      const wb = XLSX.read(await file.arrayBuffer())
      const raw: any[] = XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { defval: '' })
      const pick = (r: any, keys: string[]) => { for (const k of Object.keys(r)) if (keys.includes(k.trim().toLowerCase())) return String(r[k]).trim(); return '' }
      const storeRows = raw.map(r => ({
        store_code: pick(r, ['store_code', 'store code', 'store', 'code']),
        address: pick(r, ['address', 'location']),
        market: pick(r, ['market', 'region', 'district']),
        monthly_target: parseFloat(pick(r, ['monthly_target', 'monthly target', 'target'])) || 0,
        phone: pick(r, ['phone']),
      })).filter(r => r.store_code)
      if (!storeRows.length) { setMsg('No valid rows (each needs a store_code).'); setUpBusy(false); return }
      const res = await api('/api/v1/storeops/stores/bulk', { method: 'POST', body: JSON.stringify({ stores: storeRows }) })
      setMsg(`Added ${res.inserted} store(s)${res.skipped ? ` · ${res.skipped} skipped (blank / duplicate code)` : ''}.`)
      await loadAll()
    } catch (err: any) { setMsg('Upload failed: ' + (err?.message || err)) }
    setUpBusy(false)
  }

  const dirtyStoreCount = stores.filter(s => isDirty(s, origStores[s.id], STORE_EDIT_FIELDS)).length

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🏬 Store Setup</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Store codes, addresses, markets, time zones and targets — add, edit, bulk-upload.
            People live in <Link href="/storeops/setup/employees" style={{ color: 'var(--accent)' }}>Employee Setup</Link>.
          </p>
        </div>
        {/* A store's sales-tax RATE is set in POS Settings -> Sales Tax (store > market > company).
            It is not a column on this page, and there was no way to reach it from store setup
            (owner report 2026-09-07). */}
        <SalesTaxRateLink label="Sales-tax rates" />
      </div>

      {msg && <div style={{ fontSize: 13, marginBottom: 12 }}>{msg}</div>}

      {loading ? <div style={{ padding: 40, color: 'var(--text3)' }}>Loading…</div> : (
        <>
          {/* Daily closing source — the COMPANY DEFAULT (owner 2026-10-02). Every store follows this
              unless its own row below says otherwise. Switching a store to the feed means nobody
              there submits a closing: it is written from the sales feed each night, and cash pickup,
              the envelope report and every closing report then run on it exactly as they do today. */}
          <div className="card" style={{ padding: 14, marginBottom: 14 }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 6 }}>🧾 Daily closing source</div>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <span style={{ fontSize: 13, color: 'var(--text2)' }}>Company default:</span>
              <select style={{ ...sel, width: 240 }} value={srcCfg?.org_default || 'rep_entry'}
                disabled={!!srcBusy['src-org']}
                onChange={e => saveOrgClosingSource(e.target.value)}
                title="What a store does when its own setting below is left on the company default">
                {(srcCfg?.sources || ['rep_entry', 'b2b_derived']).map(v =>
                  <option key={v} value={v}>{srcCfg?.labels?.[v] || CLOSING_SRC_LABEL[v] || v}</option>)}
              </select>
              {srcBusy['src-org'] && <span style={{ fontSize: 12, color: 'var(--text3)' }}>Saving…</span>}
            </div>
            <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 13, margin: '8px 0 0' }}>
              A store set to <strong>sales reps submit it</strong> works exactly as it does today — a rep
              fills in the Daily Closing form. A store set to <strong>derived from the sales feed</strong>
              {' '}takes no submission at all: its closing is written from the sales feed, and everything
              that follows a closing (cash pickup, envelopes, DM verify, the closing reports) runs on it
              unchanged. If the feed has not landed for a day, nothing is written and the day is reported
              as missing — it is never filled in with zeros.
            </p>
            {/* Several stores at once — the shared checkbox dropdown (../lib -> CheckboxDropdown),
                so an admin switching nine stores picks nine and presses one button. */}
            <div style={{ borderTop: '1px solid var(--line)', marginTop: 12, paddingTop: 12 }}>
              <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 6 }}>Set several stores at once</div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <StoreMultiSelect stores={stores} value={bulkSrcCodes} onChange={setBulkSrcCodes}
                  width={300} placeholder="Select stores…" disabled={!!srcBusy['src-bulk']} />
                <span style={{ fontSize: 13, color: 'var(--text2)' }}>take their daily closing from</span>
                <select style={{ ...sel, width: 240 }} value={bulkSrcValue}
                  disabled={!!srcBusy['src-bulk']}
                  onChange={e => setBulkSrcValue(e.target.value)}>
                  {(srcCfg?.sources || ['rep_entry', 'b2b_derived']).map(v =>
                    <option key={v} value={v}>{srcCfg?.labels?.[v] || CLOSING_SRC_LABEL[v] || v}</option>)}
                  <option value="">Company default ({srcCfg?.labels?.[srcCfg?.org_default || 'rep_entry']
                    || CLOSING_SRC_LABEL[srcCfg?.org_default || 'rep_entry']})</option>
                </select>
                <button className="btn" disabled={!!srcBusy['src-bulk'] || !bulkSrcCodes.length}
                  onClick={applyClosingSourceToStores}
                  title="Applies this one setting to every store ticked in the dropdown">
                  {srcBusy['src-bulk'] ? 'Saving…' : `Apply to ${bulkSrcCodes.length || 0} store(s)`}
                </button>
              </div>
              <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 13, margin: '8px 0 0' }}>
                Each store&apos;s own setting is still shown in the <strong>Daily closing</strong> column
                below, and changing it there still works — this is the same setting for a whole
                selection. Choosing <strong>Company default</strong> removes those stores&apos; own
                setting so they follow the company default again.
              </p>
            </div>

            {/* Fill in past days for the feed-derived stores. Preview first — it writes nothing. */}
            <div style={{ borderTop: '1px solid var(--line)', marginTop: 12, paddingTop: 12 }}>
              <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 6 }}>Fill in past days</div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ fontSize: 13, color: 'var(--text2)' }}>From</span>
                <input type="date" style={{ ...sel, width: 160 }} value={bfFrom}
                  onChange={e => setBfFrom(e.target.value)} />
                <span style={{ fontSize: 13, color: 'var(--text2)' }}>to</span>
                <input type="date" style={{ ...sel, width: 160 }} value={bfTo}
                  onChange={e => setBfTo(e.target.value)} />
                <button className="btn" disabled={!!bfBusy} onClick={() => runBackfill(true)}
                  title="Reports what it would write, without writing anything">
                  {bfBusy === 'preview' ? 'Checking…' : 'Preview'}
                </button>
                <button className="btn" disabled={!!bfBusy} onClick={() => runBackfill(false)}
                  title="Writes the closings for every day in the range that has sales-feed data">
                  {bfBusy === 'run' ? 'Filling in…' : 'Fill in these days'}
                </button>
              </div>
              <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 13, margin: '8px 0 0' }}>
                This only touches stores set to <strong>derived from the sales feed</strong>. A day a rep
                already submitted is left exactly as it is, a day the feed has nothing for is reported
                rather than written, and running it twice over the same dates changes nothing the second
                time. Start with <strong>Preview</strong> — it writes nothing.
              </p>
              {/* Progress and failure BESIDE the button, not at the top of the page. */}
              {bfProg && (
                <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text2)' }}>{bfProg}</div>
              )}
              {bfErr && (
                <div style={{ marginTop: 10, fontSize: 12, color: 'var(--danger, #c00)' }}>{bfErr}</div>
              )}
              {bfRes && (
                <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text2)' }}>
                  <div>
                    {bfRes.dry_run ? 'Preview' : 'Filled in'} {bfRes.start} to {bfRes.end}
                    {bfRes.partial ? ' (so far)' : ''}
                    {' — '}{bfRes.days} days, {bfRes.days_with_feed} with sales-feed data.
                  </div>
                  {/* WHAT IT WOULD WRITE, in the panel. These counts are the whole point of a
                      preview and they used to appear only in the page-level message at the top of
                      the screen, which is the same "reported away from the button" defect as the
                      failure line above (owner report 2026-10-03). */}
                  <div style={{ marginTop: 4 }}>
                    {bfRes.dry_run ? 'Would write' : 'Written'}: {bfRes.totals?.wrote || 0}
                    {' · '}{bfRes.dry_run ? 'would refresh' : 'refreshed'}: {bfRes.totals?.updated || 0}
                    {' · '}already current: {bfRes.totals?.unchanged || 0}
                    {' · '}left as a rep submitted it: {bfRes.totals?.kept_manual || 0}
                    {' · '}skipped, no feed for that store: {bfRes.totals?.skipped || 0}
                  </div>
                  {(bfRes.per_day || []).filter((d: any) => !d.b2b_has_data).length > 0 && (
                    <div style={{ marginTop: 4 }}>
                      No feed data on: {(bfRes.per_day || []).filter((d: any) => !d.b2b_has_data)
                        .map((d: any) => d.date).join(', ')}
                    </div>
                  )}
                  {(bfRes.failed || []).length > 0 && (
                    <div style={{ marginTop: 4, color: 'var(--danger, #c00)' }}>
                      Could not be done: {(bfRes.failed || []).map((f: any) => f.date).join(', ')}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Add store */}
          <div className="card" style={{ padding: 14, marginBottom: 14 }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>➕ Add store</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <input style={{ ...sel, width: 120 }} placeholder="Store code *" value={newStore.store_code} onChange={e => setNewStore({ ...newStore, store_code: e.target.value })} />
              <input style={{ ...sel, width: 220 }} placeholder="Address" value={newStore.address} onChange={e => setNewStore({ ...newStore, address: e.target.value })} />
              <MarketField width={130} value={newStore.market} options={markets} onChange={v => setNewStore({ ...newStore, market: v })} />
              <select style={{ ...sel, width: 150 }} value={newStore.timezone} onChange={e => setNewStore({ ...newStore, timezone: e.target.value })} title="Store time zone — blank uses the company default">
                {STORE_TZ_OPTS.map(t => <option key={t.v || 'default'} value={t.v}>{t.label}</option>)}
              </select>
              <input style={{ ...sel, width: 120 }} type="number" placeholder="Monthly target" value={newStore.monthly_target} onChange={e => setNewStore({ ...newStore, monthly_target: e.target.value })} />
              <select style={{ ...sel, width: 200 }} value={newStore.closing_source}
                onChange={e => setNewStore({ ...newStore, closing_source: e.target.value })}
                title="Who produces this store's daily closing — blank follows the company default">
                <option value="">
                  Daily closing: company default ({srcCfg?.labels?.[srcCfg?.org_default || 'rep_entry']
                    || CLOSING_SRC_LABEL[srcCfg?.org_default || 'rep_entry']})
                </option>
                {(srcCfg?.sources || ['rep_entry', 'b2b_derived']).map(v =>
                  <option key={v} value={v}>Daily closing: {srcCfg?.labels?.[v] || CLOSING_SRC_LABEL[v] || v}</option>)}
              </select>
              <button className="btn btn-primary" onClick={addStore}>➕ Add</button>
            </div>
          </div>

          {/* Bulk store setup */}
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 13, color: 'var(--text3)' }}>{stores.length} stores</span>
            <button className="btn btn-primary" disabled={!dirtyStoreCount || bulkBusy} onClick={saveAllStores} title="Save every changed store row in one action">
              {bulkBusy ? '⏳ Saving…' : `💾 Save All Changed${dirtyStoreCount ? ` (${dirtyStoreCount})` : ''}`}
            </button>
            <div style={{ flex: 1 }} />
            <span style={{ fontSize: 13, fontWeight: 600 }}>Bulk add stores:</span>
            <button className="btn" onClick={downloadStoreTemplate}>⬇️ Template</button>
            <label className="btn" style={{ cursor: upBusy ? 'default' : 'pointer', margin: 0 }}>
              {upBusy ? '⏳ Uploading…' : '⬆️ Upload stores'}
              <input type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} disabled={upBusy}
                onChange={e => { const f = e.target.files?.[0]; if (f) uploadStoreBulk(f); e.currentTarget.value = '' }} />
            </label>
          </div>

          <div className="table-wrapper">
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                {['Store code', 'Address', 'Market', 'Time zone', 'Monthly target', 'Daily closing', 'Active', ''].map(h =>
                  <th key={h} style={{ textAlign: 'left', padding: '8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>)}
              </tr></thead>
              <tbody>
                {stores.map(s => {
                  const key = `store-${s.id}`
                  const dirty = isDirty(s, origStores[s.id], STORE_EDIT_FIELDS)
                  return (
                  <React.Fragment key={s.id}>
                  <tr style={{ opacity: s.is_active ? 1 : 0.5 }}>
                    <td style={cell}><input style={{ ...sel, width: 110 }} value={s.store_code || ''} onChange={ev => setStore(s.id, { store_code: ev.target.value })} /></td>
                    <td style={cell}><input style={{ ...sel, width: 220 }} value={s.address || ''} onChange={ev => setStore(s.id, { address: ev.target.value })} /></td>
                    <td style={cell}><MarketField width={130} value={s.market} options={markets} onChange={v => setStore(s.id, { market: v })} /></td>
                    <td style={cell}>
                      <select style={{ ...sel, width: 150 }} value={s.timezone || ''} onChange={ev => setStore(s.id, { timezone: ev.target.value || null })} title="Store time zone — blank uses the company default">
                        {STORE_TZ_OPTS.map(t => <option key={t.v || 'default'} value={t.v}>{t.label}</option>)}
                      </select>
                    </td>
                    <td style={cell}>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                        <input style={{ ...sel, width: 120 }} type="number" title="Monthly sales/production target" value={s.monthly_target ?? ''} onChange={ev => setStore(s.id, { monthly_target: ev.target.value })} />
                        <input style={{ ...sel, width: 120 }} type="number" title="Net profit target ($) — the P&L goal" placeholder="Net profit $" value={s.net_profit_target ?? ''} onChange={ev => setStore(s.id, { net_profit_target: ev.target.value })} />
                      </div>
                    </td>
                    {/* Daily closing source — blank = follow the company default, so an admin can
                        change the default once without this column pinning every store to the old value. */}
                    <td style={cell}>
                      <select style={{ ...sel, width: 200 }}
                        value={srcIsOverride(s.store_code) ? srcOf(s.store_code) : ''}
                        disabled={!s.store_code || !!srcBusy[`src-${s.store_code}`]}
                        onChange={ev => saveClosingSource(s.id, s.store_code, ev.target.value)}
                        title="Who produces this store's daily closing — auto-saves immediately">
                        <option value="">
                          Company default ({srcCfg?.labels?.[srcCfg?.org_default || 'rep_entry']
                            || CLOSING_SRC_LABEL[srcCfg?.org_default || 'rep_entry']})
                        </option>
                        {(srcCfg?.sources || ['rep_entry', 'b2b_derived']).map(v =>
                          <option key={v} value={v}>{srcCfg?.labels?.[v] || CLOSING_SRC_LABEL[v] || v}</option>)}
                      </select>
                    </td>
                    <td style={cell}>
                      <input type="checkbox" checked={!!s.is_active} disabled={!!rowBusy[key]}
                        onChange={ev => toggleStoreActive(s, ev.target.checked)} title="Auto-saves immediately" />
                    </td>
                    <td style={cell}>
                      <button className="btn btn-primary" style={{ fontSize: 12, padding: '4px 10px' }} disabled={!!rowBusy[key]} onClick={() => saveStore(s)}>💾</button>
                      <button className="btn" style={{ fontSize: 12, padding: '4px 10px', marginLeft: 6 }}
                        title="Landlord, rent, ACH, insurance & documents (management only)"
                        aria-expanded={!!leaseOpen[key]}
                        onClick={() => setLeaseOpen(o => ({ ...o, [key]: !o[key] }))}>
                        🏢 Lease {leaseOpen[key] ? '▴' : '▾'}
                      </button>
                      {dirty && !rowMsg[key] && <span style={{ fontSize: 11, color: '#b45309', marginLeft: 6 }}>● unsaved</span>}
                      {rowMsg[key] && <span style={{ fontSize: 11, marginLeft: 6, color: rowMsg[key].startsWith('✗') ? '#b91c1c' : '#166534' }}>{rowMsg[key]}</span>}
                    </td>
                  </tr>
                  {leaseOpen[key] && (
                    <tr>
                      <td colSpan={8} style={{ padding: '0 8px 10px', borderBottom: '1px solid var(--border)' }}>
                        <LeasePanel storeCode={s.store_code} />
                      </td>
                    </tr>
                  )}
                  </React.Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
