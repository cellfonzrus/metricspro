import React, { useState } from 'react'
import { Modal, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native'
import { useQuery } from '@tanstack/react-query'

import { useAuth } from '@/auth/AuthContext'
import { atLeast, scopeOf } from '@/lib/scope'
import { getClosingSummary, verifyStore, type ClosingStore } from '@/api/dm'
import { money } from '@/lib/format'
import { todayISO } from '@/lib/period'
import { addDays } from '@/lib/daterange'
import { Body, Button, EmptyState, ErrorView, Input, Loading, Screen } from '@/components/ui'
import { colors, font, radius, spacing } from '@/theme'

// DM Verify — the DM's sign-off on each store's daily closing for a date. Shows the money totals and
// gate status per store, and records a verification (optionally correcting the numbers) via
// POST /closing/verify. The summary is store-span-scoped server-side, so a market DM sees their market.
const GATE: Record<string, [string, string]> = {
  ok: ['OK', colors.success], flagged: ['Flagged', colors.warning],
  blocked: ['Blocked', colors.danger], recon_pending: ['Recon pending', colors.textDim],
}

export default function DmVerifyScreen() {
  const { me } = useAuth()
  const [date, setDate] = useState(todayISO())
  const [active, setActive] = useState<ClosingStore | null>(null)

  const q = useQuery({
    queryKey: ['dm', 'closing', date],
    queryFn: () => getClosingSummary(date),
    enabled: atLeast(scopeOf(me), 'market'),
  })

  if (!atLeast(scopeOf(me), 'market')) {
    return <Screen><EmptyState title="Not available" subtitle="DM Verify is for district managers and admins." /></Screen>
  }

  const stores = q.data?.stores || []
  const done = stores.filter((s) => s.verification?.verified).length

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={styles.c}
        refreshControl={<RefreshControl refreshing={q.isFetching} onRefresh={q.refetch} tintColor={colors.primary} />}
      >
        {/* date stepper */}
        <View style={styles.dateRow}>
          <Pressable onPress={() => setDate(addDays(date, -1))} hitSlop={8}><Text style={styles.arrow}>◀</Text></Pressable>
          <Text style={styles.date}>{date}{date === todayISO() ? '  (today)' : ''}</Text>
          <Pressable onPress={() => date < todayISO() && setDate(addDays(date, 1))} hitSlop={8}>
            <Text style={[styles.arrow, date >= todayISO() && { opacity: 0.25 }]}>▶</Text>
          </Pressable>
        </View>

        {q.isLoading ? (
          <Loading label="Loading closings…" />
        ) : q.isError ? (
          <ErrorView message={(q.error as Error)?.message || 'Could not load closings.'} onRetry={q.refetch} />
        ) : stores.length === 0 ? (
          <EmptyState title="No closings" subtitle="No stores reported a closing for this date." />
        ) : (
          <>
            <Body dim>{done} of {stores.length} stores verified.</Body>
            {stores.map((s, i) => {
              const [gLabel, gColor] = GATE[String(s.gate_status)] || [String(s.gate_status || '—'), colors.textDim]
              const verified = !!s.verification?.verified
              return (
                <Pressable key={i} style={styles.card} onPress={() => setActive(s)}>
                  <View style={styles.cardTop}>
                    <Text style={styles.store} numberOfLines={1}>{s.store_name || s.store_code || '—'}</Text>
                    {verified
                      ? <Text style={[styles.badge, { color: colors.success }]}>✓ Verified</Text>
                      : <Text style={[styles.badge, { color: gColor }]}>{gLabel}</Text>}
                  </View>
                  <Text style={styles.meta} numberOfLines={1}>
                    {s.no_closing_submitted ? 'No closing submitted' : `Closed by ${s.closer || '—'}`}
                  </Text>
                  <Text style={styles.total}>{money(s.totals?.total_collected)} collected</Text>
                </Pressable>
              )
            })}
          </>
        )}
      </ScrollView>

      <VerifyModal store={active} date={date} onClose={() => setActive(null)}
        verifiedBy={(me?.user?.name as string) || (me?.user?.email as string) || ''}
        onDone={() => { setActive(null); q.refetch() }} />
    </Screen>
  )
}

