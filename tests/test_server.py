from __future__ import annotations

import gc
import json
import re
import threading
import unittest
import weakref
from functools import partial
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, NoReturn, cast

from pyjsonrpc2.server import JsonRpcError, JsonRpcServer, rpc_method

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

_LOGGER_NAME = "pyjsonrpc2.server"


class Handler(JsonRpcServer):
    to_update: Any = None
    data: list[str | int] = ["hello", 5]  # noqa: RUF012

    @rpc_method
    def custom_error(self) -> NoReturn:
        raise JsonRpcError(code=-32000, message="foobar", data={"foo": "bar"})

    @rpc_method
    def custom_error_without_data(self) -> NoReturn:
        raise JsonRpcError(code=-32001, message="barbaz")

    @rpc_method
    def update(self, a: Any, b: Any, c: Any, d: Any) -> None:
        self.to_update = [a, b, c, d]

    @rpc_method(name="get_data")
    def foo(self) -> list[str | int]:
        return self.data

    @staticmethod
    def get_data() -> NoReturn:  # pragma: no cover
        msg = "Never"
        raise RuntimeError(msg)

    @staticmethod
    @rpc_method
    def subtract(minuend: float = 0, subtrahend: float = 0) -> float:
        return minuend - subtrahend

    @staticmethod
    @rpc_method(name="sum")
    def add(*args: float) -> float:
        return sum(args)

    @rpc_method
    def raises_typeerror(self) -> NoReturn:
        msg = "What did you expect?"
        raise TypeError(msg)

    @rpc_method
    def returns_unencodable(self) -> object:
        return object()


async def coroutine_method() -> int:
    """A coroutine function, which the synchronous server must refuse to register."""
    return 1


def generator_method() -> Iterator[int]:
    """A generator function. It returns a generator, which the encoder refuses."""
    yield 1


async def async_generator_method() -> AsyncIterator[int]:
    """An async generator function. It returns an object of the same kind."""
    yield 1


class UnhashableMethod:  # noqa: PLW1641
    """A callable that cannot be a dict key.

    A class that defines `__eq__` without `__hash__` gets a `__hash__` of `None`.
    """

    def __eq__(self, other: object) -> bool:
        return self is other

    def __call__(self, a: float) -> float:  # pragma: no cover
        return a


class UnreferenceableMethod:
    """A callable that cannot be a weak key.

    A class with empty `__slots__` and no `__weakref__` slot takes no weak reference.
    """

    __slots__ = ()

    def __call__(self, a: float) -> float:
        return a


