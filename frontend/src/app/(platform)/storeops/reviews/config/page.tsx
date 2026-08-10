'use client'
import { useCallback, useEffect, useState } from 'react'
import { api } from '@/lib/client'
import EntityPicker from '@/components/EntityPicker'

// Google Reviews — admin config (Phase 1, owner directive 2026-07-27). API key is WRITE-ONLY (the
// backend masks it on every read — GET .../config never returns the raw key, only has_api_key + a
// trailing-4-char hint), per-store place_id/target overrides, and the sweep schedule. Write
// endpoints are gated server-side by _require_google_reviews_admin (falls back to the manager gate
// until core registers the 'google_reviews' SETTING_AREA — see the people handoff NEEDS CORE item);
// this page reads `can_edit` from the config response and disables the Save controls when false
// rather than letting a 403 be the user's only signal.

const inp: React.CSSProperties = { padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13.5, background: 'var(--surface)' }
const label: React.CSSProperties = { fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 4, display: 'block' }
const FREQ_OPTIONS = [{ id: 'daily', label: 'Daily' }, { id: 'weekly', label: 'Weekly' }]
const DOW_OPTIONS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d, i) => ({ id: String(i), label: d }))

interface Cfg {
  enabled: boolean; target_default: number; notify_on_new_reviews: boolean; lookback_days: number
  has_api_key: boolean; api_key_hint: string | null; can_edit: boolean; updated_at?: string
}
interface SweepCfg {
  enabled: boolean; frequency: string; day_of_week: number; hour: number; timezone: string
  next_run_at?: string; last_run_at?: string; last_attempt_at?: string; last_status?: string; last_detail?: string
}
interface StoreRow {
  store_code: string; address?: string; market?: string; is_active?: boolean
  place_id?: string | null; place_id_source?: string | null; resolved_address?: string | null
  target_override?: number | null; target: number; rating?: number | null; review_count?: number | null
  status: string
}

