"""PROOF: a table cell that declares WHITE TEXT also declares its OWN background.

OWNER DEFECT 2026-09-08, verbatim: *"the table entries where the store names are not seen as they
both white, background should be blue and letters white font"*.

THE DEFECT — and why `tsc` and a build could never catch it. In `commcalc/expenses/page.tsx` the
header ROW carried the house blue:

    <tr style={{ background: 'var(--accent)' }}>
      <th style={{ ..., color: 'white', background: 'var(--accent)' }}>Expense</th>   <- visible
      <th style={{ ..., color: 'white' }}>{s.store_code}</th>                          <- INVISIBLE
      <th style={{ ..., color: 'white' }}>Total</th>                                   <- INVISIBLE

`globals.css` styles the ELEMENT TYPE:

    th { background: var(--surface2); ... }        /* #f1f4f8 — near-white */

A type selector in the stylesheet paints the `th`'s own background box, which sits ON TOP of the
`tr`'s background. So a `th` that does not set its own background is near-white regardless of the
row, and white letters on it are white-on-white. The first column escaped only because it happened
to set `background: 'var(--accent)'` inline for its `position: sticky`. The store names — the one
column the reader needs to tell the rows apart — were the ones that vanished.

Nothing about this is a type error and nothing about it fails a build: every value is a legal
CSSProperties string. It is only visible to a reader looking at the screen, or to this check.

THE RULE PINNED HERE (general, not a list of the two sites that were broken): inside `frontend/src`,
any `<th>` or `<td>` whose inline style sets `color` to white MUST set `background` in that same
inline style. Inheriting it from the parent `<tr>` is not sufficient for `th` and is fragile for
`td`. Sibling tables `commcalc/gp`, `commcalc/flags` and `accounts/pl` already satisfy this by
setting the background on every cell — this check is what stops the next table from drifting.

WHAT THIS PINS
  A. the general rule, over every .tsx under frontend/src;
  B. the two REGRESSIONS the owner reported — the expenses store/Total headers and the
     residual-per-sub period/Total headers — each named explicitly, so a revert is caught here
     rather than by the owner;
  C. the parser itself is honest: it finds the cells it claims to find (a silently-zero scan that
     inspects nothing would otherwise "pass" forever).

PURE / DB-FREE: reads the real .tsx sources as text; no network, no database, stdlib only.
"""
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend', 'src')
CELL = re.compile(r'<(th|td)\b[^>]*?style=\{\{(.*?)\}\}', re.S)
WHITE = re.compile(r"color:\s*'(?:white|#fff|#ffffff)'", re.I)

failures = []
checks = 0


def check(label, cond):
    global checks
    checks += 1
    if not cond:
        failures.append(label)


def tsx_files():
    for base, _dirs, files in os.walk(ROOT):
        for f in files:
            if f.endswith('.tsx'):
                yield os.path.join(base, f)


# ── A. the general rule ────────────────────────────────────────────────────────────────────────
offenders = []
cells_seen = 0
for path in tsx_files():
    with open(path, encoding='utf-8') as fh:
        src = fh.read()
    for m in CELL.finditer(src):
        style = m.group(2)
        if not WHITE.search(style):
            continue
        cells_seen += 1
        if 'background' not in style:
            line = src[:m.start()].count('\n') + 1
            offenders.append('%s:%d' % (os.path.relpath(path, ROOT), line))

check(
    'A1 no white-text table cell relies on an inherited background; offenders=%r' % (offenders[:8],),
    not offenders,
)
# C. the scan actually inspected something — a regex that matches nothing must not read as "clean".
check('A2 the scan found white-text cells to inspect (found %d)' % cells_seen, cells_seen >= 10)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding='utf-8') as fh:
        return fh.read()


# ── B. the two reported regressions, named ─────────────────────────────────────────────────────
exp = read(os.path.join('app', '(platform)', 'commcalc', 'expenses', 'page.tsx'))

store_th = re.search(r'<th key=\{s\.store_code\}[^>]*?style=\{\{(.*?)\}\}', exp, re.S)
check('B1 expenses: the per-store header cell is present', store_th is not None)
if store_th:
    st = store_th.group(1)
    check('B2 expenses: the per-store header declares white text', bool(WHITE.search(st)))
    check("B3 expenses: the per-store header sets its own blue background", "background: 'var(--accent)'" in st)

tot_th = re.search(r"<th style=\{\{([^}]*?)\}\}>Total<ResizeHandle", exp, re.S)
check('B4 expenses: the Total header cell is present', tot_th is not None)
if tot_th:
    check("B5 expenses: the Total header sets its own blue background", "background: 'var(--accent)'" in tot_th.group(1))

rps = read(os.path.join('app', '(platform)', 'accounts', 'residual-per-sub', 'page.tsx'))
per_th = re.search(r'<th key=\{p\}[^>]*?style=\{\{(.*?)\}\}', rps, re.S)
check('B6 residual-per-sub: the per-period header cell is present', per_th is not None)
if per_th:
    check("B7 residual-per-sub: the per-period header sets its own blue background",
          "background: 'var(--accent)'" in per_th.group(1))

# ── the stylesheet premise this whole check rests on ───────────────────────────────────────────
with open(os.path.join(ROOT, 'app', 'globals.css'), encoding='utf-8') as fh:
    css = fh.read()
th_rule = re.search(r'^th\s*\{([^}]*)\}', css, re.M)
check('D1 globals.css still styles the `th` element type', th_rule is not None)
if th_rule:
    # If this ever stops being true the rule above is merely harmless, never wrong — but the
    # REASON in this docstring would be stale, and a stale reason is how a guard gets deleted.
    check('D2 the `th` type rule still sets a background (the reason this guard exists)',
          'background' in th_rule.group(1))

print('%s  harness_table_header_contrast: %d checks, %d failed'
      % ('FAIL' if failures else 'OK  ', checks, len(failures)))
for f in failures:
    print('   FAILED: %s' % f)
sys.exit(1 if failures else 0)
