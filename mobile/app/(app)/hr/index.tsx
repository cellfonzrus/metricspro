import React, { useMemo, useState } from 'react'
import {
  ActivityIndicator, Linking, Modal, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View,
} from 'react-native'
import { useQuery } from '@tanstack/react-query'
import * as DocumentPicker from 'expo-document-picker'

import {
  deleteMyDocument, getMyOnboarding, myDocumentUrl, saveMyIntake, setMyWorkState,
  uploadMyDocument, type MyOnboarding,
} from '@/api/hr'
import {
  canDeleteDocument, missingRequired, sectionsOf, taskSummary, valueFor,
  type OnboardingTask,
} from '@/lib/hr-intake'
import { Body, Button, Card, EmptyState, ErrorView, Input, Loading, Screen } from '@/components/ui'
import { SectionTitle } from '@/components/dash'
import { colors, font, radius, spacing } from '@/theme'

// My HR — the rep's own record: the paperwork they still owe, the documents against it, and the
// questions their company asks.
//
// WHAT WAS ALREADY HERE, so this screen does not duplicate it: `app/(app)/dashboards/me.tsx` is the
// rep-facing PAY and PERFORMANCE record (commission, tier, hours vs pay, targets, chargebacks,
// upcoming shifts) reading the same `GET /core/employee-dashboard` the web employee page reads. The
// half with no phone surface at all was the HR RECORD — onboarding and compliance paperwork, the
// documents, and the intake form. That is what this is.
//
// IT READS ONLY THE `/me` ROUTES. `GET /hr/onboarding/employee/{id}` is the HR-side read of the same
// record and takes no `authorization`; a screen built on it would let anyone signed in read somebody
// else's record by knowing an employee id. The `/me` family resolves the person from the TOKEN, so
// there is no id to pass and nothing to get wrong.
//
// A SENSITIVE ANSWER HAS THREE STATES. The server never echoes one back, so blank does not mean
// unanswered — it means it is not being shown. The screen says "enter it to set or replace it" and
// `lib/hr-intake.missingRequired` reports that case separately, because nagging a rep about a form
// they already filled and letting a required field through unfilled are both plausible-looking
// failures of the same two-state assumption.

const SECTION_LABELS: Record<string, string> = {
  // A HOUSE label for a HOUSE section key (these keys are the platform's own, seeded in
  // `intake_fields_list`, not a tenant's words) — and anything unrecognised is shown by its key
  // rather than renamed, so a tenant that adds a section never sees it labelled as something else.
  personal: 'About you',
  address: 'Where you live',
  emergency: 'Emergency contact',
  work_eligibility: 'Work eligibility',
  tax: 'Tax',
  direct_deposit: 'Direct deposit',
  policies: 'Policies',
  custom: 'Other questions',
  other: 'Other questions',
}

const STATUS_LABEL: Record<string, [string, string]> = {
  pending: ['To do', colors.warning],
  returned: ['Needs fixing', colors.danger],
  submitted: ['Sent — waiting on HR', colors.primary],
  signed: ['Signed — waiting on HR', colors.primary],
  verified: ['Done', colors.success],
  approved: ['Done', colors.success],
  complete: ['Done', colors.success],
  completed: ['Done', colors.success],
}

