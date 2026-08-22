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
prek run pyrefly-check --all-files     # type checking alone (strict preset, via [tool.pyrefly]), in the hook's isolated env
prek run --all-files --hook-stage manual  # what CI runs: report-only ruff, no rewriting
tox -e bench                           # benchmarks, local only (see below)
tox -e bench-compare -- a.json b.json  # compare two recorded benchmark runs
```

The default env list is tests only (`3.11`–`3.14`, `mindeps`, `coverage`); lint, format, and type checking live in `prek.toml`. `tox -e prek` exists as an *additional* env — not in `env_list`, so a bare `tox` skips it — that runs every hook from a tox-managed env instead of a global `prek`.

### Coverage across jobs

`coverage` inherits `set_env.COVERAGE_FILE = "{work_dir}/.coverage"` from `env_run_base`, and `coverage combine` reads the parallel data files out of that `work_dir`. Locally a single `tox -p` puts all of them there. CI splits the versions across matrix jobs, which do not share a filesystem, so the data is reassembled explicitly:

- each leg uploads `.tox/.coverage.*` as `coverage-<version>`. `include-hidden-files: true` is **mandatory** — those are dot-files inside a dot-folder, and upload-artifact excludes both by default, producing a silently empty artifact. `if-no-files-found: error` guards against that regressing;
- the `coverage` job downloads them with `merge-multiple: true` back into `.tox/`, then runs `tox -e coverage`. Checkout must precede the download, since checkout cleans the workspace.

Do **not** move `coverage` into the per-leg env lists to avoid this. That would enforce `fail_under = 100` per interpreter, which passes today only because nothing in `src/` is version-gated; the first `if sys.version_info >= …` branch would fail whichever legs don't execute it, while the combined gate would correctly pass.

`[tool.tox.gh.python]` drives the per-leg env selection, and `mindeps` rides along in the 3.11 leg. tox-gh is deliberately **not** in `[tool.tox] requires` — it is installed only in the CI legs (`uv tool install tox --with tox-uv --with tox-gh`).

The leg's version must be handed to tox-gh explicitly via `TOX_GH_MAJOR_MINOR`. Its autodetection resolves `shutil.which("python") or sys.executable` — the runner image's default interpreter, *not* the one tox is installed under — and since the workflow never runs `actions/setup-python`, that is the same version on every leg. Without the env var the entire matrix silently collapses onto whichever env that default maps to, and the other envs (including `mindeps`) never run. Passing `--python <version>` to `uv tool install` does **not** influence this; with tox-uv, tox provisions each env's interpreter itself.

Two further trade-offs: tox silently ignores the `gh` table when the plugin is absent, so a typo in it cannot be caught locally — only by observing which envs a CI leg actually runs. And tox-gh bails out entirely if an envlist is given explicitly (`-e`/`TOXENV`), so the bare `tox` invocation in the workflow is load-bearing.

`ruff`, `tox`, and `prek` are expected as `uv tool install`-ed globals, not project dependencies. The tox config lists `tox-uv` in `requires`, so tox provisions it if the global install lacks it; `mindeps` needs it for `uv_resolution`. There are **no dependency groups at all**: every tool is either a global, installed by the tox env that runs it (`coverage`), or supplied by `prek` in an isolated hook env. Run the tooling through `tox` or `prek`, never `uv run` — `.venv` holds only the package and `orjson`.

Tests use `unittest`, not pytest. Coverage is enforced at `fail_under = 100`, ruff runs with `select = ["ALL"]`, and type checking is `preset = "strict"` in `[tool.pyrefly]` — so a bare `pyrefly check` from the repo root is already strict. New code must be fully typed and either covered or explicitly marked `# pragma: no cover`.

One thing must be kept in sync by hand: `additional_dependencies` on **both** `pyrefly-check` entries in `prek.toml`. They stand in for two lists — `dependencies` in `[project]` (`orjson`), and whatever the non-`src` paths of `project-includes` import (`pyperf`, for `benchmarks/`). The hooks run in isolated envs, so a new import in either place must be repeated in both entries or the strict preset will fail on the unresolvable module. Note that `pyjsonrpc2` itself is *not* installed in those envs — pyrefly resolves the package from `src/` because `project-includes` lists it.

