"""Camera setup wizard — the field kit.

OWNER DIRECTIVE 2026-08-22 (sanjot@), verbatim: "This is very complex set up for any tenant to
follow , need t make it very easy for them to onboard their camera, if i was to do it agin i cannot
- so we need a detailed wizard to set up the cameras with every minute details possible with links
and storing the information as we go along so the user does not have to go back and forth like we
did earlier".

WHY THIS FILE IS MOSTLY PROSE
    Connecting a Nest camera is not hard because the API is hard. It is hard because the work happens
    in THREE separate Google consoles that do not link to each other, several of the values are
    called the same thing while being different things, and every one of the traps below fails
    LATE — days later, silently, in a way that looks like our bug rather than a missing checkbox.

    The owner and I hit all of them, in order, with the API reference open. That transcript is the
    specification for this file. Each step below carries the exact link, the exact string to paste,
    and the specific way that step goes wrong — because "see Google's documentation" is what made
    the first attempt take a day.

THE SEVEN TRAPS, and where each is handled
    1. TWO THINGS CALLED "project id". The Google CLOUD project (`metrics-pro-506103`) and the
       DEVICE ACCESS project (a UUID) are different, issued by different consoles, and both are
       needed. Pasting one where the other belongs is the single most common failure.
       → every step names which one it wants, and check_value() rejects the other BY NAME.
    2. CONSENT SCREEN LEFT IN "TESTING". Google expires refresh tokens issued by a Testing-mode
       consent screen after SEVEN DAYS. Everything works, then dies the following week looking like
       a bug in us. → its own step, marked critical, and token_age_warning() catches it after.
    3. THE $5. Device Access charges a one-time non-refundable fee before it will create the
       project. Discovering this halfway through is where people stop. → said in step 1, before
       any work begins.
    4. REDIRECT URI MISMATCH. Google compares it byte for byte. → we hand over the exact strings,
       both of them, rather than describing them.
    5. PUB/SUB TOPIC IN THE WRONG PROJECT. The Device Access console asks for a topic but does not
       create one; it must already exist in the operator's OWN cloud project. → its own step, with
       the naming rules Google enforces but does not state.
    6. sdm-publisher@googlegroups.com NEEDS THE PUBLISHER ROLE. Without it Device Access cannot
       publish and reports a failure that names neither the group nor the role. → its own step.
    7. A PUSH SUBSCRIPTION THAT IS WRONG SAYS NOTHING. Google retries into the void for days. There
       is no "test" button anywhere in Google's console. → the last step asks the operator to walk
       past a camera while we watch for the event, which is the only honest proof the chain works.

EVERYTHING HERE IS A PURE FUNCTION
    No database, no network, no clock. The whole file is (context) -> (what to show), so
    harness_vision_onboarding.py can prove the links, the pasted values and the gating offline. That
    matters more than usual here: a wrong redirect URI or a mistyped console link in this file is
    indistinguishable, to the operator, from the product being broken.
"""

import re

from app.modules.vision import google_sdm as G

# The OAuth client must accept BOTH, because the operator can start a connection from either page
# and Google matches the redirect byte for byte. Registering one and using the other is trap 4.
WIZARD_PATH = "/vision/onboarding"
SETTINGS_PATH = "/vision/settings"

# Google publishes to this group; Device Access publishes to the operator's topic AS this principal.
# Not a placeholder and not an address anyone emails — it is the IAM principal that needs the role.
SDM_PUBLISHER = "sdm-publisher@googlegroups.com"
PUBSUB_PUBLISHER_ROLE = "roles/pubsub.publisher"

# Google's own rule for a Pub/Sub topic id, which the console enforces and does not print until you
# have already got it wrong: 3-255 chars, starts with a letter, letters/digits/dashes/underscores/
# periods/tildes/plus/percent signs, and may not start with "goog".
_TOPIC_RE = re.compile(r"^[A-Za-z][A-Za-z0-9\-_.~+%]{2,254}$")
SUGGESTED_TOPIC = "metricspro-camera-events"