class JsonRpcServerTest(unittest.TestCase):
    rpc: Handler

    @classmethod
    def setUpClass(cls) -> None:
        def multiply(a: float, b: float) -> float:
            return a * b

        cls.rpc = Handler(methods={"multiply": multiply})

    @staticmethod
    def remove_data(response: dict[str, Any]) -> None:
        try:
            response["error"].pop("data")
        except KeyError:
            pass

    def rpc_call(
        self,
        request: str,
        expected_response: list[dict[str, Any]] | dict[str, Any],
        *,
        remove_data: bool = True,
        rpc: JsonRpcServer | None = None,
    ) -> None:
        raw_response = (self.rpc if rpc is None else rpc).call(request)
        if raw_response is None:
            self.fail("Expected a response, got None")
        response = json.loads(raw_response)

        if remove_data:
            if isinstance(response, list):
                for r in response:
                    self.remove_data(r)
            else:
                self.remove_data(response)

        self.assertEqual(response, expected_response)

    def test_name_collision(self) -> None:
        self.assertRaises(ValueError, self.rpc.add_method, None, name="sum")

    def test_decorator(self) -> None:
        self.assertRaises(AttributeError, rpc_method, 1)

    def test_error_display(self) -> None:
        # `__str__` builds the display string, not `__init__`, because the RPC path
        # only serializes `to_dict()`. `args` therefore holds the three fields, not
        # the formatted message.
        error = JsonRpcError(code=-32000, message="foobar", data={"foo": "bar"})
        self.assertEqual(str(error), "[-32000] foobar: {'foo': 'bar'}")
        self.assertEqual(error.args, (-32000, "foobar", {"foo": "bar"}))
        # `__str__` renders `data` with `!r`, so a string stays different from the
        # other values that could give the same `str()`.
        self.assertEqual(
            str(JsonRpcError(code=-32002, message="qux", data="boom")),
            "[-32002] qux: 'boom'",
        )
        # `__str__` leaves out `data` when it is `None`
        self.assertEqual(
            str(JsonRpcError(code=-32001, message="barbaz")), "[-32001] barbaz"
        )

    def test_custom_error(self) -> None:
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "custom_error", "id": 1}',
            {
                "jsonrpc": "2.0",
                "error": {"code": -32000, "message": "foobar", "data": {"foo": "bar"}},
                "id": 1,
            },
            remove_data=False,
        )

    def test_custom_error_without_data(self) -> None:
        # `data` is optional. The response must leave it out completely when unset.
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "custom_error_without_data", "id": 1}',
            {
                "jsonrpc": "2.0",
                "error": {"code": -32001, "message": "barbaz"},
                "id": 1,
            },
            remove_data=False,
        )

    def test_positional_parameters(self) -> None:
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "subtract", "params": [42, 23], "id": 1}',
            {"jsonrpc": "2.0", "result": 19, "id": 1},
        )
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "subtract", "params": [23, 42], "id": 2}',
            {"jsonrpc": "2.0", "result": -19, "id": 2},
        )

    def test_named_parameters(self) -> None:
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "subtract", "params": {"subtrahend": 23, "minuend": 42}, "id": 3}',
            {"jsonrpc": "2.0", "result": 19, "id": 3},
        )
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "subtract", "params": {"minuend": 42, "subtrahend": 23}, "id": 4}',
            {"jsonrpc": "2.0", "result": 19, "id": 4},
        )

    def test_notification(self) -> None:
        # Method exists
        self.assertIsNone(
            self.rpc.call(
                '{"jsonrpc": "2.0", "method": "update", "params": [1,2,3,4]}',
            ),
        )
        self.assertListEqual(self.rpc.to_update, [1, 2, 3, 4])
        # Method does not exist
        self.assertIsNone(self.rpc.call('{"jsonrpc": "2.0", "method": "foobar"}'))

    def test_notification_raises(self) -> None:
        # A notification that fails is still silent, but the server must log it
        with self.assertLogs(_LOGGER_NAME, "ERROR"):
            self.assertIsNone(
                self.rpc.call('{"jsonrpc": "2.0", "method": "raises_typeerror"}'),
            )

    def test_non_existent_method(self) -> None:
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "foobar", "id": "1"}',
            {
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": "Method not found"},
                "id": "1",
            },
        )

    def test_invalid_json(self) -> None:
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "foobar, "params": "bar", "baz]',
            {
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": "Parse error"},
                "id": None,
            },
        )

    def assert_invalid_request(self, request: str, data: str) -> None:
        # The `data` field is what separates the rejection reasons, so this method
        # asserts it instead of removing it. Without it, every case below could still
        # pass after it failed for the wrong reason.
        self.rpc_call(
            request,
            {
                "jsonrpc": "2.0",
                "error": {"code": -32600, "message": "Invalid Request", "data": data},
                "id": None,
            },
            remove_data=False,
        )

    def test_invalid_request(self) -> None:
        invalid_requests = [
            ('{"method": "test"}', "Missing 'jsonrpc' key"),
            ('{"jsonrpc": "2.0", "params": [1, 2, 3]}', "Missing 'method' key"),
            ('{"jsonrpc": "1.0", "method": "test"}', "Wrong rpc version (got '1.0')"),
            # A number appears without quotes. `!s` would have rendered it exactly
            # like the string above.
            ('{"jsonrpc": 1.0, "method": "test"}', "Wrong rpc version (got 1.0)"),
            (
                '{"jsonrpc": "2.0", "method": 123}',
                "'method' must be a string (type: <class 'int'>)",
            ),
            (
                '{"jsonrpc": "2.0", "method": "test", "params": "invalid params"}',
                "'params' must be an array or an object (type: <class 'str'>)",
            ),
            (
                '{"jsonrpc": "2.0", "method": "test", "params": null}',
                "'params' must be an array or an object (type: <class 'NoneType'>)",
            ),
        ]
        for request, data in invalid_requests:
            with self.subTest(request=request):
                self.assert_invalid_request(request, data)

    def test_non_object_request(self) -> None:
        # A top-level value that is neither an object nor an array
        for request, type_name in (
            ("1", "int"),
            ('"foobar"', "str"),
            ("true", "bool"),
            ("null", "NoneType"),
        ):
            with self.subTest(request=request):
                self.assert_invalid_request(
                    request, f"Not an object (type: <class '{type_name}'>)"
                )

    def test_invalid_id(self) -> None:
        # `bool` is a subclass of `int`, but the spec does not allow it as an id
        for raw_id, type_name in (
            ("true", "bool"),
            ("false", "bool"),
            ('{"invalid": "id"}', "dict"),
            ("[1]", "list"),
        ):
            with self.subTest(id=raw_id):
                self.assert_invalid_request(
                    f'{{"jsonrpc": "2.0", "method": "subtract", "id": {raw_id}}}',
                    f"'id' must be a number, string or null "
                    f"(type: <class '{type_name}'>)",
                )

    def test_extra_members(self) -> None:
        # The spec does not forbid undefined members and gives them no meaning, so
        # the server must ignore them instead of rejecting them
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "subtract", "params": [42, 23], "id": 1, "extra": "ignored"}',
            {"jsonrpc": "2.0", "result": 19, "id": 1},
        )
        # An undefined member does not turn a notification into a request
        self.assertIsNone(
            self.rpc.call(
                '{"jsonrpc": "2.0", "method": "subtract", "params": [42, 23], "extra": "ignored"}',
            ),
        )

    def test_fractional_id(self) -> None:
        # Clients SHOULD NOT send fractional ids, but the server does not enforce
        # that rule. It must return the same id that it received.
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "subtract", "params": [42, 23], "id": 1.5}',
            {"jsonrpc": "2.0", "result": 19, "id": 1.5},
        )

    def test_null_id(self) -> None:
        # `null` is a valid id. The server must not read it as a notification.
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "subtract", "params": [42, 23], "id": null}',
            {"jsonrpc": "2.0", "result": 19, "id": None},
        )

    def test_falsy_id(self) -> None:
        for raw_id, expected_id in (("0", 0), ('""', "")):
            with self.subTest(id=raw_id):
                self.rpc_call(
                    f'{{"jsonrpc": "2.0", "method": "subtract", "params": [42, 23], "id": {raw_id}}}',
                    {"jsonrpc": "2.0", "result": 19, "id": expected_id},
                )

    def test_null_result(self) -> None:
        # A successful call must carry a "result" member even when it is null
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "update", "params": [1, 2, 3, 4], "id": 1}',
            {"jsonrpc": "2.0", "result": None, "id": 1},
        )

    def test_batch_invalid_json(self) -> None:
        self.rpc_call(
            """[
              {"jsonrpc": "2.0", "method": "sum", "params": [1,2,4], "id": "1"},
              {"jsonrpc": "2.0", "method"
            ]""",
            {
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": "Parse error"},
                "id": None,
            },
        )

    def test_empty_array(self) -> None:
        self.rpc_call(
            "[]",
            {
                "jsonrpc": "2.0",
                "error": {"code": -32600, "message": "Invalid Request"},
                "id": None,
            },
        )

    def test_non_empty_invalid_batch(self) -> None:
        self.rpc_call(
            "[1]",
            [
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32600, "message": "Invalid Request"},
                    "id": None,
                },
            ],
        )
        self.rpc_call(
            "[1,2,3]",
            [
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32600, "message": "Invalid Request"},
                    "id": None,
                },
            ]
            * 3,
        )

    def test_batch(self) -> None:
        self.rpc_call(
            """[
                {"jsonrpc": "2.0", "method": "sum", "params": [1,2,4], "id": "1"},
                {"jsonrpc": "2.0", "method": "notify_hello", "params": [7]},
                {"jsonrpc": "2.0", "method": "subtract", "params": [42,23], "id": "2"},
                {"foo": "boo"},
                {"jsonrpc": "2.0", "method": "foo.get", "params": {"name": "myself"}, "id": "5"},
                {"jsonrpc": "2.0", "method": "get_data", "id": "9"}
            ]""",
            [
                {"jsonrpc": "2.0", "result": 7, "id": "1"},
                {"jsonrpc": "2.0", "result": 19, "id": "2"},
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32600, "message": "Invalid Request"},
                    "id": None,
                },
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32601, "message": "Method not found"},
                    "id": "5",
                },
                {"jsonrpc": "2.0", "result": ["hello", 5], "id": "9"},
            ],
        )

    def test_batch_all_notifications(self) -> None:
        self.assertIsNone(
            self.rpc.call(
                """[
                    {"jsonrpc": "2.0", "method": "notify_sum", "params": [1,2,4]},
                    {"jsonrpc": "2.0", "method": "notify_hello", "params": [7]}
                ]""",
            ),
        )

    def test_json_encode_error(self) -> None:
        with self.assertLogs(_LOGGER_NAME, "ERROR"):
            self.rpc_call(
                '{"jsonrpc": "2.0", "method": "returns_unencodable", "id": 1}',
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32603, "message": "Internal error"},
                    "id": 1,
                },
            )
        with self.assertLogs(_LOGGER_NAME, "ERROR"):
            self.rpc_call(
                '[{"jsonrpc": "2.0", "method": "returns_unencodable", "id": 1}]',
                [
                    {
                        "jsonrpc": "2.0",
                        "error": {"code": -32603, "message": "Internal error"},
                        "id": 1,
                    }
                ],
            )

    def test_method_raises_exception(self) -> None:
        with self.assertLogs(_LOGGER_NAME, "ERROR"):
            self.rpc_call(
                '{"jsonrpc": "2.0", "method": "raises_typeerror", "id": 1}',
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32603, "message": "Internal error"},
                    "id": 1,
                },
            )
        with self.assertLogs(_LOGGER_NAME, "ERROR"):
            self.rpc_call(
                '[{"jsonrpc": "2.0", "method": "raises_typeerror", "id": 2}]',
                [
                    {
                        "jsonrpc": "2.0",
                        "error": {"code": -32603, "message": "Internal error"},
                        "id": 2,
                    },
                ],
            )

    def test_invalid_params(self) -> None:
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "update", "id": 1}',
            {
                "jsonrpc": "2.0",
                "error": {"code": -32602, "message": "Invalid params"},
                "id": 1,
            },
        )
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "raises_typeerror", "params": [1], "id": 1}',
            {
                "jsonrpc": "2.0",
                "error": {"code": -32602, "message": "Invalid params"},
                "id": 1,
            },
        )

    def test_unhashable_method(self) -> None:
        # `_signature` memoizes on the callable itself, so a callable that cannot
        # be a dict key needs an uncached lookup. If the `TypeError` escaped instead,
        # the second call would still report invalid params, but only by accident. A
        # real `TypeError` from inside such a method would get the same wrong label.
        rpc = JsonRpcServer(methods={"unhashable": UnhashableMethod()})
        for _ in range(2):  # Twice, because the cache must not keep a miss either
            self.rpc_call(
                '{"jsonrpc": "2.0", "method": "unhashable", "id": 1}',
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32602, "message": "Invalid params"},
                    "id": 1,
                },
                rpc=rpc,
            )

    def test_raw_request_types(self) -> None:
        request = (
            '{"jsonrpc": "2.0", "method": "subtract", "params": [42, 23], "id": 1}'
        )
        expected = b'{"jsonrpc":"2.0","id":1,"result":19}'
        for raw in (
            request,
            request.encode(),
            bytearray(request.encode()),
            memoryview(request.encode()),
        ):
            with self.subTest(type=type(raw).__name__):
                self.assertEqual(self.rpc.call(raw), expected)

    def test_constructor_methods(self) -> None:
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "multiply", "params": [6, 7], "id": 1}',
            {"jsonrpc": "2.0", "result": 42, "id": 1},
        )

    def test_constructor_methods_are_copied(self) -> None:
        def multiply(a: float, b: float) -> float:
            return a * b

        methods = {"multiply": multiply}
        rpc = Handler(methods=methods)
        # The mapping receives neither the marked methods of the subclass, which
        # `__init__` registers, nor any later registration.
        rpc.add_method(multiply, name="times")
        self.assertEqual({"multiply": multiply}, methods)
        # The server also keeps its copy after the caller clears the mapping.
        methods.clear()
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "multiply", "params": [6, 7], "id": 1}',
            {"jsonrpc": "2.0", "result": 42, "id": 1},
            rpc=rpc,
        )

    def test_constructor_accepts_any_mapping(self) -> None:
        def multiply(a: float, b: float) -> float:
            return a * b

        rpc = JsonRpcServer(MappingProxyType({"multiply": multiply}))
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "multiply", "params": [6, 7], "id": 1}',
            {"jsonrpc": "2.0", "result": 42, "id": 1},
            rpc=rpc,
        )

    def test_dumps_kwargs(self) -> None:
        rpc = JsonRpcServer(
            {"unencodable": object},
            dumps_kwargs={"default": lambda _: "encoded by the default hook"},
        )
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "unencodable", "id": 1}',
            {"jsonrpc": "2.0", "result": "encoded by the default hook", "id": 1},
            rpc=rpc,
        )

    def test_add_object_prefix(self) -> None:
        rpc = JsonRpcServer()
        rpc.add_object(Handler(), prefix="handler.")
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "handler.get_data", "id": 1}',
            {"jsonrpc": "2.0", "result": ["hello", 5], "id": 1},
            rpc=rpc,
        )
        # The server does not register the name without the prefix
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "get_data", "id": 2}',
            {
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": "Method not found"},
                "id": 2,
            },
            rpc=rpc,
        )

    def test_add_object_duplicate_name_in_one_object(self) -> None:
        # Two pairs of marked methods resolve to the same registry name. `add_object`
        # reports both names, and it registers nothing.
        class Twice:
            @rpc_method(name="same")
            def first(self) -> None: ...

            @rpc_method(name="same")
            def second(self) -> None: ...

            @rpc_method(name="other")
            def third(self) -> None: ...

            @rpc_method(name="other")
            def fourth(self) -> None: ...

        rpc = JsonRpcServer()
        with self.assertRaisesRegex(
            ValueError, "Duplicate method names: 'other', 'same'"
        ):
            rpc.add_object(Twice())
        # The failed scan registered none of the four methods
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "same", "id": 1}',
            {
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": "Method not found"},
                "id": 1,
            },
            rpc=rpc,
        )

    def test_add_object_duplicate_name_in_registry(self) -> None:
        # The scan finds eight names that the registry already holds. `add_object`
        # sorts them, so the message does not depend on the scan order.
        rpc = JsonRpcServer()
        rpc.add_object(Handler())
        with self.assertRaisesRegex(
            ValueError,
            "Methods already registered: 'custom_error', "
            "'custom_error_without_data', 'get_data', 'raises_typeerror', "
            "'returns_unencodable', 'subtract', 'sum', 'update'",
        ):
            rpc.add_object(Handler())
        # A prefix separates the two objects, so the second scan then succeeds
        rpc.add_object(Handler(), prefix="second.")
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "second.get_data", "id": 1}',
            {"jsonrpc": "2.0", "result": ["hello", 5], "id": 1},
            rpc=rpc,
        )

    def test_add_method_returns_method(self) -> None:
        rpc = JsonRpcServer()

        @rpc.add_method
        def ping() -> str:
            return "pong"

        self.assertEqual("pong", ping())  # The decorator returned the function
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "ping", "id": 1}',
            {"jsonrpc": "2.0", "result": "pong", "id": 1},
            rpc=rpc,
        )

    def test_add_method_default_name(self) -> None:
        def ping() -> str:
            return "pong"

        @rpc_method(name="renamed")
        def decorated() -> str:
            return "pong"

        rpc = JsonRpcServer()
        rpc.add_method(ping)  # Uses __name__
        rpc.add_method(decorated)  # Uses __rpc__
        for name in ("ping", "renamed"):
            with self.subTest(method=name):
                self.rpc_call(
                    f'{{"jsonrpc": "2.0", "method": "{name}", "id": 1}}',
                    {"jsonrpc": "2.0", "result": "pong", "id": 1},
                    rpc=rpc,
                )

    def test_methods_lists_the_registry(self) -> None:
        def ping() -> str:
            return "pong"

        rpc = JsonRpcServer({"multiply": ping})
        rpc.add_method(ping, name="ping")
        self.assertEqual(sorted(rpc.methods), ["multiply", "ping"])
        self.assertIs(rpc.methods["ping"], ping)

    def test_methods_is_read_only(self) -> None:
        def ping() -> str:
            return "pong"

        rpc = JsonRpcServer()

        def write() -> None:
            cast("dict[str, Any]", rpc.methods)["ping"] = ping

        # A caller must register through `add_method`, so that the name check runs
        self.assertRaises(TypeError, write)

    def test_methods_view_ignores_later_writes(self) -> None:
        # The registry is copy-on-write, so a view holds the state that it was read in
        def ping() -> str:
            return "pong"

        rpc = JsonRpcServer()
        empty = rpc.methods
        rpc.add_method(ping)
        with_ping = rpc.methods
        rpc.remove_method("ping")
        self.assertEqual(sorted(empty), [])
        self.assertEqual(sorted(with_ping), ["ping"])
        self.assertEqual(sorted(rpc.methods), [])

    def test_remove_method(self) -> None:
        rpc = Handler()
        self.assertIn("subtract", rpc.methods)
        self.assertIs(rpc.remove_method("subtract"), Handler.subtract)
        self.assertNotIn("subtract", rpc.methods)
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "subtract", "params": [42, 23], "id": 1}',
            {
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": "Method not found"},
                "id": 1,
            },
            rpc=rpc,
        )
        # The name is free again, so the server accepts it a second time
        rpc.add_method(Handler.subtract, name="subtract")
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "subtract", "params": [42, 23], "id": 2}',
            {"jsonrpc": "2.0", "result": 19, "id": 2},
            rpc=rpc,
        )

    def test_remove_method_with_a_prefix(self) -> None:
        # `remove_method` takes the registry name, which holds the prefix
        rpc = JsonRpcServer()
        handler = Handler()
        rpc.add_object(handler, prefix="handler.")
        # A bound method is built at every attribute read, so compare with `==`
        self.assertEqual(rpc.remove_method("handler.get_data"), handler.foo)
        self.assertNotIn("handler.get_data", rpc.methods)
        # The other names of that object stay registered
        self.assertIn("handler.sum", rpc.methods)
        self.assertRaises(KeyError, rpc.remove_method, "get_data")

    def test_remove_unknown_method(self) -> None:
        rpc = JsonRpcServer()
        self.assertRaisesRegex(
            KeyError,
            re.escape("Method 'ping' is not registered"),
            rpc.remove_method,
            "ping",
        )

    def test_remove_method_drops_the_memoized_signature(self) -> None:
        # An invalid-params answer memoizes the signature of the method. `remove_method`
        # drops that entry, so the server answers the same way after a second add.
        rpc = Handler()
        invalid = (
            '{"jsonrpc": "2.0", "method": "subtract", "params": [1, 2, 3], "id": 1}'
        )
        expected = {
            "jsonrpc": "2.0",
            "error": {"code": -32602, "message": "Invalid params"},
            "id": 1,
        }
        self.rpc_call(invalid, expected, rpc=rpc)  # Memoizes the signature
        rpc.add_method(rpc.remove_method("subtract"), name="subtract")
        self.rpc_call(invalid, expected, rpc=rpc)

    def test_remove_unhashable_method(self) -> None:
        # An unhashable callable never enters the memo, so the drop finds nothing
        rpc = JsonRpcServer()
        method = UnhashableMethod()
        rpc.add_method(method, name="unhashable")
        self.assertIs(rpc.remove_method("unhashable"), method)
        self.assertNotIn("unhashable", rpc.methods)

    def test_concurrent_registration_of_one_name(self) -> None:
        # Ten threads race to register the same name. `add_method` checks the name and
        # writes it under one lock, so exactly one thread wins and nine get a
        # ValueError. Without the lock, several could pass the check together.
        rpc = JsonRpcServer()
        barrier = threading.Barrier(10)
        won: list[int] = []
        lost: list[int] = []

        def register(n: int) -> None:
            def method() -> int:
                return n

            barrier.wait(timeout=5)
            try:
                rpc.add_method(method, name="contested")
            except ValueError:
                lost.append(n)
            else:
                won.append(n)

        threads = [threading.Thread(target=register, args=(n,)) for n in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(len(won), 1)
        self.assertEqual(len(lost), 9)
        self.assertEqual(sorted(rpc.methods), ["contested"])

    def test_concurrent_calls_and_registry_writes(self) -> None:
        # Four threads rewrite the registry while four others call it. A reader must
        # see the registry before a write or after it, and never a partial state.
        rpc = JsonRpcServer()
        rpc.add_method(lambda: "pong", name="ping")
        request = '{"jsonrpc": "2.0", "method": "ping", "id": 1}'
        expected = b'{"jsonrpc":"2.0","id":1,"result":"pong"}'
        failures: list[str] = []
        barrier = threading.Barrier(8)

        def churn(n: int) -> None:
            barrier.wait(timeout=5)
            for i in range(50):
                name = f"m{n}.{i}"
                rpc.add_method(lambda: None, name=name)
                # Reading the whole view while another thread writes must not raise
                if "ping" not in dict(rpc.methods):
                    failures.append(f"lost 'ping' during {name}")
                rpc.remove_method(name)

        def call(_: int) -> None:
            barrier.wait(timeout=5)
            answers = {rpc.call(request) for _i in range(200)}
            if answers != {expected}:
                failures.append(f"wrong answer to 'ping': {answers!r}")

        threads = [threading.Thread(target=churn, args=(n,)) for n in range(4)]
        threads += [threading.Thread(target=call, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(failures, [])
        self.assertEqual(sorted(rpc.methods), ["ping"])

    def test_unreferenceable_method(self) -> None:
        # The memo holds weak keys, so a callable that takes no weak reference cannot
        # enter it. The server must answer from an uncached signature instead of
        # letting the TypeError reach the invalid-params handler.
        rpc = JsonRpcServer()
        rpc.add_method(UnreferenceableMethod(), name="unreferenceable")
        for id_ in (1, 2):  # Twice, because the first call cannot warm the memo
            with self.subTest(call=id_):
                self.rpc_call(
                    f'{{"jsonrpc": "2.0", "method": "unreferenceable",'
                    f' "params": [1, 2, 3], "id": {id_}}}',
                    {
                        "jsonrpc": "2.0",
                        "error": {"code": -32602, "message": "Invalid params"},
                        "id": id_,
                    },
                    rpc=rpc,
                )
        self.assertIs(
            rpc.remove_method("unreferenceable").__class__, UnreferenceableMethod
        )

    def test_memo_does_not_outlive_a_removed_method(self) -> None:
        # A method removed while a call to it is in flight is memoized after the
        # removal, so `remove_method` cannot drop that entry. Weak keys are what stops
        # the entry from pinning the callable, and the object that it is bound to.
        class Handler:
            @rpc_method
            def boom(self, _a: float) -> NoReturn:
                started.set()
                stop.wait(timeout=5)  # Hold the call open across the removal
                msg = "from the body"
                raise TypeError(msg)

        started = threading.Event()
        stop = threading.Event()
        rpc = JsonRpcServer()
        handler = Handler()
        rpc.add_object(handler)
        request = '{"jsonrpc": "2.0", "method": "boom", "params": [1], "id": 1}'
        thread = threading.Thread(target=rpc.call, args=(request,))
        with self.assertLogs(_LOGGER_NAME, "ERROR"):
            thread.start()
            self.assertTrue(started.wait(timeout=5))
            rpc.remove_method("boom")  # The memo holds nothing for it yet
            stop.set()
            thread.join(timeout=5)
        # The call memoized the signature after the removal. Dropping the last strong
        # reference must still release the handler.
        dead = weakref.ref(handler)
        del handler
        gc.collect()
        self.assertIsNone(dead())

    def test_add_method_rejects_unusable_callables(self) -> None:
        # The server calls a method and then encodes what the method returns. These
        # kinds fail at every call, so `add_method` refuses them at registration.
        rpc = JsonRpcServer()
        for label, method, kind in (
            ("not callable", 42, "objects that are not callable"),
            ("coroutine function", coroutine_method, "coroutine functions"),
            (
                "async generator function",
                async_generator_method,
                "async generator functions",
            ),
            ("generator function", generator_method, "generator functions"),
            # The `inspect` predicates read through `functools.partial`
            (
                "partial of a coroutine function",
                partial(coroutine_method),
                "coroutine functions",
            ),
            (
                "partial of a generator function",
                partial(generator_method),
                "generator functions",
            ),
        ):
            with self.subTest(method=label):
                self.assertRaisesRegex(
                    ValueError,
                    re.escape(f"Cannot register {kind}: 'unusable'"),
                    rpc.add_method,
                    method,
                    name="unusable",
                )
        self.assertEqual(sorted(rpc.methods), [])

    def test_registration_accepts_every_other_callable(self) -> None:
        # The check must refuse nothing that the server can use. A class returns an
        # instance, and a callable instance answers like any function.
        rpc = JsonRpcServer({"dict": dict, "len": len})
        rpc.add_method(UnhashableMethod(), name="instance")
        rpc.add_method(lambda: [1], name="lambda")
        self.assertEqual(sorted(rpc.methods), ["dict", "instance", "lambda", "len"])
        self.rpc_call(
            '{"jsonrpc": "2.0", "method": "len", "params": [[1, 2]], "id": 1}',
            {"jsonrpc": "2.0", "result": 2, "id": 1},
            rpc=rpc,
        )

    def test_constructor_rejects_a_non_callable(self) -> None:
        # Without the check the server answers a call to that name with -32602 Invalid
        # params, which blames the parameters of the request for a fault in the
        # registration. The error names every offending key, sorted.
        self.assertRaisesRegex(
            ValueError,
            re.escape("Cannot register objects that are not callable: 'a', 'z'"),
            JsonRpcServer,
            {"z": 1, "a": 2, "ok": len},
        )

    def test_constructor_rejects_coroutine_functions(self) -> None:
        # The mapping given to the constructor is a registration path of its own, so it
        # gets the same check.
        self.assertRaisesRegex(
            ValueError,
            re.escape("Cannot register coroutine functions: 'first', 'second'"),
            JsonRpcServer,
            {"first": coroutine_method, "second": coroutine_method, "third": len},
        )

    def test_subclass_rejects_a_coroutine_function(self) -> None:
        # `__init__` calls `add_object(self)`, so a marked coroutine method of a
        # subclass fails at construction rather than at the first call.
        class AsyncHandler(JsonRpcServer):
            @rpc_method
            async def fetch(self) -> int:
                return 1

            @rpc_method(name="renamed")
            async def other(self) -> int:
                return 2

        self.assertRaisesRegex(
            ValueError,
            re.escape("Cannot register coroutine functions: 'fetch', 'renamed'"),
            AsyncHandler,
        )

    def test_add_object_rejects_a_routine_that_is_not_callable(self) -> None:
        # `inspect.isroutine` is not a subset of `callable`, so the scan of
        # `add_object` does not make the callable test redundant.
        # `inspect.ismethoddescriptor` accepts any type that has `__get__` and has
        # neither `__set__` nor `__delete__`. It asks nothing about `__call__`. An
        # instance attribute holds such an object without the descriptor protocol
        # running, so the scan finds the object itself. A `classmethod` object reaches
        # the same place the same way.
        class Descriptor:
            __rpc__: str | None = None

            def __get__(self, obj: object, objtype: type | None = None) -> int:
                return 42

        class Holder:
            def __init__(self) -> None:
                self.descriptor = Descriptor()

        rpc = JsonRpcServer()
        self.assertRaisesRegex(
            ValueError,
            re.escape("Cannot register objects that are not callable: 'descriptor'"),
            rpc.add_object,
            Holder(),
        )
        self.assertEqual(sorted(rpc.methods), [])

    def test_add_object_rejects_unusable_methods(self) -> None:
        # `add_object` reports every offending name at once and registers nothing, the
        # same way that it answers a duplicate name.
        class Mixed:
            @rpc_method
            def first(self) -> Iterator[int]:
                yield 1

            @rpc_method(name="second")
            def second(self) -> Iterator[int]:
                yield 2

            @rpc_method
            def ping(self) -> str:
                return "pong"

        rpc = JsonRpcServer()
        self.assertRaisesRegex(
            ValueError,
            re.escape("Cannot register generator functions: 'obj.first', 'obj.second'"),
            rpc.add_object,
            Mixed(),
            prefix="obj.",
        )
        # The failed scan registered none of the three methods
        self.assertEqual(sorted(rpc.methods), [])
