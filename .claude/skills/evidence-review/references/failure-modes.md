# Failure-mode catalogue with verification recipes

Each family: what to look for, the trigger that usually exposes it, and the cheapest
decisive check. Recipes are written for a Python/TypeScript/Java repo but the pattern
is language-neutral. Use the family list as a checklist against the risk-ranked
modules; do not run every recipe on every file.

Contents
1. Shape drift at boundaries
2. Error-path mapping
3. Contract drift
4. Trust boundaries
5. Concurrency and lifecycle
6. Presentation to an LLM
7. Configuration and defaults
8. Process and gates
9. Reading library behavior correctly

---

## 1. Shape drift at boundaries

**Look for**: a function that accepts "a payload" from outside (API response, upload,
webhook, tool result) and branches on its shape.

**Triggers**: `[]`, `{}`, `null`, a bare scalar, a scalar wrapped in an envelope
(`{"result": "text"}`), a list of scalars, a list of lists, ragged keys, a value whose
type changes late in the sequence, one row larger than any internal buffer, CRLF line
endings, non-UTF-8 bytes, unicode in identifiers.

**Recipe**: call the real function with each trigger and print the outcome.

```python
cases = {"empty_dict": {}, "bare_string": "text", "wrapped": {"result": "text"},
         "list_of_scalars": ["a", "b"], "ragged": [{"a": 1}, {"b": 2}]}
for name, payload in cases.items():
    try:
        result = target(payload)
        print(f"{name:20} OK  {summarize(result)}")
    except Exception as error:
        print(f"{name:20} {type(error).__name__}: {str(error)[:120]}")
```

**What "wrong" looks like**: an empty object landing as one row; a string landing as a
1×1 table the model is then told to query; an "empty" guard that only covers one of the
empty shapes.

**Late type drift (schema inference)**: engines that infer schema from a sample
(DuckDB `read_json_auto`, pandas, Spark) succeed on mixed types inside the sample and
fail on drift after it. Test with a clean prefix longer than the sample window and dirt
at the tail; note the sample size in the finding. Decide with the author whether that
is the provider's problem (often yes) and ask for a comment saying so.

## 2. Error-path mapping

**Look for**: a pipeline with a generic `catch`/`onErrorResume` plus a few specific
branches. List every exception class thrown *before* the first persistent write and
check each has a branch that knows no write happened.

**Trigger**: throw the exception at the earliest site; observe which branch runs.

**Recipe**: grep the throw sites and the handler in one go.

```
grep -n "throw new\|raise " <file>          # sites, with line numbers
grep -n "onErrorResume\|except \|catch (" <file>
```
Then order them by line against the first write. Anything thrown above the write that
falls into the generic branch is a finding: wrong error code, a stack trace logged at
ERROR for an expected user error, and possibly an orphan record (an "AI reply" with no
user message, a compensation row with nothing to compensate).

**Check the tests**: do the tests for that exception go through the streaming/serving
entry point or only through a unit seam? A unit-only test is how this class of bug ships.

**Sanitized vs forwarded messages**: when a handler forwards `str(error)` to a client,
list which exception classes reach it and what their messages contain (internal
hostnames, filesystem paths, stack fragments). Decide per class.

## 3. Contract drift

**Look for**: prose that names a mechanism: prompts naming tools, docstrings describing
guarantees, constants whose names promise a bound.

**Triggers and recipes**:
- *Prompt names a tool*: `grep -rn "<tool_name>" app/ <site-packages>/<framework>/`.
  Zero hits outside the prompt is a finding; a test asserting the prompt contains the
  name is a test pinning a bug.
- *A cap that isn't a cap*: read where the limit is compared. A check performed before
  reading the item, against a running total, admits the item that crosses the limit;
  a per-file limit missing means the total is unbounded by one file.
- *"Last write wins" claims*: identify every artifact the write touches (table, file,
  cache entry) and whether each is under the same lock.
- *Docstring vs behavior*: run the docstring's own example.
- *Removed code*: `git log -S"<symbol>"` to find the removal commit; grep for residual
  references across every language in the monorepo (a Python-side removal often leaves
  a Java/TS enum member behind).

## 4. Trust boundaries

**Look for**: external text entering a prompt, a SQL identifier, a filesystem path, a
shell command, a log line, or an outbound header.

**Recipes**:
- *Prompt injection framing*: find the sentinel markers; check whether a data value
  containing the closing marker ends the frame early; check whether the code
  acknowledges it (a documented acceptance is fine, an unknown one is a finding).
