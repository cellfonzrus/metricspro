import 'react-native-gesture-handler'
import React, { useEffect } from 'react'
import { GestureHandlerRootView } from 'react-native-gesture-handler'
import { SafeAreaProvider } from 'react-native-safe-area-context'
import { StatusBar } from 'expo-status-bar'
import { Stack } from 'expo-router'
import * as SplashScreen from 'expo-splash-screen'
import { QueryClientProvider } from '@tanstack/react-query'

import { AuthProvider, useAuth } from '@/auth/AuthContext'
import { AppLockGate } from '@/auth/AppLockGate'
import { queryClient } from '@/api/query'
import { colors } from '@/theme'

SplashScreen.preventAutoHideAsync().catch(() => {})

function RootNavigator() {
  const { status } = useAuth()

  // Hide the splash once auth has resolved to a definite state.
  useEffect(() => {
    if (status !== 'loading') SplashScreen.hideAsync().catch(() => {})
  }, [status])

  return (
    <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.bg } }}>
      <Stack.Screen name="login" />
      <Stack.Screen name="choose-company" />
      <Stack.Screen name="(app)" />
    </Stack>
  )
}

export default function RootLayout() {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <AppLockGate>
              <StatusBar style="light" />
              <RootNavigator />
            </AppLockGate>
          </AuthProvider>
        </QueryClientProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  )
}
