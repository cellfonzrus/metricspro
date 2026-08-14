'use client'
// ── Run Commission — the SHARED control, mounted on every commission-structure surface ───────────
// OWNER DIRECTIVE 2026-08-05: after editing the commission structure the owner must be able to
// recalculate right there instead of navigating back to the dashboard.
//
// One implementation (this file + runCommission.ts), four mounts: /commcalc/commission-plans,
// /commcalc/plan-installments, /commcalc/payout-schedules, /commcalc/payout-plans.
//
// What it deliberately does NOT do:
//   • it does not invent a permission — it renders only for a user who can already reach
//     /commcalc/payout-schedules, the STRICTEST surface that carries a Calculate control today
//     (nav scopes ['all']), so this is never more permissive than what shipped;
//   • it does not guess a period — the target period is displayed next to the button and is picked
//     from the periods this tenant actually has sales for (RULE THREE);
//   • it does not fire on a click — a mis-click on a config page must not replace a month of payouts,
//     so an explicit confirm naming the period AND the tenant stands in front of the POST;
//   • it does not retry. See runCommission.ts RULE 3 / [[recompute-gateway-timeout]].
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, ORG_ID, fmt } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import { canAccessPath } from '@/lib/rbac'
import EntityPicker from '@/components/EntityPicker'
import {
  createRunCommission, initialState, confirmText, deltaLabel, humanSince,
  type RunState,
} from './runCommission'

// The surface that already carries a Calculate control and is gated the tightest. Matching it exactly
// is the whole permission story — do not swap this for a new key.
export const RUN_COMMISSION_GATE_PATH = '/commcalc/payout-schedules'
const REPORT_HREF = '/commcalc'   // the Rep Commission report (the CommCalc dashboard)

export type PeriodOption = { id: string; label: string; sublabel?: string }

// ── presentational: a pure function of RunState, so the proof can render every response mode ─────
export function RunCommissionPanel({ state, onConfirm, onCancel, onDismiss, reportHref = REPORT_HREF }: {
  state: RunState
  onConfirm: () => void
  onCancel: () => void
  onDismiss: () => void
  reportHref?: string
}) {
  const s = state
  if (s.phase === 'idle') return null

  if (s.phase === 'confirm') {
    return (
      <div role="alertdialog" aria-label="Confirm recalculation" data-testid="run-commission-confirm"
        style={{ ...box, borderLeft: '4px solid #b45309', background: 'var(--surface2)' }}>
        <div style={{ fontWeight: 700, marginBottom: 6 }}>{confirmText(s.period, s.tenant)}</div>
        <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.6 }}>
          Every rep&apos;s stored commission for <b>{s.period}</b> is deleted and rewritten from the current
          plans, tiers, schedules and sales. It can take several minutes and it cannot be undone — the
          previous numbers are not kept.
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
          <button className="btn btn-primary" data-testid="run-commission-confirm-yes" onClick={onConfirm}>
            Yes — recalculate {s.period}
          </button>
          <button className="btn btn-secondary" data-testid="run-commission-confirm-no" onClick={onCancel}>Cancel</button>
        </div>
      </div>
    )
  }

  if (s.phase === 'busy') {
    // 409 from the single-flight guard. NOT an error: this press started nothing.
    return (
      <div data-testid="run-commission-busy" style={{ ...box, borderLeft: '4px solid var(--amber)', background: 'var(--surface2)' }}>
        <div style={{ fontWeight: 700, marginBottom: 4 }}>
          ⏳ A recompute is already running{s.runningSince ? ` (started ${humanSince(s.runningSince)})` : ''} — wait for it to finish
        </div>
        <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.6 }}>
          Nothing was started by this press. Two recomputes of {s.period} at once would interleave the
          delete-and-rewrite of the incentive rows. Watch{' '}
          <a href={reportHref} style={{ color: 'var(--accent)' }}>the Rep Incentive report</a> — it updates when the
          running one finishes.
        </div>
        <button className="btn btn-secondary" style={{ marginTop: 8, fontSize: 12 }} onClick={onDismiss}>Dismiss</button>
      </div>
    )
  }

  if (s.phase === 'starting' || s.phase === 'running') {
    return (
      <div data-testid="run-commission-running" style={{ ...box, borderLeft: '4px solid var(--accent)', background: 'var(--surface2)' }}>
        <div style={{ fontWeight: 700, marginBottom: 4 }}>
          ⚙️ Recalculating {s.period}{s.gatewayTimeout ? ' — still running' : ''}
        </div>
        <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.6 }}>{s.message}</div>
        {(s.gatewayTimeout || s.uncertain) && (
          <div data-testid="run-commission-no-retry" style={{ fontSize: 12, color: '#b45309', marginTop: 6 }}>
            Do not press it again — re-running it now would restart the delete-and-rewrite and can leave the
            month at $0. This page keeps checking on its own.
          </div>
        )}
        {s.before !== null && (
          <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 6 }}>Before this run: {fmt(s.before)}</div>
        )}
      </div>
    )
  }

  if (s.phase === 'failed') {
    return (
      <div data-testid="run-commission-failed" style={{ ...box, borderLeft: '4px solid var(--red)', background: 'var(--surface2)' }}>
        <div style={{ fontWeight: 700, color: 'var(--red)', marginBottom: 4 }}>⚠ {s.period} — recalculation did not confirm</div>
        <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.6 }}>{s.message}</div>
        <div style={{ marginTop: 8, display: 'flex', gap: 10, alignItems: 'center' }}>
          <a href={reportHref} style={{ color: 'var(--accent)', fontSize: 12 }}>Open the Rep Incentive report →</a>
          <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={onDismiss}>Dismiss</button>
        </div>
      </div>
    )
  }

  // done
  const delta = deltaLabel(s)
  return (
    <div data-testid="run-commission-done" style={{ ...box, borderLeft: '4px solid var(--green)', background: 'var(--surface2)' }}>
      <div style={{ fontWeight: 700, color: 'var(--green)', marginBottom: 4 }}>✓ {s.period} recalculated</div>
      <div style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.7 }}>
        New total payout: <b data-testid="run-commission-total">{s.after === null ? '—' : fmt(s.after)}</b>
        {s.reps !== null ? ` across ${s.reps} rep${s.reps === 1 ? '' : 's'}` : ''}
        {delta ? <span data-testid="run-commission-delta" style={{ marginLeft: 8, color: 'var(--text3)' }}>({delta})</span> : null}
      </div>
      <div style={{ marginTop: 8, display: 'flex', gap: 10, alignItems: 'center' }}>
        <a href={reportHref} style={{ color: 'var(--accent)', fontSize: 12 }}>Open the Rep Incentive report →</a>
        <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={onDismiss}>Dismiss</button>
      </div>
    </div>
  )
}

