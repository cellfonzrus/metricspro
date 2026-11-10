/**
 * THE REGRESSION, EXECUTED (index §COMMCALC-58).
 *
 * On 2026-11-10 the owner photographed the comp report's filter panel. "Summarize by" was right
 * there, offering Daily / Weekly / Monthly, while every sweep run reported "no 'Summarize by'
 * dropdown offering Daily was found on the report's filter panel". The portal had re-skinned that
 * one control from a plain jqWidgets list to the SEARCHABLE one (its popup carries a "Looking for"
 * box), and our scan knew exactly one styling class: `.jqx-dropdownlist-state-normal`.
 *
 * A PYTHON TEST CANNOT CATCH THAT. The defect lived in a string of browser JavaScript that no test
 * executed, so the fix has to be proven the only way it can fail: by RUNNING that JavaScript
 * against a page shaped like the photograph.
 *
 * This proof reads the REAL source out of epay_sweep.py — never a copy, which would diverge — and
 * runs it against a fake DOM. The fake deliberately THROWS on any selector form it does not
 * understand, so a scan that grows a new shape fails loudly here instead of silently matching
 * nothing in production, which is the exact failure mode being locked out.
 */
import { readFileSync } from "node:fs";

const SRC = "app/modules/commcalc/epay_sweep.py";
const py = readFileSync(SRC, "utf8");

// ── pull the real strings out of the module ──────────────────────────────────────────────────────
function pyString(name) {
  const m = py.match(new RegExp(name + '\\s*=\\s*"""([\\s\\S]*?)"""'));
  if (!m) throw new Error(`${name} is not a triple-quoted string in ${SRC}`);
  return m[1];
}
function pyTuple(name) {
  const m = py.match(new RegExp(name + "\\s*=\\s*\\(([\\s\\S]*?)\\)"));
  if (!m) throw new Error(`${name} is not a tuple in ${SRC}`);
  // Match whole Python string literals: a single-quoted selector legitimately CONTAINS double
  // quotes ('[class*="jqx-combobox"]'), so a character class would stop at the first one.
  return [...m[1].matchAll(/'([^']*)'|"([^"]*)"/g)].map((x) => (x[1] !== undefined ? x[1] : x[2]));
}

function pyInt(name) {
  const m = py.match(new RegExp("^" + name + "\\s*=\\s*([0-9_]+)\\s*$", "m"));
  if (!m) throw new Error(`${name} is not a module-level int in ${SRC}`);
  return parseInt(m[1].replace(/_/g, ""), 10);
}

const PICK_JS = pyString("_PICK_JQX_ITEM_JS");
const SELECTORS = pyTuple("JQX_PICKER_SELECTORS");
const WIDGETS = pyTuple("JQX_PICKER_WIDGETS");
// Read from the module, never retyped: a proof carrying its own copy of the sample size would
// keep passing after the real one changed (§COMMCALC-62).
const SAMPLE = pyInt("PICKER_SAMPLE");

// ── a fake page ──────────────────────────────────────────────────────────────────────────────────
// A REAL TREE, not a flat list. jqWidgets builds ONE control out of nested divs that carry the same
// `jqx-*` classes and ARIA roles as their host (§COMMCALC-63), and a fake with no parent/child
// relationship cannot express that at all — a proof that cannot express the defect is not a proof.
// The nodes also carry the text and attributes a caption is read from, because "what is this
// control called" is answered from the page and nowhere else.
class FakeHost {
  constructor({ id, cls, role, widget, items, selected, ariaLabel, tag, htmlFor, text, children }) {
    this.id = id;
    this.className = cls || "";
    this.role = role || null;
    this.widget = widget;          // which jqWidgets family this control answers to
    this.items = items || [];
    this.selected = selected ?? null;
    this.ariaLabel = ariaLabel || null;
    this.tagName = (tag || "div").toUpperCase();
    this.htmlFor = htmlFor || null;
    this.ownText = text || "";
    this.children = children || [];
    this.parentElement = null;
    for (const kid of this.children) kid.parentElement = this;
  }
  get textContent() {
    return this.ownText + this.children.map((k) => k.textContent).join("");
  }
  getAttribute(name) {
    if (name === "aria-label") return this.ariaLabel;
    if (name === "role") return this.role;
    if (name === "for") return this.htmlFor;
    if (name === "id") return this.id || null;
    return null;
  }
  contains(other) {
    if (other === this) return true;
    return this.children.some((k) => k.contains(other));
  }
  get previousElementSibling() {
    const sibs = this.parentElement ? this.parentElement.children : [];
    const i = sibs.indexOf(this);
    return i > 0 ? sibs[i - 1] : null;
  }
}

