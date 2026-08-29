"""Server-side implementation of the JSON-RPC 2.0 protocol.

This module is transport-agnostic: it does no I/O. `JsonRpcServer.call()` takes a raw
request and returns the raw bytes of the response.
"""

from __future__ import annotations

__all__ = ["JsonRpcError", "JsonRpcServer", "rpc_method"]

import inspect
import logging
from enum import Enum
from typing import TYPE_CHECKING, Any, TypeVar, overload

from orjson import Fragment, loads

from ._common import _ID_TYPES, _SENTINEL, JsonRpcError, _bind_dumps

_LOGGER = logging.getLogger(__name__)
_NO_KWARGS: dict[str, Any] = {}

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Mapping, Sequence
    from inspect import Signature

    from ._common import _Id, _Sentinel

    F = TypeVar("F", bound=Callable[..., Any])


class _Error(Enum):
    PARSE_ERROR = {"code": -32700, "message": "Parse error"}  # noqa: RUF012
    INVALID_REQUEST = {"code": -32600, "message": "Invalid Request"}  # noqa: RUF012
    METHOD_NOT_FOUND = {"code": -32601, "message": "Method not found"}  # noqa: RUF012
    INVALID_PARAMS = {"code": -32602, "message": "Invalid params"}  # noqa: RUF012
    INTERNAL_ERROR = {"code": -32603, "message": "Internal error"}  # noqa: RUF012

    def __init__(self, body: dict[str, Any]) -> None:
        # A read of `.value` uses a `DynamicClassAttribute` descriptor every time
        self.body = body

    def with_data(self, data: Any) -> dict[str, Any]:
        return dict(self.body, data=data)


@overload
def rpc_method(_func: F) -> F: ...  # pragma: no cover


@overload
def rpc_method(*, name: str | None = None) -> Callable[[F], F]: ...  # pragma: no cover


def rpc_method(
    _func: F | None = None, *, name: str | None = None
) -> Callable[[F], F] | F:
    """Mark a function as an RPC method.

    Use it bare (`@rpc_method`) or with arguments (`@rpc_method(name="cube")`). A
    `JsonRpcServer` subclass registers its marked methods when you create an instance of
    it. `JsonRpcServer.add_object()` registers the marked methods of any other object. A
    marked plain function is different: you must give it to `JsonRpcServer.add_method()`
    yourself. The mark then supplies only the name.

    Args:
        _func: The function to mark. Python supplies it when you use the decorator bare.
            Do not pass it yourself.
        name: The name that clients use to call the method. Defaults to the name of the
            function itself.

    Returns:
        The same function, with the marker attribute added.

    Raises:
        AttributeError: If the decorator cannot set the marker attribute on the object.

    Example:
        >>> class MathServer(JsonRpcServer):
        ...     @rpc_method
        ...     def square(self, x):
        ...         return x**2
        ...
        ...     @rpc_method(name="cube")
        ...     def calculate_cube(self, x):
        ...         return x**3
    """

    def decorator(f: F, /) -> F:
        try:
            f.__rpc__ = name  # type: ignore[attr-defined]
        except AttributeError as e:
            msg = "Could not set the __rpc__ magic attribute"
            raise AttributeError(msg) from e
        return f

    return decorator if _func is None else decorator(_func)


