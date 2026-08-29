"""Definitions shared by the two halves of the protocol.

This module is private and has no stability guarantee. `JsonRpcError` is public API,
but only through `pyjsonrpc2.server` and `pyjsonrpc2.client`.
"""

from __future__ import annotations

from enum import Enum, auto
from functools import partial
from typing import TYPE_CHECKING, Any, TypeAlias

from orjson import dumps


class _Sentinel(Enum):
    SENTINEL = auto()


_SENTINEL = _Sentinel.SENTINEL
# Test membership with `type(x) in ...`, not with `isinstance`. The exact-type test
# rejects `bool`, where `isinstance` needs a guard of its own. The test is correct only
# for the exact types that `orjson.loads` gives. Never use it on a value that a caller
# supplies directly.
_ID_TYPES = frozenset({str, int, float, type(None)})

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

    _Id: TypeAlias = str | float | None  # noqa: PYI047


class JsonRpcError(Exception):
    """Error whose details go to the client without changes.

    Raise it from a registered method to answer with a specific code, message and data.
    Any other exception gives the generic internal error instead. On the client half,
    the future of a request raises it when the server answers that request with an
    error.

    The specification reserves the codes from -32768 to -32000. The codes -32099 to
    -32000 in that range are for implementation-defined server errors. An application
    can use any code outside the reserved range.

    Attributes:
        code: The error code.
        message: A short description of the error.
        data: Additional information about the error, or `None`.
    """

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        """Initialize the error.

        Args:
            code: The error code.
            message: A short description of the error.
            data: Optional additional information about the error. It must be JSON
                serializable. The response does not include it when it is `None`.
        """
        super().__init__(code, message, data)
        self.code = code
        self.message = message
        self.data = data

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}" + (
            "" if self.data is None else f": {self.data!r}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the error as a JSON-RPC error object.

        Returns:
            A dict with `"code"` and `"message"`. The dict also has `"data"` when `data`
            is not `None`.
        """
        to_return: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            to_return["data"] = self.data
        return to_return


def _bind_dumps(dumps_kwargs: dict[str, Any] | None) -> Callable[[Any], bytes]:
    """Bind the encoder once, before any encoding.

    Returns `orjson.dumps` itself when there is nothing to bind, so that the usual case
    has no wrapper. The cost is that this function reads `dumps_kwargs` only once. A
    change to that dict afterwards has no effect.
    """
    return partial(dumps, **dumps_kwargs) if dumps_kwargs else dumps