/** A caption printed beside a control — what a person would call it. */
const caption = (words, extra = {}) => new FakeHost({ tag: "span", text: words, ...extra });

function flatten(nodes, out = []) {
  for (const n of nodes) { out.push(n); flatten(n.children, out); }
  return out;
}

function makeDocument(roots) {
  // Every page has a body, so a top-level control has somewhere to walk up to, as it does on the
  // portal. The body carries no jqx class and no role, so it is never a candidate itself.
  const body = new FakeHost({ id: "body", tag: "body", children: roots });
  const all = flatten([body]);
  const matchOne = (host, sel) => {
    let m;
    if ((m = sel.match(/^\[class\*=["']([^"']+)["']\]$/))) return host.className.includes(m[1]);
    if ((m = sel.match(/^\[role=["']([^"']+)["']\]$/))) return host.role === m[1];
    if ((m = sel.match(/^\.([A-Za-z0-9_-]+)$/))) return host.className.split(/\s+/).includes(m[1]);
    if ((m = sel.match(/^label\[for=["']([^"']+)["']\]$/)))
      return host.tagName === "LABEL" && host.htmlFor === m[1];
    throw new Error(
      `the fake DOM does not understand the selector ${JSON.stringify(sel)}. ` +
      `Teach it that shape here before shipping a scan that uses it — an unparsed selector in ` +
      `production matches nothing and reports the control as absent.`);
  };
  return {
    querySelectorAll(selector) {
      const parts = selector.split(",").map((s) => s.trim()).filter(Boolean);
      return all.filter((h) => parts.some((p) => matchOne(h, p)));
    },
    querySelector(selector) {
      return this.querySelectorAll(selector)[0] || null;
    },
  };
}

function makeJQuery() {
  const api = (host) => {
    const call = (family) => (cmd, arg) => {
      if (host.widget !== family) throw new TypeError(`${family} is not this element's widget`);
      if (cmd === "getItems") return host.items.map((label) => ({ label }));
      if (cmd === "selectItem") {
        if (!host.items.includes(arg)) throw new Error(`no such item ${arg}`);
        host.selected = arg;
        return undefined;
      }
      if (cmd === "getSelectedItem") return host.selected === null ? null : { label: host.selected };
      throw new Error(`unknown command ${cmd}`);
    };
    const out = {};
    for (const w of ["jqxDropDownList", "jqxComboBox", "jqxListBox"]) out[w] = call(w);
    return out;
  };
  return api;
}

function run(hosts, wants) {
  const fn = eval(`(${PICK_JS.trim()})`);
  global.document = makeDocument(hosts);
  global.window = { jQuery: makeJQuery() };
  return fn([wants, SELECTORS, WIDGETS, SAMPLE]);
}

// ── the panel in the photograph ──────────────────────────────────────────────────────────────────
const THE_PHOTOGRAPHED_PANEL = () => [
  new FakeHost({
    id: "SummarizeBy",
    cls: "jqx-widget jqx-combobox jqx-combobox-state-normal",   // the SEARCHABLE flavour
    widget: "jqxComboBox",
    items: ["Daily", "Weekly", "Monthly"],
    selected: "Please Choose:",
  }),
  new FakeHost({
    id: "PaymentCategory",
    cls: "jqx-widget jqx-combobox jqx-combobox-state-normal",
    widget: "jqxComboBox",
    items: ["Activation", "Upgrade"],
    selected: "Please Choose:",
  }),
];

// ── the checks ───────────────────────────────────────────────────────────────────────────────────
let failed = 0;
const ok = (name, cond, note = "") => {
  if (cond) { console.log(`  OK   ${name}`); }
  else { failed += 1; console.log(`  FAIL ${name}${note ? " — " + note : ""}`); }
};

