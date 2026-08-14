import React from 'react'
import { StyleSheet, Text, View } from 'react-native'

import { useOnline } from '@/offline/net'
import { useOfflineQueue } from '@/offline/useQueue'
import { colors, font, spacing } from '@/theme'

// A slim status strip shown under the header when offline or when work is waiting to sync. Keeps the
// frontline user honest about what has and hasn't reached the server.
export function OfflineBanner() {
  const online = useOnline()
  const { pending } = useOfflineQueue()

  if (online && pending.length === 0) return null

  const text = !online
    ? pending.length > 0
      ? `Offline · ${pending.length} action(s) will sync when reconnected`
      : 'Offline · changes will be saved and synced'
    : `Syncing ${pending.length} pending action(s)…`

  return (
    <View style={[styles.bar, { backgroundColor: online ? colors.warning + '22' : colors.danger + '22' }]}>
      <Text style={[styles.text, { color: online ? colors.warning : colors.danger }]}>{text}</Text>
    </View>
  )
}

const styles = StyleSheet.create({
  bar: { paddingHorizontal: spacing.lg, paddingVertical: spacing.sm },
  text: { fontSize: font.small, fontWeight: '600', textAlign: 'center' },
})
