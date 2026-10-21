"""LOCK — a component that a user TYPES INTO is never declared inside another component
(owner report 2026-10-21, index §19.56).

Owner: "While create an event in the marketing menu, the user cannot type freely only letter at a
time is entered."

THE DEFECT. `frontend/src/app/(platform)/marketing/events/new/page.tsx` declared its `Field`
component INSIDE the page component's body. A function declared there is a NEW function identity on
every render, and React compares element types by REFERENCE: a different type means it UNMOUNTS the
old subtree and MOUNTS a fresh one instead of updating it. Typing a character set page state, the
page re-rendered, `Field` was a different type, and the very <input> being typed into was destroyed
and replaced — the browser dropped focus with it, and the next character went nowhere. State was
never lost; the DOM node was. Hence "only a letter at a time".

THE CLASS, not the field: ANY component declared inside another component that renders a control a
user types into. Four live instances, all fixed in the same change:
  marketing/events/new/page.tsx         `Field`          18 inputs on the create form
  crm/my-followups/page.tsx             `Section`        the outcome Note on a follow-up
  commcalc/settings/page.tsx            `Field`          every commission rate and KPI target
  closing/envelope-config/page.tsx      `CadenceFields`  day-of-month and biweekly anchor
and two more that render a typed control only THROUGH another component
  marketing/events/[id]/page.tsx        `Picker`         (select: no typing to lose, but it remounts)
  closing/envelope-config/page.tsx      `CfgCard`        renders CadenceFields

ONE HOME, DEREFERENCED: the marketing form controls (`Field`, `Area`, `Picker`) live at module scope
in `frontend/src/lib/marketingForm.tsx`, and both marketing event pages READ them rather than keeping
a copy each — the page that holds a form is not the place that decides what a form control is.

WHAT THIS LOCK FAILS ON
  1. a component declared inside another component, in any .tsx under frontend/src, that renders a
     control a user types into — an <input> whose type is not one of the click-only kinds, or a
     <textarea> — DIRECTLY, or through another component it renders that does (resolved through the
     file's own declarations and its `@/…` imports, to a fixpoint);
  2. either marketing event page declaring its own `Field` / `Area` / `Picker` again instead of
     dereferencing `lib/marketingForm.tsx` (the second copy that would diverge);
  3. the one home not existing, or not exporting all three at module scope;
  4. this lock not being run by a workflow (an unenforced lock is not a lock).

The detectors are not vacuous: §5 plants the four real pre-fix shapes and the near-misses that must
NOT fail (a click-only input, a nested display-only component, a module-scope component).

Run it against a checkout of the tree before the fix and rule 1 names all four:
    INNER_COMPONENT_ROOT=<a checkout of main> python3 backend/harness_inner_component_inputs.py
Stdlib only, DB-free: `python3 backend/harness_inner_component_inputs.py`.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.environ.get("INNER_COMPONENT_ROOT") or os.path.join(HERE, ".."))
FRONT = os.path.join(ROOT, "frontend")
SRC = os.path.join(FRONT, "src")
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")

HOME = "src/lib/marketingForm.tsx"
HOME_EXPORTS = ("Field", "Area", "Picker")
MARKETING_PAGES = (
    "src/app/(platform)/marketing/events/new/page.tsx",
    "src/app/(platform)/marketing/events/[id]/page.tsx",
)

# An <input> of one of these types is clicked or picked, never typed into: remounting it costs a
# render, not a keystroke. Everything else (text, number, date, datetime-local, email, search, tel,
# password, url, time, month, week, or no type at all) is typed into.
CLICK_ONLY = {
    "checkbox", "radio", "button", "submit", "reset", "file", "hidden", "image", "color", "range",
}

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


def read(rel, base=FRONT):
    try:
        with open(os.path.join(base, rel), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


# ── the parser ────────────────────────────────────────────────────────────────────────────────────
# A component-shaped declaration: `function Name(`, `const Name = (`, `const Name = function`,
# `const Name = x =>`, with or without `export` / `export default` and a type annotation. The leading
# whitespace is what decides nesting: a declaration indented at all is inside something else.
DECL = re.compile(
    r"^(?P<indent>[ \t]*)(?:export\s+)?(?:default\s+)?"
    r"(?:function\s+(?P<fn>[A-Z][A-Za-z0-9_]*)\s*[(<]"
    r"|const\s+(?P<cn>[A-Z][A-Za-z0-9_]*)\s*(?::[^=]*?)?=\s*(?:async\s*)?"
    r"(?:\(|function\b|[A-Za-z_$][\w$]*\s*=>))"
)
TAG = re.compile(r"<(input|textarea)\b")
RENDERS = re.compile(r"<([A-Z][A-Za-z0-9_]*)[\s/>]")
IMPORT = re.compile(r"""import\s+(?:type\s+)?\{([^}]*)\}\s+from\s+['"](@/[^'"]+)['"]""")


def strip_noise(line):
    """The line with string literals and a trailing // comment removed, so delimiter counting is not
    thrown off by a brace inside a quoted string."""
    out, i, quote = [], 0, None
    while i < len(line):
        c = line[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"`":
            quote = c
            i += 1
            continue
        if c == "/" and i + 1 < len(line) and line[i + 1] == "/":
            break
        out.append(c)
        i += 1
    return "".join(out)


def extent(lines, start):
    """Last line index of the declaration beginning at `start`, by delimiter balance — the arrow's
    closing `)` or the function's closing `}`. Balance, not a fixed window: a window bleeds into the
    next declaration and flags components that hold nothing."""
    bal, opened = 0, False
    for i in range(start, len(lines)):
        for c in strip_noise(lines[i]):
            if c in "([{":
                bal += 1
                opened = True
            elif c in ")]}":
                bal -= 1
        if opened and bal <= 0:
            return i
    return len(lines) - 1


def typed_controls(text):
    """Every control a user types into: a <textarea>, or an <input> whose type is not click-only. No
    type at all means type="text". A computed `type={…}` is counted as typed — the conservative read."""
    hits = []
    for m in TAG.finditer(text):
        if m.group(1) == "textarea":
            hits.append(("textarea", m.start()))
            continue
        tag = text[m.start(): m.start() + 800]
        end = tag.find(">")
        if end != -1:
            tag = tag[:end]
        t = re.search(r"""type\s*=\s*(?:["']([A-Za-z-]+)["']|\{)""", tag)
        if t and t.group(1) and t.group(1).lower() in CLICK_ONLY:
            continue
        hits.append(("input", m.start()))
    return hits


def declarations(src):
    """[(name, first_line, last_line, nested, body)] for every component-shaped declaration."""
    lines = src.split("\n")
    out = []
    for i, line in enumerate(lines):
        m = DECL.match(line)
        if not m:
            continue
        j = extent(lines, i)
        out.append((m.group("fn") or m.group("cn"), i + 1, j + 1, bool(m.group("indent")),
                    "\n".join(lines[i:j + 1])))
    return out


def _module_path(spec):
    """`@/lib/marketingForm` → an absolute path, or None."""
    base = os.path.join(SRC, spec[2:])
    for cand in (base + ".tsx", base + ".ts", os.path.join(base, "index.tsx")):
        if os.path.isfile(cand):
            return cand
    return None


def typed_names(src, seen=frozenset()):
    """Names declared in this source that render a control a user types into — directly, or through a
    component they render. Resolution walks this module's own declarations AND its `@/…` imports, to a
    fixpoint, so a component that holds nothing but <OtherThing /> is caught by what OtherThing holds."""
    decls = declarations(src)
    typed = {name for name, _a, _b, _n, body in decls if typed_controls(body)}

    imported = set()
    for names, spec in IMPORT.findall(src):
        path = _module_path(spec)
        if not path or os.path.abspath(path) in seen:
            continue
        theirs = typed_module_names(path, seen)
        for raw in names.split(","):
            n = raw.strip().split(" as ")[-1].strip()
            n = n[5:].strip() if n.startswith("type ") else n
            if n in theirs:
                imported.add(n)

    changed = True
    while changed:
        changed = False
        for name, _a, _b, _n, body in decls:
            if name in typed:
                continue
            if (set(RENDERS.findall(body)) - {name}) & (typed | imported):
                typed.add(name)
                changed = True
    return typed


def typed_module_names(path, seen=frozenset(), cache={}):
    """typed_names for a module on disk, memoised, with an import cycle reading as 'nothing yet'."""
    path = os.path.abspath(path)
    if path in cache:
        return cache[path]
    try:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
    except OSError:
        return set()
    cache[path] = set()
    cache[path] = typed_names(src, seen | {path})
    return cache[path]


def nested_typed(src, path=None):
    """[(name, line)] — every NESTED declaration in `src` that reaches a typed control. `path` is only
    used to resolve this module's own imports (and to not re-read itself)."""
    seen = frozenset({os.path.abspath(path)}) if path else frozenset()
    typed = typed_names(src, seen)
    return [(n, a) for n, a, _b, nested, _body in declarations(src) if nested and n in typed]


def tsx_files():
    out = {}
    for dp, dn, fn in os.walk(SRC):
        dn[:] = [d for d in dn if d not in ("node_modules", ".next")]
        for f in sorted(fn):
            if f.endswith(".tsx"):
                p = os.path.join(dp, f)
                with open(p, encoding="utf-8") as fh:
                    out[os.path.relpath(p, FRONT).replace(os.sep, "/")] = (p, fh.read())
    return out


# ── 1. nothing a user types into is declared inside another component ─────────────────────────────
print("1. no component that a user types into is declared inside another component")
files = tsx_files()
check("1a the frontend tree was found", len(files) > 50, f"{len(files)} .tsx files under {SRC}")
offenders = []
for rel, (path, src) in sorted(files.items()):
    for name, line in nested_typed(src, path):
        offenders.append(f"{rel}:{line} {name}")
check("1b no nested component reaches a control a user types into", not offenders,
      "; ".join(offenders) + " — each remounts its subtree on every render of the component it sits "
      "in, which destroys the focused input (owner report 2026-10-21)")

# ── 2-3. one home for the marketing form controls, dereferenced by both pages ─────────────────────
print("2. the marketing form controls have ONE home and both pages dereference it")
home = read(HOME)
check("3a the one home exists", home is not None, HOME)
for name in HOME_EXPORTS:
    check(f"3b {HOME} exports {name} at module scope",
          bool(home) and re.search(r"^export function %s\(" % name, home, re.M) is not None)
check("3c the one home holds no nested component with a control",
      bool(home) and not nested_typed(home, os.path.join(FRONT, HOME)))
for rel in MARKETING_PAGES:
    src = read(rel)
    check(f"2a {rel} was found", src is not None)
    if not src:
        continue
    check(f"2b {rel} imports the controls from the one home",
          "from '@/lib/marketingForm'" in src)
    for name in HOME_EXPORTS:
        second = re.search(r"^\s+(?:const|function)\s+%s\s*[=(]" % name, src, re.M)
        check(f"2c {rel} keeps no second copy of {name}", second is None,
              "a second copy diverges from the one home")

# ── 4. the lock is run by a workflow ──────────────────────────────────────────────────────────────
print("3. the lock is enforced (a lock no workflow runs is not a lock)")
me = os.path.basename(__file__)
wired = []
for f in sorted(os.listdir(WORKFLOWS)) if os.path.isdir(WORKFLOWS) else []:
    with open(os.path.join(WORKFLOWS, f), encoding="utf-8") as fh:
        if me in fh.read():
            wired.append(f)
check("4a a workflow runs this lock", bool(wired), f"no workflow mentions {me}")

# ── 5. planted controls — the detectors are not vacuous ───────────────────────────────────────────
print("4. planted controls (every detector is armed)")
PRE_FIX_FIELD = """export default function Page() {
  const [f, setF] = useState({ title: '' })
  const Field = ({ k, name }: { k: string; name: string }) => (
    <div>
      <span style={label}>{name}</span>
      <input style={input} value={f[k]} onChange={e => set(k, e.target.value)} />
    </div>
  )
  return <Field k="title" name="Event title" />
}
"""
check("5a the owner's pre-fix create form is detected",
      [n for n, _l in nested_typed(PRE_FIX_FIELD)] == ["Field"])
check("5b the same component at MODULE scope is not flagged",
      nested_typed(PRE_FIX_FIELD.replace("  const Field", "const Field")
                   .replace("      <span", "  <span").replace("      <input", "  <input")) == []
      or nested_typed("const Field = ({ k }: { k: string }) => (\n  <input value={k} />\n)\n") == [])
check("5c a nested textarea is detected", [n for n, _l in nested_typed(
    "function Page() {\n  const Notes = () => (\n    <textarea value={v} onChange={c} />\n  )\n"
    "  return <Notes />\n}\n")] == ["Notes"])
check("5d a nested click-only input is NOT flagged", nested_typed(
    "function Page() {\n  const Flag = () => (\n    <input type=\"checkbox\" checked={v} onChange={c} />\n  )\n"
    "  return <Flag />\n}\n") == [])
check("5e a nested display-only component is NOT flagged", nested_typed(
    "function Page() {\n  const Row = ({ k, v }: { k: string; v: string }) => (\n"
    "    <div style={{ ...cell }}>{k}: {v}</div>\n  )\n  return <Row k=\"a\" v=\"b\" />\n}\n") == [])
check("5f a nested component that only RENDERS a typed one is detected (the CfgCard shape)",
      sorted(n for n, _l in nested_typed(
          "function Page() {\n"
          "  const Cadence = () => (\n    <input type=\"number\" value={v} onChange={c} />\n  )\n"
          "  const Card = () => (\n    <div><Cadence /></div>\n  )\n  return <Card />\n}\n")) ==
      ["Cadence", "Card"])
check("5g the window does not bleed into the next declaration (a Tile is not flagged for its "
      "neighbour's input)", nested_typed(
    "function Page() {\n  const Tile = ({ n }: { n: string }) => (\n"
    "    <div style={{ ...panel }}>{n}</div>\n  )\n"
    "  const Note = () => (\n    <input value={v} onChange={c} />\n  )\n"
    "  return <><Tile n=\"x\" /><Note /></>\n}\n") == [("Note", 5)])
check("5h a brace inside a string literal does not end a declaration early",
      [n for n, _l in nested_typed(
          "function Page() {\n  const F = () => (\n    <div title=\"a } brace\">\n"
          "      <input value={v} onChange={c} />\n    </div>\n  )\n  return <F />\n}\n")] == ["F"])
check("5i a dynamic type={…} is read as typed", len(typed_controls('<input type={t} value={v} />')) == 1)
check("5j a nested arrow with no JSX at all is not flagged", nested_typed(
    "function Page() {\n  const Fmt = (n: number) => n.toFixed(2)\n  return <div>{Fmt(1)}</div>\n}\n") == [])
_home_typed = typed_module_names(os.path.join(FRONT, HOME))
check("5k the real one home resolves as a typed-control module (Picker is a <select>: nothing to type)",
      {"Field", "Area"} <= _home_typed and "Picker" not in _home_typed, sorted(_home_typed))
_IMPORTED_CASE = (
    "import { Field } from '@/lib/marketingForm'\n"
    "export default function Page() {\n"
    "  const Box = () => (\n    <div><Field name=\"a\" value={v} onChange={c} /></div>\n  )\n"
    "  return <Box />\n}\n")
check("5l a nested component rendering an IMPORTED typed control is detected (through the real "
      "lib/marketingForm)", [n for n, _l in nested_typed(_IMPORTED_CASE)] == ["Box"],
      nested_typed(_IMPORTED_CASE))
check("5m an import of a display-only component does not make its holder typed",
      nested_typed("import { SortableTh } from '@/components/SortableTh'\n"
                   "function Page() {\n  const H = () => (\n    <SortableTh field=\"a\" />\n  )\n"
                   "  return <H />\n}\n") == [])

print()
for p in PASS:
    print(f"  PASS  {p}")
for f in FAIL:
    print(f"  FAIL  {f}")
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
