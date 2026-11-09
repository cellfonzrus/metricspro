'use client'
// FIX IT — the ONE home for what a "this isn't set up" warning LOOKS LIKE and what pressing it does
// (owner directive 2026-11-09, index §CORE-39).
//
// Owner, verbatim: *"to fix any such problem going forward shpow it on the screen hpow the user can
// fix teh problem so it is taken care of right away and if it is not under tehoer role permissionsm,
// have them send a link ot thier manager from the chat menu so it can be fixed"*.
//
// THE DEFECT THIS REPLACES. The instance was a financing row reading "⚠️ Detection not configured"
// with nothing to press. The CLASS is that dozens of surfaces in this app say "not configured" /
// "not measured" / "needs setup" — every one of them correct, every one of them a dead end for the
// person reading it. The reader is usually not the person who can close the gap, and nothing on the
// screen told them who is.
//
// THE RULES THIS FILE ENCODES
//   • THE WORDS ARE THE SERVER'S. Every sentence here comes from `app/modules/core/setup_fix.py` and
//     is rendered as it arrived. No remedy is spelled a second time in the browser — that is the
//     divergence the house rules forbid, and `harness_setup_fix.py` fails the build on a step
//     literal appearing in `frontend/src`.
//   • THE STEPS SHOW EITHER WAY. Somebody who may not press the button can still read what is needed
//     and say it out loud. A refused viewer is informed, not stonewalled.
//   • A BUTTON THAT WOULD BE REFUSED IS NEVER OFFERED. `can_fix` is the SERVER's answer, from the
//     same check the write endpoint refuses on, so the control and the save cannot disagree. This
//     file decides no permission; it has no copy of one to get wrong.
//   • ASKING REUSES THE EXISTING CHAT PATH. "Send to my manager" opens (or re-opens) the DM through
//     `POST /chat/dm` and posts through `POST /chat/channels/{id}/messages` — the app's one send
//     path, called from here. No second messaging route exists to drift from it.
//   • A FAILED SEND SAYS SO. A 2xx is not proof (index §19.37, the same lesson): the confirmation
//     names the conversation the message actually landed in, and a failure says it was not sent.
import { useCallback, useEffect, useState } from 'react'
import { api } from '@/lib/client'

export type FixRecipient = { employee_id: string; name: string; role?: string | null; is_manager?: boolean }
export type FixGuide = {
  key: string
  title: string
  consequence: string
  steps: string[]
  /** The page that closes the gap — null when no tenant can close it. */
  page: string | null
  /** THE SERVER's answer to "may this viewer close it". Never recomputed here. */
  can_fix: boolean
  gate: string
  ask: { recipients: FixRecipient[]; message: string } | null
  /** Nobody in this company can close it: ours to do, or no role here holds the permission. */
  support: boolean
}

/** What the one action on this notice says. '' when there is no action to offer. */
export function fixActionLabel(fix: FixGuide | null | undefined): string {
  if (!fix) return ''
  if (fix.can_fix && fix.page) return 'Fix this now'
  const who = fix.ask?.recipients?.[0]?.name
  return who ? `Ask ${who} to fix this` : ''
}

/** Send the guide's own message to the people who can close the gap, through the app's one chat
 *  path. Resolves with the conversation it used so the caller can link the asker straight to it.
 *  Rejects when there is nobody to ask — a send with no recipient is the dead end, not a fix. */
export async function askTheFixer(fix: FixGuide | null | undefined): Promise<{ channelId: string }> {
  const ids = (fix?.ask?.recipients || []).map(r => r.employee_id).filter(Boolean)
  const message = fix?.ask?.message || ''
  if (!ids.length || !message) throw new Error('There is nobody in your company to ask about this.')
  const dm = await api('/api/v1/chat/dm', { method: 'POST', body: JSON.stringify({ employee_ids: ids }) })
  const channelId = String(dm?.channel?.id || '')
  if (!channelId) throw new Error('The conversation could not be opened.')
  await api(`/api/v1/chat/channels/${channelId}/messages`,
            { method: 'POST', body: JSON.stringify({ body: message }) })
  return { channelId }
}

