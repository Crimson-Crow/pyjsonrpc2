"""This example module details how error handling works in pyjsonrpc2."""

# mypy: ignore-errors
from pyjsonrpc2.server import JsonRpcError, JsonRpcServer, rpc_method


class AdvancedMathServer(JsonRpcServer):
    @rpc_method
    def divide(self, a, b):
        if b == 0:
            # JsonRpcError can be used to send custom implementation-defined server-errors.
            # Ensure that the provided arguments are json serializable types.
            raise JsonRpcError(
                code=-32000,
                message="Division by zero",
                data={"numerator": a, "denominator": b},
            )
        return a / b

    @rpc_method
    def factorial(self, n):
        if not isinstance(n, int):
            # Any exception (other than a JsonRpcError) raised during a rpc call will be caught and logged.
            # Then, an Internal error response will be returned, with the "data" field containing a string representation of the caught exception.
            raise TypeError("n must be an integer")
        if n < 0:
            raise JsonRpcError(
                code=-32001,
                message="Invalid input for factorial",
                data={"input": n, "reason": "Must be non-negative"},
            )
        result = 1
        for i in range(1, n + 1):
            result *= i
        return result


server = AdvancedMathServer()


# Usage examples
print(server.call('{"jsonrpc": "2.0", "method": "divide", "params": [10, 2], "id": 5}'))
# Output: {"jsonrpc": "2.0", "result": 5.0, "id": 5}

print(server.call('{"jsonrpc": "2.0", "method": "divide", "params": [10, 0], "id": 6}'))
# Output: {"jsonrpc": "2.0", "error": {"code": -32000, "message": "Division by zero", "data": {"numerator": 10, "denominator": 0}}, "id": 6}

print(server.call('{"jsonrpc": "2.0", "method": "factorial", "params": [5], "id": 7}'))
# Output: {"jsonrpc": "2.0", "result": 120, "id": 7}

print(server.call('{"jsonrpc": "2.0", "method": "factorial", "params": [-3], "id": 8}'))
# Output: {"jsonrpc": "2.0", "error": {"code": -32001, "message": "Invalid input for factorial", "data": {"input": -3, "reason": "Must be a non-negative integer"}}, "id": 8}

print(
    server.call('{"jsonrpc": "2.0", "method": "factorial", "params": ["foo"], "id": 9}')
)  # TypeError will be logged
# Output: {"jsonrpc": "2.0", "error": {"code": -32603, "message": "Internal error", "data": "n must be an integer"}, "id": 9}
