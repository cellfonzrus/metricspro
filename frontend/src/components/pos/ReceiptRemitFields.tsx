'use client'
// THE editor for "pay by bank transfer" — the account a customer is told to pay into (owner
// 2026-10-21, verbatim: "need to add the option of adding the payment information to the customer
// when creating the receipt for the customer as a check mark, two boxes to fill out routing number
// and account number to be sent to the customer, tehy will be saved in the register set up").
//
// WHY IT IS NOT A ROW IN THE CONFIG TABLE ABOVE. These two settings are the only ones in the POS
// registry answered MASKED by GET /pos/settings, so a plain text box would show '••••1234' and save
// that string over the real number the moment anybody pressed Save. They are also the only ones
// VALIDATED before they are stored (a routing number has a check digit; a receipt is the one place
// a wrong number is expensive) and the only ones stored encrypted. All three facts live on the
// server — `pos/receipt_remit.py` — and this screen asks it, through the one client home
// `saveReceiptRemit`. It spells no validation rule and no key of its own.
//
// The tick that puts the block on a receipt is on the PRINT screen, not here: which customer is
// paying by transfer is known at the counter. Here is only which account.
import { useState } from 'react'
import { resolvePosConfig, type PosSettingRow } from '@/lib/pos-config'
import { saveReceiptRemit } from '@/lib/pos-receipt'
import { storeLabel, type PosStore } from '@/components/pos/PosConfigSection'

const card: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10,
  marginBottom: 16, overflow: 'hidden',
}
const head: React.CSSProperties = {
  background: 'var(--surface2)', padding: '10px 16px', borderBottom: '1px solid var(--border)',
}
const label: React.CSSProperties = { fontSize: 11, color: 'var(--text3)', marginBottom: 3 }
const input: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)',
  borderRadius: 6, padding: '7px 9px', fontSize: 13, width: '100%',
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

export default function ReceiptRemitFields({ stores, rows, onSettingsChanged }: {
  stores: PosStore[]
  rows: PosSettingRow[]
  onSettingsChanged: () => Promise<void> | void
}) {
  // '' = the company default (the org row every store inherits); otherwise one store's own account.
  const [scope, setScope] = useState('')
  const [routing, setRouting] = useState('')
  const [account, setAccount] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState('')

  // What is on file now, read from the rows the page already loaded — masked, because that is all
  // the server ever answers for these two keys.
  const values = resolvePosConfig(rows, scope || null).values
  const onFileRouting = String(values.receipt_remit_routing_number || '')
  const onFileAccount = String(values.receipt_remit_account_number || '')

  const save = async (body: { routing_number?: string; account_number?: string }) => {
    setSaving(true); setError(''); setSaved('')
    try {
      const v = await saveReceiptRemit({ ...body, ...(scope ? { store_code: scope } : {}) })
      setRouting(''); setAccount('')
      setSaved(v.available
        ? (v.encryption_enabled
          ? 'Saved. Staff can now tick it on the receipt screen.'
          : 'Saved, but this server has no encryption key configured, so it is stored as plain text — tell support before you rely on it.')
        : 'Saved. A receipt carries nothing until BOTH numbers are on file.')
      await onSettingsChanged()
    } catch (err) {
      setError(message(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={card}>
      <div style={head}>
        <div style={{ fontSize: 13, fontWeight: 700 }}>🏦 Pay by bank transfer</div>
        <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>
          The account a customer is told to pay into. Staff tick a box on the receipt screen to put
          it on a receipt — it is never on one by default.
        </div>
      </div>
      <div style={{ padding: 16 }}>
        <div style={{ marginBottom: 12, maxWidth: 360 }}>
          <div style={label}>Whose account</div>
          <select value={scope} disabled={saving} style={input}
            onChange={e => { setScope(e.target.value); setError(''); setSaved('') }}>
            <option value="">Company default — every store</option>
            {stores.map(s => (
              <option key={s.store_code} value={s.store_code}>{storeLabel(s)}</option>
            ))}
          </select>
        </div>

        <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 10 }}>
          {onFileRouting || onFileAccount
            ? <>On file: routing <b>{onFileRouting || '(none)'}</b> · account <b>{onFileAccount || '(none)'}</b>. Typing a new number replaces it.</>
            : <>Nothing on file yet{scope ? ' for this store' : ''}.</>}
        </div>

        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div style={{ flex: 1, minWidth: 180 }}>
            <div style={label}>Routing number</div>
            <input style={input} value={routing} disabled={saving} inputMode="numeric"
              placeholder="9 digits" onChange={e => setRouting(e.target.value)} />
          </div>
          <div style={{ flex: 1, minWidth: 180 }}>
            <div style={label}>Account number</div>
            <input style={input} value={account} disabled={saving} inputMode="numeric"
              placeholder="4–17 digits" onChange={e => setAccount(e.target.value)} />
          </div>
          <button className="btn btn-primary" disabled={saving || !(routing.trim() || account.trim())}
            onClick={() => save({
              ...(routing.trim() ? { routing_number: routing } : {}),
              ...(account.trim() ? { account_number: account } : {}),
            })}>
            {saving ? 'Saving…' : 'Save'}
          </button>
          {(onFileRouting || onFileAccount) && (
            <button className="btn btn-secondary" disabled={saving}
              onClick={() => save({ routing_number: '', account_number: '' })}>
              Remove
            </button>
          )}
        </div>

        {error && <div style={{ fontSize: 12, color: 'var(--c-fg-dc2626)', marginTop: 10 }}>{error}</div>}
        {saved && <div style={{ fontSize: 12, color: 'var(--accent2)', marginTop: 10 }}>{saved}</div>}
      </div>
    </div>
  )
}