export default function MyHrScreen() {
  const [intake, setIntake] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [pickState, setPickState] = useState(false)

  const q = useQuery({ queryKey: ['hr-onboarding-me'], queryFn: getMyOnboarding })
  const d: MyOnboarding | undefined = q.data

  const fields = useMemo(() => d?.intake_fields ?? [], [d])
  const sections = useMemo(() => sectionsOf(fields), [fields])
  // The stored answers, overlaid with whatever is being typed right now.
  const answers = useMemo(
    () => ({ ...(d?.intake_values ?? {}), ...intake }),
    [d?.intake_values, intake])
  const required = useMemo(() => missingRequired(fields, answers), [fields, answers])
  const summary = useMemo(() => taskSummary(d?.categories), [d?.categories])
  const states: string[] = useMemo(
    () => (Array.isArray(d?.states) ? (d?.states as string[]) : []), [d?.states])

  async function openDoc(taskId: string, fileId: string) {
    setMsg('')
    try {
      const r = await myDocumentUrl(taskId, fileId)
      const url = typeof r?.url === 'string' ? r.url : ''
      if (!url) { setMsg('That document could not be opened right now.'); return }
      await Linking.openURL(url)
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e))
    }
  }

  async function upload(taskId: string) {
    setMsg('')
    setBusy(taskId)
    try {
      const r = await DocumentPicker.getDocumentAsync({ copyToCacheDirectory: true })
      if (r.canceled || !r.assets?.length) return
      const a = r.assets[0]
      await uploadMyDocument(taskId, {
        uri: a.uri, name: a.name || 'document', mimeType: a.mimeType,
      })
      setMsg('✅ Uploaded.')
      q.refetch()
    } catch (e) {
      setMsg(`🚫 ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy('')
    }
  }

  async function removeDoc(taskId: string, fileId: string) {
    setMsg('')
    setBusy(fileId)
    try {
      await deleteMyDocument(taskId, fileId)
      q.refetch()
    } catch (e) {
      setMsg(`🚫 ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy('')
    }
  }

  async function chooseState(s: string) {
    setPickState(false)
    setBusy('state')
    setMsg('')
    try {
      await setMyWorkState(s)
      q.refetch()
    } catch (e) {
      setMsg(`🚫 ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy('')
    }
  }

  async function saveIntake() {
    setBusy('intake')
    setMsg('')
    try {
      // Only what was typed is sent. There is nothing stored to resend for a sensitive field, and
      // resending a blank would wipe an answer the server declined to show us.
      await saveMyIntake(intake)
      setIntake({})
      setMsg('✅ Saved.')
      q.refetch()
    } catch (e) {
      setMsg(`🚫 ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy('')
    }
  }

  if (q.isLoading) return <Screen><Loading label="Reading your record…" /></Screen>
  if (q.error) {
    return (
      <Screen>
        <ErrorView message={q.error instanceof Error ? q.error.message : String(q.error)}
                   onRetry={() => q.refetch()} />
      </Screen>
    )
  }
  if (!d?.has_profile) {
    return (
      <Screen>
        <EmptyState
          title="No HR record is linked to this login"
          subtitle={'Your sign-in is not connected to an employee record yet, so there is nothing to '
            + 'show. Ask your manager to link it.'}
        />
      </Screen>
    )
  }
  if (!d?.ready) {
    // Nothing declared means nothing identified, and the surface says so rather than showing an
    // empty checklist that reads as "you are all done".
    return (
      <Screen>
        <EmptyState
          title="Your company has not set up its paperwork yet"
          subtitle={'There is no onboarding checklist declared, so this is not a list of things you '
            + 'have finished — it is a list that does not exist yet.'}
        />
      </Screen>
    )
  }

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={{ padding: spacing.lg, paddingBottom: spacing.xxl * 2 }}
        refreshControl={<RefreshControl refreshing={q.isFetching} onRefresh={() => q.refetch()}
                                       tintColor={colors.primary} />}
      >
        {/* ── where it stands ── */}
        <Card>
          <Text style={styles.big}>
            {summary.needsMe === 0
              ? 'Nothing is waiting on you.'
              : `${summary.needsMe} ${summary.needsMe === 1 ? 'item needs' : 'items need'} you.`}
          </Text>
          <Text style={styles.note}>
            {summary.done} of {summary.total} done
            {summary.waitingOnHr ? ` · ${summary.waitingOnHr} waiting on HR` : ''}
          </Text>
          {summary.unknown.length ? (
            <Text style={styles.note}>
              {summary.unknown.length === 1 ? 'One item is' : `${summary.unknown.length} items are`} in a
              state this version of the app does not recognise ({summary.unknown.join(', ')}), so
              {summary.unknown.length === 1 ? ' it is' : ' they are'} not counted above either way.
              Check with HR rather than assuming.
            </Text>
          ) : null}
          {d.work_auth_pending ? (
            <Text style={styles.problem}>
              {d.work_auth_notice || 'Your work authorisation paperwork is still outstanding.'}
            </Text>
          ) : null}
        </Card>

        {/* ── the work state, which unlocks the state forms ── */}
        {d.needs_work_state || !d.work_state ? (
          <Card style={styles.card}>
            <Text style={styles.label}>Which state do you work in?</Text>
            <Body dim>
              Your state&apos;s tax forms only appear once this is set, so an unset state looks like a
              shorter checklist than you actually have.
            </Body>
            <View style={{ marginTop: spacing.sm }}>
              <Button title={busy === 'state' ? 'Saving…' : (d.work_state || 'Choose your state')}
                      variant="secondary" onPress={() => setPickState(true)} />
            </View>
          </Card>
        ) : null}

        {/* ── the checklist, in the tenant's declared categories ── */}
        {(d.categories ?? []).map((cat) => (
          <View key={cat.key}>
            <SectionTitle>{cat.label || cat.key}</SectionTitle>
            {(cat.tasks ?? []).map((t: OnboardingTask) => {
              const status = String(t.status || '').toLowerCase()
              const [sLabel, sColor] = STATUS_LABEL[status] || [t.status || 'Unknown', colors.textDim]
              return (
                <Card key={t.id} style={styles.card}>
                  <View style={styles.taskHead}>
                    <Text style={styles.taskLabel}>{t.label || t.id}</Text>
                    <Text style={[styles.status, { color: sColor }]}>{sLabel}</Text>
                  </View>
                  {t.description ? <Text style={styles.note}>{t.description}</Text> : null}
                  {t.is_mandatory === false ? <Text style={styles.note}>Optional</Text> : null}
                  {t.returned_reason ? (
                    <Text style={styles.problem}>Sent back: {t.returned_reason}</Text>
                  ) : null}

                  {t.template_url ? (
                    <Pressable onPress={() => Linking.openURL(String(t.template_url))}>
                      <Text style={styles.link}>
                        Open the form to fill in{t.template_name ? ` (${t.template_name})` : ''}
                      </Text>
                    </Pressable>
                  ) : null}

                  {(t.documents ?? []).map((f) => (
                    <View key={String(f.id)} style={styles.docRow}>
                      <Pressable style={{ flex: 1 }}
                                 onPress={() => openDoc(t.id, String(f.id))}>
                        <Text style={styles.link} numberOfLines={1}>📄 {f.name || 'Document'}</Text>
                      </Pressable>
                      {canDeleteDocument(f) ? (
                        <Pressable onPress={() => removeDoc(t.id, String(f.id))}>
                          <Text style={styles.linkSmall}>
                            {busy === String(f.id) ? 'Removing…' : 'Remove'}
                          </Text>
                        </Pressable>
                      ) : null}
                    </View>
                  ))}

                  {t.requires_upload ? (
                    <View style={{ marginTop: spacing.sm }}>
                      <Button
                        title={busy === t.id ? 'Uploading…' : (t.has_document ? 'Add another file' : 'Upload')}
                        variant="secondary" onPress={() => upload(t.id)} disabled={!!busy}
                      />
                    </View>
                  ) : null}
                  {t.requires_signature && !t.template_url ? (
                    <Text style={styles.note}>
                      This one has to be signed on the web portal — the phone cannot sign it yet.
                    </Text>
                  ) : null}
                </Card>
              )
            })}
          </View>
        ))}

        {/* ── the tenant's own intake questions ── */}
        {sections.length ? (
          <>
            <SectionTitle>Your details</SectionTitle>
            {required.missing.length ? (
              <Text style={styles.problem}>
                {required.missing.length === 1 ? 'One required answer is' : `${required.missing.length} required answers are`}
                {' '}still blank.
              </Text>
            ) : null}
            {sections.map((sec) => (
              <Card key={sec.key} style={styles.card}>
                <Text style={styles.label}>{SECTION_LABELS[sec.key] || sec.key}</Text>
                {sec.fields.map((f) => (
                  <View key={f.key} style={{ marginTop: spacing.sm }}>
                    <Text style={styles.fieldLabel}>
                      {f.label}{f.required ? ' *' : ''}
                    </Text>
                    <Input
                      value={intake[f.key] ?? valueFor(f, d.intake_values)}
                      onChangeText={(v) => setIntake((p) => ({ ...p, [f.key]: v }))}
                      placeholder={f.sensitive ? 'Enter it to set or replace it' : (f.help_text || '')}
                      placeholderTextColor={colors.textDim}
                      secureTextEntry={!!f.sensitive}
                      keyboardType={f.field_type === 'number' ? 'number-pad' : 'default'}
                      autoCapitalize={f.field_type === 'email' ? 'none' : 'sentences'}
                    />
                    {f.sensitive ? (
                      <Text style={styles.note}>
                        Not shown back for your safety, so a blank box here does not mean we do not
                        have it.
                      </Text>
                    ) : f.help_text ? (
                      <Text style={styles.note}>{f.help_text}</Text>
                    ) : null}
                  </View>
                ))}
              </Card>
            ))}
            <View style={{ marginTop: spacing.sm }}>
              <Button title={busy === 'intake' ? 'Saving…' : 'Save my details'}
                      onPress={saveIntake}
                      disabled={!!busy || Object.keys(intake).length === 0} />
            </View>
          </>
        ) : null}

        {msg ? <Text style={styles.msg}>{msg}</Text> : null}
        {busy ? <ActivityIndicator color={colors.primary} style={{ marginTop: spacing.sm }} /> : null}
      </ScrollView>

      {/* ── state picker ── */}
      <Modal visible={pickState} animationType="slide" transparent
             onRequestClose={() => setPickState(false)}>
        <View style={styles.sheetWrap}>
          <View style={styles.sheet}>
            <Text style={styles.label}>Where do you work?</Text>
            <ScrollView style={{ maxHeight: 380 }}>
              {states.map((s) => (
                <Pressable key={s} style={styles.stateRow} onPress={() => chooseState(s)}>
                  <Text style={styles.taskLabel}>{s}</Text>
                </Pressable>
              ))}
              {!states.length ? (
                <Body dim>No states are set up, so this cannot be chosen here yet.</Body>
              ) : null}
            </ScrollView>
            <View style={{ marginTop: spacing.sm }}>
              <Button title="Cancel" variant="secondary" onPress={() => setPickState(false)} />
            </View>
          </View>
        </View>
      </Modal>
    </Screen>
  )
}

