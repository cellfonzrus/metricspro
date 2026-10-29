'use client'
// THE one way a receipt is SENT to a customer (§POS-09).
//
// Owner 2026-10-21, verbatim: *"i need the emial option in sales log viewer and the send notification
// option via whats app also"*.
//
// WHAT WAS WRONG, AS A CLASS. "Send this receipt to the customer" lived inline in the till's
// completion modal, keyed to the sale that had just been rung. So the Sale Log — where a counter
// opens a receipt from yesterday, or one rung at another till — could only PRINT. The class is not
// "the Sale Log is missing a button": it is that sending a receipt had no home of its own, which is
// also why adding a second channel would otherwise have meant adding it twice and letting the two
// screens drift.
//
// So both screens mount THIS, and a channel added here reaches every screen at once. It decides
// nothing: the server says who the receipt would go to on each channel, whether it can be sent, and
// what the customer will see it come from (`SaleReceipt.email` / `.whatsapp`), and this asks.
import { useState } from 'react'
import { emailSaleReceipt, whatsappSaleReceipt, type PrintChoice, type SaleReceipt } from '@/lib/pos-receipt'

/** The server's own sentence where there is one — it already says what to do about the failure. */
function message(err: unknown): string {
  if (err instanceof Error) return err.message
  const m = (err as { message?: unknown })?.message
  return typeof m === 'string' && m ? m : String(err)
}

export function SendReceiptPanel({ saleId, view, choice, onEmailSent }: {
  saleId: string
  /** The receipt the screen is showing — the same answer the print button uses. */
  view: SaleReceipt
  /** What this copy asked for, so the copy sent is the copy that was handed over (§POS-03). */
  choice?: PrintChoice
  /** The till keeps an address the customer gave at the counter on their profile; the Sale Log
   *  does not pass this, because a reprint is not where a profile is edited. */
  onEmailSent?: (address: string) => void
}) {
  // The address and number START as the server's answer for THIS sale and are the cashier's to edit
  // from there. Both screens mount this with `key={saleId}`, so opening a different sale mounts a
  // fresh panel rather than carrying one customer's address onto another's receipt — which is why
  // there is no effect here copying a prop into state after the fact.
  const [email, setEmail] = useState(view.email.to)
  const [phone, setPhone] = useState(view.whatsapp.to)
  const [busy, setBusy] = useState<'' | 'email' | 'whatsapp'>('')
  const [said, setSaid] = useState('')

  async function send(channel: 'email' | 'whatsapp') {
    setBusy(channel)
    setSaid('')
    try {
      if (channel === 'email') {
        const res = await emailSaleReceipt(saleId, email, choice)
        setSaid(res.message || `Sent to ${res.to}.`)
        if (email.trim()) onEmailSent?.(email.trim())
      } else {
        const res = await whatsappSaleReceipt(saleId, phone, choice)
        setSaid(res.message || `Sent to ${res.to} on WhatsApp.`)
      }
    } catch (err) {
      setSaid(`Could not send it: ${message(err)}`)
    }
    setBusy('')
  }

  const box: React.CSSProperties = {
    background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 8,
    padding: 12, marginBottom: 12, textAlign: 'left',
  }
  const input: React.CSSProperties = {
    flex: 1, minWidth: 0, background: 'var(--surface)', border: '1px solid var(--border)',
    borderRadius: 6, padding: '7px 9px', fontSize: 13, color: 'var(--text)',
  }
  const label: React.CSSProperties = { fontSize: 12, color: 'var(--text2)', marginBottom: 6 }
  const note: React.CSSProperties = { fontSize: 11, color: 'var(--text3)', marginTop: 6 }

  return (
    <div style={box}>
      <div style={label}>Email the receipt to:</div>
      <div style={{ display: 'flex', gap: 6 }}>
        <input type="email" value={email} placeholder="name@example.com" style={input}
          onChange={e => { setEmail(e.target.value); setSaid('') }}
          onKeyDown={e => { if (e.key === 'Enter' && !busy && email.trim()) send('email') }} />
        <button className="btn btn-primary" disabled={!!busy || !email.trim()}
          style={{ fontSize: 12, padding: '7px 12px', opacity: busy || !email.trim() ? 0.6 : 1 }}
          onClick={() => send('email')}>
          {busy === 'email' ? 'Sending…' : 'Send'}
        </button>
      </div>

      {/* The other channel, same question, same answer shape — so a customer who has given a phone
          number and no address can still be handed their receipt. */}
      <div style={{ ...label, marginTop: 12 }}>Or send it on WhatsApp to:</div>
      <div style={{ display: 'flex', gap: 6 }}>
        <input type="tel" value={phone} placeholder="+1 212 555 0123" style={input}
          onChange={e => { setPhone(e.target.value); setSaid('') }}
          onKeyDown={e => { if (e.key === 'Enter' && !busy && phone.trim()) send('whatsapp') }} />
        <button className="btn btn-secondary" disabled={!!busy || !phone.trim()}
          style={{ fontSize: 12, padding: '7px 12px', opacity: busy || !phone.trim() ? 0.6 : 1 }}
          onClick={() => send('whatsapp')}>
          {busy === 'whatsapp' ? 'Sending…' : 'Send'}
        </button>
      </div>

      {/* The Bill To (§POS-11). The owner asked for the customer's address on the receipt; it prints
          whenever their record holds one, and most records hold none. The receipt itself never says
          so — a customer cannot act on it — but the cashier standing here can, so the server's one
          sentence is shown to them. */}
      {!view.bill_to.on_file && view.bill_to.note && (
        <div style={{ ...note, color: 'var(--text2)' }}>{view.bill_to.note}</div>
      )}

      <div style={note}>
        {said
          ? said
          : view.email.from_name
            ? `The customer sees it from ${view.email.from_name}, with the receipt attached as a PDF.`
            : 'No company name is set yet, so it arrives with the platform name — set one in POS → Settings → Receipt Template.'}
      </div>
    </div>
  )
}
