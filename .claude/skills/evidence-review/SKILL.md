---
name: evidence-review
description: Evidence-based code review for a PR, branch, diff, file, or a single suspicious piece of code. Turns "this looks odd" into verified findings by enumerating concrete failure modes, executing checks against the real code (snippets, stress tests, test suites, library source), ranking by criticality, and documenting exactly what was tested and how. Use this whenever the user asks to review code, review a PR or branch, asks "did I miss anything", "is this risky", "is this worth raising", "what should I comment", "why does this code do X", or pastes a diff or snippet and wants an opinion — even when they only ask about one function. Also use it when an agent's review claims need to be checked before they are repeated to a human.
---

# Evidence Review

A review comment is a claim about behavior. Eyeballing produces observations; only
execution produces findings. Reviewers waste most of their time in two places: reading
code top-down to understand it, and deliberating over whether an observation is worth
raising. This skill replaces both with a fixed loop:

    triage → eyeball → enumerate failure modes → verify each one → rank → document

Every step exists for a reason, explained inline. Follow the order; the order is the
time-saver.

## Ground rules

- **Read-only.** A review never edits the code under review. If the user wants fixes,
  that is a separate task after the report.
- **A claim about behavior is a hypothesis until it runs.** This applies to your own
  reading, to library behavior you "remember", and doubly to anything a subagent
  reports. Never call something a blocker you have not reproduced or read at the exact
  line.
- **Two exchanges per item.** If an item is still open after two rounds of
  investigation, decide: comment, ask the author, or park it in a notes file. Rabbit
  holes about "best practice" are learning, not review; park them.
- **Report what was not verified as loudly as what was.** A reader must be able to
  tell verified findings from plausible ones and know which gates could not be run.

## Phase 0 — Triage (bounded to ~15 minutes)

Goal: decide where depth is worth it before reading any implementation.

1. Identify base and head. Confirm the merge base exists (`git merge-base HEAD <base>`);
   shallow clones need `git fetch --deepen`.
2. Run `scripts/triage.sh <base>` (or the equivalent by hand): lines changed per area,
   code files vs test files, config/dependency/secret-suspect files.
3. **Read the author's material before the code**: PR description, spec, plan, commit
   subjects. Authors usually list known limitations and accepted trade-offs. Every item
   already there moves to the "acknowledged" bucket and costs zero review time. Skipping
   this step is the single most common way to waste an hour.
4. Rank changed modules by risk. Depth goes to, in order: trust boundaries (external
   input → prompt, DB, filesystem, shell), secrets and identity, persistence and
   irreversible writes, concurrency, error paths, prompt/LLM-facing text, config
   defaults. Everything else gets a skim.
5. Start the project's test gates **now, in the background** (unit suites per component,
   lint/typecheck). They take minutes and their result is a finding either way.

## Phase 1 — Eyeball (comprehension, not judgment)

Goal: build a model of the change fast enough to spot embedded assumptions.

- Read from the contract inward: what enters (wire shape, request DTO), what leaves
  (what the model sees, what is persisted, what the user sees), what crosses a trust
  boundary. Then read only the code on those paths.
- Produce a call graph or data-flow sketch for the core module (who calls whom, with what
  data). Ask a subagent for it if the module is large; reading files is slow, asking
  "who calls whom" is fast.
