# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`pyjsonrpc2` — a transport-agnostic, server-side implementation of JSON-RPC 2.0. The library never touches sockets or HTTP: `JsonRpcServer.call()` takes a raw request (`str`/`bytes`/`bytearray`/`memoryview`) and returns raw response `bytes` (or `None` for notifications). Embedding it in a transport is the caller's job.

Requires Python >=3.11. Only runtime dependency is `orjson`. Built with the `uv_build` backend; `uv.lock` is committed.

## Commands

```bash
uv sync                                # install the package itself (no dependency groups)
uv run python -m unittest              # run all tests
uv run python -m unittest tests.test_server.JsonRpcServerTest.test_positional_parameters  # single test
tox -p                                 # full test gate: 3.11–3.14 + mindeps + coverage
tox -e 3.11                            # one interpreter (env names are "3.11" … "3.14")
tox -e mindeps                         # tests against the declared dependency floor
tox -e prek                            # all hooks, via a tox-managed prek instead of the global
prek run --all-files                   # every hook over the whole tree
prek run mypy --all-files              # mypy alone (strict, via [tool.mypy]), in the hook's isolated env
prek run --all-files --hook-stage manual  # what CI runs: report-only ruff, no rewriting
```

The default env list is tests only (`3.11`–`3.14`, `mindeps`, `coverage`); lint, format, and type checking live in `prek.toml`. `tox -e prek` exists as an *additional* env — not in `env_list`, so a bare `tox` skips it — that runs every hook from a tox-managed env instead of a global `prek`.

### Coverage across jobs

`coverage` inherits `set_env.COVERAGE_FILE = "{work_dir}/.coverage"` from `env_run_base`, and `coverage combine` reads the parallel data files out of that `work_dir`. Locally a single `tox -p` puts all of them there. CI splits the versions across matrix jobs, which do not share a filesystem, so the data is reassembled explicitly:

- each leg uploads `.tox/.coverage.*` as `coverage-<version>`. `include-hidden-files: true` is **mandatory** — those are dot-files inside a dot-folder, and upload-artifact excludes both by default, producing a silently empty artifact. `if-no-files-found: error` guards against that regressing;
- the `coverage` job downloads them with `merge-multiple: true` back into `.tox/`, then runs `tox -e coverage`. Checkout must precede the download, since checkout cleans the workspace.

Do **not** move `coverage` into the per-leg env lists to avoid this. That would enforce `fail_under = 100` per interpreter, which passes today only because nothing in `src/` is version-gated; the first `if sys.version_info >= …` branch would fail whichever legs don't execute it, while the combined gate would correctly pass.