function VerifyModal({ store, date, onClose, onDone, verifiedBy }: {
  store: ClosingStore | null; date: string; onClose: () => void; onDone: () => void; verifiedBy: string
}) {
  const t = store?.totals || {}
  const [form, setForm] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  // prefill from the store's own totals when a store opens
  React.useEffect(() => {
    if (!store) return
    setErr('')
    setForm({
      store_cash: String(t.store_cash ?? ''), store_cc: String(t.store_cc ?? ''),
      epay_cash: String(t.epay_on_cash ?? ''), epay_cc: String(t.epay_on_cc ?? ''),
      acc_sale: String(t.acc_sale ?? ''), other: String(t.other_account ?? ''), note: '',
    })
  }, [store]) // eslint-disable-line react-hooks/exhaustive-deps

  const num = (k: string) => { const v = parseFloat(form[k]); return Number.isFinite(v) ? v : 0 }
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }))

  const submit = async () => {
    if (!store?.store_code) return
    setBusy(true); setErr('')
    try {
      await verifyStore({
        store_code: store.store_code, close_date: date, store_name: store.store_name, verified: true,
        verified_by: verifiedBy,
        dm_store_cash: num('store_cash'), dm_store_cc: num('store_cc'),
        dm_epay_cash: num('epay_cash'), dm_epay_cc: num('epay_cc'),
        dm_acc_sale: num('acc_sale'), dm_other: num('other'), note: form.note || undefined,
      })
      onDone()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Could not verify.') } finally { setBusy(false) }
  }

  return (
    <Modal visible={!!store} animationType="slide" transparent onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <Pressable style={styles.sheet} onPress={() => {}}>
          <ScrollView>
            <Text style={styles.sheetTitle}>{store?.store_name || store?.store_code}</Text>
            <Text style={styles.sheetSub}>Confirm the closing totals for {date}, correct if needed, then verify.</Text>
            {err ? <Text style={styles.err}>{err}</Text> : null}
            <Field label="Store cash" value={form.store_cash} onChange={(v) => set('store_cash', v)} />
            <Field label="Store credit card" value={form.store_cc} onChange={(v) => set('store_cc', v)} />
            <Field label="ePay on cash" value={form.epay_cash} onChange={(v) => set('epay_cash', v)} />
            <Field label="ePay on card" value={form.epay_cc} onChange={(v) => set('epay_cc', v)} />
            <Field label="Accessory sales" value={form.acc_sale} onChange={(v) => set('acc_sale', v)} />
            <Field label="Other / account" value={form.other} onChange={(v) => set('other', v)} />
            <Text style={styles.fieldLabel}>Note (optional)</Text>
            <Input value={form.note} onChangeText={(v) => set('note', v)} placeholder="Anything to flag" multiline />
            <View style={{ height: spacing.md }} />
            <Button title={busy ? 'Verifying…' : 'Confirm & verify'} onPress={submit} loading={busy} variant="success" />
            <View style={{ height: spacing.sm }} />
            <Button title="Cancel" onPress={onClose} variant="secondary" />
          </ScrollView>
        </Pressable>
      </Pressable>
    </Modal>
  )
}

function Field({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <View style={{ marginBottom: spacing.sm }}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <Input value={value} onChangeText={onChange} keyboardType="decimal-pad" placeholder="0" />
    </View>
  )
}

const styles = StyleSheet.create({
  c: { padding: spacing.lg, gap: spacing.md, paddingBottom: spacing.xxl },
  dateRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.xl, paddingVertical: spacing.xs },
  arrow: { color: colors.primary, fontSize: font.h3, fontWeight: '800' },
  date: { color: colors.text, fontSize: font.body, fontWeight: '700', minWidth: 160, textAlign: 'center' },
  card: {
    backgroundColor: colors.surface, borderRadius: radius.md, borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border, padding: spacing.lg, gap: 4,
  },
  cardTop: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: spacing.sm },
  store: { color: colors.text, fontSize: font.body, fontWeight: '700', flex: 1 },
  badge: { fontSize: font.small, fontWeight: '800' },
  meta: { color: colors.textDim, fontSize: font.small },
  total: { color: colors.text, fontSize: font.small, fontWeight: '600' },
  backdrop: { flex: 1, backgroundColor: '#0008', justifyContent: 'flex-end' },
  sheet: { backgroundColor: colors.surface, borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg, padding: spacing.lg, maxHeight: '88%' },
  sheetTitle: { color: colors.text, fontSize: font.h3, fontWeight: '800' },
  sheetSub: { color: colors.textDim, fontSize: font.small, marginBottom: spacing.md },
  fieldLabel: { color: colors.textDim, fontSize: font.small, marginBottom: 4 },
  err: { color: colors.danger, fontSize: font.small, marginBottom: spacing.sm },
})
