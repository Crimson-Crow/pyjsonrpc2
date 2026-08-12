"""This example module details the different ways of adding rpc methods."""

# mypy: ignore-errors
import math

from pyjsonrpc2.server import JsonRpcServer, rpc_method


# Methods marked with the @rpc_method decorator will automatically be registered when creating an instance.
# @rpc_method also allows setting a different name internally for the rpc method (not an alias).
class MathServer(JsonRpcServer):
    @rpc_method
    def square(self, x):
        return x**2

    @rpc_method(name="cube")
    def calculate_cube(self, x):
        return x**3

    def not_added(self):  # Will not be registered as an rpc method.
        return "foo"


# Is it also possible to pass a mapping of strings to functions
server = MathServer({"get_version": lambda: "1.0"})


# Similarly to the subclassing usage, @rpc_method marked methods of a class will be added to the server when an instance is passed to add_object().
class MathUtils:
    @staticmethod
    @rpc_method  # Order of decorators is important here.
    def multiply(a, b):
        return a * b

    @rpc_method(name="divide")
    def division(self, a, b):
        if b == 0:
            raise ValueError("Cannot divide by zero")
        return a / b

    def not_added(self):  # Will not be registered as a rpc method.
        return "foo"


server.add_object(MathUtils())


# Adding individual methods can be done through add_method().
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


# JsonRpcServer throws a ValueError when attempting to add a method with an already existing name.
try:
    server.add_method(lambda x: x**2, name="square")
except ValueError as e:
    print(e)


# A few example calls
result = server.call('{"jsonrpc": "2.0", "method": "add", "params": [5, 3], "id": 1}')
print(result)  # Output: {"jsonrpc": "2.0", "result": 8, "id": 1}
result = server.call(
    b'{"jsonrpc": "2.0", "method": "subtract", "params": [5, 3], "id": 2}'
)
print(result)  # Output: {"jsonrpc": "2.0", "result": 2, "id": 2}
result = server.call(
    '{"jsonrpc": "2.0", "method": "multiply", "params": [5, 3], "id": 3}'
)
print(result)  # Output: {"jsonrpc": "2.0", "result": 15, "id": 3}