`[tool.tox.gh.python]` drives the per-leg env selection, and `mindeps` rides along in the 3.11 leg. tox-gh is deliberately **not** in `[tool.tox] requires` — it is installed only in the CI legs (`uv tool install --python <version> tox --with tox-uv --with tox-gh`, which is also how tox-gh detects the leg's version). The trade-off: tox silently ignores the `gh` table when the plugin is absent, so a typo in it cannot be caught locally — only by observing which envs a CI leg actually runs.

`ruff`, `tox`, and `prek` are expected as `uv tool install`-ed globals, not project dependencies. The tox config lists `tox-uv` in `requires`, so tox provisions it if the global install lacks it; `mindeps` needs it for `uv_resolution`. There are **no dependency groups at all**: every tool is either a global, installed by the tox env that runs it (`coverage`), or supplied by `prek` in an isolated hook env. Run the tooling through `tox` or `prek`, never `uv run` — `.venv` holds only the package and `orjson`.

Tests use `unittest`, not pytest. Coverage is enforced at `fail_under = 100`, ruff runs with `select = ["ALL"]`, and `strict = true` is set in `[tool.mypy]` — so a bare `mypy` is already strict. New code must be fully typed and either covered or explicitly marked `# pragma: no cover`.

One thing must be kept in sync by hand: the mypy hook's `additional_dependencies` in `prek.toml`, against `dependencies` in `[project]`. The hook runs in an isolated env, so any new runtime dependency must be repeated there or strict mode will fail on the unresolvable import.

Note that ruff formats Python code blocks inside Markdown, so `README.md` is subject to `ruff format`.

### Hooks: local vs CI

`prek.toml` registers both `ruff-check` and `ruff-format` **twice**, split by `stages`:

- the `pre-commit` variants rewrite the tree (`--fix`; formatting in place). They are what the git hook and a bare `prek run` use;
- the `manual` variants (`ruff-check-ci`, `ruff-format-ci`) only report — no `--fix`, and `--check` for the formatter — so nothing is repaired out from under the report.

`.github/workflows/tests.yml` selects the reporting variants with `--hook-stage manual` and sets `RUFF_OUTPUT_FORMAT=github` at the job level, which **both** subcommands honour (`ruff format` accepts `--output-format` when `--check` is passed, contrary to what the formatter docs page lists). Violations become GitHub annotations; the other hooks ignore the variable.

Hooks without a `stages` key (the builtins, mypy) are eligible for every stage and therefore run in both. The builtin fixers still rewrite files in CI, and that is fine: prek fails any hook that modifies a file, and the action passes `--show-diff-on-failure`, so the diff *is* their report.

Gotcha if hook `groups` are ever reintroduced: passing `--group`/`--no-group` without an explicit `--stage` disables the default `pre-commit` stage filter, which would run both variants of each ruff hook. Pair group selectors with `--stage pre-commit`.

## Architecture

Everything lives in `src/pyjsonrpc2/server.py`. `src/pyjsonrpc2/__init__.py` re-exports only the `server` submodule, so the public import path is `from pyjsonrpc2.server import JsonRpcServer, rpc_method, JsonRpcError`.

### Request pipeline

`call()` → `_decode_and_parse()` → `_validate_and_execute()` (once per request) → `_respond()` → `_encode()`.

- `_decode_and_parse` handles JSON parsing and the batch/single split. Batch elements are validated+executed individually, each serialized immediately, then wrapped in `orjson.Fragment` so the outer `dumps` splices pre-serialized bytes instead of re-encoding them.
- `_validate_and_execute` returns a **tuple that is splatted into `_respond(obj, id, error)`**. The arity encodes the outcome — see the `_Outcome` alias in the `TYPE_CHECKING` block — and is the single most confusing convention in the file:
  - 1-tuple `(error_dict,)` → protocol-level failure before an id could be trusted; responds with `"id": null`.
  - 2-tuple `(error_dict, id)` → failure attributable to a specific request.
  - 3-tuple `(result, id, False)` → success (the `False` flips `_respond` from the `"error"` key to `"result"`).
- `_encode` returns `None` for a falsy response (notification, or a batch that produced no responses), which is how "emit nothing" propagates out of `call()`.

### Notifications

A missing `"id"` key becomes `_SENTINEL` (not `None` — `null` is a *valid* id per spec). `_respond` returns `None` whenever the id is `_SENTINEL`, so notifications are dropped at the point of response construction rather than being special-cased throughout.

### Method registry

- `@rpc_method` stamps an `__rpc__` attribute (the custom name, or `None` to use `__name__`) on the function. It works bare or called with `name=`.
- `JsonRpcServer.__init__` calls `add_object(self)`, so subclasses that decorate their own methods self-register on instantiation. `add_object` walks `inspect.getmembers(..., isroutine)` looking for `__rpc__`, so a decorated method shadows an undecorated same-named one only by way of the explicit registry name.
- `add_method` refuses duplicate names with `ValueError`.

### Error handling

- `JsonRpcError` is the escape hatch for implementation/application-defined codes; it is caught and serialized verbatim via `to_dict()`.
- Any other exception becomes `-32603 Internal error` with `str(e)` as `data`, logged via `_LOGGER.exception`.
- `-32602 Invalid params` is detected *after the fact*: a `TypeError` escaping the call is re-checked with `inspect.signature(method).bind(*args, **kwargs)`. If binding also fails, it was an arity/keyword mismatch → invalid params; otherwise the `TypeError` came from inside the method body and is re-raised into the internal-error path. Preserve this ordering when touching the call site.
- Unserializable return values are caught in `_encode`, which recurses once to emit an internal error for the same id.

Standard protocol errors live in the `_Error` enum; `with_data()` clones the entry with a `data` field rather than mutating it.