def topic_problem(value):
    """None if this is a usable Pub/Sub topic id, else what is wrong with it — in Google's terms.

    Trap 5's second half. The console rejects a bad id with a generic message after the operator has
    typed it, and 'goog' being reserved is not mentioned anywhere they will have read."""
    v = str(value or "").strip()
    if not v:
        return "Enter a topic id."
    if v.startswith("projects/"):
        return ("That is the FULL topic path. Here we want just the short id — the last part after "
                "'/topics/'.")
    if v.lower().startswith("goog"):
        return "Google reserves ids starting with 'goog'. Pick another name."
    if not _TOPIC_RE.match(v):
        return ("A topic id must start with a letter and be 3–255 characters of letters, digits, "
                "dashes, underscores or periods. No spaces.")
    return None


def full_topic(gcp_project: str, topic: str) -> str:
    """The fully-qualified name Device Access wants pasted. The console's field is labelled 'PubSub
    topic name' and silently means this form, not the short id the operator just created."""
    p, t = str(gcp_project or "").strip(), str(topic or "").strip()
    return f"projects/{p}/topics/{t}" if p and t else ""


def check_value(kind: str, value):
    """None when `value` is a usable <kind>, else a message naming what was pasted instead.

    Trap 1 lives or dies here. Told only "that is not valid", an operator retypes the same string.
    Told "that is your Google CLOUD project id — this box wants the Device Access one, which is a
    UUID from a different console", they fix it in one move."""
    v = str(value or "").strip()
    if kind == "device_access_project":
        return G.project_id_problem(v)
    if kind == "cloud_project":
        if not v:
            return "Enter your Google Cloud project id."
        if G.looks_like_device_access_project_id(v):
            return ("That is your DEVICE ACCESS project id (the UUID). This box wants the Google "
                    "CLOUD project id — the short name like 'metrics-pro-506103' shown in the Cloud "
                    "console's project picker.")
        if not G.looks_like_cloud_project_id(v):
            return ("A Cloud project id is lowercase letters, digits and dashes, 6–30 characters, "
                    "starting with a letter — e.g. 'metrics-pro-506103'. Copy it from the project "
                    "picker at the top of the Cloud console, not the project NAME.")
        return None
    if kind == "client_id":
        if not v:
            return "Enter the OAuth client id."
        if not v.endswith(".apps.googleusercontent.com"):
            return ("An OAuth client id ends in '.apps.googleusercontent.com'. If what you have is "
                    "just digits, that is the project NUMBER, which is a different thing.")
        return None
    if kind == "topic":
        return topic_problem(v)
    return None


# ══════════════════════════════════════════════════════════════════════════════════════════════
# THE STEPS
# ══════════════════════════════════════════════════════════════════════════════════════════════
# `link`, `copy` and `expect` are format strings over the context below; anything absent renders as
# a visible placeholder rather than an empty string, so a half-filled wizard never shows a link that
# looks complete and silently is not.
#
#   api_base     where our API lives          https://metricspro-production.up.railway.app
#   app_base     where this app lives         https://metricspro-five.vercel.app
#   gcp_project  their Google Cloud project   metrics-pro-506103
#   gcp_number   its project number           437700580502
#   da_project   Device Access project        c25e6a1e-...  (a UUID — NOT the above)
#   client_id    OAuth client id              4377...-xyz.apps.googleusercontent.com
#   topic        Pub/Sub topic short id       metricspro-camera-events
#   sa_email     push service account         vision-push@metrics-pro-506103.iam.gserviceaccount.com
#
# `needs` gates a step on earlier ones. `optional` steps can be skipped outright: a tenant that only
# wants live view and busy hours never has to stand up an analyzer, and one that does not want
# per-employee data never turns activity on.

