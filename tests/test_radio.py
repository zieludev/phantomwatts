"""The ways a trainer attaches to a host stack, and what happens when that host comes and goes."""

import asyncio
import sys

import pytest
from bumble import hci
from bumble.device import Device

from phantomwatts import defaults
from phantomwatts.bumble_adapter.central import TrainerClient, central_on, find_trainer
from phantomwatts.bumble_adapter.radio import HciTransport, VirtualRadio, attach
from phantomwatts.bumble_adapter.service import serve
from phantomwatts.core.scenario import Constant
from phantomwatts.core.simulation import TrainerSimulation
from phantomwatts.protocol import ftms
from phantomwatts.protocol.ftms import ResultCode

LOOPBACK_ANY_PORT = "tcp-server:127.0.0.1:0"


async def take_control(central: Device) -> TrainerClient:
    client = await TrainerClient.connect(central, await find_trainer(central))
    await client.subscribe_to_control()
    assert (await client.command(ftms.RequestControl())).result is ResultCode.SUCCESS
    return client


async def test_a_new_host_finds_the_trainer_after_the_old_one_vanished() -> None:
    async with serve(radio=VirtualRadio(expose=LOOPBACK_ANY_PORT)) as trainer:
        host = f"tcp-client:127.0.0.1:{trainer.tcp_port}"

        async with central_on(host) as central:
            await take_control(central)
        # That host went away without disconnecting, as a guest does when it is switched off.
        # Its TCP connection closing is all the trainer gets to hear.
        await trainer.wait_for(lambda: not trainer.connected)
        assert not trainer.simulation.has_control

        async with central_on(host) as central:
            await take_control(central)
            assert trainer.simulation.has_control


async def test_a_host_that_resets_its_controller_drops_its_links() -> None:
    radio = VirtualRadio(expose=LOOPBACK_ANY_PORT)
    async with (
        serve(radio=radio) as trainer,
        central_on(f"tcp-client:127.0.0.1:{trainer.tcp_port}") as central,
    ):
        await take_control(central)
        assert trainer.connected

        # Powering the host on again starts with an HCI Reset, as a host stack does when its
        # adapter is switched off and on.
        await central.power_on()

        await trainer.wait_for(lambda: not trainer.connected)
        assert not trainer.simulation.has_control
        assert (await find_trainer(central)).name == "PhantomBike"


async def test_a_newer_tcp_connection_replaces_an_older_one() -> None:
    async with serve(radio=VirtualRadio(expose=LOOPBACK_ANY_PORT)) as trainer:
        assert trainer.tcp_port
        stale_reader, stale_writer = await asyncio.open_connection("127.0.0.1", trainer.tcp_port)
        # Half an HCI command, which the next host must not find in front of its own packets.
        stale_writer.write(bytes.fromhex("01 03"))
        await stale_writer.drain()

        async with central_on(f"tcp-client:127.0.0.1:{trainer.tcp_port}") as central:
            await take_control(central)

            assert await stale_reader.read() == b""
        stale_writer.close()
        await stale_writer.wait_closed()


async def test_the_trainer_can_drive_an_existing_controller_over_hci() -> None:
    # An attachment used inside out: its exposed controller is the "existing" one, and the
    # central joins the same link in this process.
    async with attach(VirtualRadio(expose=LOOPBACK_ANY_PORT), "C0:00:00:00:00:99") as air:
        controller = HciTransport(f"tcp-client:127.0.0.1:{air.tcp_port}")
        async with serve(TrainerSimulation(Constant(210, 95)), radio=controller) as trainer:
            assert trainer.tcp_port is None
            central_controller = air.add_controller("central", "F0:F1:F2:F3:F4:F6")
            central = Device.with_hci(
                "central", hci.Address("F0:F1:F2:F3:F4:F6"), central_controller, central_controller
            )
            await central.power_on()

            client = await take_control(central)
            await client.subscribe_to_telemetry()
            await trainer.tick(1.0)
            await client.wait_for(lambda: bool(client.bike_data))

            assert client.bike_data[0].instantaneous_power_w == 210
            with pytest.raises(RuntimeError, match="only a virtual radio"):
                await trainer.join_link()


async def test_the_tcp_port_is_released_when_the_trainer_stops() -> None:
    async with serve(radio=VirtualRadio(expose=LOOPBACK_ANY_PORT)) as trainer:
        port = trainer.tcp_port
        assert port
        async with central_on(f"tcp-client:127.0.0.1:{port}"):
            pass

    with pytest.raises(ConnectionRefusedError):
        await asyncio.open_connection("127.0.0.1", port)


async def test_riding_in_real_time_follows_the_clock() -> None:
    now = 100.0
    naps: list[float] = []

    def clock() -> float:
        return now

    async def sleep(seconds: float) -> None:
        nonlocal now
        naps.append(seconds)
        if len(naps) > 5:
            raise asyncio.CancelledError
        # One nap overruns, as naps do; the ride must follow the clock, not the request.
        now += seconds * (3 if len(naps) == 2 else 1)

    async with serve(TrainerSimulation(Constant(150, 90))) as trainer:
        with pytest.raises(asyncio.CancelledError):
            await trainer.run(interval_s=0.5, time_scale=4.0, clock=clock, sleep=sleep)

    assert naps == [0.5] * 6
    ridden_s = (4 * 0.5 + 1.5) * 4.0
    assert trainer.simulation.telemetry().crank_revolutions == ridden_s * 90 / 60 == 21


@pytest.mark.parametrize(
    ("radio", "message"),
    [
        (VirtualRadio(windows=True), "Windows controller shims come from the winvhci package"),
        (VirtualRadio(expose="winvhci"), "winvhci transport needs the winvhci package"),
    ],
)
async def test_windows_support_says_what_is_missing(
    radio: VirtualRadio, message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A None entry makes the import fail the way it does when the package is not installed.
    monkeypatch.setitem(sys.modules, "winvhci", None)
    monkeypatch.delitem(sys.modules, "winvhci.transport", raising=False)

    with pytest.raises(RuntimeError, match=message):
        async with attach(radio, defaults.TRAINER_ADDRESS):
            pass
