"""A running trainer: the simulation, its GATT front end and a radio, tied together."""

import asyncio
import contextlib
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from importlib import metadata

from bumble import hci
from bumble.device import Device

from phantomwatts import defaults
from phantomwatts.bumble_adapter.central import DEFAULT_CENTRAL_ADDRESS
from phantomwatts.bumble_adapter.peripheral import TrainerPeripheral
from phantomwatts.bumble_adapter.radio import Attachment, Radio, VirtualRadio, attach
from phantomwatts.core.simulation import TrainerSimulation
from phantomwatts.frontend import CommandRecord, GattFrontEnd
from phantomwatts.port import Telemetry


class RunningTrainer:
    """Handle on a trainer that is advertising and can be connected to."""

    def __init__(
        self,
        simulation: TrainerSimulation,
        device: Device,
        attachment: Attachment,
        on_command: Callable[[CommandRecord], None] | None,
        on_connection_change: Callable[[bool], None] | None,
    ) -> None:
        self.simulation = simulation
        self.name = device.name
        self.address = str(device.random_address)
        self._attachment = attachment
        self._on_command = on_command
        self._on_connection_change = on_connection_change
        self._change = asyncio.Event()
        self._joined: list[Device] = []
        self._front_end = GattFrontEnd(simulation, on_command=self._command_arrived)
        self._peripheral = TrainerPeripheral(
            device,
            self._front_end,
            software_revision=metadata.version("phantomwatts"),
            on_connection_change=self._connection_changed,
        )

    @property
    def commands(self) -> list[CommandRecord]:
        """What the app wrote to the control point so far, with what became of it."""
        return self._front_end.commands

    @property
    def connected(self) -> bool:
        return self._peripheral.connected

    @property
    def tcp_port(self) -> int | None:
        """The port the virtual controller listens on, when it is offered over TCP."""
        return self._attachment.tcp_port

    async def tick(self, dt: float) -> Telemetry:
        """Advance the ride by `dt` seconds and notify the new telemetry."""
        telemetry = self.simulation.step(dt)
        await self._peripheral.publish()
        self._changed()
        return telemetry

    async def run(
        self,
        interval_s: float = 1.0,
        time_scale: float = 1.0,
        *,
        on_tick: Callable[[Telemetry], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Ride in real time until cancelled. `time_scale` above 1 makes the ride run fast."""
        last = clock()
        while True:
            await sleep(interval_s)
            now = clock()
            telemetry = await self.tick((now - last) * time_scale)
            last = now
            if on_tick is not None:
                on_tick(telemetry)

    async def wait_for(self, predicate: Callable[[], bool], *, timeout_s: float = 10.0) -> None:
        """Return once `predicate()` holds. It is checked after every command and tick."""
        async with asyncio.timeout(timeout_s):
            while not predicate():
                await self._change.wait()

    async def join_link(
        self, name: str = "central", address: str = DEFAULT_CENTRAL_ADDRESS
    ) -> Device:
        """A powered-on Bumble host on the trainer's virtual link, to act as a central."""
        controller = self._attachment.add_controller(name, address)
        device = Device.with_hci(name, hci.Address(address), controller, controller)
        await device.power_on()
        self._joined.append(device)
        return device

    async def start(self) -> None:
        await self._peripheral.start()

    async def stop(self) -> None:
        for device in self._joined:
            with contextlib.suppress(Exception):
                await device.power_off()
        await self._peripheral.stop()

    def _connection_changed(self, connected: bool) -> None:
        if self._on_connection_change is not None:
            self._on_connection_change(connected)
        self._changed()

    def _command_arrived(self, record: CommandRecord) -> None:
        if self._on_command is not None:
            self._on_command(record)
        self._changed()

    def _changed(self) -> None:
        # Wake everyone waiting now; the next wait starts from a cleared event.
        self._change.set()
        self._change.clear()


@contextlib.asynccontextmanager
async def serve(
    simulation: TrainerSimulation | None = None,
    *,
    radio: Radio | None = None,
    name: str = defaults.TRAINER_NAME,
    address: str = defaults.TRAINER_ADDRESS,
    on_command: Callable[[CommandRecord], None] | None = None,
    on_connection_change: Callable[[bool], None] | None = None,
) -> AsyncGenerator[RunningTrainer, None]:
    """Advertise a trainer for as long as the block runs.

    Without a `radio` the trainer sits on a virtual link that only this process can reach;
    see `RunningTrainer.join_link`.
    """
    async with attach(radio or VirtualRadio(), address) as attachment:
        device = Device.with_hci(
            name, hci.Address(address), attachment.hci_source, attachment.hci_sink
        )
        trainer = RunningTrainer(
            simulation or TrainerSimulation(), device, attachment, on_command, on_connection_change
        )
        await trainer.start()
        try:
            yield trainer
        finally:
            await trainer.stop()