const styles = StyleSheet.create({
  card: { marginTop: spacing.sm },
  big: { color: colors.text, fontSize: font.h3, fontWeight: '800' },
  label: { color: colors.text, fontSize: font.small, fontWeight: '700' },
  fieldLabel: { color: colors.textDim, fontSize: font.tiny, fontWeight: '600', marginBottom: 2 },
  note: { color: colors.textDim, fontSize: font.tiny, marginTop: spacing.xs },
  problem: { color: colors.warning, fontSize: font.small, marginTop: spacing.sm },
  msg: { color: colors.text, fontSize: font.small, marginTop: spacing.md },
  link: { color: colors.primary, fontSize: font.small, fontWeight: '600', marginTop: spacing.xs },
  linkSmall: { color: colors.primary, fontSize: font.tiny, fontWeight: '600', marginLeft: spacing.sm },
  taskHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', gap: spacing.sm },
  taskLabel: { color: colors.text, fontSize: font.small, fontWeight: '700', flex: 1 },
  status: { fontSize: font.tiny, fontWeight: '700' },
  docRow: { flexDirection: 'row', alignItems: 'center', marginTop: spacing.xs },
  sheetWrap: { flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(0,0,0,0.6)' },
  sheet: {
    backgroundColor: colors.surface, padding: spacing.lg,
    borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg, gap: spacing.sm,
  },
  stateRow: { paddingVertical: spacing.sm, borderBottomWidth: 1, borderBottomColor: colors.border },
})