console.log("§A  the photographed panel (2026-11-10) — a searchable combobox");
{
  const hosts = THE_PHOTOGRAPHED_PANEL();
  const r = run(hosts, ["Daily"]);
  ok("A1 the control is found", r.ok === true, `got ${JSON.stringify(r)}`);
  ok("A2 it is driven to Daily", r.hit && r.hit.selected === "Daily", JSON.stringify(r.hit));
  ok("A3 the right control moved", hosts[0].selected === "Daily" && hosts[1].selected === "Please Choose:");
  ok("A4 the family is reported", r.hit && r.hit.widget === "jqxComboBox");
}

console.log("§B  the OLD flavour still works — this widened, it did not move");
{
  const hosts = [new FakeHost({
    id: "SummarizeBy",
    cls: "jqx-widget jqx-dropdownlist jqx-dropdownlist-state-normal",
    widget: "jqxDropDownList",
    items: ["Daily", "Weekly", "Monthly"],
    selected: "Please Choose:",
  })];
  const r = run(hosts, ["Daily"]);
  ok("B1 a plain dropdownlist is still found", r.ok === true && r.hit.selected === "Daily");
  ok("B2 and reported as its own family", r.hit.widget === "jqxDropDownList");
}

console.log("§C  a control in a state OTHER than 'normal' is the same control");
{
  const hosts = [new FakeHost({
    id: "SummarizeBy",
    cls: "jqx-widget jqx-combobox jqx-combobox-state-hover",   // the owner's finger is on it
    widget: "jqxComboBox",
    items: ["Daily", "Weekly", "Monthly"],
    selected: null,
  })];
  ok("C1 a hovered control is found", run(hosts, ["Daily"]).ok === true);
}

console.log("§D  what the panel offers is reported, so the next failure answers itself");
{
  const hosts = [new FakeHost({
    id: "SummarizeBy", cls: "jqx-combobox jqx-combobox-state-normal", widget: "jqxComboBox",
    items: ["Hourly", "Weekly"], selected: null,
  })];
  const r = run(hosts, ["Daily"]);
  ok("D1 not finding the value is a failure", r.ok === false);
  ok("D2 and the panel's real options are named", (r.offered || []).join(",") === "Hourly,Weekly",
     JSON.stringify(r.offered));
  ok("D3 'some pickers, none offering it' is its own sentence", /offer/i.test(r.why || ""), r.why);
}
{
  const r = run([], ["Daily"]);
  ok("D4 a panel with NO picker at all says so differently", r.ok === false && /no picker/i.test(r.why || ""), r.why);
  ok("D5 and offers nothing", (r.offered || []).length === 0);
}

console.log("§E  the label is matched on its meaning, not its punctuation");
{
  for (const spelling of ["daily", " Daily ", "Daily:", "DAILY"]) {
    const hosts = [new FakeHost({
      id: "s", cls: "jqx-combobox-state-normal", widget: "jqxComboBox",
      items: [spelling, "Weekly"], selected: null,
    })];
    const r = run(hosts, ["Daily"]);
    ok(`E ${JSON.stringify(spelling)} matches Daily`, r.ok === true && hosts[0].selected === spelling);
  }
}

console.log("§F  a picker that answers to neither declared family is left alone");
{
  const hosts = [new FakeHost({
    id: "other", cls: "jqx-listbox jqx-listbox-state-normal", widget: "jqxListBox",
    items: ["Daily"], selected: null,
  })];
  const r = run(hosts, ["Daily"]);
  ok("F1 it is not driven through an API it does not answer to", r.ok === false && hosts[0].selected === null);
  ok("F2 and it is not counted as a picker that was asked", (r.pickers || 0) === 0);
}

console.log("§G  the month picker's shape (the sibling caller) goes through the same routine");
{
  const hosts = [new FakeHost({
    id: "Month", cls: "jqx-dropdownlist-state-normal", widget: "jqxDropDownList",
    items: ["May- 26", "June- 26", "July- 26"], selected: "July- 26",
  })];
  const r = run(hosts, ["June 2026", "June 26"]);
  ok("G1 'June- 26' is reached from 'June 26'", r.ok === true && hosts[0].selected === "June- 26");
}

