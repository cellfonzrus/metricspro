"""THE MODULE GRAPH — one home for "what else answers this question, and who reads it".

Owner directive 2026-10-04: *"one hand does not talk to the other and we need to build a check
mechanism via the index and registry we created that everytime an update or extension of a module is
done all connected pieces get updated automatically, the tree should be interlinked properly."*

THE CLASS, NOT THE INSTANCE. The instance was an alert's send record implemented four times
(index 15.1). The class is: **a shared fact acquires a second caller, or a second derivation, and
nothing tells the author that other pieces answer the same question.** Fifteen `harness_*_lock.py`
files already each held their own private `HOME = ...` plus a hand-typed list of callers — which is
the duplicate defect wearing the shape of its own cure: fifteen private copies of "what depends on
what", none of them interlinked, none of them queryable, every one of them hand-maintained.

So the graph has ONE home, this file, and it is DERIVED where it can be:

  · `FACTS[key]["homes"]`      — the file(s) allowed to answer this question. Hand-declared; this is
                                 the ruling, and a ruling cannot be inferred.
  · `FACTS[key]["callers"]`    — a SNAPSHOT of every file that imports a home, and the name each one
                                 binds it to. NOT hand-maintained: `harness_module_graph_guard.py`
                                 recomputes it from the import graph and fails the build when it
                                 differs, so a new connected piece cannot land silently.
  · `FACTS[key]["index"]`      — the `docs/SYSTEM_DATA_FLOW_INDEX.md` section(s) that document it.
                                 The guard checks each one resolves to a real heading.
  · `FACTS[key]["locks"]`      — the harness(es) that enforce it. The forbidden-pattern rules stay
                                 in those locks; copying them here would be the very defect this
                                 file exists to stop. The guard checks each lock exists and is run
                                 by a workflow.

WHAT THIS BUYS, concretely: `connected(path)` answers "I am about to change this file — what else
answers the same question?" in one call, and the guard puts that answer in front of the author on
the pull request instead of relying on them to remember to look.

WHAT IT DELIBERATELY DOES NOT DO: it does not edit the connected pieces for you. Nothing can safely
rewrite a caller; what breaks the "one hand does not talk to the other" loop is that the other hand
is now NAMED, in CI, before the merge.

Pure data and pure functions — no DB, no network, no imports beyond the stdlib, so a lock may
dereference it without importing the application.
"""
from __future__ import annotations

# Bump when the SHAPE of a fact entry changes, so a stale consumer fails loudly rather than quietly.
SCHEMA = 1

