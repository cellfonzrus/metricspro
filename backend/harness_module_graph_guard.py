#!/usr/bin/env python3
"""BUILD GUARD — the module graph is complete, current, and interlinked with the index.

Owner directive 2026-10-04: *"one hand does not talk to the other and we need to build a check
mechanism via the index and registry we created that everytime an update or extension of a module is
done all connected pieces get updated automatically, the tree should be interlinked properly."*

THE CLASS. A shared fact (which table is the live feed, has this alert been sent, what counts as a
new activation) gains a caller, or a second derivation, and nothing tells the author that other
pieces answer the same question. It is how the alert send record reached FOUR implementations
(index 15.1) and how three earlier registries were written and never wired (19.18).

WHAT THIS GUARD ENFORCES, and what each check would have caught:

  A  the caller snapshot in `module_graph.FACTS` matches the real import graph — a NEW connected
     piece fails the build, printing the siblings it now has to be true for. This is the mechanism
     the directive asks for: the other hand is NAMED, in CI, before the merge.
  B  the name each caller binds a home to has not drifted (an alias rename, or a caller that
     dropped the import while keeping its own copy, is a change to the wiring and is reviewed).
  C  no caller merely IMPORTS a home — it must USE it. A dead import is a caller that has gone
     back to deriving the fact itself.
  D  every declared home exists and is not a stub.
  E  INDEX INTERLINK — every index reference on a fact resolves to a real section of
     `docs/SYSTEM_DATA_FLOW_INDEX.md`. A fact documented nowhere is a fact the next person
     re-derives.
  F  LOCK INTERLINK — every lock a fact names exists AND is run by a workflow. A lock nothing runs
     is not a lock.
  G  one home answers one question; a home is never also a caller of its own fact.
  H  every `harness_*lock*.py` that declares its own backend `HOME` points at a home in the graph,
     or is excused here by name with a reason. This is what stops the graph becoming a sixteenth
     private copy of "what depends on what" (19.18: a registry written and not wired is not a fix).

  Z  ARMED CONTROLS — each check is re-run against a deliberately broken graph and must FAIL.
     A check that cannot fail is not a check (index 24).

Usage:
  python3 backend/harness_module_graph_guard.py              # the guard (stdlib only, no DB)
  python3 backend/harness_module_graph_guard.py --bless       # rewrite the caller snapshot
  python3 backend/harness_module_graph_guard.py --impact f... # "what else is wired to these files?"
"""
from __future__ import annotations

import ast
import copy
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(HERE, "app")
INDEX = os.path.join(ROOT, "docs", "SYSTEM_DATA_FLOW_INDEX.md")
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")

sys.path.insert(0, HERE)
from app.modules.core import module_graph as G  # noqa: E402  (the one home for the graph)

# Locks that declare a backend HOME which is deliberately NOT a graph home, each with why.
HOME_DECL_EXCUSED = {
    "harness_tender_vocab_lock.py":
        "its HOME is a VOCABULARY inside closing/router.py, not an importable one-home module — "
        "there is no import edge for the graph to derive",
    "harness_vendor_price_compare.py":
        "its HOME is a DIRECTORY (app/modules/supply), used to scope a file walk",
    "harness_operator_entry_enforcement.py":
        "its HOME is a test ORG UUID, not a path",
}
FRONTEND_HOME_RE = re.compile(r"\.(ts|tsx|js|jsx|mjs|cjs)\b")

failures: list = []
checks = 0


def check(ok: bool, label: str) -> bool:
    global checks
    checks += 1
    if not ok:
        failures.append(label)
    return ok


# ── the import graph, derived ────────────────────────────────────────────────────────────────────
def _py_files() -> list:
    out = []
    for d, _, fs in os.walk(APP):
        for f in fs:
            if f.endswith(".py"):
                out.append(os.path.relpath(os.path.join(d, f), HERE).replace(os.sep, "/"))
    return sorted(out)


def _module_of(path: str) -> str:
    return os.path.splitext(path)[0].replace("/", ".")


def _bindings(path: str) -> dict:
    """{imported dotted module: {names bound locally}} for one file, nested imports included."""
    try:
        tree = ast.parse(open(os.path.join(HERE, path), encoding="utf-8", errors="replace").read())
    except (SyntaxError, OSError):
        return {}
    pkg = os.path.dirname(path).replace("/", ".")
    found: dict = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                found.setdefault(a.name, set()).add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            if n.level:
                parts = pkg.split(".")
                base = ".".join(parts[: len(parts) - n.level + 1])
                base = f"{base}.{n.module}" if n.module else base
            else:
                base = n.module or ""
            for a in n.names:
                found.setdefault(f"{base}.{a.name}", set()).add(a.asname or a.name)
                found.setdefault(base, set()).add(a.asname or a.name)
    return found


