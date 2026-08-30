"""Benchmark suite for the `JsonRpcServer.call()` pipeline.

Run it through tox, which supplies pyperf and the package itself::

    tox -e bench-server                          # every benchmark
    tox -e bench-server -- -b batch              # only the names containing "batch"
    tox -e bench-server -- --fast                # fewer values, while iterating
    tox -e bench-server -- --affinity 2          # pin the workers, for less jitter
    tox -e bench-server -- -o .benchmarks/before.json

The client half has its own suite, `bench_client.py`, run by `tox -e bench-client`.

Comparisons are made with `tox -e bench-compare -- <before.json> <after.json>`.
Only compare runs from the same machine and interpreter, otherwise the
difference being measured is the environment rather than the code.
"""

from __future__ import annotations

from typing import Any, NoReturn

from orjson import dumps

from pyjsonrpc2.server import JsonRpcError, JsonRpcServer, rpc_method

from . import make_runner


class BenchServer(JsonRpcServer):
    @rpc_method
    def noop(self) -> None:
        """Do nothing, to keep the measurement on the pipeline itself."""

    @staticmethod
    @rpc_method
    def subtract(minuend: float = 0, subtrahend: float = 0) -> float:
        return minuend - subtrahend

    @staticmethod
    @rpc_method
    def echo(value: Any) -> Any:
        return value

    @rpc_method
    def fail(self) -> NoReturn:
        raise JsonRpcError(code=-32000, message="Benchmark error", data={"foo": "bar"})


def _request(method: str, params: Any = None, id: Any = 1) -> dict[str, Any]:  # noqa: A002
    """Build a well-formed request object, omitting the absent members."""
    request: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        request["params"] = params
    if id is not None:
        request["id"] = id
    return request


# A payload big enough for orjson to dominate the measurement, which is what
# makes it the counterweight to the small requests around it.
_BIG_VALUE = [
    {"index": i, "name": f"item-{i}", "tags": ["a", "b", "c"]} for i in range(1000)
]

_BATCH = [
    _request("subtract", [i, 1], id=i) if i % 3 else _request("noop", id=None)
    for i in range(10)
]

PAYLOADS: dict[str, bytes] = {
    # Happy paths.
    "no-params": dumps(_request("noop")),
    "positional": dumps(_request("subtract", [42, 23])),
    "named": dumps(_request("subtract", {"minuend": 42, "subtrahend": 23})),
    "notification": dumps(_request("noop", id=None)),
    "large-payload": dumps(_request("echo", [_BIG_VALUE])),
    "batch-10": dumps(_BATCH),
    # Error paths. `invalid-params` is the expensive one: it only reaches its
    # verdict through `inspect.signature().bind()`, after the call has failed.
    "parse-error": b'{"jsonrpc": "2.0", "method": "noop", "id": 1',
    "invalid-request": dumps({"method": "noop", "id": 1}),
    "method-not-found": dumps(_request("does-not-exist")),
    "invalid-params": dumps(_request("subtract", {"minuend": 42, "nope": 23})),
    "custom-error": dumps(_request("fail")),
}


if __name__ == "__main__":
    server = BenchServer()
    runner, selected = make_runner("pyjsonrpc2 JsonRpcServer.call()")
    for name, payload in PAYLOADS.items():
        if selected and selected not in name:
            continue
        runner.timeit(
            name,
            stmt="call(payload)",
            globals={"call": server.call, "payload": payload},
        )
