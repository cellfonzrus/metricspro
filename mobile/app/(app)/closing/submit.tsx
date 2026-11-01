import React, { useEffect, useMemo, useState } from 'react'
import {
  ActivityIndicator, Modal, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View,
} from 'react-native'
import { useQuery } from '@tanstack/react-query'
import * as ImagePicker from 'expo-image-picker'

import { useAuth } from '@/auth/AuthContext'
import {
  getClosingStores, getCountConfig, getDay, getEnvelopeConfig, getExpenseCategories,
  getMetricCatalog, getTenderConfig, putMetricLines, submitRow, uploadEnvelopePhoto,
  type ExpenseCategory, type RowResult, type StoreOpt,
} from '@/api/closing-submit'
import {
  buildPayload, countsAndLines, declaredTenders, richLines, totalCollected,
  type ExpenseLine, type MetricLineIn,
} from '@/lib/closing-submit'
import { money } from '@/lib/format'
import { todayISO } from '@/lib/period'
import {
  Body, Button, Card, EmptyState, ErrorView, Input, Loading, Screen,
} from '@/components/ui'
import { SectionTitle } from '@/components/dash'
import { colors, font, radius, spacing } from '@/theme'

// Submit Daily Closing — the STORE's own side of the day, on the phone. The DM's sign-off already
// existed here (app/(app)/dm/verify.tsx); this is the submission it signs off.
//
// WHAT THE REP IS ASKED FOR IS THE TENANT'S OWN DECLARATION. Every field on this form comes from a
// config endpoint — tenders from `GET /closing/tender-config`, count fields from `/count-config`,
// the per-category declarations from `/metric-catalog`, expenses from `/expense-categories`. There
// is no hardcoded field list and no house vocabulary behind a default: the owner directive of
// 2026-10-30 is explicit that a tenant's declared words are a required argument, and a closing form
// that quietly showed one tenant's tender names to another is that defect with real money on it.
// The ONE fallback — an entirely unconfigured tenant gets the server's `standard` template — lives
// in `lib/closing-submit.declaredTenders` and is proved there.
//
// WHERE A DECLARATION IS STORED IS NOT THIS SCREEN'S CALL. `closing/metric_catalog.storage_for` is
// the one rule (harness_metric_catalog_lock §A8) and arrives already resolved as each category's
// `storage`. A tally rides the mig-501 `counts` map; a money or per-person declaration is a metric
// LINE. A category whose storage cannot be read is SHOWN AS UNPLACEABLE rather than routed on a
// guess — guessing is how a per-person figure lands in a store-level tally with nothing on screen
// to say so.
//
// WHAT IT DOES NOT DO: it does not verify, correct or approve a day. That is the DM's screen.

type Str = Record<string, string>

