import * as LocalAuthentication from 'expo-local-authentication'
import AsyncStorage from '@react-native-async-storage/async-storage'

// "Can this handset unlock at all, and what is the factor called" is DEREFERENCED from the app
// lock's one home (src/lib/app-lock.ts), which is where it is tested. This file only asks the
// native APIs.
import { describeDeviceFactor, type DeviceFactor } from '@/lib/app-lock'

// ── Biometric app lock ───────────────────────────────────────────────────────────────────────────
// The app holds a long-lived refresh token in the Keychain/Keystore, so a lost/stolen unlocked
// phone would otherwise walk straight into POS, payroll and customer PII. The app-lock requires
// Face ID / Touch ID / device passcode to re-enter the app after it has been backgrounded past a
// grace period, or on cold start. This is a *local* gate (it never bypasses the server); it just
// stops shoulder-surfing and casual device theft.
const ENABLED_KEY = 'mp_applock_enabled'

export type BiometricSupport = DeviceFactor & {
  hasHardware: boolean
  enrolled: boolean
}

export async function getBiometricSupport(): Promise<BiometricSupport> {
  const [hasHardware, enrolled, enrolledLevel, types] = await Promise.all([
    LocalAuthentication.hasHardwareAsync(),
    LocalAuthentication.isEnrolledAsync(),
    // The signal that answers "can an attempt ever succeed": it counts the device passcode, which
    // `authenticateAsync` falls back to, and it is 0 on a phone that has neither.
    LocalAuthentication.getEnrolledLevelAsync(),
    LocalAuthentication.supportedAuthenticationTypesAsync(),
  ])
  const factor = describeDeviceFactor({
    enrolledLevel,
    faceId: types.includes(LocalAuthentication.AuthenticationType.FACIAL_RECOGNITION),
    fingerprint: types.includes(LocalAuthentication.AuthenticationType.FINGERPRINT),
  })
  return { hasHardware, enrolled, ...factor }
}

export async function isAppLockEnabled(): Promise<boolean> {
  try {
    return (await AsyncStorage.getItem(ENABLED_KEY)) === '1'
  } catch {
    return false
  }
}

export async function setAppLockEnabled(on: boolean): Promise<void> {
  try {
    await AsyncStorage.setItem(ENABLED_KEY, on ? '1' : '0')
  } catch {
    /* ignore */
  }
}

/**
 * Prompt for the local factor. Falls back to the device passcode when no biometric is enrolled
 * (disableDeviceFallback:false), so the gate still works on a PIN-only device. Returns true on
 * success. Never throws — a thrown auth is treated as "not authenticated".
 */
export async function authenticate(
  reason = 'Unlock MetricsPro',
): Promise<boolean> {
  try {
    const res = await LocalAuthentication.authenticateAsync({
      promptMessage: reason,
      cancelLabel: 'Cancel',
      disableDeviceFallback: false,
    })
    return res.success
  } catch {
    return false
  }
}
