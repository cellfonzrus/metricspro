"""Harness for the FTP/email sweep HONEST-ZERO + dedup-parity package (2026-07-30).

Drives the REAL `_run_email_sweep` / `_run_ftp_sweep` and the REAL `_sweep_ingest_outcome` against an
in-memory fake Supabase + stubbed transports. No network, no DB, no real ingest. What it proves:

  HONEST ZERO (no swallowed writes, every 0-row outcome carries a reason)
  • every ingest payload shape in this module is classified, and NO 0-row outcome is ever recorded with
    status 'ok' or with an empty `detail` — incl. the three shapes the email ladder had no branch for
    (a file that read fine but mapped 0 rows · a custom sheet with no data rows · a KNOWN_IGNORED_TYPES
    attachment, which returns status='skipped'/rows=0 and no marker)
  • OLD-vs-NEW DIFFERENTIAL against the REAL pre-change email ladder text from `origin/main`: it agrees
    byte-for-byte on every outcome the ladder handled, and the fixtures where it disagrees are exactly
    the ones the ladder recorded as a green "ok · 0 rows"
  • a failure to write the sweep's OWN journal row is COUNTED + reported (was `except Exception: pass`),
    because a lost journal row silently re-ingests the file on every run

  DEDUP SEMANTICS (cannot re-ingest hourly, cannot permanently skip a failed file)
  • rows landed        → done, never re-fetched
  • terminal zero      → done ('empty' / 'ignored'), so it stops being re-pulled every hour
  • non-terminal zero  → retried ('skipped' / 'error'), so a file that failed once is not skipped forever
  • proven on BOTH sweeps, including the FTP sweep's historical defect (ANY recorded row = skip forever)

  FTP ↔ EMAIL PARITY
  • the same ingest payload produces the same status / detail / rows_saved on both sweeps
  • the FTP sweep now reads the real row count (device-only inventory: N devices, not 0)
  • the FTP sweep now raises the truncated-export (shrink) alert, which it never did

  MULTI-TENANT + MONEY SAFETY
  • every read and write the sweeps issue is org-scoped, and a second tenant's journal is untouched
  • the money-writing promote/recalc trigger fires for EXACTLY the pre-change set (every non-error
    daily_sales outcome) — proven by counting real calls, incl. the new 'empty' status

Run: `python3 harness_sweep_honesty.py` from the backend dir.
"""
import asyncio
import os
import subprocess
import sys
import textwrap

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from fastapi import HTTPException                      # noqa: E402
from app.modules.commcalc import router as R           # noqa: E402
import app.modules.closing.router as CR                # noqa: E402

_pass = 0
_fail = 0
FAILED = []


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        FAILED.append(name)
        print(f"  FAIL  {name}")


class _Missing(dict):
    """Stands in for a row/alert the sweep was supposed to produce but did not.

    WHY. Assertions below index the first recorded row (`email_processed[0]`, `alerts[0]`). When a
    change upstream stops the sweep recording anything, that raises IndexError and the harness DIES
    mid-file — which reads as "not run" rather than "failed" and silently retires every assertion
    after it. That is exactly how the section-3 IndexError hid the stale `fetch_new_attachments`
    stub. `first()` converts an absent row into a LOUD, recorded failure and hands back this object,
    whose every lookup is falsy, so the dependent checks fail honestly instead of exploding.
    """

    def __getitem__(self, k):
        return ''

    def get(self, k, default=None):
        return ''


def first(seq, what):
    """seq[0], or a recorded FAILURE plus a falsy stand-in when the sweep produced nothing."""
    if seq:
        return seq[0]
    check(f"ANCHOR: the sweep produced a {what} for the assertions below to inspect", False)
    return _Missing()




