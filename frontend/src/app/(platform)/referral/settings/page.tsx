'use client'
// Referral Settings — every knob of the program (money defaults + anti-fraud thresholds). Nothing about
// the program is hard-coded (RULE TWO): these back the backend referral_config row. Permission-gated —
// the page loads read-only for anyone without the 'referral' settings grant / a company-wide role.
import { useEffect, useState } from 'react'
import { api } from '@/lib/client'
import { panel, input, label, btnPrimary, type ReferralConfig } from '@/lib/referral'

export default function ReferralSettingsPage() {
  const [cfg, setCfg] = useState<ReferralConfig | null>(null)
  const [f, setF] = useState<any>({})
  const [msg, setMsg] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    (async () => {
      try { const c = await api('/api/v1/referral/config'); setCfg(c); setF(c) }
      catch (e: any) { setMsg(e?.message || String(e)) }
    })()
  }, [])

  const set = (k: string, v: any) => setF((p: any) => ({ ...p, [k]: v }))

  async function save() {
    setSaving(true); setMsg('')
    const payload = {
      default_commission_amount: Number(f.default_commission_amount) || 0,
      default_payout_offset_days: Number(f.default_payout_offset_days) || 0,
      qr_expiry_hours: Number(f.qr_expiry_hours) || 0,
      redemption_window_hours: Number(f.redemption_window_hours) || 0,
      max_referrals_per_referrer: Number(f.max_referrals_per_referrer) || 0,
      velocity_window_days: Number(f.velocity_window_days) || 0,
      duplicate_match: f.duplicate_match,
      require_approval: !!f.require_approval,
      self_referral_block: !!f.self_referral_block,
    }
    try { const c = await api('/api/v1/referral/config', { method: 'PUT', body: JSON.stringify(payload) }); setCfg(c); setF(c); setMsg('Saved ✓') }
    catch (e: any) { setMsg(e?.message || String(e)) }
    setSaving(false)
  }

  if (!cfg) return <div style={{ padding: 20, color: 'var(--text2)' }}>{msg || 'Loading…'}</div>
  const ro = !cfg.can_edit

  return (
    <div style={{ padding: 20, maxWidth: 720 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>⚙️ Referral Settings</h1>
      <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 14 }}>
        {ro ? 'Read-only — you need the referral settings permission to change these.' : 'Tune the reward and the anti-fraud guards. Changes apply to new referrals.'}
      </div>
      {!cfg.qr_signing_configured && (
        <div style={{ ...panel, borderColor: '#f39c12', marginBottom: 14, fontSize: 13 }}>
          ⚠️ No QR signing secret is configured on the server, so scannable QRs can't be minted yet.
        </div>
      )}

      <div style={{ ...panel, marginBottom: 14 }}>
        <div style={{ fontWeight: 600, marginBottom: 10 }}>Reward defaults</div>
        <Grid>
          <Num label="Default commission ($)" k="default_commission_amount" f={f} set={set} ro={ro} />
          <Num label="Payout offset (days after approval)" k="default_payout_offset_days" f={f} set={set} ro={ro} />
        </Grid>
      </div>

      <div style={{ ...panel, marginBottom: 14 }}>
        <div style={{ fontWeight: 600, marginBottom: 10 }}>QR lifetime</div>
        <Grid>
          <Num label="QR expiry (hours)" k="qr_expiry_hours" f={f} set={set} ro={ro} />
          <Num label="Redemption window (hours)" k="redemption_window_hours" f={f} set={set} ro={ro} />
        </Grid>
        <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 6 }}>The stricter (shorter) of the two is the real deadline to redeem.</div>
      </div>

      <div style={{ ...panel, marginBottom: 14 }}>
        <div style={{ fontWeight: 600, marginBottom: 10 }}>Anti-fraud guards</div>
        <Grid>
          <Num label="Max referrals per referrer" k="max_referrals_per_referrer" f={f} set={set} ro={ro} help="0 = no cap" />
          <Num label="Velocity window (days)" k="velocity_window_days" f={f} set={set} ro={ro} />
        </Grid>
        <div style={{ marginTop: 10 }}>
          <span style={label}>Duplicate matching</span>
          <select value={f.duplicate_match} onChange={e => set('duplicate_match', e.target.value)} disabled={ro} style={{ ...input, width: 'auto' }}>
            <option value="phone">Block a phone already a customer / on an open referral</option>
            <option value="none">Off — allow any phone</option>
          </select>
        </div>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, marginTop: 12 }}>
          <input type="checkbox" checked={!!f.self_referral_block} onChange={e => set('self_referral_block', e.target.checked)} disabled={ro} />
          Block self-referral (referrer phone = customer phone)
        </label>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, marginTop: 8 }}>
          <input type="checkbox" checked={!!f.require_approval} onChange={e => set('require_approval', e.target.checked)} disabled={ro} />
          Require an explicit approval before a payout (recommended)
        </label>
      </div>

      {msg && <div style={{ ...panel, borderColor: msg.includes('✓') ? '#16a34a' : '#dc2626', color: msg.includes('✓') ? '#16a34a' : '#dc2626', marginBottom: 12 }}>{msg}</div>}
      {!ro && <button onClick={save} disabled={saving} style={btnPrimary}>{saving ? 'Saving…' : 'Save settings'}</button>}
    </div>
  )
}

function Grid({ children }: { children: any }) {
  return <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))', gap: 12 }}>{children}</div>
}
function Num({ label: lbl, k, f, set, ro, help }: any) {
  return (
    <div>
      <span style={label}>{lbl}</span>
      <input value={f[k] ?? ''} onChange={e => set(k, e.target.value)} inputMode="decimal" disabled={ro} style={input} />
      {help && <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 3 }}>{help}</div>}
    </div>
  )
}
