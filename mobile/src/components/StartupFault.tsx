import React from 'react'
import { ScrollView, StyleSheet, Text, View } from 'react-native'

import { colors, font, radius, spacing } from '@/theme'
import { STARTUP_FAULT_TITLE, type StartupFault } from '@/lib/startup-fault'

// ── WHAT THE APP SHOWS INSTEAD OF DYING ──────────────────────────────────────────────────────────
// A bundle that cannot start used to take the process with it, which killed expo-updates' own
// background download and made the bad bundle permanent (index §CORE-38.7). These two surfaces are
// how it stays alive instead: the screen for a fault known before rendering, the boundary for one
// thrown during it. Neither ever renders blank — a blank screen is what hid the first fault.

export function StartupFaultScreen({ fault }: { fault: StartupFault }) {
  return (
    <View style={styles.page}>
      <ScrollView contentContainerStyle={styles.card}>
        <Text style={styles.title}>{fault.title}</Text>
        <Text style={styles.detail}>{fault.detail}</Text>
        {fault.missing.length > 0 && (
          <View style={styles.names}>
            {fault.missing.map((name) => (
              <Text key={name} style={styles.name}>
                {name}
              </Text>
            ))}
          </View>
        )}
      </ScrollView>
    </View>
  )
}

type BoundaryState = { message: string }

/**
 * Catches a throw during rendering and shows it. It cannot catch a throw raised while the bundle is
 * being evaluated — that is why nothing on the startup path may throw in the first place — but it
 * does keep one bad screen from taking the whole app down.
 */
export class StartupErrorBoundary extends React.Component<
  { children: React.ReactNode },
  BoundaryState
> {
  state: BoundaryState = { message: '' }

  static getDerivedStateFromError(error: unknown): BoundaryState {
    const message = error instanceof Error ? error.message : String(error)
    return { message: message.trim() === '' ? 'An unexpected error with no message.' : message }
  }

  render() {
    if (this.state.message === '') return this.props.children
    return (
      <StartupFaultScreen
        fault={{
          missing: [],
          title: STARTUP_FAULT_TITLE,
          detail:
            `${this.state.message}\n\nThis is a fault in the app, not in your phone or your ` +
            'account. It will be repaired by an update; close and open the app in a few minutes.',
        }}
      />
    )
  }
}

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.bg, justifyContent: 'center' },
  card: { padding: spacing.lg, gap: spacing.md },
  title: { color: colors.text, fontSize: font.h2, fontWeight: '700' },
  detail: { color: colors.textDim, fontSize: font.body, lineHeight: 22 },
  names: { gap: spacing.xs, marginTop: spacing.sm },
  name: {
    color: colors.text,
    fontSize: font.small,
    fontFamily: 'Menlo',
    backgroundColor: colors.surface,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
  },
})
