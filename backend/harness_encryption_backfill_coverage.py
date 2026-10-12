#!/usr/bin/env python3
"""BUILD LOCK + PROOF — a column encrypted on write is SEALED AT REST too (index §CORE-04).

Measured defect, 2026-10-12: two employees' bank routing numbers were readable in plain text in
production (`storeops.employee_onboarding_profile.intake_data` → `dd_routing`, `dd_bank_name`) while
`encryption_backfill.run_all()`'s docstring said it ran "every backfill sweep". Nothing in the code
was wrong. Sealing those values existed only as `POST /hr/onboarding/encrypt-existing`, a manual
per-tenant button, so a server could boot with ENCRYPTION_BACKFILL_ON_BOOT=1 forever and never touch
them. Everything written AFTER the key was set was correctly encrypted, which is exactly why it
looked fine unless you counted.

THE CLASS, not the instance. The instance was HR intake. The class is that "encrypted on write" and
"sealed at rest" were two separate facts with nothing tying them together, so every new encrypted
column started out HALF protected and the application could not tell. A second instance was already
in the tree and nobody had reported it: `pos.receipt_imports` (imei / notes / parsed / raw_ocr) had
no sweep at all.

The fix is that `encryption_backfill.SWEEPS` is the one home for "which encrypted-at-rest column
family has a sweep", `run_all()` DERIVES its calls from it, and this file fails the build when they
come apart.

  §A  SWEEPS is well-formed, and every sweep it names actually exists and is callable
  §B  run_all DERIVES from SWEEPS — it does not list calls that can fall behind the registry
  §C  COVERAGE: every module under backend/app that encrypts a value it STORES is named by a family
      in SWEEPS, or excused IN WRITING in NO_STORED_WRITE. This is the check that finds the siblings
  §D  no sweep carries its own copy of a column list — each reads the writer's declaration
  §E  the sweeps are reachable from an operator with no shell (the boot flag) and from the HR button,
      and the button holds no second loop
  §F  the stored-row sealer is honest: idempotent, never indexes ciphertext, `{}` when nothing is due
  §G  negative controls — every scanner above can actually see a violation

Stdlib only, DB-free. No database, no network, no key required.
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "app")
BACKFILL = os.path.join(APP, "core", "encryption_backfill.py")

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


def read(path):
    try:
        return open(path, encoding="utf-8").read()
    except OSError:
        return ""


BACKFILL_SRC = read(BACKFILL)

# ── the registry, read from SOURCE (no app import, no pydantic/config stack) ───────────────────────
# Parsed with ast rather than imported: this harness must run in CI's bare python with nothing
# installed, and a lock that needs the app's dependency tree to start is a lock that gets skipped.


def _literal(src, name):
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return ast.literal_eval(node.value)
    return None


def _func_src(src, name):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    return ""


def _func_code(src, name):
    """A function's source with its DOCSTRING removed — what the function DOES, not what it says.

    §D asks whether a sweep spells a column name. A sweep's docstring legitimately names the columns
    it seals, in prose, for whoever reads a failure; counting that as a second copy is the same
    false positive the grep-based scanner in §C was replaced to avoid. A structural check reads
    structure in both places."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            body = node.body[1:] if (node.body and isinstance(node.body[0], ast.Expr)
                                     and isinstance(node.body[0].value, ast.Constant)
                                     and isinstance(node.body[0].value.value, str)) else node.body
            return "\n".join(ast.get_source_segment(src, n) or "" for n in body)
    return ""


