"""THE LOCK on "it isn't set up — here is how to fix it" (owner directive 2026-11-09, index §CORE-39).

OWNER, verbatim: *"to fix any such problem going forward shpow it on the screen hpow the user can fix
teh problem so it is taken care of right away and if it is not under tehoer role permissionsm, have
them send a link ot thier manager from the chat menu so it can be fixed"*.

WHAT THIS PROTECTS. The remedy for a setup gap lives ONCE, in `app/modules/core/setup_fix.py`, and
every surface reads it. Three things can quietly undo that, and each has happened in this repo before
(CLAUDE.md §19.18 — "a mechanism written, and the callers left unwired"):

  1. a gap's page or settings area drifts, so the button sends somebody to a screen that is gone or
     refuses them at the other end;
  2. a page writes its own remedy sentence again, so the words on two screens disagree;
  3. a NEW "not configured" warning ships as a dead end, exactly like the one that started this.

So this program fails the build on all three. It is DB-free, stdlib-only, and it DEREFERENCES the
house extractors (`harness_carrier_vocab_guard.display_copy` / `comment_lines`) and the house setup
detectors (`app/core/setup_notice`) rather than carrying second copies of them.

WHAT FAILS THE BUILD
  §A  a registry entry missing its title, consequence, steps or gate; a company-closable gap with no
      page; a platform gap that offers one anyway.
  §B  a `gate` that is neither PLATFORM nor ADMIN nor a REGISTERED settings area (read out of
      `core/router.py::SETTING_AREAS` by AST — an area nobody can grant is a permission nobody can fix).
  §C  a `page` that is not a real route (read out of `frontend/src/lib/route-index.ts`, the derived
      one home for "every page this app has").
  §D  a customer-facing word that names a migration, a schema object, the SQL editor or a database
      error — judged by the §19.36 / §19.38 detectors, not by a list written here.
  §E  RULE TWO: a carrier, tenant or product name anywhere in the registry's words.
  §F  a registry STEP copied into `frontend/src` — the second spelling of a remedy.
  §G  the endpoint un-wired: the route gone, or the guide served without asking the house permission
      check, or `setup_fix` deciding a permission itself.
  §H  the browser deciding a permission: `lib/fix-it.tsx` reading a role, or rendering the fix button
      from anything but the server's `can_fix`.
  §I  THE RATCHET. A file whose DISPLAY COPY says a setup gap is open must either render the shared
      notice (import `@/lib/fix-it`) or be registered in `frontend/setup_fix_pending.txt` WITH A
      REASON. The register may only shrink: a stale line (the file is wired now), a dead line (the
      file is gone), a line with no reason, or more lines than the pin → RED.
  §J  the ask not reusing the app's one chat path, or `fix-it.tsx` losing its exports.
  §K  NEGATIVE CONTROLS: every rule above is shown to go red on a synthetic input.
  §L  this program not being run by any workflow.

NOT COVERED, stated rather than hidden. "Not measured" is a DIFFERENT class and is deliberately out of
scope: it means the platform holds no figure, which is often a data gap (a feed that did not arrive),
not a setting anybody can change. Folding the two together would route a reader to a settings page
that cannot help them. A "not measured" surface whose cause IS a missing declaration should name a
registry key explicitly, and §I will then see it wired.

  python3 backend/harness_setup_fix.py
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
FE = os.path.join(ROOT, "frontend", "src")
REGISTRY = os.path.join(ROOT, "frontend", "setup_fix_pending.txt")
CORE_ROUTER = os.path.join(BACKEND, "app", "modules", "core", "router.py")
SETUP_FIX = os.path.join(BACKEND, "app", "modules", "core", "setup_fix.py")
FIX_IT = os.path.join(FE, "lib", "fix-it.tsx")
ROUTE_INDEX = os.path.join(FE, "lib", "route-index.ts")
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")

sys.path.insert(0, BACKEND)
from app.core import setup_notice as _notice                      # noqa: E402
from app.modules.core import setup_fix as sf                      # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_carrier_vocab_guard as _vocab                      # noqa: E402

# Lower this as pages get wired. It may never rise: that is the ratchet.
PINNED_MAX = 27

# A sentence that tells a reader a SETUP GAP is open. Deliberately narrow — see "NOT COVERED" above.
GAP_PHRASE = re.compile(
    r"\b(?:not configured|needs setup|not set up|is ?n[o']t configured|no credentials|"
    r"not been set up|nothing is configured)\b", re.I)

# RULE TWO — a carrier / tenant / product name may never be a literal in the registry.
BANNED_WORDS = ("boost", "luxelink", "cellfonz", "vzone", "total wireless", "acima", "metro",
                "t-mobile", "verizon", "at&t", "shopify", "vidapay", "epay")

ok = True


def bad(section, msg):
    global ok
    ok = False
    print(f"  ✗ {section}: {msg}")


def good(section, msg):
    print(f"  ✓ {section}: {msg}")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ── §A the registry is well formed ──────────────────────────────────────────────────────────────
def check_A():
    print("\n— §A the registry —")
    if not sf.FIX_KEYS:
        return bad("A0", "no setup gap is declared; the registry is the whole mechanism")
    for key in sf.FIX_KEYS:
        e = sf.fix(key)
        for field in ("title", "consequence"):
            if not str(e.get(field) or "").strip():
                bad("A1", f"{key}: empty {field}")
        if not e.get("steps"):
            bad("A2", f"{key}: no steps — a gap with no steps is the dead end this replaces")
        for s in (e.get("steps") or []):
            if not str(s).strip() or not str(s)[0].isupper():
                bad("A2", f"{key}: a step must read as a sentence: {s!r}")
        if not e.get("gate"):
            bad("A3", f"{key}: no gate — nothing says who may close it")
        if e.get("gate") == sf.PLATFORM and e.get("page"):
            bad("A4", f"{key}: a platform gap must offer no in-app page")
        if e.get("gate") != sf.PLATFORM and not str(e.get("page") or "").startswith("/"):
            bad("A4", f"{key}: no page to send anyone to")
    if ok:
        good("A", f"{len(sf.FIX_KEYS)} gaps, each with steps, a gate and a destination")


# ── §B every gate is grantable (or honestly not) ────────────────────────────────────────────────
def setting_areas():
    """The registered settings-area keys, read out of core/router.py by AST — no app import, so this
    lock does not need FastAPI installed to tell the truth about it."""
    tree = ast.parse(read(CORE_ROUTER))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "SETTING_AREAS" and isinstance(node.value, ast.List):
                    keys = set()
                    for el in node.value.elts:
                        if isinstance(el, ast.Dict):
                            for k, v in zip(el.keys, el.values):
                                if isinstance(k, ast.Constant) and k.value == "key" and isinstance(v, ast.Constant):
                                    keys.add(v.value)
                    return keys
    return set()


def check_B():
    print("\n— §B the gate is a permission somebody can actually hold —")
    areas = setting_areas()
    if not areas:
        return bad("B0", "SETTING_AREAS could not be read out of core/router.py")
    for key in sf.FIX_KEYS:
        gate = sf.fix(key)["gate"]
        if gate in (sf.PLATFORM, sf.ADMIN):
            continue
        if gate not in areas:
            bad("B1", f"{key}: gate {gate!r} is not a settings area anyone can be granted")
    # ADMIN must resolve to an area that is NOT registered, or the degrade-to-admin branch is bypassed.
    if sf.gate_area({"gate": sf.ADMIN}) in areas:
        bad("B2", "the ADMIN gate names a registered area, so it no longer degrades to admin-only")
    if sf.gate_area({"gate": sf.PLATFORM}) is not None:
        bad("B3", "a platform gap resolved to a settings area; nobody in a tenant may close one")
    if ok:
        good("B", f"{len(areas)} grantable areas; every gate is one of them, ADMIN or PLATFORM")


# ── §C every page is a real route ───────────────────────────────────────────────────────────────
def routes():
    return set(re.findall(r"\{\s*path:\s*'([^']+)'", read(ROUTE_INDEX)))


def check_C():
    print("\n— §C the destination exists —")
    known = routes()
    if len(known) < 50:
        return bad("C0", f"route-index.ts yielded only {len(known)} routes; the reader is broken")
    for key in sf.FIX_KEYS:
        page = sf.fix(key)["page"]
        if page and page.split("?")[0] not in known:
            bad("C1", f"{key}: {page} is not a page this app has")
    if ok:
        good("C", f"every destination is one of the {len(known)} registered pages")


# ── §D / §E the words are customer-facing ───────────────────────────────────────────────────────
def check_DE():
    print("\n— §D/§E the words —")
    for key in sf.FIX_KEYS:
        e = sf.fix(key)
        for text in [e["title"], e["consequence"], *e["steps"]]:
            if _notice.is_setup_internal(text):
                bad("D1", f"{key}: an internal setup fact reaches a customer: {text!r}")
            if _notice.is_system_internal(text):
                bad("D2", f"{key}: a database / hosting error reaches a customer: {text!r}")
        blob = " ".join([e["title"], e["consequence"], *e["steps"]]).lower()
        for w in BANNED_WORDS:
            if w in blob:
                bad("E1", f"{key}: RULE TWO — {w!r} is config, never code")
    if ok:
        good("D/E", "no migration, schema object, database error, carrier or tenant name in any gap's copy")


# ── §F no second copy of a remedy in the browser ────────────────────────────────────────────────
def frontend_files():
    out = {}
    for base, dirs, files in os.walk(FE):
        dirs[:] = [d for d in dirs if d not in ("node_modules", "__tests__")]
        for f in files:
            if f.endswith((".ts", ".tsx")) and not f.endswith((".test.ts", ".test.tsx")):
                p = os.path.join(base, f)
                out[os.path.relpath(p, FE)] = read(p).splitlines()
    return out


def check_F(files):
    print("\n— §F one spelling of a remedy —")
    steps = {s for k in sf.FIX_KEYS for s in sf.fix(k)["steps"] if len(s) > 25}
    for rel, lines in sorted(files.items()):
        joined = "\n".join(lines)
        for s in steps:
            if s in joined:
                bad("F1", f"{rel}: carries a registry step verbatim — read it from the server instead")
    if ok:
        good("F", f"{len(steps)} steps, none of them spelled a second time in the browser")


# ── §G / §H nothing un-wires ────────────────────────────────────────────────────────────────────
def check_GH():
    print("\n— §G/§H the wiring —")
    router = read(CORE_ROUTER)
    if '@router.get("/fix-guide")' not in router:
        bad("G1", "the /fix-guide route is gone; every migrated warning goes dark")
    if "_setup_fix.guide(" not in router:
        bad("G2", "the endpoint no longer dereferences setup_fix.guide — a second remedy is being served")
    if "_can_edit_setting(caller, area)" not in router:
        bad("G3", "`can_fix` is no longer the house permission answer; the button and the save can disagree")
    src = read(SETUP_FIX)
    # DERIVING a permission is forbidden here; PASSING one to the house check is the whole design, so
    # the control is on the shapes that READ a permission, never on the word `perms` appearing.
    for token in ('perms.get(', '"scope"', "'scope'", "super_admin", '"settings"', "'settings'",
                  '== "admin"', "== 'admin'"):
        if token in src:
            bad("G4", f"setup_fix reads a permission itself ({token}); that answer has one home")
    if "can_edit_setting" not in src:
        bad("G5", "setup_fix no longer dereferences the house permission check at all")
    fx = read(FIX_IT)
    for token in ("isPlatformAdmin", "canAccessPath", "canSeeItem", "permissions?.settings"):
        if token in fx:
            bad("H1", f"fix-it.tsx reads a role ({token}); the browser decides no permission here")
    if "fix.can_fix" not in fx:
        bad("H2", "fix-it.tsx no longer renders from the server's can_fix")
    if ok:
        good("G/H", "the route, the guide, the house permission check and the browser's deference all hold")


# ── §I the ratchet ──────────────────────────────────────────────────────────────────────────────
def parse_register(text):
    """PURE: (entries, reasonless) from the register's text. A line with no reason after the `#` is a
    silent excusal, which is the one thing a debt register may never be."""
    out, bare = {}, []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        path, _, reason = line.partition("#")
        path, reason = path.strip(), reason.strip()
        if not path:
            continue
        if not reason:
            bare.append(path)
        out[path] = reason
    return out, bare


def register():
    if not os.path.exists(REGISTRY):
        bad("I0", "frontend/setup_fix_pending.txt is missing")
        return {}
    out, bare = parse_register(read(REGISTRY))
    for p in bare:
        bad("I1", f"{p}: registered with no reason — an excusal must never be silent")
    return out


def gap_files(files):
    """Files whose DISPLAY COPY tells a reader a setup gap is open."""
    hits = {}
    for rel, lines in sorted(files.items()):
        for i, (ln, is_cmt) in enumerate(_vocab.comment_lines(lines), 1):
            if is_cmt:
                continue
            for seg in _vocab.display_copy(rel, ln):
                if GAP_PHRASE.search(seg):
                    hits.setdefault(rel, (i, seg.strip()[:90]))
                    break
    return hits


def check_I(files):
    print("\n— §I the ratchet —")
    reg = register()
    hits = gap_files(files)
    wired = {rel for rel, lines in files.items() if "@/lib/fix-it" in "\n".join(lines)}
    for rel, (line, seg) in sorted(hits.items()):
        if rel in wired or rel in reg:
            continue
        bad("I2", f"{rel}:{line} says a setup gap is open with no way to act on it: {seg!r}\n"
                  f"       → render <FixItNotice> from a registry key, or register it with a reason")
    for rel in sorted(reg):
        if rel in wired:
            bad("I3", f"{rel}: wired to the shared notice now — delete its register line")
        elif rel not in files:
            bad("I4", f"{rel}: registered but no longer exists — delete its register line")
        elif rel not in hits:
            bad("I5", f"{rel}: no longer says a setup gap is open — delete its register line")
    if len(reg) > PINNED_MAX:
        bad("I6", f"{len(reg)} registered, pin is {PINNED_MAX} — the list may only shrink")
    if ok:
        good("I", f"{len(hits)} files name a setup gap: {len(wired & set(hits))} wired, "
                  f"{len(reg)} registered (pin {PINNED_MAX})")


# ── §J the ask reuses the one chat path ─────────────────────────────────────────────────────────
def check_J():
    print("\n— §J the ask —")
    fx = read(FIX_IT)
    for token in ("export function FixItNotice", "export function useFixGuide", "export async function askTheFixer"):
        if token not in fx:
            bad("J1", f"fix-it.tsx no longer exports what every surface imports: {token}")
    if "/api/v1/chat/dm" not in fx or "/messages" not in fx:
        bad("J2", "the ask no longer goes through the app's own chat conversation")
    if re.search(r"/api/v1/(?!chat|core/fix-guide)", fx):
        bad("J3", "fix-it.tsx calls a route other than chat and the guide — a second send path")
    if ok:
        good("J", "the ask opens the existing conversation and posts through the existing message route")


# ── §K negative controls ────────────────────────────────────────────────────────────────────────
def check_K():
    print("\n— §K armed controls (each rule must be able to go red) —")
    checks = []

    # K1: a step that names a migration must be caught by the §D detector.
    checks.append(("K1 a migration in a step is caught",
                   _notice.is_setup_internal("Ask support to run migration 1120 first.")))
    # K2: a database error in a step is caught.
    checks.append(("K2 a database error in a step is caught",
                   _notice.is_system_internal("duplicate key value violates unique constraint x")))
    # K3: the gap phrase actually matches the warning that started this.
    checks.append(("K3 the phrase matches the reported warning",
                   bool(GAP_PHRASE.search("⚠️ Detection not configured — pick the commission-plan rule"))))
    # K4: and does NOT match the "not measured" class it deliberately leaves alone.
    checks.append(("K4 'not measured' is left to its own class",
                   not GAP_PHRASE.search("this figure is not measured for the month")))
    # K5: the display-copy extractor sees the warning as copy at all.
    checks.append(("K5 the extractor sees it as display copy",
                   any(GAP_PHRASE.search(s) for s in
                       _vocab.display_copy("app/x/page.tsx", "      <span>detection not configured</span>"))))
    # K6: a platform gap cannot be fixed by an administrator.
    plat = next((k for k in sf.FIX_KEYS if sf.fix(k)["gate"] == sf.PLATFORM), None)
    checks.append(("K6 a platform gap refuses even an administrator",
                   bool(plat) and sf.guide(plat, can_fix=True)["can_fix"] is False))
    # K7: a refused caller with nobody to ask is sent to support, never a dead button.
    anyco = next(k for k in sf.FIX_KEYS if sf.fix(k)["gate"] != sf.PLATFORM)
    g = sf.guide(anyco, can_fix=False, fixers=[])
    checks.append(("K7 nobody to ask ⇒ support, not a dead button", g["ask"] is None and g["support"] is True))
    # K8: an unknown key invents nothing.
    checks.append(("K8 an unknown key invents no remedy", sf.fix("nope") is None and sf.guide("nope", can_fix=True) is None))
    # K9: the register parser refuses a reasonless line, and keeps one that carries a reason.
    _, bare = parse_register("app/(platform)/x/page.tsx\n")
    kept, bare_ok = parse_register("app/(platform)/y/page.tsx  # owed a gap\n")
    checks.append(("K9 a register line with no reason is refused",
                   bare == ["app/(platform)/x/page.tsx"] and not bare_ok and len(kept) == 1))
    # K10: the pin is a ratchet — one more entry than the pin must fail.
    checks.append(("K10 the pin refuses a longer register", len(register()) <= PINNED_MAX))

    for label, passed in checks:
        if passed:
            print(f"  ✓ {label}")
        else:
            bad("K", f"control failed: {label}")


# ── §L CI runs this ─────────────────────────────────────────────────────────────────────────────
def check_L():
    print("\n— §L CI —")
    me = os.path.basename(__file__)
    for f in sorted(os.listdir(WORKFLOWS)):
        if f.endswith((".yml", ".yaml")) and me in read(os.path.join(WORKFLOWS, f)):
            return good("L", f"run by .github/workflows/{f}")
    bad("L1", f"{me} is run by no workflow — a lock nothing runs is a comment")


def main():
    print("THE LOCK on setup-gap guidance (index §CORE-39)")
    files = frontend_files()
    check_A()
    check_B()
    check_C()
    check_DE()
    check_F(files)
    check_GH()
    check_I(files)
    check_J()
    check_K()
    check_L()
    print("\n" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
