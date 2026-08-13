import { useEffect, useState } from 'react'

import { subscribeQueue, type QueuedMutation, type FailedMutation } from './queue'

// React hook exposing live pending/failed offline mutations for the "Sync" UI.
export function useOfflineQueue(): { pending: QueuedMutation[]; failed: FailedMutation[] } {
  const [snapshot, setSnapshot] = useState<{ pending: QueuedMutation[]; failed: FailedMutation[] }>({
    pending: [],
    failed: [],
  })
  useEffect(() => subscribeQueue(setSnapshot), [])
  return snapshot
}
