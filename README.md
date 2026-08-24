# pyjsonrpc2

[![PyPI](https://img.shields.io/pypi/v/pyjsonrpc2)](https://pypi.org/project/pyjsonrpc2/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/pyjsonrpc2)](https://pypi.org/project/pyjsonrpc2/)
[![Tests](https://img.shields.io/github/actions/workflow/status/Crimson-Crow/pyjsonrpc2/tests.yml?branch=main&label=tests)](https://github.com/Crimson-Crow/pyjsonrpc2/actions/workflows/tests.yml?query=branch%3Amain)
[![GitHub](https://img.shields.io/github/license/Crimson-Crow/pyjsonrpc2)](https://github.com/Crimson-Crow/pyjsonrpc2/blob/main/LICENSE.txt)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://docs.github.com/en/pull-requests/how-tos/create-pull-requests/creating-a-pull-request)

A correct, transport-agnostic Python implementation of the JSON-RPC 2.0 protocol (currently server-side only).

## Key features
- Full compliance with the [JSON-RPC 2.0 specification](https://www.jsonrpc.org/specification), batch requests and notifications included
- Transport-agnostic: hand `call()` a raw request, send back the raw bytes it returns
- Accepts `str`, `bytes`, `bytearray` and `memoryview` input
- Multiple method registration patterns (constructor mapping, class-based, individual methods, lambda, etc.)
- Automatic & custom error handling capabilities
- Complete type hints (passes `pyrefly` on the `strict` preset)
- Extensive unit tests (full coverage)
- [Semantic versioning](https://semver.org/) adherence

## Installation

`pyjsonrpc2` requires Python 3.11 or later.

To install the package, use [pip](https://pip.pypa.io/en/stable/):

```bash
pip install pyjsonrpc2
```

## Usage

For more info, check the [examples/](examples/) directory.

### Basic Server Creation

```python
from pyjsonrpc2.server import JsonRpcServer, rpc_method, JsonRpcError

# Create a basic server
server = JsonRpcServer()
```

### Method Registration Patterns

These are the main patterns for registering RPC methods. [examples/registering_methods.py](examples/registering_methods.py) contains a few more.
1. Passing a mapping of names to callables to the constructor:
```python
server = JsonRpcServer({"get_version": lambda: "1.0"})
```

2. Class-based approach with decorators:
```python
class MathServer(JsonRpcServer):
    @rpc_method
    def square(self, x):
        return x**2

    @rpc_method(name="cube")
    def calculate_cube(self, x):
        return x**3


server = MathServer()
```

3. Registering the decorated methods of any other object, optionally under a `prefix` which keeps two objects exposing the same method names apart:
```python
class MathUtils:
    @rpc_method
    def multiply(self, a, b):
        return a * b


server.add_object(MathUtils(), prefix="utils.")  # Registers "utils.multiply"
```

4. Adding individual methods using decorators:
```python
@server.add_method
def add(a, b):
    return a + b
```

5. Adding methods with custom names:
```python
def sub(a, b):
    return a - b


server.add_method(sub, name="subtract")
```

6. Adding lambda functions:
```python
server.add_method(lambda a, b: a % b, name="modulo")
```

### Error Handling
Error handling features:
- Custom error codes for implementation-defined & application-defined errors through the `JsonRpcError` class
- Automatic conversion of Python exceptions to Internal error (`-32603`) responses
- Automatic detection of argument mismatches, reported as Invalid params (`-32602`)
- Support for additional error data in a structured format
- Built-in handling of protocol-level errors (invalid JSON, missing required fields, etc.)
- Error logging for debugging purposes, on the `pyjsonrpc2.server` logger, at the `ERROR` level and with a traceback

1. Custom Implementation-Defined Errors:
```python
class AdvancedMathServer(JsonRpcServer):
    @rpc_method
    def divide(self, a, b):
        if b == 0:
            raise JsonRpcError(
                code=-32000,
                message="Division by zero",
                data={"numerator": a, "denominator": b},
            )
        return a / b
```

2. Multiple Error Conditions:
```python
class AdvancedMathServer(JsonRpcServer):
    @rpc_method
    def factorial(self, n):
        if not isinstance(n, int):
            # Regular exceptions are caught and converted to Internal error responses
            raise TypeError("n must be an integer")

        if n < 0:
            # Custom JSON-RPC errors with additional data
            raise JsonRpcError(
                code=-32001,
                message="Invalid input for factorial",
                data={"input": n, "reason": "Must be non-negative"},
            )
        # ... implementation ...
```

The `data` passed to `JsonRpcError` must be JSON serializable. A return value which is not is caught as well, and answered with an Internal error carrying the serialization failure as its `data`.

### Request execution

`call()` returns the encoded response as `bytes`, or `None` when the client is owed no answer, i.e. for a single notification or for a batch holding only notifications.

```python
server.call('{"jsonrpc": "2.0", "method": "add", "params": [5, 3], "id": 1}')
# b'{"jsonrpc":"2.0","id":1,"result":8}'

server.call(b'{"jsonrpc": "2.0", "method": "subtract", "params": [5, 3], "id": 2}')
# b'{"jsonrpc":"2.0","id":2,"result":2}'

# A notification (no "id"): nothing is owed to the client
server.call('{"jsonrpc": "2.0", "method": "add", "params": [5, 3]}')
# None

# A batch is answered with an array of the responses its elements are owed
server.call(
    '[{"jsonrpc": "2.0", "method": "add", "params": [1, 2], "id": 3},'
    ' {"jsonrpc": "2.0", "method": "modulo", "params": [7, 3], "id": 4}]'
)
# b'[{"jsonrpc":"2.0","id":3,"result":3},{"jsonrpc":"2.0","id":4,"result":1}]'
```

Extra keyword arguments for `orjson.dumps()` can be supplied through `dumps_kwargs`.

```python
import orjson

server = JsonRpcServer(dumps_kwargs={"option": orjson.OPT_INDENT_2})
```

## Tests

The simplest way to run tests is:

```bash
python -m unittest
```

As a more robust alternative, you can install [`tox`](https://tox.wiki) to automatically test across the supported python versions, then run:

```bash
tox -p
```

## Issue tracker

Please report any bugs or enhancement ideas using the [issue tracker](https://github.com/Crimson-Crow/pyjsonrpc2/issues).

## License

`pyjsonrpc2` is licensed under the terms of the [MIT License](https://github.com/Crimson-Crow/pyjsonrpc2/blob/main/LICENSE.txt).
