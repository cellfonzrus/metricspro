'use client'
// ── RUNBOOK DOCUMENT RENDERER ────────────────────────────────────────────────────────────────────
// Owner directive 2026-09-10: "All of these will be in the training module under a tile called
// flowcharts and named appropriately."
//
// The four operating runbooks (cash, daily closing, DM verify, scheduling) are procedures staff are
// told to follow, so they belong INSIDE the app next to the screens they describe — not in a chat
// transcript or a file somebody has to be sent.
//
// WHY THE CONTENT IS STRUCTURED DATA AND NOT AN HTML BLOB. The obvious shortcut is a `body_html`
// column rendered with dangerouslySetInnerHTML, which would also make a runbook tenant-editable.
// That is a stored-XSS hole: any writer of a training row gets script execution in every reader's
// authenticated session. So a runbook is TYPED CONTENT (`lib/flowcharts`) rendered by this
// component — no HTML is ever injected, and a new runbook is a data object, not a new page.
//
// NO NEW TABLE, NO MIGRATION. The Training Center's TOURS are data because their content changes per
// tenant; these are platform procedures that ship and version with the code that implements them —
// a runbook whose steps disagree with the build is worse than no runbook. Renaming or hiding one per
// tenant already has a mechanism (`commcalc.ui_label_override`) if it is ever wanted; adding an
// override table nobody has asked for would be speculation.
import Link from 'next/link'
import type { ReactNode } from 'react'

export type RunbookLevel = {
  /** Heading — what this level DOES, phrased as an action ("The DM verifies, then collects"). */
  title: string
  /** Who this is, in their words ("Level 2 · the collector"). */
  who: string
  /** Which lane colour this level owns; kept consistent across every runbook. */
  tone: 'rep' | 'dm' | 'mm' | 'acct' | 'own'
  intro: string
  steps: { title: string; body: ReactNode }[]
  /** The bar that says when this level is finished. Deliberately concrete. */
  done?: ReactNode
}

export type RunbookRule = {
  claim: string
  why: ReactNode
  /** ok = a guarantee, hold = a deliberate pause, bad = a refusal. Encodes meaning, not decoration. */
  kind: 'ok' | 'hold' | 'bad'
}

export type RunbookTable = {
  heading: string
  intro?: string
  columns: [string, string, string]
  rows: { key: string; means: ReactNode; move: string }[]
}

/**
 * One screen in the workflow, in the order the work actually happens.
 *
 * THIS IS THE SOURCE, NOT A COPY OF ONE (owner directive 2026-09-10: "the modules for these should
 * be stacked properly based on thr work flow in one tile … and also the current module should ask
 * the chart what do they want to do next"). The same array drives the stacked hub tile AND the
 * "what next" prompt at the foot of each screen, so the order a user is walked through can never
 * drift from the order the runbook teaches — there is one list, and both readers ask it.
 */
export type RunbookStage = {
  /** A NAV href. Gating is the destination's own NAV entry, via the shared `useCanOpen`. */
  href: string
  /** The screen's name as the user sees it in the menu. */
  label: string
  /** Whose job this stage is. */
  who: string
  /** One line: what you do here. Shown under the link, so nobody has to open it to find out. */
  does: string
  /** What you are handing to whoever is next. Rendered as the reason to go there. */
  handoff?: string
}

export type Runbook = {
  slug: string
  title: string
  /** One line under the title in the Flowcharts list. */
  summary: string
  /** Nav module key, so the list can group and filter the same way the tour list does. */
  module: string
  /** Who should read it — matches the tour audience vocabulary. */
  audience: string
  icon: string
  /** Levels named on the cover, e.g. "Rep → DM → Market Mgr". */
  chain: string
  lede: string
  /** Cross-reference to the sibling runbooks, so the set reads as a set. */
  kin?: ReactNode
  /** The diagram. Drawn as inline SVG so it themes with the app and needs no image host. */
  figure: ReactNode
  figcaption: ReactNode
  levels: RunbookLevel[]
  rulesHeading: string
  rules: RunbookRule[]
  table?: RunbookTable
  /** The screens this procedure runs through, in order. See RunbookStage. */
  stages: RunbookStage[]
}

const TONE: Record<RunbookLevel['tone'], string> = {
  rep: 'var(--rb-rep)', dm: 'var(--rb-dm)', mm: 'var(--rb-mm)',
  acct: 'var(--rb-acct)', own: 'var(--rb-own)',
}

