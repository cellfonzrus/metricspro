'use client'
// POS SETUP WIZARD — owner directive 2026-08-09.
//
//   "Create a walkthrough wizard so the user is not overwhelmed and one thing after the other is
//    prompted to set it up. Give an option to upload a template ..."
//
// ONE STEP ON SCREEN AT A TIME. The rail on the left shows where you are and what is left, but only
// the current step's panel is rendered — the explicit instruction was "not overwhelmed", and a page
// that renders twelve forms is the thing that was ruled out.
//
// Everything on this page is server-derived:
//   • the step list, order, dependencies and "why it matters" come from GET /core/onboarding/pos
//     (config table core.module_onboarding_task, mig 733) — NOT from a constant in this file, so a
//     new step is a config row, not a deploy.
//   • completion is re-derived live from the tenant's own data on every load. This page cannot mark
//     a step done; it can only ask the server, which counts rows.
//   • template columns come from the real table definitions (GET /core/onboarding/templates/{k}),
//     so a downloaded template cannot drift from the table it feeds.
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'

type Evidence = { state: string; count: number | null; reason: string }
type Task = {
  task_key: string; title: string; why: string | null; step_group: string | null
  sort_order: number; depends_on: string[]; is_required: boolean; skippable: boolean
  template_key: string | null; import_source: string | null; href: string | null
  complete: boolean; completed_via: string; skipped: boolean; blocked_by: string[]
  available: boolean; evidence: Evidence
}
type Status = {
  module: string; tasks: Task[]; required_total: number; required_done: number
  total: number; done: number; complete: boolean; next_task_key: string | null
  registry_source: string
}
type TemplateCol = { name: string; type: string; hint: string }

const CARD: React.CSSProperties = {
  background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 12, padding: 20,
}