STEPS = [
    # ── BEFORE YOU START ──────────────────────────────────────────────────────────────────────
    dict(
        key="overview", group="Before you start", title="What this involves", minutes=2,
        verify="ack",
        why="Three Google consoles, about 30 minutes, and one $5 fee. Everything you type here is "
            "saved as you go, so you can stop at any point and pick up where you left off — "
            "including on a different day or a different computer.",
        body=[
            "You will end up with: live camera view in the app, and busy-hours reporting that "
            "costs nothing to run because the cameras do the detection themselves.",
            "The $5 is Google's one-time Device Access registration fee, per company. It is "
            "non-refundable and there is no way around it — better to know now than at {step:device_access}.",
            "Everything else is free at your volume. Google charges for Pub/Sub messages, but a "
            "shop's worth of person events is fractions of a cent a month.",
            "You need to be signed in to the Google account that OWNS the cameras — the one the "
            "Nest app uses. If that is not you, get that person to sit with you for {step:authorize}.",
        ],
        gotcha="Do not start this on a phone. Two of the Google consoles are effectively unusable "
               "on a small screen, and you will be copying long strings between tabs.",
    ),

    # ── GOOGLE CLOUD ──────────────────────────────────────────────────────────────────────────
    dict(
        key="cloud_project", group="Google Cloud", title="Create a Google Cloud project", minutes=4,
        verify="value", field="gcp_project", check="cloud_project",
        link="https://console.cloud.google.com/projectcreate",
        why="This project holds the API access and, later, the message queue Google uses to tell us "
            "a camera saw somebody. It is free.",
        body=[
            "Give it any name you like — 'MetricsPro Cameras' is fine.",
            "When it is created, open the project picker at the top of the page. You will see a "
            "project ID (short, lowercase, like 'metricspro-cameras-481920') and a project NUMBER "
            "(all digits). Copy the ID into the box below.",
            "Already have a Cloud project you use for something else? You can reuse it. Nothing "
            "here interferes with what is already in it.",
        ],
        gotcha="The project NAME and the project ID are different, and the console shows the name "
               "more prominently. We need the ID — lowercase with dashes.",
        expect="A project id like 'metricspro-cameras-481920'.",
    ),
    dict(
        key="enable_api", group="Google Cloud", title="Switch on the camera API", minutes=1,
        needs=["cloud_project"], verify="ack",
        link="https://console.cloud.google.com/apis/library/smartdevicemanagement.googleapis.com"
             "?project={gcp_project}",
        why="A Cloud project can talk to no Google APIs until you enable each one. Without this, "
            "the connection in {step:authorize} fails with a permission error that does not mention the API.",
        body=[
            "The link opens the Smart Device Management API page with your project already "
            "selected. Press ENABLE.",
            "If the button already says MANAGE, it is on — carry on to the next step.",
        ],
        gotcha="Check the project name in the blue bar at the top before pressing Enable. If you "
               "have several Cloud projects, the console may open on the wrong one.",
        expect="The button changes to 'Manage' and the page shows API metrics.",
    ),
    dict(
        key="consent_screen", group="Google Cloud", critical=True,
        title="Set up the consent screen — and PUBLISH it", minutes=5,
        needs=["cloud_project"], verify="ack",
        link="https://console.cloud.google.com/apis/credentials/consent?project={gcp_project}",
        why="This is the screen you will see when you authorize in {step:authorize}. It is also the single "
            "most expensive mistake in this whole setup — see below.",
        body=[
            "Choose EXTERNAL as the user type, then fill in the app name, your support email and "
            "a developer contact email. Nothing else is required.",
            "On the Scopes screen, you do not need to add anything — we request our scope at "
            "sign-in time.",
            "THEN, on the summary page, find 'Publishing status' and press PUBLISH APP. Confirm "
            "when it asks.",
            "You may see a warning about verification. Ignore it: Google only requires "
            "verification for apps offered to the general public, and yours is used by your own "
            "staff on your own account.",
        ],
        gotcha="LEAVE IT IN 'TESTING' AND YOUR CAMERAS STOP WORKING IN SEVEN DAYS. Google expires "
               "refresh tokens from a Testing-mode consent screen after a week. Everything will "
               "work perfectly, and then next week the cameras go dark and it will look like our "
               "fault. Publishing takes one click and prevents it. If you skip this, expect to be "
               "reconnecting by hand every Monday.",
        expect="Publishing status reads 'In production'.",
    ),
    dict(
        key="oauth_client", group="Google Cloud", title="Create the sign-in credentials", minutes=4,
        needs=["consent_screen"], verify="value", field="client_id", check="client_id",
        link="https://console.cloud.google.com/apis/credentials?project={gcp_project}",
        why="These are the keys the app uses to ask for permission to see your cameras.",
        body=[
            "Press CREATE CREDENTIALS → OAuth client ID. Application type: WEB APPLICATION.",
            "Under 'Authorized redirect URIs', press ADD URI and paste BOTH of the addresses "
            "below — one at a time. Both are needed: one is this wizard, the other is the "
            "settings page you will use later.",
            "Press CREATE. Google shows the client ID and the client secret. Copy the ID into the "
            "box below. Keep the tab open, or download the JSON — you need the secret in {step:authorize} "
            "and Google will not show it again.",
        ],
        copy=[
            dict(label="Authorized redirect URI (1 of 2)", value="{app_base}" + WIZARD_PATH,
                 note="This wizard. Paste it exactly — Google compares character by character."),
            dict(label="Authorized redirect URI (2 of 2)", value="{app_base}" + SETTINGS_PATH,
                 note="The settings page, for reconnecting later."),
        ],
        gotcha="No trailing slash, and https not http. A redirect URI that differs by one character "
               "produces 'redirect_uri_mismatch' at {step:authorize} with no hint as to which character.",
        expect="A client ID ending in '.apps.googleusercontent.com', plus a secret you have saved.",
    ),

    # ── DEVICE ACCESS ─────────────────────────────────────────────────────────────────────────
    dict(
        key="device_access", group="Device Access ($5)",
        title="Register with Device Access", minutes=5,
        needs=["oauth_client"], verify="value", field="da_project", check="device_access_project",
        link="https://console.nest.google.com/device-access",
        why="Separate from Google Cloud, and separately paid for. This is what actually grants a "
            "program permission to talk to Nest cameras.",
        body=[
            "Accept the terms, then pay the $5 one-time fee. Per company, not per camera.",
            "Press CREATE PROJECT and name it anything.",
            "It asks for an OAuth client ID — paste the one from the last step (shown below).",
            "Leave events switched OFF for now. We come back for that in {step:topic}, once the topic "
            "it wants actually exists.",
            "When the project is created, copy its project ID into the box below. It is a long "
            "string of hex with dashes.",
        ],
        copy=[
            dict(label="OAuth client ID (from the last step)", value="{client_id}",
                 note="Device Access asks for this while creating the project."),
        ],
        gotcha="THIS PROJECT ID IS NOT THE ONE FROM {step:cloud_project}. This one is a UUID like "
               "'c25e6a1e-5f03-4b6a-9b72-3a9cf89fcbe3'. The Cloud one is a short lowercase name. "
               "Both are called 'project ID' and you now have both open in different tabs. If you "
               "paste the wrong one here the box below will tell you which one you pasted.",
        expect="A project id shaped like c25e6a1e-5f03-4b6a-9b72-3a9cf89fcbe3.",
    ),

    # ── EVENTS ────────────────────────────────────────────────────────────────────────────────
    dict(
        key="topic", group="Busy hours (optional)", optional=True,
        title="Create the message topic", minutes=3,
        needs=["cloud_project"], verify="value", field="topic", check="topic",
        link="https://console.cloud.google.com/cloudpubsub/topic/list?project={gcp_project}",
        why="Nest cameras already detect people on Google's side. A topic is the queue Google drops "
            "those sightings into, so we can build busy-hours reporting without touching video, "
            "without an in-store computer, and across every camera you own.",
        body=[
            "Press CREATE TOPIC. For the topic ID, use the suggestion below or your own name.",
            "Leave every other option at its default. Uncheck 'Add a default subscription' if it "
            "is ticked — we create a specific one in {step:push_subscription}.",
            "Skip this whole section if you only want live camera view. You can come back later.",
        ],
        copy=[dict(label="Suggested topic ID", value=SUGGESTED_TOPIC,
                   note="Anything works, as long as it does not start with 'goog'.")],
        gotcha="Device Access will ask you for a topic but WILL NOT create one. It has to exist "
               "here first, in your own Cloud project — that is why this step comes before it.",
        expect="The topic appears in the list.",
    ),
    dict(
        key="publisher_role", group="Busy hours (optional)", optional=True,
        title="Let Google publish into your topic", minutes=3,
        needs=["topic"], verify="ack",
        link="https://console.cloud.google.com/cloudpubsub/topic/list?project={gcp_project}",
        why="Your topic is private by default. Device Access is a separate Google system and has to "
            "be granted permission, by name, to write into it.",
        body=[
            "Tick the checkbox next to your topic, then open the PERMISSIONS panel on the right "
            "(you may need the 'Show info panel' button).",
            "Press ADD PRINCIPAL. Paste the principal below, give it the role below, and save.",
        ],
        copy=[
            dict(label="New principal", value=SDM_PUBLISHER,
                 note="A Google group, not a person. Paste it exactly."),
            dict(label="Role", value="Pub/Sub Publisher",
                 note=f"Shown in the role picker as 'Pub/Sub Publisher' ({PUBSUB_PUBLISHER_ROLE})."),
        ],
        gotcha="Miss this and {step:link_topic} fails with 'Device Access could not publish into the PubSub "
               "topic' — which is accurate but does not tell you it means this checkbox.",
        expect="The principal is listed against your topic with the Publisher role.",
    ),
    dict(
        key="link_topic", group="Busy hours (optional)", optional=True,
        title="Point Device Access at the topic", minutes=2,
        needs=["publisher_role", "device_access"], verify="ack",
        link="https://console.nest.google.com/device-access/project-list",
        why="Now that the topic exists and Google is allowed to write to it, Device Access can be "
            "told where to send camera events.",
        body=[
            "Open your Device Access project, find the three-dots menu on the Pub/Sub topic row, "
            "and choose 'Enable PubSub topic for Events'.",
            "Paste the FULL topic name below — not the short id you typed in {step:topic}.",
            "While you are on this page, check that 'Events' is enabled for the project.",
        ],
        copy=[dict(label="Full topic name", value="{full_topic}",
                   note="The whole thing, starting with 'projects/'.")],
        gotcha="The field is labelled 'PubSub topic name' and wants the long form. Pasting the "
               "short id gives 'PubSub topic name cannot be empty' or a validation error about "
               "allowed characters.",
        expect="The Pub/Sub topic row shows your topic instead of being blank.",
    ),
    dict(
        key="push_subscription", group="Busy hours (optional)", optional=True,
        title="Send the events to us", minutes=5,
        needs=["link_topic"], verify="ack",
        link="https://console.cloud.google.com/cloudpubsub/subscription/list?project={gcp_project}",
        why="The topic collects events; a push subscription is what forwards them to us. Without "
            "it, the sightings pile up in Google and never arrive.",
        body=[
            "Press CREATE SUBSCRIPTION. Pick your topic. Delivery type: PUSH.",
            "Paste the endpoint URL below.",
            "Tick ENABLE AUTHENTICATION and choose (or create) a service account. Any name will "
            "do — 'vision-push' is fine. Note the full service-account email; you need it below.",
            "Leave the audience blank unless you have a reason not to; if you set one, it must "
            "match the server setting exactly.",
            "Finally, an administrator sets VISION_PUBSUB_SA_EMAIL on the API server to that "
            "service-account email (and VISION_PUBSUB_AUDIENCE if you set an audience). Until "
            "those are set the endpoint refuses every push — deliberately, so that nobody can post "
            "fake camera events at us.",
        ],
        copy=[
            dict(label="Endpoint URL", value="{api_base}/api/v1/vision/google/events",
                 note="Where Google posts each sighting."),
            dict(label="Server setting · VISION_PUBSUB_SA_EMAIL", value="{sa_email}",
                 note="The service account you just picked. Set on the API server."),
        ],
        gotcha="A wrong push subscription is completely silent. Google retries into the void for "
               "days and no console anywhere shows a red mark. That is why the last step of this "
               "wizard has you walk past a camera while we watch.",
        expect="The subscription is listed as Push, with authentication enabled.",
    ),

    # ── CONNECT ───────────────────────────────────────────────────────────────────────────────
    dict(
        key="authorize", group="Connect", title="Connect your Google account", minutes=3,
        needs=["device_access"], verify="probe",
        why="The one step that happens here rather than in Google's consoles. You will be sent to "
            "Google, asked which cameras to share, and returned here.",
        body=[
            "Enter the client SECRET from {step:oauth_client} — we never store it in the browser and it is "
            "encrypted before it is written down.",
            "Press Connect. On Google's screen, TICK EVERY CAMERA you want in MetricsPro. Cameras "
            "you leave unticked are invisible to us, and adding one later means coming back here.",
            "You will land back on this page automatically.",
        ],
        gotcha="'Error 403: access_denied' here almost always means {step:consent_screen} was left in Testing "
               "mode and you are not on the test-user list. Publishing the consent screen fixes it. "
               "'redirect_uri_mismatch' means the URIs in {step:oauth_client} do not match byte for byte.",
        expect="This page says Connected, and {step:sync} can see your cameras.",
    ),
    dict(
        key="sync", group="Connect", title="Bring in the cameras", minutes=1,
        needs=["authorize"], verify="probe",
        why="Reads the camera list from your Google account into MetricsPro.",
        body=[
            "Press the button. Every camera you ticked appears with the name it has in the Google "
            "Home app.",
            "Nothing is streamed and no video is fetched — this is just the list.",
        ],
        gotcha="No cameras found, but you have some? You either did not tick them during {step:authorize}, "
               "or they live in a 'home' that has not been connected to this company yet — the "
               "settings page has a Homes section for that.",
        expect="One row per camera.",
    ),
    dict(
        key="assign_stores", group="Connect", title="Say which store each camera is in", minutes=3,
        needs=["sync"], verify="probe",
        why="Every report in the module groups by store. A camera with no store contributes to "
            "nothing — it will not appear in busy hours, traffic or any other report.",
        body=[
            "Pick a store for each camera from the dropdown.",
            "You can rename a camera here too if the Google Home name is not what your managers "
            "call it. The original name is kept underneath.",
        ],
        gotcha="This is the step most often left half-done, and it fails quietly: the camera works, "
               "the live view works, and the reports are simply missing that camera's data with no "
               "warning anywhere.",
        expect="No camera is left showing '— unassigned —'.",
    ),
    dict(
        key="entrance", group="Connect", optional=True,
        title="Mark the door cameras", minutes=2,
        needs=["assign_stores"], verify="probe",
        why="Only needed for directional counting and the heat map, both of which come from an "
            "in-store analyzer. Busy hours does not need this.",
        body=[
            "Tick 'Entrance' on the camera that watches the door at each store.",
            "If a store has no camera on the door, leave it — it just means no in/out count for "
            "that store.",
        ],
        gotcha="An analyzer with no entrance camera ticked runs happily and produces no numbers at "
               "all, which reads as a broken analyzer.",
        expect="At least one entrance camera at each store you want traffic counts for.",
    ),

    # ── PROVE IT ──────────────────────────────────────────────────────────────────────────────
    dict(
        key="walk_test", group="Prove it works", optional=True,
        title="Walk past a camera", minutes=2,
        needs=["sync"], verify="watch",
        why="The only real proof that the event chain — topic, permission, subscription, our "
            "endpoint — is working end to end. Nothing in Google's console will tell you this, and "
            "a broken chain looks exactly like a quiet shop.",
        body=[
            "Press the button below, then walk in front of one of your cameras, or ask somebody "
            "at the store to.",
            "We watch for the next two minutes and tell you the moment a sighting arrives.",
            "Give it a few seconds — Google batches these, so it is normal for one to take 10–30 "
            "seconds to come through.",
        ],
        gotcha="Nothing after two minutes, having done {step:topic} to {step:push_subscription}? The usual causes, in order of "
               "likelihood: the publisher permission in {step:publisher_role} was not saved; the full topic name "
               "in {step:link_topic} was the short id; the push endpoint has a typo; or the server settings "
               "in {step:push_subscription} have not been applied yet. Re-check them in that order.",
        expect="'Event received' — and busy hours starts filling in from now on.",
    ),
]