HOUSE = "00000000-0000-0000-0000-000000000001"
TEN = "00000000-0000-0000-0000-0000000000aa"


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Fake Supabase: only the verbs the two sweeps use (select/eq/limit/order/execute, upsert, update).
# ══════════════════════════════════════════════════════════════════════════════════════════════════
class _Q:
    def __init__(self, store, log, schema, table, fail_writes_on=()):
        self.s, self.log, self.schema_, self.t = store, log, schema, table
        self._eq, self._limit, self._order = {}, None, None
        self._fail = fail_writes_on

    def select(self, *a, **k):
        self._verb = 'select'
        self._cols = list(a)          # recorded so a SCHEMA PROBE can be told from a DATA read
        return self

    def eq(self, k, v):
        self._eq[k] = v
        return self

    def order(self, c, desc=False):
        self._order = (c, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _key(self):
        return f"{self.schema_}.{self.t}"

    def upsert(self, row, on_conflict=None):
        self._verb, self._row, self._conflict = 'upsert', row, on_conflict
        return self

    def insert(self, row):
        self._verb, self._row = 'insert', row
        return self

    def update(self, row):
        self._verb, self._row = 'update', row
        return self

    def execute(self):
        v = getattr(self, '_verb', 'select')
        self.log.append({'table': self.t, 'verb': v, 'eq': dict(self._eq),
                         'row': getattr(self, '_row', None),
                         'cols': list(getattr(self, '_cols', [])), 'limit': self._limit})
        if v in ('upsert', 'insert', 'update') and self.t in self._fail:
            raise RuntimeError(f"write to {self.t} rejected (fake)")
        rows = self.s.setdefault(self._key(), [])
        if v == 'select':
            out = [dict(r) for r in rows
                   if all(str(r.get(k)) == str(vv) for k, vv in self._eq.items())]
            if self._order:
                out.sort(key=lambda r: str(r.get(self._order[0]) or ''), reverse=self._order[1])
            if self._limit is not None:
                out = out[:self._limit]
            return type('Res', (), {'data': out, 'count': len(out)})()
        if v == 'update':
            for r in rows:
                if all(str(r.get(k)) == str(vv) for k, vv in self._eq.items()):
                    r.update(self._row)
            return type('Res', (), {'data': []})()
        payload = self._row if isinstance(self._row, list) else [self._row]
        for p in payload:
            keys = [k.strip() for k in (getattr(self, '_conflict', None) or '').split(',') if k.strip()]
            hit = None
            if keys:
                hit = next((r for r in rows if all(str(r.get(k)) == str(p.get(k)) for k in keys)), None)
            if hit is not None:
                hit.update(p)
            else:
                rows.append(dict(p))
        return type('Res', (), {'data': payload})()


class FakeClient:
    def __init__(self, store, log, fail_writes_on=()):
        self.s, self.log, self.f = store, log, fail_writes_on

    def schema(self, s):
        c = self

        class _S:
            def table(_self, t):
                return _Q(c.s, c.log, s, t, c.f)
        return _S()


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Ingest payload fixtures — one per shape `upload_file` can return.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
PAYLOADS = {
    # ── outcomes the shipped email ladder already handled ──
    'clean_sales':        {'saved': 4533, 'file_type': 'daily_sales', 'shrink': []},
    'price_guard':        {'saved': 0, 'skipped': 'price_guard',
                           'shrink': [{'key': 'price-guard', 'prior': 900, 'new': 3,
                                       'reason': 'kept existing data for 2026-07-13 — a degraded export'}]},
    'price_guard_partial': {'saved': 51, 'skipped': 'price_guard_partial',
                            'guarded_dates': ['2026-07-13'],
                            'shrink': [{'key': 'price-guard-partial', 'prior': 900, 'new': 3,
                                        'reason': 'kept existing data for 2026-07-13 — ingested fresh days'}]},
    'inv_no_stores':      {'success': False, 'saved': 0, 'devices': 0, 'stores': 0,
                           'skipped': 'inventory_no_stores',
                           'note': 'parsed 0 stores + 0 devices from 812 row(s) — need a STORE column'},
    'inv_devices_only':   {'success': True, 'saved': 0, 'stores': 0, 'devices': 743,
                           'skipped': 'inventory_devices_only',
                           'note': '0 stores (no store column found) · 743 device row(s) saved'},
    'xreport_zero':       {'success': False, 'file_type': 'x_report', 'tenders': 0, 'saved': 0,
                           'skipped': 'header_not_found',
                           'note': "Read 3 sheet(s) — none carried a 'Tender Types … Net' header row"},
    'xreport_partial':    {'success': True, 'file_type': 'x_report', 'tenders': 14, 'saved': 14,
                           'skipped': 'x_report_partial_save',
                           'note': 'Saved 14 tender row(s), but 2 write(s) FAILED'},
    'xreport_unmapped':   {'success': True, 'file_type': 'x_report', 'tenders': 9, 'saved': 9,
                           'skipped': 'x_report_unmapped_labels',
                           'note': 'Saved 9 tender row(s). 3 tender label(s) were NOT recognized'},
    # ── the three shapes the ladder had NO branch for (recorded as a green "ok · 0 rows") ──
    'mapped_zero':        {'saved': 0, 'file_type': 'sales', 'period': 'July 2026', 'shrink': []},
    'custom_empty':       {'saved': 0, 'report_key': 'sales_trend_x', 'target_table': 'raw_custom_import',
                           'note': 'no data rows found — nothing captured (existing data preserved)'},
    'ignored_type':       {'status': 'skipped', 'file_type': 'sales_trend', 'rows': 0,
                           'reason': "'sales_trend' is a derived report with no importer — ignored."},
    # ── an outcome marker this code has not learned yet ──
    'future_marker':      {'saved': 0, 'skipped': 'some_new_reason_2027', 'note': 'brand new outcome'},
}

EXPECT = {   # (status, terminal, rows_saved)
    'clean_sales':         ('ok', True, 4533),
    'price_guard':         ('skipped', False, 0),
    'price_guard_partial': ('ok', True, 51),
    'inv_no_stores':       ('skipped', False, 0),
    'inv_devices_only':    ('ok', True, 743),
    'xreport_zero':        ('skipped', False, 0),
    'xreport_partial':     ('ok', True, 14),
    'xreport_unmapped':    ('ok', True, 9),
    'mapped_zero':         ('empty', True, 0),
    'custom_empty':        ('empty', True, 0),
    'ignored_type':        ('ignored', True, 0),
    'future_marker':       ('skipped', False, 0),
}

print("\n── 1. every ingest payload shape is classified, and NO 0-row outcome is a green tick ──")
for name, res in PAYLOADS.items():
    o = R._sweep_ingest_outcome(res, upload_type='daily_sales')
    exp = EXPECT[name]
    check(f"{name}: status={exp[0]} terminal={exp[1]} rows={exp[2]}",
          (o['status'], o['terminal'], o['rows_saved']) == exp)
check("no 0-row outcome is EVER recorded as 'ok'",
      all(R._sweep_ingest_outcome(p)['status'] != 'ok'
          for p in PAYLOADS.values() if R._sweep_ingest_outcome(p)['rows_saved'] == 0))
check("EVERY non-ok outcome carries a non-empty reason (`detail`)",
      all((R._sweep_ingest_outcome(p, upload_type='t')['detail'] or '').strip()
          for p in PAYLOADS.values() if R._sweep_ingest_outcome(p)['status'] != 'ok'))
check("a 'clean' ingest carries NO detail (so the UI shows a plain green tick)",
      R._sweep_ingest_outcome(PAYLOADS['clean_sales'])['detail'] is None)
check("a caveat ingest DOES carry a detail (amber, not a clean tick)",
      bool(R._sweep_ingest_outcome(PAYLOADS['price_guard_partial'])['detail'])
      and bool(R._sweep_ingest_outcome(PAYLOADS['inv_devices_only'])['detail']))
check("None / garbage result → 'empty' with a reason, never a crash",
      R._sweep_ingest_outcome(None)['status'] == 'empty'
      and R._sweep_ingest_outcome('nope')['detail'])
check("_ingest_rows_saved reads every count spelling (saved/tenders/stores/rows/devices)",
      R._ingest_rows_saved({'saved': 5}) == 5 and R._ingest_rows_saved({'tenders': 7}) == 7
      and R._ingest_rows_saved({'stores': 3}) == 3 and R._ingest_rows_saved({'rows': 2}) == 2
      and R._ingest_rows_saved(PAYLOADS['inv_devices_only']) == 743
      and R._ingest_rows_saved(None) == 0)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 2. OLD-vs-NEW differential against the REAL pre-change email ladder (pinned commit) ──")
# WHY THIS BLOCK IS ANCHORED TO A COMMIT AND NOT TO `origin/main`.
# The differential compares today's ladder against the code as it was BEFORE the honest-zero fix.
# It read that "before" from `origin/main` — a MOVING ref. The fix merged, origin/main absorbed it,
# the old text ceased to exist there, `str.index` raised, and the bare `except` printed a
# parenthetical note and set `_old_ladder = None`. The `if _old_ladder:` below then skipped FIVE
# assertions while the run still reported all-green: the harness whose entire job is to prove the
# sweep never reports a green lie was, itself, reporting a green lie. That is the single most
# important thing this repair fixes.
#
# Two changes. (1) The counterparty is now `f66139f2^` — the immutable commit immediately before
# "fix(commcalc): FTP/email sweep honest-zero + dedup parity" — so the differential is runnable
# forever instead of until the next merge. (2) Failing to extract it is now a LOUD FAILURE, never a
# silent skip: if no candidate ref yields the old ladder, the check below goes red and says so.
_old_ladder, _ladder_err, _ladder_ref = None, None, None
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
_CANDIDATE_REFS = [r for r in (os.environ.get('SWEEP_BASE_REF'), 'f66139f2^', 'origin/main') if r]
for _ref in _CANDIDATE_REFS:
    try:
        _main_src = subprocess.check_output(
            ['git', 'show', f'{_ref}:backend/app/modules/commcalc/router.py'],
            cwd=_REPO_ROOT, text=True, stderr=subprocess.DEVNULL)
        _i = _main_src.index("            rows_saved = (res or {}).get('saved', 0)\n"
                             "            shrink = (res or {}).get('shrink') or []")
        _j = _main_src.index("        except HTTPException as he:", _i)
        _block = textwrap.dedent(_main_src[_i:_j])
        _fn = ("def _old_ladder(res, XREPORT_ZERO_REASONS):\n"
               "    status, detail, rows_saved, shrink, skipped_flag = 'ok', None, 0, [], None\n"
               + textwrap.indent(_block, '    ')
               + "    return status, detail, rows_saved\n")
        _ns = {}
        exec(compile(_fn, f'<{_ref} email ladder>', 'exec'), _ns)
        _old_ladder, _ladder_ref = _ns['_old_ladder'], _ref
        break
    except Exception as e:                                        # pragma: no cover
        _ladder_err = f'{_ref}: {type(e).__name__}: {e}'

if _old_ladder is None:                                           # pragma: no cover
    print(f"        tried {_CANDIDATE_REFS}; last error: {_ladder_err}\n"
          f"        set SWEEP_BASE_REF to a ref that still contains the pre-fix email ladder.")
check("the pre-change ladder was extracted from a PERMANENT ref and runs "
      f"(ref={_ladder_ref or 'NONE'})", _old_ladder is not None)

if _old_ladder:
    agreed, diverged = [], []
    for name, res in PAYLOADS.items():
        o = R._sweep_ingest_outcome(res, upload_type='daily_sales')
        try:
            old = _old_ladder(res, R.XREPORT_ZERO_REASONS)
        except Exception as ex:
            diverged.append((name, f'old raised {ex}', o['status']))
            continue
        new = (o['status'], o['detail'], o['rows_saved'])
        (agreed if old == new else diverged).append((name, old, new))
    ladder_handled = ['clean_sales', 'price_guard', 'price_guard_partial', 'inv_no_stores',
                      'inv_devices_only', 'xreport_zero', 'xreport_partial', 'xreport_unmapped']
    check("byte-identical status+detail+rows for EVERY outcome the old ladder handled",
          all(n in [a[0] for a in agreed] for n in ladder_handled))
    check("the ONLY divergences are the 0-row shapes the ladder had no branch for",
          sorted(d[0] for d in diverged) == ['custom_empty', 'future_marker', 'ignored_type', 'mapped_zero'])
    check("and for each of those the OLD code returned a green 'ok' with 0 rows",
          all(d[1][0] == 'ok' and d[1][2] == 0 for d in diverged))
    check("...while the NEW code names a reason for each",
          all(d[2][1] for d in diverged))
    for n, old, new in diverged:
        print(f"        {n}: OLD {old[0]!r}/{old[2]} rows/detail={old[1]!r}  →  NEW {new[0]!r}/{new[2]} rows")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Sweep drivers
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def run_email(store, attachments, payloads, *, org=HOUSE, fail_writes_on=(), alerts=None,
              promote_calls=None):
    """Drive the REAL _run_email_sweep. `payloads` maps filename → the dict upload_file returns
    (or an HTTPException to raise)."""
    log = []
    R.sb = lambda: FakeClient(store, log, fail_writes_on)                       # noqa: E731
    # The REAL signature is `fetch_new_attachments(cfg, already, unrouted=None)`, and the sweep calls
    # it with all three (router.py: `await _asyncio.to_thread(_email.fetch_new_attachments, cfg,
    # already, _unrouted)`). `unrouted` is a genuine later FEATURE — the list the fetcher appends
    # "data file that matched no pattern" records to, so a report renamed at the source is surfaced
    # instead of dropped silently. This stub still took two arguments, so every email sweep died
    # inside the driver with "takes 2 positional arguments but 3 were given", the sweep recorded
    # NOTHING, and section 3 then blew up on `email_processed[0]` — an IndexError standing in for
    # what was really a stale test double. Mirrors the real contract now, including leaving
    # `unrouted` untouched (every fixture attachment matches a pattern).
    def _fetch(cfg, already, unrouted=None):
        return [a for a in attachments if (a['message_id'], a['name']) not in already]

    R._email.fetch_new_attachments = _fetch
    seen_files = []

    async def _uf(ut, uf, period, force=False, org_id=None, trace_source=None, **kw):
        seen_files.append({'name': uf.filename, 'upload_type': ut, 'period': period,
                           'org_id': org_id, 'trace_source': trace_source})
        p = payloads.get(uf.filename)
        if isinstance(p, Exception):
            raise p
        return p
    R.upload_file = _uf
    R._registry_auto_map = lambda c, o: {'sales': True}
    R._resolve_carrier_mode = lambda carriers: 'plan'

    def _promote(c, o, p):
        (promote_calls if promote_calls is not None else []).append((o, p))
        return {'written': 0}
    R._promote_feed_to_raw_sales = _promote

    async def _alert(c, o, kind, subj, text, ref):
        (alerts if alerts is not None else []).append({'org': o, 'subject': subj, 'text': text, 'ref': ref})
    CR._send_alert = _alert
    out = asyncio.run(R._run_email_sweep(org))
    return out, log, seen_files


def run_ftp(store, files, payloads, *, org=HOUSE, fail_writes_on=(), alerts=None):
    log = []
    R.sb = lambda: FakeClient(store, log, fail_writes_on)                       # noqa: E731
    R._ftp.fetch_new_files = lambda cfg, already: [
        f for f in files if (f['name'], f['size']) not in already]
    seen_files = []

    async def _uf(ut, uf, period, force=False, org_id=None, trace_source=None, **kw):
        seen_files.append({'name': uf.filename, 'upload_type': ut, 'period': period,
                           'org_id': org_id, 'trace_source': trace_source})
        p = payloads.get(uf.filename)
        if isinstance(p, Exception):
            raise p
        return p
    R.upload_file = _uf

    async def _alert(c, o, kind, subj, text, ref):
        (alerts if alerts is not None else []).append({'org': o, 'subject': subj, 'text': text, 'ref': ref})
    CR._send_alert = _alert
    out = asyncio.run(R._run_ftp_sweep(org))
    return out, log, seen_files


def email_store(processed=None, org=HOUSE):
    return {'commcalc.email_sweep_config': [
                {'org_id': org, 'account': 'default', 'imap_host': 'imap.x', 'patterns': [{'pattern': '*.csv'}]}],
            'commcalc.email_processed': list(processed or []),
            'commcalc.carrier': [{'org_id': org, 'name': 'Total'}]}


def ftp_store(processed=None, org=HOUSE):
    return {'commcalc.ftp_sweep_config': [
                {'org_id': org, 'host': 'ftp.x', 'remote_dir': '/out/', 'patterns': [{'pattern': '*.csv'}]}],
            'commcalc.ftp_processed': list(processed or [])}


def att(name, mid=None):
    return {'name': name, 'size': len(name) * 10, 'upload_type': 'daily_sales',
            'message_id': mid or f'<{name}>', 'bytes': b'x'}


def ffile(name, ut='daily_sales'):
    return {'name': name, 'size': len(name) * 10, 'upload_type': ut, 'bytes': b'x'}


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 3. FTP ↔ EMAIL parity: the same payload produces the same honest record ──────────")
for fx in ('clean_sales', 'price_guard', 'inv_devices_only', 'xreport_zero', 'mapped_zero',
           'custom_empty', 'ignored_type'):
    es = email_store()
    eo, _l, _s = run_email(es, [att('f.csv')], {'f.csv': PAYLOADS[fx]})
    fs = ftp_store()
    fo, _l2, _s2 = run_ftp(fs, [ffile('f.csv')], {'f.csv': PAYLOADS[fx]})
    er = first(es['commcalc.email_processed'], 'email_processed row')
    fr = first(fs['commcalc.ftp_processed'], 'ftp_processed row')
    check(f"{fx}: FTP records the same status/detail/rows as email "
          f"({er['status']} · {er['rows_saved']} rows)",
          (er['status'], er['detail'], er['rows_saved']) == (fr['status'], fr['detail'], fr['rows_saved']))

fs = ftp_store()
fo, _l, _s = run_ftp(fs, [ffile('inv.csv', 'inventory_aging')], {'inv.csv': PAYLOADS['inv_devices_only']})
check("FTP now records the REAL row count for a device-only inventory ingest (743, was 0)",
      first(fs['commcalc.ftp_processed'], 'ftp_processed row')['rows_saved'] == 743)
fs = ftp_store()
fo, _l, _s = run_ftp(fs, [ffile('x.xlsx', 'x_report')], {'x.xlsx': PAYLOADS['xreport_zero']})
check("FTP no longer records a green ✓ for an X-Report that saved 0 tender rows",
      first(fs['commcalc.ftp_processed'], 'ftp_processed row')['status'] == 'skipped'
      and 'header' in (first(fs['commcalc.ftp_processed'], 'ftp_processed row')['detail'] or ''))
check("...and the FTP sweep's status line names it instead of claiming 0/1 ingested only",
      'saved 0 rows' in (first(fs['commcalc.ftp_sweep_config'], 'ftp_sweep_config row')['last_status'] or ''))
fs = ftp_store()
fo, _l, _s = run_ftp(fs, [ffile('ma.csv', 'ma_commission')], {'ma.csv': PAYLOADS['clean_sales']})
check("FTP passes NO period for the four DATE-KEYED reports (email-sweep parity)",
      first(_s, 'uploaded file record')['period'] == '')

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 4. dedup: cannot RE-INGEST hourly (terminal outcomes are done) ────────────────────")
for label, fx in (('landed rows', 'clean_sales'), ("read-but-empty ('empty')", 'mapped_zero'),
                  ("no importer ('ignored')", 'ignored_type')):
    es = email_store()
    run_email(es, [att('f.csv')], {'f.csv': PAYLOADS[fx]})
    _, _, again = run_email(es, [att('f.csv')], {'f.csv': PAYLOADS[fx]})
    check(f"email · {label}: NOT re-ingested on the next sweep", again == [])
    fs = ftp_store()
    run_ftp(fs, [ffile('f.csv')], {'f.csv': PAYLOADS[fx]})
    _, _, again2 = run_ftp(fs, [ffile('f.csv')], {'f.csv': PAYLOADS[fx]})
    check(f"ftp   · {label}: NOT re-ingested on the next sweep", again2 == [])

print("\n── 5. dedup: cannot PERMANENTLY SKIP a failed file (non-terminal retries) ────────────")
for label, payload in (("errored", HTTPException(400, "This doesn't look like the right file")),
                       ("price-guard refusal", PAYLOADS['price_guard']),
                       ("X-Report 0 tenders", PAYLOADS['xreport_zero']),
                       ("unknown future marker", PAYLOADS['future_marker'])):
    es = email_store()
    run_email(es, [att('f.csv')], {'f.csv': payload})
    o2, _l, again = run_email(es, [att('f.csv')], {'f.csv': PAYLOADS['clean_sales']})
    check(f"email · {label}: retried next sweep and can then succeed",
          len(again) == 1 and o2['ingested'] == 1 and o2['retried'] == 1)
    fs = ftp_store()
    run_ftp(fs, [ffile('f.csv')], {'f.csv': payload})
    o3, _l2, again2 = run_ftp(fs, [ffile('f.csv')], {'f.csv': PAYLOADS['clean_sales']})
    check(f"ftp   · {label}: retried next sweep and can then succeed",
          len(again2) == 1 and o3['ingested'] == 1 and o3['retried'] == 1)

# The FTP sweep's historical defect, stated as its own case: a pre-existing errored row used to be in
# `already` (ANY recorded row was), so the file was skipped forever with no way to retry but SQL.
fs = ftp_store([{'org_id': HOUSE, 'filename': 'f.csv', 'file_size': 50, 'upload_type': 'daily_sales',
                 'rows_saved': 0, 'status': 'error', 'detail': 'boom'}])
o, _l, seen = run_ftp(fs, [ffile('f.csv')], {'f.csv': PAYLOADS['clean_sales']})
check("a historical FTP 'error' row no longer skips the file forever (self-heals, no SQL)",
      len(seen) == 1 and o['ingested'] == 1)
fs = ftp_store([{'org_id': HOUSE, 'filename': 'f.csv', 'file_size': 50, 'upload_type': 'daily_sales',
                 'rows_saved': 0, 'status': 'ok', 'detail': None}])
o, _l, seen = run_ftp(fs, [ffile('f.csv')], {'f.csv': PAYLOADS['clean_sales']})
check("a historical FTP green-zero row is retried ONCE and then settles honestly",
      len(seen) == 1 and first(fs['commcalc.ftp_processed'], 'ftp_processed row')['rows_saved'] == 4533)
fs2 = ftp_store([{'org_id': HOUSE, 'filename': 'f.csv', 'file_size': 50, 'upload_type': 'daily_sales',
                  'rows_saved': 4533, 'status': 'ok', 'detail': None}])
o, _l, seen = run_ftp(fs2, [ffile('f.csv')], {'f.csv': PAYLOADS['clean_sales']})
check("a historical FTP row that DID land rows is still skipped (no needless re-ingest)", seen == [])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 6. the sweep's OWN journal write is never swallowed ───────────────────────────────")
es = email_store()
o, _l, _s = run_email(es, [att('f.csv')], {'f.csv': PAYLOADS['clean_sales']},
                      fail_writes_on=('email_processed',))
check("email: a failed history write is COUNTED (was `except Exception: pass`)",
      o['journal_failures'] == 1 and o['journal_first_error'])
check("email: and reported on the mailbox status line, naming the re-processing consequence",
      'could NOT be recorded' in (first(es['commcalc.email_sweep_config'], 'email_sweep_config row')['last_status'] or ''))
check("email: the ingest itself still counts as ingested (the rows DID land)", o['ingested'] == 1)
fs = ftp_store()
o, _l, _s = run_ftp(fs, [ffile('f.csv')], {'f.csv': PAYLOADS['clean_sales']},
                    fail_writes_on=('ftp_processed',))
check("ftp: a failed history write is COUNTED and reported",
      o['journal_failures'] == 1
      and 'could NOT be recorded' in (first(fs['commcalc.ftp_sweep_config'], 'ftp_sweep_config row')['last_status'] or ''))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 7. truncated-export (shrink) alert now fires on BOTH sweeps ───────────────────────")
SHRUNK = {'saved': 40, 'file_type': 'daily_sales',
          'shrink': [{'key': '2026-07-14', 'prior': 4200, 'new': 40}]}
ea = []
run_email(email_store(), [att('f.csv')], {'f.csv': SHRUNK}, alerts=ea)
check("email: alerts (unchanged behaviour, now via the shared helper)",
      len(ea) == 1 and 'dropped to 40 rows' in first(ea, 'email shrink alert')['subject']
      and 'Mailbox: default' in first(ea, 'email shrink alert')['text'])
fa = []
run_ftp(ftp_store(), [ffile('f.csv')], {'f.csv': SHRUNK}, alerts=fa)
check("ftp: NOW alerts too (it discarded `shrink` entirely before)",
      len(fa) == 1 and 'dropped to 40 rows' in first(fa, 'ftp shrink alert')['subject'])
check("ftp: the alert names the FTP source, not a mailbox",
      'FTP: ftp.x/out/' in first(fa, 'ftp shrink alert')['text'])
check("both alerts share the ref key so one data drop cannot double-alert",
      first(ea, 'email shrink alert')['ref'] == first(fa, 'ftp shrink alert')['ref'])
fs = ftp_store()
run_ftp(fs, [ffile('f.csv')], {'f.csv': SHRUNK}, alerts=[])
check("ftp: the partial-export drop is also on the status line",
      'partial-export drop' in (fs['commcalc.ftp_sweep_config'][0]['last_status'] or ''))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 8. MONEY SAFETY: the promote/recalc trigger fires for EXACTLY the pre-change set ──")
# Before: every daily_sales result that did not RAISE was recorded 'ok' or 'skipped', and both fired
# the trigger. So: fires for every non-error outcome, and never for an error.
for fx in ('clean_sales', 'price_guard', 'price_guard_partial', 'mapped_zero', 'custom_empty',
           'inv_devices_only', 'future_marker', 'ignored_type'):
    calls = []
    run_email(email_store(), [att('f.csv')], {'f.csv': PAYLOADS[fx]}, promote_calls=calls)
    check(f"{fx}: promote/recalc STILL fires (pre-change behaviour preserved)", len(calls) == 1)
calls = []
run_email(email_store(), [att('f.csv')], {'f.csv': HTTPException(400, 'bad file')}, promote_calls=calls)
check("an ERRORED daily_sales still does NOT fire promote/recalc", calls == [])
calls = []
run_email(email_store(), [att('x.xlsx')],
          {'x.xlsx': PAYLOADS['clean_sales']}, promote_calls=calls)
check("promote is scoped to the tenant that swept", calls and calls[0][0] == HOUSE)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 9. multi-tenant: every read AND write is org-scoped; no cross-tenant bleed ────────")
es = email_store(org=TEN)
es['commcalc.email_processed'] = [
    {'org_id': HOUSE, 'account': 'default', 'message_id': '<f.csv>', 'filename': 'f.csv',
     'upload_type': 'daily_sales', 'rows_saved': 4533, 'status': 'ok'}]
o, log, seen = run_email(es, [att('f.csv')], {'f.csv': PAYLOADS['clean_sales']}, org=TEN)
check("the HOUSE journal row does NOT dedup the tenant's identical attachment", len(seen) == 1)
# RULE ONE, read side. One exemption, and it is stated rather than hidden: `_table_has_column()`
# (router.py) probes whether a column EXISTS by issuing `select(<col>).limit(1)` and throwing the
# result away — it returns a bool, never rows. It carries no org filter because it is generic over
# tables, some of which have no org_id. That is a schema question, not a tenant-data read, so it is
# exempt; but the exemption is defined by SHAPE (exactly one column, limit 1), not by table name, so
# it cannot quietly widen into "this table is allowed to skip org scoping".
def _is_schema_probe(q):
    return q['verb'] == 'select' and q['limit'] == 1 and len(q['cols']) == 1


_data_reads = [q for q in log if q['verb'] == 'select' and not _is_schema_probe(q)]
check("every DATA read carries .eq('org_id', <caller>)",
      all(q['eq'].get('org_id') == TEN for q in _data_reads))
check("the org-scope check is non-vacuous (it really saw the sweep's reads)",
      len(_data_reads) >= 2)
# And the exempted probes are only ever that: single-column, limit-1, no row data consumed.
check("every org-unscoped read is a bare schema probe, nothing else",
      all(_is_schema_probe(q) for q in log
          if q['verb'] == 'select' and q['eq'].get('org_id') != TEN))
check("every write STAMPS the caller's org_id (RULE ONE write side)",
      all((q['row'] or {}).get('org_id', TEN) == TEN for q in log
          if q['verb'] in ('upsert', 'insert')))
check("the tenant's ingest is filed under the TENANT org",
      any(r['org_id'] == TEN for r in es['commcalc.email_processed'])
      and seen[0]['org_id'] == TEN)
check("the HOUSE row is untouched",
      [r for r in es['commcalc.email_processed'] if r['org_id'] == HOUSE][0]['rows_saved'] == 4533)
fs = ftp_store(org=TEN)
fs['commcalc.ftp_processed'] = [{'org_id': HOUSE, 'filename': 'f.csv', 'file_size': 50,
                                 'upload_type': 'daily_sales', 'rows_saved': 4533, 'status': 'ok'}]
o, log, seen = run_ftp(fs, [ffile('f.csv')], {'f.csv': PAYLOADS['clean_sales']}, org=TEN)
check("ftp: the HOUSE journal row does not dedup the tenant's file, and reads are org-scoped",
      len(seen) == 1 and all(q['eq'].get('org_id') == TEN for q in log if q['verb'] == 'select'))
check("ftp: trace_source is still tagged so upload_trace attributes the ingest",
      seen[0]['trace_source'] == 'ftp_sweep')

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n── 10. status lines tell the whole truth (no silent 0/N) ─────────────────────────────")
es = email_store()
o, _l, _s = run_email(es, [att('a.csv'), att('b.csv'), att('c.csv'), att('d.csv')],
                      {'a.csv': PAYLOADS['clean_sales'], 'b.csv': PAYLOADS['mapped_zero'],
                       'c.csv': PAYLOADS['ignored_type'], 'd.csv': PAYLOADS['price_guard']})
msg = es['commcalc.email_sweep_config'][0]['last_status']
check("only the file that really landed rows counts as ingested (1/4, not 3/4)",
      msg.startswith('1/4 attachments ingested'))
check("the read-but-empty file is named", 'read but carried no ingestable rows' in msg and 'b.csv' in msg)
check("the ignored type is reported separately", 'ignored (no importer' in msg)
check("the price-guard refusal keeps its wording", 'refused by price guard' in msg)
fs = ftp_store()
o, _l, _s = run_ftp(fs, [ffile('a.csv'), {'name': 'bad.csv', 'size': 9, 'upload_type': 'sales',
                                          'bytes': None, 'error': 'RETR timeout'}],
                    {'a.csv': PAYLOADS['clean_sales']})
fmsg = fs['commcalc.ftp_sweep_config'][0]['last_status']
check("ftp: a download failure is reported (and not recorded, so it retries)",
      'download failure' in fmsg and fs['commcalc.ftp_processed'] and
      len(fs['commcalc.ftp_processed']) == 1)

print("\n══════════════════════════════════════════════════════════════════════════════════════")
print(f"  {_pass} passed, {_fail} failed")
if FAILED:
    for f in FAILED:
        print(f"   ✗ {f}")
print("══════════════════════════════════════════════════════════════════════════════════════")
sys.exit(1 if _fail else 0)
