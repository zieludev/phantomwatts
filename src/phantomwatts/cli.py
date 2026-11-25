"""The `phantomwatts` command."""

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
from collections.abc import Sequence
from importlib import metadata

from phantomwatts import defaults
from phantomwatts.core import scenario
from phantomwatts.core.scenario import DEFAULT_CADENCE_RPM, SCENARIO_FORMS, Jitter
from phantomwatts.core.simulation import TrainerSimulation
from phantomwatts.frontend import CommandRecord
from phantomwatts.port import Telemetry

logger = logging.getLogger("phantomwatts")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if arguments.verbose else logging.WARNING,
        format="%(asctime)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logger.setLevel(logging.DEBUG if arguments.verbose else logging.INFO)
    try:
        asyncio.run(arguments.run(arguments))
    except KeyboardInterrupt:
        return 0
    except (ValueError, RuntimeError, OSError) as error:
        print(f"phantomwatts: {error}", file=sys.stderr)
        return 2
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="phantomwatts",
        description="A virtual Bluetooth LE smart trainer that needs no radio.",
    )
    parser.add_argument("--version", action="version", version=metadata.version("phantomwatts"))
    commands = parser.add_subparsers(required=True, metavar="{run,demo}")

    run = commands.add_parser(
        "run",
        help="run a trainer for an app to connect to",
        description="Run a trainer until interrupted. Standard output carries one line per "
        "event (ready, connected, command, disconnected) for a test harness to read; "
        "logs go to standard error.",
    )
    where = run.add_mutually_exclusive_group()
    where.add_argument(
        "--attach",
        metavar="SPEC",
        help="offer a virtual controller to an outside host stack on this Bumble transport, "
        f"for example tcp-server:127.0.0.1:6402 or vhci (default: {defaults.ATTACH})",
    )
    where.add_argument(
        "--hci",
        metavar="SPEC",
        help="no virtual radio: run the trainer on the controller behind this Bumble "
        "transport, for example hci-socket:0. This takes a real adapter away from the system",
    )
    run.add_argument(
        "--windows",
        action="store_true",
        help="the outside host stack is Windows: apply the controller shims of the winvhci package",
    )
    run.add_argument(
        "--controller-address",
        default=defaults.CONTROLLER_ADDRESS,
        metavar="ADDRESS",
        help="public address of the virtual controller (default: %(default)s)",
    )
    run.add_argument(
        "--name", default=defaults.TRAINER_NAME, help="advertised name (default: %(default)s)"
    )
    run.add_argument(
        "--address",
        default=defaults.TRAINER_ADDRESS,
        help="the trainer's static random address (default: %(default)s)",
    )
    run.add_argument(
        "--scenario",
        default="constant:150w",
        metavar="SPEC",
        help=f"what the rider does: {SCENARIO_FORMS} (default: %(default)s)",
    )
    run.add_argument(
        "--cadence",
        type=float,
        default=DEFAULT_CADENCE_RPM,
        metavar="RPM",
        help="the rider's cadence (default: %(default)g)",
    )
    run.add_argument(
        "--jitter",
        type=int,
        metavar="SEED",
        help="add repeatable noise to power and cadence; the same seed gives the same ride",
    )
    run.add_argument(
        "--interval",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="time between notifications (default: %(default)g)",
    )
    run.add_argument(
        "--speed",
        type=float,
        default=1.0,
        metavar="FACTOR",
        help="how fast the ride runs compared with real time (default: %(default)g)",
    )
    run.add_argument(
        "--telemetry", action="store_true", help="also print a line for every notification"
    )
    run.add_argument("-v", "--verbose", action="store_true", help="log the Bluetooth traffic")
    run.set_defaults(run=_run)

    demo = commands.add_parser(
        "demo",
        help="watch a trainer and an app talk, with no hardware at all",
        description="Run a trainer and a small central in this process, over a virtual link.",
    )
    demo.add_argument(
        "--tcp",
        action="store_true",
        help="put the central's host stack behind TCP on 127.0.0.1, as the Windows bridge is",
    )
    demo.add_argument(
        "--pace",
        type=float,
        default=0.15,
        metavar="SECONDS",
        help="pause per simulated second (default: %(default)g)",
    )
    demo.add_argument("-v", "--verbose", action="store_true", help="log the Bluetooth traffic")
    demo.set_defaults(run=_demo)
    return parser


async def _run(arguments: argparse.Namespace) -> None:
    from phantomwatts.bumble_adapter.radio import HciTransport, VirtualRadio
    from phantomwatts.bumble_adapter.service import serve

    rider = scenario.parse(arguments.scenario, cadence_rpm=arguments.cadence)
    if arguments.jitter is not None:
        rider = Jitter(rider, seed=arguments.jitter)
    if arguments.hci:
        radio = HciTransport(arguments.hci)
        where = f"on the controller behind {arguments.hci}"
    else:
        attach = arguments.attach or defaults.ATTACH
        radio = VirtualRadio(attach, arguments.controller_address, arguments.windows)
        where = f"behind a virtual controller on {attach}"

    async with serve(
        TrainerSimulation(rider),
        radio=radio,
        name=arguments.name,
        address=arguments.address,
        on_command=_print_command,
        on_connection_change=_print_connection,
    ) as trainer:
        logger.info("%s (%s) is advertising %s", trainer.name, trainer.address, where)
        port = "" if trainer.tcp_port is None else f" port={trainer.tcp_port}"
        _print(f"ready name={trainer.name} address={trainer.address}{port}")
        ride = asyncio.create_task(
            trainer.run(
                arguments.interval,
                arguments.speed,
                on_tick=_print_telemetry if arguments.telemetry else None,
            )
        )
        await _until_terminated(ride)


async def _until_terminated(ride: asyncio.Task[None]) -> None:
    terminated = asyncio.Event()
    # Windows has no signal handlers on the event loop; Ctrl+C still ends the ride there.
    with contextlib.suppress(NotImplementedError):
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, terminated.set)
    waiting = asyncio.create_task(terminated.wait())
    try:
        await asyncio.wait({ride, waiting}, return_when=asyncio.FIRST_COMPLETED)
        if ride.done():
            # The ride never ends by itself, so this re-raises whatever broke it.
            ride.result()
    finally:
        for task in (ride, waiting):
            task.cancel()
        await asyncio.gather(ride, waiting, return_exceptions=True)


async def _demo(arguments: argparse.Namespace) -> None:
    from phantomwatts.bumble_adapter.demo import run_demo

    if not arguments.verbose:
        logger.setLevel(logging.WARNING)
    await run_demo(over_tcp=arguments.tcp, pace_s=arguments.pace, say=_print)


def _print_connection(connected: bool) -> None:
    _print("connected" if connected else "disconnected")


def _print_command(record: CommandRecord) -> None:
    _print(f"command {record.command} -> {record.outcome.value}")


def _print_telemetry(telemetry: Telemetry) -> None:
    _print(
        f"telemetry power={telemetry.power_w:.0f}W cadence={telemetry.cadence_rpm:.0f}rpm "
        f"speed={telemetry.speed_mps * 3.6:.1f}km/h heart_rate={telemetry.heart_rate_bpm:.0f}bpm"
    )


def _print(line: str) -> None:
    # Flushed line by line so a test harness reading a pipe sees commands as they happen.
    print(line, flush=True)
