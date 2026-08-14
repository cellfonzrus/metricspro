import React, { useCallback, useEffect, useRef, useState } from 'react'
import { AppState, type AppStateStatus, Pressable, StyleSheet, Text, View } from 'react-native'

import { colors, font, radius, spacing } from '@/theme'
import { authenticate, isAppLockEnabled } from './biometric'
import { useAuth } from './AuthContext'

// ── App lock gate ────────────────────────────────────────────────────────────────────────────────
// When the user has enabled the app lock, a returning-from-background app (past a short grace period)
// or a cold start must pass a biometric / passcode check before the UI is revealed. Implemented as an
// overlay so no protected screen ever renders underneath while locked.
const GRACE_MS = 60_000 // don't re-prompt for a quick app-switch under a minute

export function AppLockGate({ children }: { children: React.ReactNode }) {
  const { status } = useAuth()
  const [enabled, setEnabled] = useState(false)
  const [locked, setLocked] = useState(false)
  const [checking, setChecking] = useState(false)
  const backgroundedAt = useRef<number | null>(null)
  const appState = useRef<AppStateStatus>(AppState.currentState)

  // Read the setting whenever auth state changes (a fresh sign-in may have enabled it).
  useEffect(() => {
    let alive = true
    isAppLockEnabled().then((on) => {
      if (!alive) return
      setEnabled(on)
      if (on && status === 'signedIn') setLocked(true)
    })
    return () => {
      alive = false
    }
  }, [status])

  const tryUnlock = useCallback(async () => {
    if (checking) return
    setChecking(true)
    const ok = await authenticate('Unlock MetricsPro')
    setChecking(false)
    if (ok) setLocked(false)
  }, [checking])

  // Auto-prompt when the lock appears.
  useEffect(() => {
    if (locked && enabled && status === 'signedIn') void tryUnlock()
  }, [locked, enabled, status, tryUnlock])

  useEffect(() => {
    const sub = AppState.addEventListener('change', (next) => {
      const prev = appState.current
      appState.current = next
      if (!enabled || status !== 'signedIn') return
      if (next.match(/inactive|background/)) {
        backgroundedAt.current = Date.now()
      } else if (next === 'active' && prev.match(/inactive|background/)) {
        const away = backgroundedAt.current ? Date.now() - backgroundedAt.current : 0
        if (away > GRACE_MS) setLocked(true)
      }
    })
    return () => sub.remove()
  }, [enabled, status])

  // Only gate the signed-in experience. Sign-in screen and loading are never locked.
  const shouldLock = enabled && locked && status === 'signedIn'

  return (
    <View style={styles.root}>
      {children}
      {shouldLock && (
        <View style={styles.overlay}>
          <Text style={styles.logo}>MetricsPro</Text>
          <Text style={styles.title}>Locked</Text>
          <Text style={styles.subtitle}>Authenticate to continue</Text>
          <Pressable style={styles.button} onPress={tryUnlock} disabled={checking}>
            <Text style={styles.buttonText}>{checking ? 'Verifying…' : 'Unlock'}</Text>
          </Pressable>
        </View>
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  overlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: colors.bg,
    alignItems: 'center',
    justifyContent: 'center',
    padding: spacing.xl,
  },
  logo: { color: colors.primary, fontSize: font.h2, fontWeight: '800', marginBottom: spacing.xxl },
  title: { color: colors.text, fontSize: font.h1, fontWeight: '700' },
  subtitle: { color: colors.textDim, fontSize: font.body, marginTop: spacing.sm, marginBottom: spacing.xl },
  button: {
    backgroundColor: colors.primary,
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.xxl,
    borderRadius: radius.pill,
  },
  buttonText: { color: colors.primaryText, fontSize: font.body, fontWeight: '700' },
})
