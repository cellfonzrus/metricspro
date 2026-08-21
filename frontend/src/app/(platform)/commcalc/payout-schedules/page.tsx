'use client'
import { useState, useEffect } from 'react'
import { api, apiUpload, fmt } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import EntityPicker from '@/components/EntityPicker'
import RunCommissionButton from '../_lib/RunCommissionButton'

// Multi-month payout schedules (migration 057). A schedule spreads one activation's commission over
// N months (flat or %MRC); months 2..N pay only if the bill was paid + residual received that month.
// "Preview" runs the engine READ-ONLY against raw_mi — it does NOT change live payouts (wiring into
// the calc is a deliberate later step once a schedule is validated here).

type Line = { month_index: number; payout_kind: string; flat_amount: any; mrc_pct: any; mrc_basis: string; requires_paid: boolean }
type Sched = { id?: string; carrier_id?: string | null; activation_type: string; num_months: number; gate_signal: string; bypass_tier: boolean; is_active: boolean; lines?: Line[] }

const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const GATES = [
  { v: 'paid_residual', l: 'Bill paid + residual received (recommended)' },
  { v: 'active_status', l: 'Subscriber Active that month' },
  { v: 'nonzero_residual', l: 'Non-zero residual that month' },
]
const blankLine = (i: number): Line => ({ month_index: i, payout_kind: i === 1 ? 'flat' : 'pct_mrc', flat_amount: '', mrc_pct: '', mrc_basis: 'commissionable_mrc', requires_paid: i > 1 })
const blankSched = (): Sched => ({ carrier_id: '', activation_type: '*', num_months: 3, gate_signal: 'paid_residual', bypass_tier: true, is_active: true, lines: [blankLine(1), blankLine(2), blankLine(3)] })
const blankMrc = () => ({ plan_pattern: '', match_op: 'equals', mrc: '', carrier_id: '', priority: 100, is_active: true })

