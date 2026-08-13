import React, { useEffect, useState } from 'react'
import { ScrollView, StyleSheet, Switch, Text, View } from 'react-native'

import { useAuth } from '@/auth/AuthContext'
import {
  authenticate,
  getBiometricSupport,
  isAppLockEnabled,
  setAppLockEnabled,
  type BiometricSupport,
} from '@/auth/biometric'
import { useOfflineQueue } from '@/offline/useQueue'
import { discardFailed, flushQueue, retryFailed } from '@/offline/queue'
import { Body, Button, Card, H2, Screen } from '@/components/ui'
import { colors, font, spacing } from '@/theme'

export default function Settings() {
  const { me, tenants, activeOrg, switchTenant, signOut } = useAuth()
  const { pending, failed } = useOfflineQueue()
  const [biometric, setBiometric] = useState<BiometricSupport | null>(null)
  const [lockOn, setLockOn] = useState(false)

  useEffect(() => {
    getBiometricSupport().then(setBiometric)
    isAppLockEnabled().then(setLockOn)
  }, [])

  const toggleLock = async (next: boolean) => {
    // Require a successful auth before ENABLING, so a user can't lock themselves out with a factor
    // that doesn't actually work on this device.
    if (next) {
      const ok = await authenticate('Confirm to enable app lock')
      if (!ok) return
    }
    await setAppLockEnabled(next)
    setLockOn(next)
  }

  return (
    <Screen>
      <ScrollView contentContainerStyle={styles.container}>
        <H2>Account</H2>
        <Card style={{ gap: spacing.xs }}>
          <Row label="Name" value={(me?.user?.name as string) || '—'} />
          <Row label="Email" value={(me?.user?.email as string) || '—'} />
          <Row label="Role" value={(me?.user?.role_display as string) || (me?.user?.role as string) || '—'} />
          <Row label="Employee ID" value={(me?.user?.employee_id as string) || 'Not linked'} />
        </Card>

        {tenants.length > 1 && (
          <>
            <H2>Company</H2>
            <Card style={{ gap: spacing.sm }}>
              <Body dim>You belong to multiple companies. Pick the one you are working in.</Body>
              {tenants.map((t) => (
                <Button
                  key={t.org_id}
                  title={`${t.org_name ?? t.org_id}${t.org_id === activeOrg ? '  ✓' : ''}`}
                  variant={t.org_id === activeOrg ? 'primary' : 'secondary'}
                  onPress={() => switchTenant(t.org_id)}
                />
              ))}
            </Card>
          </>
        )}

        <H2>Security</H2>
        <Card style={{ gap: spacing.md }}>
          <View style={styles.switchRow}>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowLabel}>App lock</Text>
              <Text style={styles.hint}>
                {biometric?.hasHardware
                  ? `Require ${biometric.label} to open the app`
                  : 'No biometric hardware detected — device passcode will be used'}
              </Text>
            </View>
            <Switch value={lockOn} onValueChange={toggleLock} />
          </View>
          <Body dim>Your session is stored encrypted in the device Keychain / Keystore.</Body>
        </Card>

        <H2>Sync</H2>
        <Card style={{ gap: spacing.sm }}>
          <Row label="Pending" value={String(pending.length)} />
          {pending.length > 0 && <Button title="Sync now" variant="secondary" onPress={() => flushQueue()} />}
          {failed.length > 0 && (
            <>
              <Text style={[styles.rowLabel, { color: colors.danger, marginTop: spacing.sm }]}>
                {failed.length} action(s) failed
              </Text>
              {failed.map((f) => (
                <View key={f.id} style={styles.failed}>
                  <Text style={styles.failedTitle}>{f.label}</Text>
                  <Text style={styles.hint}>{f.lastError}</Text>
                  <View style={styles.failedActions}>
                    <Button title="Retry" variant="secondary" onPress={() => retryFailed(f.id)} />
                    <Button title="Discard" variant="danger" onPress={() => discardFailed(f.id)} />
                  </View>
                </View>
              ))}
            </>
          )}
          {pending.length === 0 && failed.length === 0 && <Body dim>Everything is synced.</Body>}
        </Card>

        <View style={{ height: spacing.lg }} />
        <Button title="Sign out" variant="danger" onPress={signOut} />
      </ScrollView>
    </Screen>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.row}>
      <Text style={styles.rowLabel}>{label}</Text>
      <Text style={styles.rowValue}>{value}</Text>
    </View>
  )
}

const styles = StyleSheet.create({
  container: { padding: spacing.lg, gap: spacing.md, paddingBottom: spacing.xxl },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 2 },
  rowLabel: { color: colors.textDim, fontSize: font.small },
  rowValue: { color: colors.text, fontSize: font.body, fontWeight: '600', flexShrink: 1, textAlign: 'right' },
  switchRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  hint: { color: colors.textDim, fontSize: font.small },
  failed: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border, paddingTop: spacing.sm, gap: spacing.xs },
  failedTitle: { color: colors.text, fontSize: font.body, fontWeight: '600' },
  failedActions: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.xs },
})
