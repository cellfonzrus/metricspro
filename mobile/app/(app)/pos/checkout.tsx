import React, { useState } from 'react'
import { Alert, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native'
import { useRouter } from 'expo-router'
import { useQuery } from '@tanstack/react-query'

import { checkoutDurable, type Payment } from '@/api/pos'
import { getAllowedStores } from '@/api/timeclock'
import { queryClient } from '@/api/query'
import {
  cartSubtotal,
  clearCart,
  removeFromCart,
  setQuantity,
  useCart,
} from '@/modules/pos/cart'
import { Body, Button, Card, EmptyState, H2, Screen } from '@/components/ui'
import { OfflineBanner } from '@/components/OfflineBanner'
import { colors, font, radius, spacing } from '@/theme'

const money = (n: number) => `$${(n || 0).toFixed(2)}`
const PAYMENT_METHODS = ['cash', 'card'] as const

export default function Checkout() {
  const router = useRouter()
  const cart = useCart()
  const [store, setStore] = useState<string | null>(null)
  const [method, setMethod] = useState<(typeof PAYMENT_METHODS)[number]>('card')
  const [busy, setBusy] = useState(false)

  // Reuse the rep's clock-in store list as the sell-at location list.
  const allowed = useQuery({ queryKey: ['timeclock', 'allowed-stores'], queryFn: getAllowedStores })
  const stores = allowed.data?.stores ?? []
  const selectedStore = store ?? allowed.data?.home_store ?? stores[0] ?? null

  const subtotal = cartSubtotal(cart)

  if (cart.length === 0)
    return (
      <Screen>
        <EmptyState title="Cart is empty" subtitle="Add products from the POS tab." />
      </Screen>
    )

  const submit = async () => {
    if (!selectedStore) {
      Alert.alert('Select a store', 'Choose which store this sale is for.')
      return
    }
    setBusy(true)
    try {
      const payments: Payment[] = [{ method, amount: subtotal }]
      const res = await checkoutDurable({
        sale: { store_code: selectedStore, register_number: 1 },
        items: cart,
        payments,
      })
      clearCart()
      queryClient.invalidateQueries({ queryKey: ['pos'] })
      if ('queued' in res) {
        Alert.alert('Saved offline', 'The sale will be completed automatically when you reconnect.')
      } else {
        Alert.alert('Sale complete', `${money(subtotal)} · ${selectedStore}`)
      }
      router.back()
    } catch (e) {
      Alert.alert('Checkout failed', e instanceof Error ? e.message : 'Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Screen>
      <OfflineBanner />
      <ScrollView contentContainerStyle={styles.container}>
        <H2>Items</H2>
        <Card style={{ gap: spacing.sm }}>
          {cart.map((line) => (
            <View key={line.product_id} style={styles.line}>
              <View style={{ flex: 1 }}>
                <Text style={styles.lineName} numberOfLines={1}>
                  {line.short_name}
                </Text>
                <Body dim>
                  {money(line.unit_price)} each
                  {line.list_price != null && line.list_price !== line.unit_price
                    ? ` (list ${money(line.list_price)})`
                    : ''}
                </Body>
              </View>
              <View style={styles.qty}>
                <Stepper onPress={() => setQuantity(line.product_id, line.quantity - 1)} label="−" />
                <Text style={styles.qtyText}>{line.quantity}</Text>
                <Stepper onPress={() => setQuantity(line.product_id, line.quantity + 1)} label="+" />
              </View>
              <Text style={styles.lineTotal}>{money(line.unit_price * line.quantity)}</Text>
              <Pressable onPress={() => removeFromCart(line.product_id)} hitSlop={8}>
                <Text style={styles.remove}>✕</Text>
              </Pressable>
            </View>
          ))}
        </Card>

        <H2>Store</H2>
        <View style={styles.chips}>
          {stores.map((s) => {
            const on = s === selectedStore
            return (
              <Pressable key={s} onPress={() => setStore(s)} style={[styles.chip, on && styles.chipOn]}>
                <Text style={[styles.chipText, on && styles.chipTextOn]}>{s}</Text>
              </Pressable>
            )
          })}
          {stores.length === 0 && <Body dim>No store list available.</Body>}
        </View>

        <H2>Payment</H2>
        <View style={styles.chips}>
          {PAYMENT_METHODS.map((m) => {
            const on = m === method
            return (
              <Pressable key={m} onPress={() => setMethod(m)} style={[styles.chip, on && styles.chipOn]}>
                <Text style={[styles.chipText, on && styles.chipTextOn]}>{m.toUpperCase()}</Text>
              </Pressable>
            )
          })}
        </View>

        <Card style={{ gap: spacing.xs }}>
          <View style={styles.totalRow}>
            <Text style={styles.totalLabel}>Subtotal</Text>
            <Text style={styles.totalValue}>{money(subtotal)}</Text>
          </View>
          <Body dim>Tax is calculated by the register at completion.</Body>
        </Card>

        <Button title={`Charge ${money(subtotal)}`} variant="success" loading={busy} onPress={submit} />
      </ScrollView>
    </Screen>
  )
}

function Stepper({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <Pressable onPress={onPress} style={styles.stepper} hitSlop={6}>
      <Text style={styles.stepperText}>{label}</Text>
    </Pressable>
  )
}

const styles = StyleSheet.create({
  container: { padding: spacing.lg, gap: spacing.md, paddingBottom: spacing.xxl },
  line: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  lineName: { color: colors.text, fontSize: font.body, fontWeight: '600' },
  qty: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  qtyText: { color: colors.text, fontSize: font.body, fontWeight: '700', minWidth: 20, textAlign: 'center' },
  stepper: {
    width: 30,
    height: 30,
    borderRadius: radius.sm,
    backgroundColor: colors.surfaceAlt,
    alignItems: 'center',
    justifyContent: 'center',
  },
  stepperText: { color: colors.text, fontSize: font.h3, fontWeight: '700' },
  lineTotal: { color: colors.text, fontSize: font.body, fontWeight: '700', minWidth: 64, textAlign: 'right' },
  remove: { color: colors.danger, fontSize: font.body, paddingLeft: spacing.xs },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  chip: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    borderRadius: radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  chipOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText: { color: colors.text, fontSize: font.small, fontWeight: '600' },
  chipTextOn: { color: colors.primaryText },
  totalRow: { flexDirection: 'row', justifyContent: 'space-between' },
  totalLabel: { color: colors.textDim, fontSize: font.body },
  totalValue: { color: colors.text, fontSize: font.h3, fontWeight: '800' },
})
