# pyjsonrpc2

[![PyPI](https://img.shields.io/pypi/v/pyjsonrpc2)](https://pypi.org/project/pyjsonrpc2/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/pyjsonrpc2)](https://pypi.org/project/pyjsonrpc2/)
[![GitHub](https://img.shields.io/github/license/Crimson-Crow/pyjsonrpc2)](https://github.com/Crimson-Crow/pyjsonrpc2/blob/main/LICENSE.txt)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://makeapullrequest.com)

A flexible Python implementation of the JSON-RPC 2.0 protocol (currently server-side only).

## Key features
- Full compliance with the [JSON-RPC 2.0 specification](https://www.jsonrpc.org/specification)
- No transport functionality
- Multiple method registration patterns (class-based, individual methods, lambda, etc.)
- Automatic & custom error handling capabilities
- Support for both string and bytes input
- Complete type hints (passes `mypy --strict`)
- Extensive unit tests (full coverage)
- [Semantic versioning](https://semver.org/) adherence

## Installation

To install the package, use [pip](https://pip.pypa.io/en/stable/):

```bash
pip install pyjsonrpc2
```

## Usage

For more info, check the `/examples` directory.

### Basic Server Creation

```python
from pyjsonrpc2.server import JsonRpcServer, rpc_method, JsonRpcError

# Create a basic server
server = JsonRpcServer()
```

### Method Registration Patterns

These are the main patterns for registering RPC methods. `/examples/registering_methods.py` contains a few more.
1. Class-based approach with decorators:
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

2. Adding individual methods using decorators:
```python
@server.add_method
def add(a, b):
    return a + b
```

3. Adding methods with custom names:
```python
def sub(a, b):
    return a - b


server.add_method(sub, name="substract")
```

4. Adding lambda functions:
```python
server.add_method(lambda a, b: a % b, name="modulo")
```

### Error Handling
Error handling features:
- Custom error codes for implementation-defined & application-defined errors through the `JsonRpcError` class
- Automatic conversion of Python exceptions to JSON-RPC Internal error responses
- Support for additional error data in a structured format
- Built-in handling of protocol-level errors (invalid JSON, missing required fields, etc.)
- Error logging for debugging purposes

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

### Request execution
```python
result = server.call('{"jsonrpc": "2.0", "method": "add", "params": [5, 3], "id": 1}')
result = server.call(
    b'{"jsonrpc": "2.0", "method": "subtract", "params": [5, 3], "id": 2}'
)
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
