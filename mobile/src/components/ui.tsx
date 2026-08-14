import React from 'react'
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  type TextInputProps,
  View,
  type ViewStyle,
} from 'react-native'
import { SafeAreaView } from 'react-native-safe-area-context'

import { colors, font, radius, spacing } from '@/theme'

// ── Shared UI kit ────────────────────────────────────────────────────────────────────────────────
// A handful of primitives every screen reuses, so the three modules look like one app. Deliberately
// tiny and dependency-free (no component library) for a lean first bundle.

export function Screen({ children, style }: { children: React.ReactNode; style?: ViewStyle }) {
  return (
    <SafeAreaView style={[styles.screen, style]} edges={['top', 'left', 'right']}>
      {children}
    </SafeAreaView>
  )
}

export function Card({ children, style }: { children: React.ReactNode; style?: ViewStyle }) {
  return <View style={[styles.card, style]}>{children}</View>
}

export function Button({
  title,
  onPress,
  variant = 'primary',
  disabled,
  loading,
}: {
  title: string
  onPress: () => void
  variant?: 'primary' | 'secondary' | 'danger' | 'success'
  disabled?: boolean
  loading?: boolean
}) {
  const bg =
    variant === 'primary'
      ? colors.primary
      : variant === 'danger'
        ? colors.danger
        : variant === 'success'
          ? colors.success
          : colors.surfaceAlt
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled || loading}
      style={({ pressed }) => [
        styles.button,
        { backgroundColor: bg, opacity: disabled ? 0.5 : pressed ? 0.85 : 1 },
      ]}
    >
      {loading ? (
        <ActivityIndicator color={colors.primaryText} />
      ) : (
        <Text style={styles.buttonText}>{title}</Text>
      )}
    </Pressable>
  )
}

export function Input(props: TextInputProps) {
  return <TextInput placeholderTextColor={colors.textDim} style={styles.input} {...props} />
}

export function Badge({ label, color = colors.primary }: { label: string; color?: string }) {
  return (
    <View style={[styles.badge, { backgroundColor: color + '22', borderColor: color }]}>
      <Text style={[styles.badgeText, { color }]}>{label}</Text>
    </View>
  )
}

export function Loading({ label }: { label?: string }) {
  return (
    <View style={styles.center}>
      <ActivityIndicator color={colors.primary} size="large" />
      {label ? <Text style={styles.dim}>{label}</Text> : null}
    </View>
  )
}

export function ErrorView({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <View style={styles.center}>
      <Text style={styles.errorTitle}>Something went wrong</Text>
      <Text style={styles.dim}>{message}</Text>
      {onRetry ? (
        <View style={{ marginTop: spacing.lg }}>
          <Button title="Try again" onPress={onRetry} variant="secondary" />
        </View>
      ) : null}
    </View>
  )
}

export function EmptyState({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <View style={styles.center}>
      <Text style={styles.emptyTitle}>{title}</Text>
      {subtitle ? <Text style={styles.dim}>{subtitle}</Text> : null}
    </View>
  )
}

export function H1({ children }: { children: React.ReactNode }) {
  return <Text style={styles.h1}>{children}</Text>
}
export function H2({ children }: { children: React.ReactNode }) {
  return <Text style={styles.h2}>{children}</Text>
}
export function Body({ children, dim }: { children: React.ReactNode; dim?: boolean }) {
  return <Text style={[styles.body, dim && styles.dim]}>{children}</Text>
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    padding: spacing.lg,
  },
  button: {
    borderRadius: radius.md,
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.lg,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 48,
  },
  buttonText: { color: colors.primaryText, fontSize: font.body, fontWeight: '700' },
  input: {
    backgroundColor: colors.surfaceAlt,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    color: colors.text,
    fontSize: font.body,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    minHeight: 48,
  },
  badge: {
    borderRadius: radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    alignSelf: 'flex-start',
  },
  badgeText: { fontSize: font.tiny, fontWeight: '700' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.xl, gap: spacing.sm },
  dim: { color: colors.textDim, fontSize: font.small, textAlign: 'center' },
  errorTitle: { color: colors.danger, fontSize: font.h3, fontWeight: '700' },
  emptyTitle: { color: colors.text, fontSize: font.h3, fontWeight: '700' },
  h1: { color: colors.text, fontSize: font.h1, fontWeight: '800' },
  h2: { color: colors.text, fontSize: font.h2, fontWeight: '700' },
  body: { color: colors.text, fontSize: font.body },
})
