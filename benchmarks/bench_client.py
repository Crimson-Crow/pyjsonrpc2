"""Benchmark suite for the `JsonRpcClient` request and response pipelines.

Run it through tox, which supplies pyperf and the package itself::

    tox -e bench-client                       # every benchmark
    tox -e bench-client -- -b handle          # only the names containing "handle"
    tox -e bench-client -- --fast             # fewer values, while iterating
    tox -e bench-client -- --affinity 2       # pin the workers, for less jitter
    tox -e bench-client -- -o .benchmarks/before-client.json

Comparisons are made with `tox -e bench-compare -- <before.json> <after.json>`. Only
compare runs from the same machine and interpreter. Otherwise the difference being
measured is the environment rather than the code.

The client keeps state, so this suite cannot use `Runner.timeit` the way the server
suite does:

- `request()` adds a pending future, and nothing removes it. A `timeit` loop would grow
  `_pending` to a size that no caller reaches.
- `handle()` removes a pending future. A `timeit` loop would find an empty registry
  after the first iteration, and would measure the drop path instead.

Every benchmark therefore builds its own client, and the pending futures that it needs,
before the clock starts. Setup is not part of any measurement.

Two more rules follow from that setup:

- the collector is off inside every timed part. Thousands of live futures are an
  artifact of the loop length, and no caller keeps that many. A collection here would
  measure the loop rather than the client.
- each `handle-*` benchmark answers the ids 1 to N, so it depends on the documented
  default id iterator of the client, `itertools.count(1)`. A change to that default
  sends every response down the unknown-id path, and the suite reports no error.
"""

from __future__ import annotations

import gc
import logging
from typing import TYPE_CHECKING, Any

import pyperf
from orjson import dumps

from pyjsonrpc2.client import InvalidResponseError, JsonRpcClient

from . import make_runner

if TYPE_CHECKING:
    from collections.abc import Callable

# The drop paths of `handle()` log a warning. With no handler at all, the logging module
# writes those to stderr, which would add I/O to the measurement. A null handler keeps
# the cost of the log record, which a caller also pays, and removes the I/O.
logging.getLogger("pyjsonrpc2.client").addHandler(logging.NullHandler())

# A payload big enough for orjson to dominate the measurement, which is what makes it
# the counterweight to the small requests around it.
_BIG_VALUE = [
    {"index": i, "name": f"item-{i}", "tags": ["a", "b", "c"]} for i in range(1000)
]

_ERROR_OBJECT = {"code": -32000, "message": "Benchmark error", "data": {"foo": "bar"}}


def _result_response(id: int) -> dict[str, Any]:  # noqa: A002
    """Build a successful response for one request id."""
    return {"jsonrpc": "2.0", "id": id, "result": 19}


def _error_response(id: int) -> dict[str, Any]:  # noqa: A002
    """Build a response that fails one request with a `JsonRpcError`."""
    return {"jsonrpc": "2.0", "id": id, "error": _ERROR_OBJECT}


def _large_response(id: int) -> dict[str, Any]:  # noqa: A002
    """Build a successful response that carries the large payload."""
    return {"jsonrpc": "2.0", "id": id, "result": _BIG_VALUE}


def _malformed_response(id: int) -> dict[str, Any]:  # noqa: A002
    """Build a response that holds both `"result"` and `"error"`.

    The specification allows only one of the two, so the client fails that request with
    an `InvalidResponseError`.
    """
    return {"jsonrpc": "2.0", "id": id, "result": 19, "error": _ERROR_OBJECT}


def _fill_pending(client: JsonRpcClient, number: int) -> None:
    """Register `number` requests on the client, and drop the bytes of each one.

    The ids are 1 to `number`, because the client numbers its requests from 1. Each
    response that a benchmark prepares answers one of those ids.
    """
    for _ in range(number):
        client.request("noop")


def _time_handle(client: JsonRpcClient, payloads: list[bytes]) -> float:
    """Time one `handle()` call for each payload, and return the elapsed seconds."""
    handle = client.handle
    gc.disable()
    t0 = pyperf.perf_counter()
    for payload in payloads:
        handle(payload)
    elapsed = pyperf.perf_counter() - t0
    gc.enable()
    return elapsed