STEP_KEYS = [s["key"] for s in STEPS]
_BY_KEY = {s["key"]: s for s in STEPS}


def step_number(key: str) -> int:
    """1-based position of a step. The operator sees these numbers, and the prose refers to them."""
    return STEP_KEYS.index(key) + 1 if key in STEP_KEYS else 0


def _fmt(template, ctx: dict) -> str:
    """Render a step string against the context.

    Two token kinds:

      {step:key}   a CROSS-REFERENCE to another step, rendered as "step 7". Prose never carries a
                   literal step number, because prose numbers rot silently: insert one step near the
                   top and every later reference points one place off, sending the operator to the
                   wrong console page with nothing to tell them they were misdirected. Rendering
                   from the real position means a reorder cannot desynchronise them, and the harness
                   proves every reference names a step that exists.

      {value}      a context value. Anything not known yet renders as a VISIBLE placeholder rather
                   than an empty string — a link that silently lost its project id looks finished
                   and opens Google on whatever project happened to be selected."""
    out = str(template or "")
    for key in re.findall(r"\{step:(\w+)\}", out):
        n = step_number(key)
        out = out.replace("{step:" + key + "}", f"step {n}" if n else "an earlier step")
    for token in re.findall(r"\{(\w+)\}", out):
        val = str(ctx.get(token) or "").strip()
        out = out.replace("{" + token + "}", val or f"‹{token.replace('_', ' ')} — not set yet›")
    return out


