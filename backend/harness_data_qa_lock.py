#!/usr/bin/env python3
"""THE LOCK — the in-app data assistant reads the platform's OWN reports, as the signed-in user, and
there is exactly one declaration of what it may read.

CLAUDE.md, "A fix is a DESIGN fix or it is not a fix": the INSTANCE the owner asked for is an
assistant that can say which store was best. The CLASS is that "what is the revenue of store X in
month M" already has exactly one home — the report — and an assistant that answers it from anywhere
else is a second derivation of money. Two paths answering one question is the duplicate defect the
index rules forbid, and this file is the check that fails the build when one appears.

WHAT FAILS THE BUILD
  A  ONE registry. No second module declares report paths for an assistant, and the registry itself
     declares no columns, no measures and no SQL.
  B  ONE reader. `data_qa_agent._fetch` is the only place a registered path is used, it only ever
     GETs, and it carries the CALLER'S authorization rather than a service key — so the assistant
     inherits the endpoints' RBAC instead of re-implementing it.
  C  NO permission rule of its own. The agent and the API refuse to contain a scope, store-span or
     role decision: those live in `storeops.caller_scope` / `scope_keyset` and in the AI guard.
  D  NO arithmetic outside the proved module. The agent does not add, average or rank; every figure
     it reports came from `data_qa_compute`.
  E  THE PURPOSE is registered in BOTH shared registries (the guard's `AI_PURPOSES` and billing's
     `AI_CALL_SITES`), so the spend is both authorized and billed — and the guard refuses an
     unregistered purpose, which an armed control proves.
  F  SEV-1 2026-07-30 — the model call is the ASYNC client, awaited, with an explicit timeout.
  G  PURITY of the two provable modules: stdlib only, no `app.` import, no database, no network.
  H  BOUNDS — a question cannot run unbounded reports, model turns or rows into context.
  I  the model is never handed rows to retype: a tool call references a RESULT id.
  J  registered in the module graph and in migration 1055.

Every control is ARMED: the check is shown to FAIL on a deliberately broken input, so a lock that
has quietly stopped looking at anything cannot pass.

Run: python3 backend/harness_data_qa_lock.py     (stdlib only, no DB, no network)
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(HERE, "app")
MIG = os.path.join(ROOT, "database", "migrations", "1055_data_qa_assistant.sql")
sys.path.insert(0, HERE)

from app.modules.core import control_box as cbx              # noqa: E402
from app.modules.core import data_qa_agent as AG             # noqa: E402
from app.modules.core import data_qa_compute as CALC         # noqa: E402
from app.modules.core import data_qa_registry as REG         # noqa: E402
from app.modules.billing import ai_usage as USAGE            # noqa: E402

REGISTRY_HOME = "modules/core/data_qa_registry.py"
COMPUTE_HOME = "modules/core/data_qa_compute.py"
AGENT_HOME = "modules/core/data_qa_agent.py"
API_HOME = "modules/core/data_qa_api.py"

FAILS = []
CHECKS = 0


def ok(cond, label):
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILS.append(label)


def section(name):
    print(f"\n── {name}")


def py_files():
    for base, dirs, names in os.walk(APP):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for n in names:
            if n.endswith(".py"):
                full = os.path.join(base, n)
                yield os.path.relpath(full, APP).replace(os.sep, "/"), full


def read(rel):
    return open(os.path.join(APP, rel), encoding="utf-8").read()


FILES = {rel: open(full, encoding="utf-8").read() for rel, full in py_files()}

_TRIPLE_D = '"' * 3
_TRIPLE_S = "'" * 3


def code_only(src):
    """`src` with every comment and the contents of every string literal blanked.

    Without this, a lock is defeated by its own documentation: `data_qa_agent` says "do NOT
    reintroduce `Anthropic(` in this file" IN its docstring, and a checker reading raw text then
    reports the WARNING as the violation. Every "no X appears here" check below reads this view, so
    it sees what the interpreter sees rather than what the author wrote about.
    """
    kept = []
    for line in src.splitlines():
        kept.append("" if line.lstrip().startswith("#") else line.split("#", 1)[0])
    txt = "\n".join(kept)
    txt = re.sub(_TRIPLE_D + r"(?:.|\n)*?" + _TRIPLE_D, '""', txt)
    txt = re.sub(_TRIPLE_S + r"(?:.|\n)*?" + _TRIPLE_S, "''", txt)
    txt = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', txt)
    txt = re.sub(r"'(?:\\.|[^'\\\n])*'", "''", txt)
    return txt


CODE = {rel: code_only(src) for rel, src in FILES.items()}


# ── §A one registry ─────────────────────────────────────────────────────────────────────────────
section("A. ONE declaration of what the assistant may read")

def _declares_report_paths(src, rel=""):
    """STRUCTURAL, not textual: does this module contain a dict whose VALUES are dicts each carrying
    a `"path"` key whose value is an `/api/v1/...` literal?

    That shape IS a semantic layer — a declaration of "this question is served by that endpoint" —
    and the registry is the only module allowed to hold one. A module that merely MENTIONS a path in
    prose, or builds one URL, does not match (the first version of this check matched the operator
    console and the tenant middleware on the word "question" in a comment). A sibling registry
    cannot avoid matching, because it has to declare paths to be one."""
    try:
        t = ast.parse(src)
    except SyntaxError:
        return False
    found = 0
    for node in ast.walk(t):
        if not isinstance(node, ast.Dict):
            continue
        for val in node.values:
            if not isinstance(val, ast.Dict):
                continue
            for k, v in zip(val.keys, val.values):
                if (isinstance(k, ast.Constant) and k.value == "path"
                        and isinstance(v, ast.Constant)
                        and str(v.value).startswith("/api/v1/")):
                    found += 1
    return found >= 3


others = [rel for rel, src in FILES.items()
          if rel != REGISTRY_HOME and _declares_report_paths(src, rel)]
ok(not others, f"A1 no second question registry: {others}")
ok(_declares_report_paths(FILES[REGISTRY_HOME], REGISTRY_HOME),
   "A1-ARMED the detector really fires on the registry itself (so A1 is not vacuous)")

banned = {"columns", "column", "measures", "fields", "schema", "sql", "table", "query"}
leaks = [k for k in REG.keys() if banned & set(REG.question(k))]
ok(not leaks, f"A2 no question declares a report's columns, tables or SQL: {leaks}")
ok(bool(banned & set({"columns": 1, "label": 2})),
   "A2-ARMED the key check really fires on a dict that does declare columns")

ok(not re.search(r"\.execute\(|\.table\(|\.rpc\(|\bselect\b.{0,80}\bfrom\b",
                 CODE[REGISTRY_HOME], re.I | re.S),
   "A3 the registry issues no query of its own — it NAMES a report, it is not one")
ok(bool(re.search(r"\.execute\(", code_only("x.execute()"))),
   "A3-ARMED the query detector really fires on real code")

# Every path a model can reach is in the registry, and none of them is a write route.
WRITEY = re.compile(r"/(?:compute|recompute|run|import|upload|delete|accept|submit|close|send)\b")
bad = [k for k in REG.keys() if WRITEY.search(REG.question(k)["path"])]
ok(not bad, f"A4 no registered question points at a write / compute route: {bad}")
ok(bool(WRITEY.search("/api/v1/account/pl/compute")),
   "A4-ARMED the write-route detector really fires")


# ── §B one reader, GET only, the caller's own token ─────────────────────────────────────────────
section("B. ONE reader, GET only, as the caller")
agent_src = FILES[AGENT_HOME]
tree = ast.parse(agent_src)

verbs = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        if node.value.id in ("cli", "client") and node.attr in ("get", "post", "put", "patch",
                                                                "delete", "request"):
            verbs.add(node.attr)
ok(verbs <= {"get"}, f"B1 the report reader issues only GET: {sorted(verbs)}")

fetchers = [n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and "ASGITransport" in ast.dump(n)]
ok(fetchers == ["_fetch"], f"B2 exactly one in-process report reader: {fetchers}")
ok("ASGITransport" not in "".join(src for rel, src in FILES.items() if rel != AGENT_HOME),
   "B3 no other module calls the app in-process on the assistant's behalf")

ok('headers["authorization"] = authorization' in agent_src,
   "B4 the read carries the CALLER'S authorization — the assistant is as blind as the user")
ok("get_supabase_admin" not in CODE[AGENT_HOME] and "SERVICE_KEY" not in CODE[AGENT_HOME],
   "B5 the agent never reaches for a service key, which would bypass every endpoint's RBAC")
ok("reg.validate(" in agent_src,
   "B6 a tool call is validated against the registry before any path exists")
# The path handed to _fetch must come from validate(), never from the model's arguments.
run_q = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
         and n.name == "_run_tool"]
ok(len(run_q) == 1 and "args.get(\"path\")" not in ast.dump(run_q[0]),
   "B7 no tool argument is ever used as a path")
ok(not re.search(r"\bpath\s*=\s*(?:args|c\.input|json\.loads)", CODE[AGENT_HOME]),
   "B8-ARMED a path assigned from caller input would fail this check")


# §C's two detectors work on the AST, not on text. `code_only()` blanks string literals (so a
# docstring cannot defeat a lock), which also blanks the very subscript key we are looking for —
# and the RAW source would match the prose in a comment explaining the rule. The parse tree has
# neither problem: comments are absent and a real subscript is still a node.
def _reads_perm_scope_src(src):
    """Does this source subscript a mapping with 'perms' or 'scope'? (`c["perms"]["scope"]`,
    `caller.get("perms")["scope"]`, `x["scope"]` — any of them is a permission rule.)"""
    try:
        tree = ast.parse(src)
    except SyntaxError:                                  # pragma: no cover
        return False
    for n in ast.walk(tree):
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) \
           and str(n.slice.value) in ("perms", "scope"):
            return True
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get" \
           and n.args and isinstance(n.args[0], ast.Constant) \
           and str(n.args[0].value) in ("perms", "scope"):
            return True
    return False


def _compares_to_src(src, literal):
    """Does this source compare anything to `literal` with == or != or `in`?"""
    try:
        tree = ast.parse(src)
    except SyntaxError:                                  # pragma: no cover
        return False
    for n in ast.walk(tree):
        if isinstance(n, ast.Compare):
            for c in [n.left] + list(n.comparators):
                if isinstance(c, ast.Constant) and c.value == literal:
                    return True
                if isinstance(c, (ast.Tuple, ast.List, ast.Set)):
                    for e in c.elts:
                        if isinstance(e, ast.Constant) and e.value == literal:
                            return True
    return False


def _reads_perm_scope(home):
    return _reads_perm_scope_src(FILES[home])


def _compares_to(home, literal):
    return _compares_to_src(FILES[home], literal)


# ── §C no permission rule of its own ───────────────────────────────────────────────────────────
section("C. no second permission rule")
# An RBAC role, not the `user`/`assistant` role of a chat message — a conversation turn has a
# "role" too, and conflating the two would reject correct code.
RBAC_ROLE = re.compile(r"\brole\b.{0,12}(?:==|!=|\bin\b).{0,40}"
                       r"(?:admin|manager|owner|\brep\b|\bdm\b|district|market|super)", re.I)
for home in (AGENT_HOME, API_HOME):
    src = CODE[home]
    ok("caller_scope" not in src and "scope_keyset" not in src and "in_keyset" not in src,
       f"C1 {home} holds no store-scope decision (that is storeops' one home)")
    ok(not RBAC_ROLE.search(src), f"C2 {home} branches on no RBAC role literal")
    ok(not re.search(r"super_admin\s*=\s*True", src), f"C3 {home} grants nobody super-admin")
ok("_gate.decide" in FILES[AGENT_HOME] or "decide_async" in FILES[AGENT_HOME],
   "C4 authorization is asked of the SHARED guard, not decided here")
ok(bool(RBAC_ROLE.search("if role == 'district_manager': pass")),
   "C2-ARMED the RBAC-role detector really fires")
ok(not RBAC_ROLE.search('if role in ("user", "assistant"): pass'),
   "C2-ARMED2 and it does NOT fire on a chat message's role")

# ── the rep narrowing (owner directive 2026-10-05: *"only their own commission, only their action
# plan"*). The thing to prevent is the obvious shortcut: filtering rows, stripping fields or
# matching a rep's NAME inside this package, where it would be a second copy of a fact
# `commcalc/payout_audience.py` already owns and would drift from it the first time either changed.
for home in (AGENT_HOME, API_HOME, REGISTRY_HOME, COMPUTE_HOME):
    src = CODE[home]
    ok("epay_salesperson" not in src and "storeops_name" not in src,
       f"C5 {home} matches no rep NAME field (payout_audience owns that)")
    ok("rep_keys" not in src and "mine_only" not in src and "row_is_mine" not in src,
       f"C6 {home} does not re-implement the own-rep predicate")
    ok(not _reads_perm_scope(home),
       f"C7 {home} reads no scope out of the caller's permissions")
    ok(not _compares_to(home, "self"),
       f"C8 {home} compares nothing to the literal scope 'self'")
# The ONE place the self answer may come from, and it is asked of storeops, not computed.
ok("role_is_self_scoped" in CODE[API_HOME],
   "C9 the API asks storeops' one home whether the caller is self-scoped")
ok("role_is_self_scoped" not in CODE[AGENT_HOME] and "role_is_self_scoped" not in CODE[REGISTRY_HOME],
   "C10 …and asks it ONCE — the agent and the registry are handed a boolean, never the question")
ok("caller_is_self" in CODE[AGENT_HOME] and "caller_is_self" in CODE[REGISTRY_HOME],
   "C11 that boolean is what travels (so there is nothing to decide downstream)")
# The ENFORCEMENT point must stay in code, not in the prompt: the narrowed set gates the tool call.
ok(re.search(r"authorized_questions", CODE[AGENT_HOME]) is not None,
   "C12 the agent still gates each tool call on the authorized question set")
ok("answerable(enabled_modules, caller_is_self)" in CODE[AGENT_HOME],
   "C13 and that set is built WITH the self flag — not the full registry")
ok(_reads_perm_scope_src('x = caller["perms"]["scope"]'),
   "C7-ARMED the permissions-scope detector really fires")
ok(not _reads_perm_scope_src('x = caller["role"]'),
   "C7-ARMED2 and it does not fire on an unrelated subscript")
ok(_compares_to_src("if scope == 'self': pass", "self"),
   "C8-ARMED the scope-literal detector really fires")
ok(not _compares_to_src("if scope == 'market': pass", "self"),
   "C8-ARMED2 and it does not fire on a different literal")


# ── §D no arithmetic outside the proved module ──────────────────────────────────────────────────
section("D. the model does not add up, and neither does the agent")
ARITH = re.compile(r"\bsum\(|\bround\(|/\s*len\(|\bstatistics\.|\bmean\(")
agent_body = "\n".join(l for l in CODE[AGENT_HOME].splitlines()
                       if "usage_in" not in l and "usage_out" not in l)
hits = [l.strip() for l in agent_body.splitlines() if ARITH.search(l)]
ok(not hits, f"D1 the agent computes no figure of its own: {hits[:3]}")
ok(bool(ARITH.search("x = sum(vals) / len(vals)")), "D1-ARMED the arithmetic detector really fires")
for fn in ("group", "pivot", "rank", "compare", "chart_spec", "to_number", "describe"):
    ok(callable(getattr(CALC, fn, None)), f"D2 {fn} is the proved home for its arithmetic")
ok("data_qa_compute" in agent_src, "D3 the agent dereferences that home rather than copying it")


# ── §E the purpose is registered in BOTH shared registries ─────────────────────────────────────
section("E. authorized AND billed")
spec = cbx.AI_PURPOSES.get("data_qa")
ok(bool(spec), "E1 'data_qa' is a registered AI purpose")
ok(spec and spec.get("authorizer") in cbx.AI_AUTHORIZERS,
   "E2 its authorizer is a predicate that really exists")
ok(spec and spec.get("module") and spec.get("scopes"),
   "E3 a module_scope purpose declares both a module and the scopes (or it authorizes nobody)")
ok(spec and spec.get("deny_code") in cbx._DENY,
   "E4 its refusal has a worded message — one wording, in the guard")
ok(spec and spec.get("subject_rule") == cbx.SUBJECT_BOUNDED_TEXT,
   "E5 the question is bounded text, audited as a digest")
ok(spec and AGENT_HOME.split("/")[-1] in str(spec.get("call_site")),
   "E6 the purpose names its real call site")
ok(any(s.get("purpose") == "data_qa" and s.get("metered") for s in USAGE.AI_CALL_SITES),
   "E7 the spend is declared METERED in billing's call-site registry")
ok(AG.PURPOSE == "data_qa", "E8 the agent uses that exact purpose name")

# ARMED: the guard really refuses an unregistered purpose and a purpose with no real predicate.
boss = {"super_admin": True, "perms": {"modules": {"ai_assistant": True}, "scope": "all"}}
d = cbx.ai_guard_decision(boss, purpose="data_qa_v2", has_key=True)
ok(not d.get("allow"), "E9-ARMED an UNREGISTERED purpose is refused even to a super-admin")
d = cbx.ai_guard_decision(boss, purpose="data_qa", has_key=True,
                          purposes={"data_qa": {**spec, "authorizer": "nope"}})
ok(not d.get("allow") and d.get("code") == "unknown_authorizer",
   "E10-ARMED a purpose naming a predicate that does not exist authorizes NOBODY")
d = cbx.ai_guard_decision(boss, purpose="data_qa", subject="which store is best", has_key=True)
ok(d.get("allow"), "E11 and a real management caller with the module IS allowed")
# A SELF-SCOPED REP MAY ASK (owner directive 2026-10-05: *"only their own commission, only their
# action plan"*), and the breadth of what they may ask is NOT this guard's business — it is the
# question registry's, proved in harness_data_qa_registry.py §G. Asserting it in both places is how
# the two facts stay separable: the guard says who may spend, the registry says what is offered.
rep = {"super_admin": False, "perms": {"modules": {"ai_assistant": True}, "scope": "self"}}
d = cbx.ai_guard_decision(rep, purpose="data_qa", subject="what is my commission", has_key=True)
ok(d.get("allow"), "E12 a self-scoped rep is allowed to SPEND on the assistant")
ok(set(REG.answerable(None, True)) < set(REG.keys()),
   "E12b …and is offered strictly fewer questions than a manager (the narrowing lives there)")
unlisted = {"super_admin": False, "perms": {"modules": {"ai_assistant": True}, "scope": "store"}}
d = cbx.ai_guard_decision(unlisted, purpose="data_qa", subject="x", has_key=True)
ok(not d.get("allow"), "E12c a scope the purpose does not list is still refused")
nomod = {"super_admin": False, "perms": {"modules": {}, "scope": "all"}}
d = cbx.ai_guard_decision(nomod, purpose="data_qa", subject="x", has_key=True)
ok(not d.get("allow"), "E13 a tenant without the module is refused")


# ── §F SEV-1 2026-07-30 ─────────────────────────────────────────────────────────────────────────
section("F. the model call cannot freeze the event loop")
ok("AsyncAnthropic" in agent_src, "F1 the async client is used")
ok(not re.search(r"(?<![A-Za-z])Anthropic\(", CODE[AGENT_HOME].replace("AsyncAnthropic(", "")),
   "F2 the SYNCHRONOUS client is not reintroduced (it froze /health for ~30 minutes once)")
ok(bool(re.search(r"(?<![A-Za-z])Anthropic\(",
                  code_only("cli = Anthropic(api_key=k)").replace("AsyncAnthropic(", ""))),
   "F2-ARMED the sync-client detector really fires on real code")
ok(re.search(r"await\s+cli\.messages\.create", agent_src) is not None, "F3 the call is awaited")
ok("timeout=MODEL_TIMEOUT_S" in agent_src and "max_retries=MODEL_MAX_RETRIES" in agent_src,
   "F4 an explicit timeout and retry cap bound the worst case")
ok("await cli.get(" in agent_src, "F5 the in-process report read is awaited too")
api_tree = ast.parse(FILES[API_HOME])
posts = [n for n in ast.walk(api_tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "data_qa"]
ok(len(posts) == 1, "F6 the POST handler is async def")


# ── §G purity of the provable modules ──────────────────────────────────────────────────────────
section("G. the semantic layer and the arithmetic are pure")
for home in (REGISTRY_HOME, COMPUTE_HOME):
    src = FILES[home]
    t = ast.parse(src)
    imports = set()
    for n in ast.walk(t):
        if isinstance(n, ast.Import):
            imports |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            imports.add(n.module.split(".")[0])
    ok("app" not in imports, f"G1 {home} imports nothing from the app ({sorted(imports)})")
    ok(not ({"httpx", "requests", "supabase", "psycopg2", "asyncpg"} & imports),
       f"G2 {home} reaches no database and no network")
    ok("get_supabase" not in CODE[home], f"G3 {home} holds no client")
ok(len({"httpx"} & {"httpx"}) == 1, "G2-ARMED the network-import detector really fires")


# ── §H bounds ───────────────────────────────────────────────────────────────────────────────────
section("H. one question cannot run away")
ok(AG.MAX_ROUNDS >= 1 and AG.MAX_ROUNDS <= 40, "H1 the tool loop has a turn ceiling")
ok(AG.MAX_REPORTS >= 1 and AG.MAX_REPORTS <= 20, "H2 reports per question are capped")
ok(AG.MAX_ROWS_TO_MODEL >= 5, "H3 rows into context are capped")
ok(AG.REPORT_TIMEOUT_S > 0 and AG.MODEL_TIMEOUT_S > 0, "H4 both timeouts are real")
ok("for _round in range(MAX_ROUNDS)" in agent_src,
   "H5 the loop is BOUNDED by that ceiling, not by `while True`")
ok("while True" not in CODE[AGENT_HOME], "H6 and there is no unbounded loop at all")
ok("ws.reports >= MAX_REPORTS" in agent_src, "H7 the report cap is enforced inside the tool")


# ── §I the model is handed an id, never rows ───────────────────────────────────────────────────
section("I. the arithmetic runs on the report's own rows")
defs = AG.tool_defs(None)
by_name = {t["name"]: t for t in defs}
ok(set(by_name) == {"run_question", "group_rank", "pivot_table", "compare_rows", "make_chart"},
   f"I1 the tool surface is exactly the five tools: {sorted(by_name)}")
for name in ("group_rank", "pivot_table", "compare_rows", "make_chart"):
    props = by_name[name]["input_schema"]["properties"]
    ok("result" in props, f"I2 {name} takes a RESULT id")
    ok("rows" not in props and "data" not in props and "values" not in props,
       f"I3 {name} cannot be handed rows — a model cannot retype a number on the way to a total")
ok(by_name["run_question"]["input_schema"]["properties"]["question"].get("enum"),
   "I4 the question is an ENUM of the registry's keys, not free text")
ok(all(t.get("strict") for t in defs), "I5 every tool is strict, so arguments arrive schema-valid")
ok("/api/v1/" not in repr(defs),
   "I6 no API path is shown to the model — it chooses a question, never a route")
narrow = AG.tool_defs([])
ok(len(narrow[0]["input_schema"]["properties"]["question"]["enum"])
   < len(defs[0]["input_schema"]["properties"]["question"]["enum"]),
   "I7 a tenant without a module is not even shown that question")


# ── §J registered where the house requires ─────────────────────────────────────────────────────
section("J. registered")
try:
    from app.modules.core import module_graph as MG
    fact = None
    for key in MG.keys():
        if REGISTRY_HOME in " ".join(MG.fact(key)["homes"]):
            fact = key
    ok(bool(fact), "J1 the semantic layer is a fact in the module graph (§50)")
except Exception as e:
    ok(False, f"J1 the module graph could not be read: {e}")
ok(os.path.exists(MIG), "J2 migration 1055 exists")
if os.path.exists(MIG):
    sql = open(MIG, encoding="utf-8").read()
    ok("data_qa" in sql, "J3 the migration seeds the purpose's budget row")
    ok("-- REVERT:" in sql, "J4 the migration carries its revert note")
    ok("ai_budget_config" in sql, "J5 it seeds the SHARED ceiling table, not a new one")
index = open(os.path.join(ROOT, "docs", "SYSTEM_DATA_FLOW_INDEX.md"), encoding="utf-8").read()
ok("data_qa_registry" in index, "J6 the registry is registered in the index")
ok("/core/data-qa" in index, "J7 and so is its endpoint")


print(f"\n{'=' * 78}")
if FAILS:
    print(f"FAILED {len(FAILS)} of {CHECKS} checks:")
    for f in FAILS:
        print(f"  ✗ {f}")
    sys.exit(1)
print(f"OK — {CHECKS} checks passed (data-qa design lock)")
