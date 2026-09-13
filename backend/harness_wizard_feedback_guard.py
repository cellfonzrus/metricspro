"""Guard — the Implementation Wizard must never fail SILENTLY.

Owner report 2026-09-13, verbatim: "nothing happens after i hit import on the green button - or seed
default or upload sample to detect it does not give any error or confirmation or any directions what
to do it should be self explanatory for the user".

THREE DEFECTS, all of which made a working page look dead:

  1. SWALLOWED ERRORS. Every loader on the page ended `.catch(() => {})`. When the readiness call
     failed the list stayed empty and the page rendered "Loading…" for ever — identical to a slow
     network, with no error, no cause and nothing to click.
  2. THE RESULT RENDERED OFF-SCREEN. Each action called the PAGE-level `setMsg`, which renders once,
     below the whole report list. Acting on a row several screens down showed nothing at all.
  3. NO INSTRUCTIONS AT THE POINT OF USE. The only guidance sat at the top of the card, above every
     report, so an expanded row offered three buttons and no order to press them in.

SOURCE-PARSING, like harness_gp_totals_reconcile §B: these are properties of the FILE, so the file is
the thing asserted. A future edit that reintroduces a silent catch fails this build.
"""
import sys, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "..", "frontend", "src", "app", "(platform)",
                    "commcalc", "implementation", "page.tsx")
PASS, FAIL = [], []


def ok(cond, what):
    (PASS if cond else FAIL).append(what)
    print(("  PASS " if cond else "  FAIL ") + what)


raw = open(PAGE, encoding="utf-8").read()


def strip_comments(text):
    """Assertions below are about CODE, not prose. A comment that QUOTES the defect it fixed (this
    file's own header does) must not read as the defect still being present."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)          # /* block */ and {/* jsx */}
    return "\n".join(l for l in text.split("\n") if not l.lstrip().startswith("//"))


src = strip_comments(raw)
mapper = src[src.index("function ReportMapper("):]

print("\n(1) NO SWALLOWED ERRORS — a failure must be visible, never discarded")
silent = re.findall(r"\.catch\(\(\)\s*=>\s*\{\s*\}\)", src)
ok(not silent, f"no `.catch(() => {{}})` anywhere on the page (found {len(silent)})")
ok(".catch((e: any) =>" in src or ".catch(e =>" in src, "failures are caught INTO a message")

print("\n(2) A FAILED LOAD SAYS SO, AND OFFERS A WAY OUT")
ok("loadErr" in src, "the page keeps an error state for the readiness load")
ok("setLoading(false)" in src, "the loading flag is always cleared (no permanent 'Loading…')")
ok(re.search(r"loading\s*&&\s*reportKeys\.length === 0", src) is not None,
   "'Loading…' renders ONLY while actually loading")
ok(re.search(r"!loading\s*&&\s*loadErr", src) is not None, "a failed load renders an ERROR state")
ok("onClick={loadReadiness}" in src, "the error state offers a Retry button")
ok(re.search(r"!loading\s*&&\s*!loadErr\s*&&\s*reportKeys\.length === 0", src) is not None,
   "'no reports' is distinguished from 'still loading' and from 'failed'")

print("\n(3) THE RESULT APPEARS BESIDE THE BUTTON THAT CAUSED IT")
ok("const say = (m: string) => { setRowMsg(m); setMsg(m) }" in mapper,
   "ReportMapper has a say() that sets BOTH the row message and the page message")
ok("say(m); say(m)" not in mapper and "setRowMsg(m); say(m)" not in mapper,
   "say() does not call itself (an infinite render loop)")
bare = re.findall(r"(?<!set)(?<![A-Za-z])setMsg\(", mapper)
ok(len(bare) == 1, f"inside ReportMapper every result goes through say(), not bare setMsg() "
                   f"(found {len(bare)} — the 1 allowed is say()'s own body)")
ok("{rowMsg && (" in mapper, "the row renders its OWN message, inside the expanded panel")

print("\n(4) THE PANEL EXPLAINS ITSELF BEFORE ANYTHING IS CLICKED")
ok("<ol style=" in mapper, "the expanded panel carries NUMBERED steps")
panel = mapper[mapper.index("{open && ("):]
steps = panel[panel.index("<ol style="):panel.index("</ol>")]
# Pull EVERY button label out of the panel STRUCTURALLY (>text</button>), not by matching words we
# already expect. A word-list would silently stop checking a button the moment it is renamed — the
# exact failure this guard exists to prevent. The ternary-labelled import button is read separately.
labels = [re.sub(r"^[^A-Za-z]+", "", t).strip()
          for t in re.findall(r">\s*([^<>{}]+?)\s*</button>", panel)]
imp = re.search(r"importing \? '[^']*' : '([^']+)'", panel)
if imp:
    labels.append(re.sub(r"^[^A-Za-z]+", "", imp.group(1)).strip())
labels = [l for l in dict.fromkeys(labels) if l]
ok(len(labels) >= 4, f"read the panel's button labels structurally: {labels}")
for lab in labels:
    ok(lab in steps, f"the steps name the button AS LABELLED: {lab!r}")
ok("derives_period" in mapper and "blank" in mapper.lower(),
   "step 3 tells the user which PERIOD spelling this report wants (registry-driven, not hardcoded)")

print("\n(5) A LONG UPLOAD SAYS IT IS WORKING")
ok("⏳" in mapper and "Importing" in mapper,
   "the import announces itself in-flight (a 7 MB file otherwise looks like a dead button)")

print(f"\nharness_wizard_feedback_guard: {len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
