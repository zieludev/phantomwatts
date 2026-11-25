"""`phantomwatts demo`: a trainer and an app talking over a virtual link, with no hardware."""

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Callable

from bumble.device import Device

from phantomwatts.bumble_adapter.central import TrainerClient, central_on, find_trainer
from phantomwatts.bumble_adapter.radio import VirtualRadio
from phantomwatts.bumble_adapter.service import RunningTrainer, serve
from phantomwatts.core.scenario import Constant
from phantomwatts.core.simulation import TrainerSimulation
from phantomwatts.protocol import ftms, uuids

RIDER = Constant(power_w=150, cadence_rpm=90)
TARGET_POWER_W = 250
CLIMB = ftms.SimulationParameters(
    wind_speed_mps=0.0, grade_percent=6.0, crr=0.004, cw_kg_per_m=0.51
)
SERVICE_NAMES = {
    0x1800: "Generic Access",
    0x1801: "Generic Attribute",
    uuids.DEVICE_INFORMATION_SERVICE: "Device Information",
    uuids.FITNESS_MACHINE_SERVICE: "Fitness Machine",
    uuids.CYCLING_POWER_SERVICE: "Cycling Power",
    uuids.HEART_RATE_SERVICE: "Heart Rate",
}


async def run_demo(
    *, over_tcp: bool = False, pace_s: float = 0.15, say: Callable[[str], None] = print
) -> None:
    """Play the whole chain: discover, subscribe, take control, set targets, watch them act.

    `pace_s` is the pause per simulated second, there only so a person can follow along.
    """
    radio = VirtualRadio(expose="tcp-server:127.0.0.1:0" if over_tcp else None)
    async with serve(TrainerSimulation(RIDER), radio=radio) as trainer:
        say(f"trainer  {trainer.name} ({trainer.address}) advertises on a virtual link, no radio")
        async with _central(trainer, say) as central:
            found = await find_trainer(central)
            advertised = " ".join(f"{service:04X}" for service in found.services)
            say(f"central  found {found.name}, advertising services {advertised}")

            client = await TrainerClient.connect(central, found)
            names = [SERVICE_NAMES.get(service, f"{service:04X}") for service in client.services]
            say(f"central  connected; it has {', '.join(names)}")
            feature = ftms.Feature.decode(await client.read(uuids.FITNESS_MACHINE_FEATURE))
            power = ftms.SupportedPowerRange.decode(await client.read(uuids.SUPPORTED_POWER_RANGE))
            say(f"central  it can be set to: {_names(feature.target_settings)}")
            say(f"central  power targets from {power.minimum_w} to {power.maximum_w} W")
            await client.subscribe_to_telemetry()
            await client.subscribe_to_control()
            say("central  subscribed to bike data, power, heart rate, status and control point")

            ride = _Ride(trainer, client, pace_s, say)
            await ride.seconds(3)

            await _command(client, ftms.RequestControl(), "Request Control", say)
            await _command(
                client,
                ftms.SetTargetPower(TARGET_POWER_W),
                f"Set Target Power {TARGET_POWER_W} W",
                say,
            )
            await ride.seconds(8)

            await _command(
                client,
                ftms.SetIndoorBikeSimulationParameters(CLIMB),
                f"Set Simulation Parameters, grade {CLIMB.grade_percent} %",
                say,
            )
            await ride.seconds(6)
            await client.disconnect()

        say("trainer  what the app asked for, as the trainer logged it:")
        for record in trainer.commands:
            say(f"           {record.command} -> {record.outcome.value}")


@contextlib.asynccontextmanager
async def _central(
    trainer: RunningTrainer, say: Callable[[str], None]
) -> AsyncGenerator[Device, None]:
    if trainer.tcp_port is None:
        yield await trainer.join_link()
        return
    async with central_on(f"tcp-client:127.0.0.1:{trainer.tcp_port}") as central:
        say(
            f"central  its host stack reaches a virtual controller over TCP, "
            f"127.0.0.1:{trainer.tcp_port}"
        )
        yield central


class _Ride:
    def __init__(
        self,
        trainer: RunningTrainer,
        client: TrainerClient,
        pace_s: float,
        say: Callable[[str], None],
    ) -> None:
        self._trainer = trainer
        self._client = client
        self._pace_s = pace_s
        self._say = say
        self._second = 0

    async def seconds(self, count: int) -> None:
        """Advance the ride and print each second as the central received it."""
        client = self._client
        for _ in range(count):
            self._second += 1
            await asyncio.sleep(self._pace_s)
            await self._trainer.tick(1.0)
            await client.wait_for(
                lambda: len(client.bike_data) == len(client.heart_rates) == self._second
            )
            bike, heart = client.bike_data[-1], client.heart_rates[-1]
            self._say(
                f"  {self._second:2d} s   {bike.instantaneous_power_w or 0:4d} W"
                f"   {bike.instantaneous_cadence_rpm or 0:3.0f} rpm"
                f"   {bike.instantaneous_speed_kmh or 0:5.1f} km/h"
                f"   {heart.heart_rate_bpm:3d} bpm"
            )


async def _command(
    client: TrainerClient, request: ftms.Request, label: str, say: Callable[[str], None]
) -> None:
    seen = len(client.statuses)
    response = await client.command(request)
    say(f"central  {label}: {response.result.name.replace('_', ' ').lower()}")
    if isinstance(request, ftms.RequestControl):
        return
    await client.wait_for(lambda: len(client.statuses) > seen)
    say(f"trainer  notifies its status: {_describe(client.statuses[-1])}")


def _names(flags: ftms.TargetSettingFeatures) -> str:
    return ", ".join(
        (flag.name or "").replace("_", " ").lower()
        for flag in ftms.TargetSettingFeatures
        if flag in flags
    )


def _describe(status: ftms.Status) -> str:
    match status:
        case ftms.TargetPowerChanged(watts):
            return f"target power changed to {watts} W"
        case ftms.IndoorBikeSimulationParametersChanged(parameters):
            return f"simulation parameters changed, grade {parameters.grade_percent} %"
        case _:
            return str(status)
