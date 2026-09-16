# Dev config and dev script refactor (design)

> Status: **implemented** on `feat/dev-config-scripts`, all seven steps of section 6, in one
> commit after this document was written. Scope is `deepagent-service/scripts/` plus the
> `one.properties` template, the spike's config surface and tests. Nothing under `app/`, `skills/`
> or `utils/` changes, so the deployed service behaves exactly as it does today. Written in
> English because it is about developer ergonomics rather than product behaviour. Section 11
> records where the implementation departs from the design above it.

## 1. Why

Two costs, both measured on the current tree.

**Adding one dev config key takes about twelve edits.** Tracing `DEV_SSO_TOKEN` finds 34 mentions
across five non-test files. The declaration chain in `scripts/dev_config.py` alone is five steps:
a constant, an entry in the `DEV_KEYS` tuple, a `DevConfig` field, a line in the loader, and a
keyword in the constructor call. `scripts/dev_chat.py` then adds an import, an argparse block, a
`resolve_option` call, a parameter on `collect_config_source_rows`, and a `ConfigSourceRow`. That
last one changes a function signature, so three call sites move with it.

**There are four precedence rules, not one.** Which layers apply depends on which key you are
looking at.

| Key | Order today | Layers |
|---|---|---|
| `AGENT_API_BEARER_TOKEN` | CLI flag, then env, then file, then default | 4 |
| Other `Settings` keys | env, then file, then default | 3 |
| `DEV_*` keys | CLI flag, then file, then default. No env layer | 3 |
| `ONE_PROPERTIES_PATH` | env, then default | 2 |

A reader has to know which group a key belongs to before they can answer "where did this value
come from". The `--verbose` table makes this visible: its source column cannot say `env` on a
`DEV_*` row, and can only say `cli` on the bearer token row.

**The env-blindness is already causing a wrong value.** `resolve_shell_exports()` reads
`AGENT_WORKSPACE_ROOT` from the properties file, falling back to a spike default when the file has
no entry. It never looks at env. Its docstring argues this is safe, on the grounds that the export
only adds "a spike default the service itself would not use" when the file is silent. That holds
only if the file is the sole other source. Reproduced in a container that sets the key in env and
has no file entry:

```
env says                /workspace
Settings resolves to    /workspace
--shell-exports says    /tmp/erd-spike-workspace
```

`run-deepagent.sh` exports the third value before starting uvicorn, so the server runs on a
workspace root nobody asked for. See section 10.

A fourth cost sits next to these. `scripts/dev_chat.py:468-642` is a 175 line `main()` that handles
argument parsing, config resolution, connector merging, state loading, preflight, streaming,
dashboard writing, and the SSO gate.

## 2. What this does not change

| Area | Why it is out of scope |
|---|---|
| `app/`, `skills/`, `utils/` | These ship in the image and sync to the internal repository. The goal is zero production behaviour change. One comment in `app/config.py` was corrected, described in the next row |
| The env layer | It cannot be removed, from production or from dev. See section 4.5 |
| Any CLI flag | All fifteen stay. See section 4 |
| `spike/mcp-shell/` | Labelled throwaway in its own README. It consumes `dev_config`, so it inherits the improvement. Steps 5 and 7 edit it only at that surface: `bridge.py` calls `resolve()` and logs the warning, `run-deepagent.sh` and the README describe the new rule |
| A startup validation check in the service lifespan | Considered and dropped. It would run in the internal deployment against a properties file we cannot read, and the required key set differs per runtime. Two comments already described it as if it existed, in `app/config.py` next to `AGENT_API_BEARER_TOKEN` and in `tests/conftest.py`; both were corrected to say what actually happens, which is a 401 per request from `require_bearer_token()` and no startup failure at all |

## 3. The single rule

Every key resolves the same way:

```
    CLI flag  ─►  env var  ─►  properties file  ─►  built-in default
   (if the key has a flag)
```

One exception, which is inherent rather than a choice. `ONE_PROPERTIES_PATH` selects which
properties file is read, so it cannot itself come from that file. It stays env, then default.

