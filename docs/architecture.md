# Architecture

A functional core with an imperative shell around it. The trainer's behaviour and the wire
formats are plain Python that knows nothing about Bluetooth; one package talks to the Bluetooth
stack; the command line and the pytest plugin sit on the outside.

```
 entry points     cli.py         pytest_plugin.py        testing.py
                     |                  |                    |
                     v                  v                    v
 shell           +----------------------------------------------------------+
 (does I/O)      | bumble_adapter/   the only code that imports Bumble      |
                 |   service.py      serve(): simulation, front end, radio  |
                 |   peripheral.py   GATT services, notify, indicate        |
                 |   radio.py        virtual link, controllers, transports  |
                 |   central.py      a small app-side client                |
                 |   demo.py         the two of them talking                |
                 +----------------------------------------------------------+
                                           |
                                           v
 core                                 frontend.py
 (pure)                 characteristic values and control point rules,
                                  bytes in, bytes out
                                 /                    \
                                v                      v
                port.py  <---  core/                protocol/
          TrainerBackend,      simulation.py        ftms.py  cps.py  hrs.py
          commands,            scenario.py          advertising.py  uuids.py
          telemetry            physics.py           codecs, nothing else
```

An arrow means "imports". The shell also uses the core and the codecs directly; nothing imports
upwards.

## The dependency rule

- `protocol` imports nothing of this project. `port` neither.
- `core` imports `port`, and not `protocol`: the simulation works in watts and percent, never
  in bytes.
- `frontend` imports `port` and `protocol`, and is the only place where the two meet.
- Those four import only `collections`, `dataclasses`, `enum`, `math`, `random`, `re` and
  `typing` from the standard library. No `asyncio`, no `time`, no sockets: time moves when
  somebody calls `step(dt)`.
- `bumble` and `winvhci` are imported in `bumble_adapter` and nowhere else, and
  `bumble_adapter` imports none of the entry points.
- Importing `phantomwatts`, its command line or its pytest plugin does not load Bumble.

`tests/test_architecture.py` checks every line of this list on each run.

## Why bother

Three reasons, all concrete.

1. The logic has to be testable without Bluetooth. The codecs, the simulation and the control
   point rules (who has control, which result code, which status notification) are tested with
   values in and bytes out; only the end-to-end tests open a Bluetooth stack, and even those
   need no hardware.
2. Bumble is at version 0.0.x and renames things between releases. `pyproject.toml` pins it to
   one exact version, so the lock file, the tests and CI all describe the same Bumble, and an
   upgrade is a deliberate change. That change should touch one package, and Bumble's type
   gaps should relax the type checker in one package (see `pyproject.toml` again).
3. A second backend is planned: a real trainer, mirrored into a virtual machine through the same
   virtual controller. It will implement `TrainerBackend` and reuse the front end, the
   peripheral and the radio unchanged.

## The port

`port.py` is the seam: `TrainerBackend` has `capabilities`, `execute(command)`, `telemetry()`
and `release_control()`. The commands are plain dataclasses (`SetTargetPower(watts=200)`), and
they double as the log a test asserts on. They are deliberately not the FTMS message types of
`protocol`: a mirrored trainer may speak something else on its far side.

## Where it stops

There is no dependency injection container, no plugin system and no base class with a single
subclass. Three things are simpler than a textbook version would have them:

- `serve()` takes a `TrainerSimulation`, not a `TrainerBackend`. Advancing time is the
  simulation's business and is not part of the port; a mirror will be driven by its own
  notifications instead of a clock.
- `execute` is synchronous. A mirror has to wait for the real trainer's answer, so this is the
  first signature I expect to change, and the only caller is `frontend.py`.
- The front end reports what changed (a status notification) from the command it just carried
  out. A backend cannot push an event on its own yet, because the simulation never needs to.

## What I added to the plan

`frontend.py` was not in the first sketch, which had the Bumble peripheral talk to the port
directly. Pulling the protocol logic out of the peripheral left that file with nothing but
wiring, and made the part most likely to be wrong the part that is easiest to test.
