"""This example module details the different ways of calling a remote method."""

from pyjsonrpc2.client import JsonRpcClient
from pyjsonrpc2.server import JsonRpcServer, rpc_method


# The client never touches a transport of its own. request() returns the bytes to send
# and a Future that receives the answer. handle() takes the bytes that come back. Every
# example in this directory uses a server called directly in the same process as its
# transport, so the requests and responses below are the real ones.
class MathServer(JsonRpcServer):
    @rpc_method
    def subtract(self, minuend, subtrahend):
        return minuend - subtrahend

    @rpc_method
    def log(self, message):
        logged.append(message)


logged = []
server = MathServer()
client = JsonRpcClient()


def transport(request):
    """Take the place of a socket, an HTTP request, a pipe, or whatever you use."""
    return server.call(request)


# A request is a pair: the bytes to send, and the future that receives its answer.
request, future = client.request("subtract", 42, 23)
print(request)
# Output: b'{"jsonrpc":"2.0","method":"subtract","params":[42,23],"id":1}'

# The client settles nothing until a response comes back, however long that takes.
print(future.done())  # Output: False
client.handle(transport(request))
print(future.result())  # Output: 19


# Parameters are positional or named, exactly as the protocol allows.
request, future = client.request("subtract", minuend=42, subtrahend=23)
print(request)
# Output: b'{"jsonrpc":"2.0","method":"subtract","params":{"minuend":42,"subtrahend":23},"id":2}'
client.handle(transport(request))
print(future.result())  # Output: 19


# The client leaves out "params" completely when a call has no parameters.
request, future = client.request("subtract")
print(request)
# Output: b'{"jsonrpc":"2.0","method":"subtract","id":3}'


# A JSON-RPC request has nowhere to put both kinds of parameters at once.
try:
    client.request("subtract", 42, subtrahend=23)
except ValueError as e:
    print(e)
# Output: 'params' is either an array or an object, so parameters cannot be both positional and keyword


# The method name is positional-only. A parameter that is also called "method"
# therefore goes into "params" and does not collide with it.
print(client.notify("log", method="not a collision"))
# Output: b'{"jsonrpc":"2.0","method":"log","params":{"method":"not a collision"}}'


# A notification carries no id, so the server owes no answer and there is no future.
# The client cannot report even a failure against it.
notification = client.notify("log", "hello")
print(notification)
# Output: b'{"jsonrpc":"2.0","method":"log","params":["hello"]}'
print(transport(notification))  # Output: None
# Only the notification that went through the transport arrived. To build is not to
# send.
print(logged)  # Output: ['hello']


# A request is outstanding from the moment you build it, even if it never reaches a
# transport. The request built above and never sent still waits, and so does every
# request that a dead transport lost. cancel_pending() releases them all at once.
print(client.cancel_pending())  # Output: 1
print(future.cancelled())  # Output: True