class JsonRpcServer:
    """A JSON-RPC 2.0 server that dispatches requests to the methods registered on it.

    The server does no I/O of its own. Give a raw request to `call()`, then send the
    bytes that it returns over the transport of your choice.

    You can register methods in four ways:

    - Give a mapping of names to callables to the constructor.
    - Mark the methods of a subclass with `rpc_method`.
    - Call `add_method()` for a single callable.
    - Call `add_object()` for every marked method of an object.

    Example:
        >>> class MathServer(JsonRpcServer):
        ...     @rpc_method
        ...     def square(self, x):
        ...         return x**2
        >>> server = MathServer()
        >>> request = '{"jsonrpc": "2.0", "method": "square", "params": [4], "id": 1}'
        >>> server.call(request)
        b'{"jsonrpc":"2.0","id":1,"result":16}'
    """

    def __init__(
        self,
        methods: Mapping[str, Callable[..., Any]] | None = None,
        *,
        dumps_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Create a server and register its own `rpc_method`-marked methods.

        Args:
            methods: Mapping of RPC method names to the callables to register. The
                server copies it. This path does not read `__rpc__`.
            dumps_kwargs: Extra keyword arguments for `orjson.dumps()`, such as
                `{"option": orjson.OPT_INDENT_2}`. The server reads them only here.
        """
        self._methods: dict[str, Callable[..., Any]] = (
            {} if methods is None else dict(methods)
        )
        self._dumps = _bind_dumps(dumps_kwargs)
        self._signatures: dict[Callable[..., Any], Signature] = {}
        self.add_object(self)

    def add_object(self, obj: object, *, prefix: str = "") -> None:
        """Register every `rpc_method`-marked method of an object.

        Args:
            obj: The object to scan. The server ignores everything else that it holds.
            prefix: Text to put before every name from this object. Use it to keep two
                objects that have the same method names separate.

        Raises:
            ValueError: If one of the new names is already registered.
        """
        for name, method in inspect.getmembers(obj, inspect.isroutine):
            if hasattr(method, "__rpc__"):
                self.add_method(method, name=prefix + (method.__rpc__ or name))

    def add_method(self, method: F, *, name: str | None = None) -> F:
        """Register a single callable as an RPC method.

        This method returns the callable unchanged, so you can also use it as a
        decorator:

            @server.add_method
            def add(a, b):
                return a + b

        Args:
            method: The callable to register. The server calls it with the `"params"`
                of a request. An array becomes positional arguments. An object becomes
                keyword arguments.
            name: The name that clients use to call it. Defaults to the name given to
                `rpc_method`, and then to the name of the callable itself.

        Returns:
            The same callable, unchanged.

        Raises:
            ValueError: If the name is already registered.
        """
        name = name or getattr(method, "__rpc__", None) or method.__name__
        if name in self._methods:
            msg = f"Method {name!r} already registered"
            raise ValueError(msg)
        self._methods[name] = method
        return method

    def _signature(self, method: Callable[..., Any]) -> Signature:
        """Return `inspect.signature(method)`, memoized on the callable."""
        cache = self._signatures
        try:
            return cache[method]
        except KeyError:
            pass
        except TypeError:
            return inspect.signature(method)  # if the callable is unhashable
        signature = cache[method] = inspect.signature(method)
        return signature

    def _validate_and_execute(self, request: Any) -> tuple[Any, _Id | _Sentinel, bool]:  # noqa: C901, PLR0911, PLR0912
        """Validate one request and run the method that it names.

        Args:
            request: Any decoded JSON value. It is not always an object: the `"jsonrpc"`
                lookup below is what rejects the values that are not objects.

        Returns:
            An `(obj, id, error)` tuple:

            - `obj`: the payload to answer with.
            - `id`: the id that the answer is owed to. It is `None` when the request is
              too broken to trust its id, and `_SENTINEL` for a notification.
            - `error`: `True` when `obj` is an error object, `False` when it is a
              result.
        """
        # Validate the "jsonrpc" member
        try:
            if request["jsonrpc"] != "2.0":
                return (
                    _Error.INVALID_REQUEST.with_data(
                        f"Wrong rpc version (got {request['jsonrpc']!r})"
                    ),
                    None,
                    True,
                )
        except KeyError:
            return _Error.INVALID_REQUEST.with_data("Missing 'jsonrpc' key"), None, True
        except TypeError:
            return (
                _Error.INVALID_REQUEST.with_data(
                    f"Not an object (type: {type(request)})"
                ),
                None,
                True,
            )

        # Extract and validate the "id" member
        id = request.get("id", _SENTINEL)  # noqa: A001
        if id is not _SENTINEL and type(id) not in _ID_TYPES:
            return (
                _Error.INVALID_REQUEST.with_data(
                    f"'id' must be a number, string or null (type: {type(id)})"
                ),
                None,
                True,
            )

        # Extract and validate the "method" member
        try:
            method_name = request["method"]
        except KeyError:
            return _Error.INVALID_REQUEST.with_data("Missing 'method' key"), None, True
        if type(method_name) is not str:
            return (
                _Error.INVALID_REQUEST.with_data(
                    f"'method' must be a string (type: {type(method_name)})"
                ),
                None,
                True,
            )

        # Extract and validate the "params" member
        args: Sequence[Any] = ()
        kwargs: dict[str, Any] = _NO_KWARGS
        params = request.get("params", _SENTINEL)
        if params is not _SENTINEL:
            type_params = type(params)
            if type_params is dict:
                kwargs = params
            elif type_params is list:
                args = params
            else:
                return (
                    _Error.INVALID_REQUEST.with_data(
                        f"'params' must be an array or an object (type: {type_params})"
                    ),
                    None,
                    True,
                )

        # Find the method in the registry
        try:
            method = self._methods[method_name]
        except KeyError:
            return _Error.METHOD_NOT_FOUND.body, id, True

        # Call the method and handle the errors
        try:
            try:
                result = method(*args, **kwargs) if kwargs else method(*args)
            except JsonRpcError as e:  # Custom error
                return e.to_dict(), id, True
            except TypeError as e:
                try:  # Check whether invalid params caused it
                    self._signature(method).bind(*args, **kwargs)
                except TypeError:
                    return _Error.INVALID_PARAMS.with_data(str(e)), id, True
                raise
        except Exception as e:
            _LOGGER.exception(
                "RPC Error [id: %s] [method: '%s'] Uncaught exception",
                "notification" if id is _SENTINEL else id,
                method_name,
            )
            return _Error.INTERNAL_ERROR.with_data(str(e)), id, True
        return result, id, False

    def _encode(
        self,
        obj: Any,
        id: _Id = None,  # noqa: A002
        error: bool = True,  # noqa: FBT001 FBT002
    ) -> bytes:
        """Build one response envelope and serialize it.

        There is nothing to encode for a notification, because the callers remove them
        first.
        """
        response = {"jsonrpc": "2.0", "id": id, "error" if error else "result": obj}
        try:
            return self._dumps(response)
        except TypeError as e:
            # The result is not serializable. Answer the same id with an internal
            # error instead. This call recurses only one level deep, because the new
            # payload is a string and the id already survived a JSON decode.
            _LOGGER.exception("RPC Error [id:%s] Unserializable response", id)
            return self._encode(_Error.INTERNAL_ERROR.with_data(str(e)), id)

    def call(self, request: bytes | bytearray | memoryview | str) -> bytes | None:
        """Handle one raw request and return the raw response.

        The method accepts a single request or a batch. It reports a failure to the
        client and raises nothing. If a method raises an exception that is not a
        `JsonRpcError`, the server logs it with level ERROR and answers the client with
        -32603 Internal error.

        Args:
            request: The request, as JSON text or as its UTF-8 encoding.

        Returns:
            The encoded response. Returns `None` when the client is owed no answer, that
            is, for a single notification or for a batch of notifications only.
        """
        try:
            decoded = loads(request)
        except ValueError as e:
            return self._encode(_Error.PARSE_ERROR.with_data(str(e)))

        if type(decoded) is list:  # Batch request
            if not decoded:
                return self._encode(_Error.INVALID_REQUEST.with_data("Empty batch"))
            responses: list[Fragment] = []
            for element in decoded:
                obj, id, error = self._validate_and_execute(element)  # noqa: A001
                if id is not _SENTINEL:  # Not a notification
                    # Encode each element, then splice the bytes into the outer array
                    responses.append(Fragment(self._encode(obj, id, error)))
            return self._dumps(responses) if responses else None

        obj, id, error = self._validate_and_execute(decoded)  # noqa: A001
        if id is _SENTINEL:  # A notification is owed no answer
            return None
        return self._encode(obj, id, error)
