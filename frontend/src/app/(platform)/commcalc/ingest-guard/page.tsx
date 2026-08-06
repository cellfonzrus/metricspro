'use client'
import { useState, useEffect, useCallback } from 'react'
import { api, getActiveOrg } from '@/lib/client'

/**
 * CROSS-TENANT INGEST GUARD — admin UI (owner-approved 2026-08-06).
 *
 * The screen that manages the control for the "another tenant's sales landed in my org" class.
 * Deliberately written in plain shop English, not developer English: the person who uses this is
 * an operator wondering why a store they don't recognise showed up in their numbers.
 */
type Mode = 'off' | 'warn' | 'block'
interface ModeOpt { value: Mode; label: string; help: string }
interface Cfg {
  ready?: boolean
  mode?: Mode
  block_min_rows?: number
  allow_creates_alias?: boolean
  notify_on_flag?: boolean
  known_store_keys?: number
  modes?: ModeOpt[]
  hint?: string
  updated_at?: string
  updated_by?: string
}
interface Item {
  id: string
  created_at: string
  store_raw: string
  source?: string
  upload_type?: string
  target_table?: string
  period?: string
  filename?: string
  rows_seen: number
  rows_withheld: number
  amount_seen: number
  sample?: any
  status: string
  mode_at_flag?: string
  decided_at?: string
  decided_by?: string
  decision_note?: string
}
interface StoreOpt { store_code: string; store_address?: string }

