"""Account Module — multi-company accounting engine (#8), P&L + Balance Sheet (#9),
and the VIP credit-memo vs MI/ATU reconciliation (#10).

Design: every dollar total is computed deterministically in `coa.py` (exact, reproducible).
`engine.py` lets Claude assemble + narrate the statements, then the deterministic figures are
re-asserted as authoritative so a hallucinated number can never ship. Statements are persisted
as snapshots in commcalc.account_statements; the pages read the snapshot, not a fresh model call.
"""