export default function PayoutSchedulesPage() {
  const [carriers, setCarriers] = useState<any[]>([])
  const [scheds, setScheds] = useState<Sched[]>([])
  const [ready, setReady] = useState(true)
  const [draft, setDraft] = useState<Sched>(blankSched())
  const [msg, setMsg] = useState('')
  const [period, setPeriod] = useState('')
  const [preview, setPreview] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [mrcItems, setMrcItems] = useState<any[]>([])
  const [mrcReady, setMrcReady] = useState(true)
  const [mrcDraft, setMrcDraft] = useState<any>(blankMrc())
  const [coverage, setCoverage] = useState<any>(null)
  const [covPeriod, setCovPeriod] = useState('')
  const [imp, setImp] = useState<any>(null)       // price-sheet import: {file, headers, plan_col, mrc_col, rows, total, carrier_id}
  const [impBusy, setImpBusy] = useState(false)
  const [sources, setSources] = useState<any[]>([])   // shareable carrier templates (clone sources)
  const [tplSrc, setTplSrc] = useState('')            // selected `${source_org_id}|${source_carrier_id}`
  const [tplPreview, setTplPreview] = useState<any>(null)
  const [tplBusy, setTplBusy] = useState(false)
  const [tplMsg, setTplMsg] = useState('')

  async function load() {
    try {
      // Independent reads → one round trip. carriers is a cache-served reference list (LOOKUP).
      const [carr, r] = await Promise.all([
        apiCached('/api/v1/commcalc/carriers', LOOKUP).catch(() => []),
        api('/api/v1/commcalc/payout-schedule'),
      ])
      setCarriers(carr)
      setScheds(r.schedules || []); setReady(r.ready !== false)
      if (r.ready === false) setMsg(r.note || 'Run migration 057 to enable.')
    } catch (e: any) { setMsg('Load failed: ' + (e?.message || e)) }
  }
  async function loadMrc() {
    try { const r = await api('/api/v1/commcalc/product-mrc'); setMrcItems(r.items || []); setMrcReady(r.ready !== false) }
    catch { setMrcItems([]) }
  }
  async function loadSources() {
    try { const r = await api('/api/v1/commcalc/carrier-template/sources'); setSources(r.sources || []) }
    catch { setSources([]) }
  }
  useEffect(() => { load(); loadMrc(); loadSources() }, [])

  // ── Import a carrier payout template (clone another org's shareable config into this tenant) ──────
  const tplSrcObj = () => { const [o, c] = (tplSrc || '').split('|'); return sources.find(s => s.source_org_id === o && s.source_carrier_id === c) }
  async function tplPreviewRun() {
    const s = tplSrcObj(); if (!s) { setTplMsg('Pick a template to import.'); return }
    setTplBusy(true); setTplMsg(''); setTplPreview(null)
    try { setTplPreview(await api('/api/v1/commcalc/carrier-template/clone', { method: 'POST', body: JSON.stringify({ source_org_id: s.source_org_id, source_carrier_id: s.source_carrier_id, dry_run: true }) })) }
    catch (e: any) { setTplMsg('❌ ' + (e?.message || e)) } finally { setTplBusy(false) }
  }
  async function tplImport() {
    const s = tplSrcObj(); if (!s) return
    const c = tplPreview?.counts || {}
    const compLine = (c.schedules_company_skipped || 0) > 0 ? `\nNot cloned: ${c.schedules_company_skipped} company-scoped schedule(s) — company structures are org-specific.` : ''
    if (!confirm(`Import the "${s.carrier_name}" template into your org?\n\nCreates: ${c.carriers_created || 0} carrier, ${c.schedules_created || 0} schedules, ${c.lines_created || 0} lines, ${c.product_mrc_created || 0} MRC.\nSkips (already present): ${c.schedules_skipped || 0} schedules, ${c.product_mrc_skipped || 0} MRC.${compLine}\n\nThis only ADDS config — no pay changes until you recompute a period.`)) return
    setTplBusy(true); setTplMsg('')
    try {
      const r = await api('/api/v1/commcalc/carrier-template/clone', { method: 'POST', body: JSON.stringify({ source_org_id: s.source_org_id, source_carrier_id: s.source_carrier_id, dry_run: false }) })
      const rc = r?.counts || {}
      setTplMsg(`✅ Imported: ${rc.carriers_created || 0} carrier, ${rc.schedules_created || 0} schedules, ${rc.lines_created || 0} lines, ${rc.product_mrc_created || 0} MRC (skipped ${rc.schedules_skipped || 0} schedules, ${rc.product_mrc_skipped || 0} MRC already present). No pay change until you recompute a period.`)
      setTplPreview(r); load(); loadMrc(); loadSources()
    } catch (e: any) { setTplMsg('❌ ' + (e?.message || e)) } finally { setTplBusy(false) }
  }

  async function saveMrc() {
    if (!String(mrcDraft.plan_pattern || '').trim()) { setMsg('Enter a plan name.'); return }
    try {
      await api('/api/v1/commcalc/product-mrc', { method: 'POST', body: JSON.stringify({
        ...mrcDraft, carrier_id: mrcDraft.carrier_id || null,
        mrc: Number(mrcDraft.mrc) || 0, priority: Number(mrcDraft.priority) || 100 }) })
      setMrcDraft(blankMrc()); loadMrc()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function delMrc(id: string) {
    if (!confirm('Delete this MRC entry?')) return
    try { await api(`/api/v1/commcalc/product-mrc/${id}`, { method: 'DELETE' }); loadMrc() } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function runCoverage() {
    try { setCoverage(await api(`/api/v1/commcalc/product-mrc/coverage${covPeriod.trim() ? `?period=${encodeURIComponent(covPeriod.trim())}` : ''}`)) }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function impPreview(file: File, planCol = '', mrcCol = '') {
    setImpBusy(true); setMsg('')
    try {
      const fd = new FormData()
      fd.append('file', file); fd.append('dry_run', 'true')
      if (planCol) fd.append('plan_col', planCol)
      if (mrcCol) fd.append('mrc_col', mrcCol)
      const r = await apiUpload('/api/v1/commcalc/product-mrc/import', fd)
      setImp({ ...r, file, carrier_id: imp?.carrier_id || '' })
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setImpBusy(false)
  }
  async function impCommit() {
    if (!imp?.file || !imp.plan_col || !imp.mrc_col) { setMsg('Pick the plan and MRC columns first.'); return }
    setImpBusy(true); setMsg('')
    try {
      const fd = new FormData()
      fd.append('file', imp.file); fd.append('plan_col', imp.plan_col); fd.append('mrc_col', imp.mrc_col)
      if (imp.carrier_id) fd.append('carrier_id', imp.carrier_id)
      const r = await apiUpload('/api/v1/commcalc/product-mrc/import', fd)
      setMsg(`✅ Imported ${r.saved} new + ${r.updated} updated plan MRCs (${r.skipped} rows skipped).`)
      setImp(null); loadMrc(); if (coverage) runCoverage()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setImpBusy(false)
  }
  // plans already seen on statements (from the coverage check) — feeds the plan-name dropdown
  const seenPlans: string[] = Array.from(new Set(((coverage?.plans || []) as any[]).map(p => p.customer_plan))).sort()

  function setNum(n: number) {
    const lines = Array.from({ length: n }, (_, i) => draft.lines?.[i] || blankLine(i + 1))
    setDraft({ ...draft, num_months: n, lines })
  }
  const setLine = (i: number, patch: Partial<Line>) =>
    setDraft({ ...draft, lines: (draft.lines || []).map((l, idx) => idx === i ? { ...l, ...patch } : l) })

  async function save() {
    setBusy(true); setMsg('')
    try {
      await api('/api/v1/commcalc/payout-schedule', { method: 'POST', body: JSON.stringify({
        ...draft, carrier_id: draft.carrier_id || null,
        lines: (draft.lines || []).map(l => ({ ...l, flat_amount: Number(l.flat_amount) || 0, mrc_pct: Number(l.mrc_pct) || 0 })),
      }) })
      setMsg('✅ Schedule saved.'); setDraft(blankSched()); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy(false) }
  }
  async function edit(s: Sched) { setDraft({ ...s, carrier_id: s.carrier_id || '', lines: s.lines?.length ? s.lines : Array.from({ length: s.num_months }, (_, i) => blankLine(i + 1)) }) }
  async function del(id?: string) {
    if (!id || !confirm('Delete this schedule?')) return
    try { await api(`/api/v1/commcalc/payout-schedule/${id}`, { method: 'DELETE' }); load() } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function runPreview() {
    if (!period.trim()) { setMsg('Enter a pay period (e.g. June 2026).'); return }
    setBusy(true); setMsg(''); setPreview(null)
    try { setPreview(await api(`/api/v1/commcalc/payout-schedule/preview?period=${encodeURIComponent(period.trim())}`)) }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy(false) }
  }
  // Preview is read-only. Applying the saved schedules/plans to the live Rep Commission report is the
  // SHARED <RunCommissionButton> (commcalc/_lib) — this page's bespoke recompute() was folded into it on
  // 2026-08-05 so the confirm, the 409 handling and the never-re-fire rule are identical on every
  // commission-structure page instead of being re-implemented per page.
  const carrierName = (id?: string | null) => carriers.find(c => c.id === id)?.name || (id ? 'carrier' : 'Any carrier')

  return (
    <div style={{ maxWidth: 980 }}>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📆 Multi-Month Payout Schedules</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Spread a rep&apos;s commission for one activation over up to 3 months (flat or % of that month&apos;s MRC).
          Months 2–3 pay only if the bill was <strong>paid + residual received</strong> that month. No schedule =
          single-month payout (unchanged). <strong>Preview</strong> is read-only — it does not change live payouts.
        </p>
      </div>
      {!ready && <div className="card" style={{ padding: 14, marginBottom: 14, background: '#fffbeb', border: '1px solid #fde68a', fontSize: 13 }}>⚠️ {msg || 'Run migration 057_multi_month_payout.sql in Supabase to enable this feature.'}</div>}

      {/* import carrier template — clone another org's shareable payout config into this tenant */}
      {sources.length > 0 && (
        <div className="card" style={{ padding: 16, marginBottom: 16 }}>
          <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 4 }}>📥 Import a carrier payout template</div>
          <p style={{ color: 'var(--text2)', fontSize: 13, margin: '0 0 12px' }}>
            Copy a shareable carrier&apos;s payout config (the carrier + its multi-month schedules + per-product MRC)
            into your org. Rows are re-stamped for your tenant with new IDs; anything you already have is{' '}
            <strong>skipped</strong> (your edits survive). This only <strong>adds config</strong> — no pay changes
            until you recompute a period.
          </p>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 10 }}>
            <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Template<br />
              <div style={{ marginTop: 4 }}>
                <EntityPicker
                  options={sources.map(s => ({ id: `${s.source_org_id}|${s.source_carrier_id}`, label: `${s.carrier_name} — ${s.schedule_count} sched · ${s.line_count} lines · ${s.product_mrc_count} MRC${s.is_own ? ' (your org)' : ''}` }))}
                  value={tplSrc || null} width={380}
                  onChange={(v: string | null) => { setTplSrc(v || ''); setTplPreview(null); setTplMsg('') }}
                  placeholder="pick a shared template…" ariaLabel="Carrier template" />
              </div>
            </label>
            <button className="btn btn-secondary" disabled={tplBusy || !tplSrc} onClick={tplPreviewRun}>{tplBusy ? '…' : '🔎 Preview'}</button>
            {tplPreview?.dry_run && <button className="btn btn-primary" disabled={tplBusy} onClick={tplImport}>⬇ Import into my org</button>}
          </div>
          {tplMsg && <div style={{ fontSize: 13, marginBottom: 8 }}>{tplMsg}</div>}
          {tplPreview && (
            <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, fontSize: 13, background: 'var(--surface2)' }}>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>
                {tplPreview.dry_run ? '🔎 Preview' : '✅ Result'} — {tplPreview.carrier?.name}{' '}
                <span style={{ fontWeight: 400, color: 'var(--text3)' }}>
                  ({tplPreview.carrier?.action === 'match' ? 'carrier already exists → matched' : 'carrier will be created'})
                </span>
              </div>
              <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                <span>Schedules: <strong>{tplPreview.counts?.schedules_created || 0}</strong> new / {tplPreview.counts?.schedules_skipped || 0} skipped</span>
                <span>Lines: <strong>{tplPreview.counts?.lines_created || 0}</strong> new</span>
                <span>Per-product MRC: <strong>{tplPreview.counts?.product_mrc_created || 0}</strong> new / {tplPreview.counts?.product_mrc_skipped || 0} skipped</span>
              </div>
              {(tplPreview.schedules?.create || []).length > 0 && (
                <div style={{ marginTop: 8, color: 'var(--text2)' }}>
                  New schedules: {(tplPreview.schedules.create as any[]).map(s => `${s.activation_type} (${s.num_months}mo/${s.lines}L)`).join(', ')}
                </div>
              )}
              {(tplPreview.schedules?.skip || []).length > 0 && (
                <div style={{ marginTop: 4, color: 'var(--text3)' }}>
                  Skipped (already present): {(tplPreview.schedules.skip as any[]).map(s => s.activation_type).join(', ')}
                </div>
              )}
              {(tplPreview.schedules?.company_skipped || []).length > 0 && (
                <div style={{ marginTop: 4, color: 'var(--text3)' }}>
                  {(tplPreview.schedules.company_skipped as any[]).length} company-scoped schedule(s) not cloned
                  (company structures are org-specific): {(tplPreview.schedules.company_skipped as any[]).map(s => s.activation_type).join(', ')}
                </div>
              )}
              <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text3)', borderTop: '1px solid var(--border)', paddingTop: 6 }}>
                Import is schedule-grain: an already-present schedule is skipped as a whole (its lines aren&apos;t
                re-checked), so a hand-edit survives. To rebuild a schedule that&apos;s missing lines (e.g. one was
                deleted), <strong>delete that schedule below and re-import</strong> — it re-creates with all its lines.
              </div>
            </div>
          )}
        </div>
      )}

      {/* editor */}
      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 12 }}>{draft.id ? '✏️ Edit schedule' : '➕ New schedule'}</div>
        <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 12 }}>
          <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Carrier<br />
            <select style={{ ...sel, marginTop: 4 }} value={draft.carrier_id || ''} onChange={e => setDraft({ ...draft, carrier_id: e.target.value })}>
              <option value="">Any carrier</option>
              {carriers.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Applies to<br />
            <select style={{ ...sel, marginTop: 4 }} value={draft.activation_type} onChange={e => setDraft({ ...draft, activation_type: e.target.value })}>
              <option value="*">All activations</option><option value="premium">Premium</option><option value="byod">BYOD</option><option value="upgrade">Upgrade</option>
            </select>
          </label>
          <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Months<br />
            <select style={{ ...sel, marginTop: 4 }} value={draft.num_months} onChange={e => setNum(parseInt(e.target.value))}>
              {Array.from({ length: 12 }, (_, i) => i + 1).map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>“Paid” signal (months 2–3)<br />
            <select style={{ ...sel, marginTop: 4, width: 280 }} value={draft.gate_signal} onChange={e => setDraft({ ...draft, gate_signal: e.target.value })}>
              {GATES.map(g => <option key={g.v} value={g.v}>{g.l}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6, paddingBottom: 6 }}>
            <input type="checkbox" checked={draft.bypass_tier} onChange={e => setDraft({ ...draft, bypass_tier: e.target.checked })} /> Don&apos;t re-apply KPI tier
          </label>
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse', maxWidth: 720 }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>{['Month', 'Type', 'Amount', 'MRC basis', 'Requires paid'].map(h => <th key={h} style={{ textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
          <tbody>
            {(draft.lines || []).map((l, i) => (
              <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '6px 8px', fontWeight: 600 }}>Month {l.month_index}</td>
                <td style={{ padding: '6px 8px' }}>
                  <select style={sel} value={l.payout_kind} onChange={e => setLine(i, { payout_kind: e.target.value })}>
                    <option value="flat">Flat $</option><option value="pct_mrc">% of MRC</option>
                  </select>
                </td>
                <td style={{ padding: '6px 8px' }}>
                  {l.payout_kind === 'pct_mrc'
                    ? <input style={{ ...sel, width: 90 }} type="number" step="0.01" placeholder="0.05 = 5%" value={l.mrc_pct} onChange={e => setLine(i, { mrc_pct: e.target.value })} />
                    : <input style={{ ...sel, width: 90 }} type="number" step="0.01" placeholder="$" value={l.flat_amount} onChange={e => setLine(i, { flat_amount: e.target.value })} />}
                </td>
                <td style={{ padding: '6px 8px' }}>
                  {l.payout_kind === 'pct_mrc'
                    ? <select style={sel} value={l.mrc_basis} onChange={e => setLine(i, { mrc_basis: e.target.value })}><option value="commissionable_mrc">Commissionable MRC</option><option value="base_mrc">Base MRC</option><option value="product_catalog">Per-product MRC (catalog)</option></select>
                    : <span style={{ fontSize: 12, color: 'var(--text3)' }}>—</span>}
                </td>
                <td style={{ padding: '6px 8px' }}>
                  {l.month_index === 1 ? <span style={{ fontSize: 12, color: 'var(--text3)' }}>always</span>
                    : <input type="checkbox" checked={l.requires_paid} onChange={e => setLine(i, { requires_paid: e.target.checked })} />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div style={{ marginTop: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
          <button className="btn btn-primary" disabled={busy} onClick={save}>💾 Save schedule</button>
          {draft.id && <button className="btn btn-secondary" onClick={() => setDraft(blankSched())}>Cancel edit</button>}
          {msg && ready && <span style={{ fontSize: 13 }}>{msg}</span>}
        </div>
      </div>

      {/* existing schedules */}
      {scheds.length > 0 && (
        <div className="card" style={{ padding: 0, marginBottom: 16 }}>
          <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>Configured schedules ({scheds.length})</div>
          {scheds.map(s => (
            <div key={s.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px', borderTop: '1px solid var(--border)', fontSize: 13 }}>
              <span style={{ fontWeight: 600 }}>{carrierName(s.carrier_id)}</span>
              <span style={{ color: 'var(--text3)' }}>· {s.activation_type === '*' ? 'all activations' : s.activation_type} · {s.num_months} mo · {(s.lines || []).map(l => l.payout_kind === 'pct_mrc' ? `${Math.round((l.mrc_pct || 0) * 100)}%MRC` : `$${l.flat_amount}`).join(' → ')}</span>
              <span style={{ flex: 1 }} />
              {!s.is_active && <span style={{ fontSize: 11, color: '#b45309' }}>inactive</span>}
              <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px' }} onClick={() => edit(s)}>Edit</button>
              <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px', color: '#dc2626' }} onClick={() => del(s.id)}>Delete</button>
            </div>
          ))}
        </div>
      )}

      {/* per-product MRC catalog */}
      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 4 }}>🏷️ Per-product MRC catalog</div>
        <p style={{ color: 'var(--text2)', fontSize: 13, margin: '0 0 12px' }}>
          Maps a subscriber&apos;s plan (the raw_mi <strong>Customer Plan</strong>) → its monthly recurring charge.
          A <strong>% of MRC</strong> line uses this directly when its basis is <strong>Per-product MRC</strong>, and as
          a fallback whenever the carrier statement reports $0 MRC (e.g. Total Wireless) — so residual installments
          compute real amounts instead of $0. Carriers that report a real MRC (Boost) are unaffected.
        </p>
        {!mrcReady && <div style={{ padding: 12, marginBottom: 12, background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 8, fontSize: 13 }}>⚠️ Run migration 074_product_mrc.sql in Supabase to enable this catalog.</div>}

        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 12 }}>
          <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Plan name<br />
            {/* RULE THREE §3b: pick an OBSERVED plan (from the statements) instead of free-typing; a
                genuine new/contains pattern stays available via the explicit create affordance. */}
            <div style={{ marginTop: 4 }}>
              <EntityPicker
                options={(() => { const o = seenPlans.map(p => ({ id: p, label: p })); if (mrcDraft.plan_pattern && !o.some(x => x.id === mrcDraft.plan_pattern)) o.unshift({ id: mrcDraft.plan_pattern, label: mrcDraft.plan_pattern }); return o })()}
                value={mrcDraft.plan_pattern || null} allowCreate width={220}
                onChange={v => setMrcDraft({ ...mrcDraft, plan_pattern: v || '' })}
                onCreate={v => setMrcDraft({ ...mrcDraft, plan_pattern: v })}
                placeholder={seenPlans.length ? 'pick or type a plan…' : 'e.g. Total Unlimited $60'} ariaLabel="Plan name" />
            </div>
            {!seenPlans.length && <span style={{ fontSize: 11, fontWeight: 400, color: 'var(--text3)', display: 'block' }}>Tip: run “Check plans” below to fill this dropdown with the plans on your statements.</span>}
          </label>
          <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Match<br />
            <select style={{ ...sel, marginTop: 4 }} value={mrcDraft.match_op} onChange={e => setMrcDraft({ ...mrcDraft, match_op: e.target.value })}>
              <option value="equals">Exact</option><option value="contains">Contains</option>
            </select>
          </label>
          <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>MRC $<br />
            <input style={{ ...sel, marginTop: 4, width: 90 }} type="number" step="0.01" placeholder="60.00" value={mrcDraft.mrc} onChange={e => setMrcDraft({ ...mrcDraft, mrc: e.target.value })} />
          </label>
          <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Carrier<br />
            <select style={{ ...sel, marginTop: 4 }} value={mrcDraft.carrier_id || ''} onChange={e => setMrcDraft({ ...mrcDraft, carrier_id: e.target.value })}>
              <option value="">Any carrier</option>
              {carriers.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Priority<br />
            <input style={{ ...sel, marginTop: 4, width: 70 }} type="number" value={mrcDraft.priority} onChange={e => setMrcDraft({ ...mrcDraft, priority: e.target.value })} />
          </label>
          <button className="btn btn-primary" onClick={saveMrc}>{mrcDraft.id ? '💾 Update' : '➕ Add'}</button>
          {mrcDraft.id && <button className="btn btn-secondary" onClick={() => setMrcDraft(blankMrc())}>Cancel</button>}
        </div>

        {/* bulk import from a carrier price sheet — no more typing every plan by hand */}
        <div style={{ border: '1px dashed var(--border)', borderRadius: 8, padding: 12, marginBottom: 12 }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontWeight: 600, fontSize: 13 }}>📄 Import a price sheet</span>
            <label className="btn btn-secondary" style={{ fontSize: 12, cursor: 'pointer' }}>
              {imp?.file ? `↻ ${imp.file.name}` : 'Choose Excel/CSV…'}
              <input type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} disabled={impBusy}
                onChange={e => { const f = e.target.files?.[0]; if (f) impPreview(f); e.currentTarget.value = '' }} />
            </label>
            <span style={{ fontSize: 12, color: 'var(--text3)' }}>Any sheet with a plan-name column and a price column — we&apos;ll detect them, you confirm.</span>
          </div>
          {imp && (
            <div style={{ marginTop: 10 }}>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 8 }}>
                <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Plan column<br />
                  <select style={{ ...sel, marginTop: 4 }} value={imp.plan_col || ''} onChange={e => impPreview(imp.file, e.target.value, imp.mrc_col || '')}>
                    <option value="">— pick —</option>
                    {(imp.headers || []).map((h: string) => <option key={h} value={h}>{h}</option>)}
                  </select>
                </label>
                <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>MRC / price column<br />
                  <select style={{ ...sel, marginTop: 4 }} value={imp.mrc_col || ''} onChange={e => impPreview(imp.file, imp.plan_col || '', e.target.value)}>
                    <option value="">— pick —</option>
                    {(imp.headers || []).map((h: string) => <option key={h} value={h}>{h}</option>)}
                  </select>
                </label>
                <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Carrier<br />
                  <select style={{ ...sel, marginTop: 4 }} value={imp.carrier_id || ''} onChange={e => setImp({ ...imp, carrier_id: e.target.value })}>
                    <option value="">Any carrier</option>
                    {carriers.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                  </select>
                </label>
                <button className="btn btn-primary" disabled={impBusy || !imp.total} onClick={impCommit}>{impBusy ? '…' : `⬇ Import ${imp.total || 0} plans`}</button>
                <button className="btn btn-secondary" onClick={() => setImp(null)}>Cancel</button>
              </div>
              {imp.note && <div style={{ fontSize: 12, color: '#b45309', marginBottom: 6 }}>{imp.note}</div>}
              {(imp.rows || []).length > 0 && (
                <table style={{ borderCollapse: 'collapse', fontSize: 12 }}>
                  <thead><tr style={{ background: 'var(--surface2)' }}><th style={{ textAlign: 'left', padding: '4px 10px' }}>Plan</th><th style={{ textAlign: 'left', padding: '4px 10px' }}>MRC</th></tr></thead>
                  <tbody>
                    {imp.rows.map((r: any, i: number) => (
                      <tr key={i} style={{ borderTop: '1px solid var(--border)' }}><td style={{ padding: '3px 10px' }}>{r.plan}</td><td style={{ padding: '3px 10px', fontWeight: 600 }}>{fmt(r.mrc)}</td></tr>
                    ))}
                  </tbody>
                </table>
              )}
              {imp.total > (imp.rows || []).length && <div style={{ fontSize: 11, color: 'var(--text3)', padding: 4 }}>…and {imp.total - (imp.rows || []).length} more.</div>}
            </div>
          )}
        </div>

        {mrcItems.length > 0 && (
          <table style={{ width: '100%', borderCollapse: 'collapse', maxWidth: 760, marginBottom: 6 }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>{['Plan', 'Match', 'MRC', 'Carrier', 'Prio', ''].map(h => <th key={h} style={{ textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
            <tbody>
              {mrcItems.map(m => (
                <tr key={m.id} style={{ borderTop: '1px solid var(--border)', fontSize: 13 }}>
                  <td style={{ padding: '6px 8px', fontWeight: 600 }}>{m.plan_pattern}{!m.is_active && <span style={{ fontSize: 11, color: '#b45309' }}> (inactive)</span>}</td>
                  <td style={{ padding: '6px 8px', color: 'var(--text3)' }}>{m.match_op}</td>
                  <td style={{ padding: '6px 8px', fontWeight: 600 }}>{fmt(m.mrc)}</td>
                  <td style={{ padding: '6px 8px', color: 'var(--text3)' }}>{carrierName(m.carrier_id)}</td>
                  <td style={{ padding: '6px 8px', color: 'var(--text3)' }}>{m.priority}</td>
                  <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                    <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px' }} onClick={() => setMrcDraft({ ...m, carrier_id: m.carrier_id || '' })}>Edit</button>{' '}
                    <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px', color: '#dc2626' }} onClick={() => delMrc(m.id)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {/* coverage helper — which plans in raw_mi still need an MRC */}
        <div style={{ marginTop: 12, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <div style={{ fontWeight: 600, fontSize: 13 }}>Plan coverage</div>
            <input style={{ ...sel, width: 150 }} placeholder="Period (optional)" value={covPeriod} onChange={e => setCovPeriod(e.target.value)} />
            <button className="btn btn-secondary" onClick={runCoverage}>Check plans</button>
            {coverage?.plans && <span style={{ fontSize: 13, color: 'var(--text2)' }}>{coverage.plans.filter((p: any) => !p.matched).length} unmatched of {coverage.plans.length} plans</span>}
          </div>
          {coverage?.plans?.length > 0 && (
            <table style={{ width: '100%', borderCollapse: 'collapse', maxWidth: 760, marginTop: 8 }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>{['Customer Plan', 'Subs', 'MRC', ''].map(h => <th key={h} style={{ textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
              <tbody>
                {coverage.plans.slice(0, 100).map((p: any, i: number) => (
                  <tr key={i} style={{ borderTop: '1px solid var(--border)', fontSize: 12, background: p.matched ? 'transparent' : '#fef2f2' }}>
                    <td style={{ padding: '5px 8px' }}>{p.customer_plan}</td>
                    <td style={{ padding: '5px 8px' }}>{p.subscribers}</td>
                    <td style={{ padding: '5px 8px', fontWeight: 600 }}>{p.matched ? fmt(p.mrc) : <span style={{ color: '#b91c1c' }}>none</span>}</td>
                    <td style={{ padding: '5px 8px', textAlign: 'right' }}>
                      {!p.matched && <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px' }} onClick={() => setMrcDraft({ ...blankMrc(), plan_pattern: p.customer_plan, carrier_id: p.carrier_id || '' })}>＋ Add MRC</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {coverage?.plans?.length > 100 && <div style={{ fontSize: 12, color: 'var(--text3)', padding: 6 }}>Showing first 100.</div>}
        </div>
      </div>

      {/* RUN COMMISSION (owner directive 2026-08-05) — the shared control. It targets the SAME `period`
          state the preview below uses, and its picker writes back to it, so the page can never preview
          one month and recompute another. */}
      <div className="card" style={{ padding: 16, marginBottom: 16, display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{ fontWeight: 700, fontSize: 13 }}>⚡ Apply these schedules to live pay</div>
        <RunCommissionButton period={period} onPeriodChange={setPeriod}
          note="Preview is read-only. This is the action that writes the new numbers into the Rep Commission report for the period shown." />
      </div>

      {/* preview */}
      <div className="card" style={{ padding: 16 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>🔎 Preview (read-only)</div>
          <input style={{ ...sel, width: 150 }} placeholder="Pay period e.g. June 2026" value={period} onChange={e => setPeriod(e.target.value)} />
          <button className="btn btn-primary" disabled={busy} onClick={runPreview}>{busy ? '…' : 'Run preview'}</button>
          {preview?.totals && <span style={{ fontSize: 13, color: 'var(--text2)' }}>{fmt(preview.totals.amount)} · {preview.totals.reps} reps · {preview.totals.paid} paid / {preview.totals.withheld} withheld / {preview.totals.pending} pending</span>}
        </div>
        {preview?.note && <div style={{ fontSize: 13, color: 'var(--text3)' }}>{preview.note}</div>}
        {preview?.ledger?.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 720 }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>{['Subscriber', 'Rep', 'Store', 'Month', 'MRC', 'Amount', 'Status'].map(h => <th key={h} style={{ textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
              <tbody>
                {preview.ledger.slice(0, 200).map((d: any, i: number) => (
                  <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '5px 8px', fontSize: 12 }}>{d.subscriber_id}</td>
                    <td style={{ padding: '5px 8px', fontSize: 12 }}>{d.rep || '—'}</td>
                    <td style={{ padding: '5px 8px', fontSize: 12 }}>{d.store || '—'}</td>
                    <td style={{ padding: '5px 8px', fontSize: 12 }}>{d.month_index}</td>
                    <td style={{ padding: '5px 8px', fontSize: 12 }}>{fmt(d.mrc_at_pay)}{d.mrc_source === 'product_catalog' && <span title="from per-product MRC catalog" style={{ marginLeft: 4, fontSize: 10, color: '#2563eb' }}>catalog</span>}{d.mrc_source === 'none' && d.payout_kind === 'pct_mrc' && <span title="no MRC found — add one to the catalog" style={{ marginLeft: 4, fontSize: 10, color: '#b91c1c' }}>no MRC</span>}</td>
                    <td style={{ padding: '5px 8px', fontSize: 12, fontWeight: 600 }}>{fmt(d.amount)}</td>
                    <td style={{ padding: '5px 8px', fontSize: 12, color: d.status === 'paid' ? '#15803d' : d.status === 'withheld_unpaid' ? '#b91c1c' : '#b45309' }}>{d.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {preview.ledger.length > 200 && <div style={{ fontSize: 12, color: 'var(--text3)', padding: 8 }}>Showing first 200 of {preview.ledger.length}.</div>}
          </div>
        )}
      </div>
    </div>
  )
}
