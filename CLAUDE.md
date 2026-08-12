# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`pyjsonrpc2` — a transport-agnostic, server-side implementation of JSON-RPC 2.0. The library never touches sockets or HTTP: `JsonRpcServer.call()` takes a raw request (`str`/`bytes`/`bytearray`/`memoryview`) and returns raw response `bytes` (or `None` for notifications). Embedding it in a transport is the caller's job.

Requires Python >=3.11. Only runtime dependency is `orjson`. Built with the `uv_build` backend; `uv.lock` is committed.

## Commands

```bash
uv sync                                # install package + dev group (coverage, mypy)
uv run python -m unittest              # run all tests
uv run python -m unittest tests.test_server.JsonRpcServerTest.test_positional_parameters  # single test
tox -p                                 # full gate: py311–py314 + lint + type + coverage
tox -e lint                            # ruff check + ruff format --check
tox -e type                            # mypy (strict, via [tool.mypy])
uv run mypy                            # same check, using the project env
prek run --all-files                   # all hooks over the whole tree
```

`ruff`, `tox`, and `prek` are expected as `uv tool install`-ed globals, not project dependencies. Only `coverage` and `mypy` live in `[dependency-groups] dev`, because they are the two that must see the project's own environment.

Tests use `unittest`, not pytest. Coverage is enforced at `fail_under = 100`, ruff runs with `select = ["ALL"]`, and `strict = true` is set in `[tool.mypy]` — so a bare `mypy` is already strict. New code must be fully typed and either covered or explicitly marked `# pragma: no cover`.

Two version pins must be kept in sync by hand: the `ruff` rev in `.pre-commit-config.yaml` and `ruff==` in the tox `lint` env.

Note that ruff formats Python code blocks inside Markdown, so `README.md` is subject to `ruff format`.

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