def context(*, api_base="", app_base="", gcp_project="", gcp_number="", da_project="",
            client_id="", topic="", sa_email="") -> dict:
    """Everything the steps interpolate, in one place. `full_topic` is derived rather than stored,
    so the long form Device Access wants can never drift from the short id the operator typed."""
    ctx = dict(api_base=str(api_base or "").rstrip("/"), app_base=str(app_base or "").rstrip("/"),
               gcp_project=str(gcp_project or "").strip(),
               gcp_number=str(gcp_number or "").strip(),
               da_project=str(da_project or "").strip(),
               client_id=str(client_id or "").strip(),
               topic=str(topic or "").strip(),
               sa_email=str(sa_email or "").strip())
    ctx["full_topic"] = full_topic(ctx["gcp_project"], ctx["topic"])
    return ctx


def field_kit(step_key: str, ctx: dict) -> dict:
    """One step, fully resolved: where to click, what to paste, what goes wrong, what success is."""
    s = _BY_KEY.get(step_key)
    if not s:
        return {}
    return {
        "key": s["key"],
        "number": step_number(s["key"]),   # what the operator sees, and what the prose refers to
        "group": s["group"],
        "title": s["title"],
        "minutes": s.get("minutes"),
        # Prose goes through _fmt too — it is where the {step:key} cross-references live. Rendering
        # only the links and leaving the prose raw is exactly the bug that shipped a gotcha reading
        # "{step:link_topic}" to the operator.
        "why": _fmt(s.get("why"), ctx) if s.get("why") else "",
        "body": [_fmt(b, ctx) for b in (s.get("body") or [])],
        "gotcha": _fmt(s.get("gotcha"), ctx) if s.get("gotcha") else "",
        "expect": _fmt(s.get("expect"), ctx) if s.get("expect") else "",
        "link": _fmt(s["link"], ctx) if s.get("link") else "",
        "copy": [dict(label=c["label"], value=_fmt(c["value"], ctx), note=c.get("note") or "")
                 for c in (s.get("copy") or [])],
        "verify": s.get("verify") or "ack",
        "field": s.get("field") or "",
        "check": s.get("check") or "",
        "needs": list(s.get("needs") or []),
        "optional": bool(s.get("optional")),
        "critical": bool(s.get("critical")),
    }