FACTS: dict[str, dict] = {
    # ── §CORE-31 (security pass 2026-11-01) — a retention window that DELETES ────────────────────
    'location_fix_retention': {
        "question": 'HOW LONG MAY WE KEEP WHERE SOMEBODY WAS, AND WHAT HAPPENS WHEN THAT RUNS OUT? '
                    'Two tenant settings promised a window and nothing deleted anything: '
                    '`punch_geo_retention_days` (found by the punch geofence\'s own security '
                    'pass) and `checkin_geo_retention_days` (declared on the control box as '
                    '`marketing_checkin_gps_retention` = `unmonitored`, "no purge job runs"). '
                    'One class, two instances. A window that deletes nothing is worse than no '
                    'window because it READS as a commitment, so this holds the registry of every '
                    'relation that stores a person\'s position, the ONE date arithmetic, and a '
                    'sweep that clears the COORDINATES while KEEPING the verdict and the distance '
                    '— which is what makes honouring the promise free: no flag, report or export '
                    'moves. A row with no stamp is REPORTED, never purged on a guess. The '
                    'coordinate column NAMES are not held here: they are dereferenced from '
                    '`store_geofence.PUNCH_COLUMNS`, because a second copy of a column name is '
                    'how this class of defect returns. A BIPA face template is a different fact '
                    'and keeps its own home in `storeops/face_retention.py`. The schema numbers that add each '
                    'stamp column are carried by index CORE-31 and by the files themselves, not '
                    'restated in this string: a literal here would be a customer-copy defect under '
                    'the 19.36 boundary, which reads message keys and never data.',
        "homes": [
            "app/modules/core/geo_retention.py"
        ],
        "index": [
            "CORE-31"
        ],
        "locks": [
            "harness_geo_retention_lock.py"
        ],
        "callers": {
            'app/modules/core/router.py': ('_geo_ret',),
            'app/modules/marketing/event_logic.py': ('_ret',),
            'app/modules/storeops/router.py': ('_ret',),
        },
    },
    # ── §CORE-17 (owner ask 2026-10-24) — a reported figure NAMES the rows behind it ────────────
    'report_drilldown': {
        "question": 'WHERE IS THIS NUMBER COMING FROM? A report that summarises — a tile, a rollup '
                    'row, a per-person or per-store total — must open that figure to the rows it '
                    'was built from, and every row must name the report, feed or upload it came '
                    'out of plus the keys that address it there. Before this there were as many '
                    'answers as reports: the P&L accumulates `detail` labels in `account/coa.py` '
                    'and ties them out in TypeScript (§4e), the commission explain re-computes '
                    'from the plan engine, `/ma-overview-recon/drill` scans one table per tile, '
                    'and `"source"` means a table name, a screen name, an upload route or a free '
                    'sentence on ~30 other payloads. 93 of 136 report routes answered nothing at '
                    'all (audit 2026-10-24). The SHAPE of the answer lives here — `origin`, '
                    '`drill_row`, `tie_out`, `panel` — and the tie-out STATES a disagreement '
                    'between a figure and its own evidence rather than rounding one away. Absence '
                    'is never zero and never a tie: `ties` is None, not False, when a counted row '
                    'has no number. Which reports still OWE a drill-down is part of the same fact '
                    'and is written down (`harness_drilldown_pending.txt`), because "by default" '
                    'is a ratchet, not a sentence in a document.',
        # The CLIENT side of the same fact is a second home on purpose (§CORE-17.3d): a report whose
        # tiles are folded over the rows the reader filtered down to needs the panel narrowed to that
        # same selection, and `drill-narrow.ts` is the only place that happens. It is pinned to the
        # server's fold by object identity on the unfiltered case (frontend/prove_drill_narrow.mjs §A8)
        # over fixtures regenerated from the real declarations (harness_drill_narrow_fixtures.py), so
        # the two cannot become two arithmetics hoping to agree.
        "homes": ('app/modules/core/report_drilldown.py',
                  'frontend/src/lib/drill-narrow.ts'),
        "index": ('CORE-17',),
        "locks": ('harness_report_drilldown.py', 'harness_drilldown_coverage.py',
                  'harness_cash_recon_drilldown.py', 'harness_tender_recon_drilldown.py',
                  'harness_day_recon_drilldown.py', 'harness_drill_narrow_fixtures.py',
                  'harness_batch5_drilldown.py', 'harness_batch6_drilldown.py',
                  'harness_batch7_drilldown.py', 'harness_batch8_drilldown.py',
                  'harness_batch9_drilldown.py', 'harness_batch10_drilldown.py',
                  'harness_batch11_drilldown.py', 'harness_batch12_drilldown.py',
                  'harness_batch13_drilldown.py', 'harness_batch14_drilldown.py',
                  'harness_batch15_drilldown.py', 'harness_batch16_drilldown.py',
                  'harness_batch17_drilldown.py',
                  'harness_batch18_drilldown.py',
                  'harness_batch19_drilldown.py',
                  'harness_batch20_drilldown.py',
                  'harness_batch21_drilldown.py',
                  'harness_batch22_drilldown.py',
                  'harness_batch25_drilldown.py'),
        # SNAPSHOT — written BY HAND (§50: `--bless` silently deleted 11 of 12 facts on 2026-10-04).
        "callers": {
            'app/modules/account/device_billing_drilldown.py': ('report_drilldown',),
            'app/modules/account/inventory_value_drilldown.py': ('report_drilldown',),
            'app/modules/account/liabilities_drilldown.py': ('report_drilldown',),
            'app/modules/account/memo_recon_drilldown.py': ('report_drilldown',),
            'app/modules/account/statement_drilldown.py': ('report_drilldown',),
            'app/modules/asset/device_asset_drilldown.py': ('report_drilldown',),
            'app/modules/closing/attempts_drilldown.py': ('report_drilldown',),
            'app/modules/closing/cash_recon_drilldown.py': ('report_drilldown',),
            'app/modules/closing/cash_variance.py': ('report_drilldown',),
            'app/modules/closing/chain_drilldown.py': ('report_drilldown',),
            'app/modules/closing/day_recon_drilldown.py': ('report_drilldown',),
            'app/modules/closing/declared_feed_drilldown.py': ('report_drilldown',),
            'app/modules/closing/envelope_drilldown.py': ('report_drilldown',),
            'app/modules/closing/external_credit_drilldown.py': ('report_drilldown',),
            'app/modules/closing/ops_chargeback_drilldown.py': ('report_drilldown',),
            'app/modules/closing/pickup_drilldown.py': ('report_drilldown',),
            'app/modules/closing/recon_drilldown.py': ('report_drilldown',),
            'app/modules/closing/rollup_drilldown.py': ('report_drilldown',),
            'app/modules/closing/three_way_drilldown.py': ('report_drilldown',),
            'app/modules/commcalc/accessory_money_drilldown.py': ('report_drilldown',),
            'app/modules/commcalc/accessory_order_drilldown.py': ('_dd',),
            'app/modules/commcalc/activation_count_drilldown.py': ('report_drilldown',),
            'app/modules/commcalc/byod_upgrade_drilldown.py': ('report_drilldown',),
            'app/modules/commcalc/carrier_owed_drilldown.py': ('report_drilldown',),
            'app/modules/commcalc/carrier_pay_drilldown.py': ('report_drilldown',),
            'app/modules/commcalc/device_financing_drilldown.py': ('report_drilldown',),
            'app/modules/commcalc/entitlement_coverage_drilldown.py': ('report_drilldown',),
            'app/modules/commcalc/exec_month_drilldown.py': ('report_drilldown',),
            'app/modules/commcalc/performance_coaching_drilldown.py': ('report_drilldown',),
            'app/modules/commcalc/processor_money_drilldown.py': ('report_drilldown',),
            'app/modules/commcalc/rep_incentive_drilldown.py': ('report_drilldown',),
            'app/modules/commcalc/sales_margin_drilldown.py': ('report_drilldown',),
            'app/modules/commcalc/sales_outcome_drilldown.py': ('report_drilldown',),
            'app/modules/commcalc/targets_drilldown.py': ('report_drilldown',),
            'app/modules/commcalc/tender_recon_drilldown.py': ('report_drilldown',),
            'app/modules/crm/reports_drilldown.py': ('report_drilldown',),
            'app/modules/helpdesk/dashboard_drilldown.py': ('report_drilldown',),
            'app/modules/storeops/payroll_hours_drilldown.py': ('report_drilldown',),
            'app/modules/storeops/scheduled_hours_drilldown.py': ('report_drilldown',),
        },
    },
    # ── §CORE-31 (owner report 2026-10-24) — what an INTERNAL SELF-CALL must prove ───────────────
    'internal_self_call': {
        "question": 'When a handler reaches a sibling module through the platform\'s OWN HTTP '
                    'surface (`INTERNAL_API_BASE_URL`, default 127.0.0.1:8000), what identity must '
                    'that call carry? TWO facts, not one: WHO is calling (the caller\'s bearer '
                    'token) and WHICH TENANT they are acting as (`x-active-org`, as '
                    '`tenant_middleware` already validated it). The call re-enters the same gate '
                    'from the outside, so anything it drops is a fact the gate must guess — and '
                    'the gate correctly refuses to. Dropping the token read as **$0.00 owed** on '
                    'every envelope (2026-08-04); dropping the tenant read as a **409 '
                    '`tenant_choice_required`** on the envelope payout screen for the four logins '
                    'that belong to more than one company and have no `is_default_org` row (owner '
                    'report 2026-10-24: `core.access_log` shows both 409s with `actor_auth_id` '
                    'NULL between the outer 200s of `ab08bb15…` acting as Luxelink). The NOTE a '
                    'refused self-call turns into is part of the fact: 401/403/404/409 each read '
                    'as a cause, never as a number. Stamping the already-validated tenant is not a '
                    'widening — `x-active-org` is honoured only when it names one of the '
                    'caller\'s own memberships.',
        "homes": ('app/core/internal_call.py',),
        "index": ('CORE-16',),
        "locks": ('harness_internal_self_call.py',),
        # SNAPSHOT — written BY HAND (§50: `--bless` silently deleted 11 of 12 facts on 2026-10-04).
        "callers": {
            'app/modules/closing/router.py': ('internal_api_base', 'self_call_headers', 'self_call_note'),
            'app/modules/storeops/router.py': ('internal_api_base', 'self_call_headers'),
        },
    },
    # ── §CORE-07 (owner report 2026-10-14) — which PERSON a login belongs to ─────────────────────
    'self_lockout': {
        "question": 'Would THIS administrative write take away the authority that just made it? '
                    'Every role / membership / role-permission write in `core/router.py` gates on '
                    '`can_edit_setting(caller, \'security\')` and may then write the very row or '
                    'role that answer was computed from — a one-way door, because no screen is left '
                    'that could undo it (the `ss@1313global.us` Vzone membership, owner '
                    '2026-10-16). The AUTHORITY ANSWER ITSELF is part of this fact and lives here '
                    'too: `can_edit_setting` moved out of `core/router.py` and the router '
                    'dereferences it, because a guard asking a different question than the gate '
                    'protects a different door. Which writers are EXCUSED is part of the fact and '
                    'is written down — provisioning, elevation, reinstatement and the deliberate '
                    'self hand-over can only add authority or ARE the hand-over.',
        "homes": ('app/modules/core/self_lockout.py',),
        "index": ('CORE-12',),
        "locks": ('harness_self_lockout.py',),
        # SNAPSHOT — written BY HAND (§50: `--bless` silently deleted 11 of 12 facts on 2026-10-04).
        "callers": {
            'app/modules/core/router.py': ('_self_lockout',),
        },
    },
    'app_user_employee_link': {
        "question": 'Which EMPLOYEE does a login belong to, and in what order is that decided when '
                    'an administrator names one, the row already carries one, and the roster '
                    'resolves one by email? `storeops.app_users.employee_id` is the single column '
                    'that ties a login to its person, and with it NULL the login is NOBODY: '
                    '`core/scope.py::visible_people_keyset` narrows a people_visibility-\'self\' '
                    'role to the empty set, so the rep opens Schedule to a blank week, and '
                    '`closing/router.py::_closer_gate` refuses their closing `closer_no_name`. '
                    'Three paths wrote such a row and each decided for itself: the Roles grid sent '
                    'the id, the add-a-person form and the spreadsheet upload did not. Which '
                    'writers are EXCUSED is part of the same fact and is written down — tenant '
                    'creation and super-admin elevation mint an \'all\'-scope administrator, who '
                    'is on no store\'s roster. Sibling of §19.45 THE ONE MINT, which gives the '
                    'PERSON a business id; this one makes the LOGIN point at it.',
        "homes": ('app/modules/core/app_user_identity.py',),
        "index": ('CORE-07',),
        "locks": ('harness_app_user_employee_link.py',),
        # SNAPSHOT — written BY HAND (§50: `--bless` silently deleted 11 of 12 facts on 2026-10-04).
        # The lock verifies it against the real import graph.
        "callers": {
            'app/modules/core/router.py': ('_app_user_identity',),
        },
    },
    'stored_credential_column': {
        "question": 'Which stored COLUMNS hold a credential — a portal password, an authenticator '
                    'secret, a live browser session, a vendor API key — and therefore must be '
                    'ENCRYPTED AT REST? Which primitive opens one (text vs the JSONB envelope), '
                    'which schema the table is in and which columns address one of its rows are the '
                    'same fact, read again by the backfill, and live here too (`json_columns`, '
                    '`table_schema`, `table_key`). A credential-looking column that is deliberately '
                    'NOT encrypted is part of the same fact and carries its reason in writing '
                    '(`NOT_A_SECRET`), because an excuse nobody wrote down is indistinguishable '
                    'from an oversight.',
        "homes": ('app/modules/core/portal_secret.py',),
        "index": ('CORE-03',),
        "locks": ('harness_portal_secret_lock.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11 of
        # 12 facts on 2026-10-04). The lock verifies it against the real import graph.
        "callers": {
            'app/core/encryption_backfill.py': ('portal_secret',),
            'app/modules/account/router.py': ('_psec',),
            'app/modules/billing/router.py': ('_psec',),
            'app/modules/commcalc/router.py': ('_psec',),
            'app/modules/storeops/google_reviews.py': ('_psec',),
            'app/modules/storeops/router.py': ('_psec',),
            'app/modules/supply/api_credential.py': ('_psec',),
        },
    },
    # ── §CORE-04 (measured 2026-10-12) — and the rows ALREADY stored ─────────────────────────────
    'encryption_backfill_coverage': {
        "question": 'Which encrypted-at-rest column family has a sweep that seals the rows written '
                    'BEFORE the column became encrypted? "Encrypted on write" and "sealed at rest" '
                    'used to be two unrelated facts, so every new encrypted column started out half '
                    'protected and the application could not tell: two employees\' bank routing '
                    'numbers were readable in production on 2026-10-12 while run_all() claimed to '
                    'run every sweep. A column whose writes were ALWAYS encrypted owes no sweep and '
                    'says so in writing (`BORN_SEALED`), proved rather than trusted. And — the '
                    'second half, measured 2026-10-13 — WHAT sealing a family means is the '
                    'WRITER\'s to say, declared per family as `sealer` and APPLIED by the sweep: '
                    'every sweep read its writer\'s list of WHICH columns and then inlined its own '
                    '`crypto.encrypt`, which agreed by luck for a text column and did NOT for a '
                    'JSONB one, so the credential registry\'s JSONB half was skipped on every boot '
                    'and three live portal sessions stayed readable.',
        "homes": ('app/core/encryption_backfill.py',),
        "index": ('CORE-04',),
        "locks": ('harness_encryption_backfill_coverage.py',),
        # SNAPSHOT — written BY HAND (§50). These are the modules that import the home, DERIVED from
        # the import graph. The modules the registry HOLDS TO A SWEEP are a different set: the home
        # now imports THEM (each family's `sealer`), which is the opposite direction and does not
        # make them callers. `vision/router.py` is held to no sweep at all — it claims BORN_SEALED.
        # That set is enforced by the lock's §C coverage scan and §I sealer wiring, not by an import
        # edge — which is the whole point of the lock: a module can start encrypting without ever
        # referencing this one.
        "callers": {
            'app/main.py': ('run_all',),
            'app/modules/hr/router.py': ('encryption_backfill',),
            'app/scripts/backfill_customer_pin_encryption.py': ('backfill_customer_pins',),
        },
    },
    # ── §COMMCALC-07 (owner directive 2026-10-12) — one pull loop, many feeds ───────────────────
    'portal_report_pull': {
        "question": 'Which code reaches a report on a portal we are logged into, presses its export '
                    'control and lands the file? It used to be whichever sweep needed it: the loop '
                    'and the feed were one lump inside merchant_portal_sweep.py, bound to card '
                    'settlement by three hard-coded calls, so a second feed on the same machinery '
                    'could only be built by copying the calibration ladder, the export words, the '
                    'honest-failure wording and the cold-session restore. A feed is now an ADAPTER '
                    '(four functions) and the loop is written once; three older sweeps still drive '
                    'their own and are PINNED with reasons in the lock, a count that may only shrink. '
                    'Second axis (§COMMCALC-37): WITH WHICH PARAMETERS was it run? A report is '
                    'identified by its page AND its filters, so the dates, dropdowns and detail '
                    'toggles are DECLARED per report in portal_calibration and set by '
                    'apply_report_params before the Run press and the export — and a report whose '
                    'declared parameters could not all be set is NOT exported, because the page '
                    'default under the asked-for date is the wrong figures wearing the right date. '
                    'Whether a report runs once per day is DERIVED from that declaration ({date} '
                    'appearing in a value), never a second switch that could disagree with it. '
                    'Third axis (COMMCALC-39): WHERE DOES THE DECLARATION COME FROM? Off the page, '
                    'not a guess — read_page_controls / control_selector / control_purpose / '
                    'suggest_params read a report page through the authenticated session and SUGGEST '
                    'its declaration, writing nothing, because a framework-generated control id '
                    'cannot be guessed and a wrong selector is now a report that never exports. '
                    'specs_for is the one resolver of a source\'s declared reports, so the capture '
                    'reads exactly the set the pull will run.',
        "homes": ('app/modules/commcalc/portal_report_pull.py',),
        "index": ('COMMCALC-07', 'COMMCALC-37', 'COMMCALC-39'),
        "locks": ('harness_portal_report_pull.py',),
        # SNAPSHOT — written BY HAND (§50): the modules that IMPORT the home, derived from the import
        # graph. The adapters import it for its helpers and are resolved BY it through the registry,
        # which is a runtime edge the import graph cannot see and the lock's §A proves instead.
        "callers": {
            'app/modules/commcalc/login_progressive.py': ('prp',),
            'app/modules/commcalc/merchant_portal_sweep.py': ('DEFAULT_WINDOW_DAYS', 'MAX_WINDOW_DAYS', 'date_range', 'make_pull_fn', 'next_expiry', 'prp', 'pull_reports_on_page', 'session_ttl_hint', 'window_days'),
            'app/modules/commcalc/pos_report_sweep.py': ('prp',),
            'app/modules/commcalc/report_declaration.py': ('_prp',),
            'app/modules/commcalc/router.py': ('_prp',),
            'app/modules/commcalc/supplier_invoice_sweep.py': ('prp',),
        },
    },
    # ── §COMMCALC-38 (owner ask 2026-10-27) — reading an exported report's columns ───────────────
    'report_column_vocabulary': {
        "question": 'Which column of a downloaded report is which fact? Every portal feed faces it '
                    'before reading a figure: the export says "Trans Date", "Ext Price", "Center '
                    'Number". `pos_report_feed` answered it with a normalizer, a '
                    'longest-synonym-first header matcher and a header-row scorer, welded to its own '
                    'vocabulary — so a second feed needing the same three things against its own '
                    'vocabulary would have copied them. That copy fails expensively: `norm` is what '
                    'makes \'B-1115\' and \'b 1115\' one store key, and this platform has already '
                    'lost a phantom store holding $289 to a code compared under one rule here and '
                    'another rule there. So the MECHANISM is here and the VOCABULARY stays with each '
                    'feed, which also keeps every vendor and tenant spelling out of this module. '
                    '`money_or_none` DEREFERENCES merchant_portals.money and adds exactly one rule — '
                    'a blank cell is None, never 0.0 — because 0.0 asserts a supplier billed nothing '
                    'and a blank asserts nothing at all.',
        "homes": ('app/modules/commcalc/report_columns.py',),
        "index": ('COMMCALC-38',),
        "locks": ('harness_supplier_invoice_feed.py',),
        # SNAPSHOT — written BY HAND (§50). Both feeds keep their old public spellings
        # (`norm`/`map_headers`/`header_score`/`_cell`) as dereferences of this home, so every
        # existing caller of theirs reads the same code it always did.
        "callers": {
            'app/modules/commcalc/login_progressive.py': ('rc',),
            'app/modules/commcalc/portal_report_pull.py': ('norm',),
            'app/modules/commcalc/pos_report_feed.py': ('rc',),
            'app/modules/commcalc/report_declaration.py': ('_rc',),
            'app/modules/commcalc/supplier_invoice_feed.py': ('rc',),
        },
    },
    # ── §COMMCALC-47 — where a click on the live screen lands ────────────────────────────────────
    'live_frame_click_target': {
        "question": 'The live login streams a real browser and lets the operator click the picture; the '
                    'UI sends the click as a FRACTION of the image, so where on the live page is that? '
                    'It was answered inside the click handler by multiplying the fraction by the '
                    'configured viewport — but the frames come from Chrome\'s screencast, and every '
                    'frame arrives with metadata saying what it actually shows (deviceWidth / '
                    'deviceHeight, pageScaleFactor, offsetTop). The frame handler kept the JPEG and '
                    'the ack and DROPPED the metadata, so on any page whose visible size is not the '
                    'window every click landed off target by that ratio, worse the further from the '
                    'top-left the operator aimed (the owner\'s billing portal, 2026-10-29: "it shows '
                    'the live scren but cannot click on the box"). A frame is not a guess about a '
                    'viewport — it is an image that knows what it shows, so the geometry travels WITH '
                    'the frame and the click dereferences it. The arithmetic reduces exactly to the '
                    'old formula at scale 1 / offset 0 / device size = window, which is every portal '
                    'that works today.',
        "homes": ('app/modules/commcalc/live_frame.py',),
        "index": ('COMMCALC-47',),
        "locks": ('harness_live_frame.py',),
        # SNAPSHOT — written BY HAND (§50). Both live screens (the portal login and supply's vendor
        # window) drive the SAME live_login session, so this one translation serves both and there was
        # no sibling to fix.
        "callers": {
            'app/modules/commcalc/live_login.py': ('lf',),
        },
    },
    # ── §COMMCALC-46 — what a login page wants before it will show the password ──────────────────
    'login_first_step': {
        "question": 'Is the password on this login page, and if not, what does the page want first? '
                    'Every login driver must answer it before typing anything, and three of them '
                    'answered it differently: live_login._companyid_step and '
                    'vidapay_sweep.begin_login_b2bsoft each spelled ONE vendor\'s element ids '
                    '(#companyId / #btnSubmit / #username), and the GENERIC begin_login — the path '
                    'every other portal signs in through — did not answer it at all, reporting a '
                    'two-page sign-in as a form that renders unexpectedly (the owner\'s UPS billing '
                    'login, 2026-10-27). "The password is on a later page" is a SHAPE of login form, '
                    'not a fact about a vendor, so the decision is read off the page\'s own controls '
                    'and own words here, and a portal whose markup explains nothing declares its '
                    'fields in portal_calibration["login"] (RULE TWO: config, never code).',
        "homes": ('app/modules/commcalc/login_progressive.py',),
        "index": ('COMMCALC-46',),
        "locks": ('harness_login_progressive.py',),
        # SNAPSHOT — written BY HAND (§50). The planner is PURE and takes read_page_controls' output,
        # so the lock can prove every page shape without a browser; the driver is the only caller of
        # a page object, and both login paths plus the live session reach it rather than copying it.
        "callers": {
            'app/modules/commcalc/live_login.py': ('lp',),
            'app/modules/commcalc/vidapay_sweep.py': ('_lp',),
        },
    },
    # ── §COMMCALC-48 — a tenant's declared reports, as one editable thing ───────────────────────
    'report_declaration': {
        "question": 'Which reports does this login pull, and what must be set on each report\'s page '
                    'before the export is pressed? It was already config and not code \u2014 '
                    'pos_report_sweep.report_specs reads WHICH (portal_reports) and '
                    'portal_report_pull.report_params reads HOW (portal_calibration) \u2014 but '
                    'nothing could WRITE either column. No screen and no endpoint, so a tenant\'s '
                    'reports could be declared only by somebody with SQL access against the live '
                    'database, which from the owner\'s side is the same thing as hardcoded '
                    '(2026-10-29: "start definig the reports fromt eh back end again not hardpcded '
                    'but consumer defined editablke"). An editor wants ONE row per report with its '
                    'filters attached, and that is a THIRD shape of the same fact \u2014 so it is '
                    'DERIVED both ways and never stored, with columns(rows(src)) pinned as the '
                    'identity: opening the editor and saving changes nothing. per_day and problems '
                    'are derived for the same reason a stored copy could disagree with the filters '
                    'it describes. A capture SUGGESTS and never decides. AND THE FOLD HAPPENS HERE '
                    '(2026-10-31): merge_suggestion was declared as the home for folding a capture '
                    'into a row and had NO caller — PortalReportEditor.tsx had written the fold '
                    'again in TypeScript, matching a per-report RESULT (keyed `report`, as every '
                    'per-report result in this module is) against an editor ROW (keyed `key`), so it '
                    'matched nothing for any tenant and a capture of fourteen report pages added no '
                    'filter while announcing it had read them. spec_key is the only bridge between '
                    'the two spellings; the route now folds through this home and the browser only '
                    'displays what came back.',
        "homes": ('app/modules/commcalc/report_declaration.py',),
        "index": ('COMMCALC-48',),
        # The pytest lock that pins the fold to this home —
        # tests/unit/test_capture_merges_where_it_is_declared.py, run by tests.yml — is named in the
        # question above; `locks` carries harnesses, which is what this guard resolves.
        "locks": ('harness_report_declaration.py',),
        # SNAPSHOT — written BY HAND (§50). The home is PURE: the router is its only caller, and it
        # dereferences spec_key (§COMMCALC-39) and the filter vocabulary (§COMMCALC-37) rather than
        # restating either, so there is no second rule for "which report is this".
        "callers": {
            'app/modules/commcalc/router.py': ('rd',),
        },
    },
    # ── §COMMCALC-50 — what a page is telling us instead of showing a form ──────────────────────
    'portal_page_verdict': {
        "question": 'Is this page REFUSING us, CHALLENGING us, or THROTTLING us, rather than showing '
                    'us a form \u2014 and what should the operator be told? Four vocabularies '
                    'answered it: portal_block_marker rows (configurable, throttles only), '
                    'vidapay_sweep._BOT_PHRASES (a tuple in code), _looks_like_cloudflare\'s inline '
                    'markers, and a fourth tuple inside begin_login_b2bsoft that was OR\'d with the '
                    'first predicate, so a phrase a tenant configured was honoured by one half of '
                    'that condition and not the other. None held the owner\'s UPS billing wording '
                    '("you are trying to perform an action that is not allowed", 2026-10-30), so the '
                    'driver reported "Could not find the login form \u2014 it may render differently '
                    'than expected" about a page with no form on it. A page that refuses us is not a '
                    'login form that renders unexpectedly, so the words live in ONE table with a '
                    'kind column and a tenant adds a ROW, never a branch. The three kinds stay '
                    'three answers because the remedies differ \u2014 an address, a proxy, or time '
                    '\u2014 and only a throttle sets a cooldown. The rows are read WHOLE (the '
                    '\u00a74b.1 reading rule): the columns arrive one hand-applied schema change at '
                    'a time, so a '
                    'select naming them and a shorter one behind it is the column-block ladder that '
                    'rule forbids, and a row predating the kind column simply arrives without the '
                    'key and reads as the throttle row it always was. '
                    'THE SECOND HALF OF THE QUESTION (2026-10-31): "instead of showing a form" was '
                    'never asked. These markers match the page\'s MARKUP as well as its words '
                    '(_page_text reads content()) and "captcha" is a seeded challenge marker, so '
                    'every portal whose sign-in page ships a reCAPTCHA read as a bot wall \u2014 the '
                    'owner\'s UPS Store reporting portal says the word nine times, none of it '
                    'visible, on a working Username + Password form, and his live login spun 60s in '
                    '_wait_settle and then accused the egress IP while both boxes sat empty. '
                    'page_verdict now takes form_present and returns NOTHING when the form is '
                    'there; vidapay_sweep.login_form_present is the one home for that question and '
                    'is deliberately the same password-frame answer _wait_for_password already acts '
                    'on. Whether a human check GATES the submit stays a separate DOM question with '
                    'its own home (human_check_on_sign_in) \u2014 this marker list must not answer '
                    'it a second time by text search over markup.',
        "homes": ('app/modules/commcalc/portal_backoff.py',),
        "index": ('COMMCALC-50',),
        "locks": ('harness_portal_page_verdict.py',),
        # SNAPSHOT \u2014 written BY HAND (\u00a750). The driver keeps the predicates every caller already
        # asks (_looks_like_bot_wall / _looks_like_cloudflare) and dereferences the vocabulary; the
        # live login asks the driver's one reader. _CAPTCHA_TEXT is the EXCUSED sibling (\u00a7COMMCALC-11):
        # "must a human tick a box on this form" is a different question about a form that IS there.
        "callers": {
            'app/modules/commcalc/dlar_sweep.py': ('_pb',),
            'app/modules/commcalc/epay_sweep.py': ('_pb',),
            'app/modules/commcalc/import_audit.py': ('_pb',),
            'app/modules/commcalc/live_login.py': ('_pb',),
            'app/modules/commcalc/router.py': ('_m',),
            'app/modules/commcalc/vidapay_sweep.py': ('PortalRateLimited', '_pb'),
        },
    },
    # ── §COMMCALC-52 — what happened to the login we just submitted ─────────────────────────────
    'login_outcome': {
        "question": 'We submitted a portal login. WHAT HAPPENED — and what is the ONE thing the '
                    'operator should do about it? Three portal drivers answered this three ways and '
                    'the worst of them was load-bearing. epay_sweep decided from page.content() — '
                    'the MARKUP — with `any(w in body for w in ("invalid","incorrect","failed")) '
                    'and "passwordinput" in body`, so a single-page application that merely BUNDLES '
                    '`var M={invalid:"Invalid user name or password"}` read as having SAID it. Its '
                    'only route to success was the vendor\'s own words in the document title, which '
                    'is a carrier name in code (RULE TWO) and a false negative for every other '
                    'tenant\'s portal. Measured 2026-10-31: 1,665 scheduled runs, every 30 minutes, '
                    'no gap; the last successful login 2026-10-09T19:30:43Z; then 1,056 CONSECUTIVE '
                    'runs reporting "credentials rejected", and three carrier feeds dark for 22 days '
                    'because nobody could tell whether that sentence was true. dlar_sweep\'s probe '
                    'was sound (ask for a page that needs a session) but named every failure a '
                    'rejected credential; vidapay_sweep\'s was sound too (DOM, never markup) but '
                    'asserted "Login rejected" for every still-on-the-form case and asked the '
                    'operator to work the rest out from a diagnostic dict. SO: six named outcomes, '
                    'each carrying the one remedy that applies to it — signed_in, '
                    'credentials_rejected, human_check, portal_refused, login_page_changed, unknown '
                    '— decided from STRUCTURE (is the form still on screen) and VISIBLE text '
                    '(document.body.innerText, or visible_from_html for a driver with no browser), '
                    'never markup. The evidence arguments are keyword-only with NO default, so a '
                    'caller that has not looked at the page cannot get an answer; '
                    'is_credential_problem is an ALLOWLIST, so an outcome nobody has ruled on stays '
                    'quiet about the password rather than blaming it. A challenge or refusal is '
                    'believed only when the form is ABSENT — §COMMCALC-50.8\'s rule applied '
                    'here, not copied here. EXCUSED SIBLING, pinned shrink-only: router.py\'s '
                    'MAILBOX sweep says "login rejected" for an IMAP [AUTHENTICATIONFAILED], which '
                    'is a protocol answer with no page to misread.',
        "homes": ('app/modules/commcalc/login_outcome.py',),
        "index": ('COMMCALC-52',),
        "locks": ('harness_login_outcome.py',),
        # SNAPSHOT — written BY HAND (§50). All three portal drivers dereference it for the NAME and
        # for the operator sentence; each still reads its own page its own way, because what the
        # facts are read OFF differs per driver (a browser frame, an HTTP document, an error region)
        # and only the decision is shared.
        "callers": {
            'app/modules/commcalc/dlar_sweep.py': ('_lo',),
            'app/modules/commcalc/epay_sweep.py': ('_lo',),
            'app/modules/commcalc/vidapay_sweep.py': ('_lo',),
        },
    },
    # ── §COMMCALC-53 — what day is it where this portal lives ────────────────────────────────────
    'tenant_today': {
        "question": 'A feed window, an "as of" stamp, a staleness age — WHAT DAY IS IT, for this '
                    'tenant? Three portal drivers resolved it against the CONTAINER\'s clock '
                    '(datetime.now(timezone.utc).date()), which is not any tenant\'s calendar. '
                    'Measured 2026-10-31: the comprehensive comp report has never once arrived '
                    'automatically since the leg was built — not one `epay auto-sweep` row in '
                    'commcalc.upload_log, for any org. Its slot is 23:30 in the tenant\'s zone, '
                    'deliberately, because the portal posts a day\'s compensation at about 23:30 '
                    'local; 23:30 America/New_York is 03:30 UTC THE NEXT DAY, so on eight '
                    'consecutive recorded runs the window asked for a day one ahead of the tenant\'s '
                    'and the portal answered zero rows — read as "no compensation posted", for four '
                    'months. The rule already had a home, written 2026-07-30 for a scheduled send, '
                    'whose docstring states this exact defect ("resolving a relative date off the '
                    'server\'s UTC day would put an 8pm-Eastern Friday run on Saturday\'s date"); '
                    'nothing in commcalc read it. §19.18\'s shape for the fifth time. Each driver '
                    'now BINDS that function (feed_today IS business_today — the same object, so the '
                    'lock asserts identity and no second zoneinfo ladder can appear), and `tz` is '
                    'keyword-only with NO default at every window-building entry point, because the '
                    'defect was a caller that had never asked the org what day it was and still got '
                    'an answer. The zone comes from the connector\'s own `*_sweep_config.timezone` '
                    'row, a column that already exists NOT NULL on every sweep config table, so '
                    'nothing had to be added to the schema. '
                    'EXCUSED SIBLING, pinned shrink-only: feed_period.month_state defaults `today` '
                    'to the container\'s day, excused because it is MONTH grain (the two clocks can '
                    'disagree only either side of midnight on the 1st) and `today` is already '
                    'injectable there, so a caller that cares passes the tenant\'s day.',
        "homes": ('app/modules/notify/report_filters.py',),
        "index": ('COMMCALC-53',),
        "locks": ('harness_feed_window_tenant_day.py',),
        # SNAPSHOT — written BY HAND (§50). Each driver binds the home's function object rather than
        # calling through a wrapper, so the dereference is provable by identity, not by grep.
        "callers": {
            'app/modules/commcalc/b2b_sweep.py': ('_report_filters',),
            'app/modules/commcalc/epay_sweep.py': ('_report_filters',),
            'app/modules/commcalc/vip_sweep.py': ('_report_filters',),
            'app/modules/notify/attention.py': ('report_filters',),
            'app/modules/notify/finance_reports.py': ('ReportConfigError',),
            'app/modules/notify/report_registry.py': ('ReportConfigError', '_business_today', '_resolve_billing_friday', '_validate_filters'),
            'app/modules/notify/workforce_reports.py': ('_business_today',),
        },
    },
    # ── §COMMCALC-38 — what a supplier billed us ─────────────────────────────────────────────────
    'supplier_invoice_line': {
        "question": 'What did a supplier bill us, and which shipment was it for? Asked so every '
                    'charge can be checked against the sale that should have collected for it '
                    '(owner 2026-10-27). The platform already held DISTRIBUTOR invoices in '
                    'commcalc.vip_invoices, but those tables are keyed on one portal\'s internal '
                    'invoice id, carry no supplier and no source column, and are read by the P&L, '
                    'the balance sheet, device COGS and the Distributor Invoices report — so a '
                    'second supplier landed in them would be indistinguishable from the first and '
                    'would make every one of those readers wrong, silently, on money. This home '
                    'names no supplier. vip_invoices is the EXCUSED SIBLING that should fold into '
                    'it later, with the reason recorded in index COMMCALC-38 rather than left for '
                    'a reader to notice. The two recon references are kept verbatim AND normalized '
                    'by the one rule, because the supplier and the POS print a tracking number '
                    'differently and two rules would make one shipment read as unbilled on one side '
                    'and uncollected on the other.',
        "homes": ('app/modules/commcalc/supplier_invoice_feed.py',
                  'app/modules/commcalc/supplier_invoice_sweep.py'),
        "index": ('COMMCALC-38',),
        "locks": ('harness_supplier_invoice_feed.py',),
        "callers": {
            'app/modules/commcalc/connector_registry.py': ('SUPPLIER_INVOICE_CONNECTOR',),
            'app/modules/commcalc/router.py': ('_SUPPLIER_INVOICE_CONNECTOR', 'sis'),
        },
    },
    'pos_report_declared_metric': {
        "question": 'What does an external POS report line MEAN in this tenant\'s own declared '
                    'vocabulary, and may that meaning be read as the tenant\'s word? The owner asked '
                    'for the portal\'s daily report to be checked against what the reps declare, and '
                    'the two name the same things differently. Spelling the categories in code would '
                    'break for the next tenant and is what he explicitly forbade; resolving a line to '
                    'the closest-looking category would produce a comparison that reads as evidence '
                    'and is not. So the match is a stated ladder and every row carries its BASIS: '
                    'only a declared mapping or the category\'s own label counts as the tenant\'s '
                    'word, and a line two categories could claim resolves to neither.',
        "homes": ('app/modules/commcalc/pos_report_feed.py',),
        "index": ('COMMCALC-07',),
        "locks": ('harness_pos_report_feed.py',),
        "callers": {
            'app/modules/commcalc/pos_report_sweep.py': ('feed',),
        },
    },
    # ── §4a.4 (owner decision 2026-10-19) — which POS departments are DEVICE departments ────────
    'device_department': {
        "question": 'Is this POS department a DEVICE department? Asked by the Gross Profit report '
                    '(both classification branches), by the P&L\'s sales classifier, and by the GP '
                    'Category Map screen that tells the owner what each department IS. The answer is '
                    'the org\'s OWN declared box-department set, with an explicit GP category '
                    'override allowed to beat it and the house labels as the default for an org that '
                    'declared nothing. It had TWO homes: the GP report read the declaration and the '
                    'P&L read the category overrides ALONE, so a department the owner declared but '
                    'never overrode was device revenue on one report and unbooked sales on the '
                    'other ($111,464.09 across Mar-Oct 2026). Where each side is configured: '
                    'index 4a.4.',
        "homes": ('app/modules/commcalc/gp_report.py',),
        "index": ('4a.4',),
        "locks": ('harness_gp_pnl_commission_parity.py',),
        # SNAPSHOT — written BY HAND, never blessed (§50: `--bless` silently deleted 11 of 12 facts
        # on 2026-10-04).
        #
        # READ THIS LIST CORRECTLY. The home is the GP ENGINE module, which also owns the VOID-TOKEN
        # vocabulary (`VOID_TOKENS` / `is_voided` / `countable_sale_skip_reason`) that half the
        # commcalc package dereferences. The derivation is per-MODULE, so every importer of that
        # engine lands here whatever it reads. The bindings that read THIS fact are
        # `device_department_test` and `_dept_classifier`; a caller binding only a void-token name
        # is listed because it imports the home, not because it classifies a department. What the
        # A-check buys even so: a NEW importer cannot land silently, and the next author is told
        # that this file answers the device-department question before they touch it.
        "callers": {
            'app/modules/account/coa.py': ('device_department_test',),
            'app/modules/commcalc/calculator.py': ('_VOID_TOKENS', '_is_voided'),
            'app/modules/commcalc/commission_drilldown.py': ('_is_voided',),
            'app/modules/commcalc/commission_engine.py': ('_VOID_TOKENS', '_is_voided'),
            'app/modules/commcalc/flags.py': ('_is_voided',),
            'app/modules/commcalc/ma_recon.py': ('_is_voided',),
            'app/modules/commcalc/plan_impact.py': ('_is_voided',),
            'app/modules/commcalc/router.py': ('DEVICE_DEPTS', 'GP_CATEGORIES', 'ONDIGO_DEPT', '_GP_CATEGORIES_SET', '_GP_CATS_DEFAULT', '_GP_VOID_TOKENS', '_dept_classifier', '_gp', '_gp_is_voided', '_gp_skip_reason', '_ik', 'bucket_map', 'calc_gp_report', 'device_department_test'),
            'app/modules/commcalc/sale_installment_engine.py': ('_VOID_TOKENS', '_is_voided'),
            'app/modules/commcalc/tax_collected.py': ('VOID_TOKENS', 'is_voided', 'safe_float'),
            'app/modules/commcalc/void_watchdog.py': ('_skip',),
            'app/modules/commcalc/watchdog_router.py': ('is_voided',),
            'app/modules/marketing/event_sales.py': ('is_voided',),
        },
    },
    # ── §37.11 (owner ruling 2026-10-20) — which FIGURE does a mapped sale line book ─────────────
    'sale_line_booking_basis': {
        "question": 'A POS sale line no P&L classifier claims is routed to a revenue line by the '
                    'org own rule map. WHICH FIGURE does that rule book, and does the line carry '
                    'money at all? Both answers used to be one word, ext_price, written straight '
                    'into the P&L sales scan: so a wallet refill would have booked the customer '
                    'pass-through money as income, a plan activation (which rings no gross at all, '
                    'because the customer pays the carrier) could not be booked or even reported, '
                    'and the blank department the unbooked report prints as (blank) was the one '
                    'department a rule could not name. The owner ruling of 2026-10-20 is that '
                    'commission and margin coming in ARE income, and only money reimbursed toward '
                    'phone cost is a subsidy, so the figure is a per-rule BASIS: ext (the gross) '
                    'or gp (the store margin). Where it is configured: index 37.11.',
        "homes": ('app/modules/account/sales_line_map.py',),
        "index": ('37.11',),
        "locks": ('harness_royalty_lock.py',),
        # SNAPSHOT — written BY HAND, never blessed (§50: `--bless` silently deleted 11 of 12 facts
        # on 2026-10-04).
        "callers": {
            'app/modules/account/coa.py': ('_slm',),
            'app/modules/account/router.py': ('sales_line_map',),
        },
    },
    # ── §COMMCALC-03 (owner directive 2026-10-12) — evaluate a store we do not own yet ───────────
    'multi_handset_one_buyer': {
        "question": 'Which buyers took more handsets in one day at one store than a family plausibly '
                    'would — counted ACROSS the receipts the sale was split over, from the LIVE '
                    'daily sales feed rather than anything uploaded for an evaluation?',
        "homes": ('app/modules/commcalc/multi_handset_day.py',),
        "index": ('COMMCALC-05',),
        "locks": ('harness_multi_handset_day.py',),
        # SNAPSHOT — written BY HAND, never blessed (§50).
        #
        # THE SIBLING, NAMED SO NOBODY MERGES THEM. `commcalc/prospect_eval.py` (COMMCALC-03) asks
        # the SAME question of an UPLOADED carrier statement for a store we do not own. The two are
        # deliberately separate: that one reads `prospect_feed_row` and the carrier's own account
        # column weeks after the fact, this one reads the live POS feed the morning after, and
        # neither feed can answer for the other. The owner drew the line himself ("this would check
        # the flags for ongoing activation not the test data uploaded for evaluation").
        #
        # WHAT IS NOT HERE: no severity, area or label of its own (`flag_registry` owns all three),
        # no threshold (a `watchdog_rule` config row), and no idea of its own about which line is a
        # handset — the per-org `box_departments` set is INJECTED by the caller, the same membership
        # test `router._sales_cell_agg` counts boxes with. §G of the lock fails the build if this
        # module reads that config itself or defaults it to the house labels.
        "callers": {
            'app/modules/commcalc/watchdog_router.py': ('_handsets',),
        },
    },
    'store_takeover_evaluation': {
        "question": 'What would a store we are considering taking over be worth, from its own MA '
                    'commission / daily-TX / handset-order uploads against OUR average store '
                    'expenses with ITS actual rent — and which of its subscriber accounts carry '
                    'more lines than one month should?',
        "homes": ('app/modules/commcalc/prospect_eval.py',),
        "index": ('COMMCALC-03', 'COMMCALC-03.26', 'COMMCALC-03.27', 'COMMCALC-03.28'),
        "locks": ('harness_prospect_eval.py', 'harness_prospect_pl_trend.py',
                  'harness_upload_trace_coverage.py'),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11
        # of 12 facts on 2026-10-04). The lock verifies it against the real import graph.
        #
        # NOTE WHAT IS NOT HERE. This home re-derives no booking rule: the evaluation books through
        # `account/ma_store_pnl` (`ma_commission_bookings` / `ma_tx_bookings` / `rebate_route`),
        # `account/device_cogs` (`ma_unit_price_from_rows` / `ma_sold_cost_from_rows`),
        # `account/coa` (`PL_SPEC` / `PL_SECTION` / `route_expense_line`), `account/engine._assemble`,
        # `account/analysis.pl_totals`, `commcalc/expenses_effective` and `commcalc/column_mapping` —
        # injected by `router._prospect_homes()` so the prospect P&L and the company P&L cannot
        # disagree. §D of the lock fails the build if this module grows a rule of its own.
        #
        # §COMMCALC-03.26 / .27 ADDED TWO MORE DEREFERENCES, NOT TWO MORE RULES:
        #   · the seller's declared accessory sale books its COST at `account/coa`'s own
        #     `account_config.accessory_cogs_pct` (mig 611, the rate the owner set to 20% on
        #     2026-06-20), injected — §X2 of the lock fails the build on a literal rate here;
        #   · the M1 / M2-M12 split reads `account/ma_store_pnl.ma_earned_month_ladder` and
        #     `ma_received_month_ladder`, with `commcalc/commission_legs` saying which rung is
        #     trailing and `commcalc/commission_ledger.LEG_LABELS` saying what the buckets are
        #     called. §X15 fails the build if this module parses a month out of a label itself.
        # The per-store rent is the SAME `apply_rent_override` §COMMCALC-03.6 shipped, now applied
        # once per column instead of once per prospect; §X6 holds that exactly one call site exists.
        #
        # §COMMCALC-03.28 ADDED THREE ONE-HOMES AND NO SECOND DERIVATION. The month-over-month P&L is
        # `evaluate` + `router._prospect_assemble` called once per month — the SAME booking path and
        # the SAME assembler the month report uses, which §Z of the lock proves by requiring a
        # column and that month's own report to agree on every line. `feeds_for_months` (which rows
        # belong to a month, off the registry's `period_scope`), `month_has_ended` (which
        # `default_period` now dereferences too) and `expense_window` (which of OUR months the
        # benchmark is taken over, nearest-first in either direction) are the three, and §Y/§Z fail
        # the build if a caller stops dereferencing any of them.
        "callers": {
            'app/modules/commcalc/router.py': ('_pev',),
        },
    },
    'leg_store_trend_shape': {
        "question": 'For each STORE, what did every month-of-life rung (M1..M12) of received '
                    'commission pay in each month of the window — and which rung is running down?',
        "homes": ('app/modules/commcalc/commission_leg_store_trend.py',),
        "index": ('COMMCALC-12',),
        "locks": ('harness_commission_leg_store_trend.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11 of
        # 12 facts on 2026-10-04). The lock verifies it against the real import graph.
        #
        # NOTE WHAT IS NOT HERE. This home computes NO money. It is a PIVOT of the money
        # `router._commission_leg_trend_compute` already produced — the same read, the same carrier
        # classification (§58/§66), the same store identity (§64) — reshaped store-major with the
        # months side by side. §A of the lock fails the build if Σ(per-store) ever stops equalling the
        # company figure to the cent, which is the whole claim that this is a shape and not a sibling
        # derivation of the leg trend.
        #
        # THE RUNG CONVENTION IS `commcalc/commission_legs`'s, dereferenced: `ladder_key`,
        # `ladder_sort_key`, `months_present`, `ladder_column`, `ladder_label` and the Unlabelled
        # bucket. §F of the lock fails the build on a `range(` or a month COUNT here, so a rung the
        # feed carries can never be invisible — an M13 is a column the day it arrives.
        #
        # TWO CALLERS, ONE PIVOT (added 2026-10-16, §COMMCALC-15). The owner asked for the same
        # question of a store we do NOT own yet — "a seaprate block with m2-m12 for each store with
        # filters for all month and all stores" — and the live endpoint cannot answer it, because it
        # reads our own carrier feed and may never read a prospect's uploads (§COMMCALC-03.2's AST
        # lock). So `prospect_eval.commission_leg_store_months` builds the per-(month, store) cells
        # out of the two ladder one-homes and hands them to THIS pivot: the same rows, cells, deltas,
        # worst-rung read-out, rung columns and partial-month rule. §W9 of
        # `harness_prospect_eval.py` fails the build if that caller starts deriving any of them
        # itself, which is what a second block would have been.
        "callers": {
            'app/modules/commcalc/prospect_eval.py': ('_lst',),
            'app/modules/commcalc/router.py': ('_leg_store_pivot',),
        },
    },
    'prospect_feed_tally': {
        "question": 'How many rows has each store being evaluated got, per uploaded feed, per '
                    'month — and has that even been counted yet?',
        "homes": ('app/modules/commcalc/prospect_feed_tally.py',),
        "index": ('COMMCALC-14',),
        "locks": ('harness_prospect_feed_tally.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11 of
        # 12 facts on 2026-10-04). The lock verifies it against the real import graph.
        #
        # TWO READINGS, ONE SHAPE, ONE ACCUMULATOR. `from_grouped` takes counts the DATABASE grouped
        # (migration 1085) and `from_rows` counts the rows here; both reach the shape through `_add`,
        # and §A of the lock fails the build if they ever disagree on equivalent input. The fast
        # reading is preferred and the slow one SAYS it was used, so a slow answer is never mistaken
        # for a fast one.
        #
        # WHY IT EXISTS AT ALL. The evaluations list used to carry this tally inline and compute it by
        # shipping every feed row here — 202,865 rows, 203 round trips, ~59 s on the acting org — so
        # the two-row answer the owner asked for was withheld for a minute and the list read as empty
        # (owner report 2026-10-16). The tally is a DECORATION and now loads beside that answer.
        #
        # UNKNOWN IS NOT ZERO. `missing_tally` is the one place "not counted yet" is distinguished
        # from "counted, and empty"; §C of the lock holds both the payload and the screen to it,
        # because printing 0 over 137,374 uploaded rows is the same wrongness in a cell.
        # The binding is the import alias, DERIVED from the AST import graph like every other entry
        # here (§50) — `router._prospect_tally_read` is the one function that uses it.
        "callers": {
            'app/modules/commcalc/router.py': ('_pft',),
        },
    },
    'peer_traffic_band': {
        "question": 'Which stores see comparable FOOT TRAFFIC (bill-payment visits), and how far is '
                    'each one behind the best and the median of its own band?',
        "homes": ('app/modules/commcalc/peer_comparison.py',),
        "index": ('59',),
        "locks": ('harness_peer_comparison.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11 of
        # 12 facts on 2026-10-04). The lock verifies it against the real import graph.
        "callers": {
            'app/modules/commcalc/product_mix.py': ('_pc',),
            'app/modules/commcalc/router.py': ('_peercmp',),
            'app/modules/commcalc/sales_outcome_drilldown.py': ('_peer',),
            'app/modules/commcalc/spiff_impact.py': ('_pc',),
        },
    },
    'device_price_band': {
        "question": 'What price band did the CUSTOMER pay for this device, which model was it, and is '
                    'this rep low on accessory $ per box AND on port-ins at the same time?',
        "homes": ('app/modules/commcalc/product_mix.py',),
        "index": ('62', 'COMMCALC-32'),
        "locks": ('harness_product_mix.py', 'harness_payables_device_rows.py'),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11 of
        # 12 facts on 2026-10-04). The lock verifies it against the real import graph.
        "callers": {
            'app/modules/commcalc/prospect_eval.py': ('_pm',),
            'app/modules/commcalc/router.py': ('_pmix',),
            'app/modules/commcalc/sales_outcome_drilldown.py': ('_pmix',),
            'app/modules/payables/engine.py': ('_pm',),
        },
    },
    'device_recovery_amount': {
        "question": 'How much has the carrier ALREADY recovered against this device - deducted on '
                    'its own statement or reimbursed - and does anything actually MEASURE that, or '
                    'is the answer unknown?',
        "homes": ('app/modules/payables/recovered_amount.py',),
        "index": ('COMMCALC-33',),
        "locks": ('harness_payables_recovered_amount.py',),
        # SNAPSHOT — written BY HAND, never blessed (§50: `--bless` silently deleted 11 of 12 facts
        # on 2026-10-04, and rewrote 51 of them on 2026-10-26). The lock verifies it against the
        # real import graph.
        "callers": {
            'app/modules/payables/engine.py': ('_rec',),
            'app/modules/payables/unsold_inventory.py': ('_rec',),
        },
    },
    'payable_billing_period': {
        "question": 'Which BILLING PERIOD does this dated payable money fall in, and WHAT SAID SO - '
                    'a period the carrier stamped, a declared weekly rhythm, or no rhythm at all?',
        "homes": ('app/modules/payables/billing_period.py',),
        "index": ('COMMCALC-34',),
        "locks": ('harness_billing_period.py',),
        # SNAPSHOT — written BY HAND, never blessed (§50: `--bless` silently deleted 11 of 12 facts
        # on 2026-10-04, and rewrote 51 of them on 2026-10-26). The lock verifies it against the
        # real import graph.
        "callers": {
            'app/modules/asset/router.py': ('_bp',),
        },
    },
    'payable_dated_owed': {
        "question": 'What is OWED, on which date, at which grain, and which source said so - the '
                    'per-device payable ledger or the processor\'s own dated feed?',
        "homes": ('app/modules/payables/owed_read.py',),
        "index": ('COMMCALC-34',),
        "locks": ('harness_billing_period.py',),
        # SNAPSHOT — written BY HAND, never blessed (§50: `--bless` silently deleted 11 of 12 facts
        # on 2026-10-04, and rewrote 51 of them on 2026-10-26). The lock verifies it against the
        # real import graph.
        "callers": {
            'app/modules/asset/router.py': ('_owed_r',),
            'app/modules/payables/router.py': ('_owed',),
        },
    },
    'kpi_column_meaning': {
        "question": 'Which column carries which KPI actual at each grain, and what does the carrier\'s '
                    'port-in rate MEAN \u2014 which direction is good and what scale is it on?',
        "homes": ('app/modules/commcalc/kpi_failing.py',),
        "index": ('19.28', '62'),
        "locks": ('harness_kpi_registry_lock.py', 'harness_product_mix.py'),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11 of
        # 12 facts on 2026-10-04). The lock verifies it against the real import graph.
        #
        # The port-in half was added in §62 after the only reader of `raw_dlar_store.port_pct` on the
        # platform was found to have BOTH its direction and its scale wrong — it called the carrier's
        # port-INS a port-out rate and multiplied an already-percent value by 100, so the flag fired on
        # every store with a single port-in. `harness_product_mix.py` §J1 fails the build if any module
        # but this home reads that column again.
        "callers": {
            'app/modules/commcalc/calculator.py': ('_kpi_failing',),
            'app/modules/commcalc/dlar_vs_platform.py': ('_kf',),
            'app/modules/commcalc/flags.py': ('_kpi',),
            'app/modules/commcalc/payout_structure.py': ('_kpi',),
            'app/modules/commcalc/performance_coaching_drilldown.py': ('_kpif',),
            'app/modules/commcalc/router.py': ('_kpi_failing', '_kpif'),
        },
    },
    'spiff_store_impact': {
        "question": 'What is ONE carrier pay type worth to a store\'s commission payout revenue and '
                    'to its net profit, and which stores are not earning it on the sales they make?',
        "homes": ('app/modules/commcalc/spiff_impact.py',),
        "index": ('60',),
        "locks": ('harness_spiff_impact.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11 of
        # 12 facts on 2026-10-04). The lock verifies it against the real import graph.
        "callers": {
            'app/modules/commcalc/router.py': ('_spiffimp',),
            'app/modules/commcalc/sales_outcome_drilldown.py': ('_spiff',),
        },
    },
    'accessory_target_allocation': {
        "question": 'Given ONE company accessory sales goal, what target should each store carry, '
                    'derived from its own accessories-per-box history against the boxes it sells?',
        "homes": ('app/modules/commcalc/accessory_target_plan.py',),
        "index": ('61',),
        "locks": ('harness_accessory_target_plan.py', 'harness_batch15_drilldown.py'),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11 of
        # 12 facts on 2026-10-04). The lock verifies it against the real import graph.
        "callers": {
            'app/modules/commcalc/router.py': ('_accplan',),
        },
    },
    'manager_report_card': {
        "question": 'What was assigned to this district manager for each of their stores, did the '
                    'system record it as met, and who is accountable one level above them?',
        "homes": ('app/modules/commcalc/manager_report_card.py',),
        "index": ('59.9',),
        "locks": ('harness_manager_report_card.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11 of
        # 12 facts on 2026-10-04). The lock verifies it against the real import graph.
        "callers": {
            'app/modules/commcalc/router.py': ('_mrcard',),
        },
    },
    'device_reimbursement_paid_vs_claimed': {
        "question": 'What did the carrier actually PAY for device financing, what does the '
                    'distributor CLAIM it reimbursed, and may the two be compared at all? At '
                    'STORE-MONTH grain from the statement, and at DEVICE grain from the carrier\'s '
                    'own per-line feed — including WHICH STORE was paid for a device, because the '
                    'distributor books the claim to the store it was stocked to and the carrier '
                    'pays the store it was activated at. The COST side dereferences that same '
                    'device verdict (fact `device_cost_store`, SS69) rather than deriving its own.',
        "homes": ('app/modules/commcalc/device_reimb_recon.py',),
        "index": ('19.53', '65'),
        "locks": ('harness_device_reimb_recon.py',
                  'harness_device_line_reimbursement.py',
                  'harness_batch16_drilldown.py'),
        # SNAPSHOT — written BY HAND, multi-line, not blessed. `--bless` silently deleted 11 of 12
        # facts on 2026-10-04, so this entry is maintained here and the lock verifies it against the
        # real import graph rather than regenerating it.
        "callers": {
            'app/modules/account/device_cogs.py': ('_drr',),
            'app/modules/commcalc/carrier_device_money.py': ('_drr',),
            'app/modules/commcalc/device_financing_drilldown.py': ('_drr',),
            'app/modules/commcalc/router.py': ('_drr',),
        },
    },
    # ══════════════════════════════════════════════════════════════════════════════════════════
    # Factored out of `router._device_line_reimb_run` on 2026-10-28 when §COMMCALC-43's chargeback
    # amount needed the same figure. It is NOT a sibling of the fact above: that one owns the
    # VERDICT (paid here, paid elsewhere, never paid, not measured) and this one owns only the
    # DOLLARS against one device and the I/O that binds the §58/§66/§27 questions to them. Two
    # reports read this; both read the verdict fact through it.
    'carrier_device_money': {
        "question": 'How much device-financing money has the carrier already paid against THIS '
                    'device? It binds the org\'s own declarations to §58\'s classification, asks '
                    '§66 whether a classification is evidence at all, asks §27 how long either '
                    'side of the event the money may arrive, reads the per-line feed paged, and '
                    'folds it per device through §65\'s pure `carrier_line_side`. It answers None '
                    'and never 0.00 for a device the feed carries no row for, because a rebate '
                    'lands months after the sale and a confident zero there is the fake-zero '
                    'class.',
        "homes": ('app/modules/commcalc/carrier_device_money.py',),
        "index": ('COMMCALC-44',),
        "locks": ('harness_carrier_device_money.py',
                  'harness_device_line_reimbursement.py'),
        # SNAPSHOT — written BY HAND, multi-line, not blessed (see the note on the fact above).
        "callers": {
            'app/modules/commcalc/byod_upgrade_drilldown.py': ('_cdm',),
            'app/modules/commcalc/router.py': ('_cdm',),
            'app/modules/commcalc/watchdog_router.py': ('_cdm',),
        },
    },
    # ── §COMMCALC-49 (owner ask 2026-10-29) — HOW MANY OF THIS WILL SELL NEXT ───────────────────
    'demand_projection': {
        "question": 'Given the units of something sold inside a window, how many will sell over the '
                    'next horizon — and which sale line sold a DEVICE at all? Both facts were '
                    'written TWICE before this home existed, and the second copy of each shipped '
                    'with a docstring saying it was a copy (the import-cycle excuse). The home '
                    'imports NOTHING, so that excuse cannot be revived. Velocity is per window-UNIT '
                    'and the horizon is in the same unit, so a caller counting days and a caller '
                    'counting months share one arithmetic without a hidden second divisor; the '
                    'banker\'s rounding both live callers were already ordering phones on is '
                    'preserved to the digit. A window of no days is reported as unmeasurable, never '
                    'as a projection of zero. SECOND HOME, added 2026-10-30 after the owner '
                    'found all three callers blind to a whole tenant: which DEPARTMENTS an org '
                    'calls a device is the org\'s own declaration '
                    '(`accessory_config.box_departments` — the list the box count, the '
                    'product mix and the GP report already read), asked through '
                    '`device_vocabulary.declared_device_departments`. It had been an OPTIONAL '
                    'default holding one tenant\'s POS labels, so a month of another tenant\'s '
                    'handsets read as zero. The vocabulary is now a REQUIRED argument: a caller '
                    'that has not asked the org cannot answer at all.',
        "homes": ('app/modules/commcalc/demand_projection.py',
                  'app/modules/commcalc/device_vocabulary.py'),
        "index": ('COMMCALC-49',),
        "locks": ('harness_demand_projection.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11
        # of 12 facts on 2026-10-04, and it strips exactly these reasons).
        "callers": {
            'app/modules/asset/purchase_orders.py': ('_devvocab', 'demand_projection'),
            'app/modules/commcalc/accessory_order_plan.py': ('_dp',),
            'app/modules/commcalc/router.py': ('_devvocab', '_dp'),
            'app/modules/payables/router.py': ('_devvocab', 'demand_projection'),
        },
    },
    'device_model_family': {
        "question": 'WHICH SOLD LINES ARE THE SAME PHONE, for the purpose of ordering accessories? A '
                    'case fits a phone, not a colour of it, so five colours of one handset are one '
                    'order row — while a Pro is a different phone and must stay one. The capacity is '
                    'dropped by PATTERN (a number followed by a storage unit is a format marker, like '
                    'the CellPhone category); the COLOUR is dropped only if the ORG HAS DECLARED THAT '
                    'WORD, because a colour word is a tenant\'s own spelling and CLAUDE.md bans a '
                    'house default that is really one tenant\'s vocabulary. So `colour_tokens` is a '
                    'REQUIRED keyword argument with nothing behind it, an org that has declared none '
                    'gets no grouping and the surface SAYS so, and a curated '
                    '`device_model_alias.canonical_model` outranks the derivation. The folded row '
                    'carries the spellings that went into it, so the drill-down costs no second '
                    'query. A DEVICE CONDITION is read the same way and applied AFTER the alias '
                    '(owner 2026-10-31: a pre-owned handset is a different device), which is the '
                    'only order that cannot lose the marker — the router used to canonicalise '
                    'first and silently merged the two.',
        "homes": ('app/modules/commcalc/device_model_family.py',),
        "index": ('COMMCALC-49.11', 'POS-18'),
        "locks": ('harness_accessory_order_grid.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50).
        "callers": {
            'app/modules/commcalc/router.py': ('_dmf',),
        },
    },
    'device_condition_vocabulary': {
        "question": 'WHICH DEVICE CONDITIONS EXIST, and what does THIS feed call one? Two different '
                    'questions, split on CLAUDE.md\'s line: the four conditions are the '
                    'platform\'s POLICY (the same for every tenant, and the same four the schema '
                    'has always allowed on `pos.inventory_serial`, pinned key by key), while '
                    'the WORDS a product description marks one with are the TENANT\'s vocabulary '
                    '(`CPO` in one feed, `CERTIFIED_PREOWNED` in another) and may only be declared '
                    '— a REQUIRED argument with no house fallback, so an org that has declared none '
                    'identifies no condition and the surface says so rather than reading a '
                    'refurbished handset as new. An UNMARKED line is the house default with '
                    '`basis=unmarked`, which keeps an inference apart from a reading. Both lists '
                    'were written twice before this (a schema constraint and a literal array on the '
                    'inventory screen) and the accessory grid kept a pre-owned handset off the new '
                    'one\'s row only because the word happened to survive a parse.',
        "homes": ('app/modules/pos/device_condition.py',),
        "index": ('POS-18',),
        "locks": ('harness_accessory_order_grid.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50).
        "callers": {
            'app/modules/commcalc/device_model_family.py': ('_cond',),
            'app/modules/commcalc/router.py': ('_cond',),
            'app/modules/pos/router.py': ('_devcond',),
        },
    },
    'accessory_order_catalog': {
        "question": 'WHAT KINDS OF ACCESSORY CAN BE BOUGHT, and how is each one chosen? The '
                    'load-bearing distinction is SPLIT against ASSIGNED: a case and a protector are '
                    'bought FOR a phone and divide across styles, so they are columns per phone; a '
                    'cable, a charging cube and earphones are bought BY CONNECTOR and consolidate '
                    'across every phone that takes the same one, because the cables are common to a '
                    'lot of phones. The option lists are extensible per org, with a picture per '
                    'option; a house default is correct here because these are the generic names of '
                    'a kind of product, read against no tenant\'s feed. NOT `accessory_catalog`, '
                    'which classifies what was SOLD from an uploaded product catalog '
                    '— one word apart in the filename, two different questions, and writing this '
                    'feature overwrote that module once before the name was fixed. Also the ONE home '
                    'for the share-reading rule (percentages or fractions, decided by the total), '
                    'which the colour mix now dereferences instead of keeping its own copy.',
        "homes": ('app/modules/commcalc/accessory_order_catalog.py',),
        "index": ('COMMCALC-49.12',),
        "locks": ('harness_accessory_order_grid.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50).
        "callers": {
            'app/modules/commcalc/accessory_order_plan.py': ('_cat',),
            'app/modules/commcalc/router.py': ('_aocat',),
        },
    },
    'device_cost_store': {
        "question": 'WHICH STORE carries a handset\'s cost? The distributor ledger books it to the '
                    'store the unit was STOCKED to; the carrier pays the store it was ACTIVATED at. '
                    'Declared per org, and when the activating basis is declared the cost follows '
                    'the per-device verdict that already reconciles those two authorities — never a '
                    'second opinion formed on the cost side.',
        "homes": ('app/modules/account/device_cogs.py',),
        "index": ('70', '42.5'),
        "locks": ('harness_device_inventory_cogs.py',
                  'harness_device_line_reimbursement.py'),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11
        # of 12 facts on 2026-10-04).
        "callers": {
            'app/modules/account/balance_sheet.py': ('FEE_CATEGORIES',),
            'app/modules/account/coa.py': ('_device_cogs',),
            'app/modules/account/router.py': ('_dc', '_dccfg', '_dcscfg'),
            'app/modules/account/statement_engine.py': ('device_cogs',),
            'app/modules/commcalc/router.py': ('_dcg',),
            'app/modules/payables/engine.py': ('_cogs',),
        },
    },
    'entitlement_month_coverage': {
        "question": 'An entitlement is a COUNT OF MONTHS per store — which store-month inside a '
                    'store\'s own entitlement window never arrived? And which of those absences is '
                    'a fact about the CARRIER (the month\'s payment run arrived for other stores) '
                    'rather than about OUR INGEST (no payment of this kind reached us at all that '
                    'month) or simply undecidable (the store\'s first entitled month is not '
                    'declared, so a gap cannot be told from the entitlement running out)?',
        "homes": ('app/modules/commcalc/entitlement_coverage.py',),
        "index": ('68',),
        "locks": ('harness_mdf_entitlement_coverage.py',),
        # SNAPSHOT — written BY HAND, multi-line, not blessed. `--bless` silently deleted 11 of 12
        # facts on 2026-10-04, so this entry is maintained here and the lock verifies it against the
        # real import graph rather than regenerating it.
        "callers": {
            'app/modules/commcalc/entitlement_coverage_drilldown.py': ('_ec',),
            'app/modules/commcalc/router.py': ('_ec',),
        },
    },
    'declared_metric_catalog': {
        "question": 'What does this business DECLARE at the end of a day, what is a unit worth, and '
                    'who declared it? Asked by the closing form, the Count Fields editor and the '
                    'daily-incentive rules. ONE registry; the two physical stores '
                    '(daily_closing.counts and closing_metric_line) are folded by one reader, and a '
                    'store-level figure is never attributed to a person.',
        "homes": ('app/modules/closing/metric_catalog.py',),
        "index": ('76',),
        "locks": ('harness_metric_catalog_lock.py', 'harness_daily_incentive.py'),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11
        # of 12 facts on 2026-10-04, and on 2026-10-10 it stripped the hand-written caller comments
        # off five facts; that run was reverted).
        "callers": {
            'app/modules/closing/declared_feed_recon.py': ('_metrics',),
            'app/modules/closing/router.py': ('_metrics',),
            'app/modules/commcalc/daily_incentive.py': ('mc',),
            'app/modules/commcalc/router.py': ('_mc',),
            'app/modules/commcalc/targets_engine.py': ('_figure_unit',),
        },
    },
    'daily_incentive_shapes': {
        "question": 'What does a day\'s declared categories PAY each employee? Three SHAPES only; '
                    'every rule, category, rate, threshold and split is a config row. A gate that '
                    'cannot be verified fails closed, and a pool whose split cannot be measured is '
                    'returned unallocated rather than split evenly.',
        "homes": ('app/modules/commcalc/daily_incentive.py',),
        "index": ('76',),
        "locks": ('harness_daily_incentive.py', 'harness_metric_catalog_lock.py'),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (see the note above).
        "callers": {
            'app/modules/commcalc/router.py': ('_daily_incentive',),
        },
    },
    # ── §CORE-08 (owner report 2026-10-14) — which PERSON is this name ──────────────────────────
    'person_identity': {
        "question": 'Is this the same human? Asked wherever two systems\' people are joined: a '
                    'punch against a sale, the schedule against the POS, a closing against the '
                    'roster, a commission plan against a rep. The owner reported a store-day '
                    'listing "Simarjyot Singh, Singh, Simarjyot" — ONE man, because the who-worked '
                    'signal unioned NAME STRINGS and a union of strings has no notion of a person. '
                    'FOUR questions, named apart so they cannot be mistaken for one another: '
                    '`person_key` (comma-order-insensitive, what ~30 commcalc money call sites '
                    'already key on, deliberately unwidened), `token_key` (word order thrown away '
                    'too, the 2026-09-02 schedule-vs-POS join), `folded_token_key` (THE STRONGEST '
                    'and the one for new code: order-free AND accent-folded, so Martínez matches '
                    'Martinez) and `loosely_same_person` (a generous SCREENING rule, proven '
                    'non-transitive, so never a grouping key). Two modules each declared '
                    'themselves the home — storeops/payroll_identity for the clock feed and '
                    'commcalc/commission_engine for pay — and `closing` could reach neither '
                    'without a cycle, which is exactly how it grew a seventh rule of its own.',
        "homes": ('app/modules/core/person_identity.py',),
        "index": ('CORE-08',),
        "locks": ('harness_person_identity.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11
        # of 12 facts on 2026-10-04).
        "callers": {
            'app/modules/closing/declared_feed_recon.py': ('_person',),
            'app/modules/closing/ops_chargebacks.py': ('_person_identity',),
            'app/modules/closing/router.py': ('_person_identity',),
            'app/modules/commcalc/commission_drilldown.py': ('_person_identity',),
            'app/modules/commcalc/commission_engine.py': ('_person_identity',),
            'app/modules/commcalc/management_incentive.py': ('_person_identity',),
            'app/modules/commcalc/targets_engine.py': ('_person_identity',),
            'app/modules/storeops/payroll_approval.py': ('_pkey',),
            'app/modules/storeops/payroll_identity.py': ('_person_identity',),
        },
    },
    'store_identity': {
        "question": 'Which STORE is this string? Asked by the GP report, the commission-leg '
                    'trend/breakout, the residual report, flags 7/8, the closing store-day match, '
                    'the P&L store grain and the MA account index. The answer is the org\'s own '
                    'declared spellings; AMBIGUITY RESOLVES TO NOTHING, and money nothing can '
                    'place is reported on its own row rather than dropped.',
        "homes": ('app/modules/account/store_identity.py',),
        "index": ('64',),
        "locks": ('harness_store_identity_lock.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11
        # of 12 facts on 2026-10-04).
        "callers": {
            'app/core/scope.py': ('_fold_accents',),
            'app/modules/account/balance_sheet.py': ('_sid_fold',),
            'app/modules/account/coa.py': ('_sid',),
            'app/modules/account/device_payable.py': ('_sid_fold',),
            'app/modules/account/residual_subs.py': ('_sid',),
            'app/modules/account/store_identity_audit.py': ('_sid',),
            'app/modules/closing/router.py': ('_sid_fold', '_store_identity'),
            'app/modules/closing/unfinished_day.py': ('_sid',),
            'app/modules/commcalc/b2b_sweep.py': ('_sid_fold',),
            'app/modules/commcalc/flags.py': ('_sid',),
            'app/modules/commcalc/gp_report.py': ('_sid',),
            'app/modules/commcalc/router.py': ('_sid_pairs', '_store_identity'),
            'app/modules/commcalc/vidapay_sweep.py': ('_sid_fold',),
            'app/modules/core/person_identity.py': ('fold_accents',),
            'app/modules/payables/router.py': ('_sid',),
            'app/modules/storeops/clock_feed.py': ('_sid',),
            'app/modules/storeops/clock_variance.py': ('_sid_fold',),
            'app/modules/storeops/clockin_evidence.py': ('_sid_fold',),
        },
    },
    'carrier_dollar_component': {
        "question": 'Is this carrier dollar a commission, a spiff, a residual or a reimbursement — '
                    'and is that the ORG\'S OWN declaration or a guess the platform made? Which '
                    'P&L LINE and which GROSS-PROFIT COLUMN that component lands on is the same '
                    'fact, read twice, and lives here too (`component_line` / `gp_column`). '
                    'WHETHER THE COMPONENT IS EVIDENCE AT ALL is part of the same fact and is '
                    'answered from `basis` alone (`component_is_evidence`): a component reached on '
                    'a fallback basis is a placement of last resort, so a keyword guess never '
                    'overrides the org\'s own declaration, and what a caller DOES about an '
                    'unreliable component is the caller\'s policy, never this home\'s. AND A '
                    'DECLARATION MUST BE HONOURABLE: the component vocabulary '
                    '(`carrier_map.COMPONENTS`, dereferenced) covers every category the platform '
                    'can place, because a category it cannot express leaves a keyword guess '
                    'standing where the org\'s own word already exists — which booked $260,500.00 '
                    'of declared market-development money on the device-financing reimbursement '
                    'line. Which components ARE the device-financing side is the same fact too '
                    '(`device_reimb_line` / `device_reimb_components`), and so is what a component '
                    'is CALLED for a human (`component_label`).',
        "homes": ('app/modules/commcalc/carrier_dollar_class.py',),
        "index": ('58', '66', '69'),
        "locks": ('harness_carrier_dollar_class.py',
                  'harness_gp_carrier_class_dereference.py',
                  'harness_component_basis_evidence.py',
                  'harness_declared_category_honourable.py'),
        # SNAPSHOT — written BY HAND, multi-line, never blessed. `--bless` silently deleted 11 of 12
        # facts on 2026-10-04, so this entry is maintained here and the lock verifies it against the
        # real import graph rather than regenerating it.
        "callers": {
            'app/modules/account/coa.py': ('_cdc',),
            'app/modules/commcalc/carrier_device_money.py': ('_cdc',),
            'app/modules/commcalc/gp_report.py': ('_cdc',),
            'app/modules/commcalc/router.py': ('_cdc', '_cdc_gp', '_cdc_leg'),
            'app/modules/commcalc/sales_margin_drilldown.py': ('_cdc',),
        },
    },
    'installment_month_of_life': {
        "question": 'Which instalment month is this subscriber or sale in, and is that month PROVEN '
                    'by the row or assumed from the window the reader pulled?',
        "homes": ('app/modules/commcalc/installment_month.py',),
        "index": ('19.51',),
        "locks": ('harness_installment_month_anchor.py',),
        # SNAPSHOT — written BY HAND, multi-line, not blessed. `--bless` has silently deleted facts
        # before, so this entry is maintained here and the lock verifies it against the real import
        # graph rather than regenerating it.
        "callers": {
            'app/modules/commcalc/installment_engine.py': ('_im',),
            'app/modules/commcalc/sale_installment_engine.py': ('_im',),
        },
    },
    'finding_kind_and_severity': {
        "question": 'What kind of finding is this, how bad is it, and who reviews it?',
        "homes": ('app/modules/commcalc/flag_registry.py',),
        "index": ('52.1', '52.2'),
        "locks": ('harness_flag_registry_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/account/recon.py': ('_reg',),
            'app/modules/asset/invoice_due.py': ('_reg',),
            'app/modules/asset/router.py': ('_reg',),
            'app/modules/closing/cash_watchdog.py': ('_reg',),
            'app/modules/closing/ops_chargebacks.py': ('_reg',),
            'app/modules/commcalc/byod_upgrade_day.py': ('_reg',),
            'app/modules/commcalc/flags.py': ('_reg',),
            'app/modules/commcalc/multi_handset_day.py': ('_reg',),
            'app/modules/commcalc/portout_flags.py': ('_reg',),
            'app/modules/commcalc/router.py': ('flag_registry',),
            'app/modules/commcalc/sale_installment_engine.py': ('_reg',),
            'app/modules/commcalc/sales_recon.py': ('_reg',),
            'app/modules/commcalc/void_watchdog.py': ('_reg',),
            'app/modules/commcalc/watchdog_router.py': ('_reg',),
            'app/modules/payables/engine.py': ('_reg',),
        },
    },
    'cash_count_comparability': {
        "question": 'May a count of the whole envelope be scored against this expected figure?',
        "homes": ('app/modules/closing/billpay_netting.py',),
        "index": ('74.2', '74.3'),
        "locks": ('harness_envelope_count_basis.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/closing/pickup_actual.py': ('_bn',),
            'app/modules/closing/router.py': ('billpay_netting',),
        },
    },
    'cash_count_day_verdict': {
        "question": 'What did this store-day\'s counted envelopes come to, short or over?',
        "homes": ('app/modules/closing/pickup_actual.py',),
        "index": ('CLOSING-01.1', 'CLOSING-01.4'),
        "locks": ('harness_management_recon_expected.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`, but CHECK THE
        # DIFF: the rewriter strips hand-written comments out of these `callers` dicts (it dropped
        # 119 lines of them on 2026-10-12), so this entry was written by hand.
        "callers": {
            'app/modules/closing/billpay_pickup.py': ('_outflow',),
            'app/modules/closing/deposit_accountability.py': ('_day_variance', '_envelope_opened', '_row_variance'),
            'app/modules/closing/router.py': ('_pickup_actual',),
        },
    },
    'cash_count_correction_trail': {
        "question": 'Was this management cash figure changed, from what, and by whom?',
        "homes": ('app/modules/closing/count_revision.py',),
        "index": ('73.2', '73.3'),
        "locks": ('harness_cash_count_revision.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/closing/router.py': ('_count_revision',),
        },
    },
    'cash_figure_basis': {
        "question": 'Which cash does this figure count, and may it be compared with that one?',
        "homes": ('app/modules/closing/cash_variance.py',),
        "index": ('72.2', '72.3'),
        "locks": ('harness_cash_variance.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/closing/router.py': ('_cash_variance',),
        },
    },
    'cash_drawer_variance_owner': {
        "question": 'Which store-day carries a drawer variance nobody has ruled on?',
        "homes": ('app/modules/closing/cash_watchdog.py',),
        "index": ('52.3',),
        "locks": ('harness_cash_watchdog.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/watchdog_router.py': ('_cash',),
        },
    },
    'void_and_return_visibility': {
        "question": 'What did we void or return, and who rang it?',
        "homes": ('app/modules/commcalc/void_watchdog.py',),
        "index": ('52.4',),
        "locks": ('harness_void_watchdog.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/watchdog_router.py': ('_void',),
        },
    },
    'vendor_order_route': {
        "question": 'How does an order actually reach this vendor, and may a sweep send it?',
        "homes": ('app/modules/supply/order_transport.py',),
        "index": ('51.1',),
        "locks": ('harness_order_transport.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/storevisit/router.py': ('_ot',),
            'app/modules/supply/router.py': ('_tr',),
        },
    },
    'vendor_order_dialect': {
        "question": 'What do I say to this vendor API, and what comes back?',
        "homes": ('app/modules/supply/shopify_draft_order.py',),
        "index": ('51.2', '51.7'),
        "locks": ('harness_order_transport.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/storevisit/router.py': ('_shopify',),
            'app/modules/supply/router.py': ('_shopify',),
        },
    },
    'vendor_api_credential': {
        "question": 'What bearer token do I use for this vendor right now?',
        "homes": ('app/modules/supply/api_credential.py',),
        "index": ('51.6',),
        "locks": ('harness_order_transport.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/storevisit/router.py': ('_cred',),
            'app/modules/supply/router.py': ('_cred',),
        },
    },
    'vendor_customer_identity': {
        "question": "Who is this store on the vendor's side?",
        "homes": ('app/modules/supply/vendor_customer.py',),
        "index": ('51.8',),
        "locks": ('harness_order_transport.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/storevisit/router.py': ('_vc',),
            'app/modules/supply/order_transport.py': ('_customer',),
            'app/modules/supply/router.py': ('_vc',),
        },
    },
    'alert_send_record': {
        "question": 'Has this alert finding been carried to this address, over this channel?',
        "homes": ('app/modules/storeops/alert_log.py',),
        "index": ('15.1',),
        "locks": ('harness_alert_channel_record.py', 'harness_alert_autofix.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/closing/router.py': ('_alert_log',),
            'app/modules/notify/digest_delivery.py': ('_log',),
            'app/modules/storeops/router.py': ('_alert_log',),
        },
    },
    'send_report_identity': {
        "question": 'Which report does this send record name?',
        "homes": ('app/modules/notify/send_identity.py',),
        "index": ('15.3',),
        "locks": ('harness_notify_send_identity.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/notify/router.py': ('send_identity',),
        },
    },
    'send_failure_reason': {
        "question": 'Why did this send fail for this recipient, and what does a human do about it?',
        "homes": ('app/modules/notify/send_outcome.py',),
        "index": ('NOTIFY-01',),
        "locks": ('harness_notify_send_outcome.py',),
        # HAND-WRITTEN, per the precedent above: `--bless` strips the per-caller notes. The digest leg
        # imports it INSIDE the except branch, so the alias it binds is `_why`, not the module name.
        "callers": {
            'app/modules/notify/digest_delivery.py': ('_why',),
            'app/modules/notify/router.py': ('send_outcome',),
        },
    },
    'alert_channel_ladder': {
        "question": 'Which channels does this recipient still owe, and what did each one do?',
        "homes": ('app/modules/notify/digest_delivery.py',),
        "index": ('15.2',),
        "locks": ('harness_alert_channel_record.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/closing/router.py': ('_delivery',),
            'app/modules/commcalc/router.py': ('_delivery',),
            'app/modules/storeops/clock_variance_api.py': ('_delivery',),
            'app/modules/storeops/router.py': ('_delivery',),
            'app/modules/storevisit/router.py': ('_delivery',),
        },
    },
    'month_archive_due': {
        # §19.54 widened this ONE question rather than standing up a second home for the same
        # subject: "which days and months is this feed's window actually about" covers both when a
        # month has CLOSED (so its archive is due and may be compared) and which days that closed
        # month is OWED — plus the window a pull must ASK for so its last day arrives at all
        # (`request_window`, `month_days`). A second module for the boundary would have been a
        # second place to answer the same thing, which is the defect the graph exists to stop.
        "question": "Which days and months is this feed's window about — has the month closed so its "
                    "month-end archive is due and comparable, which days is a closed month owed, and "
                    "what window must a pull ASK for so its last day actually arrives?",
        "homes": ('app/modules/commcalc/feed_period.py', 'app/modules/commcalc/sales_recon.py'),
        "index": ('19.52', '19.54'),
        "locks": ('harness_sales_recon_basis.py', 'harness_feed_day_grain.py',
                  'harness_statement_month_coverage.py'),
        # SNAPSHOT — written BY HAND, multi-line, not blessed. `--bless` silently deleted 11 of 12
        # facts on 2026-10-04, so this entry is maintained here and the lock verifies it against the
        # real import graph rather than regenerating it.
        "callers": {
            'app/modules/commcalc/entitlement_coverage.py': ('_fp',),
            'app/modules/commcalc/epay_sweep.py': ('_feed_period',),
            'app/modules/commcalc/import_audit.py': ('_recon',),
            'app/modules/commcalc/pay_data_quality.py': ('_fp',),
            'app/modules/commcalc/router.py': ('_feed_period', 'sales_recon'),
            'app/modules/notify/report_registry.py': ('SR',),
        },
    },
    'sweep_due': {
        "question": ("Should this sweep run NOW, and why — or is the only answer anybody has a stored "
                     "clock time?"),
        "homes": ('app/modules/commcalc/sweep_due.py',),
        "index": ('COMMCALC-45',),
        "locks": ('harness_sweep_due.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/router.py': ('_sweep_due',),
        },
    },
    'sweep_verdict': {
        "question": ("Did this sweep run leave the feed whole — and does the stored status WORD say "
                     "the same thing as the status sentence beside it?"),
        "homes": ('app/modules/commcalc/dlar_sweep.py',),
        "index": ('COMMCALC-40',),
        "locks": ('harness_portal_cell.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/router.py': ('dlar_sweep',),
        },
    },
    'portal_cell': {
        "question": ("Did the source actually SEND this figure, or is this a zero we wrote down? And "
                     "which source field feeds which column?"),
        "homes": ('app/modules/commcalc/portal_cell.py',),
        "index": ('COMMCALC-16',),
        "locks": ('harness_portal_cell.py', 'harness_kpi_registry_lock.py', 'harness_kpi_vintage.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/dlar_sweep.py': ('_cell',),
        },
    },
    'payroll_hours_basis': {
        "question": 'Which hours is this person paid for on this day, and on what BASIS — a closed '
                    'punch, a human correction, a measured zero, the schedule they are ticked to be '
                    'paid from, or nothing at all? It was `actual_hours or scheduled_hours` in six '
                    'places over a column effectively nothing writes (7 of 7,740 shift rows), so the '
                    '`or` fell through to the schedule on effectively every row and no reader was '
                    'ever told. The hours and the REASON they are those hours are ONE fact: a caller '
                    'that wants the number takes the basis with it (§14y). §CORE-17.3x made that '
                    'basis a drill-down\'s provenance — which relation answered for a figure — so a '
                    'second copy of the vocabulary would be a panel naming the wrong table.',
        "homes": ('app/modules/storeops/payroll_hours_basis.py',),
        "index": ('14y',),
        "locks": ('harness_payroll_hours_basis.py', 'harness_payroll_hours_basis_lock.py'),
        # SNAPSHOT — written BY HAND (§50: `--bless` silently deleted 11 of 12 facts on 2026-10-04).
        # The eleven pre-existing callers are named here for the FIRST time, beside the drill-down
        # that needed the fact registered: batch 22's posture for `rep_pay_snapshot`, taken again.
        "callers": {
            'app/modules/account/coa.py': ('_hours_basis',),
            'app/modules/commcalc/labour_coverage.py': ('_hours_basis',),
            'app/modules/hr/router.py': ('_hours_basis',),
            'app/modules/storeops/clock_feed.py': ('_basis',),
            'app/modules/storeops/clock_variance.py': ('_basis',),
            'app/modules/storeops/clock_variance_api.py': ('_hb',),
            'app/modules/storeops/payroll_evidence.py': ('_hb',),
            'app/modules/storeops/payroll_hours_drilldown.py': ('_hb',),
            'app/modules/storeops/pto_accrual.py': ('_hours_basis',),
            'app/modules/storeops/router.py': ('_hours_basis',),
            'app/modules/storeops/salary_expense.py': ('_hb',),
            'app/modules/storeops/salary_owed.py': ('_hours_basis',),
        },
    },
    'status_detail': {
        "question": ("This status must fit a column — which part of it survives, and does the reader "
                     "know something was dropped?"),
        "homes": ('app/core/status_detail.py',),
        "index": ('CORE-18',),
        "locks": ('harness_status_detail.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/auto_calc.py': ('_status_detail',),
            'app/modules/commcalc/router.py': ('_status_detail',),
            'app/modules/commcalc/vidapay_sweep.py': ('_status_detail',),
        },
    },
    'feed_lineage': {
        "question": "Which table is the LIVE feed for this item, and which column means 'arrived'?",
        "homes": ('app/modules/commcalc/data_lineage_registry.py',),
        "index": ('19.43',),
        "locks": ('harness_data_lineage_guard.py', 'harness_feed_watchdog.py', 'harness_ingest_freshness.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/account/device_billing_drilldown.py': ('_lineage',),
            'app/modules/account/inventory_value_drilldown.py': ('_lineage',),
            'app/modules/account/liabilities_drilldown.py': ('_lineage',),
            'app/modules/account/ma_store_pnl.py': ('_MA_DAILY_TX_TABLE', '_store_col'),
            'app/modules/account/memo_recon_drilldown.py': ('_lineage',),
            'app/modules/account/statement_drilldown.py': ('_lineage',),
            'app/modules/asset/device_asset_drilldown.py': ('_lineage',),
            'app/modules/closing/envelope_drilldown.py': ('_lineage',),
            'app/modules/closing/ops_chargeback_drilldown.py': ('_lineage',),
            'app/modules/closing/router.py': ('_lineage',),
            'app/modules/commcalc/accessory_money_drilldown.py': ('_lineage',),
            'app/modules/commcalc/accessory_order_drilldown.py': ('_lin',),
            'app/modules/commcalc/activation_count_drilldown.py': ('_lineage',),
            'app/modules/commcalc/auto_calc.py': ('_lineage',),
            'app/modules/commcalc/carrier_owed_drilldown.py': ('_lineage',),
            'app/modules/commcalc/carrier_pay_drilldown.py': ('_lineage',),
            'app/modules/commcalc/derived_builds.py': ('_lineage',),
            'app/modules/commcalc/device_financing_drilldown.py': ('_lineage',),
            'app/modules/commcalc/dlar_sweep.py': ('_lineage',),
            'app/modules/commcalc/email_sweep.py': ('_lineage',),
            'app/modules/commcalc/entitlement_coverage_drilldown.py': ('_lineage',),
            'app/modules/commcalc/epay_sweep.py': ('_lin',),
            'app/modules/commcalc/exec_month_drilldown.py': ('_lineage',),
            'app/modules/commcalc/inventory_sold_recon.py': ('_dlr',),
            'app/modules/commcalc/ma_recon.py': ('SALES_DISPLAY_SOURCES',),
            'app/modules/commcalc/performance_coaching_drilldown.py': ('_lineage',),
            'app/modules/commcalc/processor_ledger.py': ('_lineage',),
            'app/modules/commcalc/processor_money_drilldown.py': ('_lineage',),
            'app/modules/commcalc/prospect_eval.py': ('MONTH_COLUMNS', 'store_account_column'),
            'app/modules/commcalc/report_pull.py': ('_lin',),
            'app/modules/commcalc/router.py': ('_dlr', '_lineage'),
            'app/modules/commcalc/sales_margin_drilldown.py': ('_lineage',),
            'app/modules/commcalc/sales_outcome_drilldown.py': ('_lineage',),
            'app/modules/commcalc/targets_drilldown.py': ('_lineage',),
            'app/modules/payables/owed_read.py': ('_lineage',),
            'app/modules/pos/inventory_integrity_router.py': ('_dlr',),
            'app/modules/storeops/payroll_hours_drilldown.py': ('_lineage',),
            'app/modules/storeops/scheduled_hours_drilldown.py': ('_lineage',),
        },
    },
    'ma_reported_income': {
        # NOTE: no migration number in this string — it is a backend string, and the §19.36
        # setup-internals lock (rightly) refuses one in a shape the customer boundary cannot
        # neutralize. The column and its migration live in `ma_store_pnl.PL_CONFIG_COLUMNS`.
        "question": ('What MA/VidaPay income does a report show, on what timing basis — asked PER '
                     'month-of-life RUNG, because each rung is reported by only one of the two '
                     'feeds — at what store grain?'),
        "homes": ('app/modules/account/ma_store_pnl.py', 'app/modules/account/residual_subs.py'),
        "index": ('4a', '4b', 'COMMCALC-17'),
        "locks": ('harness_ma_income_one_home_guard.py', 'harness_mi_residual_store_grain.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/account/coa.py': ('_MA_COMPONENTS', '_msp', '_msp_spec', '_rs', '_rs_mi', '_rs_mi_idx'),
            'app/modules/account/device_cogs.py': ('_NO_RULE_REASON', 'load_unbooked_reasons'),
            'app/modules/account/ledger_pnl.py': ('_msp',),
            'app/modules/account/router.py': ('_msp', 'residual_subs'),
            'app/modules/account/statement_engine.py': ('_msp',),
            'app/modules/commcalc/commission_received.py': ('ma_commission_components',),
            'app/modules/commcalc/comp_trend.py': ('_msp', '_rs'),
            'app/modules/commcalc/router.py': ('_MA_LEG_COMPONENTS', '_msp', '_msp_gp', '_msp_leg', '_msp_ma', '_rs_gp', '_rsub', 'load_store_index'),
            'app/modules/commcalc/whatif.py': ('residual_subs',),
            'app/modules/marketing/router.py': ('_MA_COMPONENTS',),
            'app/modules/payables/engine.py': ('_msp',),
        },
    },
    'closing_cash_era': {
        "question": 'For this daily_closing row, which columns carry the drawer and the bill-pay cash?',
        "homes": ('app/modules/closing/envelope_report.py',),
        "index": ('23m', '23p'),
        "locks": ('harness_envelope_receipt_basis.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/closing/attention_providers.py': ('_er',),
            'app/modules/closing/billpay_pickup.py': ('envelope_report',),
            'app/modules/closing/envelope_drilldown.py': ('_envelope_report',),
            'app/modules/closing/external_credit_recon.py': ('_envelope_report',),
            'app/modules/closing/pickup_actual.py': ('count_fields',),
            'app/modules/closing/router.py': ('_er', 'envelope_report_mod'),
        },
    },
    'line_class': {
        "question": 'What kind of line is this sales row, and does it count as a new activation?',
        "homes": ('app/modules/commcalc/line_class.py',),
        "index": ('6', 'COMMCALC-03.23'),
        "locks": ('harness_line_class_lock.py', 'harness_activation_event_lock.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`, but CHECK THE
        # DIFF: the rewriter strips the hand-written comments out of these `callers` dicts, so the
        # two §COMMCALC-43 entries below were added by hand.
        "callers": {
            'app/modules/asset/router.py': ('_lc',),
            'app/modules/closing/router.py': ('_lcls',),
            'app/modules/commcalc/byod_upgrade_day.py': ('_lc',),
            'app/modules/commcalc/calculator.py': ('_lc',),
            'app/modules/commcalc/commission_engine.py': ('_lc',),
            'app/modules/commcalc/ma_recon.py': ('_lc',),
            'app/modules/commcalc/payout_audience.py': ('_lc',),
            'app/modules/commcalc/payout_structure.py': ('_lc',),
            'app/modules/commcalc/plan_options.py': ('_lc',),
            'app/modules/commcalc/prospect_eval.py': ('_lc',),
            'app/modules/commcalc/router.py': ('_lc',),
            'app/modules/commcalc/sales_comparison.py': ('_lc',),
            'app/modules/commcalc/watchdog_router.py': ('_lc',),
            'app/modules/commcalc/zero_sales.py': ('_lc',),
            'app/modules/core/plan_sources.py': ('_lc',),
        },
    },
    'rep_pay_snapshot': {
        "question": 'Which relation holds the CALCULATED rep-commission snapshot — the stored '
                    'result of a commission run, as opposed to anything a carrier reported? A '
                    'figure read out of it is a figure this platform computed and saved, so a '
                    'panel over it must name THAT and never a carrier feed. The name is '
                    'deliberately not a `data_lineage_registry` constant: the snapshot is written '
                    'by this platform, not fed to it, and a second spelling in the feed registry '
                    'would be the divergence that registry exists to prevent (§CORE-17.3t).',
        "homes": ('app/modules/commcalc/carrier_pay_drilldown.py',),
        "index": ('CORE-17',),
        "locks": ('harness_batch22_drilldown.py',),
        # SNAPSHOT — written BY HAND (§50: `--bless` silently deleted 11 of 12 facts on 2026-10-04).
        "callers": {
            'app/modules/commcalc/exec_month_drilldown.py': ('_cpd',),
            'app/modules/commcalc/performance_coaching_drilldown.py': ('_cpd',),
            'app/modules/commcalc/rep_incentive_drilldown.py': ('_cpd',),
            'app/modules/commcalc/router.py': ('_cpdd',),
            'app/modules/commcalc/sales_margin_drilldown.py': ('_cpd',),
            'app/modules/commcalc/targets_drilldown.py': ('_cpd',),
        },
    },
    'activation_bucket_column': {
        "question": 'Which payload column holds each activation bucket\'s count? The bucket '
                    'vocabulary, which families are excluded from the headline activation total '
                    'and which column each bucket is written under are ONE fact: a fold writes a '
                    'bucket under a column key and a drill-down has to name that figure by the '
                    'same key, so a second copy of the mapping is a panel that silently explains '
                    'the wrong number. It was a literal map inside `commcalc/router` until '
                    '§CORE-17.3v moved it here and deleted that copy in the same commit.',
        "homes": ('app/modules/commcalc/activation_bucketing.py',),
        "index": ('CORE-17',),
        "locks": ('harness_batch22_drilldown.py',),
        # SNAPSHOT — written BY HAND (§50: `--bless` silently deleted 11 of 12 facts on 2026-10-04).
        "callers": {
            'app/modules/commcalc/activation_count_drilldown.py': ('_act',),
            'app/modules/commcalc/router.py': ('_ab', '_ab_cats', '_ab_pol', '_act_bucketing'),
        },
    },
    'boost_payout_terms': {
        "question": "What does this tenant's Boost KPI-tier configuration actually pay, and what bar "
                    "is each measure scored against?",
        "homes": ('app/modules/commcalc/boost_terms.py',),
        "index": ('6p',),
        "locks": ('harness_boost_terms_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/calculator.py': ('_boost_terms',),
            'app/modules/commcalc/payout_structure.py': ('_bt',),
            'app/modules/commcalc/router.py': ('_bt',),
        },
    },
    # ── §COMMCALC-37 (owner ask 2026-10-26) — a device's arrival on the shelf has ONE date ──────
    'device_stock_age': {
        "question": 'WHEN DID THIS DEVICE ARRIVE ON THE SHELF, HOW LONG HAS IT SAT THERE, AND IS IT '
                    'STILL THERE? `inventory_aging_device.received_date` is NULL on all 10,525 rows '
                    'house-wide (measured 2026-10-26) — the POS export carries the unit\'s AGE, not a '
                    'date — so the receipt date must be DERIVED, and three callers each derived it: '
                    'the `/inventory-recon` endpoint, the management-incentive aging gate, and '
                    '`device_history.build_aging_inventory`, which read `days_in_stock` VERBATIM and '
                    'therefore reported a July age in October for any unit whose snapshot was stale. '
                    'The age is counted to TODAY, never read off the file. "On the shelf" is `on_hand` '
                    'AND the org\'s CURRENT snapshot date, because a failed off-hand flip leaves '
                    '`on_hand=true` on a unit long gone (balance_sheet\'s ~$129k of ghosts). The '
                    'WINDOW is not decided here: it is the carrier\'s declared `sell_within_days`, '
                    'resolved by `device_aging_policy` (§COMMCALC-26) — and an unresolved device does '
                    'NOT inherit the house 60, because printing someone else\'s deadline over a rep\'s '
                    'phone is the defect that section was opened for.',
        "homes": ('app/modules/commcalc/inventory_stock_age.py',),
        "index": ('COMMCALC-37', 'COMMCALC-36'),
        "locks": ('harness_inventory_stock_age.py', 'harness_inventory_stock_age_guard.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`. Hand-written for
        # the reason recorded in §COMMCALC-01: --bless strips per-caller comments, and the three
        # collapsed callers are exactly what a future reader needs explained. Verified by running the
        # guard (not --bless) afterwards.
        "callers": {
            'app/modules/commcalc/device_history.py': ('_isa',),
            'app/modules/commcalc/router.py': ('_isa',),
        },
    },
    'trade_in_basis': {
        "question": 'Which record says a trade-in happened — the till or the carrier statement — and '
                    'how many does this rep get paid for?',
        "homes": ('app/modules/commcalc/trade_in_basis.py',),
        "index": ('COMMCALC-01',),
        "locks": ('harness_trade_in_basis.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`, but see the
        # caveat recorded in §COMMCALC-01: --bless rewrites a callers block to BARE lines and strips
        # the per-caller comments from every fact it touches, so this block is hand-written and the
        # derived edge below was verified by running the guard (not --bless) afterwards.
        "callers": {
            'app/modules/commcalc/calculator.py': ('_tib',),
        },
    },
    'attach_qualifier': {
        "question": 'Did this SALE earn the line its full declared rate, or the lesser one — and who '
                    'decides what counts toward the condition?',
        "homes": ('app/modules/commcalc/attach_qualifier.py',),
        "index": ('COMMCALC-06',),
        "locks": ('harness_attach_qualifier.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`, but see the
        # caveat recorded in §COMMCALC-01: --bless rewrites a callers block to BARE lines and strips
        # the per-caller comments from every fact it touches, so this block is hand-written and the
        # derived edge below was verified by running the guard (not --bless) afterwards.
        "callers": {
            'app/modules/commcalc/commission_engine.py': ('_aq',),
            'app/modules/commcalc/plan_options.py': ('_aq',),
            'app/modules/commcalc/router.py': ('_aqm',),
        },
    },
    'carrier_claim_value': {
        "question": 'Is this unpaid activation a CLAIM against the carrier at all, and what is it '
                    'worth?',
        "homes": ('app/modules/commcalc/carrier_claim_value.py',),
        "index": ('COMMCALC-22',),
        "locks": ('harness_carrier_claim_value.py',),
        # SNAPSHOT -- hand-written (--bless strips per-caller comments, SSCOMMCALC-01); the derived
        # edges were verified by RUNNING the guard afterwards.
        "callers": {
            'app/modules/commcalc/discrepancy_engine.py': ('ccv',),
            'app/modules/commcalc/ma_recon.py': ('ccv',),
        },
    },
    'installment_exclusion_basis': {
        "question": 'WHY did this device category pay no multi-month installment -- did somebody '
                    'decide that, or did nobody?',
        "homes": ('app/modules/commcalc/installment_category.py',),
        "index": ('COMMCALC-21',),
        "locks": ('harness_installment_exclusion_basis.py',),
        # SNAPSHOT — hand-written (--bless strips per-caller comments, §COMMCALC-01); the derived
        # edges were verified by RUNNING the guard afterwards.
        "callers": {
            'app/modules/commcalc/installment_category_payout.py': ('icat',),
            'app/modules/commcalc/line_class.py': ('_icat',),
            'app/modules/commcalc/plan_pay_gate.py': ('_icat',),
            'app/modules/commcalc/router.py': ('CATEGORY_KEYS', '_icat', '_icat_dev', 'icat'),
            'app/modules/commcalc/sale_installment_engine.py': ('icat',),
            'app/modules/commcalc/sales_comparison.py': ('_icat',),
        },
    },
    'payout_slice_evidence': {
        "question": 'Is this stored pay breakdown a figure at all, or a blank that prints as $0.00?',
        "homes": ('app/modules/commcalc/payout_slice_evidence.py',),
        "index": ('COMMCALC-09',),
        "locks": ('harness_payout_slice_evidence.py',),
        # SNAPSHOT — hand-written, not `--bless`-generated, because --bless rewrites a callers block to
        # BARE lines and strips the per-caller comments (the caveat recorded in §COMMCALC-01). The
        # derived edges were verified by RUNNING the guard afterwards.
        "callers": {
            'app/modules/commcalc/performance_coaching_drilldown.py': ('_pse',),
            'app/modules/commcalc/rep_incentive_drilldown.py': ('_pse',),
            'app/modules/commcalc/router.py': ('_pse',),
            'app/modules/commcalc/whatif.py': ('_pse',),
        },
    },
    'commission_ledger_identity': {
        "question": 'Which ledger row is this, and which period does it book into?',
        "homes": ('app/modules/commcalc/commission_ledger.py',),
        "index": ('4b',),
        "locks": ('harness_ledger_identity_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/account/coa.py': ('_cl',),
            'app/modules/account/ledger_pnl.py': ('_cl',),
            'app/modules/account/ma_store_pnl.py': ('_LEG_LABELS', 'month_leg_of'),
            'app/modules/commcalc/column_mapping.py': ('commission_ledger',),
            'app/modules/commcalc/commission_statement.py': ('CATEGORIES', 'CATEGORY_LABELS'),
            'app/modules/commcalc/ledger_batch.py': ('CL',),
            'app/modules/commcalc/ledger_ma_sync.py': ('commission_ledger',),
            'app/modules/commcalc/onboarding_intake.py': ('CL',),
            'app/modules/commcalc/router.py': ('_cled', '_month_token', 'commission_ledger'),
            'app/modules/commcalc/sale_installment_engine.py': ('month_leg_of',),
        },
    },
    'month_focus_declaration': {
        "question": 'What did management DECLARE for this month — the focus, the pay type driving its '
                    'spiffs, the temporary spiffs on the table — and what does it still owe this week?',
        "homes": ('app/modules/commcalc/month_focus.py',),
        "index": ('64',),
        "locks": ('harness_month_focus.py',),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11 of
        # 12 facts on 2026-10-04, and on 2026-10-09 it stripped the explanatory comments out of ten
        # callers dicts, so this package hand-edited the graph instead). The lock verifies it against
        # the real import graph.
        #
        # THE SIBLING THAT ANSWERS A NEIGHBOURING QUESTION: `commcalc.payout_config.custom_spiffs` is
        # what the engine actually PAYS; this fact is only an INTENT with a status. They are
        # deliberately two facts, and `month_focus.spiff_reconciliation` is the ONE place that
        # compares them — so a change to either has to keep that comparison honest rather than
        # merging the two into one.
        #
        # ONE direct caller, deliberately. The login popup's attention item
        # (`commcalc/import_audit._p_month_focus`) and the platform banner are TWO SURFACES over this
        # same due list, and both reach it through `router._month_focus_payload` rather than importing
        # this home again — so there is no second assembly to drift.
        "callers": {
            'app/modules/commcalc/router.py': ('_mfocus',),
        },
    },
    'people_visibility': {
        # Two dimensions of ONE declared fact, so they are one node: `visible_people_keyset` answers
        # whose rows are mine, `visible_store_codes` answers which stores reach me at all, and
        # `WORKED_AT_SOURCES` is the evidence registry both directions dereference (§14x). Splitting
        # them would invite exactly the drift the owner's "one fact, one home" rule forbids — a
        # manager's people list on the pins while their report moved to evidence.
        "question": 'Whose SHIFTS and whose STORES may this login read?',
        "homes": ('app/core/scope.py',),
        "index": ('14w', '14x'),
        "locks": ('harness_people_visibility_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/account/device_payable.py': ('store_market_resolver',),
            'app/modules/account/device_purchases.py': ('store_market_resolver',),
            'app/modules/account/residual_subs.py': ('_cscope',),
            'app/modules/account/router.py': ('_cscope',),
            'app/modules/account/statement_filter.py': ('core_scope',),
            'app/modules/asset/purchase_orders.py': ('_cscope',),
            'app/modules/asset/router.py': ('_cscope',),
            'app/modules/closing/closer_pick.py': ('PICK_ANY_SCOPES', 'is_market_or_wider'),
            'app/modules/closing/ops_chargebacks.py': ('_cscope',),
            'app/modules/closing/router.py': ('_cscope', '_cscope_opts'),
            'app/modules/commcalc/month_focus.py': ('is_market_or_wider',),
            'app/modules/commcalc/processor_ledger.py': ('_cscope',),
            'app/modules/commcalc/router.py': ('_core_scope', '_cscope', '_tso_omo', 'sanitize_market_label'),
            'app/modules/core/onboarding.py': ('_cscope',),
            'app/modules/core/router.py': ('_cscope', '_omo', '_scope'),
            'app/modules/payables/router.py': ('_cscope',),
            'app/modules/pos/router.py': ('_cscope', 'in_keyset'),
            'app/modules/storeops/clock_variance_api.py': ('_cscope',),
            'app/modules/storeops/overhead_allocation.py': ('_market_by_code',),
            'app/modules/storeops/payroll_approval.py': ('_cscope',),
            'app/modules/storeops/router.py': ('_cscope',),
            'app/modules/storevisit/router.py': ('_cscope',),
        },
    },
    'payout_audience': {
        "question": 'Is this surface employee-facing, and therefore what may it show?',
        "homes": ('app/modules/commcalc/payout_audience.py',),
        "index": ('6',),
        "locks": ('harness_payout_audience_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/commission_drilldown.py': ('_pa',),
            'app/modules/commcalc/commission_statement.py': ('_pa',),
            'app/modules/commcalc/router.py': ('_pa',),
            'app/modules/core/router.py': ('_pa',),
        },
    },
    'paramount_kpi': {
        "question": "What is this store's Paramount KPI score?",
        "homes": ('app/modules/commcalc/paramount_kpi.py',),
        "index": ('10',),
        "locks": ('harness_paramount_kpi_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/router.py': ('parse_paramount_mtd_kpis',),
        },
    },
    'column_tolerance': {
        "question": 'Which column of this uploaded sheet holds the value I asked for?',
        "homes": ('app/core/column_tolerant.py',),
        "index": ('2',),
        "locks": ('harness_any_columns_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/account/billpay_pl.py': ('_ct',),
            'app/modules/account/ledger_pnl.py': ('_ct',),
            'app/modules/account/ma_store_pnl.py': ('_ct',),
            'app/modules/account/residual_subs.py': ('_ct',),
            'app/modules/commcalc/carrier_dollar_class.py': ('_ct',),
            'app/modules/commcalc/commission_engine.py': ('_ct',),
            'app/modules/commcalc/dlar_sweep.py': ('_ct',),
            'app/modules/commcalc/expenses_effective.py': ('_ct',),
            'app/modules/commcalc/installment_engine.py': ('_col',),
            'app/modules/commcalc/prospect_eval.py': ('_ct',),
            'app/modules/commcalc/router.py': ('_ct',),
            'app/modules/commcalc/setup_documents.py': ('read_row',),
            'app/modules/commcalc/whatif.py': ('_ct',),
            'app/modules/pos/customer_master.py': ('_ct',),
            'app/modules/pos/inventory_integrity_router.py': ('_ct',),
            'app/modules/pos/receipt_import.py': ('_ct',),
            'app/modules/supply/store.py': ('present_columns',),
        },
    },
    'multimonth_offer': {
        "question": 'Does this org offer multi-month / installment payouts at all?',
        "homes": ('app/modules/commcalc/multimonth_config.py',),
        "index": ('8',),
        "locks": ('harness_multimonth_offer_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/router.py': ('_mmc',),
        },
    },
    'landing_identity': {
        "question": 'Which report kind does an uploaded file land as?',
        "homes": ('app/modules/commcalc/landing_identity.py',),
        "index": ('2',),
        "locks": ('harness_landing_identity_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/onboarding_intake.py': ('_li',),
            'app/modules/commcalc/router.py': ('_landing',),
        },
    },
    'data_qa_semantic_layer': {
        "question": 'Which of this platform\'s own reports answers this business question?',
        "homes": ('app/modules/core/data_qa_registry.py',),
        "index": ('52.1',),
        "locks": ('harness_data_qa_lock.py', 'harness_data_qa_registry.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/core/data_qa_agent.py': ('reg',),
            'app/modules/core/data_qa_api.py': ('reg',),
        },
    },
    'data_qa_arithmetic': {
        "question": 'What is the total, the rank, the pivot or the chart over these report rows?',
        "homes": ('app/modules/core/data_qa_compute.py',),
        "index": ('52.2',),
        "locks": ('harness_data_qa_lock.py', 'harness_data_qa_compute.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/core/data_qa_agent.py': ('calc',),
        },
    },
    'period_stored_spelling': {
        "question": 'Which spelling is this month STORED under, and which spellings must a filter match?',
        "homes": ('app/modules/account/_period.py',),
        "index": ('19.47',),
        "locks": ('harness_period_one_spelling.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/account/analysis.py': ('_period',),
            'app/modules/account/autocompute.py': ('_MONTHS', 'period_keys'),
            'app/modules/account/coa.py': ('_period', 'parse_period'),
            'app/modules/account/engine.py': ('_period',),
            'app/modules/account/finance_attention.py': ('_pkeys', 'fin_period'),
            'app/modules/account/projection_engine.py': ('_period',),
            'app/modules/account/recon.py': ('_period',),
            'app/modules/account/residual_subs.py': ('parse_period', 'recent_period_keys'),
            'app/modules/account/router.py': ('_pd', '_period', 'parse_period', 'period_keys'),
            'app/modules/account/royalty.py': ('canonical_period', 'month_range', 'parse_period'),
            'app/modules/account/royalty_router.py': ('_period',),
            'app/modules/account/statement_engine.py': ('_period',),
            'app/modules/account/statement_filter.py': ('_per',),
            'app/modules/commcalc/auto_calc.py': ('_pd',),
            'app/modules/commcalc/commission_ledger.py': ('_pd',),
            'app/modules/commcalc/discrepancy_appeals.py': ('_pd',),
            'app/modules/commcalc/labour_actuals.py': ('_pk',),
            'app/modules/commcalc/ledger_batch.py': ('_pd',),
            'app/modules/commcalc/ma_recon.py': ('_pd',),
            'app/modules/commcalc/router.py': ('_pd',),
        },
    },
    'derived_labour_actuals': {
        "question": 'What did the platform\'s OWN labour rows (salary, rep commission) actually cost '
                    'in this month, per store — as opposed to what some other month cost?',
        "homes": ('app/modules/commcalc/labour_actuals.py',),
        "index": ('COMMCALC-23',),
        "locks": ('harness_labour_actuals.py', 'harness_expense_one_path.py'),
        # SNAPSHOT — written BY HAND, never blessed (§50: `--bless` silently deleted 11 of 12 facts
        # on 2026-10-04 and still strips the per-caller comments). The lock verifies it against the
        # real import graph.
        "callers": {
            'app/modules/commcalc/router.py': ('_lact',),
        },
    },
    'statement_crosscheck_verdict': {
        "question": 'Was this statement actually crosschecked, and did it pass?',
        "homes": ('app/modules/account/analysis.py',),
        "index": ('19.49',),
        "locks": ('harness_statement_crosscheck_earned.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/account/engine.py': ('_analysis',),
            'app/modules/account/projection_engine.py': ('analysis',),
            'app/modules/account/router.py': ('analysis',),
            'app/modules/account/statement_engine.py': ('_analysis',),
            'app/modules/account/statement_filter.py': ('_analysis',),
            'app/modules/account/valuation.py': ('analysis',),
            'app/modules/commcalc/router.py': ('_an',),
        },
    },
    'store_monthly_pl': {
        "question": 'What did this store of OURS earn in this month — its revenue, its gross '
                    'profit, its bottom line, and when was that computed?',
        "homes": ('app/modules/account/statement_filter.py',),
        "index": ('19.50', 'COMMCALC-03.24'),
        "locks": ('harness_prospect_eval.py', 'harness_spiff_impact.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/account/router.py': ('statement_filter',),
            'app/modules/commcalc/router.py': ('_sf', '_sf_peer', 'resolve_store_matcher', 'unbound_spellings'),
        },
    },
    'relation_schema': {
        "question": 'WHICH SCHEMA holds this relation — or did the reader that named the table just '
                    'assume one, and read an empty table (or no table at all) instead?',
        "homes": ('app/modules/core/relation_home.py',),
        "index": ('CORE-30',),
        "locks": ('harness_relation_home_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/payout_accrual.py': ('_relation_home',),
            'app/modules/commcalc/router.py': ('_relation_home',),
            'app/modules/core/geo_retention.py': ('_rel',),
            'app/modules/storeops/clockin_evidence.py': ('_relation_home',),
        },
    },
    'complete_feed_read': {
        "question": 'Have I read ALL of this feed — every row, each exactly once — or only the '
                    'first N rows somebody typed, in whatever order the backend felt like?',
        "homes": ('app/modules/core/feed_read.py',),
        "index": ('19.48', 'CORE-05'),
        "locks": ('harness_feed_read_lock.py', 'harness_pay_feed_balance.py',
                  'harness_installment_month_anchor.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/carrier_device_money.py': ('_feed_read',),
            'app/modules/commcalc/installment_engine.py': ('_feed_read',),
            'app/modules/commcalc/router.py': ('_feed_read',),
            'app/modules/payables/engine.py': ('_feed_read',),
            'app/modules/payables/unsold_inventory.py': ('_feed_read',),
            'app/modules/payables/vendor_invoice.py': ('_feed_read',),
            'app/modules/recovery/engine.py': ('_feed_read',),
        },
    },
    'payment_category_map': {
        "question": 'Which pay CATEGORY did this org declare for this payment type — is this line '
                    'commission, a rebate, or something it has never mapped?',
        "homes": ('app/modules/commcalc/payment_category.py',),
        "index": ('57.1',),
        "locks": ('harness_payment_category_home_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/carrier_dollar_class.py': ('_pc',),
            'app/modules/commcalc/gp_report.py': ('_pc',),
            'app/modules/commcalc/processor_ledger.py': ('_pcat',),
            'app/modules/commcalc/router.py': ('_payment_category', '_pc', '_pcat'),
            'app/modules/commcalc/spiff_impact.py': ('_pc_fold',),
        },
    },
    'clawback_direction': {
        "question": 'Is this processor-feed row money the carrier TOOK BACK — and was it recognised '
                    'by the DIRECTION the money moved, or by a category name no tenant can declare?',
        "homes": ('app/modules/commcalc/clawback.py',),
        "index": ('55.2',),
        "locks": ('harness_clawback_lock.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/flags.py': ('_clawback',),
            'app/modules/commcalc/router.py': ('_cb',),
            'app/modules/commcalc/withholding_report.py': ('_cb',),
        },
    },
    'appeal_state_machine': {
        "question": 'What are the legal appeal states for money the carrier has not paid, which '
                    'transitions are allowed, and who may stamp them?',
        "homes": ('app/modules/commcalc/discrepancy_appeals.py',),
        "index": ('15.3', '55.4'),
        "locks": ('harness_appeal_one_machine_lock.py',),
        # TWO row homes, ONE machine: discrepancy_results (mig 947) and commcalc.flags (mig 1060)
        # carry byte-identical column names so `apply_appeal` patches either with no branch.
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/router.py': ('_da',),
            'app/modules/commcalc/withholding_report.py': ('APPEAL_STATES',),
        },
    },
    'pay_feed_balance': {
        "question": 'Did this pay figure account for every dollar the carrier paid, or did it '
                    'silently drop what it could not place?',
        "homes": ('app/modules/commcalc/pay_data_quality.py',),
        # §19.54 — "and does this month's statement cover every day it is owed" is the same question
        # one layer out (a dollar that never ARRIVED cannot be placed), so it lives in the same home
        # and is locked by the new harness beside the old one.
        "index": ('19.48', '19.54'),
        "locks": ('harness_pay_feed_balance.py', 'harness_statement_month_coverage.py'),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/commcalc/accessory_cost_audit.py': ('_f', 'pdq'),
            'app/modules/commcalc/clawback.py': ('PLACEABLE_CATEGORIES',),
            'app/modules/commcalc/commission_drilldown.py': ('_pdq',),
            'app/modules/commcalc/commission_engine.py': ('_pdq_cfg',),
            'app/modules/commcalc/entitlement_coverage.py': ('VERDICT_FAILED', 'VERDICT_NOT_MEASURED', 'VERDICT_PASSED'),
            'app/modules/commcalc/payment_category.py': ('PLACEABLE_CATEGORIES', 'UNCATEGORISED'),
            'app/modules/commcalc/plan_pay_gate.py': ('_pdq',),
            'app/modules/commcalc/router.py': ('_pdq',),
        },
    },
    'expense_month_one_path': {
        "question": 'What did this store spend in this month — including a month it never saved?',
        "homes": ('app/modules/commcalc/expenses_effective.py',),
        "index": ('19.50',),
        "locks": ('harness_expense_one_path.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/account/coa.py': ('effective_expense_rows',),
            'app/modules/commcalc/labour_coverage.py': ('period_sort_key',),
            'app/modules/commcalc/prospect_eval.py': ('period_sort_key',),
            'app/modules/commcalc/router.py': ('_expfx', 'effective_series'),
            'app/modules/commcalc/sales_comparison.py': ('_period_key',),
        },
    },
    'gated_detector_verdict': {
        "question": 'Did this check actually run, or is its clean result just an empty config?',
        "homes": ('app/modules/commcalc/labour_coverage.py',),
        "index": ('19.50',),
        "locks": ('harness_expense_one_path.py',),
        # SNAPSHOT — regenerate with `python3 harness_module_graph_guard.py --bless`.
        "callers": {
            'app/modules/account/coa.py': ('_lcov',),
            'app/modules/commcalc/gp_report.py': ('_lcov',),
            'app/modules/commcalc/router.py': ('_labour', '_lcov'),
        },
    },
    'target_axis': {
        "question": 'What is this tenant actually targetable ON — with what label, in what unit, and '
                    'is its target a column on commcalc.targets or a commcalc.target_line row?',
        "homes": ('app/modules/commcalc/targets_engine.py',),
        "index": ('COMMCALC-02',),
        "locks": ('harness_target_axis_lock.py', 'harness_target_declared_axis.py'),
        # SNAPSHOT — written BY HAND, multi-line, never blessed (§50: `--bless` silently deleted 11 of
        # 12 facts on 2026-10-04). The lock verifies it against the real import graph.
        "callers": {
            'app/modules/commcalc/router.py': ('_te', 'targets_engine'),
            'app/modules/commcalc/targets_drilldown.py': ('_te',),
            'app/modules/storevisit/visit_plan_router.py': ('_te',),
        },
    },
    # ── §COMMCALC-11 (owner report 2026-10-15) — a check that GATES a sign-in ────────────────────
    # ── §POS-15 (owner ask 2026-10-30) — what a till may be paid with ───────────────────────────
    'register_tender_vocabulary': {
        "question": 'WHAT MAY A TILL BE PAID WITH, which of those does THIS org offer, and what '
                    'does a sale on terms owe? It was written FOUR times: two literal button '
                    'arrays in the register screen (one for a tenant with integrated card '
                    'processing, one without), the condition that chose between them, and '
                    '`CHECK (payment_method IN …)` on `pos.sale_payments`. Adding one '
                    'tender type therefore meant a screen, a condition and a constraint, in three '
                    'languages, with the build green if any of them was missed. The class a tender '
                    'RECONCILES as is NOT answered here — each entry carries the words a feed would '
                    'print and `closing.router.tender_class` (the house vocabulary, §30.13) '
                    'answers, so a terms sale files under the existing `store_acct` and no class '
                    'was invented. A sale on terms does not settle at the till: the server '
                    're-derives `pos.sales.balance` as the sum of the legs that did NOT settle, '
                    'because `pos.checkout` stores whatever it is handed, and no receipt prints '
                    '"Paid" on it (both layouts ask `settles_now`).',
        "homes": ('app/modules/pos/tender_vocabulary.py',),
        "index": ('POS-15',),
        "locks": ('harness_pos_tender_vocabulary.py', 'harness_tender_vocab_lock.py'),
        # SNAPSHOT — written BY HAND, never blessed (§50). The lock verifies it against the real
        # import graph.
        "callers": {
            'app/modules/pos/router.py': ('_tender',),
            'app/modules/pos/sale_receipt.py': ('_tv',),
            'app/modules/pos/sale_settlement.py': ('_tender',),
        },
    },
    # ── §POS-16 (owner ask 2026-10-30) — a wholesale customer and their W-9 ─────────────────────
    'wholesale_w9_gate': {
        "question": 'MAY I SELL TO THIS CUSTOMER, AND IS THIS SALE TAXABLE? One question, because '
                    'tax-free is not a property of a customer TYPE — it is a property of a type '
                    'WITH a current exemption certificate on file. Answering the two separately '
                    'produces exactly one failure and it is the expensive one: a sale goes out '
                    'tax-free against a certificate nobody holds and the house carries the '
                    'liability. So `may_sell` answers both, no state exists where a sale is exempt '
                    'and the certificate is not current, and the sale RECORDS its basis '
                    '(`pos.sales.tax_exempt_basis`) because it cannot be re-derived later — the '
                    'certificate may be filed the next day. An admin override permits a TAXABLE '
                    'sale and never an exemption, and is named on the row. Only a PERSON can '
                    'confirm receipt: a customer\'s own upload through their return link lands '
                    '`submitted`, since an anonymous caller holding a link cannot be the user the '
                    'owner meant by "the user confirms they received the filled out w9".',
        "homes": ('app/modules/pos/wholesale_w9.py',),
        "index": ('POS-16',),
        "locks": ('harness_pos_wholesale_w9.py',),
        # SNAPSHOT — written BY HAND, never blessed (§50).
        "callers": {
            'app/modules/pos/router.py': ('_w9',),
        },
    },
    # ── §POS-16.3 (2026-10-30) — what format an uploaded document may be ────────────────────────
    'upload_document_format': {
        "question": 'WHAT FORMAT MAY AN UPLOADED DOCUMENT BE? Sniff the MAGIC BYTES, never trust '
                    'the extension, check the result against the tenant\'s allow-list. The rule '
                    'lived inside `hr/router.py` and was reachable only from the three onboarding '
                    'upload routes; a wholesale customer sending back a completed W-9 (§POS-16) '
                    'asks the identical question from another module, and a second sniffer there '
                    'is how one path comes to accept a renamed executable while the other refuses '
                    'it. A file IS what its bytes say: the filename is read ONLY to word the '
                    'refusal when the header matches nothing known, and an empty allow-list reads '
                    'as the declared default rather than as "anything goes". One refusal wording, '
                    'so two upload screens cannot differ.',
        "homes": ('app/core/upload_format.py',),
        "index": ('POS-16',),
        "locks": ('harness_pos_wholesale_w9.py',),
        # SNAPSHOT — written BY HAND, never blessed (§50).
        "callers": {
            'app/modules/hr/router.py': ('_upload_format',),
            'app/modules/pos/router.py': ('_upload_format',),
        },
    },
    # ── §POS-19 (owner ask 2026-10-31) — what a customer still owes us, and how late ───────────
    'pos_open_receivable': {
        "question": 'WHAT DOES A CUSTOMER STILL OWE US ON A SALE, AND HOW LATE IS IT? The Terms '
                    'tender (§POS-15) created the platform\'s first receivable, and the question '
                    'is NOT the one `sale_settlement` answers: that home compares two recorded '
                    'figures as of the SALE, takes no clock and must not (its lock fails the build '
                    'on a `datetime` in it), while collection is a question about TODAY against a '
                    'due date. A sale with no deferred leg is not a receivable of $0.00 — it is '
                    'not a receivable, which matters because 4,603 of 4,605 live sales carry no '
                    'payment row at all and a `balance > 0` filter would have admitted every '
                    'part-tendered cash sale as a debt. Not yet due is owed and is not late, so it '
                    'has its own state and its own total. An age that cannot be measured is '
                    '`unknown`, never the newest band. The aging ladder is '
                    '`manager_followup.AGE_BANDS` dereferenced, because a second spelling of what '
                    '"61-90 days" means would drift — and these bands are a POLICY, not a '
                    'tenant\'s own vocabulary. What this home REFUSES to do is infer a payment '
                    'from a later over-tender: taking money against an open balance is not built '
                    'yet, and that gap is carried in the payload (`COLLECTION_NOT_WIRED`) and said '
                    'on the screen rather than guessed at, which would read every collected debt '
                    'as money owed back to the customer.',
        "homes": ('app/modules/pos/receivable.py',),
        "index": ('POS-19',),
        "locks": ('harness_pos_receivable.py',),
        # SNAPSHOT — written BY HAND, never blessed (§50). The lock verifies it against the real
        # import graph.
        "callers": {
            'app/modules/pos/router.py': ('_recv',),
        },
    },
    'human_check_on_sign_in': {
        "question": 'Does a human check ("I\'m not a robot") gate THIS page\'s sign-in, and of '
                    'which kind? Recognised by whether it is PAINTED was the defect: Google\'s '
                    'documented invisible setup renders the widget `data-size="invisible"` with a '
                    '`data-callback` and posts the form only from that callback, so it is present, '
                    'armed and gating — and `is_visible()` is False. The live session read that as '
                    'no check, auto-submitted past it, and reported a CORRECT password as a wrong '
                    'one. Three states, not a bool, because the two kinds need different words to '
                    'the operator: a visible challenge is on the screen, an invisible one appears '
                    'only once Sign in is pressed. The same fact answers "why was this submit '
                    'refused while still on the login form", which has two causes that send the '
                    'operator to opposite places (`human_check_blocked_message`) — both '
                    'post-submit sites dereference it before blaming the credentials.',
        "homes": ('app/modules/commcalc/vidapay_sweep.py',),
        "index": ('COMMCALC-11',),
        "locks": ('harness_human_check_kind.py',),
        # SNAPSHOT — written BY HAND, never blessed (§50). The lock verifies it against the real
        # import graph.
        "callers": {
            'app/modules/commcalc/live_login.py': ('vp',),
            'app/modules/commcalc/merchant_portal_sweep.py': ('vp',),
            'app/modules/commcalc/portal_report_pull.py': ('vp',),
            'app/modules/commcalc/router.py': ('UnsafePortalUrlError', 'VidaPayAuthError', '_AuthErr', 'vp'),
            'app/modules/supply/portal.py': ('vp',),
        },
    },
    # ── §COMMCALC-13 (found 2026-10-15) — what a finished live login leaves on the row ───────────
    'live_login_terminal_status': {
        "question": 'What `data_source.auth_status` does a live login leave behind, and when is an '
                    'in-flight claim a LEFTOVER? `live_login_start` stamps \'authenticating\' '
                    'before the worker starts; §COMMCALC-10 taught a session that DIES to write '
                    '\'error\'; a session that merely ENDS (the inactivity timeout, or the operator '
                    'pressing Close) wrote only `auth_message` and left the status in flight, so the '
                    'chip read "Logging in…" for an hour after the browser had closed. A status is a '
                    'record of what IS: `terminal_status` restores the row\'s PRE-LOGIN status and '
                    'never hands back the in-flight one. The sibling is the worker KILLED mid-login, '
                    'which never writes an ending at all — and every merge to main redeploys both '
                    'hosts, so that is routine here; nothing on the row distinguishes it from a live '
                    'login, so the discriminator is AGE (`in_flight_is_stale`), with the budget '
                    'DERIVED from the two homes that already answer "how long may this take" rather '
                    'than copied. `present_auth_status` is what a surface reads. AND, the other '
                    'axis of the same home (§COMMCALC-24): WHICH PROCESS may answer about a '
                    'session at all. `_SESSIONS` is a dict in ONE process\'s memory, so on the '
                    'split deploy (§41) an endpoint that reads it from the API host sees an EMPTY '
                    'registry and reports "No live session running" about a session that is alive '
                    'on the worker. The old gate was spelled `require_browser_service` and asked '
                    '"does this launch a browser?"; /frame launches nothing, it reads a dict, so '
                    'the whole READ half of the operator surface was left ungated and the owner\'s '
                    'UPS login was invisible to him for five days while the browser sat on the '
                    'portal\'s Sign In button and timed out for operator inactivity. The question '
                    'that matters is "does this touch _SESSIONS?", so the gate is RENAMED here: '
                    '`require_session_host` is THE gate, and `sessions_live_here` is the pure fact '
                    'for a reader that must degrade rather than proxy. Both dereference '
                    '`app.core.service_role` — one reader of the role, not two. AND the third axis '
                    '(§COMMCALC-27): HOW PROMPTLY the operator\'s input reaches the page. The loop '
                    'is single-threaded — it streams frames, detects state and executes input on one '
                    'thread — and it serviced ONE queued command per iteration behind a full `_pump` '
                    '(80ms on CDP, 300ms without), while `_do_click` ended with a mandatory ~750ms '
                    'burst plus `_wait_settle` plus a re-classification. So each click was charged '
                    'for the previous click\'s VERIFICATION before being dequeued, the window felt '
                    'frozen ("i hvae to press hard", 2026-10-22), and pressing again lengthened the '
                    'queue. The rule: work that only makes sense once the operator has STOPPED never '
                    'sits on the latency path of their next action. `operator_input_pending` is the '
                    'ONE reader of "still acting" (pure; an unreadable queue reads as not pending, '
                    'keeping the old always-settle path), `_next_input` drains human input WITHOUT '
                    'reordering SUBMIT_CODE / RESEND / CANCEL / PULL, and BOTH loops drain, bounded '
                    'by `_INPUT_DRAIN_MAX` so a flood never starves the idle check. AND the fourth '
                    'axis of the same question (§COMMCALC-28), on the OTHER side of the wire: the '
                    'page polled `/live-login/frame` on a fixed-rate `setInterval(300)` and fanned '
                    'out three more timers per input. A fixed-rate timer fires whether or not the '
                    'last response came back, so once a poll outlived its interval — three hops, '
                    'browser to the page\'s API route to the API host to the session host — the '
                    'polls overlapped permanently, each carrying a stale `since` and so each '
                    'answered with a FULL frame of the SAME paint, holding the per-origin '
                    'connection budget the operator\'s own input needs. The owner reported the view '
                    'still laggy after the input fix landed ("it is very laggy", 2026-10-23). A '
                    'poll\'s cadence is a GAP BETWEEN RESPONSES: `pollFrames` is the ONE issuer, '
                    '`frameBusyRef` the ONE reader of "a request is in flight", a mid-flight nudge '
                    'is remembered rather than sent in parallel, and the next request is armed in a '
                    '`finally` from the moment the last one settled. AND the fifth axis '
                    '(§COMMCALC-30): WHO IS THERE. `_touch` is called by operator COMMANDS only, '
                    'never by the /state or /frame polls — deliberately, so an abandoned tab cannot '
                    'hold a browser open — but that left one fact answering two questions, and the '
                    '`human_action` budget, SHORTENED to 8 minutes on the assumption nobody is '
                    'acting, was applied to an operator who was reading the page or solving the '
                    'captcha the phase is named for. It closed the owner\'s 00:48 session at ~00:56 '
                    'while he was in it (2026-10-23). `_note_viewer` records a sighting from the two '
                    'methods that ANSWER about a session (`state`, `frame_since`), `viewer_present` '
                    'is the ONE reader of "a human is there" (pure; never seen reads as NOT watched), '
                    'and watching buys the ORDINARY budget, never a longer one — a longer one was '
                    'written and reverted because `in_flight_stale_after_s` derives the staleness '
                    'window from the longest an operator may hold a session, and widening it would '
                    'have made a killed worker\'s leftover row read as LIVE. That derivation now '
                    'dereferences `max_inactivity_limit`. Watching is not acting: `_note_viewer` '
                    'never touches `_last_activity`. AND the sixth axis of the '
                    'same question (§COMMCALC-31), which is §COMMCALC-27 found in a SECOND place: '
                    'the loop\'s own DETECTION sat on the operator\'s latency path too. '
                    '`_preauth_detect` walks every frame of the page (`_classify` reads each '
                    'frame\'s text, `_captcha_present` queries selectors), it ran on a fixed '
                    '`_CLASSIFY_POLL_S` timer, and it ran BEFORE the input drain — so a click could '
                    'wait a full `_pump` PLUS a whole page re-read before it was dequeued ("it is '
                    'very slow to react", 2026-10-26, four days after the click fix shipped). The '
                    'input drain now LEADS the tick in both loops, and `detect_due` is the ONE '
                    'answer to "may detection run now": deferred while `operator_input_pending` — '
                    'the same home §COMMCALC-27 already established, not a second queue peek — and '
                    'BOUNDED by `_DETECT_DEFER_MAX_S`, because a deferral with no ceiling is how a '
                    'captcha stops being noticed; past the ceiling it runs regardless. It is pure, '
                    'and unreadable inputs read as DUE, so the failure mode is the old always-detect '
                    'behaviour. AND the seventh (§COMMCALC-31 again, the reporting half): WHAT IS '
                    'THIS SESSION DOING RIGHT NOW. `phase` + `message` are a standing INSTRUCTION '
                    '("complete the login on the screen"), so a session grinding through a page '
                    're-read and a session that had stopped looked identical ("it shoudl show what '
                    'is going on it seems nothing is moving"). The step in flight is a DIFFERENT '
                    'fact from the phase: `_note_act` is the one setter (lock-only, never touches '
                    'the page or the row — it runs on the hot path), `activity` the one reader, and '
                    '`state`/`frame_since` DEREFERENCE it rather than composing their own, so the '
                    'line moves on an unchanged frame payload too. Frame age is None before the '
                    'first frame, never a fabricated 0 that would read as "just updated". '
                    'AND the eighth (2026-10-31): HAS THIS SESSION PASSED THE PORTAL SIGN-IN. '
                    'Three things asked it and the browser got it wrong: `can_pull` and '
                    '`can_calibrate` each carried the phase tuple inline, while the live window '
                    'compared the phase to the single word "authenticated" and cancelled the '
                    'session for anything else. Signing in immediately starts the post-login pull, '
                    'whose phase is `pulling`, so the operator closing the window during it '
                    'CANCELLED the session he had just made and the report capture then had nothing '
                    'to read. `signed_in` over `_SIGNED_IN_PHASES` is the one answer, PUBLISHED in '
                    '`state` so a surface dereferences it; an absent field reads as do-not-cancel, '
                    'because a cancelled session cannot be recovered. It deliberately excludes '
                    'thread liveness, which `can_pull`/`can_calibrate` add, so a momentary hiccup '
                    'cannot read as "never signed in". The capture CONTROL moved into the live '
                    'window too (it opens the one report editor over it), because the filters are '
                    'read THROUGH that session and sending the operator elsewhere meant closing it.',
        "homes": ('app/modules/commcalc/live_login.py',),
        "index": ('COMMCALC-13', 'COMMCALC-24', 'COMMCALC-27', 'COMMCALC-28', 'COMMCALC-30',
                  'COMMCALC-31'),
        "locks": ('harness_terminal_status.py', 'harness_live_login_progress.py',
                  'harness_live_session_host.py', 'harness_live_input_latency.py'),
        # SNAPSHOT — written BY HAND, never blessed (§50). The lock verifies it against the real
        # import graph.
        "callers": {
            'app/modules/commcalc/router.py': ('_ll', 'live_login'),
            'app/modules/supply/router.py': ('live_login',),
        },
    },
}

def keys() -> tuple:
    """Every fact in the graph, in a stable order."""
    return tuple(sorted(FACTS))


def fact(key: str) -> dict:
    """The one entry for `key`. KeyError is deliberate — a typo must not read as "no edges"."""
    return FACTS[key]


def homes_for(key: str) -> tuple:
    return tuple(FACTS[key]["homes"])


def callers_for(key: str) -> tuple:
    return tuple(sorted(FACTS[key]["callers"]))


def bound_names(key: str, path: str) -> tuple:
    """The name(s) `path` binds a home of `key` to — what an anti-un-wiring check looks for."""
    return tuple(FACTS[key]["callers"].get(path, ()))


def home_under_app(key: str, index: int = 0) -> str:
    """A home's path relative to `backend/app` — the shape the `harness_*_lock.py` walks use.

    The locks used to each carry their own `HOME = "modules/commcalc/line_class.py"` literal. They
    dereference this instead, so the ruling lives in one place and `--bless` cannot leave a lock
    pointing at a file that has moved.
    """
    return homes_for(key)[index].split("app/", 1)[-1]


def locks_for(key: str) -> tuple:
    return tuple(FACTS[key]["locks"])


def index_refs_for(key: str) -> tuple:
    return tuple(FACTS[key]["index"])


def all_homes() -> dict:
    """home path -> the fact key it answers. A home answers exactly one question by construction."""
    out: dict = {}
    for k in keys():
        for h in FACTS[k]["homes"]:
            out.setdefault(h, k)
    return out


def all_locks() -> tuple:
    return tuple(sorted({l for k in keys() for l in FACTS[k]["locks"]}))


def facts_touching(path: str) -> tuple:
    """Every fact `path` participates in, whether as a home or as a caller."""
    return tuple(k for k in keys()
                 if path in FACTS[k]["homes"] or path in FACTS[k]["callers"])


def connected(path: str) -> dict:
    """THE QUESTION THIS FILE EXISTS TO ANSWER: "I am changing `path` — what else is wired to it?"

    Returns {fact_key: {"role", "question", "homes", "siblings", "index", "locks"}}.
    `siblings` are the OTHER files wired to the same fact: for a home, every caller; for a caller,
    the homes plus every other caller. Those are the pieces a change has to be true for.
    """
    out: dict = {}
    for k in facts_touching(path):
        f = FACTS[k]
        is_home = path in f["homes"]
        sibs = set(f["callers"]) if is_home else set(f["homes"]) | set(f["callers"])
        sibs.discard(path)
        out[k] = {
            "role": "home" if is_home else "caller",
            "question": f["question"],
            "homes": tuple(f["homes"]),
            "siblings": tuple(sorted(sibs)),
            "index": tuple(f["index"]),
            "locks": tuple(f["locks"]),
        }
    return out


def impact_report(paths) -> str:
    """`connected()` for a changed-file list, rendered for a human reading a pull request."""
    lines: list = []
    for p in sorted(set(paths)):
        conn = connected(p)
        if not conn:
            continue
        lines.append(f"{p}")
        for k, c in sorted(conn.items()):
            lines.append(f"  [{c['role']}] {k} — {c['question']}")
            lines.append(f"      home(s): {', '.join(c['homes'])}")
            lines.append(f"      index:   {', '.join(c['index'])}   locks: {', '.join(c['locks'])}")
            if c["siblings"]:
                lines.append(f"      also answers this question ({len(c['siblings'])}):")
                for s in c["siblings"]:
                    lines.append(f"        - {s}")
        lines.append("")
    if not lines:
        return "No file in this change is wired to a fact in the module graph."
    return "\n".join(lines).rstrip()
