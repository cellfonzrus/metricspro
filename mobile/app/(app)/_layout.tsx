import React from 'react'
import { Redirect, Stack } from 'expo-router'

import { useAuth } from '@/auth/AuthContext'
import { Loading, Screen } from '@/components/ui'
import { colors } from '@/theme'

// Guards the entire signed-in area. Detail screens (checkout, lead detail) are declared here so they
// push OVER the tab bar as a stack.
export default function AppLayout() {
  const { status, tenants, activeOrg } = useAuth()

  if (status === 'loading')
    return (
      <Screen>
        <Loading />
      </Screen>
    )
  if (status === 'signedOut') return <Redirect href="/login" />
  // A login in >1 company must pick one before any company-scoped screen loads (else clock-in /
  // targets / POS / CRM calls are ambiguous). Single-company logins never hit this.
  if (tenants.length > 1 && !activeOrg) return <Redirect href="/choose-company" />

  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: colors.surface },
        headerTintColor: colors.text,
        headerTitleStyle: { color: colors.text },
        contentStyle: { backgroundColor: colors.bg },
      }}
    >
      <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
      <Stack.Screen name="pos/checkout" options={{ title: 'Checkout', presentation: 'modal' }} />
      <Stack.Screen name="pos/receipts" options={{ title: 'Imported receipts' }} />
      <Stack.Screen name="pos/receipt-import" options={{ title: 'Import receipt', presentation: 'modal' }} />
      <Stack.Screen name="crm/[leadId]" options={{ title: 'Lead' }} />
      <Stack.Screen name="crm/new" options={{ title: 'New lead', presentation: 'modal' }} />
      <Stack.Screen name="earnings/targets" options={{ title: 'Targets' }} />
    </Stack>
  )
}
