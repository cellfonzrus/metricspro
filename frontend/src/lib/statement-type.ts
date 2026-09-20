// THE STATEMENT-TYPE TOKEN a free-text statement type names — the twin of the backend's
// `report_kinds.statement_type_token` (index §30.10), so the card a page highlights and the
// mapping key the commit saves under agree. The vocabulary is the registry's commission-family
// `statement_type` values (`useReportKinds().visible` rows with landing 'commission'); nothing is
// listed here (RULE TWO: a third statement type is a registry row).
export const STATEMENT_TYPE_DEFAULT = 'commission'

const tok = (v: string | null | undefined) => String(v || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')

/** 'residual statement' → 'residual'; '' / 'commission statement' → 'commission'; an unknown text → its own slug. */
export function statementTypeToken(text: string, knownTokens: string[]): string {
  const s = tok(text)
  if (!s) return STATEMENT_TYPE_DEFAULT
  const known = Array.from(new Set(knownTokens.map(tok).filter(t => t && t !== STATEMENT_TYPE_DEFAULT))).sort((a, b) => b.length - a.length)
  for (const t of known) if (t === s || s.split('_').includes(t) || s.includes(t)) return t
  if (s.includes(STATEMENT_TYPE_DEFAULT)) return STATEMENT_TYPE_DEFAULT
  return s
}