function download(filename: string, text: string) {
  const blob = new Blob([text], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename
  document.body.appendChild(a); a.click(); document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

export default function PosOnboardingPage() {
  const [status, setStatus] = useState<Status | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [activeKey, setActiveKey] = useState<string>('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async (keepActive = false) => {
    setErr('')
    try {
      const s: Status = await api('/api/v1/core/onboarding/pos')
      setStatus(s)
      if (!keepActive) setActiveKey(s.next_task_key || s.tasks[0]?.task_key || '')
    } catch (e: any) {
      setErr(e?.message || 'Could not load your POS setup checklist.')
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const tasks = status?.tasks || []
  const active = useMemo(
    () => tasks.find(t => t.task_key === activeKey) || tasks.find(t => !t.complete) || tasks[0],
    [tasks, activeKey])

  const groups = useMemo(() => {
    const out: { name: string; tasks: Task[] }[] = []
    for (const t of tasks) {
      const name = t.step_group || 'Setup'
      const g = out.find(x => x.name === name)
      if (g) g.tasks.push(t); else out.push({ name, tasks: [t] })
    }
    return out
  }, [tasks])

  async function setState(taskKey: string, s: 'skipped' | 'pending' | 'acknowledged') {
    if (busy) return
    setBusy(true); setErr('')
    try {
      const next: Status = await api(`/api/v1/core/onboarding/pos/task/${taskKey}`, {
        method: 'POST', body: JSON.stringify({ status: s }),
      })
      setStatus(next)
      const nk = next.tasks.find(t => !t.complete && t.available && !t.skipped)?.task_key
      if (s !== 'pending' && nk) setActiveKey(nk)
    } catch (e: any) {
      setErr(e?.message || 'Could not save.')
    } finally { setBusy(false) }
  }

  if (loading) return <div style={{ padding: 24, color: 'var(--text3)' }}>Loading your POS setup…</div>

  const pct = status && status.required_total > 0
    ? Math.round((status.required_done / status.required_total) * 100) : 0

  return (
    <div style={{ padding: '20px 24px', maxWidth: 1180, margin: '0 auto' }}>
      <div style={{ marginBottom: 4, fontSize: 22, fontWeight: 800 }}>Set up your Point of Sale</div>
      <div style={{ fontSize: 13.5, color: 'var(--text2)', marginBottom: 18 }}>
        A few things have to exist before you can ring a sale. We will take them one at a time —
        most can be imported from what MetricsPro already knows about your business.
      </div>

      {err && (
        <div style={{ background: '#fef2f2', border: '1px solid #fecaca', color: '#991b1b',
          borderRadius: 10, padding: '10px 14px', marginBottom: 14, fontSize: 13 }}>{err}</div>
      )}

      {status?.registry_source === 'shipped' && (
        <div style={{ background: '#f8fafc', border: '1px solid var(--border)', color: 'var(--text2)',
          borderRadius: 10, padding: '9px 14px', marginBottom: 14, fontSize: 12.5 }}>
          Showing the standard checklist. Per-tenant customisation of these steps becomes available
          once migration 733 is applied.
        </div>
      )}

      {/* progress */}
      <div style={{ ...CARD, padding: 16, marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>
            {status?.complete
              ? 'Your POS is ready to use'
              : `${status?.required_done ?? 0} of ${status?.required_total ?? 0} required steps done`}
          </div>
          <div style={{ marginLeft: 'auto', fontSize: 12.5, color: 'var(--text3)' }}>
            {status?.done ?? 0} of {status?.total ?? 0} steps complete overall
          </div>
        </div>
        <div style={{ height: 8, background: 'var(--border)', borderRadius: 99, overflow: 'hidden' }}>
          <div style={{ height: '100%', width: `${pct}%`, borderRadius: 99,
            background: status?.complete ? '#16a34a' : '#2563eb', transition: 'width .3s' }} />
        </div>
        {status?.complete && (
          <div style={{ marginTop: 12 }}>
            <Link href="/pos/sales" className="btn" style={{ textDecoration: 'none' }}>
              Open the register →
            </Link>
          </div>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr', gap: 16, alignItems: 'start' }}>
        {/* ── the rail: where you are, what is left ─────────────────────────────────────── */}
        <div style={{ ...CARD, padding: 12 }}>
          {groups.map(g => (
            <div key={g.name} style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 11, fontWeight: 800, letterSpacing: .6, textTransform: 'uppercase',
                color: 'var(--text3)', padding: '4px 8px' }}>{g.name}</div>
              {g.tasks.map(t => {
                const on = t.task_key === active?.task_key
                return (
                  <button key={t.task_key} onClick={() => setActiveKey(t.task_key)}
                    style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%',
                      textAlign: 'left', padding: '7px 8px', border: 'none', borderRadius: 8,
                      cursor: 'pointer', fontSize: 13,
                      background: on ? 'var(--bg2, #eef2ff)' : 'transparent',
                      color: t.complete ? 'var(--text3)' : 'var(--text)',
                      fontWeight: on ? 700 : 500 }}>
                    <span style={{ width: 18, textAlign: 'center', flexShrink: 0 }}>
                      {t.complete ? '✅' : t.skipped ? '⏭️' : t.available ? '⬜' : '🔒'}
                    </span>
                    <span style={{ flex: 1, textDecoration: t.skipped && !t.complete ? 'line-through' : 'none' }}>
                      {t.title}
                    </span>
                    {t.is_required && !t.complete && (
                      <span style={{ fontSize: 10, fontWeight: 800, color: '#b45309' }}>REQ</span>
                    )}
                  </button>
                )
              })}
            </div>
          ))}
        </div>

        {/* ── ONE step at a time ────────────────────────────────────────────────────────── */}
        <div>
          {active
            ? <StepPanel task={active} onChanged={() => load(true)} onSetState={setState} busy={busy} />
            : <div style={CARD}>Nothing to set up.</div>}
        </div>
      </div>
    </div>
  )
}

// ────────────────────────────────────────────────────────────────────────────────────────────────
function StepPanel({ task, onChanged, onSetState, busy }: {
  task: Task
  onChanged: () => void
  onSetState: (k: string, s: 'skipped' | 'pending' | 'acknowledged') => void
  busy: boolean
}) {
  return (
    <div style={{ ...CARD }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 6 }}>
        <div style={{ fontSize: 18, fontWeight: 800 }}>{task.title}</div>
        {task.is_required
          ? <span style={{ fontSize: 11, fontWeight: 800, color: '#b45309', background: '#fffbeb',
              border: '1px solid #fde68a', padding: '2px 7px', borderRadius: 99 }}>REQUIRED</span>
          : <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text3)', background: 'var(--bg)',
              border: '1px solid var(--border)', padding: '2px 7px', borderRadius: 99 }}>OPTIONAL</span>}
        {task.complete && (
          <span style={{ fontSize: 11, fontWeight: 800, color: '#166534', background: '#f0fdf4',
            border: '1px solid #bbf7d0', padding: '2px 7px', borderRadius: 99 }}>DONE</span>
        )}
      </div>

      {task.why && (
        <div style={{ fontSize: 13.5, color: 'var(--text2)', lineHeight: 1.55, marginBottom: 14 }}>
          {task.why}
        </div>
      )}

      {/* Live evidence — the wizard shows its work rather than asserting "done". */}
      <div style={{ fontSize: 12.5, color: 'var(--text3)', background: 'var(--bg)',
        border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', marginBottom: 14 }}>
        {task.evidence.state === 'manual'
          ? 'This step is confirmed by you — there is nothing for us to count.'
          : task.evidence.state === 'unknown'
            ? `We could not check this: ${task.evidence.reason}`
            : `Checked just now: ${task.evidence.reason}.`}
      </div>

      {task.blocked_by.length > 0 && (
        <div style={{ background: '#fffbeb', border: '1px solid #fde68a', color: '#92400e',
          borderRadius: 8, padding: '9px 12px', marginBottom: 14, fontSize: 13 }}>
          Finish <b>{task.blocked_by.join(', ')}</b> first — this step needs it.
        </div>
      )}

      {/* Catalog steps get an inline builder: define departments/categories/products right here,
          pick a pre-built combination, or start from the device list this store already recorded —
          instead of bouncing to a page that itself needs the catalog to exist first. */}
      {['departments', 'categories', 'products'].includes(task.task_key) && task.available && (
        <CatalogBuilder task={task} onChanged={onChanged} />
      )}

      {task.import_source && !task.complete && task.available && (
        <ImportFromExisting source={task.import_source} onChanged={onChanged} />
      )}

      {task.template_key && (
        <TemplateBlock templateKey={task.template_key} />
      )}

      <div style={{ display: 'flex', gap: 8, marginTop: 18, flexWrap: 'wrap' }}>
        {task.href && (
          <Link href={task.href} className="btn" style={{ textDecoration: 'none' }}>
            {task.complete ? 'Review' : 'Do this now'} →
          </Link>
        )}
        <button className="btn" onClick={onChanged} disabled={busy}>Re-check</button>
        {task.skippable && !task.complete && !task.skipped && (
          <button className="btn" disabled={busy}
            onClick={() => onSetState(task.task_key, 'skipped')}
            style={{ marginLeft: 'auto' }}>Skip for now</button>
        )}
        {task.skipped && (
          <button className="btn" disabled={busy}
            onClick={() => onSetState(task.task_key, 'pending')}
            style={{ marginLeft: 'auto' }}>Un-skip</button>
        )}
        {task.evidence.state === 'manual' && !task.complete && (
          <button className="btn" disabled={busy}
            onClick={() => onSetState(task.task_key, 'acknowledged')}>Mark as done</button>
        )}
      </div>
    </div>
  )
}

// ────────────────────────────────────────────────────────────────────────────────────────────────
// "Upload a template instead" — the columns are fetched from the server, which reads them off the
// real table. Nothing about the header list is typed in this file.
function TemplateBlock({ templateKey }: { templateKey: string }) {
  const [cols, setCols] = useState<TemplateCol[] | null>(null)
  const [note, setNote] = useState('')
  const [source, setSource] = useState('')
  const [open, setOpen] = useState(false)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let dead = false
    ;(async () => {
      try {
        const t = await api(`/api/v1/core/onboarding/templates/${templateKey}`)
        if (dead) return
        setCols(t.columns || []); setNote(t.note || ''); setSource(t.column_source || '')
      } catch (e: any) { if (!dead) setErr(e?.message || 'Could not load the template columns.') }
    })()
    return () => { dead = true }
  }, [templateKey])

  async function grab() {
    setBusy(true); setErr('')
    try {
      const r = await api(`/api/v1/core/onboarding/templates/${templateKey}/csv`)
      download(r.filename || `${templateKey}_template.csv`, r.csv || '')
    } catch (e: any) { setErr(e?.message || 'Download failed.') } finally { setBusy(false) }
  }

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 14, marginTop: 4 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 700, fontSize: 13.5 }}>📄 Prefer a spreadsheet?</span>
        <button className="btn" onClick={grab} disabled={busy}>
          {busy ? 'Preparing…' : 'Download the template'}
        </button>
        <Link href="/pos/import" className="btn" style={{ textDecoration: 'none' }}>
          Upload a filled template →
        </Link>
        <button className="btn" onClick={() => setOpen(o => !o)} style={{ marginLeft: 'auto' }}>
          {open ? 'Hide columns' : `Show the ${cols?.length ?? ''} columns`}
        </button>
      </div>
      {note && <div style={{ fontSize: 12.5, color: 'var(--text2)', marginTop: 8 }}>{note}</div>}
      {err && <div style={{ fontSize: 12.5, color: '#b91c1c', marginTop: 8 }}>{err}</div>}
      {source === 'snapshot' && (
        <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 6 }}>
          Columns are from this release&apos;s built-in copy of the table definition — the live
          definition was not readable from here.
        </div>
      )}
      {open && cols && (
        <div style={{ marginTop: 10, maxHeight: 260, overflow: 'auto',
          border: '1px solid var(--border)', borderRadius: 8 }}>
          <table style={{ width: '100%', fontSize: 12.5, borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--bg)' }}>
              <th style={{ textAlign: 'left', padding: '6px 10px' }}>Column</th>
              <th style={{ textAlign: 'left', padding: '6px 10px' }}>What goes in it</th>
            </tr></thead>
            <tbody>
              {cols.map(c => (
                <tr key={c.name} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '5px 10px', fontFamily: 'ui-monospace, monospace' }}>{c.name}</td>
                  <td style={{ padding: '5px 10px', color: 'var(--text2)' }}>{c.hint || c.type}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ────────────────────────────────────────────────────────────────────────────────────────────────
// "Bring it over from what MetricsPro already has." Preview first, always — the operator sees the
// count and a sample before anything is written.
function ImportFromExisting({ source, onChanged }: { source: string; onChanged: () => void }) {
  const NEEDS_VARIANT = source === 'inventory_from_metricspro'
  const [variant, setVariant] = useState(NEEDS_VARIANT ? 'asset_ledger' : '')
  const [preview, setPreview] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [result, setResult] = useState<any>(null)

  const look = useCallback(async () => {
    setBusy(true); setErr(''); setResult(null)
    try {
      const q = variant ? `?variant=${encodeURIComponent(variant)}` : ''
      setPreview(await api(`/api/v1/core/onboarding/import-sources/${source}/preview${q}`))
    } catch (e: any) { setErr(e?.message || 'Preview failed.') } finally { setBusy(false) }
  }, [source, variant])

  useEffect(() => { look() }, [look])

  async function apply() {
    if (!preview?.count) return
    if (!window.confirm(`Create ${preview.count} record(s) from ${preview.title}? Existing records are left alone.`)) return
    setBusy(true); setErr('')
    try {
      const q = variant ? `?variant=${encodeURIComponent(variant)}` : ''
      const r = await api(`/api/v1/core/onboarding/import-sources/${source}/apply${q}`,
        { method: 'POST', body: JSON.stringify({ variant }) })
      setResult(r); onChanged()
    } catch (e: any) { setErr(e?.message || 'Import failed.') } finally { setBusy(false) }
  }

  return (
    <div style={{ border: '1px solid #bfdbfe', background: '#eff6ff', borderRadius: 10,
      padding: 14, marginBottom: 14 }}>
      <div style={{ fontWeight: 700, fontSize: 13.5, marginBottom: 4 }}>
        ⚡ {preview?.title || 'Bring this over from MetricsPro'}
      </div>
      <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 10 }}>{preview?.detail}</div>

      {NEEDS_VARIANT && (
        <div style={{ display: 'flex', gap: 14, marginBottom: 10, fontSize: 13, flexWrap: 'wrap' }}>
          <label style={{ display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}>
            <input type="radio" checked={variant === 'asset_ledger'}
              onChange={() => setVariant('asset_ledger')} />
            VIP consignment ledger (unsold, on inventory)
          </label>
          <label style={{ display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}>
            <input type="radio" checked={variant === 'inventory_aging'}
              onChange={() => setVariant('inventory_aging')} />
            Existing POS inventory snapshot
          </label>
        </div>
      )}

      {err && <div style={{ fontSize: 12.5, color: '#b91c1c', marginBottom: 8 }}>{err}</div>}

      {preview && (
        <div style={{ fontSize: 13, marginBottom: 10 }}>
          <b>{preview.count}</b> record(s) ready to create in <code>{preview.creates}</code>.
          {preview.count === 0 && ' Nothing found for your tenant — use the template instead.'}
          {preview.sample?.length > 0 && (
            <div style={{ marginTop: 6, fontSize: 12, color: 'var(--text2)',
              maxHeight: 92, overflow: 'auto', fontFamily: 'ui-monospace, monospace' }}>
              {preview.sample.slice(0, 8).map((s: any, i: number) => (
                <div key={i}>{typeof s === 'string' ? s : (s.short_name || s.legal_name || s.plan_name || s.serial_number || JSON.stringify(s).slice(0, 90))}</div>
              ))}
              {preview.sample.length > 8 && <div>…</div>}
            </div>
          )}
        </div>
      )}

      {result && (
        <div style={{ fontSize: 13, background: '#f0fdf4', border: '1px solid #bbf7d0',
          color: '#166534', borderRadius: 8, padding: '8px 12px', marginBottom: 10 }}>
          Created <b>{result.created}</b>, skipped {result.skipped} that already existed.
          {result.errors?.length > 0 && (
            <div style={{ color: '#991b1b', marginTop: 4 }}>{result.errors.join(' · ')}</div>
          )}
        </div>
      )}

      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn" onClick={look} disabled={busy}>Re-check</button>
        <button className="btn" onClick={apply} disabled={busy || !preview?.count}>
          {busy ? 'Working…' : `Bring over ${preview?.count ?? 0}`}
        </button>
      </div>
    </div>
  )
}

// ────────────────────────────────────────────────────────────────────────────────────────────────
// CATALOG BUILDER — the fix for "the wizard won't let me add a department/category/item".
// Three ways in, from smartest to most manual:
//   • pick a pre-built COMBINATION (presets, tailored by this store's own devices),
//   • adopt the department/category structure OTHER stores in the system commonly use, or
//   • type one in by hand.
// Everything writes through the same idempotent endpoints, so nothing is ever duplicated and the two
// dependent steps (departments → categories) can both be satisfied from one preset click.
type Dept = { id: string; short_name: string }
type Cat = { id: string; name: string; department_id: string | null }
type PresetDept = { short_name: string; full_name?: string; system_category?: string; categories: string[] }
type Preset = { id: string; label: string; why: string; departments: PresetDept[] }
type Learned = { department: string; orgs: number; categories: string[] }
type Suggest = {
  have: { departments: Dept[]; categories: Cat[] }
  presets: Preset[]
  learned: Learned[]
  derived: { manufacturers: string[]; device_types: string[]; devices: string[]; plans: string[]; has_own_data: boolean }
}

function CatalogBuilder({ task, onChanged }: { task: Task; onChanged: () => void }) {
  const [sug, setSug] = useState<Suggest | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')

  const reload = useCallback(async () => {
    setErr('')
    try { setSug(await api('/api/v1/pos/catalog/suggest')) }
    catch (e: any) { setErr(e?.message || 'Could not load suggestions.') }
  }, [])
  useEffect(() => { reload() }, [reload])

  const refresh = useCallback(async () => { await reload(); onChanged() }, [reload, onChanged])

  async function applyPreset(p: Preset) {
    if (busy) return
    setBusy(p.id); setErr('')
    try {
      const departments = p.departments.map(d => ({
        short_name: d.short_name, full_name: d.full_name || d.short_name, system_category: d.system_category,
      }))
      const categories = p.departments.flatMap(d => d.categories.map(name => ({ name, department: d.short_name })))
      const system_categories = Array.from(new Set(p.departments.map(d => d.system_category).filter(Boolean)))
      await api('/api/v1/pos/catalog/apply-suggestion', {
        method: 'POST', body: JSON.stringify({ departments, categories, system_categories }),
      })
      await refresh()
    } catch (e: any) { setErr(e?.message || 'Could not apply.') } finally { setBusy('') }
  }

  async function applyLearned(l: Learned) {
    if (busy) return
    setBusy('learned:' + l.department); setErr('')
    try {
      await api('/api/v1/pos/catalog/apply-suggestion', {
        method: 'POST',
        body: JSON.stringify({
          departments: [{ short_name: l.department }],
          categories: l.categories.map(name => ({ name, department: l.department })),
        }),
      })
      await refresh()
    } catch (e: any) { setErr(e?.message || 'Could not apply.') } finally { setBusy('') }
  }

  if (task.task_key === 'products') {
    return <ProductBuilder sug={sug} err={err} onChanged={refresh} reload={reload} />
  }

  const have = sug?.have
  return (
    <div style={{ border: '1px solid #bfdbfe', background: '#eff6ff', borderRadius: 10, padding: 14, marginBottom: 14 }}>
      <div style={{ fontWeight: 700, fontSize: 13.5, marginBottom: 6 }}>
        🧩 Build your catalog — pick a ready-made set, or add your own
      </div>

      {err && <div style={{ fontSize: 12.5, color: '#b91c1c', marginBottom: 8 }}>{err}</div>}

      {/* what already exists */}
      {have && (
        <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 10 }}>
          You have <b>{have.departments.length}</b> department(s) and <b>{have.categories.length}</b> categor(ies).
          {have.departments.length > 0 && (
            <div style={{ marginTop: 4, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {have.departments.map(d => (
                <span key={d.id} style={{ fontSize: 11, background: 'var(--card)', border: '1px solid var(--border)',
                  borderRadius: 99, padding: '2px 8px' }}>{d.short_name}</span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* presets */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10, marginBottom: 12 }}>
        {(sug?.presets || []).map(p => (
          <div key={p.id} style={{ border: '1px solid var(--border)', background: 'var(--card)', borderRadius: 10, padding: 12 }}>
            <div style={{ fontWeight: 700, fontSize: 13 }}>{p.label}</div>
            <div style={{ fontSize: 11.5, color: 'var(--text2)', margin: '4px 0 8px', lineHeight: 1.45 }}>{p.why}</div>
            <div style={{ fontSize: 11.5, color: 'var(--text3)', marginBottom: 8 }}>
              {p.departments.map(d => (
                <div key={d.short_name} style={{ marginBottom: 2 }}>
                  <b style={{ color: 'var(--text2)' }}>{d.short_name}</b>
                  {d.categories.length > 0 && <span> — {d.categories.join(', ')}</span>}
                </div>
              ))}
            </div>
            <button className="btn btn-primary" disabled={!!busy} onClick={() => applyPreset(p)}>
              {busy === p.id ? 'Adding…' : 'Use this set'}
            </button>
          </div>
        ))}
      </div>

      {/* learned from other stores */}
      {sug?.learned && sug.learned.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 12.5, fontWeight: 700, marginBottom: 6 }}>
            💡 Common in other stores on the system
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {sug.learned.map(l => (
              <button key={l.department} className="btn" disabled={!!busy}
                onClick={() => applyLearned(l)} title={l.categories.join(', ')}
                style={{ fontSize: 12 }}>
                {busy === 'learned:' + l.department ? 'Adding…' : `+ ${l.department}`}
                {l.categories.length > 0 && (
                  <span style={{ color: 'var(--text3)' }}> ({l.categories.length} cats)</span>
                )}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* manual add */}
      <ManualCatalogAdd have={sug?.have} onChanged={refresh} />
    </div>
  )
}

function ManualCatalogAdd({ have, onChanged }: { have?: { departments: Dept[]; categories: Cat[] }; onChanged: () => void }) {
  const [dept, setDept] = useState('')
  const [cat, setCat] = useState('')
  const [catDept, setCatDept] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const depts = have?.departments || []

  async function addDept() {
    if (!dept.trim() || busy) return
    setBusy(true); setErr('')
    try {
      await api('/api/v1/pos/departments', { method: 'POST', body: JSON.stringify({ short_name: dept.trim() }) })
      setDept(''); onChanged()
    } catch (e: any) { setErr(e?.message || 'Could not add.') } finally { setBusy(false) }
  }
  async function addCat() {
    if (!cat.trim() || busy) return
    setBusy(true); setErr('')
    try {
      await api('/api/v1/pos/categories', {
        method: 'POST', body: JSON.stringify({ name: cat.trim(), department_id: catDept || undefined }),
      })
      setCat(''); onChanged()
    } catch (e: any) { setErr(e?.message || 'Could not add.') } finally { setBusy(false) }
  }

  const inp: React.CSSProperties = { padding: '5px 8px', border: '1px solid var(--border)', borderRadius: 6,
    background: 'var(--card)', color: 'var(--text)', fontSize: 13 }

  return (
    <div style={{ borderTop: '1px solid var(--border)', paddingTop: 10 }}>
      <div style={{ fontSize: 12.5, fontWeight: 700, marginBottom: 6 }}>Or add your own</div>
      {err && <div style={{ fontSize: 12, color: '#b91c1c', marginBottom: 6 }}>{err}</div>}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', marginBottom: 8 }}>
        <input style={{ ...inp, width: 200 }} placeholder="New department (e.g. Phones)"
          value={dept} onChange={e => setDept(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && addDept()} />
        <button className="btn" onClick={addDept} disabled={busy || !dept.trim()}>Add department</button>
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
        <input style={{ ...inp, width: 200 }} placeholder="New category (e.g. Smartphones)"
          value={cat} onChange={e => setCat(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && addCat()} />
        <select style={inp} value={catDept} onChange={e => setCatDept(e.target.value)}>
          <option value="">(no department)</option>
          {depts.map(d => <option key={d.id} value={d.id}>{d.short_name}</option>)}
        </select>
        <button className="btn" onClick={addCat} disabled={busy || !cat.trim()}>Add category</button>
      </div>
    </div>
  )
}

// ────────────────────────────────────────────────────────────────────────────────────────────────
// PRODUCT BUILDER — the Products step. Add products from the DEVICES this store already recorded
// (one click each, or all at once), or type one in with its department/category/price.
function ProductBuilder({ sug, err, onChanged, reload }: {
  sug: Suggest | null; err: string; onChanged: () => void; reload: () => void
}) {
  const [busy, setBusy] = useState('')
  const [localErr, setLocalErr] = useState('')
  const [added, setAdded] = useState<Set<string>>(new Set())
  const depts = sug?.have.departments || []
  const cats = sug?.have.categories || []
  const devices = sug?.derived.devices || []

  // manual form
  const [name, setName] = useState('')
  const [deptId, setDeptId] = useState('')
  const [catId, setCatId] = useState('')
  const [price, setPrice] = useState('')

  async function createProduct(body: any, tag: string) {
    setBusy(tag); setLocalErr('')
    try {
      await api('/api/v1/pos/products', { method: 'POST', body: JSON.stringify(body) })
      return true
    } catch (e: any) { setLocalErr(e?.message || 'Could not add product.'); return false }
    finally { setBusy('') }
  }

  async function addDevice(d: string) {
    if (busy) return
    const ok = await createProduct({ short_name: d, full_name: d, system_category: 'Cell Phone', inventory_type: 'serial' }, 'dev:' + d)
    if (ok) { setAdded(s => new Set(s).add(d)); onChanged() }
  }
  async function addAllDevices() {
    if (busy) return
    setBusy('all')
    for (const d of devices) {
      if (added.has(d)) continue
      try {
        await api('/api/v1/pos/products', {
          method: 'POST',
          body: JSON.stringify({ short_name: d, full_name: d, system_category: 'Cell Phone', inventory_type: 'serial' }),
        })
        setAdded(s => new Set(s).add(d))
      } catch { /* keep going; a dup just means it exists */ }
    }
    setBusy(''); onChanged()
  }
  async function addManual() {
    if (!name.trim()) return
    const ok = await createProduct({
      short_name: name.trim(), full_name: name.trim(),
      department_id: deptId || undefined, category_id: catId || undefined,
      retail_price: price ? Number(price) : undefined,
    }, 'manual')
    if (ok) { setName(''); setPrice(''); onChanged() }
  }

  const inp: React.CSSProperties = { padding: '5px 8px', border: '1px solid var(--border)', borderRadius: 6,
    background: 'var(--card)', color: 'var(--text)', fontSize: 13 }
  const catsFor = catId ? cats : cats.filter(c => !deptId || c.department_id === deptId)

  return (
    <div style={{ border: '1px solid #bfdbfe', background: '#eff6ff', borderRadius: 10, padding: 14, marginBottom: 14 }}>
      <div style={{ fontWeight: 700, fontSize: 13.5, marginBottom: 6 }}>🧩 Add your products</div>
      {(err || localErr) && <div style={{ fontSize: 12.5, color: '#b91c1c', marginBottom: 8 }}>{err || localErr}</div>}

      {devices.length > 0 ? (
        <div style={{ marginBottom: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <span style={{ fontSize: 12.5, fontWeight: 700 }}>
              {devices.length} device(s) this store has already sold
            </span>
            <button className="btn btn-primary" onClick={addAllDevices} disabled={!!busy}>
              {busy === 'all' ? 'Adding…' : 'Add all as products'}
            </button>
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', maxHeight: 150, overflow: 'auto' }}>
            {devices.map(d => (
              <button key={d} className="btn" disabled={!!busy || added.has(d)} onClick={() => addDevice(d)}
                style={{ fontSize: 12, opacity: added.has(d) ? 0.5 : 1 }}>
                {added.has(d) ? '✓ ' : '+ '}{d}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 12 }}>
          No recorded devices to suggest yet — add your products by hand below, or download the template.
        </div>
      )}

      {/* manual add */}
      <div style={{ borderTop: '1px solid var(--border)', paddingTop: 10 }}>
        <div style={{ fontSize: 12.5, fontWeight: 700, marginBottom: 6 }}>Or add one by hand</div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <input style={{ ...inp, width: 220 }} placeholder="Product / service name"
            value={name} onChange={e => setName(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && addManual()} />
          <select style={inp} value={deptId} onChange={e => { setDeptId(e.target.value); setCatId('') }}>
            <option value="">Department…</option>
            {depts.map(d => <option key={d.id} value={d.id}>{d.short_name}</option>)}
          </select>
          <select style={inp} value={catId} onChange={e => setCatId(e.target.value)}>
            <option value="">Category…</option>
            {catsFor.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <input style={{ ...inp, width: 90 }} placeholder="Price" inputMode="decimal"
            value={price} onChange={e => setPrice(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && addManual()} />
          <button className="btn" onClick={addManual} disabled={!!busy || !name.trim()}>Add product</button>
        </div>
      </div>

      <div style={{ marginTop: 10 }}>
        <button className="btn" onClick={reload} disabled={!!busy}>Re-check</button>
      </div>
    </div>
  )
}