export function RunbookDoc({ doc }: { doc: Runbook }) {
  return (
    <article className="rb">
      <RunbookStyles />
      <header className="rb-mast">
        <Link href="/training?tab=flowcharts" className="rb-back">← Flowcharts</Link>
        <p className="rb-eyebrow">MetricsPro · Operating procedure</p>
        <h1 className="rb-h1">{doc.title}</h1>
        <p className="rb-lede">{doc.lede}</p>
        <div className="rb-meta">
          <span>{doc.levels.length} levels</span>
          <span>{doc.chain}</span>
        </div>
        {doc.kin && <p className="rb-kin">{doc.kin}</p>}
      </header>

      <figure className="rb-fig">
        <div className="rb-scroll">{doc.figure}</div>
        <figcaption>{doc.figcaption}</figcaption>
      </figure>

      {doc.levels.map(lv => (
        <section key={lv.title} className="rb-level" style={{ ['--tone' as string]: TONE[lv.tone] }}>
          <div className="rb-levelhead">
            <h2 className="rb-h2">{lv.title}</h2>
            <span className="rb-who">{lv.who}</span>
          </div>
          <p className="rb-intro">{lv.intro}</p>
          <ol className="rb-steps">
            {lv.steps.map(s => (
              <li key={s.title}>
                <div>
                  <h3 className="rb-h3">{s.title}</h3>
                  <div className="rb-stepbody">{s.body}</div>
                </div>
              </li>
            ))}
          </ol>
          {lv.done && <div className="rb-done"><strong>Done when</strong>{lv.done}</div>}
        </section>
      ))}

      <section className="rb-rules">
        <p className="rb-eyebrow">Controls</p>
        <h2 className="rb-h2">{doc.rulesHeading}</h2>
        <div className="rb-rulelist">
          {doc.rules.map(r => (
            <div key={r.claim} className={`rb-rule rb-${r.kind}`}>
              <span className="rb-claim">{r.claim}</span>
              <span className="rb-why">{r.why}</span>
            </div>
          ))}
        </div>
      </section>

      {doc.table && (
        <section className="rb-rules">
          <p className="rb-eyebrow">Reference</p>
          <h2 className="rb-h2">{doc.table.heading}</h2>
          {doc.table.intro && <p className="rb-intro">{doc.table.intro}</p>}
          <div className="rb-tablewrap">
            <table className="rb-table">
              <thead><tr>{doc.table.columns.map(c => <th key={c}>{c}</th>)}</tr></thead>
              <tbody>
                {doc.table.rows.map(r => (
                  <tr key={r.key}>
                    <td className="rb-mono">{r.key}</td>
                    <td>{r.means}</td>
                    <td>{r.move}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <footer className="rb-foot">
        Every step here maps to a screen in MetricsPro. Where this runbook and a screen disagree,
        the screen is the record — and the disagreement is worth reporting.
      </footer>
    </article>
  )
}

/** A pill used inside step and table copy to name a state the screen actually shows. */
export function Pill({ tone, children }: { tone: 'green' | 'amber' | 'red'; children: ReactNode }) {
  return <span className={`rb-pill rb-pill-${tone}`}>{children}</span>
}

/** A field or state name exactly as it appears in the product. */
export function F({ children }: { children: ReactNode }) {
  return <span className="rb-f">{children}</span>
}

// Scoped to `.rb` so a runbook can never restyle the app around it. Colours come from the platform
// tokens where they exist (`--accent`, `--text2`) and are otherwise declared here for BOTH themes —
// a lane colour defined only under one theme is the classic unreadable-in-dark-mode bug.
function RunbookStyles() {
  return (
    <style>{`
.rb {
  --rb-rep:#1d6b4f; --rb-dm:#2f6f8f; --rb-mm:#7a5aa8; --rb-acct:#96701a; --rb-own:#a8321f;
  --rb-seal:#1d6b4f; --rb-seal-dim:#e3efe8;
  --rb-short:#a8321f; --rb-short-dim:#f7e6e2;
  --rb-hold:#96701a; --rb-hold-dim:#f6eed8;
  --rb-ink:var(--text1,#16201c); --rb-soft:var(--text2,#4a5a52); --rb-mute:var(--text3,#74857c);
  --rb-rule:var(--border,#d6e0da); --rb-card:var(--surface,#fff); --rb-soft-bg:var(--surface2,#eef3f0);
  max-width:78ch; margin:0 auto; padding-bottom:72px;
  font-size:15.5px; line-height:1.55; color:var(--rb-ink);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) .rb {
    --rb-rep:#5fc79b; --rb-dm:#7fb8d6; --rb-mm:#b79ae0; --rb-acct:#e0b455; --rb-own:#f0917c;
    --rb-seal:#5fc79b; --rb-seal-dim:#163024;
    --rb-short:#f0917c; --rb-short-dim:#331913;
    --rb-hold:#e0b455; --rb-hold-dim:#322813;
  }
}
:root[data-theme="dark"] .rb {
  --rb-rep:#5fc79b; --rb-dm:#7fb8d6; --rb-mm:#b79ae0; --rb-acct:#e0b455; --rb-own:#f0917c;
  --rb-seal:#5fc79b; --rb-seal-dim:#163024;
  --rb-short:#f0917c; --rb-short-dim:#331913;
  --rb-hold:#e0b455; --rb-hold-dim:#322813;
}
.rb-back { font-size:12.5px; color:var(--rb-mute); text-decoration:none; display:inline-block; margin-bottom:14px; }
.rb-back:hover { color:var(--rb-seal); }
.rb-mast { border-bottom:2px solid var(--rb-ink); padding-bottom:18px; margin-bottom:26px; }
.rb-eyebrow { font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:.13em;
  color:var(--rb-mute); margin:0 0 10px; }
.rb-h1 { font-size:clamp(26px,4vw,40px); font-weight:700; letter-spacing:-.022em; line-height:1.06;
  margin:0 0 12px; text-wrap:balance; }
.rb-h2 { font-size:clamp(18px,2vw,22px); font-weight:700; letter-spacing:-.012em; margin:0; text-wrap:balance; }
.rb-h3 { font-size:15px; font-weight:600; margin:0 0 3px; }
.rb-lede { font-size:17px; color:var(--rb-soft); margin:0 0 18px; }
.rb-meta { display:flex; flex-wrap:wrap; gap:8px 20px; font-size:11.5px; color:var(--rb-mute);
  text-transform:uppercase; letter-spacing:.05em; font-variant-numeric:tabular-nums; }
.rb-kin { margin:10px 0 0; font-size:13px; color:var(--rb-mute); }

.rb-fig { margin:0 0 8px; }
.rb-scroll { overflow-x:auto; padding-bottom:6px; }
.rb-scroll svg { display:block; width:100%; height:auto; }
.rb-fig figcaption { font-size:13px; color:var(--rb-mute); margin-top:10px; }

.rb-level { margin-top:40px; padding-left:18px; border-left:3px solid var(--tone,var(--rb-rule)); }
.rb-levelhead { display:flex; flex-wrap:wrap; align-items:baseline; gap:6px 12px; margin-bottom:4px; }
.rb-who { font-size:11px; font-weight:500; text-transform:uppercase; letter-spacing:.09em; color:var(--tone); }
.rb-intro { color:var(--rb-soft); margin:0 0 4px; }
.rb-steps { margin:16px 0 0; padding:0; list-style:none; counter-reset:rbs; display:grid; gap:15px; }
.rb-steps > li { counter-increment:rbs; display:grid; grid-template-columns:28px 1fr; gap:13px; }
.rb-steps > li::before { content:counter(rbs); font-size:12px; color:var(--tone);
  border-top:2px solid var(--tone); padding-top:5px; font-weight:600; font-variant-numeric:tabular-nums; }
.rb-stepbody { font-size:14.8px; color:var(--rb-soft); }
.rb-stepbody p { margin:0; }
.rb-stepbody p + p { margin-top:7px; }

.rb-done { margin-top:18px; padding:11px 15px; border-radius:4px; background:var(--rb-seal-dim);
  border-left:3px solid var(--rb-seal); font-size:14.5px; }
.rb-done strong { display:block; font-size:11px; text-transform:uppercase; letter-spacing:.1em;
  color:var(--rb-seal); margin-bottom:3px; }

.rb-rules { margin-top:50px; border-top:2px solid var(--rb-ink); padding-top:24px; }
.rb-rulelist { display:grid; gap:2px; margin-top:18px; }
.rb-rule { display:grid; gap:4px; padding:13px 15px; background:var(--rb-card); border:1px solid var(--rb-rule); }
.rb-rule + .rb-rule { border-top:none; }
.rb-claim { font-weight:600; font-size:15px; }
.rb-why { font-size:14.5px; color:var(--rb-soft); }
.rb-ok { border-left:3px solid var(--rb-seal); }
.rb-hold { border-left:3px solid var(--rb-hold); }
.rb-bad { border-left:3px solid var(--rb-short); }

.rb-tablewrap { overflow-x:auto; margin-top:18px; }
.rb-table { border-collapse:collapse; width:100%; min-width:540px; font-size:14px; }
.rb-table th, .rb-table td { text-align:left; padding:9px 12px; border-bottom:1px solid var(--rb-rule); vertical-align:top; }
.rb-table th { font-size:11px; text-transform:uppercase; letter-spacing:.09em; color:var(--rb-mute); font-weight:600; }
.rb-mono { font-family:ui-monospace,Menlo,Consolas,monospace; font-size:13px; white-space:nowrap; }

.rb-f { font-family:ui-monospace,Menlo,Consolas,monospace; font-size:.88em;
  background:var(--rb-soft-bg); padding:.1em .36em; border-radius:3px; overflow-wrap:anywhere; }
.rb-pill { display:inline-block; font-size:10.5px; font-weight:700; text-transform:uppercase;
  letter-spacing:.07em; padding:2px 7px; border-radius:2px; }
.rb-pill-green { background:var(--rb-seal-dim); color:var(--rb-seal); }
.rb-pill-amber { background:var(--rb-hold-dim); color:var(--rb-hold); }
.rb-pill-red { background:var(--rb-short-dim); color:var(--rb-short); }

.rb-foot { margin-top:52px; padding-top:16px; border-top:1px solid var(--rb-rule);
  font-size:13px; color:var(--rb-mute); }

@media (max-width:560px) {
  .rb-steps > li { grid-template-columns:22px 1fr; gap:10px; }
  .rb-level { padding-left:14px; }
}
    `}</style>
  )
}
