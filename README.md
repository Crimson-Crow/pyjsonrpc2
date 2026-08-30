# pyjsonrpc2

[![PyPI](https://img.shields.io/pypi/v/pyjsonrpc2)](https://pypi.org/project/pyjsonrpc2/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/pyjsonrpc2)](https://pypi.org/project/pyjsonrpc2/)
[![Tests](https://img.shields.io/github/actions/workflow/status/Crimson-Crow/pyjsonrpc2/tests.yml?branch=main&label=tests)](https://github.com/Crimson-Crow/pyjsonrpc2/actions/workflows/tests.yml?query=branch%3Amain)
[![GitHub](https://img.shields.io/github/license/Crimson-Crow/pyjsonrpc2)](https://github.com/Crimson-Crow/pyjsonrpc2/blob/main/LICENSE.txt)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://docs.github.com/en/pull-requests/how-tos/create-pull-requests/creating-a-pull-request)

A correct, transport-agnostic Python implementation of the JSON-RPC 2.0 protocol.

## Key features
- Fully complies with the [JSON-RPC 2.0 specification](https://www.jsonrpc.org/specification)
- Works over any transport: the library does no I/O
  - the server takes a raw request and returns the raw bytes of the response
  - the client returns the raw bytes of a request and a `concurrent.futures.Future` that receives the answer
- Works from several threads: the client is thread safe, and the server is thread safe when your registered methods are also thread safe
- Accepts JSON input as `str`, `bytes`, `bytearray` or `memoryview`
- Declares complete type hints (passes `pyrefly` on the `strict` preset)
- Covers every line with unit tests
- Follows [semantic versioning](https://semver.org/)

## Installation

`pyjsonrpc2` requires Python 3.11 or later.

Use [pip](https://pip.pypa.io/en/stable/) to install the package:

```bash
pip install pyjsonrpc2
```

## Usage

`JsonRpcServer` turns the raw bytes of a request into the raw bytes of a response. `JsonRpcClient` turns a method call into the raw bytes of a request and a `Future` that receives the answer. Neither half does any I/O.

The [examples/](examples/) directory has more examples.

### Server

#### Basic server creation

```python
from pyjsonrpc2.server import JsonRpcServer, rpc_method, JsonRpcError

# Create a basic server
server = JsonRpcServer()
```

#### Method registration patterns

These are the main patterns to register RPC methods. [examples/server/registering_methods.py](examples/server/registering_methods.py) shows a few more.
1. Give a mapping of names to callables to the constructor:
```python
server = JsonRpcServer({"get_version": lambda: "1.0"})
```

2. Decorate the methods of a subclass:
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

3. Register the decorated methods of another object. An optional `prefix` keeps two objects that have the same method names separate:
```python
class MathUtils:
    @rpc_method
    def multiply(self, a, b):
        return a * b


server.add_object(MathUtils(), prefix="utils.")  # Registers "utils.multiply"
```

4. Add one method with a decorator:
```python
@server.add_method
def add(a, b):
    return a + b
```

5. Add a method under a different name:
```python
def sub(a, b):
    return a - b


server.add_method(sub, name="subtract")
```

6. Add a lambda function:
```python
server.add_method(lambda a, b: a % b, name="modulo")
```

#### Error handling
The server handles errors as follows:
- `JsonRpcError` carries a custom code for an implementation-defined or an application-defined error
- Any other Python exception becomes an Internal error (`-32603`) response
- An argument mismatch becomes an Invalid params (`-32602`) response
- An error can carry additional data in any JSON structure
- The server answers a protocol error itself, such as invalid JSON or a missing key
- The server logs an uncaught exception on the `pyjsonrpc2.server` logger, at the `ERROR` level and with a traceback

1. Raise a custom implementation-defined error:
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

2. Use more than one error condition:
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

The `data` that you give to `JsonRpcError` must be JSON serializable. A return value must also be JSON serializable. The server catches a return value that is not, and answers with an Internal error. That error carries the serialization failure as its `data`.

#### Request execution

`call()` returns the encoded response as `bytes`. It returns `None` when the server owes the client no answer. There are two such cases: a single notification, and a batch that holds only notifications.

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

`dumps_kwargs` gives extra keyword arguments to `orjson.dumps()`.

```python
import orjson

server = JsonRpcServer(dumps_kwargs={"option": orjson.OPT_INDENT_2})
```

### Client

#### Making calls

`request()` returns two things: the bytes to send, and the `Future` that receives the answer. The future stays pending until you give the response to `handle()`. `handle()` matches the response to its request by `id`.

```python
from pyjsonrpc2.client import JsonRpcClient

client = JsonRpcClient()

request, future = client.request("subtract", 42, 23)
# request: b'{"jsonrpc":"2.0","method":"subtract","params":[42,23],"id":1}'

transport.send(request)  # a socket, an HTTP request, a pipe, or anything else
client.handle(transport.recv())
future.result()  # 19
```

Parameters are positional or named, exactly as in the protocol. `*args` becomes the `"params"` array. `**kwargs` becomes the `"params"` object. A call that gives both raises `ValueError`. The method name is positional-only, so a parameter with the name `method` also goes into `"params"`. A method name that is not a string raises `TypeError`.

```python
client.request("subtract", minuend=42, subtrahend=23)
client.request("subtract", 42, subtrahend=23)  # ValueError
client.request(42)  # TypeError
```

#### Notifications

A notification carries no `id`. The server owes no answer, and the client gives you no future. The client can match nothing against a notification, not even a failure.

```python
client.notify("log", "hello")
# b'{"jsonrpc":"2.0","method":"log","params":["hello"]}'
```

#### Batch requests

A batch collects calls and encodes them as one payload. `request()` returns a future for its own element. `notify()` returns nothing. One response payload settles every future in the batch.

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

`encode()` does not close a batch. You can add more calls and encode the batch again. `encode()` refuses an empty batch with `ValueError`, because the specification has no answer for one.

#### Handling responses

`handle()` accepts a single response or a batch, as JSON text or as its UTF-8 encoding. It accepts them in any order and from any thread. It returns the errors that the server sent under a null `id`, which belong to no request. This list is usually empty:

```python
unattributed = client.handle(response)
```

A server that cannot parse a request has no `id` to answer under, so it answers with a null `id`. The client can match nothing against a null `id`. A guess would fail the wrong call, so `handle()` returns these errors to you instead.

The client logs a response that matches nothing else, then drops it. It uses the `pyjsonrpc2.client` logger at the `WARNING` level. Three examples are a late answer, a duplicate, and an `id` that you never sent.

#### Error handling

- An `"error"` response raises `JsonRpcError` out of `Future.result()`. That error carries the server's `code`, `message` and `data`. The server raises the same class, so neither half must translate anything.
- A response that is not a JSON-RPC response fails the future that it matches, with `InvalidResponseError`. The caller of that one request is the one who hears about it. A response is malformed when:
  - the `"jsonrpc"` version is wrong
  - it has both a `"result"` and an `"error"`
  - it has neither of them
- `handle()` raises `InvalidResponseError` itself when the payload as a whole is unusable. It settles nothing in that case. A payload is unusable when:
  - it is not valid JSON
  - it is not an object and not an array
  - it is an empty array
- A response that belongs to no request and is not a well-formed error settles no future. `handle()` raises nothing for it and does not return it. The client logs it and drops it. This group holds:
  - a response with no `id`
  - a null `id` that carries a `"result"`
  - a null `id` that carries an unusable `"error"`
  - a batch element that is not an object

```python
from pyjsonrpc2.client import InvalidResponseError, JsonRpcError

try:
    future.result()
except JsonRpcError as e:
    print(e.code, e.message, e.data)
except InvalidResponseError as e:
    print("the server answered with something that is not JSON-RPC:", e)
```

A request is pending from the moment that the client builds it. This is true even if the request never reaches a transport. When a transport stops, nothing can answer the requests that are still pending. Release them, or their futures stay pending forever:

```python
client.cancel_pending(ConnectionError("socket closed"))  # returns how many were pending
client.cancel_pending()  # cancels them instead. result() raises CancelledError
```

#### Client configuration

Both arguments are keyword-only. `dumps_kwargs` works as it does on the server. `id_iterator` replaces the source of request ids, which is `itertools.count(1)` by default. Use it for a server that is particular about the type of an id. The iterator must give JSON serializable values that are unique for the life of the client, and never `None`. The client refuses an id that already awaits a response, and raises `ValueError`. It does not lose the future that waits under that id.

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
