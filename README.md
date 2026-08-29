# pyjsonrpc2

[![PyPI](https://img.shields.io/pypi/v/pyjsonrpc2)](https://pypi.org/project/pyjsonrpc2/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/pyjsonrpc2)](https://pypi.org/project/pyjsonrpc2/)
[![Tests](https://img.shields.io/github/actions/workflow/status/Crimson-Crow/pyjsonrpc2/tests.yml?branch=main&label=tests)](https://github.com/Crimson-Crow/pyjsonrpc2/actions/workflows/tests.yml?query=branch%3Amain)
[![GitHub](https://img.shields.io/github/license/Crimson-Crow/pyjsonrpc2)](https://github.com/Crimson-Crow/pyjsonrpc2/blob/main/LICENSE.txt)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://docs.github.com/en/pull-requests/how-tos/create-pull-requests/creating-a-pull-request)

A correct, transport-agnostic Python implementation of the JSON-RPC 2.0 protocol.

## Key features
- Both halves of the protocol: `JsonRpcServer` and `JsonRpcClient`
- Full compliance with the [JSON-RPC 2.0 specification](https://www.jsonrpc.org/specification), batch requests and notifications included
- Transport-agnostic: hand `call()` a raw request and send back the raw bytes it returns; the client hands you the raw bytes of a request and a `concurrent.futures.Future` to read its answer from
- Responses are matched to requests by `id`, so a transport may answer out of order, from another thread, or not at all
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

The two halves never meet. `JsonRpcServer` turns the raw bytes of a request into the raw bytes of a response; `JsonRpcClient` turns a method call into the raw bytes of a request plus the `Future` its answer will arrive in. Neither performs any I/O of its own — carrying the bytes between them is your job.

For more info, check the [examples/](examples/) directory.

## Server

### Basic Server Creation

```python
from pyjsonrpc2.server import JsonRpcServer, rpc_method, JsonRpcError

# Create a basic server
server = JsonRpcServer()
```

### Method Registration Patterns

These are the main patterns for registering RPC methods. [examples/server/registering_methods.py](examples/server/registering_methods.py) contains a few more.
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

## Client

### Making calls

`request()` returns a pair: the bytes to send, and the `Future` its answer will be delivered to. Nothing is settled until a response is fed back to `handle()`, which matches it to its request by `id`.

```python
from pyjsonrpc2.client import JsonRpcClient

client = JsonRpcClient()

request, future = client.request("subtract", 42, 23)
# request: b'{"jsonrpc":"2.0","method":"subtract","params":[42,23],"id":1}'

transport.send(request)  # a socket, an HTTP request, a pipe... whatever you use
client.handle(transport.recv())
future.result()  # 19
```

Parameters are either positional or named, exactly as in the protocol: `*args` becomes the `"params"` array, `**kwargs` becomes the `"params"` object, and asking for both raises `ValueError`. The method name is positional-only, so a parameter that happens to be called `method` still lands in `"params"`.

```python
client.request("subtract", minuend=42, subtrahend=23)
client.request("subtract", 42, subtrahend=23)  # ValueError
```

### Notifications

A notification carries no `id`, so the server owes nothing back and there is no future to hand out — not even a failure can be reported against it.

```python
client.notify("log", "hello")
# b'{"jsonrpc":"2.0","method":"log","params":["hello"]}'
```

### Batch requests

A batch accumulates calls and encodes them as one payload. Requests hand back their own future, notifications hand back nothing, and one payload back settles all of them at once.

```python
batch = client.batch()
total = batch.request("sum", 1, 2, 4)
batch.notify("log", "part of the batch")
difference = batch.request("subtract", 42, 23)

transport.send(batch.encode())
client.handle(transport.recv())

total.result()  # 7
difference.result()  # 19
```

Encoding does not close a batch: more calls can be added and the batch encoded again. An empty one is refused with `ValueError`, since the specification has no answer for it.

### Handling responses

`handle()` accepts single responses and batches, as JSON text or its UTF-8 encoding, in any order and from any thread. Its return value is the errors it could **not** attribute to a pending request — usually empty:

```python
unattributed = client.handle(response)
```

A server that cannot parse what it was sent has no `id` to answer under, so it replies with a null one. Nothing can be matched against that, and guessing would fail the wrong call, so those errors are handed back to the caller instead. Responses that match nothing else — a late answer, a duplicate, an `id` never sent — are logged on the `pyjsonrpc2.client` logger at the `WARNING` level.

### Error handling

- An `"error"` response raises `JsonRpcError` out of `Future.result()`, carrying the server's `code`, `message` and `data`. It is the same class the server raises, so the two sides never have to translate anything
- A response that is not a JSON-RPC response — wrong version, both `"result"` and `"error"`, neither of them — fails the future it matches with `InvalidResponseError`, so whoever waits on that call is the one who hears about it
- A payload that is unusable as a whole — not valid JSON, not an object or an array, an empty array — is raised out of `handle()` itself, and nothing in it is settled

```python
from pyjsonrpc2.client import InvalidResponseError, JsonRpcError

try:
    future.result()
except JsonRpcError as e:
    print(e.code, e.message, e.data)
except InvalidResponseError as e:
    print("the server answered with something that is not JSON-RPC:", e)
```

A request counts as outstanding from the moment it is built, whether or not it ever reaches a transport. When a transport dies there is nobody left to answer the calls in flight, so release them rather than leaving their futures pending forever:

```python
client.cancel_pending(ConnectionError("the socket went away"))  # returns how many
client.cancel_pending()  # cancels them instead: result() raises CancelledError
```

### Client configuration

Both arguments are keyword-only. `dumps_kwargs` works as it does on the server, and `id_iterator` replaces the source of request ids (`itertools.count(1)` by default) for servers that are particular about their type. It must yield values that are unique for the lifetime of the client and never `None`.

```python
import itertools

client = JsonRpcClient(id_iterator=(f"call-{n}" for n in itertools.count()))
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
