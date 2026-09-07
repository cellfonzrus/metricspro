'use client'
// Marketing settings — the option lists (THE "+"), and the module switches.
//
// This screen is the whole of the owner's "none of the options I mentioned above are hard coded but
// options pre added with plus sign to add more as per user discretion" requirement: every list the
// module uses is edited here, and adding a value takes effect immediately with no deploy, no
// migration and no code change.
//
// House options are shown as such and can be RENAMED or TURNED OFF for this organisation without
// affecting anybody else — the edit writes a row for this tenant that wins over the house one.
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import {
  panel, input, label, btn, btnPrimary, th, cell, fmtMoney,
  type MarketingOption, type MarketingConfig, type OptionList,
} from '@/lib/marketing'

function OptionListEditor({ list, onChanged, highlight }: {
  list: OptionList; onChanged: () => void; highlight: boolean
}) {
  const [adding, setAdding] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  async function save(body: Record<string, unknown>) {
    setBusy(true); setMsg('')
    try { await api('/api/v1/marketing/options', { method: 'POST', body: JSON.stringify({ list_key: list.list_key, ...body }) }); onChanged() }
    catch (e: any) { setMsg(e?.message || String(e)) }
    setBusy(false)
  }

  return (
    <section style={{ ...panel, marginBottom: 14, borderColor: highlight ? '#2563eb' : undefined }}>
      <h2 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 8px' }}>{list.label}</h2>
      {msg && <div style={{ fontSize: 12, color: '#dc2626', marginBottom: 8 }}>{msg}</div>}
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead><tr>
          <th style={th}>Label</th><th style={th}>Key</th><th style={th}>Order</th>
          <th style={th}>Source</th><th style={th}>Shown</th>
        </tr></thead>
        <tbody>
          {list.options.map(o => (
            <tr key={o.key} style={{ opacity: o.is_active ? 1 : 0.5 }}>
              <td style={cell}>
                <input style={{ ...input, width: 240 }} defaultValue={o.label} disabled={busy}
                       onBlur={ev => { if (ev.target.value !== o.label) save({ key: o.key, label: ev.target.value, is_active: o.is_active, sort_order: o.sort_order }) }} />
              </td>
              <td style={{ ...cell, fontFamily: 'monospace', fontSize: 11, color: 'var(--text2)' }}>{o.key}</td>
              <td style={cell}>
                <input style={{ ...input, width: 70 }} type="number" defaultValue={o.sort_order} disabled={busy}
                       onBlur={ev => { if (Number(ev.target.value) !== o.sort_order) save({ key: o.key, label: o.label, sort_order: Number(ev.target.value), is_active: o.is_active }) }} />
              </td>
              <td style={{ ...cell, fontSize: 11, color: 'var(--text2)' }}>
                {o.source === 'house' ? 'standard' : 'yours'}
              </td>
              <td style={cell}>
                <input type="checkbox" checked={o.is_active} disabled={busy}
                       onChange={ev => save({ key: o.key, label: o.label, sort_order: o.sort_order, is_active: ev.target.checked })} />
              </td>
            </tr>
          ))}
          {!list.options.length && (
            <tr><td style={{ ...cell, color: 'var(--text2)' }} colSpan={5}>
              Nothing in this list yet — add the first one below.
            </td></tr>
          )}
        </tbody>
      </table>
      <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
        <input style={{ ...input, maxWidth: 280 }} placeholder="Add another…" value={adding}
               onChange={ev => setAdding(ev.target.value)}
               onKeyDown={ev => { if (ev.key === 'Enter' && adding.trim()) { save({ label: adding.trim() }); setAdding('') } }} />
        <button style={btnPrimary} disabled={busy || !adding.trim()}
                onClick={() => { save({ label: adding.trim() }); setAdding('') }}>＋ Add</button>
      </div>
      <p style={{ fontSize: 11, color: 'var(--text2)', marginTop: 8 }}>
        Turning a standard option off hides it from new pickers for your organisation only. Events
        that already use it keep showing its name — nothing is deleted.
      </p>
    </section>
  )
}

