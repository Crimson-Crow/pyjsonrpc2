"""This example module details how the client reports the many ways a call can fail."""

import json

from pyjsonrpc2.client import InvalidResponseError, JsonRpcClient, JsonRpcError
from pyjsonrpc2.server import JsonRpcServer, rpc_method


class MathServer(JsonRpcServer):
    @rpc_method
    def divide(self, a, b):
        if b == 0:
            raise JsonRpcError(
                code=-32000,
                message="Division by zero",
                data={"numerator": a, "denominator": b},
            )
        return a / b


server = MathServer()
client = JsonRpcClient()


def transport(request):
    return server.call(request)


# A failure arrives the same way a result does, through the future. Whatever the server
# put in the "error" member comes out of result() as a JsonRpcError.
request, future = client.request("divide", 10, 0)
client.handle(transport(request))
try:
    future.result()
except JsonRpcError as e:
    print(e)  # Output: [-32000] Division by zero: {'numerator': 10, 'denominator': 0}
    print(e.code, e.message, e.data)
    # Output: -32000 Division by zero {'numerator': 10, 'denominator': 0}


# The protocol's own errors arrive as ordinary error responses too.
request, future = client.request("does_not_exist")
client.handle(transport(request))
try:
    future.result()
except JsonRpcError as e:
    print(e)  # Output: [-32601] Method not found


# Some failures belong to no request at all. A server that cannot parse the payload has
# no id to answer under, so it answers with a null id. The client can match nothing
# against a null id, and a guess would fail the wrong call. handle() therefore returns
# those errors to the caller instead of raising them.
request, future = client.request("divide", 10, 2)
unattributed = client.handle(transport(b"{ not json"))
print([(e.code, e.message) for e in unattributed])  # `e.data` holds the parser message
# Output: [(-32700, 'Parse error')]

# The call above is still outstanding, because it may never have reached the server.
# The transport decides what to do about that.
print(future.done())  # Output: False
print(client.cancel_pending())  # Output: 1


# A response that is not a JSON-RPC response fails the future that it matches. Only the
# caller who waits on that call hears about it. The response below carries both a result
# and an error, and the specification allows exactly one of the two.
request, future = client.request("divide", 10, 2)
request_id = json.loads(request)["id"]
client.handle(
    f'{{"jsonrpc": "2.0", "id": {request_id}, "result": 5, "error": {{"code": -1}}}}'
)
try:
    future.result()
except InvalidResponseError as e:
    print(type(e).__name__)  # Output: InvalidResponseError


# handle() raises an error for a payload that is unusable as a whole. There is no
# future to attach that error to, and handle() settles nothing in such a payload.
for payload in (b'{"jsonrpc": "2.0"', b"42", b"[]"):
    try:
        client.handle(payload)
    except InvalidResponseError as e:
        print(f"{payload!r} -> {e}")
# Output: b'{"jsonrpc": "2.0"' -> Response is not valid JSON: unexpected end of data: line 1 column 18 (char 17)
# Output: b'42' -> Response is not an object or an array (type: <class 'int'>)
# Output: b'[]' -> Response is an empty batch


# Some responses match nothing: a late answer, a duplicate, or one with an id that the
# client never sent. The client logs them on the "pyjsonrpc2.client" logger at WARNING
# and then ignores them. They say nothing about the calls that are still outstanding.
print(client.handle(b'{"jsonrpc": "2.0", "id": 999, "result": 1}'))  # Output: []


# When a transport dies, nothing is left to answer the calls in flight. You can release
# them with an exception of your own, instead of leaving their futures pending forever.
first = client.request("divide", 1, 2)[1]
print(client.cancel_pending(ConnectionError("the socket went away")))  # Output: 1
try:
    first.result()
except ConnectionError as e:
    print(e)  # Output: the socket went away

# Give no exception to cancel them instead. result() then raises CancelledError.
second = client.request("divide", 3, 4)[1]
print(client.cancel_pending())  # Output: 1
print(second.cancelled())  # Output: True
