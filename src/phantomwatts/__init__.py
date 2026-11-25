"""A virtual Bluetooth LE smart trainer that needs no radio.

    radio = phantomwatts.VirtualRadio(expose="tcp-server:127.0.0.1:6402")
    async with phantomwatts.serve(radio=radio) as trainer:
        await trainer.run()

The simulation and the codecs import nothing Bluetooth; `serve` and the radio types load Bumble
the first time they are used.
"""

import importlib
from typing import TYPE_CHECKING

from phantomwatts.core.physics import Road
from phantomwatts.core.scenario import (
    Constant,
    Interval,
    Intervals,
    Jitter,
    Ramp,
    RiderInput,
    Scenario,
)
from phantomwatts.core.simulation import ControlMode, Rider, Session, TrainerSimulation
from phantomwatts.frontend import CommandRecord, Unrecognized
from phantomwatts.port import (
    Capabilities,
    Command,
    Outcome,
    Pause,
    Range,
    RequestControl,
    Reset,
    SetSimulation,
    SetTargetPower,
    SetTargetResistance,
    Start,
    Stop,
    Telemetry,
    TrainerBackend,
)

if TYPE_CHECKING:
    from phantomwatts.bumble_adapter.radio import HciTransport, VirtualRadio
    from phantomwatts.bumble_adapter.service import RunningTrainer, serve

_NEEDS_BUMBLE = {
    "HciTransport": "phantomwatts.bumble_adapter.radio",
    "VirtualRadio": "phantomwatts.bumble_adapter.radio",
    "RunningTrainer": "phantomwatts.bumble_adapter.service",
    "serve": "phantomwatts.bumble_adapter.service",
}

__all__ = [
    "Capabilities",
    "Command",
    "CommandRecord",
    "Constant",
    "ControlMode",
    "HciTransport",
    "Interval",
    "Intervals",
    "Jitter",
    "Outcome",
    "Pause",
    "Ramp",
    "Range",
    "RequestControl",
    "Reset",
    "Rider",
    "RiderInput",
    "Road",
    "RunningTrainer",
    "Scenario",
    "Session",
    "SetSimulation",
    "SetTargetPower",
    "SetTargetResistance",
    "Start",
    "Stop",
    "Telemetry",
    "TrainerBackend",
    "TrainerSimulation",
    "Unrecognized",
    "VirtualRadio",
    "serve",
]


def __getattr__(name: str) -> object:
    module = _NEEDS_BUMBLE.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)
