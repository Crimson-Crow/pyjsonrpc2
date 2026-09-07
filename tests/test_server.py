from __future__ import annotations

import json
import unittest
from types import MappingProxyType
from typing import Any, NoReturn

from pyjsonrpc2.server import JsonRpcError, JsonRpcServer, rpc_method

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


class UnhashableMethod:  # noqa: PLW1641
    """A callable that cannot be a dict key.

    A class that defines `__eq__` without `__hash__` gets a `__hash__` of `None`.
    """

    def __eq__(self, other: object) -> bool:
        return self is other

    def __call__(self, a: float) -> float:  # pragma: no cover
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
        try:  # noqa: SIM105
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
