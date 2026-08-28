"""Proves the camera setup wizard offline — no network, no DB, no clock.

WHY THIS HARNESS EARNS ITS KEEP
    Everything in onboarding.py is a string an operator will follow literally: a console link they
    click, a value they paste into Google, a warning they act on. A wrong one here is
    indistinguishable, from where they sit, from the product being broken — and it fails in
    somebody else's console, where we cannot see it.

    So this asserts the things that are true of a CORRECT wizard rather than re-typing its content:
    every step is reachable, every link carries the project it needs, nothing that must be pasted
    is ever silently blank, the two project ids can never be confused, and the gate order can never
    ask somebody to paste a topic into Device Access before the topic exists.

Run:  python3 backend/harness_vision_onboarding.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.vision import onboarding as O   # noqa: E402

_pass = _fail = 0


def ck(label, cond, extra=None):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print(f"  XX  {label}" + (f"  {extra!r}" if extra is not None else ""))


def eq(label, got, want):
    ck(label, got == want, None if got == want else f"got {got!r} want {want!r}")


# A fully-populated tenant, and a brand-new one that has typed nothing yet.
FULL = O.context(
    api_base="https://metricspro-production.up.railway.app/",
    app_base="https://metricspro-five.vercel.app",
    gcp_project="metrics-pro-506103", gcp_number="437700580502",
    da_project="c25e6a1e-5f03-4b6a-9b72-3a9cf89fcbe3",
    client_id="437700580502-abc.apps.googleusercontent.com",
    topic="metricspro-camera-events",
    sa_email="vision-push@metrics-pro-506103.iam.gserviceaccount.com")
EMPTY = O.context()

print("── the steps hang together ──────────────────────────────────────────────────────────────")
ck("there are steps at all", len(O.STEPS) >= 10)
eq("no duplicate step keys", len(set(O.STEP_KEYS)), len(O.STEP_KEYS))
for s in O.STEPS:
    ck(f"{s['key']}: has a title", bool(s.get("title")))
    ck(f"{s['key']}: says why it exists", bool(s.get("why")))
    ck(f"{s['key']}: names its trap", bool(s.get("gotcha")))
    ck(f"{s['key']}: has a group", bool(s.get("group")))
# Every dependency must name a real step, and must come EARLIER — a step that depends on a later
# one can never unblock, and the wizard would sit on it forever with no way forward.
for i, s in enumerate(O.STEPS):
    for n in (s.get("needs") or []):
        ck(f"{s['key']}: needs '{n}' which exists", n in O._BY_KEY)
        ck(f"{s['key']}: needs '{n}' which comes first",
           n in O.STEP_KEYS and O.STEP_KEYS.index(n) < i)

print("── nothing an operator must paste is ever silently blank ────────────────────────────────")
# THE FAILURE THIS PREVENTS: a link rendered with an empty project, which opens Google's console on
# whatever project happens to be selected. The operator enables the API on the wrong project and
# everything downstream fails for a reason nothing reports.
for s in O.STEPS:
    kit_empty = O.field_kit(s["key"], EMPTY)
    kit_full = O.field_kit(s["key"], FULL)
    if kit_full["link"]:
        ck(f"{s['key']}: link is https", kit_full["link"].startswith("https://"))
        ck(f"{s['key']}: unset values show a placeholder, never a blank",
           "{" not in kit_empty["link"] and "  " not in kit_empty["link"])
    for c in kit_full["copy"]:
        ck(f"{s['key']}: copy '{c['label']}' is filled", bool(c["value"].strip()))
        ck(f"{s['key']}: copy '{c['label']}' left no unrendered token", "{" not in c["value"])
    for c in kit_empty["copy"]:
        ck(f"{s['key']}: copy '{c['label']}' marks what is missing rather than looking done",
           bool(c["value"].strip()))

print("── cross-references point at the step they mean ─────────────────────────────────────────")
# THE DEFECT THIS EXISTS FOR, found by rendering the page: the prose said "NOT THE ONE FROM STEP 2"
# and several other numbers, three of which were off by one — and the UI showed no numbers at all,
# so every reference was both invisible and wrong. Prose numbers rot the moment a step is inserted;
# these are now {step:key} tokens resolved from the real position.
OPERATOR_TEXT = []   # every string an operator actually reads
for _s in O.STEPS:
    kit = O.field_kit(_s["key"], FULL)
    OPERATOR_TEXT += [kit["title"], kit["why"], kit["gotcha"], kit["expect"]]
    OPERATOR_TEXT += list(kit["body"])
    OPERATOR_TEXT += [c["note"] for c in kit["copy"]]
import re as _re
for _t in OPERATOR_TEXT:
    ck("no unresolved {step:...} token reaches the operator", "{step:" not in (_t or ""), _t)
# A raw source scan: no literal "step <digit>" may survive in a step definition, because a literal
# cannot be kept in sync with a reorder.
_src_steps = src_of_steps = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                              "app/modules/vision/onboarding.py")).read()
_defs = _src_steps[_src_steps.index("STEPS = ["):_src_steps.index("STEP_KEYS = [")]
ck("no literal 'step N' is hard-coded in the step definitions",
   not _re.search(r"step \d", _defs), _re.findall(r"step \d[^\"]{0,40}", _defs))
# Every reference names a real step...
for _key in _re.findall(r"\{step:(\w+)\}", _defs):
    ck(f"cross-reference {{step:{_key}}} names a real step", _key in O._BY_KEY)
# ...and resolves to that step's actual position, not a number somebody typed.
eq("step numbers are 1-based", O.step_number(O.STEP_KEYS[0]), 1)
eq("the last step is numbered last", O.step_number(O.STEP_KEYS[-1]), len(O.STEPS))
eq("an unknown key resolves to 0", O.step_number("nope"), 0)
ck("an unknown key renders as prose, not 'step 0'",
   "step 0" not in O._fmt("see {step:nope}", FULL), O._fmt("see {step:nope}", FULL))
# The specific references that were wrong before, checked against where those steps really are.
_da = O.field_kit("device_access", FULL)
ck("the Device Access trap points at the Cloud-project step by its real number",
   f"step {O.step_number('cloud_project')}" in _da["gotcha"], _da["gotcha"])
_pub = O.field_kit("publisher_role", FULL)
ck("the publisher-role trap points at the link-topic step by its real number",
   f"step {O.step_number('link_topic')}" in _pub["gotcha"], _pub["gotcha"])
_lt = O.field_kit("link_topic", FULL)
ck("the link-topic step points back at the topic step by its real number",
   f"step {O.step_number('topic')}" in " ".join(_lt["body"]), _lt["body"])
_wt = O.field_kit("walk_test", FULL)
for _need in ("topic", "publisher_role", "link_topic", "push_subscription"):
    ck(f"the walk-test trap names {_need} by its real number",
       f"step {O.step_number(_need)}" in _wt["gotcha"], _wt["gotcha"])
# Every step carries the number the operator sees, matching its position.
for _i, _s in enumerate(O.STEPS):
    eq(f"{_s['key']} is numbered {_i + 1}", O.field_kit(_s["key"], FULL)["number"], _i + 1)

print("── the links point where they say ───────────────────────────────────────────────────────")
k = {s["key"]: O.field_kit(s["key"], FULL) for s in O.STEPS}
ck("enable_api names the API being enabled",
   "smartdevicemanagement.googleapis.com" in k["enable_api"]["link"])
ck("enable_api carries the project, so the console cannot open on the wrong one",
   "project=metrics-pro-506103" in k["enable_api"]["link"])
ck("consent_screen carries the project", "project=metrics-pro-506103" in k["consent_screen"]["link"])
ck("oauth_client carries the project", "project=metrics-pro-506103" in k["oauth_client"]["link"])
ck("topic carries the project", "project=metrics-pro-506103" in k["topic"]["link"])
ck("device_access points at the Nest console, not the Cloud one",
   "console.nest.google.com" in k["device_access"]["link"])
ck("link_topic points at the Nest console", "console.nest.google.com" in k["link_topic"]["link"])

print("── the exact strings Google compares byte for byte ──────────────────────────────────────")
uris = [c["value"] for c in k["oauth_client"]["copy"] if "redirect" in c["label"].lower()]
eq("both redirect URIs are offered", len(uris), 2)
ck("the wizard's own redirect is one of them",
   "https://metricspro-five.vercel.app/vision/onboarding" in uris, uris)
ck("the settings page redirect is the other",
   "https://metricspro-five.vercel.app/vision/settings" in uris, uris)
ck("no redirect URI has a trailing slash", all(not u.endswith("/") for u in uris))
# THE REGRESSION: app_base arriving with a trailing slash would produce '...app//vision/onboarding',
# which Google treats as a different address and rejects with redirect_uri_mismatch.
slashy = O.context(app_base="https://metricspro-five.vercel.app/")
ck("a trailing slash on the app base does not double up",
   "//vision" not in O.field_kit("oauth_client", slashy)["copy"][0]["value"])
slashy_api = O.field_kit("push_subscription", O.context(
    api_base="https://x.example.com/", sa_email="a@b.com"))["copy"][0]["value"]
ck("a trailing slash on the api base does not double up", "com//api" not in slashy_api, slashy_api)
ck("the push endpoint is our real events route",
   k["push_subscription"]["copy"][0]["value"].endswith("/api/v1/vision/google/events"))
pub = [c["value"] for c in k["publisher_role"]["copy"]]
ck("the publisher principal is Google's group, exactly", O.SDM_PUBLISHER in pub, pub)
eq("the publisher group is the documented one", O.SDM_PUBLISHER, "sdm-publisher@googlegroups.com")

print("── the full topic name, which is where step 8 goes wrong ────────────────────────────────")
eq("full topic is the long form Device Access wants",
   O.full_topic("metrics-pro-506103", "metricspro-camera-events"),
   "projects/metrics-pro-506103/topics/metricspro-camera-events")
eq("no project, no topic name", O.full_topic("", "t"), "")
eq("no topic, no topic name", O.full_topic("p", ""), "")
ck("step 8 hands over the long form, not the short id",
   k["link_topic"]["copy"][0]["value"].startswith("projects/"))
ck("...and it is derived from what was typed, so the two cannot drift",
   "metricspro-camera-events" in k["link_topic"]["copy"][0]["value"])

print("── trap 1: the two things called 'project id' ───────────────────────────────────────────")
DA = "c25e6a1e-5f03-4b6a-9b72-3a9cf89fcbe3"
GCP = "metrics-pro-506103"
eq("a real Device Access id passes", O.check_value("device_access_project", DA), None)
eq("a real Cloud id passes", O.check_value("cloud_project", GCP), None)
# The whole point: not merely rejected, but told WHICH ONE they pasted.
msg = O.check_value("cloud_project", DA)
ck("Cloud box given the Device Access id says so by name", msg and "DEVICE ACCESS" in msg, msg)
msg = O.check_value("device_access_project", GCP)
ck("Device Access box given the Cloud id says so by name",
   msg and ("cloud" in msg.lower() or "device access" in msg.lower()), msg)
ck("an empty Cloud id asks for one", bool(O.check_value("cloud_project", "")))
ck("an empty Device Access id asks for one", bool(O.check_value("device_access_project", "")))
# The project NUMBER is the third value people paste into these boxes.
ck("the project number is not accepted as a Cloud project id",
   bool(O.check_value("cloud_project", "437700580502")))
ck("the project number is not accepted as a client id",
   bool(O.check_value("client_id", "437700580502")))
ck("...and it is told what it actually is",
   "project NUMBER" in (O.check_value("client_id", "437700580502") or ""))
eq("a real client id passes", O.check_value("client_id", "1-x.apps.googleusercontent.com"), None)

print("── trap 5: topic ids Google will reject ─────────────────────────────────────────────────")
eq("a normal topic id passes", O.topic_problem("metricspro-camera-events"), None)
eq("dots and underscores are allowed", O.topic_problem("metrics_pro.cam-events"), None)
ck("an empty id is refused", bool(O.topic_problem("")))
ck("spaces are refused", bool(O.topic_problem("my topic")))
ck("a two-character id is refused (Google's minimum is 3)", bool(O.topic_problem("ab")))
ck("a leading digit is refused", bool(O.topic_problem("1topic")))
# Google reserves 'goog' and does not say so until the console rejects it.
ck("'goog' prefix is refused", bool(O.topic_problem("googtopic")))
ck("...and the reason names the reservation",
   "goog" in (O.topic_problem("googtopic") or "").lower())
# Pasting the LONG form here is the mirror of pasting the short form into Device Access.
long_form = O.topic_problem("projects/p/topics/t")
ck("the full path pasted into the short box is caught", bool(long_form))
ck("...and it says which part is wanted", "short id" in (long_form or ""))

print("── the order can never ask for something that does not exist yet ────────────────────────")
none_done = O.plan(FULL, {})
first = [s for s in none_done if s["state"] == "current"]
eq("exactly one step is current", len(first), 1)
eq("the first step is the overview", first[0]["key"], "overview")
ck("everything else is blocked or waiting",
   all(s["state"] in ("blocked", "todo") for s in none_done if s["key"] != "overview"))
# THE ORDERING BUG THIS CATCHES: Device Access asks for a topic it will not create, so the topic
# must be creatable before step 8 — and the publisher role granted before Device Access tries to use it.
idx = {s["key"]: i for i, s in enumerate(O.STEP_KEYS and O.STEPS)}
ck("the topic is created before it is pasted into Device Access", idx["topic"] < idx["link_topic"])
ck("the publisher role is granted before Device Access publishes",
   idx["publisher_role"] < idx["link_topic"])
ck("the subscription comes after the topic is linked",
   idx["link_topic"] < idx["push_subscription"])
ck("the consent screen is published before the client is made",
   idx["consent_screen"] < idx["oauth_client"])
ck("the OAuth client exists before Device Access asks for it",
   idx["oauth_client"] < idx["device_access"])
ck("cameras are synced before they are assigned to stores", idx["sync"] < idx["assign_stores"])
ck("the walk test comes last", idx["walk_test"] == len(O.STEPS) - 1)

print("── a half-finished wizard resumes where it stopped ──────────────────────────────────────")
part = O.plan(FULL, {"overview": True, "cloud_project": True, "enable_api": True})
cur = [s for s in part if s["state"] == "current"]
eq("still exactly one current step", len(cur), 1)
eq("it is the next unfinished one", cur[0]["key"], "consent_screen")
ck("finished steps stay finished",
   all(s["state"] == "done" for s in part
       if s["key"] in ("overview", "cloud_project", "enable_api")))
blocked = [s for s in part if s["state"] == "blocked"]
ck("a blocked step names what is blocking it", all(s["blocked_by"] for s in blocked))
ck("oauth_client is blocked by the consent screen",
   [s for s in part if s["key"] == "oauth_client"][0]["blocked_by"] == ["consent_screen"])

print("── an operator who only wants live view can still finish ────────────────────────────────")
required = [s["key"] for s in O.STEPS if not s.get("optional")]
for key in ("topic", "publisher_role", "link_topic", "push_subscription", "walk_test", "entrance"):
    ck(f"'{key}' is optional", key not in required)
for key in ("overview", "cloud_project", "enable_api", "consent_screen", "oauth_client",
            "device_access", "authorize", "sync", "assign_stores"):
    ck(f"'{key}' is required", key in required)
done_required = {key: True for key in required}
p = O.progress(O.plan(FULL, done_required))
ck("required steps alone count as complete", p["complete"], p)
eq("...and required_left is zero", p["required_left"], 0)
ck("the optional ones are still listed as outstanding", p["done"] < p["total"])
p0 = O.progress(O.plan(FULL, {}))
ck("a fresh tenant is not complete", not p0["complete"])
ck("a fresh tenant is told how long it will take", p0["minutes_left"] > 0)
eq("nothing is done yet", p0["done"], 0)
# The two clocks. The headline promises time to a WORKING setup, so it must not include the
# optional busy-hours and analyzer work, and it must not be zero while required steps remain.
ck("time-to-working is less than time-to-everything",
   p0["minutes_left_required"] < p0["minutes_left"], (p0["minutes_left_required"], p0["minutes_left"]))
ck("time-to-working is not zero while required steps remain", p0["minutes_left_required"] > 0)
eq("a finished-required tenant has no required minutes left",
   O.progress(O.plan(FULL, done_required))["minutes_left_required"], 0)
ck("...but still has optional minutes, and is told how many are outstanding",
   O.progress(O.plan(FULL, done_required))["optional_left"] > 0)
eq("a fully-done tenant has nothing outstanding",
   O.progress(O.plan(FULL, {k: True for k in O.STEP_KEYS}))["optional_left"], 0)
# The progress headline is a COUNT of required steps, so it can never disagree with the rail's
# per-step numbering — which counts optional steps too. required_done <= the step number of the
# current step, always.
_partial = O.plan(FULL, {"overview": True, "cloud_project": True})
_cur = [s for s in _partial if s["state"] == "current"][0]
ck("the current step's number is at least the required-done count",
   _cur["number"] >= O.progress(_partial)["required_done"], (_cur["number"], _cur["key"]))

print("── trap 2: the seven-day Testing-mode token ─────────────────────────────────────────────")
eq("an unlinked tenant is not warned", O.token_age_warning(9, False), None)
eq("no age, no warning", O.token_age_warning(None, True), None)
eq("a fresh connection is not warned", O.token_age_warning(1, True), None)
ck("day five warns before it breaks", bool(O.token_age_warning(5, True)))
ck("...and the warning names the fix", "publish" in (O.token_age_warning(5, True) or "").lower())
ck("past seven days it says it may already be dead",
   "already" in (O.token_age_warning(8, True) or "").lower())
eq("garbage age is ignored rather than guessed", O.token_age_warning("soon", True), None)

print("── Google's errors, mapped to the step that causes them ─────────────────────────────────")
ck("redirect_uri_mismatch points at the sign-in credentials step by name",
   "sign-in credentials" in (O.explain_google_error("Error 400: redirect_uri_mismatch") or ""))
ck("access_denied points at the consent screen by name",
   "consent screen" in (O.explain_google_error("Error 403: access_denied") or ""))
ck("the real verification message is recognised too",
   bool(O.explain_google_error(
       "Access blocked: metricspro-five.vercel.app has not completed the Google verification process")))
ck("invalid_grant is explained as the seven-day expiry",
   "seven days" in (O.explain_google_error("invalid_grant") or ""))
ck("a 403 on the SDM host points at the enable-API step by name",
   "camera API" in (O.explain_google_error("403 from smartdevicemanagement.googleapis.com") or ""))
ck("an unknown enterprise points at the Device Access step by name",
   "Device Access" in (O.explain_google_error("enterprise not found") or ""))
eq("an error we have nothing to add to is left alone",
   O.explain_google_error("connection reset by peer"), None)
eq("empty in, nothing out", O.explain_google_error(""), None)
# explain_google_error is rendered by the SETTINGS page too, which has no step rail — so it names
# steps by title. A bare "step 7" there would point at nothing the reader can see.
for _msg in ("redirect_uri_mismatch", "access_denied", "invalid_grant", "invalid_client",
             "403 smartdevicemanagement", "enterprise not found"):
    _out = O.explain_google_error(_msg) or ""
    ck(f"'{_msg}' explanation carries no bare step number",
       not _re.search(r"step \d", _out), _out)
eq("None in, nothing out", O.explain_google_error(None), None)

print("── nothing here reaches for the network, a clock, or a database ─────────────────────────")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "app/modules/vision/onboarding.py")).read()
for bad in ("requests.", "httpx.", "get_supabase", "datetime.now", "time.time"):
    ck(f"onboarding.py does not use {bad}", bad not in src)

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