- *Identifiers*: every f-string DDL/SQL must be preceded by a validator; grep for
  `f'CREATE`, `f"SELECT` and trace the identifier back to the validator.
- *Paths*: `resolve()` then `is_relative_to(root)` on every server-supplied relative
  path, both at write time (library) and read time (app). Check both layers exist.
- *Secrets in messages/logs*: grep the log calls in the module for the header/token
  variable names; read `__str__`/`toString` of request objects; check what `str(error)`
  from the HTTP client contains (usually the URL, sometimes headers).
- *Identity*: find the filter that sets the current user; check the condition under
  which a client-supplied header is trusted, and what the default config value is.
  Fail-open defaults (`enabled: false` trusts the header) are a finding even if prod
  config is correct, because config drift is the trigger.

## 5. Concurrency and lifecycle

**Look for**: a lock, a shared directory, a per-request object, `asyncio.run` inside a
sync callable, contextvars crossing threads, retries.

**Recipes**:
- *Shared path from shared key*: if a file or table name is derived from arguments,
  identical arguments collide. Map every step of the write/read sequence to
  inside/outside the lock. Anything outside that feeds something inside is a window.
- *Stress it before claiming it*:
  ```python
  failures = 0
  for trial in range(30):
      errors = []
      threads = [threading.Thread(target=work) for _ in range(2)]
      [t.start() for t in threads]; [t.join() for t in threads]
      failures += bool(errors)
  print("trials=30 failed=", failures)
  ```
  Report the trial count. Zero reproductions with a real window is a nit with a free
  fix; do not present it as a blocker.
- *Retries*: list what the retry wraps. Retrying a call that may mutate is a finding
  unless the protocol exposes idempotency hints that are checked. Retrying
  deterministic 4xx doubles latency on misconfiguration.
- *Per-request state*: confirm creation per request and cleanup on every exit path
  (error in setup, client disconnect, cancellation). Check what happens when cleanup
  itself raises.
- *Contextvars*: `ContextVar.reset` with a token from another context raises; async
  generators finalized by the event loop after a disconnect run in a foreign context.

## 6. Presentation to an LLM

**Look for**: code that renders tool results, previews, or errors into text the model
reads.

**Recipes**:
- *Delimiters*: render a cell containing `|` and `\n` through the real renderer and
  look at the output. Markdown tables break on both.
- *Caps in one dimension only*: row caps without cell/byte caps; render 20 rows of 5k
  characters and measure the output length.
- *Misframing*: does the text tell the model the data is something it is not
  ("Landed table … call the tool again" for a prose response)? Does the system prompt
  make an unconditional claim ("every call lands a table") the code will violate on a
  planned input?
- *Cap placement*: a preview cap is safe; a cap on the channel the model uses to read
  full content is not. Parametrize rather than globalize.

## 7. Configuration and defaults

**Look for**: new env vars, properties, compose services, dependency ranges.

**Recipes**:
- `git diff <base>...HEAD -- '*.properties' '*.yml' '*.env*' pyproject.toml package.json`
  and read every added line. Templates with empty values are fine; real hostnames,
  tokens, or credentials are blockers.
- For each new setting: what does an empty value do (documented?), what does a wrong
  value do (fail at startup or silently default?), and is a placeholder value
  reachable in production if the enabling flag is forgotten?
- Check a config key exists on both sides (properties file vs settings class); a key
  only one side knows is silently ignored.

## 8. Process and gates

- Run every test gate the project defines (per-component suites, lint, typecheck) and
  attribute failures: environment (missing embedded DB binary, network) vs code. Report
  numbers, and which gate could not be verified here.
- Plan files with checkboxes: are they current, or do they describe mechanisms the PR
  removed? Stale plans mislead the next agent.
- PR description: does it carry the review verdict the project requires, for the
  current head (count commits after the last "final review" commit)?
- CI: are there check runs at all? If none, the gates are manual and your run is the
  evidence.

## 9. Reading library behavior correctly

- Locate the installed source, not documentation: `.venv/lib/python*/site-packages/`,
  `node_modules/`, `~/.m2/`. Cite path, line, and version (`pip show`, lockfile).
- Prefer the branch that handles your exact input type; libraries often have a
  reduced-capability path (a dict schema instead of a model class, a string instead
  of an object) that skips validation entirely. Find the `isinstance` split.
- When a library is the canonical solution to the same problem the code hand-rolls
  (an official adapter, a reference client), read what it does for the same case; that
  is the "how common is this" answer, and it takes one fetch.