The only behavioural change is that `DEV_*` keys gain the env layer they currently lack. Official
`Settings` keys already work this way, and the CLI layer already sits on top of them inside
`dev_chat.py`.

This closes a real gap. `scripts/env_to_properties.py` exists because some environments, such as a
Claude Code web session or a CI container, only have env vars. It writes `Settings` keys into the
properties file, but it cannot do that for `DEV_*` keys, because they are not `Settings` fields.
Today that means `DEV_CONNECTORS` cannot be configured in such an environment without editing the
file by hand.

## 4. Design

### 4.1 One resolver

```python
# scripts/dev_config.py

@dataclass(frozen=True)
class DevSetting:
    key: str
    value: str | None
    source: str       # "cli" | "env" | "properties" | "default"
    secret: bool      # controls how it is displayed, never whether it is read


@dataclass(frozen=True)
class DevConfig:
    deepagent_url: str
    sso_token: str | None
    sso_url: str | None
    connectors: list[dict[str, str | None]]
    bearer_token: str | None
    sso_token_header: str
    sso_url_header: str
    settings: tuple[DevSetting, ...]      # display order, for --verbose


def resolve(cli_overrides: dict[str, str | None] | None = None) -> DevConfig:
    """Resolve every key by the single rule in section 3."""
```

`resolve()` loops over a small list of key descriptors, each carrying the key name, the attribute
name, the default, and whether the value is a secret. The list drives the loop only. It does not
generate the dataclass or the argument parser. Generating those would be worth doing at roughly
eight keys and is not worth it at four, because the machinery would cost a reader more than the
explicit code it replaced.

Two existing functions, `dev_key_sources()` and `official_key_source()`, become one. `ResolvedOption`
and `resolve_option` in `dev_chat.py` are deleted, because `resolve()` returns the source already
folded into each `DevSetting`.

### 4.2 Every flag stays

The command line interface is unchanged. `main()` collects the four config flags into a plain dict
and passes it to `resolve()`.

| Flag | Kept because |
|---|---|
| `message` (positional) | It is the turn's text |
| `--new`, `--session-id`, `--state-dir` | They decide whether a run starts a new session or continues one |
| `--csv` | Attaches data files |
| `--connector`, `--no-connectors` | They merge with the configured set rather than replacing it |
| `--user-id`, `--dashboard-out`, `--open`, `--verbose` | Per-invocation behaviour, not config |
| `--base-url`, `--token`, `--sso-token`, `--sso-url` | One-run overrides of a config key |

### 4.3 A shorter main()

```python
def main() -> None:
    args = _build_parser().parse_args()
    config = resolve(_cli_overrides(args))     # exits with one line on bad config
    if args.verbose:
        print_config(config)
    session = _load_or_start_session(args, config)
    payload = _build_payload(args, config, session)
    _preflight(config, payload)
    _stream_and_report(args, config, payload)
```

Most of this is moving existing code into three named helpers. `_preflight`, `_stream_chat` and
`_print_event` already exist as functions.

### 4.4 Printing the config

`--verbose` becomes a loop over `config.settings`. The nine hand-written rows in
`collect_config_source_rows()` are deleted, along with the signature that grows with every new key.

Secret handling is unchanged and still mandatory. Secrets are reported by source and presence
only. Connector URLs are never printed, because a query string can carry a token.

### 4.5 Warn when env shadows the file, rather than removing the env layer

The env layer looks like the one worth deleting, because in local development the properties file
is meant to be the single source of truth. It cannot be deleted, in any of the three processes.

