'use client'
// Vision — SETTINGS. The one page that answers "why is nothing being recorded", in the order the
// gates actually bind: deployment switch → company switch → Google link → cameras → analyzers →
// consent. Each section states what it controls and what turning it on means.
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { panel, btn, btnPrimary, cell, th, cameraName, fmtDateTime, type Camera, type VisionConfig } from '@/lib/vision'

export default function VisionSettingsPage() {
  const [status, setStatus] = useState<any>(null)
  const [cameras, setCameras] = useState<Camera[]>([])
  const [consent, setConsent] = useState<any[]>([])
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [newSecret, setNewSecret] = useState<{ agent_key: string; secret: string } | null>(null)

  const load = useCallback(async () => {
    setErr('')
    try {
      const [s, c] = await Promise.all([
        api('/api/v1/vision/status'),
        api('/api/v1/vision/cameras'),
      ])
      setStatus(s); setCameras(c.cameras || [])
      try { setConsent((await api('/api/v1/vision/consent')).consent || []) } catch { setConsent([]) }
    } catch (e: any) { setErr(e?.message || String(e)) }
  }, [])

  useEffect(() => { void load() }, [load])

  async function act(fn: () => Promise<any>, ok: string) {
    setBusy(true); setMsg(''); setErr('')
    try { await fn(); setMsg(ok); await load() }
    catch (e: any) { setErr(e?.message || String(e)) }
    finally { setBusy(false) }
  }

  const saveConfig = (patch: Partial<VisionConfig>) => act(
    () => api('/api/v1/vision/config', { method: 'PUT', body: JSON.stringify(patch) }),
    'Saved.')

  if (!status) return <div style={{ padding: 20, color: 'var(--text2)' }}>{err || 'Loading…'}</div>

  const cfg: VisionConfig = status.config
  const canEdit: boolean = status.can_edit

  return (
    <div style={{ padding: 20, maxWidth: 1000 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700 }}>⚙️ Vision Settings</h1>
        <Link href="/vision" style={{ ...btn, textDecoration: 'none' }}>📹 Live Cameras</Link>
      </div>

      {msg && <div style={{ ...panel, marginBottom: 12, borderLeft: '3px solid #16a34a', fontSize: 13 }}>{msg}</div>}
      {err && <div style={{ ...panel, marginBottom: 12, borderColor: '#dc2626', color: '#dc2626', fontSize: 13 }}>{err}</div>}
      {!canEdit && (
        <div style={{ ...panel, marginBottom: 12, fontSize: 13, color: 'var(--text2)' }}>
          You can view this page but not change it. Editing needs a company-wide role or the
          <b> vision</b> settings permission.
        </div>
      )}

      {!cfg.available && (
        <Section title="Not installed">
          <div style={{ fontSize: 13.5, color: 'var(--text2)' }}>
            Migration <code>900_vision_camera_analytics.sql</code> has not been run on this database.
            Until it is, this module stores nothing and every switch below is inert.
          </div>
        </Section>
      )}

      {/* 1. Master switch */}
      <Section title="1 · Company master switch"
        note="Off by default for every company. Off here means off everywhere — no live view, no counting, no transcripts.">
        <Toggle label="Camera analytics enabled" checked={cfg.enabled} disabled={!canEdit || !cfg.available}
          onChange={v => saveConfig({ enabled: v })} />
        {cfg.enabled_at && (
          <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 6 }}>
            Turned on {fmtDateTime(cfg.enabled_at)} by {cfg.enabled_by || 'unknown'}
          </div>
        )}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(230px,1fr))', gap: 8, marginTop: 12 }}>
          <Toggle label="Live view" checked={cfg.live_view_enabled} disabled={!canEdit || !cfg.enabled}
            onChange={v => saveConfig({ live_view_enabled: v })} hint="Watching a camera from this app" />
          <Toggle label="Customers in / out" checked={cfg.traffic_enabled} disabled={!canEdit || !cfg.enabled}
            onChange={v => saveConfig({ traffic_enabled: v })} hint="Door counting" />
          <Toggle label="Heat map" checked={cfg.heatmap_enabled} disabled={!canEdit || !cfg.enabled}
            onChange={v => saveConfig({ heatmap_enabled: v })} hint="Where people stand" />
        </div>
      </Section>

      {/* 2. Voice — the one with legal weight */}
      <Section title="2 · Voice transcripts & coaching"
        note="Separate from everything above, and off by default. This is the switch that starts recording what your staff say at work.">
        {cfg.audio_kill_switch ? (
          <div style={{ fontSize: 13.5, color: 'var(--text2)' }}>
            🔒 <b>Disabled for this deployment.</b> Voice capture is switched off at the server
            (<code>VISION_AUDIO_ENABLED</code> is not set), so it is off for every company regardless of
            the settings here. Turning it on is a deliberate server change, not a checkbox — most of the
            states these stores operate in require every party to a recorded conversation to consent.
          </div>
        ) : (
          <>
            <Toggle label="Capture voice transcripts" checked={cfg.audio_analytics_enabled}
              disabled={!canEdit || !cfg.enabled} onChange={v => saveConfig({ audio_analytics_enabled: v })}
              hint="Employee speech only — the customer's half is discarded at ingest and never stored" />
            <Toggle label="Score behaviour from transcripts" checked={cfg.behavior_scoring_enabled}
              disabled={!canEdit || !cfg.enabled} onChange={v => saveConfig({ behavior_scoring_enabled: v })}
              hint="Coaching numbers. Never used in any pay calculation." />
            <div style={{ marginTop: 10, fontSize: 13 }}>
              <span style={{ color: 'var(--text2)' }}>Consent policy: </span>
              <select value={cfg.audio_consent_mode} disabled={!canEdit}
                onChange={e => saveConfig({ audio_consent_mode: e.target.value as any })}
                style={{ padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)' }}>
                <option value="required">Require a signed consent record per employee (recommended)</option>
                <option value="off">We hold our own signed releases outside this system</option>
              </select>
            </div>
            <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 6 }}>
              An employee who has <b>declined</b> or <b>withdrawn</b> is never recorded, under either
              policy. Choosing the second option is written to the audit log with your name.
            </div>
          </>
        )}
      </Section>

      {/* 3. Google */}
      <Section title="3 · Google (Nest) connection"
        note="Store cameras are reached through Google's Smart Device Management API. One Device Access project per company; the account that owns the cameras authorizes it once.">
        <div style={{ fontSize: 13.5, marginBottom: 10 }}>
          Status: <b style={{ color: status.google.linked ? '#16a34a' : '#f39c12' }}>
            {status.google.linked ? 'Connected' : 'Not connected'}
          </b>
          {status.google.project_id && <span style={{ color: 'var(--text3)' }}> · project {status.google.project_id}</span>}
          {status.google.last_error && (
            <div style={{ color: '#dc2626', fontSize: 12.5, marginTop: 6 }}>{status.google.last_error}</div>
          )}
        </div>
        <GoogleLink canEdit={canEdit} linked={status.google.linked} onDone={load} />
        <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button style={btnPrimary} disabled={!canEdit || !status.google.linked || busy}
            onClick={() => act(() => api('/api/v1/vision/cameras/sync', { method: 'POST' }), 'Cameras synced.')}>
            Sync cameras from Google
          </button>
          {status.google.linked && (
            <button style={btn} disabled={!canEdit || busy}
              onClick={() => act(() => api('/api/v1/vision/google/link', { method: 'DELETE' }), 'Disconnected.')}>
              Disconnect
            </button>
          )}
        </div>
      </Section>

      {/* 4. Cameras */}
      <Section title="4 · Cameras"
        note="A newly synced camera contributes nothing until you assign it to a store. Mark exactly one camera per store as the entrance — that is the one that counts customers in and out.">
        {cameras.length === 0 ? (
          <div style={{ fontSize: 13.5, color: 'var(--text2)' }}>No cameras yet — connect Google and sync.</div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 720 }}>
              <thead>
                <tr>
                  <th style={th}>Camera</th><th style={th}>Store</th><th style={th}>Analytics</th>
                  <th style={th}>Entrance</th><th style={th}>Audio</th><th style={th}>Status</th>
                </tr>
              </thead>
              <tbody>
                {cameras.map(c => (
                  <tr key={c.id}>
                    <td style={{ ...cell, fontWeight: 600 }}>
                      {cameraName(c)}
                      <div style={{ fontSize: 11, color: 'var(--text3)', fontWeight: 400 }}>
                        {c.stream_protocol.toUpperCase()}{c.room ? ` · ${c.room}` : ''}
                      </div>
                    </td>
                    <td style={cell}>
                      <input defaultValue={c.store_code || ''} placeholder="store code" disabled={!canEdit}
                        onBlur={e => e.target.value !== (c.store_code || '') && act(
                          () => api(`/api/v1/vision/cameras/${c.id}`, { method: 'PATCH', body: JSON.stringify({ store_code: e.target.value }) }),
                          'Camera updated.')}
                        style={{ width: 100, padding: '4px 7px', borderRadius: 5, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 12.5 }} />
                    </td>
                    <td style={cell}><Check checked={c.analytics_enabled} disabled={!canEdit}
                      onChange={v => act(() => api(`/api/v1/vision/cameras/${c.id}`, { method: 'PATCH', body: JSON.stringify({ analytics_enabled: v }) }), 'Camera updated.')} /></td>
                    <td style={cell}><Check checked={c.is_entrance} disabled={!canEdit}
                      onChange={v => act(() => api(`/api/v1/vision/cameras/${c.id}`, { method: 'PATCH', body: JSON.stringify({ is_entrance: v }) }), 'Camera updated.')} /></td>
                    <td style={cell}><Check checked={c.audio_enabled} disabled={!canEdit || !c.supports_audio || cfg.audio_kill_switch}
                      onChange={v => act(() => api(`/api/v1/vision/cameras/${c.id}`, { method: 'PATCH', body: JSON.stringify({ audio_enabled: v }) }), 'Camera updated.')} /></td>
                    <td style={{ ...cell, color: c.status === 'online' ? '#16a34a' : 'var(--text3)' }}>{c.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {/* 5. Analyzers */}
      <Section title="5 · Edge analyzers"
        note="Video is never processed on our servers. A small box in each store holds the live feed and posts only derived numbers. Register one per store; the signing secret is shown once.">
        <div style={{ fontSize: 13.5, marginBottom: 10 }}>
          {status.edge_agents.total} registered · <b style={{ color: status.edge_agents.online ? '#16a34a' : '#f39c12' }}>
            {status.edge_agents.online} online</b>
          {status.edge_agents.last_ingest_at && <span style={{ color: 'var(--text3)' }}> · last data {fmtDateTime(status.edge_agents.last_ingest_at)}</span>}
        </div>
        <NewAgent canEdit={canEdit} onCreated={s => { setNewSecret(s); void load() }} />
        {newSecret && (
          <div style={{ ...panel, marginTop: 10, borderLeft: '3px solid #f39c12' }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 6 }}>Copy this now — it cannot be shown again</div>
            <code style={{ display: 'block', fontSize: 12, wordBreak: 'break-all', marginBottom: 8 }}>
              --agent-key {newSecret.agent_key} --secret {newSecret.secret}
            </code>
            <div style={{ fontSize: 11.5, color: 'var(--text3)' }}>
              Run the analyzer with: <code>python3 backend/vision_edge_analyzer.py --api &lt;api url&gt; --agent-key … --secret … --tz-offset &lt;store offset in minutes&gt;</code>
            </div>
          </div>
        )}
      </Section>

      {/* 6. Consent */}
      <Section title="6 · Consent register"
        note="Who has agreed to have their speech transcribed. 'Not asked' is the answer that matters most, so everyone is listed — not only the people with a record.">
        <div style={{ fontSize: 13.5, marginBottom: 10 }}>
          {status.consent.signed} signed · {status.consent.declined} declined ·
          {' '}{status.consent.withdrawn} withdrawn
        </div>
        {consent.length > 0 && (
          <div style={{ maxHeight: 280, overflowY: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr><th style={th}>Employee</th><th style={th}>Store</th><th style={th}>Consent</th><th style={th}>When</th></tr></thead>
              <tbody>
                {consent.map(r => (
                  <tr key={r.employee_id}>
                    <td style={cell}>{r.name}</td>
                    <td style={cell}>{r.store_code || '—'}</td>
                    <td style={{ ...cell, color: r.status === 'signed' ? '#16a34a' : r.status === 'not_asked' ? 'var(--text3)' : '#dc2626' }}>
                      {r.status.replace('_', ' ')}
                    </td>
                    <td style={cell}>{fmtDateTime(r.signed_at || r.withdrawn_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      {/* 7. Retention */}
      <Section title="7 · Retention"
        note="Nothing is kept forever. Per-sample occupancy and transcripts expire quickly; the rolled-up aggregates carry no per-person detail and are kept for year-on-year comparison.">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(190px,1fr))', gap: 10 }}>
          <NumField label="Occupancy samples" value={cfg.presence_retention_days} disabled={!canEdit}
            onSave={v => saveConfig({ presence_retention_days: v })} />
          <NumField label="Voice transcripts" value={cfg.transcript_retention_days} disabled={!canEdit}
            onSave={v => saveConfig({ transcript_retention_days: v })} />
          <NumField label="Customer visits" value={cfg.visit_retention_days} disabled={!canEdit}
            onSave={v => saveConfig({ visit_retention_days: v })} />
          <NumField label="Heat map (aggregate)" value={cfg.heat_retention_days} disabled={!canEdit}
            onSave={v => saveConfig({ heat_retention_days: v })} />
        </div>
        <div style={{ marginTop: 10 }}>
          <button style={btn} disabled={!canEdit || busy}
            onClick={() => act(async () => {
              const p = await api('/api/v1/vision/retention/purge', { method: 'POST', body: JSON.stringify({}) })
              setMsg(`Dry run: ${p.total} row(s) are past their retention window. Purge again with confirm to delete.`)
            }, '')}>
            Preview purge
          </button>
          <button style={{ ...btn, marginLeft: 8 }} disabled={!canEdit || busy}
            onClick={() => act(() => api('/api/v1/vision/retention/purge', { method: 'POST', body: JSON.stringify({ confirm: true }) }), 'Expired data deleted.')}>
            Purge expired data
          </button>
        </div>
      </Section>
    </div>
  )
}

function GoogleLink({ canEdit, linked, onDone }: { canEdit: boolean; linked: boolean; onDone: () => void }) {
  const [project, setProject] = useState('')
  const [clientId, setClientId] = useState('')
  const [secret, setSecret] = useState('')
  const [code, setCode] = useState('')
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const redirect = typeof window !== 'undefined' ? `${window.location.origin}/vision/settings` : ''

  async function saveAndAuthorize() {
    setBusy(true); setErr('')
    try {
      await api('/api/v1/vision/google/link', {
        method: 'POST',
        body: JSON.stringify({ project_id: project, client_id: clientId, client_secret: secret }),
      })
      const r = await api(`/api/v1/vision/google/auth-url?redirect_uri=${encodeURIComponent(redirect)}`)
      setUrl(r.url)
    } catch (e: any) { setErr(e?.message || String(e)) }
    finally { setBusy(false) }
  }

  async function complete() {
    setBusy(true); setErr('')
    try {
      await api('/api/v1/vision/google/link', {
        method: 'POST',
        body: JSON.stringify({ code, redirect_uri: redirect, client_id: clientId, client_secret: secret }),
      })
      setCode(''); setUrl(''); onDone()
    } catch (e: any) { setErr(e?.message || String(e)) }
    finally { setBusy(false) }
  }

  if (linked) return <div style={{ fontSize: 12.5, color: 'var(--text3)' }}>
    The refresh token is stored encrypted and cannot be read back. To change accounts, disconnect and link again.
  </div>

  return (
    <div style={{ display: 'grid', gap: 8, maxWidth: 520 }}>
      <Text label="Device Access project id" value={project} onChange={setProject} disabled={!canEdit} />
      <Text label="OAuth client id" value={clientId} onChange={setClientId} disabled={!canEdit} />
      <Text label="OAuth client secret" value={secret} onChange={setSecret} disabled={!canEdit} type="password" />
      <div style={{ fontSize: 11.5, color: 'var(--text3)' }}>
        Add <code>{redirect}</code> as an authorized redirect URI on the OAuth client, or Google will
        refuse the consent step.
      </div>
      <div><button style={btnPrimary} onClick={saveAndAuthorize} disabled={!canEdit || busy || !project || !clientId || !secret}>
        Save & get the Google consent link
      </button></div>
      {url && (
        <div style={{ ...panel }}>
          <a href={url} target="_blank" rel="noreferrer" style={{ color: '#2563eb', fontSize: 13 }}>
            1 · Open the Google consent screen and pick the cameras to share →
          </a>
          <div style={{ marginTop: 8, display: 'flex', gap: 6 }}>
            <input placeholder="2 · paste the ?code= value from the redirect" value={code}
              onChange={e => setCode(e.target.value)} style={{ flex: 1, padding: '6px 9px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 13 }} />
            <button style={btnPrimary} onClick={complete} disabled={busy || !code}>Finish</button>
          </div>
        </div>
      )}
      {err && <div style={{ color: '#dc2626', fontSize: 12.5 }}>{err}</div>}
    </div>
  )
}

function NewAgent({ canEdit, onCreated }: { canEdit: boolean; onCreated: (s: any) => void }) {
  const [label, setLabel] = useState('')
  const [store, setStore] = useState('')
  const [busy, setBusy] = useState(false)
  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
      <input placeholder="Label (e.g. Main St analyzer)" value={label} onChange={e => setLabel(e.target.value)}
        style={{ padding: '6px 9px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 13 }} />
      <input placeholder="Store code" value={store} onChange={e => setStore(e.target.value)}
        style={{ width: 110, padding: '6px 9px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 13 }} />
      <button style={btn} disabled={!canEdit || busy || !store} onClick={async () => {
        setBusy(true)
        try { onCreated(await api('/api/v1/vision/edge-agents', { method: 'POST', body: JSON.stringify({ label, store_code: store }) })) }
        finally { setBusy(false) }
      }}>Register analyzer</button>
    </div>
  )
}

function Section({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <div style={{ ...panel, marginBottom: 14 }}>
      <div style={{ fontWeight: 700, fontSize: 15, marginBottom: note ? 4 : 10 }}>{title}</div>
      {note && <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 12 }}>{note}</div>}
      {children}
    </div>
  )
}

function Toggle({ label, checked, onChange, disabled, hint }: {
  label: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; hint?: string
}) {
  return (
    <label style={{ display: 'block', cursor: disabled ? 'default' : 'pointer', opacity: disabled ? 0.55 : 1, marginBottom: 6 }}>
      <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <input type="checkbox" checked={!!checked} disabled={disabled} onChange={e => onChange(e.target.checked)} />
        <span style={{ fontSize: 13.5, fontWeight: 500 }}>{label}</span>
      </span>
      {hint && <span style={{ fontSize: 11.5, color: 'var(--text3)', marginLeft: 24 }}>{hint}</span>}
    </label>
  )
}

function Check({ checked, onChange, disabled }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return <input type="checkbox" checked={!!checked} disabled={disabled} onChange={e => onChange(e.target.checked)} />
}

function Text({ label, value, onChange, disabled, type }: {
  label: string; value: string; onChange: (v: string) => void; disabled?: boolean; type?: string
}) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <span style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.4px', color: 'var(--text3)' }}>{label}</span>
      <input type={type || 'text'} value={value} disabled={disabled} onChange={e => onChange(e.target.value)}
        style={{ padding: '6px 9px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 13 }} />
    </label>
  )
}

function NumField({ label, value, onSave, disabled }: {
  label: string; value: number; onSave: (v: number) => void; disabled?: boolean
}) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <span style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.4px', color: 'var(--text3)' }}>{label}</span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <input type="number" min={0} defaultValue={value} disabled={disabled}
          onBlur={e => Number(e.target.value) !== value && onSave(Number(e.target.value))}
          style={{ width: 78, padding: '6px 9px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 13 }} />
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>days</span>
      </span>
    </label>
  )
}