def derive(files=None, cache=None) -> dict:
    """{fact key: {caller path: (bound names,)}} straight from the import statements."""
    files = files or _py_files()
    cache = cache if cache is not None else {f: _bindings(f) for f in files}
    out: dict = {}
    for key in G.keys():
        homes = set(G.homes_for(key))
        want = {_module_of(h) for h in homes}
        edges: dict = {}
        for f in files:
            if f in homes:
                continue
            names = set()
            for mod, bound in cache[f].items():
                if mod in want:
                    names |= bound
            if names:
                edges[f] = tuple(sorted(names))
        out[key] = edges
    return out


def _uses(path: str, names) -> bool:
    """Does `path` actually USE one of the names it bound — attribute, call or subscript?"""
    try:
        src = open(os.path.join(HERE, path), encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
    except (SyntaxError, OSError):
        return False
    want = set(names)
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in want:
            return True
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in want:
            return True
        if isinstance(n, ast.Name) and n.id in want and not isinstance(n.ctx, ast.Store):
            # a bare reference that is not the import binding itself (passed, returned, compared)
            return True
    return False


# ── A/B — the snapshot is complete and current ───────────────────────────────────────────────────
def check_snapshot(graph, derived) -> None:
    for key in sorted(graph.FACTS):
        want = derived.get(key, {})
        have = graph.FACTS[key]["callers"]
        new = sorted(set(want) - set(have))
        gone = sorted(set(have) - set(want))
        check(not new, f"A[{key}] NEW connected piece not in the graph: {', '.join(new)} — "
                       f"run --bless and check the siblings it must now be true for")
        check(not gone, f"A[{key}] caller in the graph no longer imports a home: {', '.join(gone)}")
        for f in sorted(set(want) & set(have)):
            check(tuple(have[f]) == tuple(want[f]),
                  f"B[{key}] {f} binds {want[f]}, the graph says {tuple(have[f])}")


# ── C — a caller USES the home, not merely imports it ────────────────────────────────────────────
def check_dereferenced(graph) -> None:
    for key in sorted(graph.FACTS):
        for f, names in sorted(graph.FACTS[key]["callers"].items()):
            if not os.path.exists(os.path.join(HERE, f)):
                check(False, f"C[{key}] caller {f} does not exist")
                continue
            check(_uses(f, names),
                  f"C[{key}] {f} imports the home as {names} but never uses it — a dead import is "
                  f"a caller that has gone back to deriving the fact itself")


# ── D/G — homes ──────────────────────────────────────────────────────────────────────────────────
def check_homes(graph) -> None:
    seen: dict = {}
    for key in sorted(graph.FACTS):
        homes = graph.FACTS[key]["homes"]
        check(bool(homes), f"D[{key}] names no home")
        for h in homes:
            p = os.path.join(HERE, h)
            check(os.path.exists(p) and os.path.getsize(p) > 400,
                  f"D[{key}] home {h} is missing or a stub")
            check(seen.setdefault(h, key) == key,
                  f"G[{key}] home {h} already answers '{seen.get(h)}' — one home, one question")
            check(h not in graph.FACTS[key]["callers"],
                  f"G[{key}] home {h} is also listed as a caller of its own fact")


# ── E — index interlink ──────────────────────────────────────────────────────────────────────────
def _index_sections() -> set:
    try:
        text = open(INDEX, encoding="utf-8", errors="replace").read()
    except OSError:
        return set()
    out = set()
    for m in re.finditer(r"^#{1,6}\s+§?\s*([0-9]+[a-z]*(?:\.[0-9]+)?)[.\s]", text, re.M):
        out.add(m.group(1))
    for m in re.finditer(r"^[-*]\s+\*\*§?\s*([0-9]+[a-z]*(?:\.[0-9]+)?)[.\s]", text, re.M):
        out.add(m.group(1))
    # §19's gap list writes its subsections as a line-leading "§19.43 **TITLE" rather than a heading
    for m in re.finditer(r"^§\s*([0-9]+[a-z]*(?:\.[0-9]+)?)\s+\*\*", text, re.M):
        out.add(m.group(1))
    return out


def check_index(graph, sections=None) -> None:
    sections = _index_sections() if sections is None else sections
    check(len(sections) > 50, "E the index yielded no sections — the parser or the file moved")
    for key in sorted(graph.FACTS):
        refs = graph.FACTS[key]["index"]
        check(bool(refs), f"E[{key}] names no index section")
        for r in refs:
            check(r in sections,
                  f"E[{key}] index section {r} does not resolve in SYSTEM_DATA_FLOW_INDEX.md")


# ── F — lock interlink ───────────────────────────────────────────────────────────────────────────
def _workflow_text() -> str:
    body = []
    if os.path.isdir(WORKFLOWS):
        for f in sorted(os.listdir(WORKFLOWS)):
            if f.endswith((".yml", ".yaml")):
                body.append(open(os.path.join(WORKFLOWS, f), encoding="utf-8",
                                 errors="replace").read())
    return "\n".join(body)


def check_locks(graph, wf=None) -> None:
    wf = _workflow_text() if wf is None else wf
    for key in sorted(graph.FACTS):
        locks = graph.FACTS[key]["locks"]
        check(bool(locks), f"F[{key}] names no lock")
        for l in locks:
            check(os.path.exists(os.path.join(HERE, l)), f"F[{key}] lock {l} does not exist")
            check(l in wf, f"F[{key}] lock {l} is not run by any workflow — a lock nothing runs "
                           f"is not a lock")


# ── H — no lock keeps a private HOME; the ones the graph names dereference it ────────────────────
def _home_decls(path: str):
    """(path literals ending .py, all literals) from a harness's HOME/HOMES declaration."""
    src = open(path, encoding="utf-8", errors="replace").read()
    decls = re.findall(r"^\s*(?:HOME|HOMES|ONE_HOME|HOME_FILE)\s*=\s*(.+)$", src, re.M)
    lits: list = []
    for d in decls:
        lits += re.findall(r'"([^"]+)"', d) + re.findall(r"'([^']+)'", d)
    joined = re.findall(r"^\s*(?:HOME|HOMES|ONE_HOME|HOME_FILE)\s*=[\s\S]{0,400}?\)", src, re.M)
    for j in joined:
        lits += re.findall(r'"([^"]+)"', j) + re.findall(r"'([^']+)'", j)
    return bool(decls), [x for x in lits if x.endswith(".py")], lits, src


def check_lock_homes(graph, excused=None) -> None:
    """H1 a backend HOME literal must BE a graph home (or the lock is excused by name with a
    reason); H2 a lock the graph names, which declares a HOME at all, must dereference the graph
    rather than keep its own copy of the ruling. H2 is the anti-un-wiring for the graph itself —
    writing a registry and leaving the callers on their literals is not a fix (index 19.18)."""
    excused = HOME_DECL_EXCUSED if excused is None else excused
    tails = {h.split("app/", 1)[-1] for h in graph.all_homes()}
    named = {l for k in graph.keys() for l in graph.FACTS[k]["locks"]}
    for f in sorted(os.listdir(HERE)):
        if not (f.startswith("harness_") and f.endswith(".py")):
            continue
        has_decl, paths, lits, src = _home_decls(os.path.join(HERE, f))
        if not has_decl:
            continue
        if FRONTEND_HOME_RE.search(" ".join(lits)):
            continue                                  # a frontend home; the graph is backend-only
        if f in excused:
            check(bool(str(excused[f]).strip()), f"H[{f}] is excused with no reason")
            continue
        for p in paths:
            check(p.split("app/", 1)[-1] in tails,
                  f"H1[{f}] declares HOME {p}, which is not a home in module_graph — register the "
                  f"fact it owns, or excuse this lock by name with a reason")
        if f in named or paths:
            check("module_graph" in src,
                  f"H2[{f}] declares its own HOME instead of dereferencing module_graph — the "
                  f"ruling has one home and the locks read it (index 19.18)")


# ── Z — armed controls ───────────────────────────────────────────────────────────────────────────
class _Graph:
    """A mutable stand-in for the module, so a check can be run against a broken graph."""

    def __init__(self, facts):
        self.FACTS = facts

    def keys(self):
        return tuple(sorted(self.FACTS))

    def homes_for(self, k):
        return tuple(self.FACTS[k]["homes"])

    def all_homes(self):
        return {h: k for k in self.keys() for h in self.FACTS[k]["homes"]}


def _fails(fn, *a, **kw) -> bool:
    """True when `fn` records at least one failure — an armed control."""
    global failures, checks
    keep_f, keep_c = failures, checks
    failures, checks = [], 0
    try:
        fn(*a, **kw)
        broke = bool(failures)
    finally:
        failures, checks = keep_f, keep_c
    return broke


def armed_controls(derived) -> None:
    global HERE
    key = sorted(G.FACTS)[0]
    base = copy.deepcopy(dict(G.FACTS))

    g = _Graph(copy.deepcopy(base))
    g.FACTS[key]["callers"].pop(sorted(g.FACTS[key]["callers"])[0], None)
    check(_fails(check_snapshot, g, derived), "Z-A a dropped caller must fail check A")

    g = _Graph(copy.deepcopy(base))
    first = sorted(g.FACTS[key]["callers"])[0]
    g.FACTS[key]["callers"][first] = ("_definitely_not_the_bound_name",)
    check(_fails(check_snapshot, g, derived), "Z-B a drifted bound name must fail check B")

    g = _Graph(copy.deepcopy(base))
    g.FACTS[key]["callers"]["app/modules/core/module_graph.py"] = ("alert_log",)
    check(_fails(check_dereferenced, g), "Z-C a caller that does not use the home must fail check C")

    g = _Graph(copy.deepcopy(base))
    g.FACTS[key]["homes"] = ("app/modules/does/not/exist.py",)
    check(_fails(check_homes, g), "Z-D a missing home must fail check D")

    g = _Graph(copy.deepcopy(base))
    other = sorted(G.FACTS)[1]
    g.FACTS[other]["homes"] = tuple(g.FACTS[key]["homes"])
    check(_fails(check_homes, g), "Z-G one home under two facts must fail check G")

    g = _Graph(copy.deepcopy(base))
    g.FACTS[key]["index"] = ("99999.9",)
    check(_fails(check_index, g), "Z-E an unresolvable index section must fail check E")

    g = _Graph(copy.deepcopy(base))
    g.FACTS[key]["locks"] = ("harness_does_not_exist.py",)
    check(_fails(check_locks, g), "Z-F a missing lock must fail check F")

    g = _Graph(copy.deepcopy(base))
    g.FACTS[key]["locks"] = ("harness_module_graph_guard.py",)
    check(_fails(check_locks, g, wf="nothing runs anything"),
          "Z-F2 a lock no workflow runs must fail check F")

    # H1 — a backend HOME literal that is not a graph home. The three excused locks are exactly
    # that case, so clearing the excuse list must fail.
    check(_fails(check_lock_homes, _Graph(copy.deepcopy(base)), excused={}),
          "Z-H1 an unexcused lock HOME outside the graph must fail check H1")
    # H2 — a lock the graph names that stops dereferencing it. Re-run H over a temporary copy of a
    # wired lock with the import stripped out.
    import tempfile
    wired = os.path.join(HERE, "harness_line_class_lock.py")
    body = open(wired, encoding="utf-8").read()
    check("module_graph" in body, "Z-H2a harness_line_class_lock.py must dereference the graph")
    with tempfile.TemporaryDirectory() as td:
        broken = os.path.join(td, "harness_unwired_control.py")
        open(broken, "w", encoding="utf-8").write(
            body.replace("module_graph", "NOT_THE_GRAPH") .replace(
                'HOME = home_under_app("line_class")', 'HOME = "modules/commcalc/line_class.py"'))
        keep = HERE
        try:
            HERE = td
            check(_fails(check_lock_homes, _Graph(copy.deepcopy(base))),
                  "Z-H2 a lock that stops dereferencing the graph must fail check H2")
        finally:
            HERE = keep


# ── --bless ──────────────────────────────────────────────────────────────────────────────────────
def bless(derived) -> int:
    path = os.path.join(APP, "modules", "core", "module_graph.py")
    src = open(path, encoding="utf-8").read()
    changed = 0
    for key in sorted(G.FACTS):
        block = ["        \"callers\": {"]
        for f, names in sorted(derived.get(key, {}).items()):
            nm = ", ".join(repr(n) for n in names)
            block.append("            %r: (%s%s)," % (f, nm, "," if len(names) == 1 else ""))
        block.append("        },")
        new = "\n".join(block)
        pat = re.compile(r'(    %r: \{.*?"callers": \{).*?(\n        \},)' % key, re.S)
        m = pat.search(src)
        if not m:
            print(f"  !! could not locate the callers block for {key}", file=sys.stderr)
            continue
        old = src[m.start():m.end()]
        rebuilt = old[:old.index('        "callers": {')] + new
        if old != rebuilt:
            src = src[:m.start()] + rebuilt + src[m.end():]
            changed += 1
    open(path, "w", encoding="utf-8").write(src)
    print(f"blessed: {changed} fact(s) rewritten in app/modules/core/module_graph.py")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--impact":
        print(G.impact_report(a.replace(os.sep, "/").removeprefix("backend/") for a in args[1:]))
        return 0
    derived = derive()
    if args and args[0] == "--bless":
        return bless(derived)

    check_snapshot(G, derived)
    check_dereferenced(G)
    check_homes(G)
    check_index(G)
    check_locks(G)
    check_lock_homes(G)
    armed_controls(derived)

    edges = sum(len(G.FACTS[k]["callers"]) for k in G.keys())
    print(f"module graph: {len(G.keys())} facts, {edges} edges, {checks} checks")
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK — the graph is complete, current and interlinked with the index.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
