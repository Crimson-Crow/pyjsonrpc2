"""A correct, transport-agnostic Python implementation of the JSON-RPC 2.0 protocol."""

__all__ = ["__version__"]

from importlib.metadata import version

__version__ = version("pyjsonrpc2")
