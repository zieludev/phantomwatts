"""Through the Linux kernel: the virtual controller behind /dev/vhci.

Both tests pass on a GitHub-hosted Ubuntu runner, whose kernel ships hci_vhci. They need root,
and are only collected when PHANTOMWATTS_LINUX_VHCI is set (see tests/conftest.py and
.github/workflows/linux-vhci.yml).

They stop at the kernel. No bluetoothd runs on the runner, so nothing here says how BlueZ, or
an app on top of it, gets on with the trainer.
"""

import asyncio
import contextlib
import errno
import fcntl
import os
import platform
import socket
import struct
import sys
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from phantomwatts import defaults
from phantomwatts.bumble_adapter.central import TrainerClient, central_on, find_trainer
from phantomwatts.protocol import ftms

# A second lock besides tests/conftest.py: on a desktop this adds a Bluetooth adapter to the
# machine, which BlueZ starts using, so it must not run even when someone names the file.
if platform.system() != "Linux" or os.environ.get("PHANTOMWATTS_LINUX_VHCI") != "1":
    pytest.skip("needs Linux with /dev/vhci, and root", allow_module_level=True)

ADAPTERS = Path("/sys/class/bluetooth")
EVENT_TIMEOUT_S = 15.0

# From the kernel's include/net/bluetooth/hci_sock.h and hci.h.
HCIDEVUP = 0x400448C9
HCIDEVDOWN = 0x400448CA
HCIGETDEVINFO = 0x800448D3
HCI_DEV_INFO_SIZE = 92
HCI_UP, HCI_RUNNING = 1 << 0, 1 << 2


def _adapters() -> set[str]:
    return {entry.name for entry in ADAPTERS.glob("hci*")}


async def _new_adapter(known: set[str]) -> int:
    """The index of the adapter the kernel makes of the controller; sysfs has no event for it."""
    while True:
        new = _adapters() - known
        if new:
            (name,) = new
            return int(name.removeprefix("hci"))
        await asyncio.sleep(0.1)


@contextlib.asynccontextmanager
async def _trainer_behind_vhci() -> AsyncGenerator[tuple[asyncio.StreamReader, int], None]:
    """`phantomwatts run --attach vhci`: its event lines and the index of its adapter."""
    known = _adapters()
    # --verbose puts the HCI traffic on standard error, which pytest shows when a test fails:
    # the only way to see how far the kernel got on a runner nobody can log in to.
    process = await asyncio.create_subprocess_exec(
        *(sys.executable, "-m", "phantomwatts", "run", "--attach", "vhci", "--verbose"),
        stdout=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    try:
        ready = await asyncio.wait_for(process.stdout.readline(), EVENT_TIMEOUT_S)
        assert ready.startswith(b"ready "), "no trainer: /dev/vhci takes root and hci_vhci"
        index = await asyncio.wait_for(_new_adapter(known), EVENT_TIMEOUT_S)
        yield process.stdout, index
    finally:
        if process.returncode is None:
            process.terminate()
            await process.wait()


async def _power(index: int, request: int) -> None:
    with socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI) as control:
        try:
            await asyncio.to_thread(fcntl.ioctl, control, request, index)
        except OSError as error:
            # The kernel starts a new adapter by itself and keeps it up for two seconds.
            if error.errno != errno.EALREADY:
                raise


def _flags_and_address(index: int) -> tuple[int, str]:
    info = bytearray(struct.pack("<H", index).ljust(HCI_DEV_INFO_SIZE, b"\0"))
    with socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI) as control:
        fcntl.ioctl(control, HCIGETDEVINFO, info)
    address, flags = struct.unpack_from("<6sI", info, 10)
    return flags, address[::-1].hex(":").upper()


async def test_the_kernel_brings_the_virtual_controller_up() -> None:
    async with _trainer_behind_vhci() as (_, index):
        # Returns once the kernel has taken the controller through its whole start-up.
        await _power(index, HCIDEVUP)

        flags, address = _flags_and_address(index)

    assert address == defaults.CONTROLLER_ADDRESS
    assert flags & HCI_UP
    assert flags & HCI_RUNNING


async def test_a_host_on_the_kernels_hci_socket_completes_the_exchange() -> None:
    async with _trainer_behind_vhci() as (events, index):

        async def event() -> str:
            line = await asyncio.wait_for(events.readline(), EVENT_TIMEOUT_S)
            return line.decode().rstrip()

        # Bumble's hci-socket takes the adapter over as a user channel, which the kernel only
        # grants once its own start-up of the adapter is over and the adapter is down again.
        await _power(index, HCIDEVUP)
        await _power(index, HCIDEVDOWN)

        async with central_on(f"hci-socket:{index}") as central:
            client = await TrainerClient.connect(central, await find_trainer(central))
            assert await event() == "connected"
            await client.subscribe_to_control()
            await client.command(ftms.RequestControl())
            assert await event() == "command RequestControl() -> success"
            await client.command(ftms.SetTargetPower(210))
            assert await event() == "command SetTargetPower(watts=210) -> success"
            await client.disconnect()
            assert await event() == "disconnected"
