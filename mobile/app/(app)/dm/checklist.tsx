import React, { useMemo, useState } from 'react'
import { Modal, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native'
import { useQuery } from '@tanstack/react-query'
import * as Location from 'expo-location'

import { useAuth } from '@/auth/AuthContext'
import { atLeast, scopeOf } from '@/lib/scope'
import {
  createVisit, getChecklistItems, getVisitStores, getVisits, submitVisit, updateVisit,
  type ChecklistItem, type Visit, type VisitAccessory, type VisitResponse, type VisitStore,
} from '@/api/dm'
import { Body, Button, EmptyState, ErrorView, Input, Loading, Screen } from '@/components/ui'
import { SectionTitle } from '@/components/dash'
import { colors, font, radius, spacing } from '@/theme'

// DM Checklist — a district-manager's store-visit inspection. The DM checks in at a store (GPS
// captured, best-effort and time-boxed so it never hangs), runs through the org's checklist items
// (each a check + optional note), records any accessories seen, and submits. Mirrors the web Store
// Visit flow (/storevisit); every endpoint is org-scoped server-side. Gated to market scope (DMs).

function withTimeout<T>(p: Promise<T>, ms: number, fallback: T): Promise<T> {
  return Promise.race([p, new Promise<T>((res) => setTimeout(() => res(fallback), ms))])
}

// Best-effort GPS for the check-in; returns nothing rather than blocking if the user denies or it's slow.
async function tryGetLocation(): Promise<{ check_in_lat?: number; check_in_lng?: number; check_in_accuracy?: number }> {
  try {
    const perm = await withTimeout(
      Location.requestForegroundPermissionsAsync(), 4000,
      { status: 'undetermined' } as Awaited<ReturnType<typeof Location.requestForegroundPermissionsAsync>>,
    )
    if (perm.status !== 'granted') return {}
    const pos = await withTimeout(Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced }), 5000, null)
    if (!pos) return {}
    return { check_in_lat: pos.coords.latitude, check_in_lng: pos.coords.longitude, check_in_accuracy: pos.coords.accuracy ?? undefined }
  } catch {
    return {}
  }
}

const asArray = <T,>(d: unknown, key: string): T[] =>
  Array.isArray(d) ? (d as T[]) : (((d as Record<string, unknown>)?.[key] as T[]) || [])

const STATUS: Record<string, [string, string]> = {
  submitted: ['Submitted', colors.success], in_progress: ['In progress', colors.warning],
  checked_in: ['Checked in', colors.warning], draft: ['Draft', colors.textDim],
}

export default function DmChecklistScreen() {
  const { me } = useAuth()
  const [mode, setMode] = useState<'list' | 'form'>('list')
  const [visitId, setVisitId] = useState('')
  const [store, setStore] = useState<VisitStore | null>(null)

  const gated = atLeast(scopeOf(me), 'market')

  const visits = useQuery({
    queryKey: ['dm', 'visits'],
    queryFn: () => getVisits(),
    enabled: gated && mode === 'list',
  })

  if (!gated) {
    return <Screen><EmptyState title="Not available" subtitle="DM Checklist is for district managers and admins." /></Screen>
  }

  if (mode === 'form') {
    return (
      <VisitForm
        visitId={visitId} store={store}
        dmName={(me?.user?.name as string) || (me?.user?.email as string) || ''}
        onDone={() => { setMode('list'); setVisitId(''); setStore(null); visits.refetch() }}
        onCancel={() => { setMode('list'); setVisitId(''); setStore(null) }}
      />
    )
  }

  const rows = asArray<Visit>(visits.data, 'visits')

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={styles.c}
        refreshControl={<RefreshControl refreshing={visits.isFetching} onRefresh={visits.refetch} tintColor={colors.primary} />}
      >
        <StartCard
          dmName={(me?.user?.name as string) || (me?.user?.email as string) || ''}
          dmEmail={(me?.user?.email as string) || ''}
          onStarted={(id, s) => { setVisitId(id); setStore(s); setMode('form') }}
        />

        <SectionTitle>Recent visits</SectionTitle>
        {visits.isLoading ? (
          <Loading label="Loading visits…" />
        ) : visits.isError ? (
          <ErrorView message={(visits.error as Error)?.message || 'Could not load visits.'} onRetry={visits.refetch} />
        ) : rows.length === 0 ? (
          <EmptyState title="No visits yet" subtitle="Start a store visit above to run the inspection checklist." />
        ) : (
          rows.map((v, i) => {
            const [label, color] = STATUS[String(v.status)] || [String(v.status || '—'), colors.textDim]
            const resume = v.status !== 'submitted' && !!v.id
            return (
              <Pressable key={v.id || i} style={styles.card}
                disabled={!resume}
                onPress={() => { if (resume && v.id) { setVisitId(v.id); setStore({ store_code: v.store_code, address: v.store_address, market: v.market }); setMode('form') } }}>
                <View style={styles.cardTop}>
                  <Text style={styles.store} numberOfLines={1}>{v.store_code || '—'}</Text>
                  <Text style={[styles.badge, { color }]}>{label}</Text>
                </View>
                <Text style={styles.meta} numberOfLines={1}>
                  {(v.check_in_at ? new Date(v.check_in_at).toLocaleString() : '—')}
                  {v.dm_name ? ` · ${v.dm_name}` : ''}
                </Text>
                {resume ? <Text style={styles.resume}>Tap to resume</Text> : null}
              </Pressable>
            )
          })
        )}
      </ScrollView>
    </Screen>
  )
}

