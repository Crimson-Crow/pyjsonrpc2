"""Runner setup that the benchmark suites in this directory share."""

from __future__ import annotations

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
    runner = pyperf.Runner(add_cmdline_args=_forward_cmdline_args)
    runner.argparser.add_argument(
        "-b",
        "--benchmark",
        metavar="SUBSTRING",
        help="only run the benchmarks whose name contains SUBSTRING",
    )
    runner.metadata["description"] = description
    return runner, runner.parse_args().benchmark
