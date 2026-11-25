"""Through the real Windows Bluetooth stack: a WinRT central drives the trainer.

Both tests pass on GitHub's windows-11-arm runner. They need Windows with the winvhci driver
of dlech/windows-vhci-driver installed, and are only collected when PHANTOMWATTS_WINDOWS_E2E
is set (see tests/conftest.py and .github/workflows/windows-e2e.yml).

The trainer sits on a virtual link; a virtual controller on that link is handed to Windows
through the driver; bleak then talks to the trainer through WinRT like any Windows app. The
driver gives its radio to one process at a time, so each test wants a machine to itself.
"""

import asyncio
import os
import platform
import sys

import pytest
from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from phantomwatts import defaults
from phantomwatts.bumble_adapter.radio import VirtualRadio
from phantomwatts.bumble_adapter.service import serve
from phantomwatts.core.scenario import Constant
from phantomwatts.core.simulation import TrainerSimulation
from phantomwatts.frontend import CommandRecord
from phantomwatts.port import Outcome, RequestControl, SetTargetPower
from phantomwatts.protocol import ftms, uuids

# A second lock besides tests/conftest.py: anywhere else this would scan with the machine's own
# Bluetooth adapter, so it must not run even when someone names the file on purpose.
if platform.system() != "Windows" or os.environ.get("PHANTOMWATTS_WINDOWS_E2E") != "1":
    pytest.skip("needs Windows with the winvhci driver installed", allow_module_level=True)

RADIO_BRING_UP_S = 120.0
PROCESS_START_S = 60.0
TARGET_POWER_W = 230

# bleak 3.1 still calls WinRT methods that PyWinRT has renamed. Warnings are errors in this
# suite, and this one is bleak's to fix.
pytestmark = pytest.mark.filterwarnings("ignore:.* is deprecated, use .*:DeprecationWarning")


def _uuid(short: int) -> str:
    return f"0000{short:04x}-0000-1000-8000-00805f9b34fb"


async def _find(name: str) -> BLEDevice:
    """Scan until the trainer shows up; Windows needs a while to bring the new radio up."""
    async with asyncio.timeout(RADIO_BRING_UP_S):
        while True:
            try:
                device = await BleakScanner.find_device_by_name(name, timeout=5.0)
            except (OSError, BleakError):
                # Until the radio is up, bleak reports that the machine has no adapter at all.
                device = None
            if device is not None:
                return device
            await asyncio.sleep(1.0)


async def _start_an_erg_workout(name: str) -> None:
    """What an app does: find the trainer, subscribe, take control, set a target, watch it."""
    responses: asyncio.Queue[ftms.ControlPointResponse] = asyncio.Queue()
    power_followed = asyncio.Event()

    def on_response(_: BleakGATTCharacteristic, value: bytearray) -> None:
        responses.put_nowait(ftms.ControlPointResponse.decode(bytes(value)))

    def on_bike_data(_: BleakGATTCharacteristic, value: bytearray) -> None:
        power = ftms.IndoorBikeData.decode(bytes(value)).instantaneous_power_w or 0
        if power >= TARGET_POWER_W - 5:
            power_followed.set()

    async with BleakClient(await _find(name)) as client:
        control_point = _uuid(uuids.FITNESS_MACHINE_CONTROL_POINT)
        await client.start_notify(control_point, on_response)
        await client.start_notify(_uuid(uuids.INDOOR_BIKE_DATA), on_bike_data)

        await client.write_gatt_char(control_point, ftms.RequestControl().encode(), response=True)
        granted = await asyncio.wait_for(responses.get(), 10)
        assert granted.result is ftms.ResultCode.SUCCESS

        await client.write_gatt_char(
            control_point, ftms.SetTargetPower(TARGET_POWER_W).encode(), response=True
        )
        accepted = await asyncio.wait_for(responses.get(), 10)
        assert accepted.result is ftms.ResultCode.SUCCESS

        await asyncio.wait_for(power_followed.wait(), 30)


async def test_a_winrt_central_finds_the_trainer_subscribes_and_sets_a_target() -> None:
    radio = VirtualRadio(expose="winvhci", windows=True)
    async with serve(TrainerSimulation(Constant(150, 90)), radio=radio) as trainer:
        ride = asyncio.create_task(trainer.run(interval_s=1.0))
        try:
            await _start_an_erg_workout(trainer.name)

            assert trainer.commands == [
                CommandRecord(RequestControl(), Outcome.SUCCESS),
                CommandRecord(SetTargetPower(TARGET_POWER_W), Outcome.SUCCESS),
            ]
        finally:
            ride.cancel()


async def _events_until(lines: asyncio.StreamReader, last: str) -> list[str]:
    events: list[str] = []
    while last not in events:
        line = await lines.readline()
        if not line:
            break
        events.append(line.decode().rstrip())
    return events


async def test_the_same_through_the_bridge_with_the_trainer_in_a_process_of_its_own() -> None:
    """What a Windows guest runs, on one machine: `phantomwatts run --windows` behind TCP, and
    the driver project's vhcibridge.ps1 between that port and the driver."""
    commands = [
        "command RequestControl() -> success",
        f"command SetTargetPower(watts={TARGET_POWER_W}) -> success",
    ]
    # --verbose puts the HCI traffic on standard error, which pytest shows when this fails:
    # the only way to see how far Windows got on a runner nobody can log in to.
    trainer = await asyncio.create_subprocess_exec(
        *(sys.executable, "-m", "phantomwatts", "run", "--windows", "--verbose"),
        *("--attach", "tcp-server:127.0.0.1:0"),
        stdout=asyncio.subprocess.PIPE,
    )
    assert trainer.stdout is not None
    bridge = None
    try:
        ready = (await asyncio.wait_for(trainer.stdout.readline(), PROCESS_START_S)).decode()
        assert ready.startswith(f"ready name={defaults.TRAINER_NAME} "), ready
        bridge = await asyncio.create_subprocess_exec(
            *("powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass"),
            *("-File", os.environ["PHANTOMWATTS_VHCIBRIDGE"]),
            *("-RemoteHost", "127.0.0.1", "-Port", ready.rpartition("=")[2].strip()),
        )

        await _start_an_erg_workout(defaults.TRAINER_NAME)

        events = await asyncio.wait_for(_events_until(trainer.stdout, commands[-1]), 10)
    finally:
        for process in (bridge, trainer):
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()

    assert events[0] == "connected"
    assert [event for event in events if event.startswith("command")] == commands
