# Pre-launch legal checklist

**I am not a lawyer, and this is not legal advice.** What follows are drafts written to fit how
MetricsPro actually works, using the facts in this repository. They are a serious starting point — not
a substitute for review by a licensed attorney in your state. Have one read them before you publish.
The review is short and cheap compared with any single claim these documents are meant to blunt.

One thing worth saying plainly, because it is the premise of the request: **documents do not stop
lawsuits.** They decide who wins, how much is at stake, and where the fight happens. What actually
keeps you out of court is the operational discipline in §2 — the consent forms actually signed, the
signage actually posted, the feature left switched off.

---

## 1. What is drafted, and what each is for

| Document | Protects against |
|---|---|
| `legal/terms.html` | The main contract. Liability cap, warranty disclaimer, indemnity from the customer, arbitration and class-action waiver, and the wage-and-hour and biometric responsibility shifts. **The single most important page here.** |
| `legal/privacy.html` | Privacy-law exposure and the disclosure duties under CCPA/CPRA and the other state laws. Also what app stores and enterprise customers ask to see. |
| `legal/eula.html` | The software licence, and the clauses **Apple requires** in an app EULA. Missing these can hold up a review. |
| `legal/acceptable-use.html` | Gives you clean grounds to suspend an account, and distances you from what a customer does with the tool. |
| `legal/biometric-policy.html` | The publicly available written retention schedule **BIPA requires you to have**. See §2. |
| `legal/subprocessors.html` | Standard due-diligence answer; supports the "we don't sell data" position. Lists provider **categories**, not company names — a confirmed public vendor list mainly helps someone impersonate one of those vendors in a phishing attempt. Names go out on request. |
| `legal/cookies.html` | Accurate today because the site sets no cookies. Stops being accurate the moment you add analytics. |

## 2. The five things that actually create lawsuit risk here

Ranked by how much damage they can do to a business your size.

### 1. Biometrics — the big one

Illinois BIPA carries a **private right of action with statutory damages per violation**, and
plaintiff firms file these in volume against employers using biometric time clocks. Texas and
Washington have their own statutes; more states are adding them.

The exposure is real but the defence is simple, and it is procedural rather than technical:

- [ ] Face recognition stays **off** unless there is a concrete reason to turn it on. It is currently
      off across the platform — leaving it there is the cheapest risk reduction available to you.
- [ ] If it is ever turned on: a signed **written release** from every affected employee, obtained
      **before** any capture, disclosing the purpose and the retention schedule.
- [ ] The retention policy is **publicly available** — that is what publishing
      `legal/biometric-policy.html` accomplishes. Have it live before the feature is enabled anywhere.
- [ ] Never sell, lease, trade or profit from a template. Prohibited outright.
- [ ] Every business using your platform must do the same. The Terms require it; whether they comply
      is the risk you are indemnified for, not immune from.

### 2. Audio recording

Video is comparatively low-risk. **Audio is not.** Many states require consent from every party to a
conversation, and violations can be criminal as well as civil.

The Vision module **can** process audio, and it is built defensively — but the protection is
configuration, and configuration can be changed by whoever holds the switch:

- A **global kill switch** (`VISION_AUDIO_ENABLED`) turns the audio path off for every company. It is
  off unless explicitly set, and switching it off stops the microphone at the analyzer within a minute.
- Audio is **not stored**: an utterance is transcribed and the buffer destroyed. No audio file is
  written to disk and none is posted to the platform. This is a real mitigation — there is no
  recording to produce in discovery — but note that under most two-party-consent statutes the
  violation is the **interception**, not the retention. Not keeping the audio is not a defence to
  having listened.
- Consent is tracked per employee, with an `audio_consent_mode` of `required` or `off`.

- [ ] **Leave the global kill switch off** unless there is a specific, advised reason to enable it.
      This is the highest-risk switch in the product.
- [ ] Understand what `audio_consent_mode: off` means before anyone sets it: it is an operator
      **asserting they already hold recorded consent**. If that assertion is wrong, the exposure is
      criminal in some states, and the setting is a written record of a deliberate choice.
- [ ] Remember that employee consent does not cover the **customers** standing at the counter. In an
      all-party-consent state, a conversation between a rep and a member of the public needs that
      person's consent too — which means posted notice at minimum, and state-specific advice before
      you rely on it.

### 3. Wage and hour