// ── Start card: pick a store and check in ──────────────────────────────────────────────────────────
function StartCard({ dmName, dmEmail, onStarted }: {
  dmName: string; dmEmail: string; onStarted: (visitId: string, store: VisitStore) => void
}) {
  const [pickerOpen, setPickerOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const stores = useQuery({ queryKey: ['dm', 'visit-stores'], queryFn: () => getVisitStores() })
  const list = useMemo(() => asArray<VisitStore>(stores.data, 'stores').filter((s) => (s.store_code || '').trim()), [stores.data])

  const start = async (s: VisitStore) => {
    setPickerOpen(false); setBusy(true); setErr('')
    try {
      const gps = await tryGetLocation()
      const v = await createVisit({
        store_code: s.store_code as string, store_address: s.address, market: s.market ?? undefined,
        dm_name: dmName, dm_email: dmEmail, check_in_at: new Date().toISOString(), ...gps,
      })
      if (!v?.id) throw new Error('Visit did not start — no id returned.')
      onStarted(v.id, s)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not start the visit.')
    } finally { setBusy(false) }
  }

  return (
    <View style={styles.startCard}>
      <Text style={styles.startTitle}>Start a store visit</Text>
      <Text style={styles.startSub}>Pick a store to check in (your location is captured) and run the checklist.</Text>
      {err ? <Text style={styles.err}>{err}</Text> : null}
      <Button title={busy ? 'Checking in…' : 'Check in at a store'} onPress={() => setPickerOpen(true)} loading={busy} />

      <Modal visible={pickerOpen} animationType="slide" transparent onRequestClose={() => setPickerOpen(false)}>
        <Pressable style={styles.backdrop} onPress={() => setPickerOpen(false)}>
          <Pressable style={styles.sheet} onPress={() => {}}>
            <Text style={styles.sheetTitle}>Choose a store</Text>
            {stores.isLoading ? (
              <Loading label="Loading stores…" />
            ) : (
              <ScrollView style={{ maxHeight: 440 }}>
                {list.map((s, i) => (
                  <Pressable key={s.store_code || i} style={styles.optRow} onPress={() => start(s)}>
                    <Text style={styles.optLabel} numberOfLines={1}>{s.store_code}</Text>
                    {s.address ? <Text style={styles.optSub} numberOfLines={1}>{s.address}</Text> : null}
                  </Pressable>
                ))}
                {list.length === 0 ? <Body dim>No stores in your scope.</Body> : null}
              </ScrollView>
            )}
          </Pressable>
        </Pressable>
      </Modal>
    </View>
  )
}

// ── Visit form: checklist items + accessories, then submit ─────────────────────────────────────────
function VisitForm({ visitId, store, dmName, onDone, onCancel }: {
  visitId: string; store: VisitStore | null; dmName: string
  onDone: () => void; onCancel: () => void
}) {
  const items = useQuery({ queryKey: ['dm', 'checklist-items'], queryFn: getChecklistItems })
  const list = useMemo(() => {
    const arr = asArray<ChecklistItem>(items.data, 'items').filter((it) => it.is_active !== false)
    return arr.slice().sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0))
  }, [items.data])

  const [checks, setChecks] = useState<Record<string, boolean>>({})
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [acc, setAcc] = useState<VisitAccessory[]>([])
  const [visitNote, setVisitNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const toggle = (k: string) => setChecks((c) => ({ ...c, [k]: !c[k] }))
  const setNote = (k: string, v: string) => setNotes((n) => ({ ...n, [k]: v }))
  const addAcc = () => setAcc((a) => [...a, { accessory_name: '', qty: 1 }])
  const setAccField = (i: number, field: keyof VisitAccessory, v: string) =>
    setAcc((a) => a.map((row, j) => j === i ? { ...row, [field]: field === 'qty' ? (parseInt(v, 10) || 0) : v } : row))
  const removeAcc = (i: number) => setAcc((a) => a.filter((_, j) => j !== i))

  const done = list.filter((it) => checks[it.item_key]).length

  const submit = async () => {
    setBusy(true); setErr('')
    try {
      const responses: VisitResponse[] = list.map((it) => ({
        item_key: it.item_key, label_snapshot: it.label, category_snapshot: it.category,
        checked: !!checks[it.item_key], note: notes[it.item_key] || undefined,
      }))
      const accessories = acc.filter((a) => (a.accessory_name || '').trim())
      await updateVisit(visitId, {
        responses, accessories, notes: visitNote || undefined,
        check_out_at: new Date().toISOString(),
      })
      await submitVisit(visitId)
      onDone()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not submit the visit.')
    } finally { setBusy(false) }
  }

  return (
    <Screen>
      <ScrollView contentContainerStyle={styles.c}>
        <View style={styles.startCard}>
          <Text style={styles.startTitle}>{store?.store_code || 'Store visit'}</Text>
          <Text style={styles.startSub}>{store?.address || 'Checklist inspection'} · {dmName}</Text>
        </View>

        {items.isLoading ? (
          <Loading label="Loading checklist…" />
        ) : items.isError ? (
          <ErrorView message={(items.error as Error)?.message || 'Could not load the checklist.'} onRetry={items.refetch} />
        ) : (
          <>
            <SectionTitle>Checklist · {done}/{list.length}</SectionTitle>
            {list.length === 0 ? <Body dim>No checklist items configured.</Body> : null}
            {list.map((it) => {
              const on = !!checks[it.item_key]
              return (
                <View key={it.item_key} style={styles.item}>
                  <Pressable style={styles.itemTop} onPress={() => toggle(it.item_key)}>
                    <View style={[styles.box, on && styles.boxOn]}>{on ? <Text style={styles.check}>✓</Text> : null}</View>
                    <Text style={styles.itemLabel}>{it.label}</Text>
                  </Pressable>
                  <Input value={notes[it.item_key] || ''} onChangeText={(v) => setNote(it.item_key, v)}
                    placeholder="Note (optional)" />
                </View>
              )
            })}

            <SectionTitle>Accessories seen</SectionTitle>
            {acc.map((a, i) => (
              <View key={i} style={styles.accRow}>
                <View style={{ flex: 1 }}>
                  <Input value={a.accessory_name} onChangeText={(v) => setAccField(i, 'accessory_name', v)} placeholder="Accessory" />
                </View>
                <View style={styles.qty}>
                  <Input value={String(a.qty ?? '')} onChangeText={(v) => setAccField(i, 'qty', v)}
                    keyboardType="number-pad" placeholder="Qty" />
                </View>
                <Pressable onPress={() => removeAcc(i)} hitSlop={8}><Text style={styles.remove}>✕</Text></Pressable>
              </View>
            ))}
            <Button title="+ Add accessory" onPress={addAcc} variant="secondary" />

            <SectionTitle>Visit note</SectionTitle>
            <Input value={visitNote} onChangeText={setVisitNote} placeholder="Overall note (optional)" multiline />

            {err ? <Text style={styles.err}>{err}</Text> : null}
            <View style={{ height: spacing.sm }} />
            <Button title={busy ? 'Submitting…' : 'Submit visit'} onPress={submit} loading={busy} variant="success" />
            <View style={{ height: spacing.sm }} />
            <Button title="Cancel" onPress={onCancel} variant="secondary" />
          </>
        )}
      </ScrollView>
    </Screen>
  )
}