- Read the tests before the implementation when both exist: tests state intent faster.
- Keep a working list of **candidate observations**: one line each, "X assumes Y". Do
  not judge them yet. Typical sources: an unconditional branch ("every response lands
  as a table"), a comment that explains *what* but not *why we stopped here*, a cap
  that is checked before the thing it caps is measured, a prompt that names a tool.

## Phase 2 — Enumerate failure modes

Goal: turn each observation into something falsifiable.

For each candidate write three fields. If you cannot fill all three, it is a question
for the author, not a finding.

    trigger:   concrete input or state that reaches the code
    outcome:   what goes wrong, observably (wrong error code, orphan row, corrupted
               preview, silent 1-row table, model told to call a tool that doesn't exist)
    radius:    who is affected and whether it is loud or silent

Walk the families in `references/failure-modes.md` against the ranked modules. The
families that produce real findings most often, in this order:

1. **Shape drift at boundaries** — empty, `{}`, `null`, scalar where list expected,
   list of scalars, huge, CRLF, non-UTF-8, unicode names.
2. **Error-path mapping** — which exception classes get which branch; anything thrown
   *before* a write that a generic handler assumes happened *after* it.
3. **Contract drift** — prompt text vs. actual tools; docs vs. code; a test that pins
   the wrong behavior; a cap that isn't a cap.
4. **Trust** — external text into prompt or SQL identifiers, path escape, secret or
   internal hostname in an error message that reaches a user, spoofable identity
   headers, fail-open config defaults.
5. **Concurrency** — shared path derived from shared key, work outside the lock that
   feeds work inside it, retries on non-idempotent operations.
6. **Presentation to an LLM** — unescaped delimiters, row caps without byte caps,
   framing that misdescribes the data.

Silent failures rank above loud ones at equal severity: a loud failure gets reported by
someone; a silent one ships.

## Phase 3 — Verify (the heart of the process)

Goal: for each failure mode, run the cheapest check that is decisive, and record it.

Pick from this ladder, cheapest first, stopping at the first decisive result:

1. **Read the exact lines** and the call sites (`grep` the symbol; do not trust the diff
   alone, the diff hides context).
2. **Run the real function** with the trigger input in the project's environment (venv,
   test DB). Print the outcome. This is usually one snippet and settles most shape
   questions in one exchange.
3. **Read the pinned library source** in the venv or `node_modules` when the claim is
   about library behavior. Cite file, line, and version.
4. **Write a throwaway stress test** for concurrency claims (N trials, real threads,
   real payload). If it does not reproduce, say so and downgrade; explain the window
   honestly instead of asserting it.
5. **Run the relevant test file**, then the suite, and read the failures' root cause
   before attributing them to the change (environment failures look like test failures).
6. **Check git history** (`git log -S<symbol>`) when the question is intent: whether a
   removal was deliberate, what a previous reviewer already accepted.

Rules while verifying:

- Record, for every check, the command or snippet, the inputs, and the literal output.
  This goes into the report verbatim; it is what lets the author trust the finding
  without redoing it.
- Classify the result: **verified** (reproduced or read at the line), **plausible**
  (mechanism confirmed, not reproduced), **refuted** (drop it, but keep the note so the
  author sees it was checked). Only verified items can be blockers.
- When a subagent reports a finding you intend to repeat, re-verify its top claims
  yourself with step 1 at minimum. Subagents overstate; you own what you post.
- Do not fix while verifying. A snippet that demonstrates the failure is evidence; a
  patch is scope creep.

## Phase 4 — Rank

Assign each surviving item one level. Criteria, not vibes:

| level | criteria |
|---|---|
| **Blocker** | verified; wrong user-visible behavior, data corruption, or security exposure on a normal path |
| **Major** | verified; reachable by plausible misuse, config drift, or a documented future input; or silent wrong behavior on an edge path |
| **Minor / nit** | rare or narrow; cheap to fix; confusing when it hits (unreproducible errors) |
| **Process** | gates not run, plan or PR description stale, missing final-review verdict, test pinning a bug |
| **Acknowledged** | already listed by the author; the only question is whether "accepted" is right |
| **Refuted / checked fine** | investigated, no issue; listed so coverage is visible |

Order within a level by reachability × blast radius × silence, then by cost to fix
(cheaper first, so the author can clear the list top-down).

Cap the posted list. Five to eight comments with evidence beat twenty observations; a
long review gets skimmed. Everything else goes to a plan file or a notes section.

## Phase 5 — Document

Use `references/report-template.md`. The non-negotiable parts:

- **Verification status first**: which gates ran, the command, the result, and which
  could not run and why (an environment failure is not a code failure, say which).
- **Each finding carries**: location (`file:line`), the claim in one sentence, the
  trigger → outcome, the evidence (how it was verified, with the literal output), the
  ask (change / add comment / decide), and the level.
- **Acknowledged** and **checked-and-fine** sections, so the author can see what was
  covered and does not re-explain what is already known.
- **Not verified** list for anything plausible that stayed plausible.

For each comment you actually post, use one shape so wording stops being a decision:

    My assumption: <what the code seems to intend>.
    Observed: <what ran / what line says>, <literal result>.
    Ask: <change | add a comment saying … | decide …>.
    Level: <blocker | major | nit | optional>.

Confirm assumptions before suggesting when you are not certain of intent: "my
understanding is X; if so, could you Y". It is faster for the author than a bare
suggestion they have to argue with.

## Working with subagents

Large diffs are reviewed by area, in parallel, with an identical brief: the risk order
above, "verify every claim by reading the actual code or running it", a cap on findings,
and a required "checked and found fine" list. Keep judgment for yourself: which items
matter, what the contract is, what is out of scope. Re-verify anything you will call a
blocker. Never repeat a subagent's severity label unexamined.

## Anti-patterns that burn review time

- Reading the implementation before the PR description and spec.
- Asking "is this best practice / how common is this" during a review. Park it.
- Four rounds of "how should I word this". Use the comment shape above.
- Reporting from the diff without opening the file; the diff hides the call sites.
- Declaring a concurrency bug from reasoning alone; run the stress test, report the
  trial count.
- A twenty-item review. Rank, cut, move the tail to a plan file.
- Fixing during review.
