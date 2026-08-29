"""This example module details how error handling works in pyjsonrpc2."""

from pyjsonrpc2.server import JsonRpcError, JsonRpcServer, rpc_method


class AdvancedMathServer(JsonRpcServer):
    @rpc_method
    def divide(self, a, b):
        if b == 0:
            # Raise JsonRpcError to send a custom implementation-defined server
            # error. Give it arguments of JSON serializable types only.
            raise JsonRpcError(
                code=-32000,
                message="Division by zero",
                data={"numerator": a, "denominator": b},
            )
        return a / b

    @rpc_method
    def factorial(self, n):
        if not isinstance(n, int):
            # The server catches and logs any exception that is not a JsonRpcError.
            # It then answers with an Internal error response. The "data" field of
            # that response holds the string form of the exception.
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
# Output: b'{"jsonrpc":"2.0","id":5,"result":5.0}'

print(server.call('{"jsonrpc": "2.0", "method": "divide", "params": [10, 0], "id": 6}'))
# Output: b'{"jsonrpc":"2.0","id":6,"error":{"code":-32000,"message":"Division by zero","data":{"numerator":10,"denominator":0}}}'

print(server.call('{"jsonrpc": "2.0", "method": "factorial", "params": [5], "id": 7}'))
# Output: b'{"jsonrpc":"2.0","id":7,"result":120}'

print(server.call('{"jsonrpc": "2.0", "method": "factorial", "params": [-3], "id": 8}'))
# Output: b'{"jsonrpc":"2.0","id":8,"error":{"code":-32001,"message":"Invalid input for factorial","data":{"input":-3,"reason":"Must be non-negative"}}}'

print(
    server.call('{"jsonrpc": "2.0", "method": "factorial", "params": ["foo"], "id": 9}')
)  # The server logs the TypeError
# Output: b'{"jsonrpc":"2.0","id":9,"error":{"code":-32603,"message":"Internal error","data":"n must be an integer"}}'
