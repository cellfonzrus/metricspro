// Referral shared types + the helpers every Referral page needs. Kept here (rather than re-declared
// per page) so the referral shape can't drift between the dashboard, the list, the detail view and the
// approvals queue — the places that all render the same record. Styling reuses lib/crm's visual
// language so the module doesn't look bolted on.
import type { CSSProperties } from 'react'

// The six product 'bubbles', verbatim from the owner directive. MIRROR of
// backend referral_core.ALLOWED_PRODUCTS — keep in sync.
export const PRODUCTS = ['Phone', 'Activations', 'Tablet', 'BYOD', 'Home Internet', 'Accessories'] as const
export type Product = typeof PRODUCTS[number]

// The lifecycle states. MIRROR of backend referral_core state machine.
export type ReferralStatus =
  | 'created' | 'sent' | 'redeemed' | 'sale_logged' | 'activated'
  | 'commission_pending' | 'approved' | 'paid'
  | 'expired' | 'rejected' | 'void' | 'flagged_fraud'

export interface Referral {
  id: string
  referral_no: number
  referrer_name: string | null
  referrer_phone: string | null
  referrer_email: string | null
  customer_name: string | null
  customer_phone: string | null
  products: string[]
  status: ReferralStatus
  token_version: number
  redeem_expires_at: string | null
  sale_ref: string | null
  activation_ref: string | null
  commission_amount: number | null
  commission_amount_effective?: number
  payout_date: string | null
  approver_employee_id: string | null
  fraud_flag: boolean
  fraud_reason: string | null
  store_code: string | null
  market: string | null
  created_by: string | null
  notes: string | null
  created_at: string
  updated_at: string
  // decorated
  referrer_display?: string
  customer_display?: string
  status_label?: string
  is_redeem_expired?: boolean
  can_approve?: boolean
  approval_conflict?: string
}

export interface ReferralAudit {
  id: string; referral_id: string; action: string
  from_status: string | null; to_status: string | null; reason: string | null
  actor_employee_id: string | null; actor_kind: string; meta: any; created_at: string
}

export interface ReferralConfig {
  default_commission_amount: number; default_payout_offset_days: number
  qr_expiry_hours: number; redemption_window_hours: number
  max_referrals_per_referrer: number; velocity_window_days: number
  duplicate_match: string; require_approval: boolean; self_referral_block: boolean
  can_edit: boolean; can_approve: boolean; allowed_products: string[]
  qr_signing_configured: boolean
  me: { employee_id: string | null; store_code: string | null; market: string | null; is_manager: boolean }
}

// ── shared styles (identical tokens to lib/crm so the two modules match) ──
export const input: CSSProperties = { padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)', width: '100%', outline: 'none' }
export const label: CSSProperties = { fontSize: 12, color: 'var(--text2)', marginBottom: 3, display: 'block' }
export const cell: CSSProperties = { padding: '7px 12px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }
export const th: CSSProperties = { ...cell, textAlign: 'left', fontSize: 11, textTransform: 'uppercase', letterSpacing: '.4px', color: 'var(--text2)', fontWeight: 600 }
export const panel: CSSProperties = { background: 'var(--surface2)', borderRadius: 8, padding: 14, border: '1px solid var(--border)' }
export const btn: CSSProperties = { padding: '7px 14px', borderRadius: 7, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: 13, cursor: 'pointer' }
export const btnPrimary: CSSProperties = { ...btn, background: '#2563eb', borderColor: '#2563eb', color: '#fff', fontWeight: 600 }

// Colour per state — the funnel/list read at a glance. Terminal/exception states are muted or red.
export const STATUS_COLOR: Record<string, string> = {
  created: '#6b7280', sent: '#2563eb', redeemed: '#0891b2', sale_logged: '#7c3aed',
  activated: '#0d9488', commission_pending: '#f39c12', approved: '#16a34a', paid: '#15803d',
  expired: '#9ca3af', rejected: '#dc2626', void: '#9ca3af', flagged_fraud: '#dc2626',
}
export const STATUS_LABEL: Record<string, string> = {
  created: 'Created', sent: 'QR Sent', redeemed: 'Redeemed', sale_logged: 'Sale Logged',
  activated: 'Activated', commission_pending: 'Pending Approval', approved: 'Approved', paid: 'Paid',
  expired: 'Expired', rejected: 'Rejected', void: 'Void', flagged_fraud: 'Flagged Fraud',
}

export function referrerName(r: Partial<Referral>): string {
  return (r.referrer_name || '').trim() || r.referrer_phone || 'Unknown referrer'
}
export function customerName(r: Partial<Referral>): string {
  return (r.customer_name || '').trim() || r.customer_phone || '—'
}

/** Format a US 10-digit number for display; anything else is shown as typed. */
export function fmtPhone(v: string | null | undefined): string {
  const d = String(v || '').replace(/[^0-9]/g, '')
  if (d.length === 10) return `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}`
  if (d.length === 11 && d[0] === '1') return fmtPhone(d.slice(1))
  return String(v || '')
}

export function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString()
}
export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}
