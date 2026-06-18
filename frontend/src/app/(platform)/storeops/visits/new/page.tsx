'use client'
import { useState, useEffect, useMemo } from 'react'
import { useRouter } from 'next/navigation'
import { api, apiUpload, localToday } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'

const VACCESSORIZE_URL = 'https://www.vaccessorize.com'
const sel: React.CSSProperties = { padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 14, background: 'var(--surface)' }
const input: React.CSSProperties = { ...sel, width: '100%' }
const labelStyle: React.CSSProperties = { fontSize: 12, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', display: 'block', marginBottom: 4 }

// Category display order for the checklist.
const CATS: [string, string][] = [
  ['appearance', 'Appearance'], ['facilities', 'Facilities'], ['security', 'Security'],
  ['supplies', 'Supplies'], ['accessories', 'Accessories'], ['general', 'Other'],
]

type Resp = { checked: boolean; note: string; photo_path?: string; photo_url?: string }

export default function NewVisitPage() {
  const router = useRouter()
  const { user, permissions } = useAuth()

  const [stores, setStores] = useState<any[]>([])
  const [items, setItems] = useState<any[]>([])
  const [market, setMarket] = useState('')
  const [storeCode, setStoreCode] = useState('')
  const [visitDate, setVisitDate] = useState(localToday())
  const [schedReps, setSchedReps] = useState<string[]>([])

  const [gps, setGps] = useState<{ lat: number; lng: number; accuracy: number } | null>(null)
  const [gpsErr, setGpsErr] = useState('')
  const [busy, setBusy] = useState('')

  const [visit, setVisit] = useState<any>(null)         // set after check-in
  const [resp, setResp] = useState<Record<string, Resp>>({})
  const [accessories, setAccessories] = useState<any[]>([{ accessory_name: '', qty: 1, note: '' }])
  const [extraNotes, setExtraNotes] = useState('')
  const [actualRep, setActualRep] = useState('')
  const [discrepancy, setDiscrepancy] = useState('')
  const [cleanPhoto, setCleanPhoto] = useState<{ path: string; url: string } | null>(null)

  useEffect(() => {
    if (user?.market && permissions?.scope === 'market') setMarket(user.market)
  }, [user, permissions])

  useEffect(() => {
    Promise.all([
      api('/api/v1/storevisit/stores').catch(() => []),
      api('/api/v1/storevisit/checklist-items').catch(() => []),
    ]).then(([s, it]) => { setStores(s || []); setItems(it || []) }).catch(console.error)
  }, [])

  // Auto-load the scheduled rep when store + date are set.
  useEffect(() => {
    if (!storeCode || !visitDate) { setSchedReps([]); return }
    api(`/api/v1/storevisit/scheduled-rep?store_code=${encodeURIComponent(storeCode)}&date=${visitDate}`)
      .then(d => setSchedReps(d?.reps || [])).catch(() => setSchedReps([]))
  }, [storeCode, visitDate])

  const markets = useMemo(() => Array.from(new Set(stores.map(s => s.market).filter(Boolean))).sort(), [stores])
  const storeOpts = stores.filter(s => !market || s.market === market)
  const store = stores.find(s => s.store_code === storeCode)

  function captureGps(): Promise<{ lat: number; lng: number; accuracy: number } | null> {
    return new Promise(resolve => {
      if (typeof navigator === 'undefined' || !navigator.geolocation) { setGpsErr('Geolocation not supported on this device.'); resolve(null); return }
      navigator.geolocation.getCurrentPosition(
        pos => resolve({ lat: pos.coords.latitude, lng: pos.coords.longitude, accuracy: pos.coords.accuracy }),
        err => { setGpsErr(err.message || 'Location unavailable — you can still continue.'); resolve(null) },
        { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 },
      )
    })
  }

  async function checkIn() {
    if (!storeCode) { alert('Pick a store first.'); return }
    setBusy('checkin'); setGpsErr('')
    try {
      const g = await captureGps()
      setGps(g)
      const body = {
        store_code: storeCode, store_address: store?.address || '', market: store?.market || market || '',
        dm_email: user?.email || '', dm_name: user?.full_name || '',
        check_in_at: new Date().toISOString(),
        check_in_lat: g?.lat ?? null, check_in_lng: g?.lng ?? null, check_in_accuracy: g?.accuracy ?? null,
        scheduled_rep: schedReps.join(', '),
      }
      const v = await api('/api/v1/storevisit/visits', { method: 'POST', body: JSON.stringify(body) })
      setVisit(v)
      const seed: Record<string, Resp> = {}
      items.forEach(it => { seed[it.item_key] = { checked: false, note: '' } })
      setResp(seed)
      if (schedReps.length === 1) setActualRep(schedReps[0])
    } catch (e: any) { alert('Check-in failed: ' + (e?.message || e)) }
    finally { setBusy('') }
  }

  async function uploadItemPhoto(itemKey: string, file: File) {
    if (!visit) return
    const fd = new FormData(); fd.append('file', file); fd.append('kind', `item:${itemKey}`)
    try {
      const r = await apiUpload(`/api/v1/storevisit/visits/${visit.id}/photo`, fd)
      setResp(p => ({ ...p, [itemKey]: { ...(p[itemKey] || { checked: false, note: '' }), photo_path: r.path, photo_url: r.url } }))
    } catch (e: any) { alert('Photo upload failed: ' + (e?.message || e)) }
  }

  async function uploadCleanPhoto(file: File) {
    if (!visit) return
    setBusy('clean')
    const fd = new FormData(); fd.append('file', file); fd.append('kind', 'clean_store')
    try {
      const r = await apiUpload(`/api/v1/storevisit/visits/${visit.id}/photo`, fd)
      setCleanPhoto({ path: r.path, url: r.url })
    } catch (e: any) { alert('Photo upload failed: ' + (e?.message || e)) }
    finally { setBusy('') }
  }

  async function save(submit: boolean) {
    if (!visit) return
    setBusy(submit ? 'submit' : 'draft')
    try {
      const responses = items.map(it => ({
        item_key: it.item_key, label_snapshot: it.label, category_snapshot: it.category || 'general',
        checked: !!resp[it.item_key]?.checked, note: resp[it.item_key]?.note || null,
        photo_path: resp[it.item_key]?.photo_path || null,
      }))
      const body: any = {
        actual_rep: actualRep, rep_discrepancy_reason: discrepancy, extra_notes: extraNotes,
        responses, accessories: accessories.filter(a => (a.accessory_name || '').trim()),
      }
      if (submit) body.check_out_at = new Date().toISOString()
      await api(`/api/v1/storevisit/visits/${visit.id}`, { method: 'PATCH', body: JSON.stringify(body) })
      if (submit) {
        await api(`/api/v1/storevisit/visits/${visit.id}/submit`, { method: 'POST' })
        router.push(`/storeops/visits/${visit.id}`)
      } else {
        alert('Draft saved.')
      }
    } catch (e: any) { alert('Save failed: ' + (e?.message || e)) }
    finally { setBusy('') }
  }

  const mismatch = actualRep && schedReps.length > 0 && !schedReps.includes(actualRep)
  const grouped = CATS.map(([key, label]) => [label, items.filter(it => (it.category || 'general') === key)] as [string, any[]])
    .filter(([, list]) => list.length > 0)

  return (
    <div style={{ maxWidth: 860 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 4px' }}>📝 New Store Visit</h1>
      <p style={{ color: 'var(--text2)', fontSize: 14, margin: '0 0 18px' }}>
        Check in at the store, confirm the rep on duty, and run the inspection checklist.
      </p>

      {/* ── Check-in ──────────────────────────────────────────── */}
      <div className="card" style={{ padding: 18, marginBottom: 18 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 14 }}>
          <div>
            <label style={labelStyle}>Market</label>
            <select style={input} value={market} disabled={!!visit} onChange={e => { setMarket(e.target.value); setStoreCode('') }}>
              <option value="">All markets</option>
              {markets.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          <div>
            <label style={labelStyle}>Store *</label>
            <select style={input} value={storeCode} disabled={!!visit} onChange={e => setStoreCode(e.target.value)}>
              <option value="">Select a store…</option>
              {storeOpts.map(s => <option key={s.store_code} value={s.store_code}>{s.address || s.store_code}</option>)}
            </select>
          </div>
          <div>
            <label style={labelStyle}>Visit date</label>
            <input type="date" style={input} value={visitDate} disabled={!!visit} onChange={e => setVisitDate(e.target.value)} />
          </div>
        </div>

        <div style={{ marginTop: 14, display: 'flex', gap: 18, flexWrap: 'wrap', fontSize: 13, color: 'var(--text2)' }}>
          <div><strong>Scheduled rep:</strong> {schedReps.length ? schedReps.join(', ') : <span style={{ color: 'var(--text3)' }}>none on the schedule</span>}</div>
          {visit && <div><strong>Checked in:</strong> {new Date(visit.check_in_at).toLocaleString()}</div>}
          {gps && <div><strong>GPS:</strong> {gps.lat.toFixed(5)}, {gps.lng.toFixed(5)} (±{Math.round(gps.accuracy)}m) · <a href={`https://maps.google.com/?q=${gps.lat},${gps.lng}`} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>map</a></div>}
        </div>
        {gpsErr && <div style={{ marginTop: 8, fontSize: 12, color: 'var(--amber)' }}>📍 {gpsErr}</div>}

        {!visit && (
          <button className="btn btn-primary" style={{ marginTop: 14 }} disabled={busy === 'checkin' || !storeCode} onClick={checkIn}>
            {busy === 'checkin' ? '📍 Getting location…' : '📍 Check in'}
          </button>
        )}
      </div>

      {visit && (
        <>
          {/* ── Rep on duty ─────────────────────────────────────── */}
          <div className="card" style={{ padding: 18, marginBottom: 18 }}>
            <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 12px' }}>Sales rep on duty</h2>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 14 }}>
              <div>
                <label style={labelStyle}>Actual rep present</label>
                <input style={input} list="sched-reps" value={actualRep} onChange={e => setActualRep(e.target.value)} placeholder="Who was actually working?" />
                <datalist id="sched-reps">{schedReps.map(r => <option key={r} value={r} />)}</datalist>
              </div>
              {mismatch && (
                <div>
                  <label style={labelStyle}>Reason for discrepancy</label>
                  <input style={input} value={discrepancy} onChange={e => setDiscrepancy(e.target.value)} placeholder="Why is the scheduled rep not here?" />
                </div>
              )}
            </div>
            {mismatch && <div style={{ marginTop: 8, fontSize: 12, color: 'var(--amber)' }}>⚠️ Actual rep differs from the schedule ({schedReps.join(', ') || 'none'}).</div>}
          </div>

          {/* ── Checklist ───────────────────────────────────────── */}
          <div className="card" style={{ padding: 18, marginBottom: 18 }}>
            <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 12px' }}>Inspection checklist</h2>
            {grouped.map(([catLabel, list]) => (
              <div key={catLabel} style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 6 }}>{catLabel}</div>
                {list.map(it => (
                  <div key={it.item_key} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', borderBottom: '1px solid var(--border)', flexWrap: 'wrap' }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, flex: '1 1 240px', cursor: 'pointer' }}>
                      <input type="checkbox" checked={!!resp[it.item_key]?.checked}
                        onChange={e => setResp(p => ({ ...p, [it.item_key]: { ...(p[it.item_key] || { checked: false, note: '' }), checked: e.target.checked } }))} />
                      <span style={{ fontSize: 14 }}>{it.label}</span>
                    </label>
                    <input style={{ ...sel, flex: '2 1 200px', fontSize: 13 }} placeholder="Note (optional)"
                      value={resp[it.item_key]?.note || ''}
                      onChange={e => setResp(p => ({ ...p, [it.item_key]: { ...(p[it.item_key] || { checked: false, note: '' }), note: e.target.value } }))} />
                    <label className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 8px', cursor: 'pointer' }}>
                      {resp[it.item_key]?.photo_url ? '📷 ✓' : '📷'}
                      <input type="file" accept="image/*" capture="environment" style={{ display: 'none' }}
                        onChange={e => { const f = e.target.files?.[0]; if (f) uploadItemPhoto(it.item_key, f) }} />
                    </label>
                  </div>
                ))}
              </div>
            ))}
          </div>

          {/* ── Accessories to order ────────────────────────────── */}
          <div className="card" style={{ padding: 18, marginBottom: 18 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
              <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>Accessories to order</h2>
              <a href={VACCESSORIZE_URL} target="_blank" rel="noreferrer" className="btn btn-secondary" style={{ fontSize: 13 }}>🛒 Order on vAccessorize.com ↗</a>
            </div>
            {accessories.map((a, i) => (
              <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
                <input style={{ ...sel, flex: '3 1 220px' }} placeholder="Accessory needed" value={a.accessory_name}
                  onChange={e => setAccessories(list => list.map((x, j) => j === i ? { ...x, accessory_name: e.target.value } : x))} />
                <input type="number" min={1} style={{ ...sel, width: 80 }} value={a.qty}
                  onChange={e => setAccessories(list => list.map((x, j) => j === i ? { ...x, qty: Number(e.target.value) || 1 } : x))} />
                <input style={{ ...sel, flex: '2 1 160px' }} placeholder="Note" value={a.note || ''}
                  onChange={e => setAccessories(list => list.map((x, j) => j === i ? { ...x, note: e.target.value } : x))} />
                <button className="btn btn-secondary" style={{ fontSize: 13 }} onClick={() => setAccessories(list => list.filter((_, j) => j !== i))}>✕</button>
              </div>
            ))}
            <button className="btn btn-secondary" style={{ fontSize: 13 }} onClick={() => setAccessories(list => [...list, { accessory_name: '', qty: 1, note: '' }])}>＋ Add accessory</button>
          </div>

          {/* ── Extra items + clean-store photo ─────────────────── */}
          <div className="card" style={{ padding: 18, marginBottom: 18 }}>
            <label style={labelStyle}>Any other items to note</label>
            <textarea style={{ ...input, minHeight: 70, fontFamily: 'inherit' }} value={extraNotes} onChange={e => setExtraNotes(e.target.value)} placeholder="Add anything else observed during the visit…" />
            <div style={{ marginTop: 16 }}>
              <label style={labelStyle}>Clean-store photo</label>
              {cleanPhoto ? (
                <div><img src={cleanPhoto.url} alt="clean store" style={{ maxWidth: 280, borderRadius: 8, border: '1px solid var(--border)' }} /></div>
              ) : (
                <label className="btn btn-secondary" style={{ fontSize: 13, cursor: 'pointer' }}>
                  {busy === 'clean' ? '⏳ Uploading…' : '📷 Take / upload photo'}
                  <input type="file" accept="image/*" capture="environment" style={{ display: 'none' }}
                    onChange={e => { const f = e.target.files?.[0]; if (f) uploadCleanPhoto(f) }} />
                </label>
              )}
            </div>
          </div>

          {/* ── Actions ─────────────────────────────────────────── */}
          <div style={{ display: 'flex', gap: 10, marginBottom: 40, flexWrap: 'wrap' }}>
            <button className="btn btn-secondary" disabled={!!busy} onClick={() => save(false)}>{busy === 'draft' ? '⏳ Saving…' : '💾 Save draft'}</button>
            <button className="btn btn-primary" disabled={!!busy} onClick={() => save(true)}>{busy === 'submit' ? '⏳ Submitting…' : '✅ Check out & submit'}</button>
          </div>
        </>
      )}
    </div>
  )
}