/** Fetch one gap's guide for the signed-in viewer. For a warning the BROWSER detects; a surface whose
 *  server already knows about the gap should serve the guide on its own payload and pass it as a prop
 *  instead, so the screen and the server cannot disagree about whether the gap is open. */
export function useFixGuide(key: string | null | undefined, origin?: string) {
  // The fetched guide is stored WITH the key it answers, and "no key" is DERIVED rather than stored:
  // a surface that stops asking must not keep rendering the last gap's steps, and clearing state
  // from inside the effect is how that becomes a flicker (and a lint rule's finding).
  const [held, setHeld] = useState<{ key: string; guide: FixGuide } | null>(null)
  useEffect(() => {
    if (!key) return
    let alive = true
    const qs = new URLSearchParams({ key, ...(origin ? { origin } : {}) })
    api(`/api/v1/core/fix-guide?${qs.toString()}`)
      .then(g => { if (alive) setHeld({ key, guide: g as FixGuide }) })
      .catch(() => { if (alive) setHeld(null) })   // no guide for this gap: the page's plain notice stands
    return () => { alive = false }
  }, [key, origin])
  return (key && held?.key === key) ? held.guide : null
}

const WRAP: React.CSSProperties = {
  border: '1px solid var(--c-fg-b45309, #b45309)', borderRadius: 8, padding: '10px 12px',
  background: 'var(--c-bg-fff7ed, rgba(180,83,9,0.07))', fontSize: 13, lineHeight: 1.5,
  display: 'flex', flexDirection: 'column', gap: 6,
}
const BTN: React.CSSProperties = {
  alignSelf: 'flex-start', padding: '6px 12px', borderRadius: 6, fontSize: 13, fontWeight: 600,
  border: '1px solid var(--c-fg-b45309, #b45309)', background: 'transparent',
  color: 'var(--c-fg-b45309, #b45309)', cursor: 'pointer', textDecoration: 'none',
}

/**
 * THE notice. Mount it wherever a surface knows a setup gap is open; it renders nothing when there
 * is no guide, so it can be mounted unconditionally.
 *
 * `lead` is the page's own customer-meaningful consequence, placed before the server's sentences.
 */
export function FixItNotice({ fix, lead, compact }: {
  fix: FixGuide | null | undefined
  lead?: string
  compact?: boolean
}) {
  const [sent, setSent] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  const ask = useCallback(async () => {
    setBusy(true); setFailed(false)
    try {
      const { channelId } = await askTheFixer(fix)
      setSent(channelId)
    } catch {
      setFailed(true)
    } finally {
      setBusy(false)
    }
  }, [fix])

  if (!fix) return null
  const label = fixActionLabel(fix)
  const others = (fix.ask?.recipients || []).slice(1)
  return (
    <div style={WRAP} role="status">
      <div><strong>{lead ? lead + ' ' : ''}{fix.title}</strong></div>
      <div style={{ color: 'var(--text2)' }}>{fix.consequence}</div>
      {!compact && (
        <ol style={{ margin: '2px 0 2px 18px', padding: 0 }}>
          {fix.steps.map((s, i) => <li key={i} style={{ margin: '2px 0' }}>{s}</li>)}
        </ol>
      )}
      {fix.can_fix && fix.page && (
        <a href={fix.page} style={BTN}>{label} →</a>
      )}
      {!fix.can_fix && fix.ask && !sent && (
        <>
          <button type="button" style={BTN} onClick={ask} disabled={busy}>
            {busy ? 'Sending…' : label}
          </button>
          {others.length > 0 && (
            <div style={{ color: 'var(--text3)', fontSize: 12 }}>
              Also goes to {others.map(o => o.name).join(', ')}.
            </div>
          )}
        </>
      )}
      {sent && (
        <div>
          Sent to {(fix.ask?.recipients || []).map(r => r.name).join(', ')}.{' '}
          <a href={`/chat?channel=${encodeURIComponent(sent)}`}>Open the conversation</a>
        </div>
      )}
      {failed && (
        <div style={{ color: 'var(--c-fg-b45309, #b45309)' }}>
          The message could not be sent. Open Chat and send it yourself, or contact support.
        </div>
      )}
      {fix.support && (
        <div>This cannot be changed in your own settings — contact support to have it switched on.</div>
      )}
    </div>
  )
}