const money = (n: number) =>
  `$${(Number(n) || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

export default function IngestGuardPage() {
  const [cfg, setCfg] = useState<Cfg>({})
  const [items, setItems] = useState<Item[]>([])
  const [stores, setStores] = useState<StoreOpt[]>([])
  const [status, setStatus] = useState('pending')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')
  const [queueHint, setQueueHint] = useState('')
  const [pickCode, setPickCode] = useState<Record<string, string>>({})
  const [busyId, setBusyId] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  const org = () => getActiveOrg()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const c = await api(`/api/v1/commcalc/ingest-guard/config?org_id=${org()}`)
      setCfg(c || {})
    } catch { setCfg({}) }
    try {
      const q = await api(`/api/v1/commcalc/ingest-guard/queue?status=${status}&org_id=${org()}`)
      setItems(q?.items || [])
      setQueueHint(q?.ok === false ? (q?.hint || '') : '')
    } catch { setItems([]) }
    try {
      // pick-don't-type: allowing a store maps it to one of THIS org's real stores.
      const s = await api(`/api/v1/commcalc/store-aliases?org_id=${org()}`)
      setStores(s?.stores || [])
    } catch { setStores([]) }
    setLoading(false)
  }, [status])

  useEffect(() => { load() }, [load])

  async function saveCfg(next: Partial<Cfg>) {
    setSaving(true); setMsg('')
    const body = {
      mode: next.mode ?? cfg.mode ?? 'warn',
      block_min_rows: next.block_min_rows ?? cfg.block_min_rows ?? 0,
      allow_creates_alias: next.allow_creates_alias ?? cfg.allow_creates_alias ?? true,
      notify_on_flag: next.notify_on_flag ?? cfg.notify_on_flag ?? true,
    }
    try {
      const c = await api(`/api/v1/commcalc/ingest-guard/config?org_id=${org()}`,
        { method: 'PUT', body: JSON.stringify(body) })
      setCfg(c || {}); setMsg('Saved.')
    } catch (e: any) { setMsg(e?.message || 'Could not save.') }
    setSaving(false)
  }

  async function decide(it: Item, decision: 'allow' | 'reject') {
    setBusyId(it.id); setMsg('')
    try {
      const r = await api(`/api/v1/commcalc/ingest-guard/queue/${it.id}/decide?org_id=${org()}`, {
        method: 'POST',
        body: JSON.stringify({ decision, store_code: decision === 'allow' ? (pickCode[it.id] || '') : '' }),
      })
      setMsg(decision === 'allow'
        ? `"${it.store_raw}" is now treated as one of your stores${r?.rows_released ? ` · ${r.rows_released} held row(s) imported` : ''}.`
        : `"${it.store_raw}" rejected — nothing was imported, and nothing was deleted.`)
      await load()
    } catch (e: any) { setMsg(e?.message || 'Could not save that decision.') }
    setBusyId(null)
  }

  const mode = (cfg.mode || 'warn') as Mode
  const modes: ModeOpt[] = cfg.modes || []
  const th: React.CSSProperties = { textAlign: 'left', padding: '10px 12px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '.05em' }
  const td: React.CSSProperties = { padding: '9px 12px', fontSize: 13, verticalAlign: 'top' }

  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Store Check on Imports</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Catches sales files that belong to a different company before they get mixed into your numbers.
        </p>
      </div>

      <div style={{ background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 10, padding: '12px 16px', marginBottom: 18, fontSize: 13, color: '#1e40af' }}>
        💡 Every time a sales file is imported, we look at the store name on each line and check it against
        your own store list. If a store turns up that you&apos;ve never had, it gets listed below so you can
        say whether it&apos;s yours. This is what stops another company&apos;s sales — and their commission —
        from landing in your reports.
        {typeof cfg.known_store_keys === 'number' && (
          <> We currently recognise <strong>{cfg.known_store_keys}</strong> names/codes/addresses as yours.</>
        )}
      </div>

      {cfg.ready === false && (
        <div style={{ background: '#fef3c7', border: '1px solid #fcd34d', borderRadius: 10, padding: '10px 16px', marginBottom: 16, fontSize: 13, color: '#92400e' }}>
          ⚠️ Not switched on yet — an administrator still needs to run the database update (migration 280).
          Imports are working normally in the meantime; nothing is being held back.
          {cfg.hint ? <div style={{ marginTop: 4, fontSize: 11, opacity: .8 }}>{cfg.hint}</div> : null}
        </div>
      )}

      {/* ── Mode ─────────────────────────────────────────────────────────── */}
      <div className="card" style={{ padding: 16, marginBottom: 18 }}>
        <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 10 }}>What should we do when we see a store we don&apos;t know?</div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          {modes.map(m => (
            <label key={m.value}
              style={{
                flex: '1 1 220px', minWidth: 220, border: `2px solid ${mode === m.value ? 'var(--primary, #2563eb)' : 'var(--border)'}`,
                borderRadius: 10, padding: 12, cursor: 'pointer',
                background: mode === m.value ? 'rgba(37,99,235,.06)' : 'transparent',
              }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 600, fontSize: 13 }}>
                <input type="radio" name="guardmode" checked={mode === m.value} disabled={saving}
                  onChange={() => saveCfg({ mode: m.value })} />
                {m.label}
              </div>
              <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 5 }}>{m.help}</div>
            </label>
          ))}
        </div>
        {mode === 'block' && (
          <div style={{ marginTop: 12, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', fontSize: 13 }}>
            <span>Don&apos;t hold back a store if the file has more than</span>
            <input className="input" type="number" style={{ width: 90 }} defaultValue={cfg.block_min_rows ?? 0}
              onBlur={e => saveCfg({ block_min_rows: parseInt(e.target.value || '0', 10) })} />
            <span>lines for it (0 = always hold back).</span>
            <span style={{ color: 'var(--text3)', fontSize: 12 }}>
              A brand-new store opening usually arrives as a whole day of sales; a file filed under the wrong
              company is usually a handful of lines.
            </span>
          </div>
        )}
        <div style={{ marginTop: 12, fontSize: 13 }}>
          <label style={{ display: 'flex', gap: 7, alignItems: 'center' }}>
            <input type="checkbox" checked={cfg.allow_creates_alias !== false} disabled={saving}
              onChange={e => saveCfg({ allow_creates_alias: e.target.checked })} />
            When I say a store is mine, remember the spelling so it&apos;s never queried again.
          </label>
        </div>
        {msg && <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text2)' }}>{msg}</div>}
      </div>

      {/* ── Queue ────────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>Show:</span>
        {['pending', 'allowed', 'rejected', 'released', 'all'].map(s => (
          <button key={s} className={`btn${status === s ? ' btn-primary' : ''}`} onClick={() => setStatus(s)}
            style={{ textTransform: 'capitalize' }}>{s}</button>
        ))}
        <button className="btn" onClick={load}>↻ Refresh</button>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>Loading…</div>
      ) : queueHint ? (
        <div className="card" style={{ padding: 24, color: 'var(--text3)', fontSize: 13 }}>{queueHint}</div>
      ) : items.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 36, color: 'var(--text3)' }}>
          {status === 'pending'
            ? '✅ Nothing to review — every store on every import so far was one of yours.'
            : 'Nothing here.'}
        </div>
      ) : (
        <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 900 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
                {['Store name on the file', 'Where it came from', 'Lines', 'Value', 'Held back', 'Status', ''].map(h =>
                  <th key={h} style={th}>{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {items.map((it, i) => (
                <tr key={it.id} style={{ borderBottom: '1px solid var(--border)', background: i % 2 ? 'var(--surface2)' : 'transparent' }}>
                  <td style={td}>
                    <div style={{ fontWeight: 600 }}>{it.store_raw}</div>
                    <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                      {it.period || '—'}{it.filename ? ` · ${it.filename}` : ''}
                      {it.sample ? (
                        <button className="btn" style={{ marginLeft: 8, padding: '1px 6px', fontSize: 11 }}
                          onClick={() => setExpanded(e => ({ ...e, [it.id]: !e[it.id] }))}>
                          {expanded[it.id] ? 'hide lines' : 'see lines'}
                        </button>
                      ) : null}
                    </div>
                    {expanded[it.id] && (
                      <pre style={{ fontSize: 10, background: 'var(--surface2)', padding: 8, borderRadius: 6, marginTop: 6, maxHeight: 220, overflow: 'auto' }}>
                        {JSON.stringify(it.sample, null, 1)}
                      </pre>
                    )}
                  </td>
                  <td style={td}>
                    <div>{it.source || '—'}</div>
                    <div style={{ fontSize: 11, color: 'var(--text3)' }}>{it.target_table}</div>
                  </td>
                  <td style={td}>{it.rows_seen}</td>
                  <td style={td}>{money(it.amount_seen)}</td>
                  <td style={td}>
                    {it.rows_withheld > 0
                      ? <span style={{ color: '#b91c1c', fontWeight: 600 }}>{it.rows_withheld}</span>
                      : <span style={{ color: 'var(--text3)' }}>none — imported</span>}
                  </td>
                  <td style={td}>
                    <span style={{ textTransform: 'capitalize' }}>{it.status}</span>
                    {it.decided_by && <div style={{ fontSize: 11, color: 'var(--text3)' }}>by {it.decided_by}</div>}
                  </td>
                  <td style={td}>
                    {it.status === 'pending' ? (
                      <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                        <select className="input" style={{ width: 190, fontSize: 12 }}
                          value={pickCode[it.id] || ''}
                          onChange={e => setPickCode(p => ({ ...p, [it.id]: e.target.value }))}>
                          <option value="">It&apos;s mine — which store?</option>
                          {stores.map(s => (
                            <option key={s.store_code} value={s.store_code}>
                              {s.store_address || s.store_code} ({s.store_code})
                            </option>
                          ))}
                        </select>
                        <button className="btn btn-primary" disabled={busyId === it.id}
                          onClick={() => decide(it, 'allow')}>
                          {busyId === it.id ? '…' : 'This is mine'}
                        </button>
                        <button className="btn" disabled={busyId === it.id}
                          title="Not our store. Nothing gets imported — and nothing already saved is deleted."
                          onClick={() => decide(it, 'reject')}>Not mine</button>
                      </div>
                    ) : (
                      <span style={{ fontSize: 11, color: 'var(--text3)' }}>
                        {it.decided_at ? new Date(it.decided_at).toLocaleString() : ''}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div style={{ marginTop: 14, fontSize: 12, color: 'var(--text3)' }}>
        Saying <strong>Not mine</strong> never deletes anything that is already in your reports — it only
        stops the held-back lines from being imported. To remove data that has already landed in the wrong
        company, ask an administrator: that is a separate, checked process.
      </div>
    </div>
  )
}
