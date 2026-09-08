"""This example module details the different ways of adding rpc methods."""

import asyncio
import math

from pyjsonrpc2.server import JsonRpcServer, rpc_method


# The server registers the methods marked with @rpc_method when you create an instance.
# @rpc_method can also give the rpc method a different name. This is a new name, not an
# alias.
class MathServer(JsonRpcServer):
    @rpc_method
    def square(self, x):
        return x**2

    @rpc_method(name="cube")
    def calculate_cube(self, x):
        return x**3

    def not_added(self):  # The server does not register this method.
        return "foo"


# You can also pass a mapping of names to functions
server = MathServer({"get_version": lambda: "1.0"})


# add_object() works in the same way as the subclass: it adds the methods marked with
# @rpc_method of the instance that you give to it.
class MathUtils:
    @staticmethod
    @rpc_method  # The order of the decorators is important here.
    def multiply(a, b):
        return a * b

    @rpc_method(name="divide")
    def division(self, a, b):
        if b == 0:
            raise ValueError("Cannot divide by zero")
        return a / b

    def not_added(self):  # The server does not register this method.
        return "foo"


server.add_object(MathUtils())


# Use add_method() to add one method at a time.
# Method #1
@server.add_method
def add(a, b):
    return a + b


# Method #2
@rpc_method(name="subtract")
def sub(a, b):
    return a - b


server.add_method(sub)


# Method #3
def natural_logarithm(x):
    return math.log(x)


server.add_method(natural_logarithm, name="ln")
server.add_method(lambda a, b: a % b, name="modulo")


# JsonRpcServer raises a ValueError if you add a method with a name that already exists.
try:
    server.add_method(lambda x: x**2, name="square")
except ValueError as e:
    print(e)


# Every registration path refuses a callable that the server cannot use. The server
# calls a method and then encodes what it returns, so these four kinds fail every call.
async def fetch(x):
    return x


async def stream(x):
    yield x


def numbers(x):
    yield x


for name, method in (
    ("not_callable", 42),
    ("fetch", fetch),
    ("stream", stream),
    ("numbers", numbers),
):
    try:
        server.add_method(method, name=name)
    except ValueError as e:
        print(e)
# Output: Cannot register objects that are not callable: 'not_callable'
# Output: Cannot register coroutine functions: 'fetch'
# Output: Cannot register async generator functions: 'stream'
# Output: Cannot register generator functions: 'numbers'

# Wrap the coroutine in a synchronous callable, and register that one instead.
server.add_method(lambda x: asyncio.run(fetch(x)), name="fetch")
print(server.call('{"jsonrpc": "2.0", "method": "fetch", "params": [1], "id": 9}'))
# Output: b'{"jsonrpc":"2.0","id":9,"result":1}'
server.remove_method("fetch")

# A class and a builtin are callable and return a value that the encoder handles, so
# the server accepts both.
server.add_method(sorted, name="sorted")
print(
    server.call('{"jsonrpc": "2.0", "method": "sorted", "params": [[3, 1]], "id": 10}')
)
# Output: b'{"jsonrpc":"2.0","id":10,"result":[1,3]}'
server.remove_method("sorted")


# The `methods` property lists the registry, from rpc method name to callable.
print(sorted(server.methods))
# Output: ['add', 'cube', 'divide', 'get_version', 'ln', 'modulo', 'multiply', 'square', 'subtract']

# The property gives a read-only view. Register through add_method() and add_object().
try:
    server.methods["square"] = lambda x: x**2
except TypeError as e:
    print(e)  # Output: 'mappingproxy' object does not support item assignment


# remove_method() takes one name back out of the registry, and returns the callable
# that the name held. The name is free again afterwards.
removed = server.remove_method("modulo")
print(removed(7, 3))  # Output: 1
print("modulo" in server.methods)  # Output: False
server.add_method(removed, name="mod")

# A view that you read before a write does not follow that write.
before = server.methods
server.remove_method("ln")
print("ln" in before, "ln" in server.methods)  # Output: True False

# remove_method() raises a KeyError for a name that the registry does not hold.
try:
    server.remove_method("ln")
except KeyError as e:
    print(e)  # Output: "Method 'ln' is not registered"

# add_object() puts a prefix before every name that it registers. remove_method() takes
# the registry name, so the name that you give to it holds that prefix too.
server.add_object(MathUtils(), prefix="utils.")
print(sorted(name for name in server.methods if name.startswith("utils.")))
# Output: ['utils.divide', 'utils.multiply']
server.remove_method("utils.divide")


# A few example calls
result = server.call('{"jsonrpc": "2.0", "method": "add", "params": [5, 3], "id": 1}')
print(result)  # Output: b'{"jsonrpc":"2.0","id":1,"result":8}'
result = server.call(
    b'{"jsonrpc": "2.0", "method": "subtract", "params": [5, 3], "id": 2}'
)
print(result)  # Output: b'{"jsonrpc":"2.0","id":2,"result":2}'
result = server.call(
    '{"jsonrpc": "2.0", "method": "multiply", "params": [5, 3], "id": 3}'
)
print(result)  # Output: b'{"jsonrpc":"2.0","id":3,"result":15}'
