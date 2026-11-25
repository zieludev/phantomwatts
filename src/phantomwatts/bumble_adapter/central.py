"""A small central: what an app does with a trainer, reduced to the essentials.

It drives the demo and the end-to-end tests. It is built on Bumble like the peripheral, but
shares only the codecs with it, so the two sides check each other through real GATT traffic.
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from typing import Self, TypeVar

from bumble import hci
from bumble.core import UUID
from bumble.device import Advertisement, Device, Peer
from bumble.gatt_client import CharacteristicProxy
from bumble.transport import open_transport

from phantomwatts.protocol import advertising, cps, ftms, hrs, uuids

DEFAULT_CENTRAL_ADDRESS = "F0:F1:F2:F3:F4:F6"
# What a desktop host stack typically asks for right after connecting.
REQUESTED_ATT_MTU = 517
_T = TypeVar("_T")


@dataclass(frozen=True)
class FoundTrainer:
    address: hci.Address
    name: str
    services: tuple[int, ...]
    service_data: ftms.ServiceData | None


@contextlib.asynccontextmanager
async def central_on(
    transport_spec: str, address: str = DEFAULT_CENTRAL_ADDRESS
) -> AsyncGenerator[Device, None]:
    """A powered-on Bumble host on the controller behind `transport_spec`."""
    async with await open_transport(transport_spec) as transport:
        device = Device.with_hci("central", hci.Address(address), transport.source, transport.sink)
        await device.power_on()
        try:
            yield device
        finally:
            await device.power_off()


async def find_trainer(
    device: Device,
    *,
    timeout_s: float = 10.0,
    own_address_type: hci.OwnAddressType = hci.OwnAddressType.RANDOM,
) -> FoundTrainer:
    """Scan until something advertises the Fitness Machine Service."""
    found: asyncio.Future[FoundTrainer] = asyncio.get_running_loop().create_future()

    def on_advertisement(advertisement: Advertisement) -> None:
        trainer = _as_trainer(advertisement)
        if trainer is not None and not found.done():
            found.set_result(trainer)

    device.on(Device.EVENT_ADVERTISEMENT, on_advertisement)
    await device.start_scanning(own_address_type=own_address_type)
    try:
        async with asyncio.timeout(timeout_s):
            return await found
    finally:
        device.remove_listener(Device.EVENT_ADVERTISEMENT, on_advertisement)
        await device.stop_scanning()


def _as_trainer(advertisement: Advertisement) -> FoundTrainer | None:
    structures = dict(advertising.parse(advertisement.data_bytes))
    listed = structures.get(advertising.COMPLETE_LIST_OF_16_BIT_SERVICE_UUIDS, b"")
    services = tuple(
        int.from_bytes(listed[offset : offset + 2], "little") for offset in range(0, len(listed), 2)
    )
    if uuids.FITNESS_MACHINE_SERVICE not in services:
        return None
    name = structures.get(advertising.COMPLETE_LOCAL_NAME) or structures.get(
        advertising.SHORTENED_LOCAL_NAME, b""
    )
    data = structures.get(advertising.SERVICE_DATA_16_BIT_UUID, b"")
    is_ftms_data = int.from_bytes(data[:2], "little") == uuids.FITNESS_MACHINE_SERVICE
    return FoundTrainer(
        address=advertisement.address,
        name=name.decode(errors="replace"),
        services=services,
        service_data=ftms.ServiceData.decode(data[2:]) if is_ftms_data else None,
    )


class TrainerClient:
    def __init__(self, peer: Peer, att_mtu: int) -> None:
        self._peer = peer
        self.att_mtu = att_mtu
        self._responses: asyncio.Queue[ftms.ControlPointResponse] = asyncio.Queue()
        self._changed = asyncio.Event()
        self.bike_data: list[ftms.IndoorBikeData] = []
        self.power_measurements: list[cps.CyclingPowerMeasurement] = []
        self.heart_rates: list[hrs.HeartRateMeasurement] = []
        self.statuses: list[ftms.Status] = []

    @classmethod
    async def connect(
        cls,
        device: Device,
        trainer: FoundTrainer,
        *,
        own_address_type: hci.OwnAddressType = hci.OwnAddressType.RANDOM,
    ) -> Self:
        connection = await device.connect(trainer.address, own_address_type=own_address_type)
        peer = Peer(connection)
        att_mtu = await peer.request_mtu(REQUESTED_ATT_MTU)
        await peer.discover_services()
        for service in peer.services:
            await service.discover_characteristics()
        return cls(peer, att_mtu)

    @property
    def services(self) -> list[int]:
        """The 16-bit UUIDs of the services the trainer exposes, in table order."""
        return [
            int.from_bytes(service.uuid.to_bytes(), "little") for service in self._peer.services
        ]

    async def read(self, characteristic: int) -> bytes:
        return await self._peer.read_value(self._characteristic(characteristic))

    async def subscribe_to_telemetry(self) -> None:
        await self._subscribe(
            uuids.INDOOR_BIKE_DATA, self.bike_data.append, ftms.IndoorBikeData.decode
        )
        await self._subscribe(
            uuids.CYCLING_POWER_MEASUREMENT,
            self.power_measurements.append,
            cps.CyclingPowerMeasurement.decode,
        )
        await self._subscribe(
            uuids.HEART_RATE_MEASUREMENT, self.heart_rates.append, hrs.HeartRateMeasurement.decode
        )

    async def subscribe_to_control(self) -> None:
        """Enable the indications and status notifications the control point works with."""
        await self._subscribe(
            uuids.FITNESS_MACHINE_CONTROL_POINT,
            self._responses.put_nowait,
            ftms.ControlPointResponse.decode,
        )
        await self._subscribe(
            uuids.FITNESS_MACHINE_STATUS, self.statuses.append, ftms.decode_status
        )

    async def command(
        self, request: ftms.Request, *, timeout_s: float = 5.0
    ) -> ftms.ControlPointResponse:
        """Write a request to the control point and wait for the indicated response."""
        await self.write_control_point(request.encode())
        async with asyncio.timeout(timeout_s):
            return await self._responses.get()

    async def write_control_point(self, value: bytes) -> None:
        control_point = self._characteristic(uuids.FITNESS_MACHINE_CONTROL_POINT)
        await self._peer.write_value(control_point, value, with_response=True)

    async def wait_for(self, predicate: Callable[[], bool], *, timeout_s: float = 5.0) -> None:
        async with asyncio.timeout(timeout_s):
            while not predicate():
                await self._changed.wait()

    async def disconnect(self) -> None:
        await self._peer.connection.disconnect()

    def _characteristic(self, uuid: int) -> CharacteristicProxy[bytes]:
        matches = self._peer.get_characteristics_by_uuid(UUID.from_16_bits(uuid))
        if not matches:
            raise LookupError(f"the trainer has no characteristic 0x{uuid:04X}")
        return matches[0]

    async def _subscribe(
        self, uuid: int, deliver: Callable[[_T], object], decode: Callable[[bytes], _T]
    ) -> None:
        def on_value(value: bytes) -> None:
            deliver(decode(value))
            # Wake everyone waiting now; the next wait starts from a cleared event.
            self._changed.set()
            self._changed.clear()

        await self._characteristic(uuid).subscribe(on_value)
