import React, { useState } from 'react'
import { ActivityIndicator, Keyboard, Pressable, StyleSheet, Text, TextInput, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useRouter } from 'expo-router'

import { useAuth } from '@/auth/AuthContext'
import { scopeOf, atLeast, type Scope } from '@/lib/scope'
import { currentPeriod, parsePeriod, periodLabel } from '@/lib/period'
import { money, count } from '@/lib/format'
import * as R from '@/api/reports'
import { colors, font, radius, spacing } from '@/theme'

// ── TopChatBar — the chat, pinned on top of every tab ──────────────────────────────────────────────
// The native twin of the web "ask bar" (frontend/src/components/AskBar.tsx), and like it, DETERMINISTIC
// and backend-free: it parses a metric + a period out of the typed question, calls the matching live
// report endpoint, and answers inline with a "View →" deep-link into that native dashboard. No LLM, no
// new API. Rendered as the tabs' custom header so it sits above Home/POS/CRM/… — "keep chat on top".
//
// It respects reporting scope exactly like the dashboards do: an intent whose report the user can't see
// is not computed — the bar says so — so the chat can never become a side channel to company numbers.

type Intent = {
  test: RegExp
  minScope: Scope
  route: string
  label: string
  run: (period: string) => Promise<{ value: string; tone?: 'good' | 'bad' }>
}

const consolidated = (o: R.Overview, key: 'net_income' | 'gross_profit'): number | undefined => {
  const s = o.scopes?.find((x) => x.scope_key === 'consolidated') || o.scopes?.[0]
  return s ? (s[key] as number | undefined) : undefined
}

const INTENTS: Intent[] = [
  {
    test: /\bnet (income|profit)\b|\bbottom line\b/i,
    minScope: 'market',
    route: '/dashboards/pnl',
    label: 'Net income',
    run: async (p) => {
      const v = consolidated(await R.getOverview(p), 'net_income')
      return { value: money(v), tone: (v ?? 0) >= 0 ? 'good' : 'bad' }
    },
  },
  {
    test: /\bgross (profit|margin)\b|\bgp\b/i,
    minScope: 'market',
    route: '/dashboards/pnl',
    label: 'Gross profit',
    run: async (p) => ({ value: money(consolidated(await R.getOverview(p), 'gross_profit')) }),
  },
  {
    test: /\b(revenue|sales|turnover)\b/i,
    minScope: 'market',
    route: '/dashboards/sales',
    label: 'Revenue',
    run: async (p) => {
      const n = await R.getSalesNarrative(p).catch(() => null)
      const v = (n?.facts?.revenue as number | undefined) ??
        ((await R.getSalesReport(p)).totals?.revenue as number | undefined)
      return { value: money(v) }
    },
  },
  {
    test: /\b(activation|activated|activations|boxes|units)\b/i,
    minScope: 'market',
    route: '/dashboards/exec-mtd',
    label: 'Activations',
    run: async (p) => {
      const n = await R.getExecMtdNarrative(p).catch(() => null)
      const v = (n?.facts?.total_activation as number | undefined) ??
        ((await R.getExecMtd(p)).by_location?.total?.total_activation as number | undefined)
      return { value: count(v) }
    },
  },
  {
    test: /\b(commission|payout|incentive|comp)\b/i,
    minScope: 'market',
    route: '/dashboards/commission',
    label: 'Commission payout',
    run: async (p) => {
      const rows = await R.getCommissions(p)
      const sum = (rows || []).reduce((s, r) => s + (Number(r.total_payout) || 0), 0)
      return { value: money(sum) }
    },
  },
]

type Answer = { label: string; period: string; value: string; route: string; tone?: 'good' | 'bad' } | { error: string }

export function TopChatBar({ title }: { title?: string }) {
  const insets = useSafeAreaInsets()
  const router = useRouter()
  const { me } = useAuth()
  const scope = scopeOf(me)
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [answer, setAnswer] = useState<Answer | null>(null)

  async function ask() {
    const text = q.trim()
    Keyboard.dismiss()
    if (!text) return
    const intent = INTENTS.find((i) => i.test.test(text))
    if (!intent) {
      setAnswer({ error: 'Try: revenue, activations, net income, gross profit, or commission.' })
      return
    }
    if (!atLeast(scope, intent.minScope)) {
      setAnswer({ error: `You don't have access to ${intent.label.toLowerCase()}.` })
      return
    }
    const period = parsePeriod(text) || currentPeriod()
    setBusy(true)
    setAnswer(null)
    try {
      const { value, tone } = await intent.run(period)
      setAnswer({ label: intent.label, period, value, route: intent.route, tone })
    } catch (e: unknown) {
      setAnswer({ error: e instanceof Error ? e.message : 'Could not fetch that just now.' })
    } finally {
      setBusy(false)
    }
  }

  function openReport() {
    if (answer && 'route' in answer) {
      router.push(answer.route as never)
      setAnswer(null)
      setQ('')
    }
  }

  return (
    <View style={[styles.wrap, { paddingTop: insets.top + spacing.xs }]}>
      <View style={styles.row}>
        {title ? <Text style={styles.title} numberOfLines={1}>{title}</Text> : null}
        <View style={styles.inputWrap}>
          <Text style={styles.icon}>💬</Text>
          <TextInput
            value={q}
            onChangeText={setQ}
            onSubmitEditing={ask}
            returnKeyType="search"
            placeholder="Ask… e.g. revenue last month"
            placeholderTextColor={colors.textDim}
            style={styles.input}
          />
          {busy ? <ActivityIndicator color={colors.primary} /> : null}
          {q.length > 0 && !busy ? (
            <Pressable onPress={ask} hitSlop={8}><Text style={styles.go}>Ask</Text></Pressable>
          ) : null}
        </View>
      </View>

      {answer ? (
        <Pressable onPress={'route' in answer ? openReport : () => setAnswer(null)} style={styles.answer}>
          {'error' in answer ? (
            <Text style={styles.answerErr}>{answer.error}</Text>
          ) : (
            <Text style={styles.answerText}>
              <Text style={{ color: colors.textDim }}>{answer.label} · {periodLabel(answer.period)}:  </Text>
              <Text style={{ color: answer.tone === 'bad' ? colors.danger : answer.tone === 'good' ? colors.success : colors.text, fontWeight: '800' }}>
                {answer.value}
              </Text>
              <Text style={{ color: colors.primary }}>    View →</Text>
            </Text>
          )}
        </Pressable>
      ) : null}
    </View>
  )
}

const styles = StyleSheet.create({
  wrap: {
    backgroundColor: colors.surface,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
    paddingHorizontal: spacing.md,
    paddingBottom: spacing.sm,
    gap: spacing.sm,
  },
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  title: { color: colors.text, fontSize: font.h3, fontWeight: '800' },
  inputWrap: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    backgroundColor: colors.surfaceAlt,
    borderRadius: radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    paddingHorizontal: spacing.md,
    minHeight: 40,
  },
  icon: { fontSize: 14 },
  input: { flex: 1, color: colors.text, fontSize: font.small, paddingVertical: spacing.sm },
  go: { color: colors.primary, fontWeight: '800', fontSize: font.small },
  answer: {
    backgroundColor: colors.surfaceAlt,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  answerText: { fontSize: font.small },
  answerErr: { color: colors.textDim, fontSize: font.small },
})
