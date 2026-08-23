"""A flexible Python implementation of the JSON-RPC 2.0 protocol."""

__all__ = ["__version__", "server"]

from importlib.metadata import version

from . import server

__version__ = version("pyjsonrpc2")
