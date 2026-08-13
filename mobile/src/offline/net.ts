import NetInfo from '@react-native-community/netinfo'
import { useEffect, useState } from 'react'

// ── Connectivity ─────────────────────────────────────────────────────────────────────────────────
// A store's Wi-Fi is frequently flaky, so the app must know when it is offline: reads fall back to
// cache (react-query), writes fall into the offline queue. `isConnected` from NetInfo means "has a
// network interface"; `isInternetReachable` is the stronger "can actually reach the internet" (null
// until probed). We treat unreachable as offline once known.
export function isOnline(state: { isConnected: boolean | null; isInternetReachable: boolean | null }) {
  if (state.isInternetReachable === false) return false
  return state.isConnected !== false
}

let _online = true
const listeners = new Set<(online: boolean) => void>()

NetInfo.addEventListener((state) => {
  const online = isOnline(state)
  if (online !== _online) {
    _online = online
    for (const cb of Array.from(listeners)) {
      try {
        cb(online)
      } catch {
        /* ignore */
      }
    }
  }
})

export function getOnline(): boolean {
  return _online
}

export function onConnectivityChange(cb: (online: boolean) => void): () => void {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

/** React hook: current online state, updates on change. */
export function useOnline(): boolean {
  const [online, setOnline] = useState(getOnline())
  useEffect(() => onConnectivityChange(setOnline), [])
  return online
}