def plan(ctx: dict, done: dict) -> list:
    """Every step in order, each marked done / blocked / current / todo.

    `done` maps step key -> bool. A step is BLOCKED while anything it needs is outstanding, and
    exactly one unblocked, unfinished step is CURRENT — the wizard shows that one and only that one.
    Optional steps never block anything and never make the wizard look unfinished."""
    out, current_taken = [], False
    done = done or {}
    for s in STEPS:
        kit = field_kit(s["key"], ctx)
        missing = [n for n in kit["needs"] if not done.get(n)]
        if done.get(s["key"]):
            state = "done"
        elif missing:
            state = "blocked"
        elif not current_taken:
            state, current_taken = "current", True
        else:
            state = "todo"
        out.append({**kit, "state": state, "blocked_by": missing})
    return out


def progress(steps: list) -> dict:
    """Headline counts. `required_left` is what decides whether setup is finished — an operator who
    never wants an analyzer or per-employee data must be able to reach 'done' without them."""
    req = [s for s in steps if not s["optional"]]
    return {
        "total": len(steps),
        "done": sum(1 for s in steps if s["state"] == "done"),
        "required_total": len(req),
        "required_done": sum(1 for s in req if s["state"] == "done"),
        "required_left": sum(1 for s in req if s["state"] != "done"),
        # Two clocks, because they answer different questions. `minutes_left_required` is time to a
        # WORKING setup, which is what the headline promises; `minutes_left` includes the optional
        # busy-hours and analyzer work. Reporting only the larger number makes a nearly-finished
        # setup look far off; reporting only the smaller one surprises anybody who does want events.
        "minutes_left_required": sum(int(s.get("minutes") or 0) for s in req if s["state"] != "done"),
        "minutes_left": sum(int(s.get("minutes") or 0) for s in steps if s["state"] != "done"),
        "optional_left": sum(1 for s in steps if s["optional"] and s["state"] != "done"),
        "complete": all(s["state"] == "done" for s in req),
    }


