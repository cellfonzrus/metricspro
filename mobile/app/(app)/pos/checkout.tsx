import React, { useState } from 'react'
import {
  Alert,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native'
import { useRouter } from 'expo-router'
import { useQuery } from '@tanstack/react-query'

import { checkoutDurable, resolveTax, type CartLine } from '@/api/pos'
import { getAllowedStores } from '@/api/timeclock'
import { queryClient } from '@/api/query'
import {
  cartSubtotal,
  clearCart,
  removeFromCart,
  setQuantity,
  setUnitPrice,
  useCart,
} from '@/modules/pos/cart'
import { buildCheckoutBody, type TaxContext } from '@/modules/pos/checkout-body'
import { CHECKOUT_NO_TAX_RATE } from '@/modules/pos/checkout-contract'
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

  // THE TAX THIS STORE CHARGES, from the server (§POS-20). The till asks; it does not work it out.
  // `rate` arrives as a PERCENT and the money rule wants a fraction, so the conversion happens here
  // once — the same conversion the web register makes.
  const taxQ = useQuery({
    queryKey: ['pos', 'tax', selectedStore],
    queryFn: () => resolveTax(selectedStore as string),
    enabled: Boolean(selectedStore),
  })
  const tax: TaxContext = {
    taxRate: taxQ.data?.rate != null ? Number(taxQ.data.rate) / 100 : null,
    taxMethod: String(taxQ.data?.pricing?.tax_method ?? 'sale_price'),
    capEnabled: Boolean(taxQ.data?.pricing?.tax_cap_enabled),
    capAmount: Number(taxQ.data?.pricing?.tax_cap_amount ?? 0),
  }

  if (cart.length === 0)
    return (
      <Screen>
        <EmptyState title="Cart is empty" subtitle="Add products from the POS tab." />
      </Screen>
    )

  // What this sale WOULD be. Built by the one home, so the figures on screen are the figures sent —
  // a screen that totals the cart its own way is how a till comes to disagree with the server.
  const preview = selectedStore
    ? buildCheckoutBody({ storeCode: selectedStore, lines: cart, tender: method, tax })
    : { body: null, refusal: 'Choose which store this sale is for.' as string }

  const subtotal = preview.body?.sale.subtotal ?? cartSubtotal(cart)
  const taxTotal = preview.body?.sale.tax_total ?? 0
  const total = preview.body?.sale.total ?? subtotal

  const submit = async () => {
    // The refusal is the builder's own sentence — the screen does not word a second one.
    if (!preview.body) {
      Alert.alert('Cannot ring this sale', preview.refusal)
      return
    }
    setBusy(true)
    try {
      const res = await checkoutDurable(preview.body)
      clearCart()
      queryClient.invalidateQueries({ queryKey: ['pos'] })
      if ('queued' in res) {
        Alert.alert('Saved offline', 'The sale will be completed automatically when you reconnect.')
      } else {
        Alert.alert('Sale complete', `${money(total)} · ${selectedStore}`)
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
        <H2>Items</H2>
        <Card style={{ gap: spacing.sm }}>
          {cart.map((line) => (
            <View key={line.product_id} style={styles.line}>
              <View style={{ flex: 1 }}>
                <Text style={styles.lineName} numberOfLines={1}>
                  {line.short_name}
                </Text>
                <View style={styles.priceRow}>
                  <Text style={styles.priceLabel}>$</Text>
                  <PriceInput line={line} />
                  <Text style={styles.priceEach}>each</Text>
                  {line.list_price != null && line.list_price !== line.unit_price ? (
                    <Text style={styles.listStrike}>{money(line.list_price)}</Text>
                  ) : null}
                </View>
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
          <View style={styles.totalRow}>
            <Text style={styles.totalLabel}>
              {tax.taxRate != null ? `Tax (${(tax.taxRate * 100).toFixed(3).replace(/\.?0+$/, '')}%)` : 'Tax'}
            </Text>
            <Text style={styles.totalValue}>{money(taxTotal)}</Text>
          </View>
          <View style={styles.totalRow}>
            <Text style={styles.totalLabel}>Total</Text>
            <Text style={styles.totalValue}>{money(total)}</Text>
          </View>
          {/* A taxable sale with no rate charges nothing, and that is not recoverable after the
              customer leaves — so it is said out loud rather than shown as a quiet $0.00. */}
          {taxQ.data && taxQ.data.scope === 'none' && <Body dim>{CHECKOUT_NO_TAX_RATE}</Body>}
          {taxQ.data && taxQ.data.scope !== 'none' && taxQ.data.scope !== 'store' && (
            <Body dim>This store has no rate of its own — charging the {taxQ.data.scope} rate.</Body>
          )}
          {/* The one thing the phone will not price. Shown before the button is pressed. */}
          {preview.refusal && <Body dim>{preview.refusal}</Body>}
        </Card>

        <Button
          title={`Charge ${money(total)}`}
          variant="success"
          loading={busy}
          disabled={!preview.body}
          onPress={submit}
        />
      </ScrollView>
      </KeyboardAvoidingView>
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

// Editable per-line price. Holds LOCAL text while editing (so the cursor doesn't jump) and commits
// the parsed number to the cart on blur / "Done".
function PriceInput({ line }: { line: CartLine }) {
  const [text, setText] = useState(String(line.unit_price ?? ''))
  const commit = () => {
    const n = parseFloat(text.replace(/[^0-9.]/g, ''))
    const price = Number.isFinite(n) ? n : 0
    setUnitPrice(line.product_id, price)
    setText(String(price))
  }
  return (
    <TextInput
      value={text}
      onChangeText={setText}
      onBlur={commit}
      onSubmitEditing={commit}
      keyboardType="decimal-pad"
      returnKeyType="done"
      selectTextOnFocus
      style={styles.priceInput}
    />
  )
}

const styles = StyleSheet.create({
  container: { padding: spacing.lg, gap: spacing.md, paddingBottom: spacing.xxl },
  line: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  lineName: { color: colors.text, fontSize: font.body, fontWeight: '600' },
  priceRow: { flexDirection: 'row', alignItems: 'center', gap: 4, marginTop: 2 },
  priceLabel: { color: colors.textDim, fontSize: font.small },
  priceInput: {
    color: colors.text,
    fontSize: font.body,
    fontWeight: '600',
    minWidth: 56,
    paddingVertical: 2,
    paddingHorizontal: spacing.xs,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  priceEach: { color: colors.textDim, fontSize: font.small },
  listStrike: { color: colors.textDim, fontSize: font.tiny, textDecorationLine: 'line-through', marginLeft: spacing.xs },
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
