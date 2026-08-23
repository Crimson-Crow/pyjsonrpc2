from __future__ import annotations

__all__ = ["JsonRpcError", "JsonRpcServer", "rpc_method"]

import inspect
import logging
from enum import Enum, auto
from functools import partial
from typing import TYPE_CHECKING, Any, TypeAlias, TypeVar, overload

from orjson import Fragment, dumps, loads


class _Sentinel(Enum):
    SENTINEL = auto()


_LOGGER = logging.getLogger(__name__)
_SENTINEL = _Sentinel.SENTINEL
_ID_TYPES = frozenset({str, int, float, type(None)})
_NO_KWARGS: dict[str, Any] = {}

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Sequence
    from inspect import Signature

    F = TypeVar("F", bound=Callable[..., Any])
    _Id: TypeAlias = str | float | None


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(code, message, data)
        self.code = code
        self.message = message
        self.data = data

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}" + (
            "" if self.data is None else f": {self.data!r}"
        )

    def to_dict(self) -> dict[str, Any]:
        to_return: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            to_return["data"] = self.data
        return to_return


class _Error(Enum):
    PARSE_ERROR = {"code": -32700, "message": "Parse error"}  # noqa: RUF012
    INVALID_REQUEST = {"code": -32600, "message": "Invalid Request"}  # noqa: RUF012
    METHOD_NOT_FOUND = {"code": -32601, "message": "Method not found"}  # noqa: RUF012
    INVALID_PARAMS = {"code": -32602, "message": "Invalid params"}  # noqa: RUF012
    INTERNAL_ERROR = {"code": -32603, "message": "Internal error"}  # noqa: RUF012

    def __init__(self, body: dict[str, Any]) -> None:
        # `.value` would go through a `DynamicClassAttribute` descriptor on every read
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
    def decorator(f: F, /) -> F:
        try:
            f.__rpc__ = name  # type: ignore[attr-defined]
        except AttributeError as e:
            msg = "Could not set the __rpc__ magic attribute"
            raise AttributeError(msg) from e
        return f

    return decorator if _func is None else decorator(_func)


class JsonRpcServer:
    def __init__(
        self,
        methods: dict[str, Callable[..., Any]] | None = None,
        *,
        dumps_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self._methods = methods or {}
        self._dumps: Callable[[Any], bytes] = (
            partial(dumps, **dumps_kwargs) if dumps_kwargs else dumps
        )
        self._signatures: dict[Callable[..., Any], Signature] = {}
        self.add_object(self)

    def add_object(self, obj: object, *, prefix: str = "") -> None:
        for name, method in inspect.getmembers(obj, inspect.isroutine):
            if hasattr(method, "__rpc__"):
                self.add_method(method, name=prefix + (method.__rpc__ or name))

    def add_method(
        self, method: Callable[..., Any], *, name: str | None = None
    ) -> None:
        name = name or getattr(method, "__rpc__", None) or method.__name__
        if name in self._methods:
            msg = f"Method {name!r} already registered"
            raise ValueError(msg)
        self._methods[name] = method

    def _signature(self, method: Callable[..., Any]) -> Signature:
        """Return `inspect.signature(method)`, memoized on the callable."""
        cache = self._signatures
        try:
            return cache[method]
        except KeyError:
            pass
        except TypeError:
            return inspect.signature(method)  # Unhashable callable
        signature = cache[method] = inspect.signature(method)
        return signature

    # `request` is an arbitrary decoded JSON value, not necessarily an object:
    # the "jsonrpc" lookup below is what rejects the non-object cases.
    def _validate_and_execute(self, request: Any) -> tuple[Any, _Id | _Sentinel, bool]:  # noqa: C901, PLR0911, PLR0912
        # Validate "jsonrpc" entry
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

        # Extract and validate "id" entry
        id = request.get("id", _SENTINEL)  # noqa: A001
        if id is not _SENTINEL and type(id) not in _ID_TYPES:
            return (
                _Error.INVALID_REQUEST.with_data(
                    f"'id' must be a number, string or null (type: {type(id)})"
                ),
                None,
                True,
            )

        # Extract and validate "method" entry
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

        # Extract and validate "params" entry
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

        # Find rpc method in registry
        try:
            method = self._methods[method_name]
        except KeyError:
            return _Error.METHOD_NOT_FOUND.body, id, True

        # Call method and handle error
        try:
            try:
                result = method(*args, **kwargs) if kwargs else method(*args)
            except JsonRpcError as e:  # Custom error
                return e.to_dict(), id, True
            except TypeError as e:
                try:  # Check if it is caused by invalid params
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

        Nothing to encode for notifications because callers drop them.
        """
        response = {"jsonrpc": "2.0", "id": id, "error" if error else "result": obj}
        try:
            return self._dumps(response)
        except TypeError as e:
            # Unserializable result: answer the same id with an internal error instead.
            # Will never recurse more than once because error is serializable.
            _LOGGER.exception("RPC Error [id:%s] Unserializable response", id)
            return self._encode(_Error.INTERNAL_ERROR.with_data(str(e)), id)

    def call(self, request: bytes | bytearray | memoryview | str) -> bytes | None:
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
                if id is not _SENTINEL:  # Notification
                    # Encoded per element then spliced
                    responses.append(Fragment(self._encode(obj, id, error)))
            return self._dumps(responses) if responses else None

        obj, id, error = self._validate_and_execute(decoded)  # noqa: A001
        if id is _SENTINEL:  # Notification
            return None
        return self._encode(obj, id, error)