console.log("§H  a picker that exists but has not filled in is NOT absent (COMMCALC-61)");
{
  // THE LIVE SHAPE, 2026-11-10 16:30, read off production: the dealer list had loaded and the
  // filter control had not, so the ONLY picker carrying anything was the account selector - and
  // the empty one was skipped entirely, so the operator was told the panel had one control.
  const hosts = [
    new FakeHost({
      id: "DealerList", cls: "jqx-combobox jqx-combobox-state-normal", widget: "jqxComboBox",
      items: ["474517 - Passaic wireless 2022 LLC(230)", "498001 - Aureon Wireless Inc."],
      selected: null,
    }),
    new FakeHost({
      id: "SummarizeBy", cls: "jqx-combobox jqx-combobox-state-normal", widget: "jqxComboBox",
      items: [],                                   // present on the page, not yet populated
      selected: null,
    }),
  ];
  const r = run(hosts, ["Daily"]);
  ok("H1 the empty control is COUNTED as a picker", r.pickers === 2, JSON.stringify(r));
  ok("H2 …and reported as not ready rather than skipped", r.ready === 1 && r.empty === 1);
  ok("H3 the candidates the selectors matched are reported too", r.candidates === 2);
  ok("H4 the sentence says one is still empty", /still empty/.test(r.why || ""), r.why);
  ok("H5 and it is NOT the 'no picker at all' sentence", !/no picker/.test(r.why || ""));
}
{
  // Every picker still empty: the page was read before it finished building. Its own sentence,
  // because the fix is to wait, not to widen the scan.
  const hosts = [new FakeHost({
    id: "SummarizeBy", cls: "jqx-combobox-state-normal", widget: "jqxComboBox",
    items: [], selected: null,
  })];
  const r = run(hosts, ["Daily"]);
  ok("H6 a wholly unfilled panel says it was read too early",
     r.ok === false && /finished filling/.test(r.why || ""), r.why);
  ok("H7 …and that is a third sentence, distinct from the other two",
     !/none offering it/.test(r.why || "") && !/no picker was found/.test(r.why || ""));
}
{
  // The empty control must not poison a panel where the value IS available.
  const hosts = [
    new FakeHost({id: "Empty", cls: "jqx-combobox-state-normal", widget: "jqxComboBox",
                  items: [], selected: null}),
    new FakeHost({id: "SummarizeBy", cls: "jqx-combobox-state-normal", widget: "jqxComboBox",
                  items: ["Daily", "Weekly"], selected: null}),
  ];
  const r = run(hosts, ["Daily"]);
  ok("H8 a ready picker is still driven with an empty one beside it",
     r.ok === true && hosts[1].selected === "Daily");
}

console.log("§I  no one control may drown the report (COMMCALC-62)");
{
  // THE LIVE SHAPE, 2026-11-10 18:30, read off production on the §COMMCALC-61 code:
  //
  //     the panel has 3 picker(s) with options, none offering it (and 10 still empty)
  //     (looked 4 times over 4.5s while the panel settled)
  //
  // THIRTEEN controls, and the sentence the operator got was almost entirely ONE of them: the
  // dealer selector carries hundreds of accounts, `offered` merged every label on the panel, and
  // the recorder truncated at 600 characters. The two other loaded controls were in that message
  // and neither could be read - which is why it took three rounds to learn the panel's real shape.
  const dealers = [];
  for (let i = 0; i < 480; i++) dealers.push(`${474517 + i} - Wireless 2024 LLC`);
  const hosts = [
    new FakeHost({
      id: "DealerList", cls: "jqx-combobox jqx-combobox-state-normal", widget: "jqxComboBox",
      items: dealers, selected: null,
    }),
    new FakeHost({
      id: "PaymentCategory", cls: "jqx-dropdownlist jqx-dropdownlist-state-normal",
      widget: "jqxDropDownList", items: ["All", "Commission", "Residual"], selected: null,
    }),
    new FakeHost({
      id: "SummarizeBy", cls: "jqx-combobox jqx-combobox-state-normal", widget: "jqxComboBox",
      items: [], selected: null,                   // still filling in, as it was at 18:30
    }),
  ];
  const r = run(hosts, ["Daily"]);

  ok("I1 every control is described, not merged into one list",
     Array.isArray(r.panel) && r.panel.length === 3, JSON.stringify(r.panel));
  ok("I2 each control is named",
     r.panel.map((e) => e.id).join(",") === "DealerList,PaymentCategory,SummarizeBy");
  ok("I3 the huge control reports its REAL size", r.panel[0].options === 480);
  ok("I4 …but contributes only a bounded sample", r.panel[0].sample.length === SAMPLE);
  ok("I5 the small control's options are NOT crowded out",
     r.offered.includes("All") && r.offered.includes("Commission"), JSON.stringify(r.offered));
  ok("I6 so the whole list stays bounded by the controls, not by one of them",
     r.offered.length <= 3 * SAMPLE, `offered=${r.offered.length}`);
  ok("I7 the unfilled control is reported as present with no options",
     r.panel[2].options === 0 && r.panel[2].sample.length === 0);
  ok("I8 and the count still separates ready from empty",
     r.pickers === 3 && r.ready === 2 && r.empty === 1, r.why);
}
{
  // A sample size of zero would silently empty the report. It is read from the module, so this
  // check is about the VALUE the module declares, not about this file's own copy.
  ok("I9 the declared sample is a usable size", SAMPLE > 0 && SAMPLE <= 20, `SAMPLE=${SAMPLE}`);
}
{
  // The value is still DRIVEN when a late control finally carries it - the per-control report
  // must not have turned the scan into a read-only describer.
  const hosts = [
    new FakeHost({
      id: "DealerList", cls: "jqx-combobox", widget: "jqxComboBox",
      items: ["474517 - Passaic wireless 2022 LLC(230)"], selected: null,
    }),
    new FakeHost({
      id: "SummarizeBy", cls: "jqx-combobox", widget: "jqxComboBox",
      items: ["Daily", "Weekly", "Monthly"], selected: null,
    }),
  ];
  const r = run(hosts, ["Daily"]);
  ok("I10 the control is still SET, not merely described",
     r.ok === true && hosts[1].selected === "Daily", JSON.stringify(r));
  ok("I11 …and the panel report comes back alongside the hit", r.panel.length === 2);
}

