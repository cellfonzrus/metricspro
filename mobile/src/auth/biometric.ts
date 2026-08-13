import * as LocalAuthentication from 'expo-local-authentication'
import AsyncStorage from '@react-native-async-storage/async-storage'

// ── Biometric app lock ───────────────────────────────────────────────────────────────────────────
// The app holds a long-lived refresh token in the Keychain/Keystore, so a lost/stolen unlocked
// phone would otherwise walk straight into POS, payroll and customer PII. The app-lock requires
// Face ID / Touch ID / device passcode to re-enter the app after it has been backgrounded past a
// grace period, or on cold start. This is a *local* gate (it never bypasses the server); it just
// stops shoulder-surfing and casual device theft.
const ENABLED_KEY = 'mp_applock_enabled'

export type BiometricSupport = {
  hasHardware: boolean
  enrolled: boolean
  // The strongest available factor label, for UI copy.
  label: 'Face ID' | 'Touch ID' | 'Biometrics' | 'Passcode' | 'None'
}

export async function getBiometricSupport(): Promise<BiometricSupport> {
  const hasHardware = await LocalAuthentication.hasHardwareAsync()
  const enrolled = await LocalAuthentication.isEnrolledAsync()
  let label: BiometricSupport['label'] = hasHardware ? 'Biometrics' : 'None'
  if (hasHardware) {
    const types = await LocalAuthentication.supportedAuthenticationTypesAsync()
    if (types.includes(LocalAuthentication.AuthenticationType.FACIAL_RECOGNITION)) label = 'Face ID'
    else if (types.includes(LocalAuthentication.AuthenticationType.FINGERPRINT)) label = 'Touch ID'
    if (!enrolled) label = 'Passcode'
  }
  return { hasHardware, enrolled, label }
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
