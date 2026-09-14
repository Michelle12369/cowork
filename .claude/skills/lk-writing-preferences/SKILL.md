---
name: lk-writing-preferences
description: LK's writing preferences for PR descriptions, docs, commit messages, comments, and replies in this repo. Use whenever writing or editing prose the user will read or publish — a PR body, a spec, a README section, a review reply, a commit message — and before asking the user to review a draft. Encodes the corrections the user has made repeatedly - structure and ordering, density, heading vocabulary, linking, and which mechanical checks to skip for prose-only changes.
---

# LK writing preferences

These are corrections the user has made more than once. Apply them before handing over a
draft; do not wait to be told again.

## Structure and ordering

- **Context, then deliverable, then reviewer aids.** A feature PR reads: 背景 (or 需求) →
  what it does and how data flows → 先看這裡 (ranked review table) → the one or two things
  important enough to stand in the body (for example an error-code table) with doc links →
  副作用 → 驗證 → 留意. Verification and caveats go last.
- **Pick headings by document type.** 為什麼會這樣 is a bug-fix heading (a defect and its
  cause). A feature merge opens with 背景 or 需求. Never copy headings from a PR of a
  different kind.
- **One home per fact.** Before publishing, scan for semantic duplicates across sections
  and delete all but one. Typical offenders: a step list that narrates a diagram, a "does
  not do X" list beside a diagram that already shows what it does, a caveat restated in
  both the definition and the notes, a side effect that repeats the intro.
- **Merge sections that answer the same question** instead of keeping them adjacent.
- **A diagram beats prose that narrates it.** Keep the diagram, cut the step list.

## Density

- **Cut what the reader can get from the code, the diagram, or a linked doc.** A "what
  changed" section is a table plus links, not a tour of every file.
- **Keep what is important enough to stand on its own.** A small contract table (five
  error codes, who acts on each) stays in the body; the per-failure mapping goes to a link.
- **Whittle the trailing sections.** 驗證 and 留意 each fit in three to five bullets. Test
  counts per file, conflict-resolution logs, and per-hop folding rules belong in the commit
  message or a linked spec, not the PR.
- **Short sentences, half-width punctuation in Chinese prose** (`, ` and `. `), matching the
  repo's existing PR bodies. Define a term at first use.

## Terms

- **Define a term once, then use exactly that term.** Do not stack synonyms (宿主 and
  "host bridge" for the same thing) or introduce a second label the reader has to map.
- **Point terms at concrete artifacts.** An abstract role (宿主) must name its real
  counterpart in the tree (`spike/mcp-shell/shell.html`, `bridge.py`) and, if the product
  piece is unbuilt, say so.

## Links and navigation

- **Hyperlink every referenced doc, spec, plan, fixture, and reviewed file** to its GitHub
  blob URL on the PR's head branch. The reviewer never searches the tree.
- **Review guide is a table in reading order**, PR #83 style: `#` / file (linked) / why.
  Order by the code path, not by risk: entry point (route, schemas, request context)
  first, then the chat-time path (landing, adapter), then the view-time path (prelude,
  classification), then wiring, model-facing text, and best-effort tooling last. Say the
  ordering rule in the lead-in. Add an optional 風險 column (高 / 中 / 低) when reading
  order and risk order differ, so the reader knows where to spend depth without the table
  being resorted: 高 for an external boundary or code every artifact runs, 中 for wiring
  and model-facing text, 低 for best-effort tooling; define the scale in the lead-in. One
  略讀 line for what to skim. No pre-reading paragraph; the 背景 section and the doc links
  carry the background.
- **Inline short content, link long content.** Do not send the reader to a spec for a
  table that fits in ten lines.

## Process

- **Prose-only changes skip mechanical gates.** Do not run ruff, tests, or formatters for a
  PR-description or doc edit; say so instead of reporting a run.
- **Decide from evidence, show the footprint first.** When the user is weighing whether to
  disable or remove something, list every touchpoint (wiring, model-facing text, tests,
  docs) before recommending.
- **Encode a recurring practice into tooling.** If the user asks for the same section or
  check twice, put it into the relevant skill as a required item rather than only into
  the current document.

## Pre-publish checklist

1. Headings match the document type (feature vs bug fix).
2. Section order: context → deliverable → reviewer aids → side effects → verification → notes.
3. Every fact appears once; diagrams are not narrated.
4. Every doc or file mentioned is hyperlinked.
5. Trailing sections are three to five bullets each.
6. Each term is defined once and points at a real artifact.
7. No gate was run for a prose-only change, and the reply says so.