| Process | Why the layer has to stay |
|---|---|
| The deployed service | Compose configures every deepagent-service key through env and mounts no properties file at all |
| The test suite | `get_settings()` is `@lru_cache(maxsize=1)` and takes no arguments, and 25 places in `app/` call it. A test cannot hand it a `Settings` object, only change what it reads. `init_settings` is first in `settings_customise_sources`, so `Settings(KEY=...)` would win, but nothing routes that through `get_settings()`. Adding that seam means editing `app/config.py`. The only other source is the properties file, selected by `ONE_PROPERTIES_PATH`, which is itself an env var, so the layer survives the change anyway. And since compose runs on env alone, tests that stop using env stop exercising the deployment path that production uses |
| The dev scripts | `DEV_*` keys already have no env layer. The official keys go through `get_settings()`, and they have to, because the dev script's job is to predict what the server will see. Reading the file directly instead would make `dev_chat.py` report one value while `run-deepagent.sh` starts a server that reads another. A stale `export AGENT_API_BEARER_TOKEN` in a developer's shell would then produce the AUTH banner in acceptance point 7, caused by the simplification itself |

So make the layer loud instead. During a dev run the properties file is meant to be
authoritative, so an env var shadowing it is nearly always an accident, usually a stale `export`
left over from an earlier session. `--verbose` already reports the source per key. The addition is
to say so without being asked:

```
⚠️  AGENT_API_BEARER_TOKEN 來自 env, 不是 one-local.properties
    dev 期間檔案才是權威來源; 這通常是上一個 session 留下的 export
```

About six lines in `dev_chat.py` and `bridge.py`, using the same `key_source()` that section 4.1
introduces. No production change.

**When it fires.** Only when the file also sets the key. The word is shadow, not env: an env var
for a key the file leaves empty is the only source there is, and that is the normal state in the
container case of section 5.2, where there may be no file at all. Warning there would train
people to ignore it. A CLI flag on top of both is not a shadow either, because the developer
typed it this run. `env_shadowed_keys()` in `dev_config.py` applies exactly this test, and
`ONE_PROPERTIES_PATH` is excluded since it cannot be in the file.

**What the warning can cover.** Exactly 36 names. The env var name equals the `Settings` field
name, since `model_config` sets `case_sensitive=True` with no prefix, and `env_ignore_empty=True`
means an empty value counts as unset and falls through to the file and then the default.

```
LLM and runtime      AGENT_RUNTIME  AGENT_MODEL  AGENT_MAX_TOKENS
                     AGENT_REASONING_MAX_TOKENS  AGENT_RECURSION_LIMIT
                     AGENT_PROVIDER_SORT  AGENT_PROVIDER_IGNORE
                     AGENT_PROVIDER_REQUIRE_PARAMETERS
                     OPENAI_BASE_URL  OPENAI_API_KEY
Auth                 AGENT_API_BEARER_TOKEN  AGENT_AUTH_MODE
                     AGENT_TOKEN_EXCHANGE_URL  AGENT_TOKEN_HEADER  AGENT_TOKEN_TTL
                     AGENT_SERVICE_ACCOUNT_KEY  AGENT_SERVICE_ACCOUNT_KEY_FILE
Connectors           CONNECTOR_CALL_BUDGET  CONNECTOR_CALL_RETRIES
                     CONNECTOR_REQUEST_TIMEOUT_SECONDS  CONNECTOR_BEARER_TOKENS
                     SSO_TOKEN_HEADER  SSO_URL_HEADER
Storage              STORAGE_BACKEND  S3_ENDPOINT  S3_BUCKET  S3_ACCESS_KEY
                     S3_SECRET_KEY  S3_KEY_PREFIX  AGENT_WORKSPACE_ROOT
Skills and repair    AGENT_BUILTIN_SKILLS_DIR  REPAIR_MODEL_CALL_TIMEOUT_SECONDS
Tracing              LANGFUSE_PUBLIC_KEY  LANGFUSE_SECRET_KEY  LANGFUSE_HOST
```

That is 35. The 36th is `ONE_PROPERTIES_PATH`, which is not a `Settings` field. It is read at
`app/config.py:19` and is the only direct `os.environ` call in the whole of `app/` and `utils/`.

**What the warning cannot cover.** Env vars that libraries in the same process read for
themselves. No credential reaches an SDK that way, because every one is passed explicitly:
`base_url` and `api_key` for openai, the keys and host for langfuse, and explicit credentials for
boto3, whose `build_s3_client()` docstring says it deliberately avoids boto3's env detection. So
`AWS_ACCESS_KEY_ID` in the environment does nothing. Two do get through:

- `OTEL_*`, because `app/main.py:59` calls `FastAPIInstrumentor.instrument_app(app)` and
  OpenTelemetry reads its own environment.
- `LANGCHAIN_OPENAI_STREAM_CHUNK_TIMEOUT_S`, a langchain-openai knob. The spike README documents
  using it as an env prefix in a real run, precisely because it is not a config key.

Plus the process-level variables httpx and friends honour, such as the proxy settings.

## 5. The developer's view

### 5.1 Daily loop, unchanged

```
term 1   uv run python spike/mcp-shell/mock_server.py
term 2   spike/mcp-shell/run-deepagent.sh
term 3   uv run python spike/mcp-shell/bridge.py
term 4   uv run scripts/dev_chat.py --state-dir spike/mcp-shell/out/.dev-session \
             --dashboard-out spike/mcp-shell/out/dashboard.html "Build a sales dashboard..."
```

### 5.2 First-time setup, easier in a container

```
now      laptop     edit one-local.properties by hand
         container  run env_to_properties.py, then hand-edit the file to add DEV_* keys

after    laptop     edit one-local.properties by hand            (unchanged)
         container  export DEV_CONNECTORS='[{"id":"sales","url":"http://127.0.0.1:8765/mcp"}]'
                    export AGENT_API_BEARER_TOKEN=...
                    no file needed
```

### 5.3 Adding a config key, four edits instead of twelve

```
1  dev_config.py   descriptor entry: name, attribute, default, secret flag
2  dev_config.py   DevConfig field
3  dev_chat.py     parser.add_argument("--foo")
4  dev_chat.py     one line in _cli_overrides()
plus               an entry in one.properties, which section 7 makes a test failure if omitted
```

## 6. Implementation order

| Step | Change | Files |
|---|---|---|
| 1 | `resolve()` with the env layer and the descriptor list. Merge the two source functions | `scripts/dev_config.py`, `tests/test_dev_config.py`, `tests/conftest.py` |
| 2 | `dev_chat.py` consumes `resolve()`. Delete `ResolvedOption` and `resolve_option` | `scripts/dev_chat.py`, `tests/test_dev_chat_script.py` |
| 3 | `--verbose` becomes a loop. Delete `collect_config_source_rows` | same two files |
| 4 | Split `main()` into the four helpers in section 4.3 | `scripts/dev_chat.py` |
| 5 | The shadowing warning from section 4.5 | `scripts/dev_chat.py`, `spike/mcp-shell/bridge.py`, their tests |
| 6 | Fix `resolve_shell_exports()` to consult env before the spike default, so it cannot clobber an env-set `AGENT_WORKSPACE_ROOT` | `scripts/dev_config.py`, `tests/test_dev_config.py` |
| 7 | Update the config section of the spike README | `spike/mcp-shell/README.md`, `one.properties` comment |

Each step is independently shippable. The full suite runs 631 tests in 23 seconds, so every step
can be validated before the next begins.

**Steps 1 to 4 must stay out of `spike/`,** because the consolidation of `spike/` into `scripts/`
is still undecided. It is U15 in `2026-09-09-mcp-dashboard-decision-summary.md`, listed under
未定案 with the trigger "最終 merge 前". Doing the config work first is the right order: the
consolidation rewrites `bridge.py` into `scripts/`, and that rewrite should land on top of
`resolve()` rather than on the config surface it replaces.

One detail makes that possible. `bridge.py` imports two names today:

```python
from scripts.dev_config import connectors_needing_real_sso, load_dev_config
```

Step 1 must therefore keep `load_dev_config()` as a thin wrapper over `resolve()`, so `bridge.py`
and its six tests need no edit. Whichever of step 5 or the consolidation arrives first deletes the
wrapper. Step 5 arrived first, so the wrapper is gone and `bridge.py` calls `resolve()`.