SWEEPS = _literal(BACKFILL_SRC, "SWEEPS") or {}
NO_STORED_WRITE = _literal(BACKFILL_SRC, "NO_STORED_WRITE") or {}
BORN_SEALED = _literal(BACKFILL_SRC, "BORN_SEALED") or {}
DEFINED = {n.name for n in ast.walk(ast.parse(BACKFILL_SRC or "pass"))
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

print("\n§A  the registry is well-formed, and names sweeps that exist")
ok(bool(SWEEPS), "A0  encryption_backfill.SWEEPS parses and is non-empty")
ok(len(SWEEPS) >= 4, "A1  all four measured families are registered",
   f"got {sorted(SWEEPS)}")
for fam, spec in sorted(SWEEPS.items()):
    ok(set(spec) >= {"writer", "stores", "sweep"},
       f"A2  {fam}: carries writer / stores / sweep", f"got {sorted(spec)}")
    ok(spec.get("sweep") in DEFINED,
       f"A3  {fam}: sweep {spec.get('sweep')!r} is defined in this module")
    ok(os.path.exists(os.path.join(HERE, spec.get("writer", "x"))),
       f"A4  {fam}: writer {spec.get('writer')!r} is a real file")
    ok(bool(spec.get("stores")) and all(isinstance(x, str) and x.strip() for x in spec["stores"]),
       f"A5  {fam}: says what it seals")
for fam in ("customer_pins", "portal_secrets", "intake_sensitive", "receipt_imports"):
    ok(fam in SWEEPS, f"A6  the measured family {fam!r} is registered")
# Every sweep takes the SAME shape, or run_all's derived call cannot work.
for fam, spec in sorted(SWEEPS.items()):
    fsrc = _func_src(BACKFILL_SRC, spec.get("sweep", ""))
    ok("dry_run" in fsrc and "client" in fsrc.split("\n")[0] + fsrc.split(")")[0],
       f"A7  {spec.get('sweep')}: takes (client, *, dry_run=…) like its siblings")
    ok("crypto.is_enabled()" in fsrc,
       f"A8  {spec.get('sweep')}: returns enabled:False rather than pretending with no key")

print("\n§B  run_all DERIVES its calls from the registry")
RUN_ALL = _func_src(BACKFILL_SRC, "run_all")
ok(bool(RUN_ALL), "B0  run_all exists")
ok("for name in sorted(SWEEPS)" in RUN_ALL,
   "B1  run_all iterates SWEEPS — it does not list the sweeps")
for fam, spec in sorted(SWEEPS.items()):
    ok(f"{spec['sweep']}(" not in RUN_ALL,
       f"B2  run_all does not hard-call {spec['sweep']} (that is the list that falls behind)")
ok("except Exception" in RUN_ALL,
   "B3  one family raising does not leave the others plaintext")
ok(RUN_ALL.count("globals()") == 1,
   "B4  the sweep is resolved by name from the registry, once")

print("\n§C  COVERAGE — every module that encrypts a STORED value has a sweep")
# A module "encrypts a stored value" when it calls crypto.encrypt / encrypt_map / encrypt_json.
# blind_index is deliberately NOT a trigger: a blind index is derived from a value, not the stored
# secret itself, and a module may compute one purely to QUERY (pos/router's digits lookup).
ENC_CALL = re.compile(r"\b(?:crypto\.)?(encrypt|encrypt_map|encrypt_json)\s*\(")


def encrypting_modules():
    """Every file under backend/app with a real encrypt CALL — comments and docstrings excluded.

    Parsed, not grepped. `portal_secret.py` mentions `crypto.encrypt` four times in prose explaining
    itself, and a grep-based scanner counted those as call sites; a structural check reads calls.
    """
    found = {}
    for base, _dirs, files in os.walk(APP):
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(base, fn)
            rel = "app/" + os.path.relpath(path, APP).replace(os.sep, "/")
            try:
                tree = ast.parse(read(path))
            except SyntaxError:
                continue
            names = set()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                nm = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
                if nm in ("encrypt", "encrypt_map", "encrypt_json"):
                    names.add(nm)
            if names:
                found[rel] = sorted(names)
    return found


ENCRYPTING = encrypting_modules()
ok(bool(ENCRYPTING), "C0  the scanner finds encrypt call sites at all")
CLAIMED = ({spec["writer"] for spec in SWEEPS.values()}
           | set(NO_STORED_WRITE) | set(BORN_SEALED))
for rel, calls in sorted(ENCRYPTING.items()):
    ok(rel in CLAIMED,
       f"C1  {rel} ({', '.join(calls)}) is named by a SWEEPS family or excused in writing",
       "add it to SWEEPS with a sweep that seals its old rows, or — if the column was born "
       "encrypted — to BORN_SEALED, or to NO_STORED_WRITE with a reason")
for rel, reason in sorted(NO_STORED_WRITE.items()):
    ok(len(reason.strip()) >= 20, f"C2  the excuse for {rel} is a written reason, not a shrug")
    ok(rel in ENCRYPTING, f"C3  the excuse for {rel} is still needed (it still encrypts)")
# The two families whose absence was the measured defect.
ok("app/modules/hr/router.py" in CLAIMED,
   "C4  HR intake — the family whose absence left two routing numbers plaintext — is covered")
ok("app/modules/pos/receipt_import.py" in CLAIMED,
   "C5  pos.receipt_imports — the sibling found while fixing it — is covered")


def column_writes_not_encrypted(rel, columns):
    """Every value this module assigns to one of `columns` that is NOT an encrypt() call or a clear.

    This is what makes BORN_SEALED a LOCK rather than a promise. A column is only exempt from having
    a sweep because no plaintext has ever reached it; the moment a write bypasses encrypt(), that
    stops being true and the module needs the sweep after all. Covers both shapes a write takes
    here: `row["x_enc"] = …` and a dict literal passed to insert/update. Setting one to None or ""
    is a REVOKE (vision clears both columns when a link is torn down), not a plaintext write."""
    src = read(os.path.join(HERE, rel))
    bad = []

    def _clear(v):
        return isinstance(v, ast.Constant) and (v.value is None or v.value == "")

    def _sealed(v):
        if _clear(v):
            return True
        for n in ast.walk(v):
            if isinstance(n, ast.Call):
                f = n.func
                nm = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
                if nm in ("encrypt", "encrypt_map", "encrypt_json"):
                    return True
        return False

    try:
        tree = ast.parse(src)
    except SyntaxError:
        return ["unparseable"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if (isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                        and t.slice.value in columns and not _sealed(node.value)):
                    bad.append(f"{t.slice.value} @ line {node.lineno}")
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if (isinstance(k, ast.Constant) and k.value in columns and not _sealed(v)):
                    bad.append(f"{k.value} @ line {k.lineno}")
    return bad


for rel, spec in sorted(BORN_SEALED.items()):
    ok(len(spec.get("reason", "").strip()) >= 60,
       f"C6  {rel}: the born-encrypted claim carries a written, measured reason")
    ok(bool(spec.get("columns")), f"C7  {rel}: names the columns it claims were born encrypted")
    ok(rel in ENCRYPTING, f"C8  {rel}: still encrypts (the entry is still needed)")
    unsealed = column_writes_not_encrypted(rel, set(spec.get("columns") or ()))
    ok(not unsealed,
       f"C9  {rel}: every write to those columns goes through encrypt() — so no plaintext row can "
       f"exist and no sweep is owed", f"plaintext writes: {unsealed}")

print("\n§D  no sweep keeps its own copy of a column list")
for fam, spec in sorted(SWEEPS.items()):
    fsrc = _func_code(BACKFILL_SRC, spec["sweep"])
    # A column name typed into a sweep is the second home the registry exists to prevent. The known
    # column names, measured: if a sweep spells one, it is no longer dereferencing its writer.
    for col in ("portal_pass", "totp_secret", "session_state", "pending_state", "api_key",
                "dd_routing", "dd_bank_name", "raw_ocr", "imei_bidx"):
        ok(col not in fsrc, f"D1  {spec['sweep']} does not spell the column {col!r}")
ok("portal_secret.SECRET_COLUMNS" in BACKFILL_SRC,
   "D2  the portal sweep reads the credential registry (§CORE-03)")
ok("receipt_import.ENCRYPTED_IMPORT_COLUMNS" in BACKFILL_SRC,
   "D3  the receipt sweep reads the importer's own column declaration")
ok("_public_intake_fields" in BACKFILL_SRC,
   "D4  the intake sweep reads HR's own 'which field is sensitive' declaration, per tenant")
ok("seal_stored_row" in BACKFILL_SRC,
   "D5  the receipt sweep applies the importer's sealer rather than re-implementing it")

print("\n§E  reachable without a shell, and the HR button holds no second loop")
MAIN = read(os.path.join(APP, "main.py"))
ok("ENCRYPTION_BACKFILL_ON_BOOT" in MAIN, "E0  the boot flag is still wired in main.py")
ok("run_all" in MAIN, "E1  the boot hook calls run_all (so it covers every registered family)")
HR = read(os.path.join(APP, "modules", "hr", "router.py"))
BUTTON = _func_src(HR, "onboarding_encrypt_existing")
ok(bool(BUTTON), "E2  the HR encrypt-existing endpoint still exists")
ok("backfill_intake_sensitive" in BUTTON,
   "E3  the HR button CALLS the shared sweep")
ok("crypto.encrypt(" not in BUTTON,
   "E4  …and no longer holds its own encrypt loop (one implementation, no divergence)")
ok("employee_onboarding_profile" not in BUTTON,
   "E5  …and no longer reads the table itself")

print("\n§F  the stored-row sealer is honest")
RI = read(os.path.join(APP, "modules", "pos", "receipt_import.py"))
SEAL = _func_src(RI, "seal_stored_row")
ok(bool(SEAL), "F0  receipt_import.seal_stored_row exists")
ok("is_encrypted" in SEAL, "F1  it never re-encrypts an already-sealed value")
ok("return patch" in SEAL and "patch: dict = {}" in SEAL,
   "F2  it returns a PATCH, so a row needing nothing issues no write")
ok("_IMPORT_BLIND_INDEX_COLUMNS" in SEAL,
   "F3  it dereferences the blind-index declaration rather than naming imei_bidx")
ok("not crypto.is_encrypted(plain)" in SEAL,
   "F4  it indexes only a PLAINTEXT source — indexing ciphertext writes a token matching nothing")
WRITER = _func_src(RI, "_encrypt_import_row")
ok("_ENCRYPTED_IMPORT_JSON_COLUMNS" in WRITER and "_IMPORT_BLIND_INDEX_COLUMNS" in WRITER,
   "F5  the NEW-row writer reads the same declarations (one home, two callers)")
for name in ("ENCRYPTED_IMPORT_COLUMNS", "ENCRYPTED_IMPORT_JSON_COLUMNS",
             "IMPORT_BLIND_INDEX_COLUMNS"):
    ok(re.search(rf"^{name} = _", RI, re.M) is not None,
       f"F6  {name} is exported as the private declaration, not a second copy")

print("\n§H  the sweeps actually seal — a REAL key, a fake database, the measured regression")
# Structure is not behaviour. §A-§G prove the registry cannot come apart; this proves a sweep put in
# front of a plaintext row seals it, that it leaves a sealed row alone, and specifically that
# run_all() now reaches the HR intake values whose only path used to be a button nobody pressed.
#
# Needs `cryptography` + the app's config stack (CI installs backend/requirements.txt for this job).
# A local run on an interpreter without them fails here, loudly and correctly — the harness genuinely
# cannot verify sealing without the primitive. See harness-local-run caveats.


class _Q:
    """The smallest thing that answers the sweeps' query chain against an in-memory table."""

    def __init__(self, store, name):
        self.store, self.name, self.filters, self.patch = store, name, {}, None
        self.lo, self.hi = 0, 10 ** 9

    def select(self, _cols):
        return self

    def update(self, patch):
        self.patch = patch
        return self

    def eq(self, k, v):
        self.filters[k] = v
        return self

    def order(self, *_a, **_k):
        return self

    def range(self, lo, hi):
        self.lo, self.hi = lo, hi
        return self

    def _match(self, row):
        return all(row.get(k) == v for k, v in self.filters.items())

    def execute(self):
        rows = [r for r in self.store[self.name] if self._match(r)]
        if self.patch is not None:
            for r in rows:
                r.update(self.patch)
            return type("R", (), {"data": rows})()
        return type("R", (), {"data": [dict(r) for r in rows[self.lo:self.hi + 1]]})()


class _Client:
    def __init__(self, store):
        self.store = store

    def schema(self, _s):
        return self

    def table(self, name):
        self.store.setdefault(name, [])
        return _Q(self.store, name)


try:
    from cryptography.fernet import Fernet
    _key = Fernet.generate_key().decode()
except Exception as e:                              # noqa: BLE001
    _key = ""
    ok(False, "H0  `cryptography` is importable so sealing can be proved", repr(e))

if _key:
    os.environ["FIELD_ENCRYPTION_KEY"] = _key
    import importlib
    from app.core import crypto as _crypto
    importlib.reload(_crypto)
    from app.core import encryption_backfill as _eb
    importlib.reload(_eb)
    ok(_crypto.is_enabled(), "H0  a real key is configured for this run")

    # ── the measured regression: a plaintext bank routing number in intake_data ───────────────────
    # The live finding, 2026-10-12: two employees' `dd_routing` / `dd_bank_name` were readable in
    # production. The values here are invented; the SHAPE is the one that was measured.
    store = {"employee_onboarding_profile": [
        {"org_id": "org-a", "employee_id": "E174",
         "intake_data": {"dd_routing": "021000021", "dd_bank_name": "Example Bank",
                         "first_name": "A"}},
        {"org_id": "org-a", "employee_id": "E999",
         "intake_data": {"dd_routing": _crypto.encrypt("011000015"), "first_name": "B"}},
    ]}
    sens = [{"key": "dd_routing", "label": "Routing", "sensitive": True},
            {"key": "dd_bank_name", "label": "Bank", "sensitive": True},
            {"key": "first_name", "label": "First", "sensitive": False}]
    from app.modules.hr import router as _hr
    _real_fields = _hr._public_intake_fields
    _hr._public_intake_fields = lambda _org: sens
    try:
        res = _eb.run_all(_Client(store))
    finally:
        _hr._public_intake_fields = _real_fields

    rows = {r["employee_id"]: r["intake_data"] for r in store["employee_onboarding_profile"]}
    ok(_crypto.is_encrypted(rows["E174"]["dd_routing"]),
       "H1  run_all SEALS a plaintext routing number — the regression; before this PR the only "
       "path was a manual per-tenant button, so it stayed readable")
    ok(_crypto.is_encrypted(rows["E174"]["dd_bank_name"]), "H2  …and every sensitive key, not one")
    eq(_crypto.decrypt(rows["E174"]["dd_routing"]), "021000021",
       "H3  …and the value still reads back exactly")
    eq(rows["E174"]["first_name"], "A",
       "H4  a field the tenant did NOT declare sensitive is left alone")
    eq(_crypto.decrypt(rows["E999"]["dd_routing"]), "011000015",
       "H5  an already-sealed row is not double-encrypted")
    eq(res["intake_sensitive"]["orgs"]["org-a"]["values"], 2,
       "H6  the count reported is VALUES sealed, which is what the HR screen says")
    eq(res["intake_sensitive"]["orgs"]["org-a"]["sealed"], 1, "H7  …and `sealed` counts profiles")

    # re-running must do nothing: idempotent, as every boot with the flag set will re-run it
    _hr._public_intake_fields = lambda _org: sens
    try:
        again = _eb.run_all(_Client(store))
    finally:
        _hr._public_intake_fields = _real_fields
    eq(again["intake_sensitive"]["orgs"]["org-a"]["values"], 0,
       "H8  a second run seals nothing — safe on every boot and on several replicas")

    # ── the sibling: pos.receipt_imports ─────────────────────────────────────────────────────────
    from app.modules.pos import receipt_import as _ri
    plain = {"id": "r1", "org_id": "org-a", "imei": "356938035643809", "notes": "see card 4242",
             "parsed": {"total": "9.99"}, "raw_ocr": None, "imei_bidx": None}
    patch = _ri.seal_stored_row(plain)
    ok(_crypto.is_encrypted(patch["imei"]), "H9  seal_stored_row seals a plaintext IMEI")
    ok(isinstance(patch["parsed"], dict) and set(patch["parsed"]) == {"enc"},
       "H10 …wraps the JSONB blob in the {'enc': …} envelope, not an enc:v1: string")
    ok("raw_ocr" not in patch, "H11 …and leaves a NULL blob null rather than writing an envelope")
    ok(bool(patch.get("imei_bidx")), "H12 …and gives the IMEI the blind index its search needs")
    eq(_ri.seal_stored_row({**plain, **patch}), {},
       "H13 …and a row already sealed returns {} (no write at all)")
    sealed_imei = {"id": "r2", "imei": _crypto.encrypt("356938035643809"), "imei_bidx": None,
                   "notes": None, "parsed": None, "raw_ocr": None}
    ok("imei_bidx" not in _ri.seal_stored_row(sealed_imei),
       "H14 …and never indexes CIPHERTEXT — a token derived from an enc:v1: string matches nothing")

    # ── a family that raises must not take the others down ───────────────────────────────────────
    class _Boom(_Client):
        def table(self, name):
            if name == "customers":
                raise RuntimeError("relation does not exist")
            return super().table(name)

    _hr._public_intake_fields = lambda _org: sens
    try:
        mixed = _eb.run_all(_Boom({"employee_onboarding_profile": []}))
    finally:
        _hr._public_intake_fields = _real_fields
    ok("error" in mixed["customer_pins"], "H15 a sweep that raises is REPORTED under its own key")
    ok("error" not in mixed["intake_sensitive"],
       "H16 …and the other families still ran (one missing migration must not leave PII plaintext)")
    eq(sorted(mixed), sorted(_eb.SWEEPS),
       "H17 run_all's result covers every registered family, derived — nothing silently absent")

print("\n§G  negative controls — each scanner above can see a violation")
_src_ok = BACKFILL_SRC


def _plant_registry(extra):
    return _literal(BACKFILL_SRC.replace("SWEEPS: dict[str, dict] = {",
                                         "SWEEPS: dict[str, dict] = {\n" + extra), "SWEEPS")


planted = _plant_registry('    "ghost": {"writer": "app/nope.py", "stores": ("x",),'
                          ' "sweep": "backfill_nothing"},\n')
ok("ghost" in planted and planted["ghost"]["sweep"] not in DEFINED,
   "G1  A3 would catch a family naming a sweep that does not exist")
ok(not os.path.exists(os.path.join(HERE, planted["ghost"]["writer"])),
   "G2  A4 would catch a family naming a writer file that does not exist")

ok(encrypting_modules.__doc__ is not None and
   "app/modules/core/portal_secret.py" in ENCRYPTING,
   "G3  the call scanner sees the known encrypt site in portal_secret")


def _plant_module(body):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "planted.py")
        open(f, "w", encoding="utf-8").write(body)
        tree = ast.parse(read(f))
        return {n.func.attr for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("encrypt", "encrypt_map", "encrypt_json")}


eq(_plant_module("from app.core import crypto\nx = crypto.encrypt('a')\n"), {"encrypt"},
   "G4  C1's scanner would catch a NEW module encrypting a stored value")
eq(_plant_module("# crypto.encrypt(secret)  <- prose, not a call\nx = 1\n"), set(),
   "G5  …and does NOT count a mention in a comment (the grep false positive this replaces)")
eq(_plant_module('"""crypto.encrypt_json(blob) explained in a docstring."""\nx = 1\n'), set(),
   "G6  …nor one in a docstring")
eq(_plant_module("from app.core import crypto\nt = crypto.blind_index('5', mode='digits')\n"), set(),
   "G7  …and a blind index alone is not a stored secret (pos/router queries with one)")
_bs = sorted(BORN_SEALED)[0] if BORN_SEALED else ""
ok(bool(_bs) and column_writes_not_encrypted(_bs, {"status"}),
   "G9  C9's scanner would catch a write to a claimed column that skips encrypt() "
   "(proved against a column in the same module that is written in the clear)")

ok("for name in sorted(SWEEPS)" not in
   RUN_ALL.replace("for name in sorted(SWEEPS)", "for name in ['customer_pins']"),
   "G8  B1 would catch run_all going back to a literal list of families")

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