Type checking was mypy until the switch to pyrefly (for GitHub annotations, see below). Two consequences of the swap are load-bearing:

- the strict preset enables `implicit-any` and `unused-ignore`, which mypy's `strict = true` does not fully cover. Empty containers need an explicit annotation (`kwargs: dict[str, Any] = {}`) and lambdas need typed parameters, so prefer a nested `def`;
- `errors.missing-override-decorator = false` is set because that rule wants `typing.override`, which is 3.12+, and `typing_extensions` is not a dependency. Drop the opt-out if the floor ever rises to 3.12.

The swap also forced `_Outcome` to become a fixed-length tuple — see the request pipeline section below. The only suppression left in `src/` is the pre-existing `# type: ignore[attr-defined]` on `f.__rpc__`, which pyrefly honours. Because `unused-ignore` is on, a stale suppression is a hard error, so suppressions here are verified rather than decorative.

Note that ruff formats Python code blocks inside Markdown, so `README.md` is subject to `ruff format`.

### Hooks: local vs CI

`prek.toml` registers `ruff-check`, `ruff-format`, and `pyrefly-check` **twice** each, split by `stages`:

- the `pre-commit` variants are for humans: ruff rewrites the tree (`--fix`; formatting in place) and pyrefly prints its default readable diagnostics. They are what the git hook and a bare `prek run` use;
- the `manual` variants (`ruff-check-ci`, `ruff-format-ci`, `pyrefly-check-ci`) are for CI. The ruff ones only report — no `--fix`, and `--check` for the formatter — so nothing is repaired out from under the report. `pyrefly-check-ci` adds `--output-format full-text-with-github`.

`.github/workflows/tests.yml` selects the reporting variants with `--hook-stage manual` and sets `RUFF_OUTPUT_FORMAT=github` at the job level, which **both** ruff subcommands honour (`ruff format` accepts `--output-format` when `--check` is passed, contrary to what the formatter docs page lists). Violations become GitHub annotations; the other hooks ignore the variable.

pyrefly does not read that variable — it takes the flag instead, and needs the `full-text-with-github` format rather than plain `github`: the latter emits *only* `::error` workflow commands, which are collapsed in the log view, so the run log would show annotations and no readable diagnostics. `full-text-with-github` emits both.

Hooks without a `stages` key (the builtins) are eligible for every stage and therefore run in both. The builtin fixers still rewrite files in CI, and that is fine: prek fails any hook that modifies a file, and the action passes `--show-diff-on-failure`, so the diff *is* their report.

Gotcha if hook `groups` are ever reintroduced: passing `--group`/`--no-group` without an explicit `--stage` disables the default `pre-commit` stage filter, which would run both variants of each duplicated hook. Pair group selectors with `--stage pre-commit`.

## Benchmarks

