import React, { useState } from 'react'
import { Alert, Image, KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native'
import { useRouter } from 'expo-router'
import { useQuery } from '@tanstack/react-query'
import * as ImagePicker from 'expo-image-picker'

import { importReceipt, previewReceipt, type ParsedReceipt } from '@/api/pos'
import { getAllowedStores } from '@/api/timeclock'
import { queryClient } from '@/api/query'
import { Body, Button, Card, H1, H2, Input, Screen } from '@/components/ui'
import { colors, font, radius, spacing } from '@/theme'

const money = (n?: number | null) => (n || n === 0 ? `$${Number(n).toFixed(2)}` : '—')

// Photograph a receipt from another POS → OCR preview → confirm → a real, searchable sale.
// Two-step by design: we OCR (dry-run, no write) first and show the parsed fields so the user can
// confirm before anything is created; only "Import as sale" writes.
export default function ReceiptImport() {
  const router = useRouter()
  const [image, setImage] = useState<string | null>(null) // base64
  const [ext, setExt] = useState<'jpg' | 'png'>('jpg')
  const [preview, setPreview] = useState<string | null>(null) // data URL for display
  const [parsed, setParsed] = useState<ParsedReceipt | null>(null)
  const [ocrNote, setOcrNote] = useState<string | null>(null)
  const [notes, setNotes] = useState('')
  const [store, setStore] = useState<string | null>(null)
  const [busy, setBusy] = useState<'ocr' | 'import' | null>(null)

  const allowed = useQuery({ queryKey: ['timeclock', 'allowed-stores'], queryFn: getAllowedStores })
  const stores = allowed.data?.stores ?? []
  const selectedStore = store ?? allowed.data?.home_store ?? stores[0] ?? null

  const runOcr = async (b64: string, e: 'jpg' | 'png') => {
    setBusy('ocr')
    setParsed(null)
    setOcrNote(null)
    try {
      const res = await previewReceipt(b64, e)
      setParsed(res.parsed || {})
      const readAnything = res.parsed && (res.parsed.items?.length || res.parsed.total || res.parsed.customer_name)
      if (!readAnything) {
        setOcrNote(
          (res.raw_ocr && (res.raw_ocr.error || res.raw_ocr.skipped)) ||
            "Couldn't read this receipt automatically. You can still import it and edit the sale later.",
        )
      }
    } catch (e2) {
      Alert.alert('Scan failed', e2 instanceof Error ? e2.message : 'Try another photo.')
    } finally {
      setBusy(null)
    }
  }

  const pick = async (from: 'camera' | 'library') => {
    const perm =
      from === 'camera'
        ? await ImagePicker.requestCameraPermissionsAsync()
        : await ImagePicker.requestMediaLibraryPermissionsAsync()
    if (!perm.granted) {
      Alert.alert(
        from === 'camera' ? 'Camera permission needed' : 'Photos permission needed',
        'Enable it in Settings to add a receipt photo.',
      )
      return
    }
    const opts: ImagePicker.ImagePickerOptions = {
      base64: true,
      quality: 0.6, // smaller upload → faster OCR round-trip; text stays legible
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
    }
    const res =
      from === 'camera'
        ? await ImagePicker.launchCameraAsync(opts)
        : await ImagePicker.launchImageLibraryAsync(opts)
    if (res.canceled || !res.assets?.[0]?.base64) return
    const a = res.assets[0]
    const e: 'jpg' | 'png' = (a.uri || '').toLowerCase().endsWith('.png') ? 'png' : 'jpg'
    setImage(a.base64!)
    setExt(e)
    setPreview(`data:image/${e === 'png' ? 'png' : 'jpeg'};base64,${a.base64}`)
    void runOcr(a.base64!, e)
  }

  const doImport = async () => {
    if (!image) return
    setBusy('import')
    try {
      const res = await importReceipt({
        image,
        ext,
        store_code: selectedStore || undefined,
        notes: notes.trim() || undefined,
      })
      if (!res.imported) {
        Alert.alert('Not imported', res.message || "Couldn't read the receipt — enter it manually.")
        return
      }
      queryClient.invalidateQueries({ queryKey: ['pos', 'receipt-imports'] })
      queryClient.invalidateQueries({ queryKey: ['pos', 'sales'] })
      Alert.alert('Imported', 'The receipt was saved as a sale.', [
        { text: 'View imported', onPress: () => router.replace('/pos/receipts') },
        { text: 'Done', onPress: () => router.back() },
      ])
    } catch (e) {
      Alert.alert('Import failed', e instanceof Error ? e.message : 'Try again.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <Screen>
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView
          contentContainerStyle={styles.container}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="on-drag"
        >
          <H1>Import a receipt</H1>
          <Body dim>Photograph a printed receipt from another system. We read it and create a sale.</Body>

          <View style={styles.captureRow}>
            <View style={{ flex: 1 }}>
              <Button title="📷 Take photo" onPress={() => pick('camera')} />
            </View>
            <View style={{ flex: 1 }}>
              <Button title="🖼 Choose photo" variant="secondary" onPress={() => pick('library')} />
            </View>
          </View>

          {preview && <Image source={{ uri: preview }} style={styles.preview} resizeMode="contain" />}

          {busy === 'ocr' && <Body dim>Reading the receipt…</Body>}
          {ocrNote && (
            <Card style={styles.noteCard}>
              <Body>{ocrNote}</Body>
            </Card>
          )}

          {parsed && (
            <Card>
              <H2>What we read</H2>
              <Field label="Customer" value={parsed.customer_name} />
              <Field label="Phone" value={parsed.phone} />
              <Field label="Device" value={parsed.device_name} />
              <Field label="IMEI" value={parsed.imei || (parsed.imeis && parsed.imeis[0])} />
              <Field label="Sale date" value={parsed.sale_date} />
              <Field label="Payment" value={parsed.payment_method} />
              <View style={styles.divider} />
              <Field label="Subtotal" value={money(parsed.subtotal)} />
              <Field label="Tax" value={money(parsed.tax)} />
              <Field label="Total" value={money(parsed.total)} strong />
              {!!(parsed.items && parsed.items.length) && (
                <>
                  <View style={styles.divider} />
                  <Text style={styles.itemsHead}>{parsed.items.length} line item(s)</Text>
                  {parsed.items.map((it, i) => (
                    <View key={i} style={styles.itemRow}>
                      <Text style={styles.itemDesc} numberOfLines={1}>
                        {it.description || it.imei || 'Item'}
                      </Text>
                      <Text style={styles.itemAmt}>
                        {it.qty && it.qty > 1 ? `${it.qty} × ` : ''}
                        {money(it.unit_price)}
                      </Text>
                    </View>
                  ))}
                </>
              )}
            </Card>
          )}

          {image && (
            <>
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
                {stores.length === 0 && <Body dim>No store list available — it will import without a store.</Body>}
              </View>

              <H2>Note (optional)</H2>
              <Body dim>Saved on the import and copied to the customer.</Body>
              <Input
                placeholder="e.g. trade-in from old carrier, warranty swap…"
                value={notes}
                onChangeText={setNotes}
                multiline
                style={styles.notesInput}
              />

              <Button
                title="Import as sale"
                variant="success"
                onPress={doImport}
                loading={busy === 'import'}
                disabled={busy !== null}
              />
            </>
          )}

          <View style={{ height: spacing.xxl }} />
        </ScrollView>
      </KeyboardAvoidingView>
    </Screen>
  )
}

function Field({ label, value, strong }: { label: string; value?: string | null; strong?: boolean }) {
  return (
    <View style={styles.field}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <Text style={[styles.fieldValue, strong && styles.fieldValueStrong]} numberOfLines={1}>
        {value || '—'}
      </Text>
    </View>
  )
}

const styles = StyleSheet.create({
  container: { padding: spacing.lg, gap: spacing.md },
  captureRow: { flexDirection: 'row', gap: spacing.md },
  preview: {
    width: '100%',
    height: 220,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceAlt,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
  },
  noteCard: { backgroundColor: colors.surfaceAlt },
  field: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: spacing.xs, gap: spacing.md },
  fieldLabel: { color: colors.textDim, fontSize: font.small },
  fieldValue: { color: colors.text, fontSize: font.body, flexShrink: 1, textAlign: 'right' },
  fieldValueStrong: { fontWeight: '800' },
  divider: { height: StyleSheet.hairlineWidth, backgroundColor: colors.border, marginVertical: spacing.sm },
  itemsHead: { color: colors.textDim, fontSize: font.small, marginBottom: spacing.xs },
  itemRow: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 2, gap: spacing.md },
  itemDesc: { color: colors.text, fontSize: font.small, flex: 1 },
  itemAmt: { color: colors.text, fontSize: font.small, fontWeight: '600' },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  chip: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  chipOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText: { color: colors.text, fontSize: font.small, fontWeight: '700' },
  chipTextOn: { color: colors.primaryText },
  notesInput: { minHeight: 72, textAlignVertical: 'top' },
})
