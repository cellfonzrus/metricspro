// WHAT A HUMAN PICKS THIS COMPANY BY — the app's half of the one home (index §POS-02).
//
// THE DEFECT (owner, 2026-10-30, with a screenshot): the "Choose a company" screen listed four raw
// UUIDs. The label was resolved `name → org_name → org_id`, so the moment the payload carried no
// usable name the screen printed the id — the one string on that row that tells a person nothing
// about which business they are about to sign into, on the one screen whose whole job is telling
// them apart.
//
// The server now guarantees a label (`core/tenant_identity.picker_label_from`: the onboarding name,
// else the slug read back as words, else "Company", and never a UUID). This module is the client's
// OWN promise of the same rule, because the app on a phone is not the app in this repository — a
// bundle months older than the server reaches the same screen, and it must not print an id either.
//
// PURE, and in `lib/` rather than `api/` deliberately: it imports nothing, so it is provable in CI
// without React Native, a device or a network. `api/core.ts` re-exports it; nothing re-derives it.

/** The shape of an org id. A label of this shape is the defect, not a label. */
const ORG_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** The last resort. Generic by RULE TWO — it names no tenant, and it is a word, not an id. */
export const GENERIC_COMPANY_LABEL = 'Company'

/** Is this text an org id rather than something a person could read? */
export function looksLikeOrgId(value: unknown): boolean {
  return ORG_ID.test(String(value ?? '').trim())
}

/** One line, collapsed whitespace — the same trim the server's `display_name_from` applies. */
function oneLine(value: unknown): string {
  return String(value ?? '')
    .split(/\s+/)
    .filter(Boolean)
    .join(' ')
}

/** `ny-logistics` → `Ny Logistics`. A slug is the typed name, machine-folded; read back it is far
 *  closer to what the business is called than any stand-in we could invent. */
function wordsFromSlug(slug: unknown): string {
  // An id stored in the slug column reads as nothing, not as an id with its hyphens knocked out.
  if (looksLikeOrgId(slug)) return ''
  const text = oneLine(String(slug ?? '').replace(/[-_]+/g, ' '))
  return text.replace(/\b\w/g, (c) => c.toUpperCase())
}

/** The fields of a membership this rule reads. Kept structural so any payload shape can be passed. */
export type CompanyLabelSource = {
  org_id?: string
  name?: string | null
  org_name?: string | null
  slug?: string | null
}

/** What to print on the button that chooses this company. Never blank, never an org id. */
export function companyLabel(t: CompanyLabelSource | null | undefined): string {
  for (const candidate of [oneLine(t?.name), oneLine(t?.org_name), wordsFromSlug(t?.slug)])
    if (candidate && !looksLikeOrgId(candidate)) return candidate
  return GENERIC_COMPANY_LABEL
}