`benchmarks/bench_server.py` is a [pyperf](https://pyperf.readthedocs.io) suite over `JsonRpcServer.call()`: one timing per *shape* of request — the happy paths, a notification (no encode step), a batch, a payload large enough for orjson to dominate, and every error path. `invalid-params` is worth its slot because it is the one branch that pays for `inspect.signature().bind()` after the call has already failed; it used to cost roughly ten times a successful call, and still costs ~4x after the signature memoization, so any change to that fallback shows up immediately.

```bash
tox -e bench                                # every benchmark (a few minutes)
tox -e bench -- --fast                      # coarser but ~4x quicker, while iterating
tox -e bench -- -b batch                    # only the names containing "batch"
tox -e bench -- -o .benchmarks/before.json  # record a baseline
tox -e bench-compare -- .benchmarks/before.json .benchmarks/after.json
```

`bench` and `bench-compare` are *additional* envs, like `prek`: absent from `env_list`, so a bare `tox` skips them. `commands_pre` creates `.benchmarks/` because pyperf will not create the directory its `-o` points into; that directory is gitignored.

**Benchmarks are deliberately local-only.** GitHub's hosted runners are shared, unpinned to a physical core, and vary in CPU model between jobs, and `pyperf system tune` has nothing to tune there. The resulting run-to-run spread is wide enough to bury the size of change actually worth measuring, so a CI gate would produce false alarms and mask real regressions rather than catch them. Per-commit tracking would need a dedicated self-hosted machine and a `schedule`/`workflow_dispatch` trigger — not the PR gate. CI does still lint and type-check `benchmarks/`, since `project-includes` and the prek hooks cover the whole tree; it just never runs it.

### Reading the stability warnings

A default run prints `the benchmark result may be unstable / Not enough samples to get a stable result` for most of the suite. That check is **not** a verdict on the numbers. `format_checks()` in pyperf's `_cli.py` warns in two very different situations:

- the standard deviation is ≥10% of the mean — the real alarm, and it does not currently fire for any benchmark here;
- otherwise, `required_nprocesses()` — the process count that would give 95% confidence in a ±1% measurement — exceeds the 20 processes actually run. That is the message we get, and ±1% is a far finer bar than the decisions it informs.

Measured on `ROG-TOWER` (i9-10850K, 3.14, default settings), stdev as a share of the mean and the processes wanted for ±1%:

| | stdev | wants | | stdev | wants |
|---|---|---|---|---|---|
| notification | 1.0% | 14 | parse-error | 2.6% | 60 |
| positional | 1.5% | 31 | custom-error | 3.2% | 78 |
| method-not-found | 1.6% | 24 | large-payload | 6.2% | 135 |
| no-params | 1.7% | 32 | invalid-params | 4.2% | 203 |
| invalid-request | 2.0% | 32 | batch-10 | 4.0% | 222 |
| named | 2.8% | 50 | | | |

Precision scales with the square root of the sample count, so 20 processes resolve an effect of roughly `1% × sqrt(wants / 20)` — about 3–4% for the worst entries. **That is the suite's real sensitivity: it detects regressions of ~4% and up.** Raising `processes` to chase the warning away is a bad trade — batch-10 would need ~11× the runtime for a 3× finer bar — and `compare_to` applies its own significance test regardless, so the failure mode is a missed 2% change, never a phantom one. Do not paper over it with `--quiet` either: that also hides the ≥10% stdev warning, which is the one worth reacting to.

What does help is pinning the workers to one core, which removes scheduler migration and core-to-core turbo variation (this CPU is not hybrid, so no P/E-core effect is involved):

```bash
tox -e bench -- --affinity 2   # any core; avoid 0, which fields more interrupts
```

For `invalid-params` that moved stdev from 3.2% to 1.6% and `wants` from 81 to 20. It is uneven, though: `batch-10` and `large-payload` barely improved (222 → 192, 135 → 90), because their between-process spread comes from allocation and GC timing rather than core placement. `large-payload` is the least sensitive benchmark in the suite; treat anything under ~8% there as noise.

The trap with pinning is that it also shifts the *mean* — `invalid-params` measured 16.9 µs pinned against 17.4 µs unpinned, a 2.9% systematic difference, which is the same size as the regressions this suite exists to catch. Pin both sides of a comparison or neither.

Finally, the warning's own advice to run `pyperf system tune` is a dead end here: every tuning operation in pyperf's `_system.py` is gated on `OS_LINUX`, so on Windows the command has nothing to tune.

Results are comparable only within one machine, one interpreter, and one set of run settings — and **`compare_to` does not check any of that**. `_compare.py` reads the values and the `tags` metadata, nothing else, so files from different machines, interpreters, or affinity settings are compared without complaint. Guarding the methodology is on us.

Between-session drift is real, and the significance test does not cover it: it models the variance *within* each run, not the gap between two sessions. Re-running `invalid-params` alone at the baseline settings with no code change produced 17.1 µs against the baseline's 16.7 µs — reported as a significant `1.02x slower`. So treat any verdict under ~3% against a stored baseline as unproven, and settle marginal cases by re-recording both sides back-to-back in one session.

That still leaves the day-to-day loop cheap, because only the candidate side has to be measured. `compare_to` accepts a candidate holding a *subset* of the baseline's benchmarks — it compares the intersection and prints `Ignored benchmarks (N)` — so scope the run to what the change touches:

```bash
tox -e bench -- -b invalid-params --affinity 2 --fast    # ~10 s, catches >10% moves
tox -e bench -- -b invalid-params --affinity 2 --rigorous -o .benchmarks/candidate.json
tox -e bench-compare -- .benchmarks/baseline.json .benchmarks/candidate.json
```

Keep `--affinity 2` on both sides even when iterating with `--fast`: a smaller sample only widens the confidence interval, whereas pinning shifts the mean. The full ~6.5-minute run is needed only when a change touches the shared pipeline (`call`, `_encode`, `_validate_and_execute`), or to re-record `baseline.json` once it goes stale — after merging anything that moves the numbers, or after an interpreter upgrade.

## Architecture

Everything lives in `src/pyjsonrpc2/server.py`. `src/pyjsonrpc2/__init__.py` re-exports only the `server` submodule, so the public import path is `from pyjsonrpc2.server import JsonRpcServer, rpc_method, JsonRpcError`.

### Request pipeline

`call()` holds the whole flow — parse, batch-vs-single, notification filtering — and every path ends in the same two-argument-plus-flag `_encode()`:

- **single request** — `_validate_and_execute()` → unpack → `_encode(obj, id, error)`;
- **batch** — the same per element, each result wrapped in `orjson.Fragment` so the outer `self._dumps()` splices pre-serialized bytes instead of re-encoding them.

`_encode` is the **sole definition of the response envelope**: it builds `{"jsonrpc", "id", "error"|"result"}` and serializes it. Its defaults (`id=None`, `error=True`) cover the two protocol-level failures — parse error and empty batch — that have no id to answer under and are written as a bare `_encode(payload)`.

This replaced an earlier arrangement with a `_respond()` envelope builder, a single-use `_batch()`, and an `_encode()` that also handled `None`/list/dict via three `@overload`s. The single-request path was inlined into `call()` to skip the `_respond` + `_encode` calls and the `_Outcome` splat, which cost ~8% — at the price of the envelope literal and the `dumps` invocation each living in two places that had to be kept in sync. Folding the envelope into `_encode` and pre-binding the encoder (below) bought that ~8% back by other means, so the duplication is gone and the numbers held: a full rigorous A/B came out at **geomean 1.01x faster**, with `batch-10` 1.06x and `parse-error` 1.06x faster, nothing significant on the hot single-request benchmarks, and the three ≤2% "slower" entries inside the suite's noise floor.

- `self._dumps` is bound **once, in `__init__`** — `partial(dumps, **dumps_kwargs)`, or `orjson.dumps` itself when there are no kwargs, so the common case carries no wrapper. This is what removed the `kwargs = self._dumps_kwargs; dumps(x, **kwargs) if kwargs else dumps(x)` dance that used to be repeated at every call site. The trade-off is that `dumps_kwargs` is now read only at construction; mutating it afterwards has no effect.
- `_validate_and_execute` returns a **fixed 3-tuple**, the `_Outcome` alias in the `TYPE_CHECKING` block, which `call()` unpacks. Every return site spells out all three:
  - `(error_dict, None, True)` → protocol-level failure before an id could be trusted; the `None` responds with `"id": null`.
  - `(error_dict, id, True)` → failure attributable to a specific request.
  - `(result, id, False)` → success (the `False` flips `_encode` from the `"error"` key to `"result"`).

  This was originally a union of 1-, 2-, and 3-tuples whose *arity* encoded the outcome, leaning on the then-splat target's defaults to fill the gaps. Only mypy verified that: it expands a union at a call site and checks each member separately ("union math"), whereas pyrefly flattens the union into one unknown-length tuple and assigns the union of every element type to every parameter, which fails. Keep the tuple fixed-length.
- "Emit nothing" is decided in `call()` on both paths: `None` when a single request's id is `_SENTINEL`, and `None` when a batch produced no responses at all. `_encode` itself always returns `bytes`.

### Notifications

A missing `"id"` key becomes `_SENTINEL` (not `None` — `null` is a *valid* id per spec). `call()` checks for `_SENTINEL` on both paths — returning `None` for a single notification, and skipping the element in the batch loop — so nothing downstream of it has to know about notifications; `_encode` is only ever handed a request that is owed an answer.

### Method registry

- `@rpc_method` stamps an `__rpc__` attribute (the custom name, or `None` to use `__name__`) on the function. It works bare or called with `name=`.
- `JsonRpcServer.__init__` calls `add_object(self)`, so subclasses that decorate their own methods self-register on instantiation. `add_object` walks `inspect.getmembers(..., isroutine)` looking for `__rpc__`, so a decorated method shadows an undecorated same-named one only by way of the explicit registry name.
- `add_method` refuses duplicate names with `ValueError`.

### Error handling

- `JsonRpcError` is the escape hatch for implementation/application-defined codes; it is caught and serialized verbatim via `to_dict()`. Its display string is built in `__str__`, **not** in `__init__` — the RPC path only ever calls `to_dict()`, so eagerly formatting `f"[{code}] {message}: {data}"` meant calling `repr()` on the `data` object for a string nobody read. Making it lazy took `custom-error` from 3.02 µs to 2.26 µs (1.34x). The visible consequence is that **`args` holds `(code, message, data)` rather than the formatted message**, which also changes `repr()`; `str()` is unchanged. Anything added to `__init__` later must keep it free of formatting work.
- Any other exception becomes `-32603 Internal error` with `str(e)` as `data`, logged via `_LOGGER.exception`.
- `-32602 Invalid params` is detected *after the fact*: a `TypeError` escaping the call is re-checked with `self._signature(method).bind(*args, **kwargs)`. If binding also fails, it was an arity/keyword mismatch → invalid params; otherwise the `TypeError` came from inside the method body and is re-raised into the internal-error path. Preserve this ordering when touching the call site.
- `_signature` memoizes `inspect.signature` in `self._signatures`, keyed on **the callable itself, not the registry name** — so an entry rebound in `_methods` can never be answered with a stale signature. Building the `Signature` is ~3x everything else on that path put together; memoizing it took `invalid-params` from 17.2 µs to 5.8 µs. An unhashable callable (`__eq__` without `__hash__`) makes the cache lookup raise `TypeError`, which *must not* escape — the enclosing handler would read it as a verdict of invalid params — so it is caught and the lookup falls through uncached. `tests.test_server.JsonRpcServerTest.test_unhashable_method` covers that.
- Unserializable return values are caught in `_encode`, which recurses to emit an internal error for the same id. The recursion is always exactly one deep: the replacement payload is `str(e)` and the id already survived a JSON decode, so the second `dumps` cannot fail.

Standard protocol errors live in the `_Error` enum. Each member also hangs its payload off a plain `body` attribute set in `__init__`: `.value` resolves through a `DynamicClassAttribute` descriptor on every read, which costs ~100 ns, while `.body` is an ordinary instance attribute holding the same dict. Read `.body` on hot paths; `.value` still works and is the same object. `with_data()` clones the entry with a `data` field rather than mutating it.

### Exact-type checks

`type(x) is dict` / `type(id) not in _ID_TYPES` are used instead of `isinstance` for values that came out of `orjson.loads`, which only ever yields exact `dict`/`list`/`str`/`int`/`float`/`bool`/`None`. This is what makes the `id` check reject `bool` for free, where `isinstance` needs an explicit `isinstance(id, bool)` guard first. The checks are **only** valid for decoded-JSON values — do not copy the idiom to anything a caller supplies directly.
