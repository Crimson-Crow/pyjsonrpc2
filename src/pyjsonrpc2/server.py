from __future__ import annotations

__all__ = ["JsonRpcError", "JsonRpcServer", "rpc_method"]

import inspect
import logging
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, TypeAlias, TypeVar, overload

from orjson import Fragment, dumps, loads


# Marks an absent "id" key. A single-member enum rather than a bare `object()`
# so that it is expressible in annotations and narrowed by `is` comparisons.
class _Sentinel(Enum):
    SENTINEL = auto()


_LOGGER = logging.getLogger(__name__)
_SENTINEL = _Sentinel.SENTINEL
_ID = (str, int, float, type(None))

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Sequence

    F = TypeVar("F", bound=Callable[..., Any])

    # A request id, once validated against _ID. `_Sentinel` means "notification".
    _Id: TypeAlias = str | float | None
    _MaybeId: TypeAlias = _Id | _Sentinel

    # Arguments splatted into _respond(). The arity encodes the outcome:
    # 1 -> error before an id could be trusted, 2 -> error for a known id,
    # 3 -> success (the trailing False selects "result" over "error").
    _Outcome: TypeAlias = (
        tuple[dict[str, Any]]
        | tuple[dict[str, Any], _MaybeId]
        | tuple[Any, _MaybeId, bool]
    )


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"[{code}] {message}" + ("" if data is None else f": {data}"))
        self.code = code
        self.message = message
        self.data = data

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

    def with_data(self, data: Any) -> dict[str, Any]:
        return dict(self.value, data=data)


def _respond(
    obj: Any,
    id: _MaybeId = None,  # noqa: A002
    error: bool = True,  # noqa: FBT001 FBT002
) -> dict[str, Any] | None:
    return (
        None
        if id is _SENTINEL
        else {"jsonrpc": "2.0", "id": id, "error" if error else "result": obj}
    )


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
        self._dumps_kwargs = dumps_kwargs or {}
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
            msg = f"Method '{name}' already registered"
            raise ValueError(msg)
        self._methods[name] = method

    # `request` is an arbitrary decoded JSON value, not necessarily an object:
    # the "jsonrpc" lookup below is what rejects the non-object cases.
    def _validate_and_execute(self, request: Any) -> _Outcome:  # noqa: C901, PLR0911, PLR0912
        # Validate "jsonrpc" entry
        try:
            if request["jsonrpc"] != "2.0":
                return (
                    _Error.INVALID_REQUEST.with_data(
                        f"Wrong rpc version (got '{request['jsonrpc']!s}')"
                    ),
                )
        except KeyError:
            return (_Error.INVALID_REQUEST.with_data("Missing 'jsonrpc' key"),)
        except TypeError:
            return (
                _Error.INVALID_REQUEST.with_data(
                    f"Not an object (type: {type(request)})"
                ),
            )

        # Extract and validate "id" entry
        id = request.get("id", _SENTINEL)  # noqa: A001
        # `bool` is a subclass of `int` but is not a valid id per spec
        if id is not _SENTINEL and (isinstance(id, bool) or not isinstance(id, _ID)):
            return (
                _Error.INVALID_REQUEST.with_data(
                    f"'id' must be a number, string or null (type: {type(id)})"
                ),
            )

        # Extract and validate "method" entry
        try:
            method_name = request["method"]
        except KeyError:
            return (_Error.INVALID_REQUEST.with_data("Missing 'method' key"),)
        if not isinstance(method_name, str):
            return (
                _Error.INVALID_REQUEST.with_data(
                    f"'method' must be a string (type: {type(method_name)})"
                ),
            )

        # Extract and validate "params" entry
        args: Sequence[Any] = ()
        kwargs = {}
        if "params" in request:  # LBYL because its absence is not exceptional behavior
            params = request["params"]
            if isinstance(params, dict):
                kwargs = params
            elif isinstance(params, list):
                args = params
            else:
                return (
                    _Error.INVALID_REQUEST.with_data(
                        f"'params' must be an array or an object (type: {type(params)})"
                    ),
                )

        # Find rpc method in registry
        try:
            method = self._methods[method_name]
        except KeyError:
            return _Error.METHOD_NOT_FOUND.value, id

        # Call method and handle error
        try:
            try:
                result = method(*args, **kwargs)
            except JsonRpcError as e:  # Custom error
                return e.to_dict(), id
            except TypeError as e:
                try:  # Check if it is caused by invalid params
                    inspect.signature(method).bind(*args, **kwargs)
                except TypeError:
                    return _Error.INVALID_PARAMS.with_data(str(e)), id
                raise
        except Exception as e:
            _LOGGER.exception(
                "RPC Error [id: %s] [method: '%s'] Uncaught exception",
                "notification" if id is _SENTINEL else str(id),
                method_name,
            )
            return _Error.INTERNAL_ERROR.with_data(str(e)), id
        return result, id, False

    def _decode_and_parse(
        self, raw_request: bytes | bytearray | memoryview | str
    ) -> dict[str, Any] | list[Fragment] | None:
        try:
            request = loads(raw_request)
        except ValueError as e:
            return _respond(_Error.PARSE_ERROR.with_data(str(e)))
        if isinstance(request, list):  # Batch request
            if not request:
                return _respond(_Error.INVALID_REQUEST.with_data("Empty batch"))
            return [
                Fragment(self._encode(response))
                for r in request
                if (
                    response := _respond(*self._validate_and_execute(r))
                )  # None (notification) check
            ]
        return _respond(*self._validate_and_execute(request))

    @overload
    def _encode(self, response: None) -> None: ...  # pragma: no cover

    @overload
    def _encode(self, response: list[Fragment]) -> bytes | None: ...  # pragma: no cover

    @overload
    def _encode(self, response: dict[str, Any]) -> bytes: ...  # pragma: no cover

    def _encode(self, response: dict[str, Any] | list[Fragment] | None) -> bytes | None:
        if not response:  # Notification or empty list (batch response)
            return None
        try:
            return dumps(response, **self._dumps_kwargs)
        except TypeError as e:
            if isinstance(response, list):  # pragma: no cover
                msg = "Should never happen: we are joining fragments of already serialized responses if this is a batch at this point"
                raise RuntimeError(msg) from e  # noqa: TRY004
            id = response["id"]  # noqa: A001
            _LOGGER.exception("RPC Error [id:%s] Unserializable response", str(id))
            return self._encode(
                _respond(_Error.INTERNAL_ERROR.with_data(str(e)), id=id)
            )

    def call(self, request: bytes | bytearray | memoryview | str) -> bytes | None:
        return self._encode(self._decode_and_parse(request))
