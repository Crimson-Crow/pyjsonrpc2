from __future__ import annotations

import json
import threading
import unittest
from concurrent.futures import CancelledError, wait
from typing import Any, NoReturn

from pyjsonrpc2.client import InvalidResponseError, JsonRpcClient, JsonRpcError
from pyjsonrpc2.server import JsonRpcServer, rpc_method

_LOGGER_NAME = "pyjsonrpc2.client"


class Handler(JsonRpcServer):
    """The server half of every round trip below."""

    recorded: list[Any] = []  # noqa: RUF012

    @staticmethod
    @rpc_method
    def subtract(minuend: float = 0, subtrahend: float = 0) -> float:
        return minuend - subtrahend

    @staticmethod
    @rpc_method(name="sum")
    def add(*args: float) -> float:
        return sum(args)

    @rpc_method
    def custom_error(self) -> NoReturn:
        raise JsonRpcError(code=-32000, message="foobar", data={"foo": "bar"})

    @rpc_method
    def record(self, value: Any) -> None:
        self.recorded.append(value)


class JsonRpcClientTest(unittest.TestCase):
    rpc: Handler

    @classmethod
    def setUpClass(cls) -> None:
        cls.rpc = Handler()

    def setUp(self) -> None:
        # Each test gets a new client, so the ids always start at 1
        self.client = JsonRpcClient()
        self.rpc.recorded.clear()

    def assert_encodes(self, request: bytes, expected: dict[str, Any]) -> None:
        self.assertEqual(json.loads(request), expected)

    def round_trip(self, request: bytes) -> list[JsonRpcError]:
        """Give a request to the server, then give its answer to the client."""
        response = self.rpc.call(request)
        if response is None:
            self.fail("Expected a response, got None")
        return self.client.handle(response)

    def assert_malformed(self, response: str) -> None:
        """A malformed response must fail the future that it matches, not the reader."""
        client = JsonRpcClient()
        _, future = client.request("subtract")
        self.assertEqual(client.handle(response), [])
        self.assertRaises(InvalidResponseError, future.result)

    def test_positional_parameters(self) -> None:
        request, _ = self.client.request("subtract", 42, 23)
        self.assert_encodes(
            request,
            {"jsonrpc": "2.0", "method": "subtract", "params": [42, 23], "id": 1},
        )

    def test_named_parameters(self) -> None:
        request, _ = self.client.request("subtract", minuend=42, subtrahend=23)
        self.assert_encodes(
            request,
            {
                "jsonrpc": "2.0",
                "method": "subtract",
                "params": {"minuend": 42, "subtrahend": 23},
                "id": 1,
            },
        )

    def test_no_parameters(self) -> None:
        # "params" is optional. The client must leave it out, not send it empty.
        request, _ = self.client.request("subtract")
        self.assert_encodes(request, {"jsonrpc": "2.0", "method": "subtract", "id": 1})

    def test_ids_are_unique(self) -> None:
        for expected_id in (1, 2, 3):
            with self.subTest(id=expected_id):
                request, _ = self.client.request("subtract")
                self.assertEqual(json.loads(request)["id"], expected_id)

    def test_mixed_parameters(self) -> None:
        # "params" is either an array or an object, so there is nowhere to put both
        batch = self.client.batch()
        for label, call in (
            ("request", self.client.request),
            ("notify", self.client.notify),
            ("batch.request", batch.request),
            ("batch.notify", batch.notify),
        ):
            with self.subTest(call=label):
                self.assertRaises(ValueError, call, "subtract", 42, subtrahend=23)

    def test_method_must_be_a_string(self) -> None:
        self.assertRaises(TypeError, self.client.request, 123)

    def test_method_is_positional_only(self) -> None:
        # `method` is positional-only. A parameter of the same name therefore goes
        # into "params" and does not collide with it.
        request = self.client.notify("record", method="value")
        self.assert_encodes(
            request,
            {"jsonrpc": "2.0", "method": "record", "params": {"method": "value"}},
        )

    def test_round_trip(self) -> None:
        request, future = self.client.request("subtract", 42, 23)
        self.assertEqual(self.round_trip(request), [])
        self.assertEqual(future.result(), 19)

    def test_notification(self) -> None:
        request = self.client.notify("record", 5)
        # There is no id, so the server owes no answer and nothing waits for one
        self.assertNotIn("id", json.loads(request))
        self.assertIsNone(self.rpc.call(request))
        self.assertListEqual(self.rpc.recorded, [5])
        self.assertEqual(self.client.cancel_pending(), 0)

    def test_error_response(self) -> None:
        request, future = self.client.request("custom_error")
        self.assertEqual(self.round_trip(request), [])
        with self.assertRaises(JsonRpcError) as ctx:
            future.result()
        self.assertEqual(ctx.exception.code, -32000)
        self.assertEqual(ctx.exception.message, "foobar")
        self.assertEqual(ctx.exception.data, {"foo": "bar"})

    def test_error_response_without_data(self) -> None:
        request, future = self.client.request("foobar")
        self.assertEqual(self.round_trip(request), [])
        with self.assertRaises(JsonRpcError) as ctx:
            future.result()
        self.assertEqual(ctx.exception.code, -32601)
        self.assertIsNone(ctx.exception.data)

    def test_null_result(self) -> None:
        # `null` is a valid result. The client must not read it as a missing one.
        _, future = self.client.request("record", 1)
        self.assertEqual(
            self.client.handle('{"jsonrpc": "2.0", "id": 1, "result": null}'), []
        )
        self.assertIsNone(future.result())

    def test_batch(self) -> None:
        batch = self.client.batch()
        total = batch.request("sum", 1, 2, 4)
        batch.notify("record", 7)
        difference = batch.request("subtract", 42, 23)
        missing = batch.request("foobar")
        self.assertEqual(len(batch), 4)  # Notifications count towards the length
        self.assertEqual(self.round_trip(batch.encode()), [])
        self.assertEqual(total.result(), 7)
        self.assertEqual(difference.result(), 19)
        self.assertListEqual(self.rpc.recorded, [7])
        self.assertRaises(JsonRpcError, missing.result)

    def test_batch_response_order_is_irrelevant(self) -> None:
        # The client matches responses on their id, so a server can answer in any order
        batch = self.client.batch()
        first = batch.request("sum", 1)
        second = batch.request("sum", 2)
        batch.encode()
        self.assertEqual(
            self.client.handle(
                '[{"jsonrpc": "2.0", "id": 2, "result": 2},'
                ' {"jsonrpc": "2.0", "id": 1, "result": 1}]'
            ),
            [],
        )
        self.assertEqual((first.result(), second.result()), (1, 2))

    def test_batch_of_notifications(self) -> None:
        batch = self.client.batch()
        batch.notify("record", 1)
        batch.notify("record", 2)
        self.assertIsNone(self.rpc.call(batch.encode()))
        self.assertListEqual(self.rpc.recorded, [1, 2])
        self.assertEqual(self.client.cancel_pending(), 0)

    def test_empty_batch(self) -> None:
        # The spec rejects an empty batch array
        self.assertRaises(ValueError, self.client.batch().encode)

    def test_batch_stays_open_after_encoding(self) -> None:
        batch = self.client.batch()
        first = batch.request("sum", 1)
        self.assertEqual(len(json.loads(batch.encode())), 1)
        # `encode()` does not close the batch. The next payload holds both calls.
        second = batch.request("sum", 2)
        self.assertEqual(self.round_trip(batch.encode()), [])
        self.assertEqual((first.result(), second.result()), (1, 2))

    def test_unattributable_error(self) -> None:
        # A server reports a parse error under a null id, because it never read an
        # id. No pending request can own that error. A guess would fail the wrong
        # call, so `handle()` returns the error to the caller instead.
        _, future = self.client.request("subtract", 42, 23)
        errors = self.client.handle(self.rpc.call("{not json") or b"")
        self.assertEqual([error.code for error in errors], [-32700])
        self.assertFalse(future.done())

    def test_batch_with_unattributable_errors(self) -> None:
        # A server answers every invalid element of a batch under a null id. One
        # payload can therefore hold several such errors together with valid responses.
        _, future = self.client.request("sum", 1)
        invalid = (
            '{"jsonrpc": "2.0", "id": null,'
            ' "error": {"code": -32600, "message": "Invalid Request"}}'
        )
        errors = self.client.handle(
            f'[{invalid}, {{"jsonrpc": "2.0", "id": 1, "result": 1}}, {invalid}]'
        )
        self.assertEqual([error.code for error in errors], [-32600, -32600])
        # Still settled, whatever its neighbors
        self.assertEqual(future.result(), 1)

    def test_unknown_id(self) -> None:
        # A late or duplicated response matches nothing. The client logs it and
        # raises nothing, because it says nothing about the outstanding requests.
        with self.assertLogs(_LOGGER_NAME, "WARNING"):
            self.assertEqual(
                self.client.handle('{"jsonrpc": "2.0", "id": 999, "result": 1}'), []
            )

    def test_unattributable_non_errors(self) -> None:
        # A null id carries a protocol-level failure. Any other content under a null
        # id is nonsense, and there is no future for it either way.
        for label, response in (
            ("result", '{"jsonrpc": "2.0", "id": null, "result": 1}'),
            ("malformed", '{"jsonrpc": "2.0", "id": null, "error": "nope"}'),
        ):
            with self.subTest(response=label), self.assertLogs(_LOGGER_NAME, "WARNING"):
                self.assertEqual(self.client.handle(response), [])

    def test_invalid_response_id(self) -> None:
        # `bool` is a subclass of `int`, but the spec does not allow it as an id. A
        # missing id is not valid either. The client can match neither one against a
        # pending request.
        for raw_id in ("true", "false", '{"invalid": "id"}', "[1]"):
            with self.subTest(id=raw_id), self.assertLogs(_LOGGER_NAME, "WARNING"):
                self.assertEqual(
                    self.client.handle(
                        f'{{"jsonrpc": "2.0", "id": {raw_id}, "result": 1}}'
                    ),
                    [],
                )
        with self.assertLogs(_LOGGER_NAME, "WARNING"):
            self.assertEqual(self.client.handle('{"jsonrpc": "2.0", "result": 1}'), [])

    def test_malformed_response(self) -> None:
        # The `error` member is what separates these cases, so each test names it in
        # full. Without them, one check could still pass for the wrong reason.
        for label, response in (
            ("missing jsonrpc", '{"id": 1, "result": 19}'),
            ("wrong version", '{"jsonrpc": "1.0", "id": 1, "result": 19}'),
            ("neither result nor error", '{"jsonrpc": "2.0", "id": 1}'),
            (
                "both result and error",
                (
                    '{"jsonrpc": "2.0", "id": 1, "result": 19,'
                    ' "error": {"code": -1, "message": "m"}}'
                ),
            ),
            ("error is a string", '{"jsonrpc": "2.0", "id": 1, "error": "nope"}'),
            ("error is an array", '{"jsonrpc": "2.0", "id": 1, "error": [-1, "m"]}'),
            (
                "error without a code",
                '{"jsonrpc": "2.0", "id": 1, "error": {"message": "m"}}',
            ),
            (
                "error without a message",
                '{"jsonrpc": "2.0", "id": 1, "error": {"code": -1}}',
            ),
        ):
            with self.subTest(response=label):
                self.assert_malformed(response)

    def test_unusable_payload(self) -> None:
        # The client can settle nothing in these payloads, so `handle()` raises instead
        for label, response in (
            ("invalid json", '{"jsonrpc": "2.0", "id": 1, "result'),
            ("number", "1"),
            ("string", '"foobar"'),
            ("null", "null"),
            ("empty batch", "[]"),
        ):
            with self.subTest(response=label):
                self.assertRaises(InvalidResponseError, self.client.handle, response)

    def test_junk_inside_a_batch_is_skipped(self) -> None:
        # One unusable element must not cost the others their answers
        _, future = self.client.request("subtract", 42, 23)
        with self.assertLogs(_LOGGER_NAME, "WARNING"):
            self.assertEqual(
                self.client.handle(
                    '[1, {"jsonrpc": "2.0"}, {"jsonrpc": "2.0", "id": 1, "result": 19}]'
                ),
                [],
            )
        self.assertEqual(future.result(), 19)

    def test_raw_response_types(self) -> None:
        response = '{"jsonrpc": "2.0", "id": 1, "result": 19}'
        for raw in (
            response,
            response.encode(),
            bytearray(response.encode()),
            memoryview(response.encode()),
        ):
            with self.subTest(type=type(raw).__name__):
                client = JsonRpcClient()
                _, future = client.request("subtract", 42, 23)
                self.assertEqual(client.handle(raw), [])
                self.assertEqual(future.result(), 19)

    def test_cancelled_request(self) -> None:
        # A caller can abandon a request while it is pending. The client discards an
        # answer that arrives afterward, and does not set it on a future that would
        # refuse it.
        request, future = self.client.request("subtract", 42, 23)
        self.assertTrue(future.cancel())
        self.assertEqual(self.round_trip(request), [])
        self.assertTrue(future.cancelled())
        # `wait()` counts a future in the CANCELLED state as outstanding. `cancel()`
        # must therefore notify the cancellation, not only record it.
        _, not_done = wait([future], timeout=5)
        self.assertFalse(not_done)
        self.assertEqual(self.client.cancel_pending(), 0)  # Removed from the registry

    def test_cancelled_request_never_sent(self) -> None:
        # Nothing answers this request. `cancel()` is the only step that it gets.
        _, future = self.client.request("subtract", 42, 23)
        self.assertTrue(future.cancel())
        _, not_done = wait([future], timeout=5)
        self.assertFalse(not_done)
        self.assertRaises(CancelledError, future.result)

    def test_cancel_twice(self) -> None:
        # The second call notifies nothing. It must not raise the `RuntimeError` that
        # `set_running_or_notify_cancel()` gives for a repeated call.
        _, future = self.client.request("subtract")
        self.assertTrue(future.cancel())
        self.assertTrue(future.cancel())
        _, not_done = wait([future], timeout=5)
        self.assertFalse(not_done)

    def test_cancel_settled_request(self) -> None:
        # A future that holds an answer refuses the cancellation. A plain `Future`
        # does the same. The client must not notify a cancellation that did not happen.
        request, future = self.client.request("subtract", 42, 23)
        self.assertEqual(self.round_trip(request), [])
        self.assertFalse(future.cancel())
        self.assertFalse(future.cancelled())
        self.assertEqual(future.result(), 19)

    def test_cancel_pending(self) -> None:
        _, first = self.client.request("subtract")
        _, second = self.client.request("subtract")
        self.assertEqual(self.client.cancel_pending(), 2)
        self.assertRaises(CancelledError, first.result)
        self.assertRaises(CancelledError, second.result)
        # `wait()` counts a future in the CANCELLED state as outstanding. The client
        # must therefore notify the cancellation, not only record it.
        _, not_done = wait([first, second], timeout=5)
        self.assertFalse(not_done)
        self.assertEqual(self.client.cancel_pending(), 0)

    def test_cancel_pending_with_exception(self) -> None:
        # A cancellation gives no reason. A transport that died can give one.
        _, future = self.client.request("subtract")
        error = ConnectionError("transport is gone")
        self.assertEqual(self.client.cancel_pending(error), 1)
        with self.assertRaises(ConnectionError) as ctx:
            future.result()
        self.assertIs(ctx.exception, error)

    def test_cancel_pending_skips_cancelled_futures(self) -> None:
        # The client cannot fail a future that the caller already canceled
        _, future = self.client.request("subtract")
        self.assertTrue(future.cancel())
        self.assertEqual(self.client.cancel_pending(ConnectionError("gone")), 1)
        self.assertRaises(CancelledError, future.result)
        _, not_done = wait([future], timeout=5)
        self.assertFalse(not_done)

    def test_dumps_kwargs(self) -> None:
        client = JsonRpcClient(
            dumps_kwargs={"default": lambda _: "encoded by the default hook"}
        )
        request, _ = client.request("subtract", object())
        self.assert_encodes(
            request,
            {
                "jsonrpc": "2.0",
                "method": "subtract",
                "params": ["encoded by the default hook"],
                "id": 1,
            },
        )

    def test_id_iterator(self) -> None:
        # A server may accept only one type of id, so the source of the ids is
        # replaceable
        client = JsonRpcClient(id_iterator=iter(["a", "b"]))
        request, future = client.request("subtract", 42, 23)
        self.assertEqual(json.loads(request)["id"], "a")
        self.assertEqual(
            client.handle('{"jsonrpc": "2.0", "id": "a", "result": 19}'), []
        )
        self.assertEqual(future.result(), 19)

    def test_duplicate_id(self) -> None:
        # An id iterator that repeats an id would orphan the future that already
        # waits under that id. That failure would hang instead of raising, so the
        # client refuses it.
        client = JsonRpcClient(id_iterator=iter([7, 7]))
        client.request("subtract")
        self.assertRaises(ValueError, client.request, "subtract")

    def test_response_from_another_thread(self) -> None:
        # This is why `request()` returns a future: the thread that reads the
        # transport does not have to be the thread that made the call
        request, future = self.client.request("subtract", 42, 23)
        thread = threading.Thread(target=self.round_trip, args=(request,))
        thread.start()
        self.assertEqual(future.result(timeout=5), 19)
        thread.join()

    def test_concurrent_requests(self) -> None:
        # The client assigns and records ids under one lock, so nothing is lost or
        # answered twice when several threads call at once
        results: dict[int, float] = {}

        def call(n: int) -> None:
            request, future = self.client.request("sum", n, n)
            self.round_trip(request)
            results[n] = future.result(timeout=5)

        threads = [threading.Thread(target=call, args=(n,)) for n in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results, {n: n * 2 for n in range(20)})
        self.assertEqual(self.client.cancel_pending(), 0)
