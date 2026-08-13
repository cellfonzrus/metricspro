import React, { useState } from 'react'
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, View } from 'react-native'
import { Redirect } from 'expo-router'

import { useAuth } from '@/auth/AuthContext'
import { Body, Button, H1, Input, Loading, Screen } from '@/components/ui'
import { colors, font, spacing } from '@/theme'

export default function Login() {
  const { status, signIn } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (status === 'loading')
    return (
      <Screen>
        <Loading />
      </Screen>
    )
  if (status === 'signedIn') return <Redirect href="/" />

  const onSubmit = async () => {
    if (!email.trim() || !password) {
      setError('Enter your email and password.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await signIn(email, password)
      // AuthProvider flips status → signedIn; the Redirect above takes over.
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Sign-in failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Screen>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
          <View style={styles.brand}>
            <Text style={styles.logo}>MetricsPro</Text>
            <Body dim>Sign in to your store account</Body>
          </View>

          <H1>Welcome back</H1>

          <View style={styles.form}>
            <Input
              placeholder="you@company.com"
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="email-address"
              textContentType="username"
              value={email}
              onChangeText={setEmail}
            />
            <Input
              placeholder="Password"
              secureTextEntry
              textContentType="password"
              value={password}
              onChangeText={setPassword}
              onSubmitEditing={onSubmit}
              returnKeyType="go"
            />
            {error ? <Text style={styles.error}>{error}</Text> : null}
            <Button title="Sign in" onPress={onSubmit} loading={busy} />
          </View>

          <Body dim>
            Your session is stored in the device Keychain / Keystore and can be locked with Face ID or
            your passcode from Settings after you sign in.
          </Body>
        </ScrollView>
      </KeyboardAvoidingView>
    </Screen>
  )
}

const styles = StyleSheet.create({
  container: { padding: spacing.xl, gap: spacing.xl, flexGrow: 1, justifyContent: 'center' },
  brand: { alignItems: 'center', gap: spacing.xs },
  logo: { color: colors.primary, fontSize: font.h1, fontWeight: '900' },
  form: { gap: spacing.md },
  error: { color: colors.danger, fontSize: font.small },
})
