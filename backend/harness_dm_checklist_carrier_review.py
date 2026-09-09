"""PROOF: the carrier brand-review questions reach the DM's screen, and name no carrier.

OWNER DIRECTIVE 2026-09-09, verbatim: *"Make the following a part of the dm checklist and replace
boost to carrier"* — followed by a carrier Brand Resolution Visit form, six sections, 24 questions.

NO NEW MECHANISM. `storeops.checklist_items` (mig 027) is already the configurable, management-
editable DM visit checklist, read by `GET /storevisit/checklist-items` and rendered by
`/storeops/visits/new`. Migration 1000 adds ROWS to it. Nothing here is a second checklist.

THE DEFECT THIS WOULD HAVE HIT, and why a static check is the only thing that could see it.
`visits/new/page.tsx` grouped the checklist by mapping over a FIXED list of six categories:

    const grouped = CATS.map(([key, label]) => [label, items.filter(it => (it.category||'general') === key)])

An item whose category was not one of those six matched no group and was filtered out of ALL of
them — it sat in the database, never rendered, and nothing anywhere said so. The API accepts any
category string (`create_checklist_item` passes `item.category` through), so config and screen could
disagree in total silence. THREE of this form's six sections are new categories, so seeding them
against the old page would have written TWELVE questions the DM could never see — a checklist that
looks complete in settings and is missing half its questions on the visit.

Nothing about that is a type error and nothing about it fails a build: every value is a legal string
and the page renders perfectly. Only a reader looking at the screen, or this check, can see it.

WHAT THIS PINS
  A. the seed is parsed OUT of migration 1000, so this file cannot pass against a seed the migration
     does not contain;
  B. EVERY category the migration seeds is one the visit page groups — the regression that would have
     hidden the twelve questions;
  C. the unknown-category fallback exists, so a category added later by an API caller lands under the
     trailing group rather than vanishing (the general defect, not just this seed's instance);
  D. no seeded label names a carrier brand — RULE TWO, and the owner's explicit instruction;
  E. both the visit form and Visit Settings carry the same category vocabulary, so an item cannot be
     filed under a category the form refuses to show;
  F. the item keys are unique and the sort orders leave the mig-027 day-to-day checks on top.

PURE / DB-FREE: parses the real migration and the real .tsx sources as text; stdlib only.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MIG = os.path.join(HERE, '..', 'database', 'migrations',
                   '1000_dm_checklist_carrier_brand_review.sql')
NEW_PAGE = os.path.join(HERE, '..', 'frontend', 'src', 'app', '(platform)', 'storeops',
                        'visits', 'new', 'page.tsx')
SET_PAGE = os.path.join(HERE, '..', 'frontend', 'src', 'app', '(platform)', 'storeops',
                        'visits', 'settings', 'page.tsx')

failures, checks = [], 0


def check(label, cond):
    global checks
    checks += 1
    if not cond:
        failures.append(label)


def read(p):
    with open(p, encoding='utf-8') as fh:
        return fh.read()


mig, new_src, set_src = read(MIG), read(NEW_PAGE), read(SET_PAGE)

# ── A. the seed, parsed out of the migration itself ────────────────────────────────────────────
body = mig.split('INSERT INTO storeops.checklist_items', 1)[1].split('ON CONFLICT', 1)[0]
ROW = re.compile(r"\('([0-9a-f-]{36})','([^']+)','((?:[^']|'')+)','([^']+)','([^']+)',(\d+)\)")
rows = ROW.findall(body)
check('A1 the migration seeds 24 checklist rows (got %d)' % len(rows), len(rows) == 24)
check('A2 every seeded row is the house org',
      all(r[0] == '00000000-0000-0000-0000-000000000001' for r in rows))

keys = [r[1] for r in rows]
cats = {r[3] for r in rows}
types = {r[4] for r in rows}
orders = [int(r[5]) for r in rows]

check('A3 every item_key is unique', len(set(keys)) == len(keys))
check('A4 every item_key is namespaced qa_ so the REVERT can find them',
      all(k.startswith('qa_') for k in keys))
check('A5 input types are only the schema''s check|text|photo (got %r)' % (types,),
      types <= {'check', 'text', 'photo'})

# ── F. ordering: the routine mig-027 checks stay on top ────────────────────────────────────────
check('F1 every seeded sort_order sits after the mig-027 defaults (max 160)', min(orders) >= 200)
check('F2 sort orders are unique so the form has a stable sequence', len(set(orders)) == len(orders))

# the six sections of the owner's form are all represented
check('F3 all six sections are seeded (got %r)' % (sorted(cats),),
      cats == {'general', 'appearance', 'facilities', 'customer', 'merchandise', 'employee'})

# ── B/E. every seeded category is one BOTH screens group ───────────────────────────────────────
# NB: split on the array's CLOSING bracket at line start. Splitting on the first ']' stops at the
# end of the first ['appearance','Appearance'] pair and silently sees ONE category — which is how
# this check first "failed": the parser was wrong, not the page.
cats_block = new_src.split('const CATS: [string, string][] = [', 1)[1].split('\n]', 1)[0]
page_cats = set(re.findall(r"\['([a-z_]+)',", cats_block))
check('B1 the visit form groups every category the migration seeds; missing=%r'
      % (sorted(cats - page_cats),), cats <= page_cats)

set_block = set_src.split('const CATS = [', 1)[1].split(']', 1)[0]  # flat string array: first ']' IS its end
settings_cats = set(re.findall(r"'([a-z_]+)'", set_block))
check('E1 Visit Settings offers every category the migration seeds; missing=%r'
      % (sorted(cats - settings_cats),), cats <= settings_cats)
check('E2 the two screens share one vocabulary (form=%r settings=%r)'
      % (sorted(page_cats - settings_cats), sorted(settings_cats - page_cats)),
      page_cats == settings_cats)

# ── C. the general defect: an unknown category is not dropped ──────────────────────────────────
grouped = new_src.split('const grouped', 1)[1][:700]
check('C1 the grouping keeps a set of the known categories', 'known' in grouped)
check('C2 ... and routes an UNKNOWN category into the trailing group instead of dropping it',
      '!known.has(' in grouped)
check('C3 the fallback is the general bucket, not a silent filter',
      "key === 'general'" in grouped)
# the old shape must be gone: a bare equality filter with no fallback would reintroduce the hole
check('C4 the original drop-everything-unknown filter is gone',
      "items.filter(it => (it.category || 'general') === key)" not in new_src)

# ── D. RULE TWO — no carrier brand in any seeded label ──────────────────────────────────────────
BRANDS = ('boost', 'vidapay', 't-cetra', 'tcetra', 'verizon', 'at&t', 'att ', 't-mobile',
          'tmobile', 'metro', 'cricket', 'total wireless', 'straight talk', 'tracfone')
labels = [r[2] for r in rows]
offenders = [(k, l) for k, l in zip(keys, labels)
             if any(b in l.lower() for b in BRANDS)]
check('D1 no seeded label names a carrier brand; offenders=%r' % (offenders[:4],), not offenders)
# and the replacement actually happened — the form's carrier-specific questions still ASK the thing
carrier_qs = [l for l in labels if 'carrier' in l.lower()]
check('D2 the carrier-specific questions survived the rename (got %d, expected >= 8)'
      % len(carrier_qs), len(carrier_qs) >= 8)
check('D3 the two free-text/photo items are the general ones',
      {k for k, r in zip(keys, rows) if r[4] != 'check'} == {'qa_visit_notes', 'qa_infraction_photos'})

# the questions themselves are questions, not fragments
qmarks = sum(1 for l in labels if l.strip().endswith('?'))
check('D4 the 22 inspection items read as questions (got %d)' % qmarks, qmarks == 22)

print('%s  harness_dm_checklist_carrier_review: %d checks, %d failed'
      % ('FAIL' if failures else 'OK  ', checks, len(failures)))
for f in failures:
    print('   FAILED: %s' % f)
sys.exit(1 if failures else 0)
