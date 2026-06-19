'use client'
// Phase 2 of the DM store-visit: roll up the store's auto-generated DLAR/sales action items
// (the existing /commcalc/targets/{period}/action-plan engine), let the DM check off the ones
// discussed + comment + attach proof, agree a rep action plan with due dates, and capture the
// rep + DM sign-off plus the printed-and-signed checklist scan.
import { useState, useEffect, useCallback } from 'react'
import { api, apiUpload, localToday } from '@/lib/client'

const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const SEV: Record<string, { bg: string; fg: string; label: string }> = {
  critical: { bg: '#fde7e7', fg: '#b42318', label: 'Critical' },
  warning: { bg: '#fef3e2', fg: '#b45309', label: 'Warning' },
  good: { bg: '#e6f7ec', fg: '#16794a', label: 'Good' },
}

type Item = { item_key: string; rep: string | null; severity: string; metric: string; title: string; detail: string }
type Ov = { discussed: boolean; comment: string; proof_photo_path?: string; proof_photo_url?: string }
type PlanRow = { rep: string; description: string; due_date: string; status: string }

const keyOf = (rep: string | null, metric: string, title: string) => `${rep || '__store__'}::${metric}::${title}`

export default function VisitActions({ visitId, storeCode, period, dmName }:
  { visitId: string; storeCode: string; period: string; dmName?: string }) {
  const [items, setItems] = useState<Item[]>([])
  const [ov, setOv] = useState<Record<string, Ov>>({})
  const [plan, setPlan] = useState<PlanRow[]>([])
  const [signoff, setSignoff] = useState<any>({})
  const [signedUrl, setSignedUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [repName, setRepName] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [ap, saved] = await Promise.all([
        api(`/api/v1/commcalc/targets/${period}/action-plan?store_code=${encodeURIComponent(storeCode)}`).catch(() => null),
        api(`/api/v1/storevisit/visits/${visitId}/action`).catch(() => null),
      ])
      // Flatten store-level + per-rep action items into one keyed list.
      const store = ap?.stores?.[0]
      const flat: Item[] = []
      ;(store?.items || []).forEach((it: any) => flat.push({ item_key: keyOf(null, it.metric, it.title), rep: null, severity: it.severity, metric: it.metric, title: it.title, detail: it.detail }))
      ;(store?.reps || []).forEach((rp: any) => (rp.items || []).forEach((it: any) =>
        flat.push({ item_key: keyOf(rp.rep, it.metric, it.title), rep: rp.rep, severity: it.severity, metric: it.metric, title: it.title, detail: it.detail })))
      // Merge any saved overlay; keep saved-only items (in case the engine no longer surfaces them).
      const ovMap: Record<string, Ov> = {}
      ;(saved?.items || []).forEach((s: any) => {
        ovMap[s.item_key] = { discussed: !!s.discussed, comment: s.comment || '', proof_photo_path: s.proof_photo_path, proof_photo_url: s.proof_photo_url }
        if (!flat.find(f => f.item_key === s.item_key))
          flat.push({ item_key: s.item_key, rep: s.rep, severity: s.severity, metric: s.metric, title: s.title, detail: s.detail })
      })
      setItems(flat)
      setOv(ovMap)
      setPlan((saved?.plan || []).map((p: any) => ({ rep: p.rep || '', description: p.description || '', due_date: p.due_date || '', status: p.status || 'open' })))
      setSignoff(saved?.signoff || {})
      setSignedUrl(saved?.signed_checklist_url || null)

      // Auto-attach the rolled-up action plan to the visit the first time it's opened (no overlay
      // saved yet) so the items are persisted on the store's visit and the DM can focus on them
      // without having to save first. Fire-and-forget; once saved, later opens won't re-seed.
      if ((saved?.items?.length || 0) === 0 && flat.length > 0) {
        const seed = { items: flat.map(it => ({ ...it, discussed: false, comment: '' })) }
        api(`/api/v1/storevisit/visits/${visitId}/action-items`, { method: 'PUT', body: JSON.stringify(seed) }).catch(() => {})
      }
    } finally { setLoading(false) }
  }, [period, storeCode, visitId])

  useEffect(() => { load() }, [load])

  function setOvFor(k: string, patch: Partial<Ov>) {
    setOv(p => {
      const cur: Ov = p[k] || { discussed: false, comment: '' }
      return { ...p, [k]: { ...cur, ...patch } }
    })
  }

  async function uploadProof(k: string, file: File) {
    const fd = new FormData(); fd.append('file', file); fd.append('kind', 'proof')
    try {
      const r = await apiUpload(`/api/v1/storevisit/visits/${visitId}/photo`, fd)
      setOvFor(k, { proof_photo_path: r.path, proof_photo_url: r.url })
    } catch (e: any) { alert('Proof upload failed: ' + (e?.message || e)) }
  }

  async function saveItems() {
    setBusy('items')
    try {
      const payload = { items: items.map(it => ({ ...it, ...(ov[it.item_key] || { discussed: false, comment: '' }) })) }
      await api(`/api/v1/storevisit/visits/${visitId}/action-items`, { method: 'PUT', body: JSON.stringify(payload) })
      alert('Discussion saved.')
    } catch (e: any) { alert('Save failed: ' + (e?.message || e)) }
    finally { setBusy('') }
  }

  async function savePlan() {
    setBusy('plan')
    try {
      const rows = plan.filter(p => p.description.trim()).map(p => ({ ...p, store_code: storeCode, due_date: p.due_date || null }))
      await api(`/api/v1/storevisit/visits/${visitId}/action-plan`, { method: 'PUT', body: JSON.stringify({ plan: rows }) })
      alert('Action plan saved.')
    } catch (e: any) { alert('Save failed: ' + (e?.message || e)) }
    finally { setBusy('') }
  }

  function seedPlan() {
    const seeds = items.filter(it => it.severity === 'critical' || it.severity === 'warning')
      .map(it => ({ rep: it.rep || '', description: it.title, due_date: '', status: 'open' }))
    setPlan(p => [...p, ...seeds.filter(s => !p.find(x => x.description === s.description && x.rep === s.rep))])
  }

  async function doSignoff(who: 'rep' | 'dm') {
    const name = who === 'dm' ? (dmName || prompt('DM name?') || '') : (repName || prompt('Rep name?') || '')
    if (!name) return
    setBusy('sign')
    try {
      const r = await api(`/api/v1/storevisit/visits/${visitId}/signoff`, { method: 'POST', body: JSON.stringify({ who, name, signed: true }) })
      setSignoff(r.signoff || {})
    } catch (e: any) { alert('Sign-off failed: ' + (e?.message || e)) }
    finally { setBusy('') }
  }

  async function uploadSigned(file: File) {
    setBusy('signed')
    const fd = new FormData(); fd.append('file', file); fd.append('kind', 'signed_checklist')
    try {
      const r = await apiUpload(`/api/v1/storevisit/visits/${visitId}/photo`, fd)
      setSignedUrl(r.url)
    } catch (e: any) { alert('Upload failed: ' + (e?.message || e)) }
    finally { setBusy('') }
  }

  const byRep: [string, Item[]][] = []
  const storeItems = items.filter(i => !i.rep)
  if (storeItems.length) byRep.push(['Store-level', storeItems])
  Array.from(new Set(items.filter(i => i.rep).map(i => i.rep as string))).forEach(rep =>
    byRep.push([rep, items.filter(i => i.rep === rep)]))

  if (loading) return <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}><div className="spinner" style={{ margin: '0 auto' }} /></div>

  return (
    <>
      {/* ── Action items rolled up from DLAR / sales performance ── */}
      <div className="card" style={{ padding: 18, marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10, flexWrap: 'wrap', gap: 8 }}>
          <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>Action items to discuss <span style={{ fontSize: 12, fontWeight: 400, color: 'var(--text3)' }}>· {period}</span></h2>
          {items.length > 0 && <button className="btn btn-primary" style={{ fontSize: 13 }} disabled={!!busy} onClick={saveItems}>{busy === 'items' ? '⏳' : '💾'} Save discussion</button>}
        </div>
        {items.length === 0 ? (
          <div style={{ color: 'var(--text3)', fontSize: 13 }}>No action items for this store in {period}. (They come from the Daily Targets / DLAR action-plan engine — load DLAR + targets for the period to populate.)</div>
        ) : byRep.map(([label, list]) => (
          <div key={label} style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 6 }}>{label}</div>
            {list.map(it => {
              const o = ov[it.item_key] || { discussed: false, comment: '' }
              const sv = SEV[it.severity] || SEV.warning
              return (
                <div key={it.item_key} style={{ padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
                  <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                    <label style={{ display: 'flex', gap: 8, alignItems: 'center', cursor: 'pointer', flex: '1 1 320px' }}>
                      <input type="checkbox" checked={!!o.discussed} onChange={e => setOvFor(it.item_key, { discussed: e.target.checked })} />
                      <span>
                        <span style={{ fontSize: 10, fontWeight: 700, padding: '1px 6px', borderRadius: 8, background: sv.bg, color: sv.fg, marginRight: 6 }}>{sv.label}</span>
                        <span style={{ fontSize: 14, fontWeight: 500 }}>{it.title}</span>
                        <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>{it.detail}</div>
                      </span>
                    </label>
                    <label className="btn btn-secondary" style={{ fontSize: 12, padding: '4px 8px', cursor: 'pointer' }}>
                      {o.proof_photo_url ? '📷 ✓' : '📷 Proof'}
                      <input type="file" accept="image/*" capture="environment" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) uploadProof(it.item_key, f) }} />
                    </label>
                  </div>
                  <input style={{ ...sel, width: '100%', marginTop: 6 }} placeholder="Comment on what was discussed…" value={o.comment} onChange={e => setOvFor(it.item_key, { comment: e.target.value })} />
                </div>
              )
            })}
          </div>
        ))}
      </div>

      {/* ── Agreed rep action plan ──────────────────────────────── */}
      <div className="card" style={{ padding: 18, marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10, flexWrap: 'wrap', gap: 8 }}>
          <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>Action plan for the rep</h2>
          <div style={{ display: 'flex', gap: 6 }}>
            {items.length > 0 && <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={seedPlan}>＋ From flagged items</button>}
            <button className="btn btn-primary" style={{ fontSize: 13 }} disabled={!!busy} onClick={savePlan}>{busy === 'plan' ? '⏳' : '💾'} Save plan</button>
          </div>
        </div>
        {plan.map((p, i) => (
          <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
            <input style={{ ...sel, flex: '1 1 120px' }} placeholder="Rep" value={p.rep} onChange={e => setPlan(l => l.map((x, j) => j === i ? { ...x, rep: e.target.value } : x))} />
            <input style={{ ...sel, flex: '3 1 240px' }} placeholder="What the rep will do" value={p.description} onChange={e => setPlan(l => l.map((x, j) => j === i ? { ...x, description: e.target.value } : x))} />
            <input type="date" style={sel} value={p.due_date} onChange={e => setPlan(l => l.map((x, j) => j === i ? { ...x, due_date: e.target.value } : x))} />
            <select style={sel} value={p.status} onChange={e => setPlan(l => l.map((x, j) => j === i ? { ...x, status: e.target.value } : x))}>
              <option value="open">Open</option><option value="done">Done</option>
            </select>
            <button className="btn btn-secondary" style={{ fontSize: 13 }} onClick={() => setPlan(l => l.filter((_, j) => j !== i))}>✕</button>
          </div>
        ))}
        <button className="btn btn-secondary" style={{ fontSize: 13 }} onClick={() => setPlan(l => [...l, { rep: '', description: '', due_date: localToday(), status: 'open' }])}>＋ Add plan item</button>
      </div>

      {/* ── Sign-off ────────────────────────────────────────────── */}
      <div className="card" style={{ padding: 18, marginBottom: 40 }}>
        <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 12px' }}>Sign-off</h2>
        <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', marginBottom: 14 }}>
          <div style={{ flex: '1 1 220px' }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 6 }}>Sales rep</div>
            {signoff.plan_rep_signed
              ? <div style={{ fontSize: 13, color: 'var(--green, #16794a)' }}>✅ {signoff.plan_rep_signed_by} · {new Date(signoff.plan_rep_signed_at).toLocaleString()}</div>
              : <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  <input style={{ ...sel, flex: '1 1 120px' }} placeholder="Rep name" value={repName} onChange={e => setRepName(e.target.value)} />
                  <button className="btn btn-secondary" style={{ fontSize: 13 }} disabled={!!busy} onClick={() => doSignoff('rep')}>✍️ Rep sign off</button>
                </div>}
          </div>
          <div style={{ flex: '1 1 220px' }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 6 }}>District manager</div>
            {signoff.plan_dm_signed
              ? <div style={{ fontSize: 13, color: 'var(--green, #16794a)' }}>✅ {signoff.plan_dm_signed_by} · {new Date(signoff.plan_dm_signed_at).toLocaleString()}</div>
              : <button className="btn btn-secondary" style={{ fontSize: 13 }} disabled={!!busy} onClick={() => doSignoff('dm')}>✍️ DM sign off{dmName ? ` (${dmName})` : ''}</button>}
          </div>
        </div>
        <div style={{ borderTop: '1px solid var(--border)', paddingTop: 12 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 6 }}>Printed & signed checklist</div>
          <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>Use the Excel/PDF/Print buttons above to print this visit, get it physically signed, then upload the scan.</div>
          {signedUrl
            ? <a href={signedUrl} target="_blank" rel="noreferrer" className="btn btn-secondary" style={{ fontSize: 13 }}>📎 View signed checklist</a>
            : <label className="btn btn-secondary" style={{ fontSize: 13, cursor: 'pointer' }}>
                {busy === 'signed' ? '⏳ Uploading…' : '📎 Upload signed checklist'}
                <input type="file" accept="image/*,application/pdf" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) uploadSigned(f) }} />
              </label>}
        </div>
      </div>
    </>
  )
}
