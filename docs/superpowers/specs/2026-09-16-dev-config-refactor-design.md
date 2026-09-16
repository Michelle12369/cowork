# Dev config and dev script refactor (design)

> Status: **design, not yet implemented.** Scope is `deepagent-service/scripts/` plus the
> `one.properties` template and tests. Nothing under `app/`, `skills/` or `utils/` changes, so the
> deployed service behaves exactly as it does today. Written in English because it is about
> developer ergonomics rather than product behaviour.

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

A third cost sits next to these. `scripts/dev_chat.py:468-642` is a 175 line `main()` that handles
argument parsing, config resolution, connector merging, state loading, preflight, streaming,
dashboard writing, and the SSO gate.

## 2. What this does not change

| Area | Why it is out of scope |
|---|---|
| `app/`, `skills/`, `utils/` | These ship in the image and sync to the internal repository. The goal is zero production change |
| The env layer | It cannot be removed, from production or from dev. See section 4.5 |
| Any CLI flag | All fifteen stay. See section 4 |
| `spike/mcp-shell/` | Labelled throwaway in its own README. It already consumes `dev_config`, so it inherits the improvement without being edited |
| A startup validation check in the service lifespan | Considered and dropped. It would run in the internal deployment against a properties file we cannot read, and the required key set differs per runtime |

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
| 6 | Update the config section of the spike README | `spike/mcp-shell/README.md`, `one.properties` comment |

Each step is independently shippable. The full suite runs 622 tests in 23 seconds, so every step
can be validated before the next begins.

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

Measured on `feat/dev-config-scripts` at commit `e290f75`.

| Fact | How it was checked |
|---|---|
| 622 tests pass in 23 seconds | `uv run pytest -q` |
| `ruff check` clean, one file fails `ruff format --check` | `tests/test_api_auth.py:69`, pre-existing and untouched |
| `DEV_SSO_TOKEN` appears at 34 sites in five non-test files | grep across `scripts/`, `spike/`, `one.properties` |
| `main()` is 175 lines | `scripts/dev_chat.py:468-642` |
| The image copies only `app`, `skills`, `utils` | `deepagent-service/Dockerfile` |
| Only `internal_runtime.py` and `upload_decrypt.py` are internal-owned on the Python side | `scripts/internal-owned-paths.txt` |
| Compose mounts no properties file and sets 23 of 35 keys | `docker-compose.app.yml`, the `deepagent-service` block |
| The test suite configures itself through env at 179 sites | grep for `monkeypatch.setenv` and `os.environ[` in `tests/` |
| The service reads 35 `Settings` names plus `ONE_PROPERTIES_PATH` | `Settings.model_fields`, and a grep for `os.environ`/`os.getenv` across `app/` and `utils/` returning one hit |
| `get_settings()` has no injection seam | `@lru_cache(maxsize=1)`, no arguments, 25 call sites in `app/` |
| SDK credentials are passed explicitly, not read from env by the SDK | `deepagents_runtime.py:60-61`, `tracing.py:45-48`, `engine/s3.py` |
