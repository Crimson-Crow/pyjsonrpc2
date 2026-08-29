"""This example module details the different ways of adding rpc methods."""

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
