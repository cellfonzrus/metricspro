// Chat web-push registration — THE single implementation (owner directive 2026-09-10: "the chat should
// be able to send notifications to the people").
//
// This was inline in /chat/page.tsx, where it only ever ran while that page was open — so a browser
// subscribed for pushes only if the user happened to visit the chat screen, which is precisely the
// case where they do not need one. It moved here unchanged in substance and is now called once from
// the header envelope, which is mounted on EVERY platform page. One call site, one implementation:
// the page no longer carries a private copy that could drift from this one.
//
// TWO GATES, BOTH REQUIRED, and this is the fix that came with the move:
//   • the browser must support service workers + PushManager, and
//   • THE SERVER must actually be able to send — `push_web` from GET /chat/me, which is
//     push.webpush_configured() (CHAT_VAPID_PUBLIC_KEY + CHAT_VAPID_PRIVATE_KEY present).
//
// The old code gated only on the client's own build-time NEXT_PUBLIC_VAPID_PUBLIC_KEY. Those two
// values live in different places (Vercel vs Railway) and can disagree, and when they do the browser
// holds a perfectly valid subscription that the backend has no private key to send to. The user
// answers a permission prompt and then never hears anything — a silent failure that looks exactly
// like "notifications are broken". Asking the sender whether it can send closes that.
//
// Never fakes a subscription: every failure path leaves the user unsubscribed and says nothing.
import { api } from '@/lib/client'

/** Standard VAPID key decode for PushManager.subscribe. Only reached when a key is configured. */
export function urlB64ToUint8(base64: string): Uint8Array {
  const padding = '='.repeat((4 - (base64.length % 4)) % 4)
  const b64 = (base64 + padding).replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(b64)
  const arr = new Uint8Array(raw.length)
  for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i)
  return arr
}

/**
 * Subscribe this browser to chat web push and register the subscription with the backend.
 *
 * `serverReady` is /chat/me's `push_web`. Returns quietly (false) whenever push cannot work — no
 * permission prompt is raised in that case, because a prompt the operator cannot honour costs the
 * user a decision and buys them nothing.
 */
export async function registerChatPush(serverReady: boolean): Promise<boolean> {
  const vapid = process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY
  if (!serverReady || !vapid) return false
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return false
  if (typeof window === 'undefined' || !('PushManager' in window)) return false
  try {
    const reg = await navigator.serviceWorker.register('/sw.js')
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlB64ToUint8(vapid) as BufferSource,
    })
    await api('/api/v1/chat/push/register', {
      method: 'POST',
      body: JSON.stringify({ token: JSON.stringify(sub), platform: 'web' }),
    })
    return true
  } catch {
    return false   // push unavailable in this environment (denied, insecure origin, no SW) — skip silently
  }
}
