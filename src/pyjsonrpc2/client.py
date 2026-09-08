"""Client-side implementation of the JSON-RPC 2.0 protocol.

This module is transport-agnostic: it does no I/O. `JsonRpcClient.request()` returns
the raw bytes of a request and the `Future` that receives the answer.
`JsonRpcClient.handle()` takes the raw bytes of a response and settles the future that
the response is owed to. The caller moves the bytes between the two.
"""

from __future__ import annotations

__all__ = ["InvalidResponseError", "JsonRpcBatch", "JsonRpcClient", "JsonRpcError"]

import logging
from concurrent.futures import Future, InvalidStateError
from concurrent.futures._base import CANCELLED
from itertools import count
from threading import Lock
from typing import TYPE_CHECKING, Any

from orjson import loads

from ._common import _ID_TYPES, _SENTINEL, JsonRpcError, _bind_dumps

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Iterator

    from ._common import _Id


class InvalidResponseError(ValueError):
    """Error that shows that a payload is not a JSON-RPC 2.0 response.

    `JsonRpcClient.handle()` raises it when the payload as a whole is unusable. For a
    single response object that is malformed on its own, `handle()` sets this error on
    the future of that response instead.
    """


class _RpcFuture(Future[Any]):
    """A future that notifies its own cancellation.

    `Future.cancel()` only records the cancellation. The executor that owns the work
    item notifies it later. This client is not an executor, so nothing else notifies
    it. `concurrent.futures.wait()` and `as_completed()` would then count a canceled
    request as outstanding forever.
    """

    def cancel(self) -> bool:
        """Cancel the request, and notify the waiters of the future.

        Returns:
            True if the future is canceled. False if the future already holds a result
            or an exception.
        """
        if not super().cancel():
            return False
        with self._condition:
            # Notifying twice would log at CRITICAL level, then raise `RuntimeError`.
            if self._state is CANCELLED:
                self.set_running_or_notify_cancel()
            # State is now CANCELLED_AND_NOTIFIED.
        return True


