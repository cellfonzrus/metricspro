// Chat web-push service worker (Phase 5). Registered by the /chat page only when
// NEXT_PUBLIC_VAPID_PUBLIC_KEY is set; the backend delivers over the standard Web Push protocol
// (see backend/app/modules/chat/push.py :: _send_webpush). Renders the notification and focuses/opens
// the chat when it is clicked. No app logic lives here beyond notification handling.

self.addEventListener('push', (event) => {
  let payload = {}
  try { payload = event.data ? event.data.json() : {} } catch (e) { payload = {} }
  const title = payload.title || 'New message'
  const data = payload.data || {}
  const options = {
    body: payload.body || '',
    data,
    icon: '/globe.svg',
    badge: '/globe.svg',
    // Collapse repeated pushes for the same conversation into one notification.
    tag: data.channel_id ? `chat-${data.channel_id}` : undefined,
    renotify: !!data.channel_id,
  }
  event.waitUntil(self.registration.showNotification(title, options))
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const channelId = (event.notification.data && event.notification.data.channel_id) || ''
  const url = channelId ? `/chat?channel=${encodeURIComponent(channelId)}` : '/chat'
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      // Focus an existing app tab if one is open; otherwise open a new one.
      for (const client of clientList) {
        if ('focus' in client && client.url.includes('/chat')) {
          client.postMessage({ type: 'chat-open', channelId })
          return client.focus()
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(url)
      return undefined
    })
  )
})