# ══════════════════════════════════════════════════════════════════════════════════════════════
# TRAP 2, CAUGHT AFTER THE FACT
# ══════════════════════════════════════════════════════════════════════════════════════════════

TESTING_TOKEN_DAYS = 7


def token_age_warning(days_old, linked: bool):
    """A warning when a connection is old enough to be about to hit the Testing-mode expiry, or None.

    We cannot read Google's publishing status through the API, so we cannot simply check whether the
    operator did step 4. What we CAN see is a connection quietly approaching seven days old, which
    is when a Testing-mode token dies. Warning at day five turns 'the cameras broke' into 'the
    cameras are about to break, and here is the one click that prevents it'.

    Deliberately advisory: a published app's token lives indefinitely and will sail past day seven
    with this warning never shown again, because refreshing resets the age."""
    if not linked or days_old is None:
        return None
    try:
        d = float(days_old)
    except (TypeError, ValueError):
        return None
    if d < TESTING_TOKEN_DAYS - 2:
        return None
    if d >= TESTING_TOKEN_DAYS:
        return ("This connection is more than seven days old. If your consent screen is still in "
                "Testing mode, it has already expired or is about to — publish it and "
                "reconnect.")
    return ("This connection is nearly seven days old. If you left the consent screen in Testing "
            "mode it will expire within days. Publishing it takes one click and is "
            "permanent.")


