import asyncio
import sys

import pytest

from phantomwatts.bumble_adapter.central import TrainerClient, central_on, find_trainer
from phantomwatts.cli import main
from phantomwatts.protocol import ftms

EVENT_TIMEOUT_S = 15.0


def test_demo_shows_the_whole_chain(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["demo", "--pace", "0"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert (
        lines[0]
        == "trainer  PhantomBike (C0:FF:EE:00:00:01) advertises on a virtual link, no radio"
    )
    assert "central  found PhantomBike, advertising services 1826 1818 180D" in lines
    assert "central  Request Control: success" in lines
    assert "central  Set Target Power 250 W: success" in lines
    assert "trainer  notifies its status: target power changed to 250 W" in lines
    assert "trainer  notifies its status: simulation parameters changed, grade 6.0 %" in lines
    assert "  11 s    248 W    90 rpm    32.7 km/h    86 bpm" in lines
    assert lines[-3:] == [
        "           RequestControl() -> success",
        "           SetTargetPower(watts=250) -> success",
        "           SetSimulation(grade_percent=6.0, wind_speed_mps=0.0, crr=0.004, "
        "cw_kg_per_m=0.51) -> success",
    ]


def test_demo_over_tcp_tells_the_same_story(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["demo", "--pace", "0"]) == 0
    in_process = capsys.readouterr().out.splitlines()

    assert main(["demo", "--tcp", "--pace", "0"]) == 0
    over_tcp = capsys.readouterr().out.splitlines()

    assert over_tcp[1].startswith("central  its host stack reaches a virtual controller over TCP")
    assert over_tcp[:1] + over_tcp[2:] == in_process


@pytest.mark.parametrize(
    ("arguments", "complaint"),
    [
        (["run", "--scenario", "nonsense"], "cannot read scenario 'nonsense'"),
        (["run", "--hci", "nope:1"], "unknown transport scheme"),
    ],
)
def test_run_explains_what_it_cannot_do(
    arguments: list[str], complaint: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(arguments) == 2

    assert f"phantomwatts: {complaint}" in capsys.readouterr().err


def test_run_without_the_windows_shims_says_where_they_come_from(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "winvhci", None)

    assert main(["run", "--windows", "--attach", "tcp-server:127.0.0.1:0"]) == 2

    assert "winvhci package of github.com/dlech/windows-vhci-driver" in capsys.readouterr().err


def test_a_virtual_radio_and_a_real_controller_exclude_each_other(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["run", "--attach", "tcp-server:127.0.0.1:0", "--hci", "hci-socket:0"])

    assert stopped.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


async def test_run_reports_what_the_app_does_on_standard_output() -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "phantomwatts",
        "run",
        *("--attach", "tcp-server:127.0.0.1:0"),
        *("--scenario", "constant:180w", "--interval", "0.02", "--speed", "50"),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    events = process.stdout

    async def event() -> str:
        line = await asyncio.wait_for(events.readline(), EVENT_TIMEOUT_S)
        return line.decode().rstrip()

    try:
        ready = await event()
        assert ready.startswith("ready name=PhantomBike address=C0:FF:EE:00:00:01 port=")
        port = int(ready.rpartition("=")[2])

        async with central_on(f"tcp-client:127.0.0.1:{port}") as central:
            client = await TrainerClient.connect(central, await find_trainer(central))
            assert await event() == "connected"
            await client.subscribe_to_control()
            await client.command(ftms.RequestControl())
            assert await event() == "command RequestControl() -> success"
            await client.command(ftms.SetTargetPower(210))
            assert await event() == "command SetTargetPower(watts=210) -> success"
            await client.command(ftms.UnmodelledRequest(ftms.OpCode.SPIN_DOWN_CONTROL, b"\x01"))
            assert await event() == (
                "command Unrecognized(opcode=19, parameter=b'\\x01') -> not supported"
            )
            await client.disconnect()
            assert await event() == "disconnected"
    finally:
        process.terminate()
        _, logs = await process.communicate()

    assert process.returncode == 0, logs.decode()
    assert "PhantomBike (C0:FF:EE:00:00:01) is advertising" in logs.decode()
