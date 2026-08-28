import React, { useEffect, useMemo, useState } from 'react'
import { Alert, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native'
import { useRouter } from 'expo-router'
import * as DocumentPicker from 'expo-document-picker'
import * as FileSystem from 'expo-file-system'
import * as Print from 'expo-print'

import {
  getReceiptFormats,
  importStructuredReceipt,
  previewStructuredReceipt,
  receiptPrintPath,
  type ReceiptDocument,
} from '@/api/pos'
import { apiGetText } from '@/api/client'
import { queryClient } from '@/api/query'
import { Body, Button, Card, H1, H2, Screen } from '@/components/ui'
import { colors, font, radius, spacing } from '@/theme'

const money = (v: unknown) => {
  const n = typeof v === 'number' ? v : parseFloat(String(v ?? '').replace(/[^0-9.-]/g, ''))
  return Number.isFinite(n) ? `$${n.toFixed(2)}` : '—'
}
const EDITABLE = new Set(['desc', 'qty', 'money', 'money_total'])

// Structured PDF import (RQ / B2B): pick the POS format → choose a PDF → review + edit the parsed
// fields → save → reprint in the same format. The editable Document comes from the backend; nothing
// about a format is hardcoded here (columns/totals are whatever the parser returned).
export default function ReceiptImportPdf() {
  const router = useRouter()
  const [formats, setFormats] = useState<{ source: string; label: string }[]>([])
  const [source, setSource] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [doc, setDoc] = useState<ReceiptDocument | null>(null)
  const [notes, setNotes] = useState('')
  const [savedId, setSavedId] = useState<string | null>(null)

  useEffect(() => {
    getReceiptFormats()
      .then((r) => {
        setFormats(r.formats || [])
        setSource(r.default_source || r.formats?.[0]?.source || '')
      })
      .catch(() => {})
  }, [])

  const pickPdf = async () => {
    if (!source) {
      Alert.alert('Pick a POS', 'Choose which POS this receipt is from first.')
      return
    }
    const res = await DocumentPicker.getDocumentAsync({ type: 'application/pdf', copyToCacheDirectory: true })
    if (res.canceled || !res.assets?.[0]?.uri) return
    setBusy('parse')
    setSavedId(null)
    try {
      const b64 = await FileSystem.readAsStringAsync(res.assets[0].uri, { encoding: FileSystem.EncodingType.Base64 })
      const r = await previewStructuredReceipt(source, b64)
      setDoc(r.document)
      setNotes('')
    } catch (e) {
      Alert.alert('Could not read PDF', e instanceof Error ? e.message : 'Try another file.')
    } finally {
      setBusy(null)
    }
  }

  const setCell = (i: number, key: string, val: string) =>
    setDoc((d) =>
      d ? { ...d, items: d.items!.map((it, idx) => (idx === i ? { ...it, cells: { ...it.cells, [key]: val } } : it)) } : d,
    )
  const setTotal = (i: number, val: string) =>
    setDoc((d) => {
      if (!d) return d
      const n = parseFloat(val.replace(/[^0-9.-]/g, ''))
      return { ...d, totals: d.totals!.map((t, idx) => (idx === i ? { ...t, amount: Number.isFinite(n) ? n : null } : t)) }
    })

  const save = async () => {
    if (!doc) return
    setBusy('save')
    try {
      const r = await importStructuredReceipt({ pos_source: doc.pos_source || source, document: doc, notes: notes || undefined })
      setSavedId(r.import_id || null)
      queryClient.invalidateQueries({ queryKey: ['pos', 'receipt-imports'] })
      Alert.alert('Imported', 'Saved. You can reprint it in the original format.')
    } catch (e) {
      Alert.alert('Save failed', e instanceof Error ? e.message : 'Try again.')
    } finally {
      setBusy(null)
    }
  }

  const print = async () => {
    if (!savedId) return
    setBusy('print')
    try {
      const html = await apiGetText(receiptPrintPath(savedId))
      await Print.printAsync({ html })
    } catch (e) {
      Alert.alert('Print failed', e instanceof Error ? e.message : 'Try again.')
    } finally {
      setBusy(null)
    }
  }

  const cols = doc?.columns || []
  const editableCols = useMemo(() => cols.filter((c) => EDITABLE.has(c.kind)), [cols])
  const readonlyCols = useMemo(() => cols.filter((c) => !EDITABLE.has(c.kind)), [cols])

  return (
    <Screen>
      <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag">
        <H1>Import receipt (PDF)</H1>
        <Body dim>Upload a receipt from another POS, review &amp; edit, then reprint in the same format.</Body>

        {!doc && (
          <Card>
            <H2>Which POS is this from?</H2>
            <View style={styles.chips}>
              {formats.map((f) => {
                const on = f.source === source
                return (
                  <Pressable key={f.source} onPress={() => setSource(f.source)} style={[styles.chip, on && styles.chipOn]}>
                    <Text style={[styles.chipText, on && styles.chipTextOn]}>{f.label}</Text>
                  </Pressable>
                )
              })}
            </View>
            <View style={{ height: spacing.md }} />
            <Button title={busy === 'parse' ? 'Reading…' : '📄 Choose a PDF'} onPress={pickPdf} loading={busy === 'parse'} />
          </Card>
        )}

        {doc && (
          <>
            <Card>
              <View style={styles.headRow}>
                <Text style={styles.title}>
                  {doc.title} · <Text style={styles.dim}>{doc.format_label}</Text>
                </Text>
              </View>
              {(doc.meta || []).map((m) => (
                <View key={m.key} style={styles.metaRow}>
                  <Text style={styles.metaLabel}>{m.label}</Text>
                  <Text style={styles.metaVal}>{m.value}</Text>
                </View>
              ))}
              {!!doc.bill_to?.lines?.length && (
                <View style={{ marginTop: spacing.sm }}>
                  <Text style={styles.metaLabel}>Bill To</Text>
                  {doc.bill_to.lines.map((l, i) => (
                    <Text key={i} style={styles.line}>
                      {l}
                    </Text>
                  ))}
                </View>
              )}
            </Card>

            <H2>Items ({doc.items?.length || 0})</H2>
            {(doc.items || []).map((it, i) => (
              <Card key={i} style={styles.itemCard}>
                {readonlyCols.map((c) => (
                  <Text key={c.key} style={styles.roCell} numberOfLines={1}>
                    <Text style={styles.roLabel}>{c.label}: </Text>
                    {it.cells[c.key] || '—'}
                  </Text>
                ))}
                <View style={styles.editRow}>
                  {editableCols.map((c) => (
                    <View key={c.key} style={[styles.editField, c.kind === 'desc' && styles.editFieldWide]}>
                      <Text style={styles.roLabel}>{c.label}</Text>
                      <TextInput
                        value={it.cells[c.key] ?? ''}
                        onChangeText={(v) => setCell(i, c.key, v)}
                        style={styles.input}
                        keyboardType={c.kind === 'qty' || c.kind.startsWith('money') ? 'numbers-and-punctuation' : 'default'}
                      />
                    </View>
                  ))}
                </View>
              </Card>
            ))}

            <H2>Totals</H2>
            <Card>
              {(doc.totals || []).map((t, i) => (
                <View key={t.key} style={styles.totalRow}>
                  <Text style={styles.totalLabel}>{t.label}</Text>
                  {t.editable ? (
                    <TextInput
                      value={t.amount == null ? '' : String(t.amount)}
                      onChangeText={(v) => setTotal(i, v)}
                      style={[styles.input, styles.totalInput]}
                      keyboardType="numbers-and-punctuation"
                    />
                  ) : (
                    <Text style={styles.totalVal}>{money(t.amount)}</Text>
                  )}
                </View>
              ))}
              {(doc.payments || []).map((p, i) => (
                <View key={`p${i}`} style={styles.totalRow}>
                  <Text style={styles.totalLabel}>{p.label}</Text>
                  <Text style={styles.totalVal}>{money(p.amount)}</Text>
                </View>
              ))}
            </Card>

            {!savedId && (
              <>
                <H2>Note (optional)</H2>
                <TextInput placeholder="e.g. bulk B2B order" placeholderTextColor={colors.textDim} value={notes} onChangeText={setNotes} style={styles.input} />
                <View style={{ height: spacing.md }} />
                <Button title="Save & import" variant="success" onPress={save} loading={busy === 'save'} disabled={busy !== null} />
              </>
            )}
            {savedId && (
              <>
                <View style={{ height: spacing.md }} />
                <Button title={busy === 'print' ? 'Preparing…' : '🖨 Print / share'} onPress={print} loading={busy === 'print'} />
                <View style={{ height: spacing.sm }} />
                <Button title="Done" variant="secondary" onPress={() => router.replace('/pos/receipts')} />
              </>
            )}
          </>
        )}
        <View style={{ height: spacing.xxl }} />
      </ScrollView>
    </Screen>
  )
}

const styles = StyleSheet.create({
  container: { padding: spacing.lg, gap: spacing.sm },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  chip: { paddingHorizontal: spacing.md, paddingVertical: spacing.sm, borderRadius: radius.pill, borderWidth: StyleSheet.hairlineWidth, borderColor: colors.border, backgroundColor: colors.surface },
  chipOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText: { color: colors.text, fontSize: font.small, fontWeight: '700' },
  chipTextOn: { color: colors.primaryText },
  headRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  title: { color: colors.text, fontSize: font.body, fontWeight: '800' },
  dim: { color: colors.textDim, fontWeight: '400' },
  metaRow: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 1, gap: spacing.md },
  metaLabel: { color: colors.textDim, fontSize: font.small },
  metaVal: { color: colors.text, fontSize: font.small, fontWeight: '600', flexShrink: 1, textAlign: 'right' },
  line: { color: colors.text, fontSize: font.small },
  itemCard: { gap: spacing.xs },
  roCell: { color: colors.text, fontSize: font.small },
  roLabel: { color: colors.textDim },
  editRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginTop: spacing.xs },
  editField: { minWidth: 80 },
  editFieldWide: { flexBasis: '100%' },
  input: { backgroundColor: colors.surfaceAlt, borderRadius: radius.sm, borderWidth: StyleSheet.hairlineWidth, borderColor: colors.border, color: colors.text, paddingHorizontal: spacing.sm, paddingVertical: spacing.xs, fontSize: font.body },
  totalRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 2 },
  totalLabel: { color: colors.textDim, fontSize: font.small },
  totalVal: { color: colors.text, fontSize: font.body, fontWeight: '700' },
  totalInput: { minWidth: 110, textAlign: 'right' },
})