**Step 1 carries one regression risk.** `tests/conftest.py` has an autouse fixture,
`_isolate_one_properties`, that points `ONE_PROPERTIES_PATH` at a path that does not exist. That is
enough isolation today only because `DEV_*` keys ignore env. Once they read env, a developer's
shell variables would leak into the suite. The fixture must clear the `DEV_*` keys as well. A
machine with none of them set will pass either way, so this will not show up in CI or in a fresh
container.

## 7. Related: pin the config declarations

Adding a `Settings` key currently means updating three declarations in two repositories, and
nothing checks any of them.

```
  app/config.py  Settings field         the only authority
        │
        ├──►  one.properties            template, three keys already missing
        ├──►  docker-compose.app.yml    23 of 35 keys present
        └──►  Helm values or configmap  internal repository, not visible from here
```

The drift is real today:

| Problem | Detail |
|---|---|
| Missing from `one.properties` | `AGENT_PROVIDER_REQUIRE_PARAMETERS`, `SSO_TOKEN_HEADER`, `SSO_URL_HEADER` |
| Dead key in `one.properties` | `ERD_GUARD_BLOCKING=true` at line 60. Nothing in `deepagent-service` reads it |
| Documented but undeclared | The spike README tells you to set `AGENT_PROVIDER_REQUIRE_PARAMETERS`, which the template never lists |

A test comparing `one.properties` keys and the compose env block against `Settings.model_fields`,
with an explicit allow list for keys deliberately left at their defaults, turns this from a
discipline problem into a test failure. It touches only `tests/` and `one.properties`.

## 8. Open questions

1. **The Helm chart.** It is not in this repository, so it may be a third declaration site. Two
   things to confirm: whether it sets `ONE_PROPERTIES_PATH`, and whether it sets any env vars
   alongside the mounted file. `_DEFAULT_PROPERTIES_PATH` is the relative path
   `one-local.properties`, resolved against the working directory, which is `/app` in the image.
   The template in this repository is named `one.properties`. So unless the chart mounts the file
   as `/app/one-local.properties`, it must be setting `ONE_PROPERTIES_PATH`.
2. **`ERD_GUARD_BLOCKING`.** Safe to delete from the template only if nothing on the internal side
   reads it.
3. **Whether the compose env block should be pinned too**, or only `one.properties`. Pinning both
   catches more drift but makes adding a key noisier.

## 9. Evidence

Measured on `feat/dev-config-scripts`, originally at `e290f75` and re-checked after the merge of
`feat/mcp-dashboard` at `c7d5523`.

| Fact | How it was checked |
|---|---|
| 631 tests pass in 23 seconds, up from 622 before the base merge | `uv run pytest -q` |
| `ruff check` clean, two files fail `ruff format --check` | `tests/test_api_auth.py` was already failing on this branch; `tests/test_middleware.py` arrived with the base and fails on `origin/feat/mcp-dashboard` by itself. Both untouched |
| `DEV_SSO_TOKEN` appears at 34 sites in five non-test files | grep across `scripts/`, `spike/`, `one.properties` |
| `main()` is 175 lines | `scripts/dev_chat.py:468-642` |
| The image copies only `app`, `skills`, `utils` | `deepagent-service/Dockerfile` |
| Only `internal_runtime.py` and `upload_decrypt.py` are internal-owned on the Python side | `scripts/internal-owned-paths.txt` |
| Compose mounts no properties file and sets 23 of 35 keys | `docker-compose.app.yml`, the `deepagent-service` block |
| The test suite configures itself through env at 179 sites | grep for `monkeypatch.setenv` and `os.environ[` in `tests/` |
| The service reads 35 `Settings` names plus `ONE_PROPERTIES_PATH` | `Settings.model_fields`, and a grep for `os.environ`/`os.getenv` across `app/` and `utils/` returning one hit |
| `get_settings()` has no injection seam | `@lru_cache(maxsize=1)`, no arguments, 25 call sites in `app/` |
| SDK credentials are passed explicitly, not read from env by the SDK | `deepagents_runtime.py:60-61`, `tracing.py:45-48`, `engine/s3.py` |
| An empty `AGENT_API_BEARER_TOKEN` does not fail at startup | `app/config.py` has zero validators; constructing `Settings()` and importing `app.main` both succeed with it empty; `require_bearer_token()` raises `UnauthorizedError` per request instead |

