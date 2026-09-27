"""LOCK — no function under backend/app reads a name that nothing defines (the pyflakes F821 class), stdlib only.

THE CLASS (2026-09-27, index §6k). #244 moved a local (`_id_map`) into a helper; a sibling caller,
`POST /commcalc/recompute-rep`, kept reading it and died with NameError on EVERY call for ten days. Python only
finds such a name when the line RUNS, and no harness ran that line. This lock finds it without running anything:
for every scope of every module (`symtable`, the compiler's own symbol tables), a name that is READ, resolves to
module level (neither local nor enclosing), and is not bound at module level (assignment, def, class, import,
`global` declaration) nor a builtin / module dunder, is an UNDEFINED NAME — a NameError waiting for its line.

A module with a `from x import *` (none today) cannot be judged and is reported as such. A name that is genuinely
dynamic goes into ALLOWED with the reason.

  (a) no undefined name in any module under backend/app;
  (b) negative controls — a planted undefined name (a function reading a local another function owns — the
      #244 shape; a class body; a comprehension) turns this lock RED, and a correctly-bound one does not.

    python3 backend/harness_undefined_names_lock.py
"""
import builtins
import os
import re
import symtable
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "app")
MODULE_DUNDERS = {"__name__", "__file__", "__doc__", "__package__", "__spec__", "__loader__", "__builtins__",
                  "__path__", "__annotations__", "__dict__", "__cached__", "__qualname__", "__module__",
                  "__class__"}
BUILTINS = set(dir(builtins))
# (relative path, name) → why it is not a defect
ALLOWED = {}
P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:1500]))


def undefined(src, filename="<src>"):
    """[(scope name, line of the scope, name)] — names read in some scope that resolve to module level and are
    bound nowhere at module level. `None` when the module star-imports (cannot be judged)."""
    if re.search(r"^\s*from\s+\S+\s+import\s+\*", src, re.M):
        return None
    top = symtable.symtable(src, filename, "exec")
    bound = set()
    for s in top.get_symbols():
        if s.is_assigned() or s.is_imported() or s.is_namespace() or s.is_declared_global():
            bound.add(s.get_name())

    # a `global x` + assignment inside a function binds x at module level too
    def collect_globals(t):
        for s in t.get_symbols():
            if s.is_declared_global() and s.is_assigned():
                bound.add(s.get_name())
        for c in t.get_children():
            collect_globals(c)
    collect_globals(top)
    out = []

    def walk(t):
        for s in t.get_symbols():
            n = s.get_name()
            if not s.is_referenced():
                continue
            if t.get_type() == "module":
                resolves_global = not (s.is_assigned() or s.is_imported() or s.is_namespace())
            else:
                resolves_global = s.is_global() and not s.is_declared_global() or (
                    s.is_declared_global() and n not in bound)
                if t.get_type() == "class" and not (s.is_assigned() or s.is_imported() or s.is_namespace()
                                                    or s.is_free()):
                    resolves_global = True
            if resolves_global and n not in bound and n not in BUILTINS and n not in MODULE_DUNDERS:
                out.append((t.get_name(), t.get_lineno(), n))
        for c in t.get_children():
            walk(c)
    walk(top)
    return out


def scan():
    hits, unjudged = [], []
    for dp, dirs, fs in os.walk(APP):
        dirs[:] = [d for d in dirs if d not in ("__pycache__",)]
        for f in fs:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, APP).replace(os.sep, "/")
            src = open(p, encoding="utf-8").read()
            try:
                u = undefined(src, p)
            except SyntaxError as e:
                hits.append((rel, "<parse>", 0, str(e)))
                continue
            if u is None:
                unjudged.append(rel)
                continue
            for scope, line, name in u:
                if (rel, name) not in ALLOWED:
                    hits.append((rel, scope, line, name))
    return hits, unjudged


hits, unjudged = scan()
check("(a) no function, class or module under backend/app reads a name nothing defines (%d hits)" % len(hits),
      not hits, ["%s: %s (scope line %d) reads undefined %r" % h for h in hits])
check("(a2) every module can be judged (no star import)", not unjudged, unjudged)

print("\n  negative controls")
SHAPE_244 = '''
def _resolve(client):
    _id_map = {}
    return _id_map

def recompute(rep):
    keys = {rep}
    _bridged = _id_map.get(rep)
    return keys, _bridged
'''
check("(b) the #244 shape — a local of one function read by another → RED",
      [h[2] for h in undefined(SHAPE_244)] == ["_id_map"], undefined(SHAPE_244))
check("(b) a class body reading an undefined name → RED",
      [h[2] for h in undefined("class K:\n    x = undefined_thing\n")] == ["undefined_thing"])
check("(b) a comprehension reading an undefined name → RED",
      [h[2] for h in undefined("def f(xs):\n    return [x + offset for x in xs]\n")] == ["offset"])
OK_SRC = '''
import os as _os
from x import y
G = 1
def a(v):
    global H
    H = v
    return [G + z for z in range(v)] + [y, _os, len, __name__]
def b():
    return H + a(1)[0]
class C:
    k = G
    def m(self):
        return C, self.k, b
def outer():
    q = 1
    def inner():
        return q
    return inner
'''
check("(b) correctly bound names (import, global, closure, builtins, class, def) → GREEN", undefined(OK_SRC) == [],
      undefined(OK_SRC))
check("(b) a star-importing module is reported as not judgeable, never silently passed",
      undefined("from os import *\nprint(path)\n") is None)

print("\n%d passed, %d failed" % (P, F))
if F:
    sys.exit(1)
print("OK — no undefined name under backend/app; a moved local cannot strand its readers again.")
