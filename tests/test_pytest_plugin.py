"""The fixture as another project's test would use it: a plain test, an app elsewhere."""

import asyncio
import socket
import subprocess
import sys

import pytest

from phantomwatts import CommandRecord, ControlMode, Outcome, RequestControl, SetTargetPower
from phantomwatts.bumble_adapter.central import TrainerClient, central_on, find_trainer
from phantomwatts.protocol import ftms
from phantomwatts.pytest_plugin import TrainerFactory


def latest_power(client: TrainerClient) -> int:
    return client.bike_data[-1].instantaneous_power_w or 0


async def an_app_that_starts_an_erg_workout(port: int) -> int:
    async with central_on(f"tcp-client:127.0.0.1:{port}") as central:
        client = await TrainerClient.connect(central, await find_trainer(central))
        await client.subscribe_to_telemetry()
        await client.subscribe_to_control()
        await client.command(ftms.RequestControl())
        await client.command(ftms.SetTargetPower(220))
        await client.wait_for(lambda: bool(client.bike_data) and latest_power(client) > 200)
        return latest_power(client)


def test_a_plain_test_can_assert_on_what_the_app_commanded(
    phantomwatts_trainer: TrainerFactory,
) -> None:
    trainer = phantomwatts_trainer("constant:180w", interval_s=0.01, time_scale=100.0)
    assert trainer.tcp_port
    assert not trainer.connected

    last_power = asyncio.run(an_app_that_starts_an_erg_workout(trainer.tcp_port))

    assert trainer.commands == [
        CommandRecord(RequestControl(), Outcome.SUCCESS),
        CommandRecord(SetTargetPower(220), Outcome.SUCCESS),
    ]
    assert 200 < last_power <= 220
    # The app has left: its host stack is gone, so is its control over the trainer.
    trainer.wait_for(lambda: not trainer.connected, timeout_s=5)
    assert trainer.simulation.mode is ControlMode.FREE_RIDE


def test_waiting_for_something_that_never_happens_times_out(
    phantomwatts_trainer: TrainerFactory,
) -> None:
    trainer = phantomwatts_trainer(expose=None)

    assert trainer.tcp_port is None
    with pytest.raises(TimeoutError):
        trainer.wait_for(lambda: trainer.connected, timeout_s=0.05)


def test_a_trainer_that_cannot_start_fails_the_test_that_asked_for_it(
    phantomwatts_trainer: TrainerFactory,
) -> None:
    with pytest.raises(ValueError, match="cannot read scenario"):
        phantomwatts_trainer("sprint:9w")
    with socket.create_server(("127.0.0.1", 0)) as taken:
        port = taken.getsockname()[1]
        with pytest.raises(OSError, match="in use"):
            phantomwatts_trainer(expose=f"tcp-server:127.0.0.1:{port}")


def test_the_plugin_loads_without_bluetooth_code() -> None:
    probe = (
        "import sys, phantomwatts, phantomwatts.pytest_plugin, phantomwatts.testing; "
        "sys.exit(any(name.split('.')[0] in ('bumble', 'winvhci') for name in sys.modules))"
    )
    assert subprocess.run([sys.executable, "-c", probe], check=False).returncode == 0
