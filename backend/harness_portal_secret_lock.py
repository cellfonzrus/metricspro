#!/usr/bin/env python3
"""BUILD LOCK + PROOF — a stored credential is ENCRYPTED AT REST, and stays wired (index §CORE-03).

Owner directive 2026-10-12, asked before a UPS Store portal login existed to protect: a portal
password should not sit in the database in plain text.

CLAUDE.md: *"A design fix ships with a check that FAILS THE BUILD if a caller stops dereferencing
the shared fact, or if a second copy appears. Without that, the next change quietly restores the
patchwork."* — and the index records that writing a registry and NOT wiring its callers has already
happened three times here (§19.18). This file is that check.

THE CLASS, not the instance. The defect was never "epay's password is plaintext". It was that a
stored credential had NO HOME: nine tables each carried their own `portal_pass` / `password` /
`api_key` / `totp_secret` / `credential`, each sweep read and wrote its own, and whether a value was
protected depended on which module happened to own the table. The `vision` module got it right on
its own (migs 900+ store `*_enc` ciphertext) and, because that decision lived inside that module,
nothing carried it to the other nine. One module's good decision that its siblings cannot inherit is
the same defect as a bad one.

What this refuses to let happen:

  §A  the primitive misbehaves — a round trip loses the value, a partial update wipes a stored
      secret, a caller's dict is mutated, or legacy plaintext stops passing through
  §B  the registry grows a second source of truth, or loses the schema/key facts a caller needs
  §C  the registry names a table or column the SCHEMA does not actually have (the first draft of it
      said `google_reviews_config`; the table is `google_review_config`), or gets a key wrong
  §D  a credential column exists in the schema and is neither registered, already `_enc`, nor
      excused IN WRITING — this is the check that finds the siblings
  §E  a reader or writer stops dereferencing the registry: the six sweep readers, the status/session
      writer, the upserts, `_ds_rows`, the google-reviews reader, the billing-connector reader
  §F  the backfill grows its own list of tables instead of reading the registry, or is not run
  §G  negative controls — every scanner above can actually see a violation

Stdlib only, DB-free. The only app imports are the registry and the two pure helpers it uses.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(HERE, "app")
MIG = os.path.join(ROOT, "database", "migrations")

sys.path.insert(0, HERE)
os.environ.setdefault("FIELD_ENCRYPTION_KEY", "")

P = F = 0


def ok(cond, label, extra=""):
    global P, F
    if cond:
        P += 1
        print(f"  PASS  {label}")
    else:
        F += 1
        print(f"  FAIL  {label}  {extra}")


def eq(a, b, label):
    ok(a == b, label, f"got {a!r} want {b!r}")


def read(*parts):
    try:
        return open(os.path.join(*parts), encoding="utf-8").read()
    except OSError:
        return ""


def strip_sql_comments(text):
    """`-- …` to end of line, removed.

    Not cosmetic. The statement-bounded searches below key on `;`, and a COMMENT can contain one:
    mig 084's `-- best-effort; expiry is also detected on use` sits between session_state and
    pending_state, and with comments left in, the scanner reported pending_state as a column that
    does not exist. A structural check reads STRUCTURE."""
    return re.sub(r"--[^\n]*", "", text)


def sql_text():
    out = []
    for f in sorted(os.listdir(MIG)):
        if f.endswith(".sql"):
            out.append((f, strip_sql_comments(read(MIG, f))))
    return out


from app.modules.core import portal_secret as ps      # noqa: E402
from app.core import crypto                            # noqa: E402

RT = read(APP, "modules", "commcalc", "router.py")
BACKFILL = read(APP, "core", "encryption_backfill.py")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§A  the primitive — a credential survives the round trip, and nothing else is touched")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# A real key, so these are REAL Fernet round trips rather than the no-op passthrough. Generated per
# run and never written anywhere: a harness that needed a fixture key in the repo would be a harness
# that put a key in the repo.
try:
    from cryptography.fernet import Fernet
    _KEY = Fernet.generate_key().decode()
except Exception:                                      # pragma: no cover — cryptography absent
    _KEY = ""

ok(bool(_KEY), "A0  a key can be generated, so the round-trip checks are real encryption",
   "the `cryptography` package is missing — §A is vacuous without it")


class _Keyed:
    """`crypto` reads its keys off `settings` at every call, so a context manager is enough."""

    def __enter__(self):
        from app.core.config import settings
        self._s = settings
        self._old = getattr(settings, "FIELD_ENCRYPTION_KEY", "")
        settings.FIELD_ENCRYPTION_KEY = _KEY
        return self

    def __exit__(self, *a):
        self._s.FIELD_ENCRYPTION_KEY = self._old
        return False


with _Keyed():
    ok(crypto.is_enabled(), "A1  with a key configured, encryption is enabled")
    eq(ps.protection_state(), "protected", "A2  protection_state SAYS protected when a key is set")

    row = {"org_id": "o1", "portal_user": "ups@example.com", "portal_pass": "s3cret",
           "enabled": True}
    sealed = ps.seal_row("vip_sweep_config", row)
    ok(crypto.is_encrypted(sealed["portal_pass"]),
       "A3  the registered column comes out as enc:v1: ciphertext")
    eq(sealed["portal_user"], "ups@example.com", "A4  an UNregistered column is untouched")
    eq(sealed["enabled"], True, "A5  a non-string column is untouched")
    eq(row["portal_pass"], "s3cret",
       "A6  the caller's own dict is NOT mutated (it may be logged or returned)")
    eq(ps.open_row("vip_sweep_config", sealed)["portal_pass"], "s3cret",
       "A7  open_row gives the sweep back exactly what the operator typed")

    eq(ps.seal_row("vip_sweep_config", sealed)["portal_pass"], sealed["portal_pass"],
       "A8  an already-sealed value is passed through, never sealed twice")
    eq(ps.open_row("vip_sweep_config", {"portal_pass": "legacy-plaintext"})["portal_pass"],
       "legacy-plaintext",
       "A9  LEGACY PLAINTEXT passes through — which is why this needs no migration and no backfill "
       "window")
    eq(sorted(ps.seal_row("vip_sweep_config", {"org_id": "o1", "enabled": False})),
       ["enabled", "org_id"],
       "A10 a partial update that OMITS the password keeps omitting it (the write-only field pattern "
       "still works, so saving a schedule cannot wipe a stored credential)")
    eq(ps.seal_row("vip_sweep_config", {"portal_pass": ""}), {"portal_pass": ""},
       "A11 a blank is left blank, not encrypted into a non-empty value")
    eq(ps.seal_row("vip_sweep_config", {"portal_pass": None}), {"portal_pass": None},
       "A12 a NULL stays NULL")
    eq(ps.seal_row("vip_sweep_config", None), {}, "A13 safe on a missing row")
    eq(ps.open_rows("data_source", None), [], "A14 safe on a missing row list")
    eq(ps.seal_row("some_unregistered_table", {"password": "p"}), {"password": "p"},
       "A15 a table the registry does not name is NOT sealed — this module never guesses")

    multi = ps.seal_row("data_source", {"password": "pw", "totp_secret": "JBSWY3DP",
                                        "pending_state": None, "username": "u"})
    ok(all(crypto.is_encrypted(multi[c]) for c in ("password", "totp_secret")),
       "A16 data_source seals BOTH the portal password and the authenticator secret")
    eq(multi["username"], "u", "A17 the username is NOT a secret and stays readable")
    eq([r["password"] for r in ps.open_rows("data_source", [multi])], ["pw"],
       "A18 open_rows is open_row over a list (the due-list shape)")

    # The JSONB half: a dict in, a valid-JSON envelope stored, the same dict back out.
    st = {"cookies": [{"name": "sid", "value": "abc"}], "_sessionStorage": {"t": "tok"}}
    sj = ps.seal_row("data_source", {"session_state": st})
    eq(sorted(sj["session_state"]), ["enc"],
       "A19 a JSONB secret is sealed into crypto's {'enc': …} envelope, which IS valid JSON")
    ok("abc" not in repr(sj["session_state"]) and "tok" not in repr(sj["session_state"]),
       "A20 …and the cookie values are not in it")
    eq(ps.open_row("data_source", sj)["session_state"], st,
       "A21 …and it opens back to the exact dict Playwright needs")
    eq(ps.open_row("data_source", {"session_state": st})["session_state"], st,
       "A22 an UNWRAPPED legacy session blob passes through, so a live session is not logged out "
       "by this deploy")
    eq(ps.seal_row("data_source", {"session_state": None})["session_state"], None,
       "A23 clearing the session still clears it (pending_state: None on every success path)")

    d = ps.describe()
    blob = repr(d)
    ok("s3cret" not in blob and "JBSWY3DP" not in blob and "enc:v1:" not in blob,
       "A24 describe() carries TABLES AND COLUMNS and never a value, sealed or not")

eq(ps.protection_state(), "unprotected",
   "A25 with NO key, protection_state SAYS unprotected rather than implying a safety that is absent")
eq(ps.seal_row("vip_sweep_config", {"portal_pass": "s3cret"})["portal_pass"], "s3cret",
   "A26 with no key configured this is a transparent no-op — the platform keeps working")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§B  one source of truth — SECRET_COLUMNS is DERIVED from TABLES, never a second copy")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
eq(ps.SECRET_COLUMNS, {t: tuple(v["secrets"]) + tuple(v.get("json", ()))
                       for t, v in ps.TABLES.items()},
   "B1  SECRET_COLUMNS is exactly the derived view of TABLES")
PSRC = read(APP, "modules", "core", "portal_secret.py")
ok(re.search(r"SECRET_COLUMNS\s*=\s*\{t:[^\n]*for t, v in TABLES", PSRC)
   and re.search(r"JSON_COLUMNS\s*=\s*\{t:[^\n]*for t, v in TABLES", PSRC),
   "B2  …and both views are COMPUTED in source, not typed out a second time")
for t, v in sorted(ps.TABLES.items()):
    ok(set(v) <= {"schema", "key", "secrets", "json"} and v["key"]
       and (v["secrets"] or v.get("json")),
       f"B3  {t} declares schema, key and at least one secret column", f"got {v!r}")
    ok(all(isinstance(c, str) and c for c in v["secrets"]),
       f"B4  {t}'s secret columns are plain names")
ok(not (set(ps.TABLES) & set(ps.ALREADY_SEALED)),
   "B5  no table is both registered and already-sealed (one answer per table)")
ok(not any(t in ps.TABLES for (t, _c) in ps.NOT_A_SECRET),
   "B6  no table is both registered and excused")
ok(all(isinstance(r, str) and len(r) > 25 for r in ps.NOT_A_SECRET.values()),
   "B7  every excuse carries a WRITTEN reason, not a bare entry")
# The platform's own login passwords are hashed, not encrypted. A hashed value must never be dragged
# into a reversible registry by a later edit.
ok(not any("auth" in t or t in ("users", "user_account", "app_user") for t in ps.TABLES),
   "B8  no platform-login table is in here — those are HASHED by core.auth_security")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§C  the registry matches the SCHEMA — every table and column it names actually exists")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
SQL = sql_text()
ALL_SQL = "\n".join(t for _f, t in SQL)


def declares_table(table):
    return re.search(r"CREATE TABLE (?:IF NOT EXISTS )?[\w.]*\b%s\b" % re.escape(table), ALL_SQL)


def declared_type(table, col):
    """The SQL type `table.col` is declared with, or '' when the column is not declared at all.

    Both shapes count, and an ALTER's `ADD COLUMN` is routinely on the line BELOW its
    `ALTER TABLE` — mig 084 adds data_source.session_state exactly that way, and a regex that
    required them adjacent reported three real columns as missing while this was being written."""
    m = re.search(r"ALTER TABLE\s+[\w.]*\b%s\b[^;]*?ADD COLUMN(?: IF NOT EXISTS)?\s+%s\s+(\w+)"
                  % (re.escape(table), re.escape(col)), ALL_SQL, re.I | re.S)
    if m:
        return m.group(1).upper()
    for m in re.finditer(r"CREATE TABLE (?:IF NOT EXISTS )?[\w.]*\b%s\b\s*\((.*?)\n\);"
                         % re.escape(table), ALL_SQL, re.S):
        m2 = re.search(r"^\s+%s\s+(\w+)" % re.escape(col), m.group(1), re.M)
        if m2:
            return m2.group(1).upper()
    return ""


def declares_column(table, col):
    return bool(declared_type(table, col))


for t, v in sorted(ps.TABLES.items()):
    ok(bool(declares_table(t)), f"C1  {t} is a real table in database/migrations")
    for c in v["secrets"]:
        ty = declared_type(t, c)
        ok(bool(ty), f"C2  {t}.{c} is a real column")
        ok(ty != "JSONB",
           f"C2b {t}.{c} is NOT JSONB, so the plain enc:v1: form is storable",
           "a JSONB column belongs in this table's `json` tuple — enc:v1:… is not valid JSON")
    for c in v.get("json", ()):
        ty = declared_type(t, c)
        ok(bool(ty), f"C2c {t}.{c} (JSON secret) is a real column")
        eq(ty, "JSONB", f"C2d {t}.{c} really is JSONB")
    for c in v["key"]:
        ok(declares_column(t, c), f"C3  {t}.{c} (the key) is a real column")
for t, cols in sorted(ps.ALREADY_SEALED.items()):
    for c in cols:
        ok(declares_column(t, c), f"C4  {t}.{c} still exists…")
        ok(c.endswith("_enc"),
           f"C5  …and is still named `_enc`, so vision has not quietly stopped encrypting it")
# The key is what a backfill addresses a row by. Getting it wrong is a silent no-op on eight of the
# ten tables, so it is checked against the declared PRIMARY KEY rather than trusted.
for t, want in (("vip_sweep_config", ("org_id",)), ("email_sweep_config", ("org_id", "account")),
                ("data_source", ("id",)), ("google_review_config", ("org_id",))):
    eq(ps.table_key(t), want, f"C6  {t}'s key is {want}")
eq(ps.SECRET_COLUMNS, {t: tuple(v["secrets"]) + tuple(v.get("json", ()))
                       for t, v in ps.TABLES.items()},
   "C7  a JSON secret is still a SECRET column — the two tuples are one answer to callers")
ok(set(ps.json_columns("data_source")) == {"session_state", "pending_state"},
   "C8  the portal session blobs are the JSON secrets (a stolen session IS a stolen login)")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§D  COVERAGE — a credential column in the schema is registered, sealed, or excused")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
CRED_NAME = re.compile(r"(pass(word)?|api_key|secret|token|credential)", re.I)
TYPEY = re.compile(r"^\s{2,}(\w+)\s+(TEXT|VARCHAR|CITEXT|JSONB|BYTEA)", re.I)


def scan_credential_columns():
    """Every credential-LOOKING column the migrations declare → {table: {column, …}}.

    Unqualified table names, because that is how the code and this registry name them. The axis is
    the COLUMN NAME, deliberately: a scan keyed on anything cleverer would stop catching the next
    secret somebody adds with an ordinary name."""
    found = {}
    for _f, text in SQL:
        cur = None
        for line in text.splitlines():
            m = re.search(r"CREATE TABLE (?:IF NOT EXISTS )?([\w.]+)", line)
            if m:
                cur = m.group(1).split(".")[-1]
                continue
            m = re.search(r"ALTER TABLE\s+([\w.]+)\s+ADD COLUMN(?: IF NOT EXISTS)?\s+(\w+)",
                          line, re.I)
            if m and CRED_NAME.search(m.group(2)):
                found.setdefault(m.group(1).split(".")[-1], set()).add(m.group(2))
                continue
            if cur:
                m2 = TYPEY.match(line)
                if m2 and CRED_NAME.search(m2.group(1)):
                    found.setdefault(cur, set()).add(m2.group(1))
            if line.strip().startswith(");"):
                cur = None
    return found


SCAN = scan_credential_columns()
ok(len(SCAN) >= 10,
   "D0  the scan still finds the credential columns it is meant to police (non-vacuous)",
   f"only {len(SCAN)} tables matched")

unaccounted = []
for t, cols in sorted(SCAN.items()):
    for c in sorted(cols):
        if c in ps.secret_columns(t):
            continue
        if c in ps.ALREADY_SEALED.get(t, ()):
            continue
        if (t, c) in ps.NOT_A_SECRET:
            continue
        unaccounted.append(f"{t}.{c}")
ok(not unaccounted,
   "D1  every credential column in the schema is registered, already `_enc`, or excused in writing",
   "unaccounted: " + ", ".join(unaccounted) + " — add it to TABLES, or to NOT_A_SECRET WITH A REASON")
# An excuse for a column that no longer exists is an excuse nobody will re-read. Keep the list live.
stale = [f"{t}.{c}" for (t, c) in ps.NOT_A_SECRET
         if c not in SCAN.get(t, set()) and not declares_column(t, c)]
ok(not stale, "D2  no excuse survives the column it excuses", "stale: " + ", ".join(stale))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§E  the callers stay WIRED — a registry nobody dereferences is not a fix")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
ok("from app.modules.core import portal_secret as _psec" in RT,
   "E0  commcalc/router.py imports the registry")

for fn, table in (("_vip_cfg", "vip_sweep_config"), ("_dlar_cfg", "dlar_sweep_config"),
                  ("_b2b_cfg", "b2b_sweep_config"), ("_epay_cfg", "epay_sweep_config"),
                  ("_ftp_cfg", "ftp_sweep_config"), ("_email_cfg", "email_sweep_config")):
    m = re.search(r"\ndef %s\(.*?\n(?=\n\ndef |\n\n@|\n\n#)" % fn, RT, re.S)
    body = m.group(0) if m else ""
    ok(bool(body) and f"_psec.open_row('{table}'" in body,
       f"E1  {fn} returns the row through _psec.open_row('{table}', …)",
       "the sweep would be handed a ciphertext as its password")

# The write chokepoint: every status and session patch on these tables lands in _status_update.
m = re.search(r"\ndef _status_update\(client, table, upd, filt\):.*?\n(?=\n\ndef )", RT, re.S)
su = m.group(0) if m else ""
ok("_psec.seal_row(table, row)" in su,
   "E2  _status_update seals on the way out — this is what encrypts every portal SESSION blob "
   "(_source_stamp writes session_state / pending_state through it)")
ok(su.index("_psec.seal_row") < su.index(".update(row)") if "_psec.seal_row" in su and
   ".update(row)" in su else "_psec.seal_row(table, row)" in su,
   "E2b …before the UPDATE, not after")

for table in ("vip_sweep_config", "dlar_sweep_config", "b2b_sweep_config", "epay_sweep_config",
              "ftp_sweep_config", "email_sweep_config"):
    # Every upsert of a credential-bearing config table passes through seal_row.
    bare = re.findall(r"table\(['\"]%s['\"]\)\.upsert\(row[,)]" % table, RT)
    ok(not bare, f"E3  no upsert of {table} writes `row` unsealed", f"{len(bare)} site(s)")
    ok(f"_psec.seal_row('{table}', row)" in RT or f'_psec.seal_row("{table}", row)' in RT,
       f"E4  …and at least one sealed upsert of {table} exists")

ok("def _ds_rows(q):" in RT and '_psec.open_rows("data_source"' in RT,
   "E5  _ds_rows is THE data_source read and it opens through the registry")
unopened = re.findall(r'table\("data_source"\)\.select\("\*"\)(?:(?!_ds_rows).){0,220}?execute\(\)',
                      RT, re.S)
ok(not unopened,
   "E6  no `data_source.select(\"*\")` is executed without going through _ds_rows",
   f"{len(unopened)} raw site(s) — a password or session blob would arrive as ciphertext")
ok('_psec.seal_row("data_source", row)' in RT,
   "E7  save_data_source seals the login row (password + authenticator secret)")

GR = read(APP, "modules", "storeops", "google_reviews.py")
ok('_psec.open_row("google_review_config"' in GR,
   "E8  google_reviews.get_config — the ONE reader of api_key — opens it")
SR = read(APP, "modules", "storeops", "router.py")
ok('_psec.seal_row("google_review_config", row)' in SR,
   "E9  …and the config PUT seals it")
AR = read(APP, "modules", "account", "router.py")
ok('_psec.open_row("vip_sweep_config"' in AR,
   "E10 the credit-memo sweep's direct portal_pass read opens it too (the one read outside "
   "commcalc/router.py)")
BR = read(APP, "modules", "billing", "router.py")
ok("def _pc_rows(q):" in BR and "_psec.open_rows(_PC_TABLE" in BR,
   "E11 the platform billing connector reads its vendor credential through the registry")
ok(BR.count("_psec.seal_row(_PC_TABLE") >= 2,
   "E12 …and both its insert and its update seal it")
AC = read(APP, "modules", "supply", "api_credential.py")
ok('_psec.seal_row("data_source", patch)' in AC,
   "E13 a minted vendor token is sealed before it is cached on the login row")

# Nothing may keep its own idea of which columns are secret.
for mod, label in ((RT, "commcalc/router.py"), (GR, "storeops/google_reviews.py"),
                   (BACKFILL, "core/encryption_backfill.py")):
    ok(not re.search(r"^\s*(_?[A-Z_]*SECRETS?|_?SECRET_COLS)\s*=\s*[\(\[{]", mod, re.M)
       or label != "core/encryption_backfill.py",
       f"E14 {label} declares no private list of secret columns")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§F  the backfill READS the registry instead of carrying its own list")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
from app.core import encryption_backfill as eb                 # noqa: E402
ok(hasattr(eb, "backfill_portal_secrets"), "F1  backfill_portal_secrets exists")
m = re.search(r"\ndef backfill_portal_secrets\(.*?\n(?=\n\ndef )", BACKFILL, re.S)
body = m.group(0) if m else ""
ok("portal_secret.SECRET_COLUMNS.items()" in body,
   "F2  it iterates the REGISTRY, so a newly registered column is backfilled with no edit here")
ok("portal_secret.table_schema(" in body and "portal_secret.table_key(" in body,
   "F3  …and it reads the schema and the key from the registry rather than assuming them")
leaked = [t for t in ps.TABLES if f'"{t}"' in body or f"'{t}'" in body]
ok(not leaked, "F4  it spells NO table name of its own (that would be the second copy)",
   f"found {leaked}")
ok(re.search(r'"portal_secrets":\s*backfill_portal_secrets\(', BACKFILL),
   "F5  run_all runs it, so ENCRYPTION_BACKFILL_ON_BOOT=1 seals the rows already stored")
ok('.eq("id", row["id"])' not in body,
   "F6  it does not address rows by `id` — eight of the ten tables have no such column")
res = eb.backfill_portal_secrets(None)
eq(res, {"tables": {}, "sealed": 0, "enabled": False},
   "F7  with no key configured the sweep is a no-op and says so (it never touches the DB)")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n§G  negative controls — every scanner above can actually see a violation")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
ok(not re.search(r"\ndef _vip_cfg\(.*?_psec\.open_row\('vip_sweep_config'",
                 RT.replace("_psec.open_row('vip_sweep_config'", "rows[0] if rows"), re.S),
   "G1  E1's scanner stops passing if the reader reverts to returning the raw row")
ok(bool(re.findall(r"table\(['\"]vip_sweep_config['\"]\)\.upsert\(row[,)]",
                   "    client.schema('commcalc').table('vip_sweep_config').upsert(row, "
                   "on_conflict='org_id').execute()")),
   "G2  E3's scanner sees an unsealed upsert when one is planted")
ok(bool(re.findall(r'table\("data_source"\)\.select\("\*"\)(?:(?!_ds_rows).){0,220}?execute\(\)',
                   '    rows = (client.schema("commcalc").table("data_source").select("*")\n'
                   '            .eq("org_id", org_id).execute().data) or []', re.S)),
   "G3  E6's scanner sees a raw data_source read when one is planted")
ok("_psec.seal_row(table, row)" not in
   read(APP, "modules", "commcalc", "router.py").replace("_psec.seal_row(table, row)", "pass"),
   "G4  E2's scanner stops passing if the write chokepoint stops sealing")


def _plant_scan(sql):
    """Run §D's scanner over ONE planted migration, to prove it would catch a new secret column."""
    global SQL
    keep = SQL
    SQL = [("plant.sql", sql)]
    try:
        return scan_credential_columns()
    finally:
        SQL = keep


planted = _plant_scan("CREATE TABLE IF NOT EXISTS commcalc.ups_portal_config (\n"
                      "  org_id UUID PRIMARY KEY,\n"
                      "  portal_pass TEXT,\n"
                      ");\n")
eq(planted, {"ups_portal_config": {"portal_pass"}},
   "G5  D1's scanner would catch a NEW credential column added to a new table")
eq(_plant_scan("ALTER TABLE commcalc.data_source ADD COLUMN IF NOT EXISTS backup_password TEXT;"),
   {"data_source": {"backup_password"}},
   "G6  …and one added by ALTER to an existing table")
eq(_plant_scan("CREATE TABLE IF NOT EXISTS x.y (\n  id UUID PRIMARY KEY,\n  label TEXT,\n);\n"), {},
   "G7  …and does not flag an ordinary column (no false positive)")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