// ── container ────────────────────────────────────────────────────────────────────────────────────
export default function RunCommissionButton({
  period, onPeriodChange, periodOptions, label = '⚡ Run Commission', note, compact = false,
}: {
  /** The page's own period context, when it has one. The control never targets anything else silently. */
  period?: string
  /** Provide it and the picker writes the page's period too (one period per page, no divergence). */
  onPeriodChange?: (p: string) => void
  /** Periods the page already loaded. Omit and the control loads the tenant's own sales periods. */
  periodOptions?: PeriodOption[]
  label?: string
  note?: string
  compact?: boolean
}) {
  const { permissions, tenant } = useAuth()
  const allowed = canAccessPath(permissions || {}, RUN_COMMISSION_GATE_PATH)
  const tenantName = tenant?.name || ''

  // The target period: seeded from the page, overridable here, ALWAYS the one displayed AND the one
  // sent to /calculate. Derived from the prop (no effect, no cascading render): a local pick wins only
  // while the page's own period is unchanged, so if the page moves the period the display follows it.
  const base = period || ''
  const [localPick, setLocalPick] = useState<{ base: string; value: string } | null>(null)
  const target = localPick && localPick.base === base ? localPick.value : base

  const [state, setState] = useState<RunState>(() => initialState(base, tenantName))
  const [ctrl] = useState(() =>
    createRunCommission({ api, orgId: ORG_ID, tenant: tenantName, onChange: setState }))

  // Periods this tenant actually HAS sales for (RULE THREE). Read-only, money-free, org-scoped; the
  // window args are minimal because only `periods` is used here.
  const [loaded, setLoaded] = useState<PeriodOption[] | null>(null)
  useEffect(() => {
    if (periodOptions || !allowed) return
    let dead = false
    api('/api/v1/commcalc/plan-field-options?months=1&limit=1&value_limit=1')
      .then((o: unknown) => {
        if (dead) return
        const rows = (o && typeof o === 'object' ? (o as { periods?: unknown }).periods : null)
        setLoaded((Array.isArray(rows) ? rows : []).map((raw) => {
          const p = (raw || {}) as { value?: string; lines?: number }
          return { id: String(p.value || ''), label: String(p.value || ''),
                   sublabel: `${Number(p.lines || 0).toLocaleString()} sale lines` }
        }).filter(o2 => o2.id))
      })
      .catch(() => { if (!dead) setLoaded([]) })
    return () => { dead = true }
  }, [periodOptions, allowed])

  const options = useMemo(() => {
    const out = [...(periodOptions || loaded || [])]
    if (target && !out.some(o => o.id === target)) out.unshift({ id: target, label: target, sublabel: 'no sales rows found' })
    return out
  }, [periodOptions, loaded, target])

  const pick = useCallback((v: string) => {
    setLocalPick({ base, value: v })
    onPeriodChange?.(v)
    if (ctrl.get().phase === 'confirm') ctrl.cancel()   // changing the period cancels a pending confirm
  }, [ctrl, onPeriodChange, base])

  if (!allowed) return null

  const running = state.phase === 'starting' || state.phase === 'running'

  return (
    <div data-testid="run-commission" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <EntityPicker options={options} value={target || null} width={compact ? 150 : 180} allowCreate clearable={false}
          placeholder="pick a period…" onChange={v => pick(v || '')} onCreate={v => pick(v)}
          createLabel={v => `Use “${v}”`} ariaLabel="Period to recalculate" />
        <button className="btn btn-primary" data-testid="run-commission-button"
          disabled={running || !target}
          title={target ? `Recalculate ${target}` : 'Pick the period to recalculate'}
          onClick={() => ctrl.request(target, tenantName)}>
          {running ? '⏳ Running…' : label}
        </button>
        {/* The period is spelled out next to the button so it is never ambiguous WHICH month is at risk. */}
        <span data-testid="run-commission-target" style={{ fontSize: 12, color: 'var(--text2)' }}>
          for <b>{target || '—'}</b>{tenantName ? ` · ${tenantName}` : ''}
        </span>
      </div>
      {note && !compact && <div style={{ fontSize: 12, color: 'var(--text3)' }}>{note}</div>}
      <RunCommissionPanel state={state} onConfirm={() => { void ctrl.confirm() }}
        onCancel={() => ctrl.cancel()} onDismiss={() => ctrl.dismiss()} />
    </div>
  )
}

const box: React.CSSProperties = {
  padding: 12, borderRadius: 8, border: '1px solid var(--border)', maxWidth: 720,
}