console.log("§J  a thing inside a thing is not a second control (COMMCALC-63)");
{
  // THE LIVE SHAPE, 2026-11-10 19:30, read off production on the §COMMCALC-62 code:
  //
  //   the panel has 2 picker(s) with options, none offering it (and 11 still empty) … the panel's
  //   controls are … listBoxContentjqxWidget0206f1e1: empty; innerListBoxjqxWidgetaa329b45: empty;
  //   innerListBoxjqxWidget5ea7ca38: empty; innerListBoxjqxWidget0206f1e1: empty;
  //   jqxWidget349f357e: empty; dropDownButtonContentjqxWidget349f357e: empty
  //
  // Two things in that sentence. The names are jqWidgets' own internal pieces, and the hashes
  // REPEAT — `0206f1e1` twice, `349f357e` twice — so one control was being counted several times
  // through its own scaffolding. And an inner piece can never carry items, so `empty > 0` was
  // permanently true: every tick burnt its whole wait budget and then reported a stall.
  //
  // The fake reproduces that faithfully: the inner pieces carry the same jqx-* class as their host
  // AND answer the same widget API, with nothing in them. That is what made them invisible as
  // scaffolding and countable as controls.
  const dealers = [];
  for (let i = 0; i < 480; i++) dealers.push(`${474517 + i} - Wireless 2024 LLC`);
  const inner = (hash) => new FakeHost({
    id: "innerListBoxjqxWidget" + hash, cls: "jqx-combobox jqx-combobox-state-normal",
    widget: "jqxComboBox", items: [], selected: null,
  });
  const roots = [
    caption("Dealer"),
    new FakeHost({
      id: "jqxWidgetaa329b45", cls: "jqx-widget jqx-combobox jqx-combobox-state-normal",
      widget: "jqxComboBox", items: dealers, selected: null,
      children: [inner("aa329b45")],
    }),
    caption("Payment Category"),
    new FakeHost({
      id: "jqxWidget5ea7ca38", cls: "jqx-widget jqx-combobox jqx-combobox-state-normal",
      widget: "jqxComboBox", items: ["All", "Commission", "Residual"], selected: null,
      children: [inner("5ea7ca38")],
    }),
    caption("Summarize by"),
    new FakeHost({
      id: "jqxWidget0206f1e1", cls: "jqx-widget jqx-combobox jqx-combobox-state-normal",
      widget: "jqxComboBox", items: ["Daily", "Weekly", "Monthly"], selected: "Please Choose:",
      children: [
        new FakeHost({ id: "listBoxContentjqxWidget0206f1e1",
                       cls: "jqx-combobox jqx-combobox-state-normal", widget: "jqxComboBox",
                       items: [], children: [inner("0206f1e1")] }),
      ],
    }),
    caption("Start Date"),
    // The date control: it matches the selectors but answers to NEITHER declared picker family, so
    // it is a candidate and never a picker — and its button scaffolding must not be one either.
    new FakeHost({
      id: "jqxWidget349f357e", cls: "jqx-widget jqx-dropdownlist jqx-datetimeinput",
      widget: "jqxDateTimeInput", items: [],
      children: [new FakeHost({ id: "dropDownButtonContentjqxWidget349f357e",
                                cls: "jqx-dropdownlist", widget: "jqxDateTimeInput", items: [] })],
    }),
  ];
  const r = run(roots, ["Daily"]);

  ok("J1 only the OUTERMOST matches are candidates — four hosts, not nine",
     r.candidates === 4, `candidates=${r.candidates}`);
  ok("J2 three of them are pickers we can drive", r.pickers === 3, `pickers=${r.pickers}`);
  ok("J3 nothing is reported as empty, because the empties were scaffolding",
     r.empty === 0 && r.ready === 3, `ready=${r.ready} empty=${r.empty}`);
  ok("J4 no internal piece appears in the panel report",
     !r.panel.some((e) => /^(innerListBox|listBoxContent|dropDownButtonContent)/.test(e.id || "")),
     JSON.stringify(r.panel.map((e) => e.id)));
  ok("J5 the repeated hashes collapse to one control each",
     new Set(r.panel.map((e) => (e.id || "").replace(/^\D+/, ""))).size === r.panel.length,
     JSON.stringify(r.panel.map((e) => e.id)));
  ok("J6 each control carries the caption printed beside it, not its generated id",
     r.panel.map((e) => e.label).join("|") === "Dealer|Payment Category|Summarize by",
     JSON.stringify(r.panel.map((e) => e.label)));
  ok("J7 and the control the sweep came for is actually SET",
     r.ok === true && r.panel.length === 3, JSON.stringify(r));
  ok("J8 the value landed on the real control, not on its inner piece",
     roots[5].selected === "Daily" && roots[5].children[0].selected === null);
}
{
  // Not every panel labels its controls the same way. All three routes are on the page, so all
  // three are proven here; an unlabelled control still has to come back describable.
  const roots = [
    new FakeHost({ id: "a", cls: "jqx-combobox", widget: "jqxComboBox", items: ["Weekly"],
                   ariaLabel: "Summarize by", selected: null }),
    new FakeHost({ id: "b", tag: "label", htmlFor: "c", text: "Payment Category" }),
    new FakeHost({ id: "c", cls: "jqx-combobox", widget: "jqxComboBox", items: ["All"],
                   selected: null }),
    new FakeHost({ id: "d", cls: "jqx-combobox", widget: "jqxComboBox", items: ["x"],
                   selected: null }),
  ];
  const r = run(roots, ["nothing-matches"]);
  const by = {};
  r.panel.forEach((e) => { by[e.id] = e.label; });
  ok("J9 an aria-label is the control's name", by.a === "Summarize by", JSON.stringify(by));
  ok("J10 a <label for> is the control's name", by.c === "Payment Category", JSON.stringify(by));
  ok("J11 an unlabelled control comes back with no name rather than a wrong one",
     by.d === null, JSON.stringify(by));
}
{
  // The nesting rule must not swallow a REAL sibling control that happens to sit inside a panel
  // div which also matches — the outermost rule is about one control's own parts, and a container
  // that is itself a picker is not a container.
  const kid = new FakeHost({ id: "SummarizeBy", cls: "jqx-combobox", widget: "jqxComboBox",
                             items: ["Daily"], selected: null });
  const roots = [new FakeHost({ id: "panelwrap", cls: "jqx-widget", children: [kid] })];
  const r = run(roots, ["Daily"]);
  ok("J12 a control inside a non-matching wrapper is still found",
     r.ok === true && kid.selected === "Daily", JSON.stringify(r));
}

console.log("");
if (failed) { console.log(`${failed} check(s) FAILED`); process.exit(1); }
console.log("all checks OK");