## 10. Verified against a real spike run

Run on 2026-09-16 in a container that had a working model endpoint in env, no `one-local.properties`,
no `DEV_*` variables and no `AGENT_API_BEARER_TOKEN`. That is the container case section 5.2
describes, so it tested the assumptions rather than just the code. All four steps ran, the model
produced a dashboard, and the mock server served 16 connector calls.

Confirmed:

| Claim | Result |
|---|---|
| §5.1, the four-terminal loop and its exact commands | Ran as written |
| §5.2, a container needs `env_to_properties.py` and then a hand-edit | Exactly so. The script wrote 13 `Settings` keys from env; `DEV_CONNECTORS` had to be appended by hand |
| §3, `DEV_*` keys ignore env | `DEV_CONNECTORS` exported in env, `load_dev_config()` returned `[]` |
| §4.4, the `--verbose` source column vocabulary varies by row | No row showed `env`, since the `DEV_*` rows cannot and the official keys were in the file |
| The loopback SSO gate from `e05f47b` | Bridge reported `sso=placeholder(loopback)` for the 127.0.0.1 connector, as designed |

Falsified, and now recorded in section 1: `resolve_shell_exports()` returned the spike default for
`AGENT_WORKSPACE_ROOT` while env and `Settings` both said `/workspace`. This is the first concrete
instance of the env-blindness this document argues against, and it was found by running the spike
rather than by reading the code.

Not covered by this run: the view-time path, meaning a browser loading the page so its `mcp()`
calls reach the bridge. The bridge log had no `[mcp]` lines because nobody opened the page. That
path is the spike's own acceptance list, not an assumption of this design.

## 11. As implemented

Departures from sections 4 to 6, each with the reason.

| Design said | Implementation does | Why |
|---|---|---|
| `resolve()` loops over descriptors carrying key, attribute, default and secret flag | The same, as `_KeySpec`, plus an `official` flag | Official keys take their value from `get_settings()` (section 4.5) and their default from `Settings`, so the loop needs to know which kind it is looking at |
| `DevConfig.settings` holds the keys in the descriptor list | It also holds `ONE_PROPERTIES_PATH` as its first entry | The `--verbose` table has always led with the file path and whether it exists, and a loop with one special case outside it is not a loop |
| Four helpers under `main()` | Five: `_resolve_config`, `_load_or_start_session`, `_build_payload`, `_build_headers_for_turn`, `_stream_and_report` | The SSO gate and the header build were one block in the old `main()` and stayed one function. `_preflight` kept its `(base_url, connectors)` signature because four tests call it directly |
| `main()` is 175 lines | 11 lines. The helpers are 30 to 45 lines each | |
| Twelve edits per new key | Four: a `_KeySpec`, a `DevConfig` field, an `add_argument`, and an entry in `CLI_OVERRIDE_KEYS` | `CLI_OVERRIDE_KEYS` maps argparse dest to key name, and both `_cli_overrides()` and the verbose label read it, so the flag and the key are tied in one place |
| `key_source()` replaces two source functions | Present, but nothing outside its tests calls it | `resolve()` folds the source into each `DevSetting`, and the shadow check reads those. Kept as the one public way to ask about a key without resolving everything |
| Step 1 alone must not touch `spike/` | All seven steps landed together, so the wrapper existed for one commit and was removed by step 5 in the same change | |

Test counts after the change: 640 pass, up from 631. The nine new tests cover the env layer on
`DEV_*` keys, CLI precedence with an empty string, the shadow warning in `dev_chat.py` and
`bridge.py`, and `resolve_shell_exports()` returning the env value the spike run showed it
dropping (section 10). `ruff check` is clean and the two `ruff format` failures are the same
two pre-existing files as in section 9.

The `tests/conftest.py` regression risk from section 6 is handled: `_isolate_one_properties`
now clears the four `DEV_*` keys as well and restores them on teardown.

