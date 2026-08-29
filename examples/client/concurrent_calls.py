"""This example module details driving a client from several threads at once.

This is why `request()` returns a `Future` and not a result. The thread that makes a
call and the thread that reads the transport can be two different threads.
"""

import queue
import threading
from concurrent.futures import as_completed, wait

from pyjsonrpc2.client import JsonRpcClient
from pyjsonrpc2.server import JsonRpcServer, rpc_method


class MathServer(JsonRpcServer):
    @rpc_method
    def square(self, x):
        return x**2


server = MathServer()
client = JsonRpcClient()

# A more realistic transport: requests go out through a queue, and answers come back at
# any time. Put a socket, a websocket or an HTTP pool in its place.
outbound = queue.Queue()


def connection():
    """Answer every request that waits in the queue, the last one first."""
    running = True
    while running:
        pending = [outbound.get()]
        while True:  # Take whatever else already waits in the queue
            try:
                pending.append(outbound.get_nowait())
            except queue.Empty:
                break
        if None in pending:  # The signal to stop
            pending = [request for request in pending if request is not None]
            running = False
        # An answer in reverse order costs nothing. The client matches responses on
        # their id, never on the order that they arrive in.
        for request in reversed(pending):
            response = server.call(request)
            if response is not None:
                client.handle(response)  # This settles the future that it belongs to


reader = threading.Thread(target=connection)
reader.start()


# Twenty threads call at once. The client assigns and records ids under a lock, so no
# call is lost, duplicated, or answered with the result of another call.
def call(n, into):
    request, future = client.request("square", n)
    into[n] = future
    outbound.put(request)


futures = {}
threads = [threading.Thread(target=call, args=(n, futures)) for n in range(20)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()

# Nothing above waited, so twenty calls are now in flight.
print(len(futures))  # Output: 20

# wait() blocks until the client settles all of them, in any order.
done, not_done = wait(futures.values(), timeout=10)
print(len(done), len(not_done))  # Output: 20 0
print([futures[n].result() for n in range(20)])
# Output: [0, 1, 4, 9, 16, 25, 36, 49, 64, 81, 100, 121, 144, 169, 196, 225, 256, 289, 324, 361]


# as_completed() yields each future at the moment the client settles it, so you can read
# the results one by one.
calls = [client.request("square", n) for n in range(4)]
for request, _ in calls:
    outbound.put(request)
print(sorted(future.result() for future in as_completed(f for _, f in calls)))
# Output: [0, 1, 4, 9]


# A done callback runs on the thread that settles the future, the reader thread here.
request, future = client.request("square", 12)
future.add_done_callback(lambda f: print("callback got", f.result()))
outbound.put(request)
future.result(timeout=10)
# Output: callback got 144

outbound.put(None)
reader.join()


# A call that the transport never answered is still outstanding after it closes.
# Release it. If you do not, the holders of those futures wait on a dead connection.
client.request("square", 99)
print(client.cancel_pending(ConnectionError("connection closed")))  # Output: 1
