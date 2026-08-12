from __future__ import annotations

import json
import unittest
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


class JsonRpcServerTest(unittest.TestCase):
    rpc: Handler

    @classmethod
    def setUpClass(cls) -> None:
        cls.rpc = Handler(methods={"multiply": lambda a, b: a * b})  # pragma: no cover

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
    ) -> None:
        raw_response = self.rpc.call(request)
        self.assertIsNotNone(raw_response)
        # https://github.com/python/mypy/issues/5088
        response = json.loads(raw_response)  # type: ignore[arg-type]

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

    def test_invalid_request(self) -> None:
        invalid_requests = [
            '{"method": "test"}',
            '{"jsonrpc": "2.0", "params": [1, 2, 3]}',
            '{"jsonrpc": "1.0", "method": "test"}',
            '{"jsonrpc": "2.0", "method": 123}',
            '{"jsonrpc": "2.0", "method": "test", "params": "invalid params"}',
            '{"jsonrpc": "2.0", "method": "test", "id": {"invalid": "id"}}',
            '{"jsonrpc": "2.0", "method": "test", "extra_field": "not allowed"}',
        ]
        for request in invalid_requests:
            self.rpc_call(
                request,
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32600, "message": "Invalid Request"},
                    "id": None,
                },
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
