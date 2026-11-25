# phantomwatts

[![CI](https://github.com/zieludev/phantomwatts/actions/workflows/ci.yml/badge.svg)](https://github.com/zieludev/phantomwatts/actions/workflows/ci.yml)

A Bluetooth LE smart trainer that exists only in software. It offers the GATT services an
indoor cycling app looks for (Fitness Machine with an Indoor Bike, Cycling Power, Heart Rate),
sends the numbers of a scripted rider, and reacts when the app sets a target power or a
gradient. It needs no bike, no trainer and no Bluetooth adapter: the radio is simulated, and
the app's own Bluetooth stack is plugged into it through a virtual controller.

I wrote it for [rouvy-linux](https://github.com/zieludev/rouvy-linux), where "does the app in
the virtual machine see a trainer?" could only be answered with a real bike next to the laptop.
It should be as useful to anyone who writes a cycling app or tests Bluetooth plumbing in a
virtual machine or in CI.

This is version 0.1. Everything that fits on one machine is tested; nobody has pointed a real
app at it yet. [What is verified](#what-is-verified) says exactly where that line runs.

## Try it

You need [uv](https://docs.astral.sh/uv/) and nothing else, no Bluetooth of any kind.

```bash
git clone https://github.com/zieludev/phantomwatts.git
cd phantomwatts
uv run phantomwatts demo
```

That starts a trainer and a small app in one process, connected by a simulated radio link, and
takes about three seconds:

```
trainer  PhantomBike (C0:FF:EE:00:00:01) advertises on a virtual link, no radio
central  found PhantomBike, advertising services 1826 1818 180D
central  connected; it has Generic Access, Generic Attribute, Device Information, Fitness Machine, Cycling Power, Heart Rate
central  it can be set to: resistance, power, indoor bike simulation
central  power targets from 0 to 2000 W
central  subscribed to bike data, power, heart rate, status and control point
   1 s    150 W    90 rpm     8.0 km/h    62 bpm
   2 s    150 W    90 rpm    13.8 km/h    64 bpm
   3 s    150 W    90 rpm    17.9 km/h    66 bpm
central  Request Control: success
central  Set Target Power 250 W: success
trainer  notifies its status: target power changed to 250 W
   4 s    189 W    90 rpm    21.6 km/h    68 bpm
   5 s    213 W    90 rpm    24.6 km/h    71 bpm
   6 s    228 W    90 rpm    27.0 km/h    73 bpm
   7 s    236 W    90 rpm    28.9 km/h    76 bpm
   8 s    242 W    90 rpm    30.2 km/h    78 bpm
   9 s    245 W    90 rpm    31.3 km/h    81 bpm
  10 s    247 W    90 rpm    32.1 km/h    84 bpm
  11 s    248 W    90 rpm    32.7 km/h    86 bpm
central  Set Simulation Parameters, grade 6.0 %: success
trainer  notifies its status: simulation parameters changed, grade 6.0 %
  12 s    150 W    90 rpm    26.2 km/h    87 bpm
  13 s    150 W    90 rpm    21.6 km/h    88 bpm
  14 s    150 W    90 rpm    18.3 km/h    89 bpm
  15 s    150 W    90 rpm    15.9 km/h    90 bpm
  16 s    150 W    90 rpm    14.2 km/h    91 bpm
  17 s    150 W    90 rpm    13.0 km/h    92 bpm
trainer  what the app asked for, as the trainer logged it:
           RequestControl() -> success
           SetTargetPower(watts=250) -> success
           SetSimulation(grade_percent=6.0, wind_speed_mps=0.0, crr=0.004, cw_kg_per_m=0.51) -> success
```

The numbers travel as real GATT notifications and control point writes between two Bluetooth
host stacks; only the air between their controllers is simulated. In ERG mode the power climbs
to the target, on the 6 % grade the same 150 W get slower and slower. `demo --tcp` plays the
same exchange with the app's host stack behind a TCP socket on 127.0.0.1, which is the way a
virtual machine reaches the trainer.

## Run a trainer

```bash
uv run phantomwatts run
```

runs a trainer until Ctrl+C and offers a virtual Bluetooth controller on `127.0.0.1:6402`. A
host stack that connects there (see the next two sections) finds a trainer called `PhantomBike`
on its radio. The rider is scripted:

| `--scenario` | The rider does |
| --- | --- |
| `constant:180w` | 180 W all the time (the default is 150 W) |
| `ramp:100w-300w/600s` | climbs from 100 W to 300 W over ten minutes, then holds |
| `intervals:150w/60s,280w/30s` | 60 s at 150 W, 30 s at 280 W, and again |

`--cadence 90` sets the cadence and `--jitter 7` adds noise that is the same every time for
the same number. Heart rate follows the effort. `phantomwatts run --help` has the rest.

Standard output carries one line per event, for a test harness to read. This is a session with
a scripted app that sets a target too early, takes control, sets it again, switches to a 2.5 %
grade and asks for something the trainer does not support:

```
$ phantomwatts run --attach tcp-server:127.0.0.1:0 --scenario constant:180w --telemetry
ready name=PhantomBike address=C0:FF:EE:00:00:01 port=41203
telemetry power=180W cadence=85rpm speed=8.6km/h heart_rate=62bpm
telemetry power=180W cadence=85rpm speed=14.8km/h heart_rate=65bpm
connected
command SetTargetPower(watts=220) -> control not permitted
command RequestControl() -> success
command SetTargetPower(watts=220) -> success
telemetry power=196W cadence=85rpm speed=19.4km/h heart_rate=67bpm
telemetry power=205W cadence=85rpm speed=23.0km/h heart_rate=70bpm
telemetry power=211W cadence=85rpm speed=25.6km/h heart_rate=72bpm
command SetSimulation(grade_percent=2.5, wind_speed_mps=0.0, crr=0.004, cw_kg_per_m=0.51) -> success
command Unrecognized(opcode=19, parameter=b'\x01') -> not supported
telemetry power=180W cadence=85rpm speed=24.1km/h heart_rate=74bpm
disconnected
```

Logs go to standard error. Port 0 lets the system pick a free port and `ready` reports it;
`telemetry` lines only appear with `--telemetry`.

## Attach it to a Windows guest

**Nobody has run this in a guest yet.** Everything but the guest has run on a GitHub runner,
the bridge included (see the end of this section). It is built on
[dlech/windows-vhci-driver](https://github.com/dlech/windows-vhci-driver), a virtual HCI
controller for Windows: the guest's ordinary Bluetooth stack runs on top of a controller that
lives in a process on the host, which is this one.

On the host:

```bash
uv run phantomwatts run --windows
```

In the guest, with the driver installed (it needs test signing on, Secure Boot off and memory
integrity off, its README explains how):

```powershell
.\vhcibridge.ps1 -RemoteHost 10.0.2.2 -Port 6402
```

`10.0.2.2` is how a QEMU guest with user-mode networking reaches the host's loopback, so the
controller is never exposed on a real network interface. Do not bind it to one: whoever
connects to that port is the Bluetooth radio of whatever is on the other side.

`--windows` matters. A stock Bumble controller does not get Windows through its Bluetooth
start-up; the driver project ships the fixes as a Python package, `winvhci`, which is not on
PyPI. `uv sync` installs it here because the tests use it. Elsewhere:

```bash
pip install "winvhci @ git+https://github.com/dlech/windows-vhci-driver@v1.3.0#subdirectory=python"
```

What I matched against that project's own example (`tools/bumble-controller.py --peer
--dual-mode` at v1.3.0, and its `tools/vhcibridge.ps1`):

- the wire: HCI packets in H4 framing over TCP, the controller listening on `127.0.0.1:6402`,
  the bridge connecting as the only client, no handshake;
- the controller: its `WindowsCompatController` for both ends of the link, its
  `WindowsCompatLink` between them, `apply_dual_mode` on the one Windows talks to, the public
  address `F0:F1:F2:F3:F4:F5`;
- the peer: advertising from a random address, restarting after a disconnect;
- the sizes: the bridge takes frames of up to 1026 bytes, and the trainer keeps the ATT MTU at
  23, so nothing it sends is longer than the 27-byte LE buffer the controller announces.

`.github/workflows/windows-e2e.yml` runs this on a `windows-11-arm` runner, with the driver
installed, in two ways. In one job the test hands the virtual controller to the driver itself.
In the other it does what the two commands above do, on one machine: `phantomwatts run
--windows` is a process of its own and `vhcibridge.ps1` connects the driver to its port on
127.0.0.1. Either way a WinRT central (bleak) finds the trainer, subscribes, takes control and
sets a target power, and in the second job the trainer's standard output reports the connection
and both commands. Both jobs pass. The workflow starts only by hand and runs an x64 Python under
emulation.

So Windows does bring the radio up with Bumble 0.0.236 (the shims were published against
0.0.226 and 0.0.234), and its GATT client is happy with this trainer. What is still open: a
guest, where the bridge crosses the virtual machine's network instead of staying on 127.0.0.1;
an x64 Windows, which the driver's author cannot Bluetooth-test in CI either, only ARM; and any
app.

## Attach it to Linux

**Nobody has run this yet either.** Bumble can hand a virtual controller to the Linux kernel
through `/dev/vhci`:

```bash
phantomwatts run --attach vhci
```

That needs the `hci_vhci` kernel module and the right to open `/dev/vhci`, which normally means
root. The kernel should then show one more Bluetooth adapter, which BlueZ should treat like
hardware. On a machine with Bluetooth devices you care about, try it in a virtual machine first.

There is also `--hci SPEC`, without any virtual radio: the trainer's own stack on a real
controller, for example `--hci hci-socket:0`, so that a phone or a head unit can see it over
the air. That takes the adapter away from the system for as long as it runs, and it has only
been run against a virtual controller so far. Bumble's USB transports need libusb, which this
project does not install (see [Licence](#licence)).

## In your tests

Installing phantomwatts gives pytest a fixture. The trainer runs on a thread of its own, so the
test can be an ordinary function that starts an app and waits:

```python
from phantomwatts import CommandRecord, Outcome, SetTargetPower


def test_my_app_starts_an_erg_workout(phantomwatts_trainer):
    trainer = phantomwatts_trainer("constant:180w")
    start_my_app(bluetooth_controller_port=trainer.tcp_port)

    wanted = CommandRecord(SetTargetPower(200), Outcome.SUCCESS)
    trainer.wait_for(lambda: wanted in trainer.commands, timeout_s=60)
```

`trainer.commands` is everything the app wrote to the control point, in order, with what
became of it. `trainer.connected` and `trainer.simulation` show the rest. The trainer listens
on a free loopback port unless you pass `expose="tcp-server:127.0.0.1:6402"`, and
`windows=True` does what `--windows` does.

From async code, without pytest:

```python
import asyncio

import phantomwatts


async def main():
    radio = phantomwatts.VirtualRadio(expose="tcp-server:127.0.0.1:6402")
    rider = phantomwatts.Ramp(start_w=100, end_w=300, duration_s=600)
    async with phantomwatts.serve(phantomwatts.TrainerSimulation(rider), radio=radio) as trainer:
        await trainer.run()


asyncio.run(main())
```

The simulation and the codecs work without any Bluetooth at all:

```python
>>> from phantomwatts import Constant, RequestControl, SetTargetPower, TrainerSimulation
>>> simulation = TrainerSimulation(Constant(power_w=150, cadence_rpm=90))
>>> simulation.execute(SetTargetPower(250))
<Outcome.CONTROL_NOT_PERMITTED: 'control not permitted'>
>>> simulation.execute(RequestControl()), simulation.execute(SetTargetPower(250))
(<Outcome.SUCCESS: 'success'>, <Outcome.SUCCESS: 'success'>)
>>> round(simulation.step(10.0).power_w, 1)
248.3
>>> from phantomwatts.protocol import ftms
>>> ftms.IndoorBikeData(instantaneous_speed_kmh=31.5, instantaneous_power_w=250).encode().hex(" ")
'40 00 4e 0c fa 00'
```

To use it from another project before it is on PyPI:

```bash
uv add --dev "phantomwatts @ git+https://github.com/zieludev/phantomwatts"
```

## What it emulates

One device with four services: Fitness Machine (feature, indoor bike data, control point,
status, supported power and resistance ranges), Cycling Power (measurement with crank
revolutions, feature, sensor location), Heart Rate (measurement, body sensor location) and
Device Information. The control point understands request control, reset, target power,
target resistance, simulation parameters, start, stop and pause, and answers everything else
with "op code not supported". One app at a time.

[docs/protocol.md](docs/protocol.md) has the tables, the result codes, and the seven places
where I did not follow a document to the letter, mostly because two documents disagree.
[docs/architecture.md](docs/architecture.md) explains how the code is laid out and why.

It is called `PhantomBike` on the air, and its Device Information names the manufacturer
`phantomwatts` and the model `Virtual indoor bike`.
There are no presets that imitate a real trainer, and there will not be.

## What is verified

| What | Status |
| --- | --- |
| Codecs: tables of hand-computed bytes, plus two independent parsers (pycycling, Bumble's Heart Rate profile) | run on Python 3.11 to 3.14 |
| Simulation and control point rules, without Bluetooth | run on Python 3.11 to 3.14 |
| A Bumble central discovers, subscribes, takes control, sets ERG and simulation, over the in-process link | run |
| The same over TCP on 127.0.0.1 in H4 framing, the byte stream the Windows bridge carries, with the central on a random and on its public address | run |
| The same with the `winvhci` Windows shims in place (still a Bumble central, not Windows) | run |
| A host stack that vanishes without disconnecting, and a new one that attaches | run |
| The trainer's stack on an outside controller over HCI (a virtual one, over TCP) | run |
| `phantomwatts run` and `phantomwatts demo` as processes | run |
| The pytest fixture | run |
| `.github/workflows/ci.yml` on GitHub (lint, types, the suite on Python 3.11 to 3.14) | run |
| `.github/workflows/windows-e2e.yml`: the real Windows Bluetooth stack on a virtual controller, a WinRT central finds the trainer, subscribes and sets a target (Windows 11 on ARM, x64 Python under emulation) | run on GitHub |
| The same workflow, the way a guest gets the trainer but on one machine: `phantomwatts run --windows` in a process of its own, `vhcibridge.ps1` between its port and the driver | run on GitHub |
| A Windows guest through `vhcibridge.ps1` and the driver | not run by anyone |
| Linux through `/dev/vhci` | not run by anyone |
| A real adapter with `--hci` | not run by anyone |
| Any real app, ROUVY and Zwift included | not run by anyone |
| Byte-for-byte comparison with a real trainer | not done |

I make no claim that it works with any particular app. If you try one, open an issue and tell
me what happened, whichever way it went.

## Fair use

This is a testing tool. Its rides are made up, so do not use it to fake a ride in an event, a
challenge or on a leaderboard: that is cheating the people who rode theirs.

## Other trainer simulators

[paixaop/zwack](https://github.com/paixaop/zwack) and
[dncore/efferent-ble-simulator](https://github.com/dncore/efferent-ble-simulator) simulate a
trainer on a real Bluetooth adapter, [michidk/fitsim](https://github.com/michidk/fitsim) on an
ESP32. If you want something a phone can see over the air today, they are the shorter way. This
project is for the case where there is no radio at all, or none you want to give away.

## Development

```bash
uv sync                      # Python 3.11 or newer, fetched by uv if needed
uv run pytest                # no hardware, a few seconds
uv run ruff check && uv run ruff format --check
uv run basedpyright
```

The tests never touch the Bluetooth of the machine they run on. The ones that would, in
`tests/windows`, are only collected on Windows and only when asked for.

## Licence

[MIT](LICENSE). What this builds on is listed in [3rdparty_licenses.md](3rdparty_licenses.md),
first of all [Bumble](https://github.com/google/bumble), a Bluetooth stack in Python, and
[windows-vhci-driver](https://github.com/dlech/windows-vhci-driver), whose notes on what
Windows demands of a controller saved me from finding it out the hard way.

Bumble declares two dependencies under the LGPL, `libusb1` and the libusb that
`libusb-package` bundles. This project never opens a USB transport, so `pyproject.toml` leaves
both out of the lock file and of the environment `uv` builds. A plain `pip install` still pulls
them in, because Bumble asks for them, and so does a project that depends on phantomwatts. With
uv such a project keeps them out the same way, with
`exclude-dependencies = ["libusb1", "libusb-package"]` under `[tool.uv]` in its own
`pyproject.toml`.

ROUVY is a trademark of VirtualTraining s.r.o., Zwift of Zwift, Inc., Windows of Microsoft and
Bluetooth of Bluetooth SIG, Inc. This project is not affiliated with any of them.
