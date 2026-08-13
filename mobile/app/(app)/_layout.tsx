import React from 'react'
import { Redirect, Stack } from 'expo-router'

import { useAuth } from '@/auth/AuthContext'
import { Loading, Screen } from '@/components/ui'
import { colors } from '@/theme'

// Guards the entire signed-in area. Detail screens (checkout, lead detail) are declared here so they
// push OVER the tab bar as a stack.
export default function AppLayout() {
  const { status } = useAuth()

  if (status === 'loading')
    return (
      <Screen>
        <Loading />
      </Screen>
    )
  if (status === 'signedOut') return <Redirect href="/login" />

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
      <Stack.Screen name="crm/[leadId]" options={{ title: 'Lead' }} />
    </Stack>
  )
}