def _bench_build(
    loops: int, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> float:
    """Time `loops` calls of `request()` or of `notify()` on one client."""
    client = JsonRpcClient()
    build = getattr(client, method)
    range_it = range(loops)
    gc.disable()
    t0 = pyperf.perf_counter()
    for _ in range_it:
        build(*args, **kwargs)
    elapsed = pyperf.perf_counter() - t0
    gc.enable()
    return elapsed


def _bench_build_batch(loops: int, size: int) -> float:
    """Time `loops` batches of `size` calls, each batch built and then encoded.

    The mix of requests and notifications is the mix that the server suite receives.
    """
    client = JsonRpcClient()
    range_it = range(loops)
    gc.disable()
    t0 = pyperf.perf_counter()
    for _ in range_it:
        batch = client.batch()
        for i in range(size):
            if i % 3:
                batch.request("subtract", i, 1)
            else:
                batch.notify("noop")
        batch.encode()
    elapsed = pyperf.perf_counter() - t0
    gc.enable()
    return elapsed


def _bench_handle(loops: int, build: Callable[[int], dict[str, Any]]) -> float:
    """Time `loops` calls of `handle()`, each one on a single matched response."""
    client = JsonRpcClient()
    _fill_pending(client, loops)
    return _time_handle(client, [dumps(build(id)) for id in range(1, loops + 1)])  # noqa: A001


def _bench_handle_batch(
    loops: int, build: Callable[[int], dict[str, Any]], size: int
) -> float:
    """Time `loops` calls of `handle()`, each one on a batch of `size` responses."""
    client = JsonRpcClient()
    total = loops * size
    _fill_pending(client, total)
    payloads = [
        dumps([build(start + n) for n in range(size)])
        for start in range(1, total + 1, size)
    ]
    return _time_handle(client, payloads)


def _bench_handle_unmatched(loops: int, response: dict[str, Any]) -> float:
    """Time `loops` calls of `handle()` on a response that answers no request.

    The client settles nothing on this path, so one payload serves every iteration and
    the client needs no pending future.
    """
    return _time_handle(JsonRpcClient(), [dumps(response)] * loops)


def _bench_handle_parse_error(loops: int) -> float:
    """Time `loops` calls of `handle()` on a payload that is not valid JSON.

    `handle()` raises on this path, so the cost of the raise and of the catch is part of
    the measurement.
    """
    handle = JsonRpcClient().handle
    range_it = range(loops)
    gc.disable()
    t0 = pyperf.perf_counter()
    for _ in range_it:
        try:
            handle(b'{"jsonrpc": "2.0", "id": 1, "result": 19')
        except InvalidResponseError:
            pass
    elapsed = pyperf.perf_counter() - t0
    gc.enable()
    return elapsed


if __name__ == "__main__":
    runner, selected = make_runner("pyjsonrpc2 JsonRpcClient")

    def add(name: str, func: Callable[..., float], *args: Any) -> None:
        """Register one benchmark, unless the caller filtered its name out."""
        if selected is None or selected in name:
            runner.bench_time_func(name, func, *args)

    # Request side.
    add("build-no-params", _bench_build, "request", ("noop",), {})
    add("build-positional", _bench_build, "request", ("subtract", 42, 23), {})
    add(
        "build-named",
        _bench_build,
        "request",
        ("subtract",),
        {"minuend": 42, "subtrahend": 23},
    )
    add("build-notification", _bench_build, "notify", ("noop",), {})
    add("build-large-payload", _bench_build, "request", ("echo", _BIG_VALUE), {})
    add("build-batch-10", _bench_build_batch, 10)

    # Response side.
    add("handle-result", _bench_handle, _result_response)
    add("handle-error", _bench_handle, _error_response)
    add("handle-large-payload", _bench_handle, _large_response)
    add("handle-invalid-response", _bench_handle, _malformed_response)
    add("handle-batch-10", _bench_handle_batch, _result_response, 10)
    # Responses that belong to no pending request. The client returns the error of the
    # first one to the caller, and logs the second one.
    add(
        "handle-null-id-error",
        _bench_handle_unmatched,
        {"jsonrpc": "2.0", "id": None, "error": _ERROR_OBJECT},
    )
    add("handle-unknown-id", _bench_handle_unmatched, _result_response(999))
    add("handle-parse-error", _bench_handle_parse_error)