export default function MarketingSettings() {
  // Which list to highlight, from ?list=<list_key> (the "+" beside a picker links here). Read from
  // window.location on mount rather than useSearchParams, so this page keeps its static-render
  // behaviour and needs no Suspense boundary — the same choice /notify, /helpdesk/new and
  // /storeops/timeclock already made for the same reason.
  const [focusList, setFocusList] = useState('')
  useEffect(() => {
    try { setFocusList(new URLSearchParams(window.location.search).get('list') || '') } catch { /* SSR */ }
  }, [])
  const [lists, setLists] = useState<OptionList[]>([])
  const [cfg, setCfg] = useState<MarketingConfig | null>(null)
  const [isDefault, setIsDefault] = useState(true)
  const [retention, setRetention] = useState<any>(null)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setMsg('')
    try {
      const [o, c] = await Promise.all([
        api('/api/v1/marketing/options?include_inactive=true'),
        api('/api/v1/marketing/config'),
      ])
      setLists(o.lists || [])
      setCfg(c.config); setIsDefault(!!c.is_default)
      api('/api/v1/marketing/checkin-retention').then(setRetention).catch(() => {})
    } catch (e: any) { setMsg(e?.message || String(e)) }
  }, [])
  useEffect(() => { load() }, [load])

  async function saveCfg(patch: Partial<MarketingConfig>) {
    setBusy(true); setMsg('')
    try { const r = await api('/api/v1/marketing/config', { method: 'PUT', body: JSON.stringify(patch) }); setCfg(r.config); setIsDefault(!!r.is_default) }
    catch (e: any) { setMsg(e?.message || String(e)) }
    setBusy(false)
  }

  return (
    <div style={{ padding: 20, maxWidth: 1000 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
        <Link href="/marketing" style={{ fontSize: 13, color: 'var(--text2)' }}>← Marketing</Link>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Marketing settings</h1>
      </div>
      {msg && <div style={{ ...panel, marginBottom: 14, borderColor: '#dc2626', color: '#dc2626' }}>{msg}</div>}

      <section style={{ ...panel, marginBottom: 14 }}>
        <h2 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 8px' }}>Event approval</h2>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}>
          <input type="checkbox" checked={!!cfg?.approval_required} disabled={busy}
                 onChange={ev => saveCfg({ approval_required: ev.target.checked })} />
          Require an event to be approved before it can go live
        </label>
        <p style={{ fontSize: 12, color: 'var(--text2)', margin: '6px 0 0' }}>
          {isDefault
            ? 'Off — this is the default and nobody has changed it. Events go live without an approval step.'
            : cfg?.approval_required
              ? 'On. Events cannot go live until approved by a market-wide or company-wide role.'
              : 'Off. Events go live without an approval step.'}
        </p>
        {cfg?.approval_required && (
          <div style={{ marginTop: 10, maxWidth: 280 }}>
            <span style={label}>Only require approval above this planned spend (blank = always)</span>
            <input style={input} type="number" defaultValue={cfg.approval_spend_threshold ?? ''} disabled={busy}
                   onBlur={ev => saveCfg({ approval_spend_threshold: ev.target.value === '' ? null : Number(ev.target.value) })} />
            <p style={{ fontSize: 12, color: 'var(--text2)', marginTop: 6 }}>
              {cfg.approval_spend_threshold != null
                ? `Events planned at ${fmtMoney(cfg.approval_spend_threshold)} or less go live without approval.`
                : 'Every event needs approval.'}
            </p>
          </div>
        )}
      </section>

      <section style={{ ...panel, marginBottom: 14 }}>
        <h2 style={{ fontSize: 15, fontWeight: 700, margin: '0 0 2px' }}>Check-in &amp; employee location</h2>
        <p style={{ fontSize: 12, color: 'var(--text2)', margin: '0 0 10px', lineHeight: 1.5 }}>
          A staff check-in takes ONE location reading at the moment the person presses the button. Nothing
          is recorded between check-ins, checking out records only the time, and every person can see
          exactly what was stored about them on their own &ldquo;My check-ins&rdquo; page.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 12 }}>
          <div>
            <span style={label}>Default check-in radius (metres)</span>
            <input style={input} type="number" defaultValue={cfg?.default_checkin_radius_m ?? 150} disabled={busy}
                   onBlur={ev => saveCfg({ default_checkin_radius_m: Number(ev.target.value) })} />
          </div>
          <div>
            <span style={label}>Ignore fixes less accurate than (metres)</span>
            <input style={input} type="number" defaultValue={cfg?.max_checkin_accuracy_m ?? 200} disabled={busy}
                   onBlur={ev => saveCfg({ max_checkin_accuracy_m: Number(ev.target.value) })} />
          </div>
          <div>
            <span style={label}>Keep check-in locations for (days)</span>
            <input style={input} type="number" defaultValue={cfg?.checkin_geo_retention_days ?? 180} disabled={busy}
                   onBlur={ev => saveCfg({ checkin_geo_retention_days: Number(ev.target.value) })} />
          </div>
          <div>
            <span style={label}>Warn about events this far ahead (hours)</span>
            <input style={input} type="number" defaultValue={cfg?.staffing_alert_lead_hours ?? 48} disabled={busy}
                   onBlur={ev => saveCfg({ staffing_alert_lead_hours: Number(ev.target.value) })} />
          </div>
        </div>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, marginTop: 12 }}>
          <input type="checkbox" checked={!!cfg?.block_checkin_outside_fence} disabled={busy}
                 onChange={ev => saveCfg({ block_checkin_outside_fence: ev.target.checked })} />
          Refuse a check-in taken away from the event location
        </label>
        <p style={{ fontSize: 12, color: 'var(--text2)', margin: '6px 0 0' }}>
          Off by default. With it off, a check-in from the wrong place is still recorded and flagged for
          the event lead — which is usually what you want, because a phone with a poor signal is far more
          common than someone pretending to be somewhere they are not.
        </p>
        {retention && (
          <p style={{ fontSize: 12, color: retention.due_for_purge ? '#f39c12' : 'var(--text2)', marginTop: 10 }}>
            {retention.total} check-in record(s) stored · {retention.due_for_purge} past their retention date.
            {' '}{retention.note}
          </p>
        )}
      </section>

      <h2 style={{ fontSize: 17, fontWeight: 700, margin: '20px 0 10px' }}>Option lists</h2>
      <p style={{ fontSize: 12, color: 'var(--text2)', margin: '0 0 12px' }}>
        Everything an event form offers is edited here. Add as many as you like — nothing needs a release.
      </p>
      {lists.map(l => (
        <OptionListEditor key={l.list_key} list={l} onChanged={load} highlight={l.list_key === focusList} />
      ))}
    </div>
  )
}
