import React, { useCallback, useEffect, useReducer, useRef } from 'react'
import { AppState, type AppStateStatus, Pressable, StyleSheet, Text, View } from 'react-native'

import { colors, font, radius, spacing } from '@/theme'
// Every decision about the lock — is it on, may we prompt, may they retry, have we trapped them —
// is DEREFERENCED from the one home (src/lib/app-lock.ts), never decided here. The screen owns the
// pixels and the native call and nothing else. `app-lock-gate-lock.test.ts` fails the build if this
// file grows its own copy of any of it, which is exactly how the un-dismissable prompt happened.
import {
  appLockReducer,
  canAttemptAppLock,
  initialAppLockState,
  shouldAutoPromptAppLock,
  shouldOfferAppLockSignOut,
  shouldShowAppLockUi,
} from '@/lib/app-lock'
import { authenticate, getBiometricSupport, isAppLockEnabled } from './biometric'
import { useAuth } from './AuthContext'

// ── App lock gate ────────────────────────────────────────────────────────────────────────────────
// When the owner has enabled the app lock, a returning-from-background app (past the grace period)
// or a cold start must pass a biometric / passcode check before the UI is revealed. Implemented as
// an overlay so no protected screen ever renders underneath while locked.

export function AppLockGate({ children }: { children: React.ReactNode }) {
  const { status, signOut } = useAuth()
  const [lock, dispatch] = useReducer(appLockReducer, initialAppLockState)
  const appState = useRef<AppStateStatus>(AppState.currentState)

  // Read the setting and the device's factor whenever auth state changes (a fresh sign-in may have
  // enabled it). Order matters only in that the reducer tolerates either arriving first.
  useEffect(() => {
    let alive = true
    isAppLockEnabled().then((enabled) => {
      if (alive) dispatch({ type: 'settingRead', enabled })
    })
    getBiometricSupport().then((s) => {
      if (alive) dispatch({ type: 'factorSupport', hasFactor: s.hasFactor })
    })
    return () => {
      alive = false
    }
  }, [status])

  useEffect(() => {
    dispatch({ type: 'authStatus', signedIn: status === 'signedIn' })
  }, [status])

  // Raise the native prompt. Deliberately NOT a dependency of the auto-prompt effect below: its
  // identity changing is what re-prompted a cancelled Face ID forever.
  const attempt = useCallback(async () => {
    dispatch({ type: 'attemptStarted' })
    const ok = await authenticate('Unlock MetricsPro')
    dispatch({ type: 'attemptFinished', ok })
  }, [])
  const attemptRef = useRef(attempt)
  attemptRef.current = attempt

  // Auto-prompt once per lock. The guard is the reducer's answer, not a React identity.
  const autoPrompt = shouldAutoPromptAppLock(lock)
  useEffect(() => {
    if (autoPrompt) void attemptRef.current()
  }, [autoPrompt])

  useEffect(() => {
    const sub = AppState.addEventListener('change', (next) => {
      const prev = appState.current
      appState.current = next
      if (next.match(/inactive|background/)) dispatch({ type: 'backgrounded', at: Date.now() })
      else if (next === 'active' && prev.match(/inactive|background/))
        dispatch({ type: 'foregrounded', at: Date.now() })
    })
    return () => sub.remove()
  }, [])

  const showLock = shouldShowAppLockUi(lock)
  const canRetry = canAttemptAppLock(lock)
  const offerSignOut = shouldOfferAppLockSignOut(lock)

  return (
    <View style={styles.root}>
      {children}
      {showLock && (
        <View style={styles.overlay}>
          <Text style={styles.logo}>MetricsPro</Text>
          <Text style={styles.title}>Locked</Text>
          <Text style={styles.subtitle}>
            {offerSignOut
              ? 'That did not work. Try again, or sign out and use your email and password.'
              : 'Authenticate to continue'}
          </Text>
          <Pressable
            accessibilityRole="button"
            style={styles.button}
            onPress={() => void attempt()}
            disabled={!canRetry}
          >
            <Text style={styles.buttonText}>{lock.attempting ? 'Verifying…' : 'Unlock'}</Text>
          </Pressable>
          {/* The way out. A signed-in app whose factor cannot succeed used to be escapable only by
              deleting the app. */}
          {offerSignOut && (
            <Pressable
              accessibilityRole="button"
              style={styles.secondary}
              onPress={() => void signOut()}
            >
              <Text style={styles.secondaryText}>Sign out</Text>
            </Pressable>
          )}
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
  subtitle: {
    color: colors.textDim,
    fontSize: font.body,
    marginTop: spacing.sm,
    marginBottom: spacing.xl,
    textAlign: 'center',
  },
  button: {
    backgroundColor: colors.primary,
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.xxl,
    borderRadius: radius.pill,
  },
  buttonText: { color: colors.primaryText, fontSize: font.body, fontWeight: '700' },
  secondary: { marginTop: spacing.lg, paddingVertical: spacing.sm, paddingHorizontal: spacing.xl },
  secondaryText: { color: colors.textDim, fontSize: font.body, fontWeight: '600' },
})