export default function GoogleReviewsConfigPage() {
  const [cfg, setCfg] = useState<Cfg | null>(null)
  const [sweep, setSweep] = useState<SweepCfg | null>(null)
  const [stores, setStores] = useState<StoreRow[]>([])
  const [apiKey, setApiKey] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [overrideEdit, setOverrideEdit] = useState<Record<string, string>>({})
  const [placeEdit, setPlaceEdit] = useState<Record<string, string>>({})

  const load = useCallback(() => {
    api('/api/v1/storeops/google-reviews/config').then(setCfg).catch((e: any) => setMsg('❌ ' + (e?.message || e)))
    api('/api/v1/storeops/google-reviews/sweep-config').then(setSweep).catch(() => {})
    api('/api/v1/storeops/google-reviews/stores').then((r: any) => setStores(r?.stores || [])).catch(() => {})
  }, [])
  useEffect(() => { load() }, [load])

  const saveConfig = useCallback(() => {
    if (!cfg) return
    setBusy(true); setMsg('')
    const body: any = { enabled: cfg.enabled, target_default: cfg.target_default,
      notify_on_new_reviews: cfg.notify_on_new_reviews, lookback_days: cfg.lookback_days }
    if (apiKey.trim()) body.api_key = apiKey.trim()
    api('/api/v1/storeops/google-reviews/config', { method: 'PUT', body: JSON.stringify(body) })
      .then((r: any) => { setCfg(r); setApiKey(''); setMsg('✅ Saved') })
      .catch((e: any) => setMsg('❌ ' + (e?.message || e))).finally(() => setBusy(false))
  }, [cfg, apiKey])

  const saveSweep = useCallback(() => {
    if (!sweep) return
    setBusy(true); setMsg('')
    api('/api/v1/storeops/google-reviews/sweep-config', {
      method: 'PUT', body: JSON.stringify({ enabled: sweep.enabled, frequency: sweep.frequency,
        day_of_week: sweep.day_of_week, hour: sweep.hour, timezone: sweep.timezone }),
    }).then(setSweep).catch((e: any) => setMsg('❌ ' + (e?.message || e))).finally(() => setBusy(false))
  }, [sweep])

  const runNow = useCallback(() => {
    setBusy(true); setMsg('')
    api('/api/v1/storeops/google-reviews/sweep/run-now', { method: 'POST', body: JSON.stringify({}) })
      .then(() => { setMsg('✅ Refresh started — this can take a minute; reload to see the latest ratings.') })
      .catch((e: any) => setMsg('❌ ' + (e?.message || e))).finally(() => setBusy(false))
  }, [])

  const resolvePlace = useCallback((storeCode: string) => {
    setBusy(true); setMsg('')
    api('/api/v1/storeops/google-reviews/resolve-place', { method: 'POST', body: JSON.stringify({ store_code: storeCode }) })
      .then(() => load()).catch((e: any) => setMsg('❌ ' + (e?.message || e))).finally(() => setBusy(false))
  }, [load])

  // Save one store's Place ID / target override. Every exit path MUST say something (2026-08-10):
  // this used to `return` silently when it decided the body was empty, and on success it left the
  // typed string sitting in local state — so a Save that did nothing and a Save that worked looked
  // IDENTICAL, and a Place ID that never persisted still showed in the box until the next reload.
  // Now: nothing-to-save says so by name, and success clears the local edit so the box re-renders
  // from what the server actually stored — a blank box after saving then means the write did not
  // land, which is information rather than a mystery.
  const saveStoreOverride = useCallback((storeCode: string) => {
    const body: any = {}
    if (storeCode in overrideEdit) {
      const v = overrideEdit[storeCode]
      if (v === '') body.clear_target_override = true
      else body.target_override = Number(v)
    }
    if (storeCode in placeEdit && placeEdit[storeCode].trim()) body.place_id = placeEdit[storeCode].trim()
    if (Object.keys(body).length === 0) {
      setMsg(`⚠️ Nothing to save for ${storeCode} — type a Place ID or a target first, then Save.`)
      return
    }
    setBusy(true); setMsg('')
    api(`/api/v1/storeops/google-reviews/store-config/${encodeURIComponent(storeCode)}`, { method: 'PUT', body: JSON.stringify(body) })
      .then((r: any) => {
        // Drop this store's local edits so the inputs fall back to the PERSISTED values below.
        setPlaceEdit(prev => { const n = { ...prev }; delete n[storeCode]; return n })
        setOverrideEdit(prev => { const n = { ...prev }; delete n[storeCode]; return n })
        setMsg(r?.place_id
          ? `✅ ${storeCode} — Place ID saved (${r.place_id_source || 'manual'}): ${r.place_id}`
          : `✅ ${storeCode} — saved.`)
        load()
      })
      .catch((e: any) => setMsg('❌ ' + (e?.message || e))).finally(() => setBusy(false))
  }, [overrideEdit, placeEdit, load])

  if (!cfg) return <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>

  const readOnly = !cfg.can_edit

  return (
    <div>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 4px' }}>⭐ Google Reviews — Settings</h1>
      <p style={{ color: 'var(--text2)', fontSize: 13.5, margin: '0 0 16px' }}>
        Pulls each store's Google rating from its address via Google Places. Google's API returns only a
        curated subset of reviews (typically ~5) — not the full history.
      </p>
      {readOnly && <div style={{ ...inp, background: '#fff7e6', marginBottom: 12 }}>You can view this page but don't have permission to save changes here — ask an admin.</div>}
      {msg && <div style={{ marginBottom: 12, fontSize: 13.5 }}>{msg}</div>}

      <div className="card" style={{ marginBottom: 16 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 12 }}>Google Places API</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12 }}>
          <div>
            <label style={label}>API key {cfg.has_api_key ? `(set — ${cfg.api_key_hint})` : '(not set)'}</label>
            <input style={{ ...inp, width: '100%' }} type="password" placeholder={cfg.has_api_key ? 'Leave blank to keep the current key' : 'Google Places API key'}
              value={apiKey} onChange={e => setApiKey(e.target.value)} disabled={readOnly} />
          </div>
          <div>
            <label style={label}>Default rating target (all stores)</label>
            <input style={{ ...inp, width: '100%' }} type="number" step="0.1" min={1} max={5} value={cfg.target_default}
              onChange={e => setCfg({ ...cfg, target_default: Number(e.target.value) })} disabled={readOnly} />
          </div>
          <div>
            <label style={label}>Enabled</label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13.5 }}>
              <input type="checkbox" checked={cfg.enabled} onChange={e => setCfg({ ...cfg, enabled: e.target.checked })} disabled={readOnly} />
              Pull ratings + reviews for this tenant
            </label>
          </div>
          <div>
            <label style={label}>Notify employees</label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13.5 }}>
              <input type="checkbox" checked={cfg.notify_on_new_reviews} onChange={e => setCfg({ ...cfg, notify_on_new_reviews: e.target.checked })} disabled={readOnly} />
              Email on new/relevant reviews
            </label>
          </div>
          <div>
            <label style={label}>Lookback window (days)</label>
            <input style={{ ...inp, width: '100%' }} type="number" step="1" min={1} max={365} value={cfg.lookback_days}
              onChange={e => setCfg({ ...cfg, lookback_days: Number(e.target.value) })} disabled={readOnly} />
            <div style={{ fontSize: 10.5, color: 'var(--text3)', marginTop: 3 }}>
              How far back to look for a PAST shift when deciding which store(s)' ratings show for an
              employee (an employee's rating card, action plans, and any performance table). Forward
              window stays 14 days.
            </div>
          </div>
        </div>
        <button className="btn btn-primary" style={{ marginTop: 12 }} disabled={busy || readOnly} onClick={saveConfig}>Save</button>
      </div>

      {sweep && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 12 }}>Auto-refresh schedule</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12, alignItems: 'end' }}>
            <div>
              <label style={label}>Frequency</label>
              <EntityPicker options={FREQ_OPTIONS} value={sweep.frequency} clearable={false}
                onChange={v => setSweep({ ...sweep, frequency: v || 'daily' })} disabled={readOnly} width="100%" />
            </div>
            {sweep.frequency === 'weekly' && (
              <div>
                <label style={label}>Day of week</label>
                <EntityPicker options={DOW_OPTIONS} value={String(sweep.day_of_week)} clearable={false}
                  onChange={v => setSweep({ ...sweep, day_of_week: Number(v || 0) })} disabled={readOnly} width="100%" />
              </div>
            )}
            <div>
              <label style={label}>Hour ({sweep.timezone})</label>
              <input style={{ ...inp, width: '100%' }} type="number" min={0} max={23} value={sweep.hour}
                onChange={e => setSweep({ ...sweep, hour: Number(e.target.value) })} disabled={readOnly} />
            </div>
            <div>
              <label style={label}>Enabled</label>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13.5 }}>
                <input type="checkbox" checked={sweep.enabled} onChange={e => setSweep({ ...sweep, enabled: e.target.checked })} disabled={readOnly} />
                Run automatically
              </label>
            </div>
          </div>
          <div style={{ marginTop: 12, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <button className="btn btn-primary" disabled={busy || readOnly} onClick={saveSweep}>Save schedule</button>
            <button className="btn btn-secondary" disabled={busy || !cfg.has_api_key} onClick={runNow}>🔄 Refresh now</button>
            <span style={{ fontSize: 12, color: 'var(--text2)' }}>
              {sweep.last_status ? `Last run: ${sweep.last_status}${sweep.last_detail ? ` — ${sweep.last_detail}` : ''}` : 'Never run yet'}
            </span>
          </div>
        </div>
      )}

      <div className="card">
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 12 }}>Per-store place resolution + target override</div>
        <div className="table-wrapper">
          <table className="table" style={{ fontSize: 12.5 }}>
            <thead><tr>
              <th>Store</th><th>Address</th><th>Rating</th><th>Target</th><th>Place ID</th><th>Actions</th>
            </tr></thead>
            <tbody>
              {stores.map(s => (
                <tr key={s.store_code}>
                  <td>{s.store_code}{s.market ? <div style={{ fontSize: 10.5, color: 'var(--text3)' }}>{s.market}</div> : null}</td>
                  <td style={{ maxWidth: 220 }}>{s.address || '—'}</td>
                  <td>{s.rating != null ? Number(s.rating).toFixed(1) : '—'} {s.review_count != null ? `(${s.review_count})` : ''}</td>
                  <td>
                    <input style={{ ...inp, width: 70 }} type="number" step="0.1" min={1} max={5}
                      placeholder={String(cfg.target_default)}
                      value={overrideEdit[s.store_code] ?? (s.target_override != null ? String(s.target_override) : '')}
                      onChange={e => setOverrideEdit(prev => ({ ...prev, [s.store_code]: e.target.value }))}
                      onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); saveStoreOverride(s.store_code) } }}
                      disabled={readOnly} />
                  </td>
                  <td>
                    <input style={{ ...inp, width: 150 }} placeholder="auto-resolve or paste"
                      value={placeEdit[s.store_code] ?? (s.place_id || '')}
                      onChange={e => setPlaceEdit(prev => ({ ...prev, [s.store_code]: e.target.value }))}
                      onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); saveStoreOverride(s.store_code) } }}
                      disabled={readOnly} />
                    {s.place_id_source && <div style={{ fontSize: 10, color: 'var(--text3)' }}>{s.place_id_source}</div>}
                  </td>
                  <td style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    <button className="btn btn-secondary" style={{ fontSize: 11.5, padding: '4px 8px' }}
                      disabled={busy || readOnly || !cfg.has_api_key} onClick={() => resolvePlace(s.store_code)}>Auto-resolve</button>
                    <button className="btn btn-secondary" style={{ fontSize: 11.5, padding: '4px 8px' }}
                      disabled={busy || readOnly} onClick={() => saveStoreOverride(s.store_code)}>Save</button>
                  </td>
                </tr>
              ))}
              {stores.length === 0 && <tr><td colSpan={6} style={{ textAlign: 'center', color: 'var(--text3)', padding: 20 }}>No stores found.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
