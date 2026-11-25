"""The trainer as a BLE peripheral: Bumble GATT services wired to a `GattFrontEnd`."""

import asyncio
import contextlib
import logging
from collections.abc import Callable

from bumble import att, gatt
from bumble.core import UUID
from bumble.device import Connection, Device
from bumble.profiles.device_information_service import DeviceInformationService

from phantomwatts import frontend
from phantomwatts.frontend import ControlPointReply, GattFrontEnd
from phantomwatts.protocol import uuids

logger = logging.getLogger(__name__)

# Both fit one read at the default ATT MTU, which carries 22 bytes of value.
MANUFACTURER = "phantomwatts"
MODEL = "Virtual indoor bike"
ADVERTISING_INTERVAL_MS = 100
# Bumble's virtual controller announces 27-byte LE buffers and hands ACL data to the host
# unfragmented. Staying at the default ATT MTU keeps every packet it sends within that size,
# so a host stack never sees a packet larger than the controller said it deals in.
ATT_MTU = 23


class TrainerPeripheral:
    def __init__(
        self,
        device: Device,
        front_end: GattFrontEnd,
        *,
        software_revision: str,
        on_connection_change: Callable[[bool], None] | None = None,
    ) -> None:
        self._device = device
        self._front_end = front_end
        self._on_connection_change = on_connection_change
        self._connections: set[Connection] = set()
        self._control_point_subscribers: set[Connection] = set()
        self._responses: set[asyncio.Task[None]] = set()

        self._indoor_bike_data = _notifying(uuids.INDOOR_BIKE_DATA)
        self._status = _notifying(uuids.FITNESS_MACHINE_STATUS)
        self._power_measurement = _notifying(uuids.CYCLING_POWER_MEASUREMENT)
        self._heart_rate = _notifying(uuids.HEART_RATE_MEASUREMENT)
        # FTMS Table 4.1 asks for an encrypted control point. This one is open, because apps
        # connect to trainers without pairing: docs/protocol.md has the reasoning.
        self._control_point = gatt.Characteristic(
            UUID.from_16_bits(uuids.FITNESS_MACHINE_CONTROL_POINT),
            gatt.Characteristic.Properties.WRITE | gatt.Characteristic.Properties.INDICATE,
            gatt.Characteristic.WRITEABLE,
            gatt.CharacteristicValue(write=self._on_control_point_write),
        )
        self._control_point.on(
            gatt.Characteristic.EVENT_SUBSCRIPTION, self._on_control_point_subscription
        )

        device.add_services(
            [
                DeviceInformationService(
                    manufacturer_name=MANUFACTURER,
                    model_number=MODEL,
                    software_revision=software_revision,
                ),
                gatt.Service(
                    UUID.from_16_bits(uuids.FITNESS_MACHINE_SERVICE),
                    [
                        _read_only(
                            uuids.FITNESS_MACHINE_FEATURE, frontend.FITNESS_MACHINE_FEATURE.encode()
                        ),
                        self._indoor_bike_data,
                        _read_only(
                            uuids.SUPPORTED_RESISTANCE_LEVEL_RANGE,
                            front_end.supported_resistance_level_range(),
                        ),
                        _read_only(uuids.SUPPORTED_POWER_RANGE, front_end.supported_power_range()),
                        self._control_point,
                        self._status,
                    ],
                ),
                gatt.Service(
                    UUID.from_16_bits(uuids.CYCLING_POWER_SERVICE),
                    [
                        self._power_measurement,
                        _read_only(
                            uuids.CYCLING_POWER_FEATURE, frontend.CYCLING_POWER_FEATURE.encode()
                        ),
                        _read_only(uuids.SENSOR_LOCATION, frontend.SENSOR_LOCATION.encode()),
                    ],
                ),
                gatt.Service(
                    UUID.from_16_bits(uuids.HEART_RATE_SERVICE),
                    [
                        self._heart_rate,
                        _read_only(
                            uuids.BODY_SENSOR_LOCATION, frontend.BODY_SENSOR_LOCATION.encode()
                        ),
                    ],
                ),
            ]
        )
        device.gatt_server.max_mtu = ATT_MTU
        advertisement = frontend.advertisement(device.name)
        device.advertising_data = advertisement.data
        device.scan_response_data = advertisement.scan_response
        device.advertising_interval_min = ADVERTISING_INTERVAL_MS
        device.advertising_interval_max = ADVERTISING_INTERVAL_MS
        device.on(Device.EVENT_CONNECTION, self._on_connection)

    @property
    def connected(self) -> bool:
        return bool(self._connections)

    async def start(self) -> None:
        await self._device.power_on()
        await self._device.start_advertising(auto_restart=True)

    async def stop(self) -> None:
        for response in list(self._responses):
            response.cancel()
        for connection in list(self._connections):
            with contextlib.suppress(Exception):
                await connection.disconnect()
        with contextlib.suppress(Exception):
            await self._device.stop_advertising()
        await self._device.power_off()

    async def publish(self) -> None:
        """Notify the current telemetry to whoever subscribed."""
        notifications = self._front_end.notifications()
        await self._device.notify_subscribers(
            self._indoor_bike_data, notifications.indoor_bike_data
        )
        await self._device.notify_subscribers(
            self._power_measurement, notifications.cycling_power_measurement
        )
        await self._device.notify_subscribers(
            self._heart_rate, notifications.heart_rate_measurement
        )

    def _on_connection(self, connection: Connection) -> None:
        logger.info("central connected: %s", connection.peer_address)
        self._connections.add(connection)

        def on_disconnection(_reason: int) -> None:
            self._on_disconnection(connection)

        connection.on(Connection.EVENT_DISCONNECTION, on_disconnection)
        self._connection_changed()

    def _on_disconnection(self, connection: Connection) -> None:
        logger.info("central disconnected: %s", connection.peer_address)
        self._connections.discard(connection)
        self._control_point_subscribers.discard(connection)
        self._front_end.client_gone()
        self._connection_changed()

    def _connection_changed(self) -> None:
        if self._on_connection_change is not None:
            self._on_connection_change(self.connected)

    def _on_control_point_subscription(
        self, connection: Connection, _notify_enabled: bool, indicate_enabled: bool
    ) -> None:
        if indicate_enabled:
            self._control_point_subscribers.add(connection)
        else:
            self._control_point_subscribers.discard(connection)

    def _on_control_point_write(self, connection: Connection, value: bytes) -> None:
        if connection not in self._control_point_subscribers:
            raise att.ATT_Error(att.ErrorCode.CCCD_IMPROPERLY_CONFIGURED)
        try:
            reply = self._front_end.control_point_written(value)
        except ValueError:
            raise att.ATT_Error(att.ErrorCode.INVALID_ATTRIBUTE_LENGTH) from None
        # The indication has to come after the Write Response (FTMS 4.16.4), and Bumble only
        # sends that once this callback has returned.
        response = asyncio.get_running_loop().create_task(self._respond(connection, reply))
        self._responses.add(response)
        response.add_done_callback(self._responses.discard)

    async def _respond(self, connection: Connection, reply: ControlPointReply) -> None:
        try:
            await self._device.indicate_subscriber(
                connection, self._control_point, reply.indication
            )
            if reply.status is not None:
                await self._device.notify_subscribers(self._status, reply.status)
        except Exception:
            logger.warning("a control point response did not reach the central", exc_info=True)


def _read_only(uuid: int, value: bytes) -> gatt.Characteristic[bytes]:
    return gatt.Characteristic(
        UUID.from_16_bits(uuid),
        gatt.Characteristic.Properties.READ,
        gatt.Characteristic.READABLE,
        value,
    )


def _notifying(uuid: int) -> gatt.Characteristic[bytes]:
    return gatt.Characteristic(
        UUID.from_16_bits(uuid),
        gatt.Characteristic.Properties.NOTIFY,
        gatt.Characteristic.Permissions(0),
        b"",
    )
