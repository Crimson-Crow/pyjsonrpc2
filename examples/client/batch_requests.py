"""This example module details how to group several calls into one batch request."""

import json
from concurrent.futures import wait

from pyjsonrpc2.client import JsonRpcClient, JsonRpcError
from pyjsonrpc2.server import JsonRpcServer, rpc_method


class MathServer(JsonRpcServer):
    @rpc_method
    def subtract(self, minuend, subtrahend):
        return minuend - subtrahend

    @rpc_method(name="sum")
    def add(self, *args):
        return sum(args)

    @rpc_method
    def log(self, message):
        logged.append(message)


logged = []
server = MathServer()
client = JsonRpcClient()


def transport(request):
    return server.call(request)


# A batch collects calls and encodes them as one payload. A request gives you its own
# future. A notification gives you nothing. Both count towards the length.
batch = client.batch()
total = batch.request("sum", 1, 2, 4)
batch.notify("log", "part of the batch")
difference = batch.request("subtract", 42, 23)
missing = batch.request("does_not_exist")

print(len(batch))  # Output: 4
# The notification carries no id. Only the three requests consume one.
print(batch.encode())
# Output: b'[{"jsonrpc":"2.0","method":"sum","params":[1,2,4],"id":1},{"jsonrpc":"2.0","method":"log","params":["part of the batch"]},{"jsonrpc":"2.0","method":"subtract","params":[42,23],"id":2},{"jsonrpc":"2.0","method":"does_not_exist","id":3}]'

# One payload goes out and one payload comes back. The client settles every future in it
# at once.
client.handle(transport(batch.encode()))
print(total.result())  # Output: 7
print(difference.result())  # Output: 19
print(logged)  # Output: ['part of the batch']

# The server answers each element on its own, so one failure costs the others nothing.
try:
    missing.result()
except JsonRpcError as e:
    print(e)
# Output: [-32601] Method not found


# The client matches responses on the id that it assigned, never on their position. A
# server can therefore answer the elements of a batch in any order. The example below
# returns the answers in reverse order, which changes nothing.
batch = client.batch()
first = batch.request("sum", 1)
second = batch.request("sum", 2)
answers = json.loads(transport(batch.encode()))
client.handle(json.dumps(answers[::-1]))
print((first.result(), second.result()))  # Output: (1, 2)


# A batch of notifications only is still worth sending. There is simply nothing to
# give to handle() afterwards.
batch = client.batch()
batch.notify("log", "first")
batch.notify("log", "second")
print(transport(batch.encode()))  # Output: None
print(logged)  # Output: ['part of the batch', 'first', 'second']


# The client refuses an empty batch instead of sending it.
try:
    client.batch().encode()
except ValueError as e:
    print(e)
# Output: A batch request must hold at least one call


# encode() does not close a batch, so you can send a long-running batch as it grows.
# The futures of the calls already sent stay valid across every later encode().
batch = client.batch()
batch.request("sum", 1, 2)
print(len(json.loads(batch.encode())))  # Output: 1
batch.request("sum", 3, 4)
print(len(json.loads(batch.encode())))  # Output: 2


# These futures behave like any other concurrent.futures.Future, so the whole of
# that module works on them: wait(), as_completed(), add_done_callback(), and more.
batch = client.batch()
futures = [batch.request("sum", n, n) for n in range(5)]
client.handle(transport(batch.encode()))
done, not_done = wait(futures, timeout=5)
print(len(done), len(not_done))  # Output: 5 0
print(sorted(future.result() for future in futures))  # Output: [0, 2, 4, 6, 8]
