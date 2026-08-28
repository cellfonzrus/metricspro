import React, { useEffect, useState } from 'react'
import { ScrollView, StyleSheet, Text, View } from 'react-native'
import { Redirect, useRouter } from 'expo-router'

import { useAuth } from '@/auth/AuthContext'
import { Body, Button, H1, Loading, Screen } from '@/components/ui'
import { colors, font, spacing } from '@/theme'

// Required company chooser for a login that belongs to more than one company. The backend needs an
// active company (x-active-org) before it will load company-scoped features (clock-in, targets, POS,
// CRM); until one is picked those calls are ambiguous. Single-company logins never see this.
export default function ChooseCompany() {
  const { status, tenants, activeOrg, switchTenant } = useAuth()
  const router = useRouter()
  const [busy, setBusy] = useState<string | null>(null)

  // If a company is already active, or the login has 0–1 companies, there's nothing to choose.
  const needsChoice = status === 'signedIn' && tenants.length > 1 && !activeOrg

  // Auto-select when there's exactly one membership (defensive — the gate shouldn't route here then).
  useEffect(() => {
    if (status === 'signedIn' && tenants.length === 1 && !activeOrg) {
      void switchTenant(tenants[0].org_id)
    }
  }, [status, tenants, activeOrg, switchTenant])

  if (status === 'loading')
    return (
      <Screen>
        <Loading />
      </Screen>
    )
  if (status === 'signedOut') return <Redirect href="/login" />
  if (!needsChoice) return <Redirect href="/" />

  const pick = async (orgId: string) => {
    setBusy(orgId)
    try {
      await switchTenant(orgId)
      router.replace('/')
    } finally {
      setBusy(null)
    }
  }

  return (
    <Screen>
      <ScrollView contentContainerStyle={styles.container}>
        <View style={styles.head}>
          <H1>Choose a company</H1>
          <Body dim>Your login has access to more than one company. Pick the one you&apos;re working in.</Body>
        </View>
        <View style={styles.list}>
          {tenants.map((t) => (
            <Button
              key={t.org_id}
              title={t.org_name ?? t.org_id}
              variant="secondary"
              loading={busy === t.org_id}
              onPress={() => pick(t.org_id)}
            />
          ))}
        </View>
        <Body dim>You can switch companies later from Settings.</Body>
      </ScrollView>
    </Screen>
  )
}

const styles = StyleSheet.create({
  container: { padding: spacing.xl, gap: spacing.xl, flexGrow: 1, justifyContent: 'center' },
  head: { gap: spacing.sm },
  list: { gap: spacing.md },
  label: { color: colors.textDim, fontSize: font.small },
})