export default function SubmitClosingScreen() {
  const { me } = useAuth()
  const [closeDate, setCloseDate] = useState(todayISO())
  const [storeCode, setStoreCode] = useState('')
  const [employeeName, setEmployeeName] = useState('')
  const [tv, setTv] = useState<Str>({})
  const [cv, setCv] = useState<Str>({})
  const [epay, setEpay] = useState<Str>({})
  const [accSale, setAccSale] = useState('')
  const [expLines, setExpLines] = useState<ExpenseLine[]>([])
  const [mlines, setMlines] = useState<MetricLineIn[]>([])
  const [photo, setPhoto] = useState('')
  const [photoPath, setPhotoPath] = useState('')
  const [remarks, setRemarks] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [retry, setRetry] = useState('')
  const [pickStore, setPickStore] = useState(false)

  useEffect(() => {
    if (!employeeName && me?.user?.name) setEmployeeName(String(me.user.name))
  }, [me?.user?.name])           // eslint-disable-line react-hooks/exhaustive-deps

  const stores = useQuery({ queryKey: ['closing-stores'], queryFn: getClosingStores })
  const tender = useQuery({ queryKey: ['closing-tender-config'], queryFn: getTenderConfig })
  const countCfg = useQuery({ queryKey: ['closing-count-config'], queryFn: getCountConfig })
  const catalog = useQuery({ queryKey: ['closing-metric-catalog'], queryFn: getMetricCatalog })
  const cats = useQuery({ queryKey: ['closing-expense-categories'], queryFn: getExpenseCategories })
  const day = useQuery({ queryKey: ['closing-day', closeDate], queryFn: () => getDay(closeDate) })
  const envCfg = useQuery({
    queryKey: ['closing-envelope-config', storeCode],
    queryFn: () => getEnvelopeConfig(storeCode || undefined),
    enabled: !!storeCode,
  })

  const storeList: StoreOpt[] = useMemo(() => {
    const d = stores.data as { stores?: StoreOpt[] } | StoreOpt[] | undefined
    if (Array.isArray(d)) return d
    return Array.isArray(d?.stores) ? d.stores : []
  }, [stores.data])

  const store = useMemo(
    () => storeList.find((s) => (s.store_code || '') === storeCode) || null,
    [storeList, storeCode])

  // The declared form, from the server's own answers. Never a list held in this file.
  const tenders = useMemo(() => declaredTenders(tender.data), [tender.data])
  const metrics = useMemo(() => catalog.data?.metrics ?? [], [catalog.data])
  const countDefs = useMemo(() => countCfg.data?.defs ?? [], [countCfg.data])
  const split = useMemo(() => countsAndLines(metrics, countDefs, cv), [metrics, countDefs, cv])
  const simpleDefs = useMemo(
    () => countDefs.filter((d) => Object.prototype.hasOwnProperty.call(split.counts, d.field_key)),
    [countDefs, split.counts])

  const collected = totalCollected(tv)
  const categories: ExpenseCategory[] = cats.data?.categories ?? []

  // A store whose closing is DERIVED from the sales feed may not be closed by hand. Said BEFORE the
  // rep counts an envelope and types seven figures, not after the server refuses it.
  const derived = (store?.closing_source || '').toLowerCase() === 'derived'

  const alreadyIn = useMemo(() => {
    const rows = (Array.isArray(day.data) ? day.data : []) as Record<string, unknown>[]
    if (!storeCode) return []
    return rows.filter((r) => String(r.store_code || '') === storeCode)
  }, [day.data, storeCode])

  const configLoading = tender.isLoading || countCfg.isLoading || catalog.isLoading
  const configError = tender.error || countCfg.error || catalog.error

  const needsPhoto = !!envCfg.data?.require_photo_if_cash
    && (parseFloat(tv.cash || '0') || 0) > 0 && !photo && !photoPath

  const canSubmit = !!closeDate && !!storeCode && !!employeeName.trim() && !derived && !busy
    && !needsPhoto && !configLoading && !configError

  async function takePhoto() {
    setMsg('')
    try {
      const perm = await ImagePicker.requestCameraPermissionsAsync()
      if (!perm.granted) {
        setMsg('📷 The camera permission was declined, so the envelope photo cannot be taken here.')
        return
      }
      const r = await ImagePicker.launchCameraAsync({ quality: 0.5, base64: true })
      if (r.canceled || !r.assets?.length) return
      const a = r.assets[0]
      const dataUrl = a.base64 ? `data:image/jpeg;base64,${a.base64}` : ''
      if (!dataUrl) {
        setMsg('📷 The photo could not be read. Try again, or submit without it if your store allows.')
        return
      }
      setPhoto(dataUrl)
      // Pre-upload is a convenience, never a dependency: if it fails the image rides the submit.
      try {
        const up = await uploadEnvelopePhoto(dataUrl)
        if (up?.path) setPhotoPath(up.path)
      } catch { /* kept locally; sent with the row */ }
    } catch (e) {
      setMsg(`📷 ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  async function submit() {
    setBusy(true)
    setMsg('')
    setRetry('')
    try {
      const payload = buildPayload({
        close_date: closeDate,
        store,
        employee_name: employeeName,
        standard: tender.data?.standard,
        tenderValues: tv,
        epay,
        acc_sale: accSale,
        catalog: metrics,
        countDefs,
        countValues: cv,
        expenseLines: expLines,
        envelope_picture: photoPath || photo,
        remarks,
      })
      const r: RowResult = await submitRow(payload)

      // Not accepted → a RECOUNT prompt. Direction only, never the amount, and the form is kept so
      // the rep can re-enter what they count.
      if (r && r.accepted === false && r.retry) {
        setRetry(r.retry.message || 'Please recount and enter the figures again.')
        return
      }

      const rich = richLines(mlines)
      if (rich.length) {
        try {
          await putMetricLines({
            store_code: storeCode,
            close_date: closeDate,
            closing_row_id: r?.id,
            lines: rich.map((l) => ({
              metric_key: l.metric_key,
              employee_name: l.employee_name || undefined,
              units: l.units || 0,
              amount: l.amount || 0,
            })),
          })
        } catch (e) {
          // SAID, never swallowed: the incentive is computed from these lines.
          setMsg('⚠️ The closing was submitted, but the per-category figures were not saved — '
            + (e instanceof Error ? e.message : String(e))
            + '. Enter the day again so the incentive is calculated on the full figures.')
          return
        }
      }

      const flags = r?.recon?.flags ?? []
      if (r?.recon?.auto_accepted) {
        setMsg('✅ Submitted — your report does not match the system and has been sent for management review.')
      } else if (flags.length) {
        setMsg(`⚠️ Submitted — ${flags.join('; ')}`)
      } else if (r?.recon?.status === 'recon_pending') {
        setMsg('✅ Submitted. Sales are not loaded yet, so it will reconcile once they land.')
      } else {
        setMsg('✅ Closing submitted and it tallies with the system.')
      }
      setTv({}); setCv({}); setEpay({}); setAccSale('')
      setExpLines([]); setMlines([]); setPhoto(''); setPhotoPath(''); setRemarks('')
      day.refetch()
    } catch (e) {
      setMsg(`🚫 ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  if (configLoading) return <Screen><Loading label="Reading your store's closing sheet…" /></Screen>
  if (configError) {
    return (
      <Screen>
        <ErrorView
          message={configError instanceof Error ? configError.message : String(configError)}
          onRetry={() => { tender.refetch(); countCfg.refetch(); catalog.refetch() }}
        />
      </Screen>
    )
  }
  if (!tenders.length) {
    // Nothing declared means nothing identified, and the surface SAYS SO (owner directive
    // 2026-10-30) rather than falling back to another tenant's tender names.
    return (
      <Screen>
        <EmptyState
          title="No closing sheet is set up yet"
          subtitle={'Your company has not declared which tenders a closing records, so there is '
            + 'nothing to ask you for. An admin sets this up under Closing settings on the web.'}
        />
      </Screen>
    )
  }

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={{ padding: spacing.lg, paddingBottom: spacing.xxl * 2 }}
        refreshControl={<RefreshControl refreshing={day.isFetching} onRefresh={() => day.refetch()}
                                       tintColor={colors.primary} />}
      >
        <Body dim>One entry per rep per day — the same fields as your closing sheet.</Body>

        {/* ── the day and the store ── */}
        <Card style={styles.card}>
          <Text style={styles.label}>Closing date</Text>
          <Input value={closeDate} onChangeText={setCloseDate} placeholder="YYYY-MM-DD"
                 placeholderTextColor={colors.textDim} autoCapitalize="none" />

          <Text style={[styles.label, { marginTop: spacing.md }]}>Store</Text>
          <Pressable style={styles.picker} onPress={() => setPickStore(true)}>
            <Text style={storeCode ? styles.pickerText : styles.pickerPlaceholder}>
              {store ? `${store.store_code} · ${store.store_name || store.store_address || ''}`
                     : 'Choose your store'}
            </Text>
          </Pressable>

          <Text style={[styles.label, { marginTop: spacing.md }]}>Your name</Text>
          <Input value={employeeName} onChangeText={setEmployeeName} placeholder="Who is closing"
                 placeholderTextColor={colors.textDim} />

          {derived ? (
            <Text style={styles.problem}>
              This store&apos;s daily closing is produced from the sales feed, so it is not entered by
              hand. Nothing you type here would be accepted.
            </Text>
          ) : null}
          {alreadyIn.length ? (
            <Text style={styles.note}>
              {alreadyIn.length === 1 ? 'One closing is' : `${alreadyIn.length} closings are`} already
              in for this store on {closeDate}. A second entry is a new rep&apos;s row, not a correction.
            </Text>
          ) : null}
        </Card>

        {/* ── tenders: the TENANT'S declared list ── */}
        <SectionTitle>Money collected</SectionTitle>
        <Card style={styles.card}>
          {tenders.map((t) => (
            <View key={t.tender_key} style={styles.row}>
              <Text style={styles.rowLabel}>{t.label || t.tender_key}</Text>
              <Input style={styles.amount} value={tv[t.tender_key] || ''}
                     onChangeText={(v) => setTv((p) => ({ ...p, [t.tender_key]: v }))}
                     keyboardType="decimal-pad" placeholder="0.00"
                     placeholderTextColor={colors.textDim} />
            </View>
          ))}
          <View style={styles.totalRow}>
            <Text style={styles.totalLabel}>Total collected</Text>
            <Text style={styles.totalValue}>{money(collected)}</Text>
          </View>
          <Text style={styles.note}>
            Accessory revenue is declared separately below — it is not a tender, so it is not part of
            this total.
          </Text>
        </Card>

        {/* ── ePay (bill payment) breakdown ── */}
        <SectionTitle>Of that, bill payments</SectionTitle>
        <Card style={styles.card}>
          <Body dim>
            How much of what you collected was bill payments. This is a breakdown of the money above,
            never extra money.
          </Body>
          {[['epay_on_cash', 'In cash'], ['epay_on_credit', 'On credit'],
            ['epay_on_acima', 'On lease']].map(([k, label]) => (
            <View key={k} style={styles.row}>
              <Text style={styles.rowLabel}>{label}</Text>
              <Input style={styles.amount} value={epay[k] || ''}
                     onChangeText={(v) => setEpay((p) => ({ ...p, [k]: v }))}
                     keyboardType="decimal-pad" placeholder="0.00"
                     placeholderTextColor={colors.textDim} />
            </View>
          ))}
          <View style={styles.row}>
            <Text style={styles.rowLabel}>Accessory sales</Text>
            <Input style={styles.amount} value={accSale} onChangeText={setAccSale}
                   keyboardType="decimal-pad" placeholder="0.00"
                   placeholderTextColor={colors.textDim} />
          </View>
        </Card>

        {/* ── declared counts: only the ones the CATALOG stores as a tally ── */}
        {simpleDefs.length ? (
          <>
            <SectionTitle>What you sold</SectionTitle>
            <Card style={styles.card}>
              {simpleDefs.map((d) => (
                <View key={d.field_key} style={styles.row}>
                  <Text style={styles.rowLabel}>{d.label || d.field_key}</Text>
                  <Input style={styles.amount} value={cv[d.field_key] || ''}
                         onChangeText={(v) => setCv((p) => ({ ...p, [d.field_key]: v }))}
                         keyboardType="number-pad" placeholder="0"
                         placeholderTextColor={colors.textDim} />
                </View>
              ))}
            </Card>
          </>
        ) : null}

        {/* ── per-category declarations that carry money or a person's name ── */}
        {split.lineMetrics.length ? (
          <>
            <SectionTitle>By category</SectionTitle>
            <Card style={styles.card}>
              <Body dim>
                These carry a dollar figure or a person&apos;s name, so each is its own line. Leave a
                line blank and it is not declared at all.
              </Body>
              {split.lineMetrics.map((m) => {
                const line = mlines.find((l) => l.metric_key === m.key)
                const set = (patch: Partial<MetricLineIn>) => setMlines((p) => {
                  const rest = p.filter((l) => l.metric_key !== m.key)
                  return [...rest, {
                    metric_key: m.key, employee_name: line?.employee_name || '',
                    units: line?.units || '', amount: line?.amount || '', ...patch,
                  }]
                })
                return (
                  <View key={m.key} style={styles.metric}>
                    <Text style={styles.rowLabel}>{m.label || m.key}</Text>
                    <View style={styles.metricInputs}>
                      <Input style={styles.metricIn} value={line?.units || ''}
                             onChangeText={(v) => set({ units: v })} keyboardType="number-pad"
                             placeholder="How many" placeholderTextColor={colors.textDim} />
                      <Input style={styles.metricIn} value={line?.amount || ''}
                             onChangeText={(v) => set({ amount: v })} keyboardType="decimal-pad"
                             placeholder="$" placeholderTextColor={colors.textDim} />
                    </View>
                    <Input value={line?.employee_name || ''}
                           onChangeText={(v) => set({ employee_name: v })}
                           placeholder="Whose (leave blank for the store)"
                           placeholderTextColor={colors.textDim} />
                  </View>
                )
              })}
            </Card>
          </>
        ) : null}

        {/* ── a declared field nothing can place ── */}
        {split.unknown.length ? (
          <Card style={styles.card}>
            <Text style={styles.problem}>
              {split.unknown.length === 1 ? 'One field your' : `${split.unknown.length} fields your`}
              {' '}company declares ({split.unknown.join(', ')}) {split.unknown.length === 1 ? 'is' : 'are'}
              {' '}not in the category catalog, so there is nowhere to record {split.unknown.length === 1
                ? 'it' : 'them'}. Everything else on this form still submits. Tell your admin — this is
              a setup gap, not something you can fix here.
            </Text>
          </Card>
        ) : null}

        {/* ── expenses ── */}
        <SectionTitle>Money paid out</SectionTitle>
        <Card style={styles.card}>
          {expLines.map((l, i) => (
            <View key={i} style={styles.metric}>
              <View style={styles.chips}>
                {categories.map((c) => (
                  <Pressable key={c.id}
                             style={[styles.chip, l.category_id === c.id && styles.chipOn]}
                             onPress={() => setExpLines((p) => p.map((x, j) =>
                               j === i ? { ...x, category_id: c.id } : x))}>
                    <Text style={[styles.chipText, l.category_id === c.id && styles.chipTextOn]}>
                      {c.name}
                    </Text>
                  </Pressable>
                ))}
              </View>
              <View style={styles.metricInputs}>
                <Input style={styles.metricIn} value={l.amount} keyboardType="decimal-pad"
                       placeholder="0.00" placeholderTextColor={colors.textDim}
                       onChangeText={(v) => setExpLines((p) => p.map((x, j) =>
                         j === i ? { ...x, amount: v } : x))} />
                <Input style={styles.metricIn} value={l.description} placeholder="What for"
                       placeholderTextColor={colors.textDim}
                       onChangeText={(v) => setExpLines((p) => p.map((x, j) =>
                         j === i ? { ...x, description: v } : x))} />
              </View>
              <Pressable onPress={() => setExpLines((p) => p.filter((_, j) => j !== i))}>
                <Text style={styles.linkSmall}>Remove</Text>
              </Pressable>
            </View>
          ))}
          {categories.length ? (
            <Pressable onPress={() => setExpLines((p) => [
              ...p, { category_id: '', amount: '', description: '' }])}>
              <Text style={styles.link}>+ Add an expense</Text>
            </Pressable>
          ) : (
            <Body dim>No expense categories are set up, so expenses are not recorded here.</Body>
          )}
        </Card>

        {/* ── envelope photo ── */}
        <SectionTitle>Cash envelope</SectionTitle>
        <Card style={styles.card}>
          {photo || photoPath ? (
            <Body>📷 Photo attached{photoPath ? ' and uploaded.' : ' (it will upload with the closing).'}</Body>
          ) : (
            <Body dim>
              {needsPhoto
                ? 'Your store requires a photo of the sealed envelope when cash is declared.'
                : 'Optional — a photo of the sealed envelope.'}
            </Body>
          )}
          <View style={{ marginTop: spacing.sm }}>
            <Button title={photo || photoPath ? 'Retake photo' : 'Take photo'}
                    variant="secondary" onPress={takePhoto} />
          </View>
        </Card>

        {/* ── remarks + submit ── */}
        <Card style={styles.card}>
          <Text style={styles.label}>Anything to note</Text>
          <Input value={remarks} onChangeText={setRemarks} placeholder="Optional"
                 placeholderTextColor={colors.textDim} multiline />
        </Card>

        {retry ? <Text style={styles.problem}>🔁 {retry}</Text> : null}
        {needsPhoto ? (
          <Text style={styles.problem}>
            A photo of the envelope is required before this can be submitted.
          </Text>
        ) : null}
        {msg ? <Text style={styles.msg}>{msg}</Text> : null}

        <View style={{ marginTop: spacing.md }}>
          <Button title={busy ? 'Submitting…' : 'Submit closing'} onPress={submit}
                  disabled={!canSubmit} />
          {busy ? <ActivityIndicator color={colors.primary} style={{ marginTop: spacing.sm }} /> : null}
        </View>
      </ScrollView>

      {/* ── store picker ── */}
      <Modal visible={pickStore} animationType="slide" transparent
             onRequestClose={() => setPickStore(false)}>
        <View style={styles.sheetWrap}>
          <View style={styles.sheet}>
            <Text style={styles.label}>Choose your store</Text>
            <ScrollView style={{ maxHeight: 380 }}>
              {storeList.map((s) => (
                <Pressable key={String(s.store_code)} style={styles.storeRow}
                           onPress={() => { setStoreCode(s.store_code || ''); setPickStore(false) }}>
                  <Text style={styles.pickerText}>{s.store_code}</Text>
                  <Text style={styles.note}>{s.store_name || s.store_address || ''}</Text>
                </Pressable>
              ))}
              {!storeList.length ? (
                <Body dim>No stores are available to you. Ask your manager to set your store.</Body>
              ) : null}
            </ScrollView>
            <View style={{ marginTop: spacing.sm }}>
              <Button title="Cancel" variant="secondary" onPress={() => setPickStore(false)} />
            </View>
          </View>
        </View>
      </Modal>
    </Screen>
  )
}

const styles = StyleSheet.create({
  card: { marginTop: spacing.sm },
  label: { color: colors.text, fontSize: font.small, fontWeight: '700', marginBottom: spacing.xs },
  note: { color: colors.textDim, fontSize: font.tiny, marginTop: spacing.xs },
  problem: { color: colors.warning, fontSize: font.small, marginTop: spacing.sm },
  msg: { color: colors.text, fontSize: font.small, marginTop: spacing.sm },
  link: { color: colors.primary, fontSize: font.small, fontWeight: '600', marginTop: spacing.xs },
  linkSmall: { color: colors.primary, fontSize: font.tiny, fontWeight: '600', marginTop: spacing.xs },
  row: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    gap: spacing.sm, marginTop: spacing.sm,
  },
  rowLabel: { color: colors.text, fontSize: font.small, flex: 1 },
  amount: { width: 112, textAlign: 'right' },
  totalRow: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    marginTop: spacing.md, paddingTop: spacing.sm, borderTopWidth: 1, borderTopColor: colors.border,
  },
  totalLabel: { color: colors.text, fontSize: font.body, fontWeight: '700' },
  totalValue: { color: colors.success, fontSize: font.h3, fontWeight: '800' },
  metric: {
    marginTop: spacing.md, paddingTop: spacing.sm,
    borderTopWidth: 1, borderTopColor: colors.border, gap: spacing.xs,
  },
  metricInputs: { flexDirection: 'row', gap: spacing.sm },
  metricIn: { flex: 1 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs },
  chip: {
    paddingHorizontal: spacing.sm, paddingVertical: spacing.xs, borderRadius: radius.pill,
    borderWidth: 1, borderColor: colors.border,
  },
  chipOn: { borderColor: colors.primary, backgroundColor: colors.surfaceAlt },
  chipText: { color: colors.textDim, fontSize: font.tiny, fontWeight: '600' },
  chipTextOn: { color: colors.primary },
  picker: {
    borderWidth: 1, borderColor: colors.border, borderRadius: radius.sm,
    paddingHorizontal: spacing.md, paddingVertical: spacing.md, backgroundColor: colors.surface,
  },
  pickerText: { color: colors.text, fontSize: font.small, fontWeight: '600' },
  pickerPlaceholder: { color: colors.textDim, fontSize: font.small },
  sheetWrap: { flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(0,0,0,0.6)' },
  sheet: {
    backgroundColor: colors.surface, padding: spacing.lg,
    borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg, gap: spacing.sm,
  },
  storeRow: { paddingVertical: spacing.sm, borderBottomWidth: 1, borderBottomColor: colors.border },
})