You compute hours, commissions and payroll figures. When a rep sues over unpaid commissions or
off-the-clock time, your customer is the defendant — but you will be named or subpoenaed, and the
plaintiff will argue your calculation caused the shortfall.

- [ ] Terms §10 puts employer duties squarely on the customer. Keep that language.
- [ ] Never describe the product as ensuring compliance, guaranteeing accuracy, or replacing a payroll
      provider — in marketing copy, in a demo, or in an email. **A sales promise can override a
      contract disclaimer.**

### 4. Data breach involving identifiers — **resolved**

**This exposure has been removed.** Migration 909 erased and dropped every SSN and driver's licence
field, and the code that captured, stored, decrypted and displayed them is gone.

A correction to what I first told you: the POS held **full** SSNs and **full** driver's licence
numbers, encrypted, not merely the last four digits. The last-4 was only what the screen displayed.
So the exposure being closed here was larger than my first note described — these are precisely the
categories that trigger the breach-notification statutes all fifty states have, and holding them in
volume is what turns one incident into fifty notification obligations.

The strongest possible position on a category of data is not to hold it, and that is now the
position. Keep it:

- [ ] Do not reintroduce SSN or licence capture without a specific, advised business reason. If a
      carrier ever demands proof of an identity check, ask exactly what they need retained before
      building storage for it — a signature or a checkbox is often sufficient, and neither triggers
      a notification statute.
- [ ] Keep the incident-response plan in `docs/INCIDENT_RESPONSE_PLAN.md` current and know who calls
      the lawyer. Employee and customer data still lives in the platform; this removes one category,
      not the duty.
- [ ] Consider **cyber liability insurance**. For a platform holding employee and customer data, this
      is ordinary cost of business, and it pays for the lawyer you would otherwise pay for yourself.

### 5. Messaging

The platform sends WhatsApp and email. TCPA claims carry statutory damages per message and attract
class actions.

- [ ] Keep it to business notifications to your customers' own staff. No marketing to consumers.
- [ ] Honour opt-outs immediately and keep the record that you did.

## 3. Corporate hygiene

- [x] **Which entity is the contracting party** — settled. **IT Solutions of LI Inc** is the company;
      **Cellfonz R Us** is the first tenant, a customer of the platform like any other. Every legal
      page already reads that way, and the internal biometric policy — which had named Cellfonz R Us
      as the platform operator — has been corrected to match. Keep it consistent from here: two
      customer-facing documents naming different companies for the same product is the kind of
      inconsistency opposing counsel enjoys finding.
- [ ] Worth knowing, because it affects who gets sued: as operator, IT Solutions of LI Inc is in
      **possession** of tenant data, while each tenant is the **employer** that collects it. Biometric
      and employment claims can reach both roles. Cellfonz R Us being both your own business and a
      tenant does not merge the two — keep the platform's contracts with it on the same footing as
      with any other tenant, or the separation is easy for a plaintiff to argue away.
- [ ] Confirm the entity is in good standing and registered where it needs to be.
- [ ] Keep corporate formalities — separate bank accounts, real records. The liability cap in the
      Terms protects the company; corporate formalities are what protect **you personally**.
- [ ] Register the **DMCA agent** with the U.S. Copyright Office (about $6, online). Naming an agent
      on a page without registering provides no safe harbour.
- [ ] Consider trademarking "MetricsPro" if the name matters commercially — and search first that
      nobody else holds it.

## 4. Consistency to maintain

- [ ] The app already serves a privacy policy at `/privacy` (referenced by the app stores). The
      website policy is a superset. **Two policies that say different things is worse than one that is
      slightly out of date** — either point the app page at the website URL, or update both together.
      My recommendation: make the website version canonical and have the app link to it.
- [ ] Link Terms and Privacy from the **signup screen** and record acceptance with a timestamp. An
      agreement nobody was shown is hard to enforce.
- [ ] Keep a dated archive of every version. Being able to prove which terms were in force on a given
      date is what makes them enforceable.

## 5. Where these drafts are deliberately incomplete

- **No Data Processing Addendum.** Enterprise customers will ask for one; it can wait until one does.
- **No insurance-backed uptime or service-level commitment.** The Terms promise no uptime, which is
  the right posture until you can meet one.
- **No international coverage.** The Privacy Policy tells non-US users not to use the Service without
  a separate written agreement. If you sell into the EEA or UK, you need GDPR terms and transfer
  mechanisms — a different conversation.
- **No accessibility statement.** ADA website claims are a live area. Worth asking your attorney
  about.
