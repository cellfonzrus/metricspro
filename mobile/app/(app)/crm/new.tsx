import React, { useState } from 'react'
import {
  Alert,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native'
import { Stack, useRouter } from 'expo-router'
import { useMutation } from '@tanstack/react-query'

import { createLead, type NewLead } from '@/api/crm'
import { queryClient } from '@/api/query'
import { Body, Button, Input, Screen } from '@/components/ui'
import { colors, font, spacing } from '@/theme'

export default function NewLead() {
  const router = useRouter()
  const [form, setForm] = useState<NewLead>({})
  const set = (k: keyof NewLead) => (v: string) => setForm((f) => ({ ...f, [k]: v }))

  const mut = useMutation({
    mutationFn: () => createLead(form),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['crm'] })
      router.back()
    },
    onError: (e) => Alert.alert('Could not create lead', e instanceof Error ? e.message : 'Try again.'),
  })

  const submit = () => {
    if (!(form.first_name || form.last_name || form.company_name || form.phone)) {
      Alert.alert('Add a name or phone', 'Enter at least a name, company, or phone number.')
      return
    }
    mut.mutate()
  }

  return (
    <Screen>
      <Stack.Screen options={{ title: 'New lead' }} />
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={90}
      >
        <ScrollView
          contentContainerStyle={styles.container}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="on-drag"
        >
          <Field label="First name">
            <Input value={form.first_name ?? ''} onChangeText={set('first_name')} autoCapitalize="words" />
          </Field>
          <Field label="Last name">
            <Input value={form.last_name ?? ''} onChangeText={set('last_name')} autoCapitalize="words" />
          </Field>
          <Field label="Phone">
            <Input value={form.phone ?? ''} onChangeText={set('phone')} keyboardType="phone-pad" />
          </Field>
          <Field label="Email">
            <Input
              value={form.email ?? ''}
              onChangeText={set('email')}
              keyboardType="email-address"
              autoCapitalize="none"
              autoCorrect={false}
            />
          </Field>
          <Field label="Company (optional)">
            <Input value={form.company_name ?? ''} onChangeText={set('company_name')} />
          </Field>
          <Field label="Store code (optional)">
            <Input value={form.store_code ?? ''} onChangeText={set('store_code')} autoCapitalize="characters" />
          </Field>
          <Field label="Notes (optional)">
            <Input value={form.notes ?? ''} onChangeText={set('notes')} multiline numberOfLines={3} style={styles.notes} />
          </Field>

          <View style={{ marginTop: spacing.md }}>
            <Button title="Create lead" onPress={submit} loading={mut.isPending} />
          </View>
          <Body dim>The lead is added to your CRM pipeline and assigned by your team&apos;s rules.</Body>
        </ScrollView>
      </KeyboardAvoidingView>
    </Screen>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={{ gap: spacing.xs }}>
      <Text style={styles.label}>{label}</Text>
      {children}
    </View>
  )
}

const styles = StyleSheet.create({
  container: { padding: spacing.lg, gap: spacing.md, paddingBottom: spacing.xxl },
  label: { color: colors.textDim, fontSize: font.small, fontWeight: '600' },
  notes: { minHeight: 80, textAlignVertical: 'top' },
})
