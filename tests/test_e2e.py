"""End to end over real GATT traffic, with no Bluetooth hardware.

The same exchange runs twice: with the central on the trainer's virtual link inside this
process, and with the central's host stack reaching a virtual controller over TCP on
127.0.0.1, which is the byte stream the Windows bridge carries.
"""

from importlib import metadata

import pytest
from bumble import hci
from bumble.core import ProtocolError
from bumble.device import Device

from phantomwatts import frontend
from phantomwatts.bumble_adapter.central import TrainerClient, central_on, find_trainer
from phantomwatts.bumble_adapter.radio import VirtualRadio
from phantomwatts.bumble_adapter.service import RunningTrainer, serve
from phantomwatts.core.scenario import Constant
from phantomwatts.core.simulation import ControlMode, TrainerSimulation
from phantomwatts.frontend import CommandRecord
from phantomwatts.port import Outcome, RequestControl, SetSimulation, SetTargetPower
from phantomwatts.protocol import ftms, uuids
from phantomwatts.protocol.ftms import ResultCode, SimulationParameters

CCCD_IMPROPERLY_CONFIGURED = 0xFD
CLIMB = SimulationParameters(wind_speed_mps=0.0, grade_percent=4.5, crr=0.004, cw_kg_per_m=0.51)


async def ride_as_an_app_would(
    trainer: RunningTrainer, central: Device, own_address_type: hci.OwnAddressType
) -> None:
    found = await find_trainer(central, own_address_type=own_address_type)
    assert found.name == "PhantomBike"
    assert found.services == frontend.ADVERTISED_SERVICES
    assert found.service_data == ftms.ServiceData(True, ftms.MachineType.INDOOR_BIKE)

    client = await TrainerClient.connect(central, found, own_address_type=own_address_type)
    await trainer.wait_for(lambda: trainer.connected)
    assert client.att_mtu == 23
    assert {
        uuids.DEVICE_INFORMATION_SERVICE,
        uuids.FITNESS_MACHINE_SERVICE,
        uuids.CYCLING_POWER_SERVICE,
        uuids.HEART_RATE_SERVICE,
    } <= set(client.services)
    feature = ftms.Feature.decode(await client.read(uuids.FITNESS_MACHINE_FEATURE))
    assert feature == frontend.FITNESS_MACHINE_FEATURE
    power_range = ftms.SupportedPowerRange.decode(await client.read(uuids.SUPPORTED_POWER_RANGE))
    assert power_range == ftms.SupportedPowerRange(0, 2000, 1)
    assert await client.read(uuids.MANUFACTURER_NAME_STRING) == b"phantomwatts"
    assert await client.read(uuids.MODEL_NUMBER_STRING) == b"Virtual indoor bike"
    assert (
        await client.read(uuids.SOFTWARE_REVISION_STRING)
        == metadata.version("phantomwatts").encode()
    )

    with pytest.raises(ProtocolError) as refused:
        await client.write_control_point(ftms.RequestControl().encode())
    assert refused.value.error_code == CCCD_IMPROPERLY_CONFIGURED
    assert trainer.commands == []

    await client.subscribe_to_telemetry()
    await client.subscribe_to_control()
    await trainer.tick(1.0)
    await client.wait_for(
        lambda: bool(client.bike_data and client.power_measurements and client.heart_rates)
    )
    assert client.bike_data[0].instantaneous_power_w == 150
    assert client.bike_data[0].instantaneous_cadence_rpm == 90
    assert client.power_measurements[0].instantaneous_power_w == 150
    assert client.power_measurements[0].crank_revolutions is not None
    assert client.heart_rates[0].heart_rate_bpm >= 60

    not_yet = await client.command(ftms.SetTargetPower(250))
    assert not_yet.result is ResultCode.CONTROL_NOT_PERMITTED
    granted = await client.command(ftms.RequestControl())
    assert granted == ftms.ControlPointResponse(ftms.OpCode.REQUEST_CONTROL, ResultCode.SUCCESS)
    accepted = await client.command(ftms.SetTargetPower(250))
    assert accepted == ftms.ControlPointResponse(ftms.OpCode.SET_TARGET_POWER, ResultCode.SUCCESS)
    await client.wait_for(lambda: ftms.TargetPowerChanged(250) in client.statuses)

    for _ in range(10):
        await trainer.tick(1.0)
    await client.wait_for(lambda: len(client.bike_data) == 11)
    powers = [sample.instantaneous_power_w or 0 for sample in client.bike_data]
    assert powers == sorted(powers)
    assert 150 < powers[1] < powers[5] < 250
    assert powers[-1] in (249, 250)

    climbing = await client.command(ftms.SetIndoorBikeSimulationParameters(CLIMB))
    assert climbing.result is ResultCode.SUCCESS
    await client.wait_for(
        lambda: ftms.IndoorBikeSimulationParametersChanged(CLIMB) in client.statuses
    )
    assert trainer.simulation.mode is ControlMode.SIMULATION
    assert trainer.simulation.road.grade_percent == 4.5

    unsupported = await client.command(
        ftms.UnmodelledRequest(ftms.OpCode.SPIN_DOWN_CONTROL, b"\x01")
    )
    assert unsupported.result is ResultCode.OP_CODE_NOT_SUPPORTED

    assert trainer.commands[:4] == [
        CommandRecord(SetTargetPower(250), Outcome.CONTROL_NOT_PERMITTED),
        CommandRecord(RequestControl(), Outcome.SUCCESS),
        CommandRecord(SetTargetPower(250), Outcome.SUCCESS),
        CommandRecord(SetSimulation(4.5, 0.0, 0.004, 0.51), Outcome.SUCCESS),
    ]

    await client.disconnect()
    await trainer.wait_for(lambda: not trainer.connected)
    assert not trainer.simulation.has_control
    assert trainer.simulation.mode is ControlMode.FREE_RIDE
    assert (await find_trainer(central, own_address_type=own_address_type)).name == "PhantomBike"


async def test_in_process_over_the_virtual_link() -> None:
    async with serve(TrainerSimulation(Constant(150, 90))) as trainer:
        central = await trainer.join_link()

        await ride_as_an_app_would(trainer, central, hci.OwnAddressType.RANDOM)


@pytest.mark.parametrize(
    ("windows", "own_address_type"),
    [
        pytest.param(False, hci.OwnAddressType.RANDOM, id="central-on-a-random-address"),
        pytest.param(False, hci.OwnAddressType.PUBLIC, id="central-on-its-public-address"),
        pytest.param(True, hci.OwnAddressType.PUBLIC, id="windows-shims-and-public-address"),
    ],
)
async def test_over_the_tcp_wire_path(windows: bool, own_address_type: hci.OwnAddressType) -> None:
    radio = VirtualRadio(expose="tcp-server:127.0.0.1:0", windows=windows)
    async with serve(TrainerSimulation(Constant(150, 90)), radio=radio) as trainer:
        assert trainer.tcp_port
        async with central_on(f"tcp-client:127.0.0.1:{trainer.tcp_port}") as central:
            await ride_as_an_app_would(trainer, central, own_address_type)
