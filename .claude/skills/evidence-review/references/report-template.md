# Review report template

Use this shape for the final report. Sections may be short; none may be omitted, so a
reader can tell what was covered from what was not.

```
# Review: <PR title or branch> (<head sha>, base <base>)

## Verification status
| gate | command | result |
|---|---|---|
| python unit | `.venv/bin/pytest -q` | 418 passed |
| frontend | `npx vitest run` | 346 passed |
| backend | `./mvnw test` | 652 run, 78 errors — all MongoTimeoutException: embedded replica set did not start in this sandbox (no mongod binary). Not a code failure; needs a run where embedded Mongo works. |
| CI check runs | GitHub | none configured; gates are manual |

## Findings (ranked)

### 1. <one-sentence claim> — <BLOCKER | MAJOR | NIT>
- Where: `path/file.ext:line`
- Trigger → outcome: <concrete input or state> → <observable wrong behavior>
- Evidence: <how verified> — <command or snippet>, output: `<literal output>`
- Ask: <change X | add a comment saying Y | decide Z>
- Tests: <which test covers it / which is missing>

### 2. …

## Acknowledged by the author
- <item> (PR description §…) — accepted / worth revisiting because …

## Checked and found fine
- <area>: <what was checked, how>

## Not verified (plausible only)
- <claim> — mechanism: … — could not reproduce in <N> trials / not runnable here because …

## Process
- <stale plan checkboxes, missing verdict in PR description, missing CI, …>
```

## Per-comment shape (for anything posted inline)

```
My assumption: <what the code seems to intend>.
Observed: <what ran or which line>, result: <literal>.
Ask: <change | add a comment saying … | decide …>.
Level: <blocker | major | nit | optional>.
```

Two variants that come up constantly:

**Confirm-then-ask** (when intent is uncertain):
> My understanding is that this check is deliberately required-only and exists to
> protect the shared call budget — is that right? If so, could you capture that in the
> comment so it doesn't grow into hand-rolled schema validation later?

**Accepted-but-document** (when the behavior is a valid trade-off):
> Fine to leave as is; a column that changes type after row 20,480 is the provider's
> data problem. Please add a comment saying so, so the resulting exception isn't
> treated as a bug here.

## Severity levels

| level | criteria |
|---|---|
| Blocker | verified; wrong user-visible behavior, data corruption, or security exposure on a normal path |
| Major | verified; reachable via plausible misuse, config drift, or a documented future input; or silent wrong behavior on an edge path |
| Nit | rare or narrow; cheap; confusing when hit |
| Process | gates, plans, descriptions, tests pinning bugs |

Order within a level: reachability × blast radius × silence, then cost to fix.