def explain_google_error(message):
    """Map a Google failure onto the step that actually causes it.

    Every one of these cost real time to diagnose the first time, and none of Google's messages
    names the checkbox at fault. Returns None when we have nothing specific to add — an invented
    explanation is worse than the raw error."""
    m = str(message or "").lower()
    if not m:
        return None
    if "redirect_uri_mismatch" in m or "redirect uri" in m:
        return ("The redirect address does not match what is registered on the OAuth client. Go "
                "back to the 'Create the sign-in credentials' step and check both URIs — byte for "
                "byte, no trailing slash, https.")
    if "access_denied" in m or "has not completed the google verification" in m:
        return ("Google refused the sign-in. Almost always this is the consent screen still being "
                "in Testing mode (the 'Set up the consent screen' step) with your account not on "
                "the test-user list. Publish it.")
    if "invalid_grant" in m:
        return ("The stored authorization is no longer valid. The usual cause is a Testing-mode "
                "consent screen expiring after seven days — publish it, then reconnect.")
    if "invalid_client" in m:
        return "The client id or secret does not match the OAuth client you created."
    if "403" in m and "smartdevicemanagement" in m:
        return ("The Smart Device Management API is not enabled on this Cloud project — that is "
                "the 'Switch on the camera API' step.")
    if "enterprise" in m and ("not found" in m or "404" in m):
        return ("Google does not recognise that Device Access project id. Check the Device "
                "Access step — it is the "
                "UUID from console.nest.google.com, not the Cloud project id.")
    return None
