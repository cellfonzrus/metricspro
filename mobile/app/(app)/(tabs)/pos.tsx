import React, { useState } from 'react'
import { FlatList, Pressable, StyleSheet, Text, View } from 'react-native'
import { useRouter } from 'expo-router'
import { useQuery } from '@tanstack/react-query'

import { searchProducts, type Product } from '@/api/pos'
import { addToCart, cartCount, cartSubtotal, useCart } from '@/modules/pos/cart'
import { Body, EmptyState, ErrorView, Input, Loading, Screen } from '@/components/ui'
import { OfflineBanner } from '@/components/OfflineBanner'
import { colors, font, radius, spacing } from '@/theme'

const money = (n: number) => `$${(n || 0).toFixed(2)}`

export default function Pos() {
  const router = useRouter()
  const [search, setSearch] = useState('')
  const cart = useCart()

  const products = useQuery({
    queryKey: ['pos', 'products', search],
    queryFn: () => searchProducts({ search }),
  })

  const rows = products.data?.products ?? []

  return (
    <Screen>
      <OfflineBanner />
      <View style={styles.searchWrap}>
        <Input
          placeholder="Search products, UPC…"
          value={search}
          onChangeText={setSearch}
          autoCapitalize="none"
          autoCorrect={false}
          returnKeyType="search"
        />
      </View>

      {products.isLoading ? (
        <Loading label="Loading catalog…" />
      ) : products.isError ? (
        <ErrorView message={(products.error as Error)?.message ?? 'Failed to load'} onRetry={products.refetch} />
      ) : rows.length === 0 ? (
        <EmptyState title="No products" subtitle={search ? 'Try a different search.' : 'The catalog is empty.'} />
      ) : (
        <FlatList
          data={rows}
          keyExtractor={(p) => p.id}
          contentContainerStyle={styles.list}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="on-drag"
          renderItem={({ item }) => <ProductRow product={item} onAdd={() => addToCart(item)} />}
        />
      )}

      {cart.length > 0 && (
        <Pressable style={styles.cartBar} onPress={() => router.push('/pos/checkout')}>
          <Text style={styles.cartBarText}>
            {cartCount(cart)} item(s) · {money(cartSubtotal(cart))}
          </Text>
          <Text style={styles.cartBarCta}>Checkout →</Text>
        </Pressable>
      )}
    </Screen>
  )
}

function ProductRow({ product, onAdd }: { product: Product; onAdd: () => void }) {
  return (
    <Pressable style={styles.row} onPress={onAdd}>
      <View style={{ flex: 1 }}>
        <Text style={styles.name} numberOfLines={1}>
          {product.short_name}
        </Text>
        <Body dim>
          {[product.category_name, product.upc].filter(Boolean).join(' · ') || product.system_category || '—'}
        </Body>
      </View>
      <View style={styles.priceCol}>
        <Text style={styles.price}>{money(product.retail_price ?? 0)}</Text>
        <View style={styles.addBtn}>
          <Text style={styles.addBtnText}>+ Add</Text>
        </View>
      </View>
    </Pressable>
  )
}

const styles = StyleSheet.create({
  searchWrap: { padding: spacing.lg, paddingBottom: spacing.sm },
  list: { paddingHorizontal: spacing.lg, paddingBottom: 120, gap: spacing.sm },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    padding: spacing.md,
    gap: spacing.md,
  },
  name: { color: colors.text, fontSize: font.body, fontWeight: '600' },
  priceCol: { alignItems: 'flex-end', gap: spacing.xs },
  price: { color: colors.text, fontSize: font.body, fontWeight: '700' },
  addBtn: { backgroundColor: colors.primary, borderRadius: radius.sm, paddingHorizontal: spacing.md, paddingVertical: spacing.xs },
  addBtnText: { color: colors.primaryText, fontSize: font.small, fontWeight: '700' },
  cartBar: {
    position: 'absolute',
    left: spacing.lg,
    right: spacing.lg,
    bottom: spacing.lg,
    backgroundColor: colors.primary,
    borderRadius: radius.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  cartBarText: { color: colors.primaryText, fontSize: font.body, fontWeight: '700' },
  cartBarCta: { color: colors.primaryText, fontSize: font.body, fontWeight: '800' },
})
