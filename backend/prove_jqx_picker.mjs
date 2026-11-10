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

const PICK_JS = pyString("_PICK_JQX_ITEM_JS");
const SELECTORS = pyTuple("JQX_PICKER_SELECTORS");
const WIDGETS = pyTuple("JQX_PICKER_WIDGETS");

// ── a fake page ──────────────────────────────────────────────────────────────────────────────────
class FakeHost {
  constructor({ id, cls, role, widget, items, selected }) {
    this.id = id;
    this.className = cls || "";
    this.role = role || null;
    this.widget = widget;          // which jqWidgets family this control answers to
    this.items = items || [];
    this.selected = selected ?? null;
  }
}

function makeDocument(hosts) {
  const matchOne = (host, sel) => {
    let m;
    if ((m = sel.match(/^\[class\*=["']([^"']+)["']\]$/))) return host.className.includes(m[1]);
    if ((m = sel.match(/^\[role=["']([^"']+)["']\]$/))) return host.role === m[1];
    if ((m = sel.match(/^\.([A-Za-z0-9_-]+)$/))) return host.className.split(/\s+/).includes(m[1]);
    throw new Error(
      `the fake DOM does not understand the selector ${JSON.stringify(sel)}. ` +
      `Teach it that shape here before shipping a scan that uses it — an unparsed selector in ` +
      `production matches nothing and reports the control as absent.`);
  };
  return {
    querySelectorAll(selector) {
      const parts = selector.split(",").map((s) => s.trim()).filter(Boolean);
      return hosts.filter((h) => parts.some((p) => matchOne(h, p)));
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
  return fn([wants, SELECTORS, WIDGETS]);
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

console.log("§H  a picker that exists but has not filled in is NOT absent (COMMCALC-60)");
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

console.log("");
if (failed) { console.log(`${failed} check(s) FAILED`); process.exit(1); }
console.log("all checks OK");