const styles = StyleSheet.create({
  c: { padding: spacing.lg, gap: spacing.md, paddingBottom: spacing.xxl },
  startCard: {
    backgroundColor: colors.surface, borderRadius: radius.md, borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.primary, padding: spacing.lg, gap: spacing.sm,
  },
  startTitle: { color: colors.text, fontSize: font.h3, fontWeight: '800' },
  startSub: { color: colors.textDim, fontSize: font.small },
  card: {
    backgroundColor: colors.surface, borderRadius: radius.md, borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border, padding: spacing.lg, gap: 4,
  },
  cardTop: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: spacing.sm },
  store: { color: colors.text, fontSize: font.body, fontWeight: '700', flex: 1 },
  badge: { fontSize: font.small, fontWeight: '800' },
  meta: { color: colors.textDim, fontSize: font.small },
  resume: { color: colors.primary, fontSize: font.small, fontWeight: '700' },
  item: {
    backgroundColor: colors.surface, borderRadius: radius.md, borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border, padding: spacing.md, gap: spacing.sm,
  },
  itemTop: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  box: {
    width: 24, height: 24, borderRadius: radius.sm, borderWidth: 2, borderColor: colors.border,
    alignItems: 'center', justifyContent: 'center',
  },
  boxOn: { backgroundColor: colors.success, borderColor: colors.success },
  check: { color: '#fff', fontSize: font.small, fontWeight: '900' },
  itemLabel: { color: colors.text, fontSize: font.body, flex: 1 },
  accRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  qty: { width: 72 },
  remove: { color: colors.danger, fontSize: font.h3, fontWeight: '800' },
  err: { color: colors.danger, fontSize: font.small },
  backdrop: { flex: 1, backgroundColor: '#0008', justifyContent: 'flex-end' },
  sheet: { backgroundColor: colors.surface, borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg, padding: spacing.lg, maxHeight: '88%' },
  sheetTitle: { color: colors.text, fontSize: font.h3, fontWeight: '800', marginBottom: spacing.sm },
  optRow: { paddingVertical: spacing.sm, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  optLabel: { color: colors.text, fontSize: font.body },
  optSub: { color: colors.textDim, fontSize: font.tiny },
})
