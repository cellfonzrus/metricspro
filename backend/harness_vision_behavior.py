"""Proof harness for voice-transcript behavior analytics (mod-vision, migration 900).

Run: python3 backend/harness_vision_behavior.py   (pure functions — no network, no DB, no ASR)

What is proven:
  1. REDACTION runs before anything is stored: phone, email, card-shaped and SSN-shaped runs go.
  2. Matching is punctuation- and case-insensitive, because ASR output is inconsistently punctuated.
  3. A rule with a `window_s` only counts inside that window — a "greeting" four minutes into a visit
     is not a greeting, and counting it as one is how a behavior metric stops meaning anything.
  4. The score is COVERAGE (share of interactions), not volume — saying "protection plan" nine times
     to one customer must NOT beat saying it once to nine customers.
  5. Negative rules subtract, and the score is clamped to 0..100.
  6. Coaching ranks the biggest weighted gaps, and surfaces any negative rule that fired.
  7. The rubric is data: a tenant's own rules replace the defaults wholesale.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.modules.vision import behavior as B   # noqa: E402

PASS, FAIL = [], []


def check(label, cond):
    (PASS if cond else FAIL).append(label)
    print(("  ok   " if cond else "  FAIL ") + label)


print("\n(1) Redaction runs at ingest — the raw string is never stored")
t, n = B.redact("call me on 415-555-0132 or sanjot@example.com")
check("phone number removed", "415" not in t and "[phone]" in t)
check("email removed", "example.com" not in t and "[email]" in t)
check("both replacements counted", n == 2)
t, _ = B.redact("card is 4111 1111 1111 1111 ok")
check("card-shaped digit run removed", "4111" not in t and "[card]" in t)
t, _ = B.redact("ssn 123-45-6789")
check("SSN-shaped run removed", "6789" not in t and "[ssn]" in t)
t, n = B.redact("your account 900123456 is active")
check("long account number removed", "900123456" not in t)
t, n = B.redact("we have 5 lines for 120 a month")
check("ordinary small numbers survive (a transcript must stay readable)",
      "5 lines" in t and "120" in t and n == 0)
check("empty input is safe", B.redact(None) == ("", 0))

print("\n(2) Matching ignores case and punctuation")
rules = [{"rule_key": "greeting", "label": "Greeted", "weight": 20, "polarity": "positive",
          "phrases": ["how can i help"], "is_active": True}]
check("exact", B.match_segment("how can i help", rules) == {"greeting": 1})
check("capitalised + punctuated", B.match_segment("Hi! How can I help?", rules) == {"greeting": 1})
check("accents normalised", B.match_segment("HOW  CAN   I  HELP", rules) == {"greeting": 1})
check("absent phrase does not match", B.match_segment("nice weather", rules) == {})
check("an inactive rule never matches",
      B.match_segment("how can i help", [{**rules[0], "is_active": False}]) == {})
# Apostrophes are DELETED, not collapsed to a space, so a rule author and an ASR engine that
# disagree about "can't" vs "cant" still match. Proven both directions.
apo = [{"rule_key": "dismissive", "label": "Dismissed", "weight": 10, "polarity": "negative",
        "phrases": ["i cant help you"], "is_active": True}]
check("rule without apostrophe matches speech WITH one",
      B.match_segment("Sorry, I can't help you.", apo) == {"dismissive": 1})
apo2 = [{**apo[0], "phrases": ["i can't help you"]}]
check("rule with apostrophe matches speech WITHOUT one",
      B.match_segment("sorry i cant help you", apo2) == {"dismissive": 1})
check("and a curly apostrophe too (what a phone keyboard actually types)",
      B.match_segment("i can\u2019t help you", apo) == {"dismissive": 1})

print("\n(3) A time-windowed rule only counts inside its window")
timed = [{"rule_key": "greeting", "label": "Greeted", "weight": 20, "polarity": "positive",
          "phrases": ["welcome in"], "window_s": 30, "is_active": True}]
check("said at 5s  -> counts", B.match_segment("welcome in", timed, elapsed_s=5) == {"greeting": 1})
check("said at 240s -> does NOT count", B.match_segment("welcome in", timed, elapsed_s=240) == {})
check("unknown elapsed falls back to counting it",
      B.match_segment("welcome in", timed, elapsed_s=None) == {"greeting": 1})

print("\n(4) The score is COVERAGE, not volume")
RUBRIC = [
    {"rule_key": "greeting", "label": "Greeted", "weight": 50, "polarity": "positive",
     "phrases": ["welcome in"], "is_active": True},
    {"rule_key": "protection", "label": "Offered protection", "weight": 50, "polarity": "positive",
     "phrases": ["protection plan"], "is_active": True},
]
# Rep A: says it nine times to ONE customer. Rep B: says it once to each of NINE customers.
rep_a = [{"text": "protection plan", "visit_id": "v1", "duration_s": 5} for _ in range(9)]
rep_b = [{"text": "protection plan", "visit_id": f"v{i}", "duration_s": 5} for i in range(9)]
sa, sbb = B.score_interactions(rep_a, RUBRIC), B.score_interactions(rep_b, RUBRIC)
check("rep A had 1 interaction", sa["interactions"] == 1)
check("rep B had 9 interactions", sbb["interactions"] == 9)
check("both cover 'protection' on 100% of their interactions -> SAME score",
      sa["score"] == sbb["score"] == 50.0)
check("raw hit counts still recorded for context", sa["rule_hits"]["protection"] == 9)

half = ([{"text": "protection plan", "visit_id": f"v{i}", "duration_s": 5} for i in range(5)] +
        [{"text": "nothing useful", "visit_id": f"w{i}", "duration_s": 5} for i in range(5)])
sh = B.score_interactions(half, RUBRIC)
check("50% coverage of a 50-weight rule (of 100 total) -> 25", sh["score"] == 25.0)
check("greet tracking: nobody was greeted", sh["greeted"] == 0 and sh["missed_greetings"] == 10)

full = [{"text": "welcome in, protection plan", "visit_id": f"v{i}", "duration_s": 5}
        for i in range(4)]
sf = B.score_interactions(full, RUBRIC)
check("both rules covered everywhere -> 100", sf["score"] == 100.0)
check("greeted on every interaction", sf["greeted"] == 4 and sf["missed_greetings"] == 0)
check("talk seconds are summed", sf["talk_seconds"] == 20.0)

print("\n(5) Negative rules subtract, and the score is clamped")
WITH_NEG = RUBRIC + [{"rule_key": "dismissive", "label": "Dismissed", "weight": 30,
                      "polarity": "negative", "phrases": ["i cant help you"], "is_active": True}]
segs = [{"text": "welcome in, protection plan. i can't help you.", "visit_id": "v1", "duration_s": 5}]
s = B.score_interactions(segs, WITH_NEG)
check("100 earned minus a 30-weight penalty at full coverage -> 70", s["score"] == 70.0)
harsh = [{"text": "i can't help you", "visit_id": f"v{i}", "duration_s": 5} for i in range(3)]
s = B.score_interactions(harsh, WITH_NEG)
check("nothing positive + a penalty clamps at 0, never negative", s["score"] == 0.0)

print("\n(6) Coaching points at the biggest weighted gap and flags what fired")
mixed = ([{"text": "welcome in", "visit_id": f"v{i}", "duration_s": 5} for i in range(10)] +
         [{"text": "i can't help you", "visit_id": "v0", "duration_s": 5}])
s = B.score_interactions(mixed, WITH_NEG)
keys = [c["rule_key"] for c in s["coaching"]]
check("the negative rule that fired is surfaced first", keys[0] == "dismissive")
check("the uncovered positive rule is the coaching gap", "protection" in keys)
check("the fully-covered rule is NOT nagged about", "greeting" not in keys)
check("coverage is reported per rule",
      s["coverage"]["greeting"] == 1.0 and s["coverage"]["protection"] == 0.0)

print("\n(7) The rubric is data, not code")
check("no stored rows -> the seed defaults are used",
      [r["rule_key"] for r in B.rules_or_defaults([])] ==
      [r["rule_key"] for r in B.DEFAULT_RULES])
mine = [{"rule_key": "spanish_greeting", "label": "Saludó", "weight": 100, "polarity": "positive",
         "phrases": ["bienvenido"], "is_active": True}]
check("a tenant's own rubric replaces the defaults wholesale",
      [r["rule_key"] for r in B.rules_or_defaults(mine)] == ["spanish_greeting"])
s = B.score_interactions([{"text": "Bienvenido!", "visit_id": "v1", "duration_s": 3}], mine)
check("and it actually scores", s["score"] == 100.0)
check("the seed rubric itself is well-formed (every rule has phrases and a weight)",
      all(r.get("phrases") and r.get("weight") for r in B.DEFAULT_RULES))
check("no duplicate rule keys in the seed",
      len({r["rule_key"] for r in B.DEFAULT_RULES}) == len(B.DEFAULT_RULES))

print("\n(8) Degenerate inputs never raise")
s = B.score_interactions([], B.DEFAULT_RULES)
check("no segments -> a zero row, not a crash", s["score"] == 0 and s["interactions"] == 0)
s = B.score_interactions([{"text": "hello", "duration_s": 1}], [])
check("no rules -> a zero score, not a division by zero", s["score"] == 0.0)
s = B.score_interactions([{"text": "hello there", "duration_s": 1}], B.DEFAULT_RULES)
check("a segment with no visit_id is treated as its own interaction", s["interactions"] == 1)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
