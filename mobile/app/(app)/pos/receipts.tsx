import React, { useState } from 'react'
import { FlatList, Pressable, StyleSheet, Text, View } from 'react-native'
import { useRouter } from 'expo-router'
import { keepPreviousData, useQuery } from '@tanstack/react-query'

import { listReceiptImports, type ReceiptImport } from '@/api/pos'
import { Body, EmptyState, ErrorView, Input, Loading, Screen } from '@/components/ui'
import { OfflineBanner } from '@/components/OfflineBanner'
import { colors, font, radius, spacing } from '@/theme'

const money = (n?: number | null) => (n || n === 0 ? `$${Number(n).toFixed(2)}` : '—')

// Find an imported receipt by IMEI / phone / customer name (backend blind-index search for IMEI,
// plain search for name/phone). One free-text box — the backend routes a full IMEI to its index.
export default function Receipts() {
  const router = useRouter()
  const [q, setQ] = useState('')

  const imports = useQuery({
    queryKey: ['pos', 'receipt-imports', q.trim()],
    queryFn: () => listReceiptImports({ q: q.trim() }),
    placeholderData: keepPreviousData,
  })

  const rows = imports.data?.receipt_imports ?? []

  return (
    <Screen>
      <OfflineBanner />
      <View style={styles.searchWrap}>
        <Input
          placeholder="Search IMEI, phone, or customer…"
          value={q}
          onChangeText={setQ}
          autoCapitalize="none"
          autoCorrect={false}
          returnKeyType="search"
        />
      </View>

      <Pressable style={styles.importBtn} onPress={() => router.push('/pos/receipt-import')}>
        <Text style={styles.importBtnText}>＋ Import a new receipt</Text>
      </Pressable>

      {imports.isLoading ? (
        <Loading label="Loading…" />
      ) : imports.isError ? (
        <ErrorView message={(imports.error as Error)?.message ?? 'Failed to load'} onRetry={imports.refetch} />
      ) : rows.length === 0 ? (
        <EmptyState
          title={q.trim() ? 'No matches' : 'No imported receipts yet'}
          subtitle={q.trim() ? 'Try an IMEI, phone or name.' : 'Import one from a printed receipt.'}
        />
      ) : (
        <FlatList
          data={rows}
          keyExtractor={(r) => r.id}
          contentContainerStyle={styles.list}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="on-drag"
          onRefresh={imports.refetch}
          refreshing={imports.isFetching}
          renderItem={({ item }) => <Row item={item} />}
        />
      )}
    </Screen>
  )
}

function Row({ item }: { item: ReceiptImport }) {
  const title = item.customer_name || item.device_name || item.imei || 'Imported receipt'
  const sub = [item.phone, item.imei, item.store_code, item.sale_date].filter(Boolean).join(' · ')
  return (
    <View style={styles.card}>
      <View style={styles.cardHead}>
        <Text style={styles.name} numberOfLines={1}>
          {title}
        </Text>
        <Text style={styles.total}>{money(item.total)}</Text>
      </View>
      {!!sub && (
        <Body dim>
          {sub}
        </Body>
      )}
      {!!item.notes && (
        <Text style={styles.note} numberOfLines={2}>
          {item.notes}
        </Text>
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  searchWrap: { padding: spacing.lg, paddingBottom: spacing.sm },
  importBtn: {
    marginHorizontal: spacing.lg,
    marginBottom: spacing.sm,
    paddingVertical: spacing.md,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.primary,
    alignItems: 'center',
    backgroundColor: colors.surface,
  },
  importBtnText: { color: colors.primary, fontSize: font.body, fontWeight: '800' },
  list: { paddingHorizontal: spacing.lg, paddingBottom: spacing.xxl, gap: spacing.sm },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    padding: spacing.md,
    gap: spacing.xs,
  },
  cardHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.sm },
  name: { color: colors.text, fontSize: font.body, fontWeight: '700', flex: 1 },
  total: { color: colors.success, fontSize: font.body, fontWeight: '800' },
  note: { color: colors.textDim, fontSize: font.small, fontStyle: 'italic' },
})