def _build_request(
    method: str, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> dict[str, Any]:
    """Build the object for one call, without the `"id"` that makes it a request.

    Raises:
        TypeError: If `method` is not a string.
        ValueError: If the caller gives positional and keyword parameters together.
    """
    if not isinstance(method, str):
        msg = f"'method' must be a string (type: {type(method)})"
        raise TypeError(msg)
    request: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if args:
        if kwargs:
            msg = "Parameters cannot be both positional and keyword ('params' must be either an array or an object)"
            raise ValueError(msg)
        request["params"] = args
    elif kwargs:
        request["params"] = kwargs
    return request


class JsonRpcBatch:
    """Several calls collected into a single batch request.

    Do not create one directly. Use `JsonRpcClient.batch()`, add calls to the batch,
    then give the bytes from `encode()` to the transport. A server may answer the
    elements of a batch in any order. A server answers nothing to a batch that holds
    only notifications.

    Example:
        >>> client = JsonRpcClient()
        >>> batch = client.batch()
        >>> total = batch.request("add", 1, 2)
        >>> batch.notify("log", "hi")
        >>> batch.encode()
        b'[{"jsonrpc":"2.0","method":"add","params":[1,2],"id":1},{"jsonrpc":"2.0","method":"log","params":["hi"]}]'
    """

    def __init__(
        self,
        register: Callable[[dict[str, Any]], Future[Any]],
        dumps: Callable[[Any], bytes],
    ) -> None:
        """Create an empty batch for one client.

        Args:
            register: The callable of the client that adds an id to a request object,
                `JsonRpcClient._register`.
            dumps: The encoder of the client, so that the batch and a single request use
                the same encoder.
        """
        self._register = register
        self._dumps = dumps
        self._requests: list[dict[str, Any]] = []

    def __len__(self) -> int:
        """Return the number of calls in the batch, notifications included."""
        return len(self._requests)

    def request(self, method: str, /, *args: Any, **kwargs: Any) -> Future[Any]:
        """Add one request to the batch and return the future that holds its outcome.

        The request becomes pending immediately. If you build a batch but never encode
        it, you must still release these requests with `JsonRpcClient.cancel_pending()`.

        Args:
            method: The name of the method to call. It is positional-only, so that you
                can still send a parameter of the same name as a keyword.
            *args: Positional parameters, sent as the `"params"` array.
            **kwargs: Keyword parameters, sent as the `"params"` object. The protocol
                accepts only one of the two forms, so do not use `**kwargs` together
                with `*args`.

        Returns:
            The future that receives the response to this element.

        Raises:
            TypeError: If `method` is not a string.
            ValueError: If the caller gives positional and keyword parameters together,
                or if the id iterator gives an id that already awaits a response.
        """
        request = _build_request(method, args, kwargs)
        future = self._register(request)
        self._requests.append(request)
        return future

    def notify(self, method: str, /, *args: Any, **kwargs: Any) -> None:
        """Add one notification to the batch.

        Args:
            method: The name of the method to call. It is positional-only, so that you
                can still send a parameter of the same name as a keyword.
            *args: Positional parameters, sent as the `"params"` array.
            **kwargs: Keyword parameters, sent as the `"params"` object. The protocol
                accepts only one of the two forms, so do not use `**kwargs` together
                with `*args`.

        Raises:
            TypeError: If `method` is not a string.
            ValueError: If the caller gives positional and keyword parameters together.
        """
        self._requests.append(_build_request(method, args, kwargs))

    def encode(self) -> bytes:
        """Serialize the batch in its current state.

        `encode()` does not close the batch. You can add more calls and encode the batch
        again. The new payload also holds the calls that you sent before.

        Returns:
            The encoded batch request.

        Raises:
            ValueError: If the batch is empty, which the specification does not allow.
        """
        if not self._requests:
            msg = "A batch request must hold at least one call"
            raise ValueError(msg)
        return self._dumps(self._requests)


class JsonRpcClient:
    """A JSON-RPC 2.0 client that gives a `Future` for every request it builds.

    The client does no I/O. The caller moves the bytes:

    - `request()` gives the bytes of a request, and the future for its answer.
    - The transport sends those bytes, and receives the response.
    - `handle()` takes the response, and settles the future.

    The client writes an `"id"` into every request. It matches each response to a future
    on that same `"id"`. A transport can therefore:

    - answer the requests in any order
    - answer from another thread
    - never answer at all

    Example:
        >>> client = JsonRpcClient()
        >>> data, future = client.request("subtract", 42, 23)
        >>> data
        b'{"jsonrpc":"2.0","method":"subtract","params":[42,23],"id":1}'
        >>> client.handle(b'{"jsonrpc": "2.0", "id": 1, "result": 19}')
        []
        >>> future.result()
        19
    """

    def __init__(
        self,
        *,
        id_iterator: Iterator[_Id] | None = None,
        dumps_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Create a client.

        Args:
            id_iterator: Source of the `"id"` of every request. The client takes one id
                at a time. Each id must be JSON serializable and unique for the life of
                the client. An id must never be `None`, because a null id marks a
                response that belongs to no request. Defaults to `itertools.count(1)`.
            dumps_kwargs: Extra keyword arguments for `orjson.dumps()`, such as
                `{"option": orjson.OPT_INDENT_2}`. The client reads them only here.
        """
        self._ids: Iterator[_Id] = count(1) if id_iterator is None else id_iterator
        self._dumps = _bind_dumps(dumps_kwargs)
        self._pending: dict[_Id, Future[Any]] = {}
        self._lock = Lock()

    def _register(self, request: dict[str, Any]) -> Future[Any]:
        """Add an id to a request object and keep the future that answers it."""
        future: Future[Any] = _RpcFuture()
        with self._lock:
            id = next(self._ids)  # noqa: A001
            # One lookup only. If the id iterator repeats an id, this stops the
            # client from losing the future that already waits under that id.
            if self._pending.setdefault(id, future) is not future:
                msg = f"Request id {id!r} is already awaiting a response"
                raise ValueError(msg)
        request["id"] = id
        return future

    def request(
        self, method: str, /, *args: Any, **kwargs: Any
    ) -> tuple[bytes, Future[Any]]:
        """Build one request and the future that receives its response.

        The request becomes pending as soon as this method returns, even if it never
        reaches a transport. `cancel_pending()` releases the requests that never do.

        Args:
            method: The name of the method to call. It is positional-only, so that you
                can still send a parameter of the same name as a keyword.
            *args: Positional parameters, sent as the `"params"` array.
            **kwargs: Keyword parameters, sent as the `"params"` object. The protocol
                accepts only one of the two forms, so do not use `**kwargs` together
                with `*args`.

        Returns:
            The encoded request, and the future that holds its outcome. The future gives
            the `"result"` of the response, or raises a `JsonRpcError` out of
            `Future.result()`.

        Raises:
            TypeError: If `method` is not a string, or if the encoder refuses the
                parameters.
            ValueError: If the caller gives positional and keyword parameters together,
                or if the id iterator gives an id that already awaits a response.
        """
        request = _build_request(method, args, kwargs)
        future = self._register(request)
        try:
            data = self._dumps(request)
        except BaseException:
            # The encoder refused the parameters, so the caller never receives this
            # future. Remove the entry that `_register` made, or the request stays
            # pending for the life of the client.
            with self._lock:
                del self._pending[request["id"]]
            raise
        return data, future

    def notify(self, method: str, /, *args: Any, **kwargs: Any) -> bytes:
        """Build one notification, which the server does not answer.

        Args:
            method: The name of the method to call. It is positional-only, so that you
                can still send a parameter of the same name as a keyword.
            *args: Positional parameters, sent as the `"params"` array.
            **kwargs: Keyword parameters, sent as the `"params"` object. The protocol
                accepts only one of the two forms, so do not use `**kwargs` together
                with `*args`.

        Returns:
            The encoded notification. A notification has no id, so the client can match
            nothing against it, not even an error.

        Raises:
            TypeError: If `method` is not a string.
            ValueError: If the caller gives positional and keyword parameters together.
        """
        return self._dumps(_build_request(method, args, kwargs))

    def batch(self) -> JsonRpcBatch:
        """Start to group calls into a single batch request.

        Returns:
            An empty `JsonRpcBatch` for this client. The batch registers each request
            when you add it, so `handle()` settles those futures like any other.
        """
        return JsonRpcBatch(self._register, self._dumps)

    def _settle(self, response: Any) -> JsonRpcError | None:  # noqa: C901, PLR0912
        """Settle the future that one response object is owed to.

        Args:
            response: Any decoded JSON value. It is not always an object: the type check
                below is what rejects the other values.

        Returns:
            The error of a response that has a null id, which belongs to no pending
            request. Returns `None` when there is nothing left for the caller to do.
        """
        if type(response) is not dict:
            _LOGGER.warning("Discarding a response that is not an object: %r", response)
            return None

        # A missing key gives `_SENTINEL`, whose type is not an id type either
        id = response.get("id", _SENTINEL)  # noqa: A001
        if type(id) not in _ID_TYPES:
            _LOGGER.warning(
                "Discarding a response with a missing or invalid 'id': %r", response
            )
            return None

        if id is None:
            # The server also could not attribute this failure to a request
            future = None
        else:
            with self._lock:
                future = self._pending.pop(id, None)
            if future is None:
                _LOGGER.warning(
                    "Discarding a response to the unknown id %r: %r", id, response
                )
                return None

        # Decode into a result, or into an exception to raise out of the future
        result = response.get("result", _SENTINEL)
        error = response.get("error", _SENTINEL)
        exc: BaseException | None = None
        if response.get("jsonrpc") != "2.0" or (result is _SENTINEL) == (
            error is _SENTINEL
        ):
            exc = InvalidResponseError(f"Not a response object: {response!r}")
        elif error is not _SENTINEL:
            try:
                exc = JsonRpcError(error["code"], error["message"], error.get("data"))
            except (KeyError, TypeError):
                exc = InvalidResponseError(f"Not an error object: {error!r}")

        if future is not None:
            try:
                if exc is None:
                    future.set_result(result)
                else:
                    future.set_exception(exc)
            except InvalidStateError:
                # The caller canceled the request before its answer arrived.
                # `_RpcFuture.cancel()` notified it already.
                pass
            return None
        if isinstance(exc, JsonRpcError):  # No future to raise it out of, so return it
            return exc
        _LOGGER.warning("Discarding an unattributable response: %r", response)
        return None

    def handle(
        self, response: bytes | bytearray | memoryview | str
    ) -> list[JsonRpcError]:
        """Parse a raw payload and settle the associated futures.

        The method accepts a single response or a batch, in any order and from any
        thread. It settles the matching future as follows:

        - A `"result"` becomes the result of the future.
        - An `"error"` becomes a `JsonRpcError` set on the future as its exception.
        - A response that is malformed on its own becomes an `InvalidResponseError` set
          on the future as its exception.

        A response that matches no pending request settles no future. The method does
        not discard it silently. It returns the responses that carry an error, which is
        how a server reports a failure that it found before it could trust an id. It
        logs the other responses with level WARNING.

        Args:
            response: The response, as JSON text or as its UTF-8 encoding.

        Returns:
            The errors of the responses that belong to no pending request, in the order
            that they appeared. This list is usually empty.

        Raises:
            InvalidResponseError: If the payload as a whole is unusable. The payload is
                not valid JSON, not an object or an array, or an empty array. The method
                settles nothing in that case.
        """
        try:
            decoded = loads(response)
        except ValueError as e:
            msg = f"Response is not valid JSON: {e}"
            raise InvalidResponseError(msg) from e

        if type(decoded) is list:  # Batch response
            if not decoded:
                msg = "Response is an empty batch"
                raise InvalidResponseError(msg)
            unattributed: list[JsonRpcError] = []
            for element in decoded:
                error = self._settle(element)
                if error is not None:
                    unattributed.append(error)
            return unattributed

        if type(decoded) is not dict:
            msg = f"Response is not an object or an array (type: {type(decoded)})"
            raise InvalidResponseError(msg)
        error = self._settle(decoded)
        return [] if error is None else [error]

    def cancel_pending(self, exc: BaseException | None = None) -> int:
        """Settle every request that still awaits a response.

        Use it when the transport fails or stops. It releases the holders of the
        futures, which otherwise wait for answers that never come. A response that
        arrives after this call matches nothing, and the client logs it.

        Args:
            exc: The exception to fail the futures with. When it is `None`, the client
                cancels the futures instead, which makes them raise `CancelledError`.

        Returns:
            The number of requests that still awaited a response.
        """
        with self._lock:
            pending = self._pending
            self._pending = {}
        for future in pending.values():
            if exc is None:
                future.cancel()
                continue
            try:
                future.set_exception(exc)
            except InvalidStateError:
                pass  # The caller canceled this request first.
        return len(pending)
