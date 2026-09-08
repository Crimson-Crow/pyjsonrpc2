"""Runner setup that the benchmark suites in this directory share."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pyperf

if TYPE_CHECKING:
    from argparse import Namespace


def _forward_cmdline_args(cmd: list[str], args: Namespace) -> None:
    """Pass `--benchmark` on to the worker processes pyperf spawns.

    Without this the workers register the unfiltered list. A worker then runs the wrong
    benchmark for the task index that pyperf hands to it.
    """
    if args.benchmark:
        cmd.extend(("--benchmark", args.benchmark))


def make_runner(description: str) -> tuple[pyperf.Runner, str | None]:
    """Create a runner that also accepts a filter on the benchmark name.

    Args:
        description: The text that identifies the suite in the result metadata.

    Returns:
        The runner, and the substring that `-b` gave. The substring is `None` when the
        caller asked for every benchmark.
    """
    # pyperf respawns each worker from `sys.argv`, which names this file as a path.
    # A worker started that way has no parent package, so `from . import make_runner`
    # fails in it. Name the module instead, the same way tox starts the suite.
    module = f"{__name__}.{Path(sys.argv[0]).stem}"
    # pyperf puts `sys.executable` in front of these itself, so do not name it here.
    runner = pyperf.Runner(
        add_cmdline_args=_forward_cmdline_args,
        program_args=("-m", module),
    )
    runner.argparser.add_argument(
        "-b",
        "--benchmark",
        metavar="SUBSTRING",
        help="only run the benchmarks whose name contains SUBSTRING",
    )
    runner.metadata["description"] = description
    return runner, runner.parse_args().benchmark
